import io
import json
import os
import unittest
import urllib.error
import urllib.request
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import main

FIXTURES = Path(__file__).parent / "fixtures"
CHAT = str(FIXTURES / "windows_desktop_us.txt")

_STATE_DIR = TemporaryDirectory()
os.environ["CHAT_SUMMARIZER_STATE"] = str(Path(_STATE_DIR.name) / "state.json")


def _ollama_available() -> bool:
    try:
        with urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=2):
            return True
    except (urllib.error.URLError, OSError):
        return False


class DefaultCommandTests(unittest.TestCase):
    def test_file_without_subcommand_uses_parse(self) -> None:
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = main.main([CHAT])
        self.assertEqual(code, 0)
        self.assertIn("Alice", buffer.getvalue())

    def test_flags_before_file_still_use_parse(self) -> None:
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = main.main(["--json", CHAT])
        self.assertEqual(code, 0)
        self.assertIsInstance(json.loads(buffer.getvalue()), list)

    def test_explicit_parse_subcommand(self) -> None:
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = main.main(["parse", CHAT])
        self.assertEqual(code, 0)
        self.assertIn("participants", buffer.getvalue())

    def test_missing_file_returns_error_code(self) -> None:
        errors = io.StringIO()
        with redirect_stderr(errors):
            code = main.main(["parse", str(FIXTURES / "nope.txt")])
        self.assertEqual(code, 1)
        self.assertIn("no such file", errors.getvalue())


class OutputFileTests(unittest.TestCase):
    def test_out_writes_json(self) -> None:
        with TemporaryDirectory() as tmp:
            target = Path(tmp) / "parsed.json"
            buffer = io.StringIO()
            errors = io.StringIO()
            with redirect_stdout(buffer), redirect_stderr(errors):
                code = main.main([CHAT, "--json", "--out", str(target)])
            self.assertEqual(code, 0)
            self.assertEqual(buffer.getvalue(), "")
            self.assertIn("wrote", errors.getvalue())
            rows = json.loads(target.read_text(encoding="utf-8"))
            self.assertEqual(len(rows), 11)
            self.assertEqual(rows[1]["sender"], "Alice")

    def test_out_writes_ollama_payload(self) -> None:
        with TemporaryDirectory() as tmp:
            target = Path(tmp) / "payload.json"
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                code = main.main([CHAT, "--ollama", "--out", str(target)])
            self.assertEqual(code, 0)
            payload = json.loads(target.read_text(encoding="utf-8"))
            self.assertEqual(payload[0]["role"], "system")
            self.assertIn("WhatsApp group-chat summarizer", payload[0]["content"])
            self.assertIn("untrusted data", payload[0]["content"])
            self.assertIn("concise bullet points", payload[0]["content"])

    def test_out_writes_transcript(self) -> None:
        with TemporaryDirectory() as tmp:
            target = Path(tmp) / "chat.txt"
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                code = main.main([CHAT, "--out", str(target)])
            self.assertEqual(code, 0)
            content = target.read_text(encoding="utf-8")
            self.assertIn("=== 2026-10-20 ===", content)
            self.assertIn("Alice (03:37 PM)", content)


@unittest.skipUnless(_ollama_available(), "ollama server is not running")
class SummarizeTests(unittest.TestCase):
    def test_summarize_produces_output(self) -> None:
        buffer = io.StringIO()
        errors = io.StringIO()
        with redirect_stdout(buffer), redirect_stderr(errors):
            code = main.main(["summarize", CHAT, "--no-stream", "--max-chars", "4000"])
        self.assertEqual(code, 0)
        self.assertTrue(buffer.getvalue().strip())
        self.assertIn("parsed 11 messages", errors.getvalue())

    def test_summarize_saves_to_file(self) -> None:
        with TemporaryDirectory() as tmp:
            target = Path(tmp) / "summary.md"
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                code = main.main(
                    ["summarize", CHAT, "--no-stream", "--max-chars", "4000", "--save", str(target)]
                )
            self.assertEqual(code, 0)
            self.assertTrue(target.read_text(encoding="utf-8").strip())


class SummarizeWiringTests(unittest.TestCase):
    def test_invalid_max_chars_is_rejected(self) -> None:
        errors = io.StringIO()
        with redirect_stdout(io.StringIO()), redirect_stderr(errors):
            code = main.main(["summarize", CHAT, "--max-chars", "0"])
        self.assertEqual(code, 2)
        self.assertIn("--max-chars must be positive", errors.getvalue())

    def _fake_ollama(self, calls, text="Merged summary."):
        def build(model, *, think=False, num_ctx=8192):
            def chat(messages, on_token):
                calls.append(messages)
                if on_token is not None:
                    on_token(text)
                return text

            return chat

        return build

    def test_chunking_makes_several_calls_and_saves(self) -> None:
        calls = []
        with TemporaryDirectory() as tmp:
            target = Path(tmp) / "summary.md"
            with patch(
                "whatsapp_parser.summarize.call_ollama", side_effect=self._fake_ollama(calls)
            ), patch("whatsapp_parser.cli.save_last_summary") as save_state:
                with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                    code = main.main(
                        [
                            "summarize",
                            CHAT,
                            "--max-chars",
                            "120",
                            "--overlap-chars",
                            "0",
                            "--no-auto-since",
                            "--save",
                            str(target),
                        ]
                    )
            self.assertEqual(code, 0)
            self.assertGreater(len(calls), 1)
            self.assertEqual(target.read_text(encoding="utf-8").strip(), "Merged summary.")
            save_state.assert_called_once()

    def test_no_chunk_makes_a_single_call(self) -> None:
        calls = []
        with patch(
            "whatsapp_parser.summarize.call_ollama", side_effect=self._fake_ollama(calls)
        ), patch("whatsapp_parser.cli.save_last_summary"):
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                code = main.main(
                    ["summarize", CHAT, "--no-chunk", "--max-chars", "120", "--no-auto-since"]
                )
        self.assertEqual(code, 0)
        self.assertEqual(len(calls), 1)

    def test_failure_does_not_save_file_or_state(self) -> None:
        def build(model, *, think=False, num_ctx=8192):
            def chat(messages, on_token):
                raise RuntimeError("server exploded")

            return chat

        with TemporaryDirectory() as tmp:
            target = Path(tmp) / "summary.md"
            with patch(
                "whatsapp_parser.summarize.call_ollama", side_effect=build
            ), patch("whatsapp_parser.cli.save_last_summary") as save_state:
                errors = io.StringIO()
                with redirect_stdout(io.StringIO()), redirect_stderr(errors):
                    code = main.main(
                        ["summarize", CHAT, "--no-auto-since", "--save", str(target)]
                    )
            self.assertEqual(code, 3)
            self.assertFalse(target.exists())
            save_state.assert_not_called()
            self.assertIn("server exploded", errors.getvalue())


if __name__ == "__main__":
    unittest.main()
