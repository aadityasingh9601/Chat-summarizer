from __future__ import annotations

import io
import os
import shutil
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import date, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import main
from whatsapp_parser.state import load_last_summary, save_last_summary

FIXTURES = Path(__file__).parent / "fixtures"
CHAT = FIXTURES / "windows_desktop_us.txt"


class Prompt:
    def __init__(self, value):
        self.value = value

    def ask(self):
        return self.value


def fake_ollama(model, **_kwargs):
    def chat(_messages, _on_token):
        return "Fake summary."

    return chat


def failing_ollama(model, **_kwargs):
    def chat(_messages, _on_token):
        raise RuntimeError("model exploded")

    return chat


class HelperTests(unittest.TestCase):
    def test_chat_files_ignores_macos_artifacts_and_other_extensions(self) -> None:
        with TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "b.txt").write_text("x")
            (folder / "A.TXT").write_text("x")
            (folder / "notes.md").write_text("x")
            (folder / "._hidden.txt").write_text("x")
            names = [path.name for path in main._chat_files(folder)]
            self.assertEqual(names, ["A.TXT", "b.txt"])

    def test_build_digest_uses_filename_as_heading(self) -> None:
        digest = main._build_digest(
            [("group one", "First summary."), ("group-two", "Second summary.")],
            day=date(2026, 10, 23),
        )
        self.assertIn("# WhatsApp digest — 2026-10-23", digest)
        self.assertIn("## group one\n\nFirst summary.", digest)
        self.assertIn("## group-two\n\nSecond summary.", digest)
        self.assertTrue(digest.endswith("\n"))

    def test_validate_date_accepts_empty_and_iso_dates(self) -> None:
        self.assertIs(main._validate_date(""), True)
        self.assertIs(main._validate_date("2026-10-23"), True)
        self.assertIsInstance(main._validate_date("23/10/2026"), str)
        self.assertIsInstance(main._validate_date("2026-13-01"), str)

    def test_validate_input_folder_requires_a_txt_file(self) -> None:
        with TemporaryDirectory() as tmp:
            empty = Path(tmp) / "empty"
            empty.mkdir()
            self.assertIsInstance(main._validate_input_folder(str(empty)), str)

            filled = Path(tmp) / "filled"
            filled.mkdir()
            shutil.copy(CHAT, filled / "chat.txt")
            self.assertIs(main._validate_input_folder(str(filled)), True)

    def test_validate_output_folder_rejects_files(self) -> None:
        with TemporaryDirectory() as tmp:
            target = Path(tmp) / "not-a-folder"
            target.write_text("x")
            self.assertIsInstance(main._validate_output_folder(str(target)), str)
            self.assertIs(
                main._validate_output_folder(str(Path(tmp) / "new-folder")), True
            )

    def test_list_models_reports_ollama_down(self) -> None:
        errors = io.StringIO()
        with patch("ollama.list", side_effect=ConnectionError), redirect_stderr(errors):
            models = main._list_models()
        self.assertIsNone(models)
        self.assertIn(
            "Ollama is not running. Please start Ollama and try again.", errors.getvalue()
        )

    def test_list_models_reads_names_from_response(self) -> None:
        response = {"models": [{"model": "qwen3:1.7b"}, {"name": "gemma:latest"}]}
        with patch("ollama.list", return_value=response):
            self.assertEqual(main._list_models(), ["qwen3:1.7b", "gemma:latest"])


class InteractiveTests(unittest.TestCase):
    def _run(self, folder: Path, output: Path, dates, *, chat_fn=fake_ollama):
        answers = list(dates)
        paths = iter([str(folder), str(output)])

        def path_prompt(*_args, **_kwargs):
            return Prompt(next(paths))

        def text_prompt(*_args, **_kwargs):
            return Prompt(answers.pop(0) if answers else "")

        buffer = io.StringIO()
        errors = io.StringIO()
        with patch("main._list_models", return_value=["test-model"]), patch(
            "main.call_ollama", side_effect=chat_fn
        ), patch("questionary.select", return_value=Prompt("test-model")), patch(
            "questionary.path", side_effect=path_prompt
        ), patch("questionary.text", side_effect=text_prompt):
            with redirect_stdout(buffer), redirect_stderr(errors):
                code = main.interactive()
        return code, buffer.getvalue(), errors.getvalue()

    def test_single_file_writes_digest_and_updates_state(self) -> None:
        with TemporaryDirectory() as tmp:
            folder = Path(tmp) / "input"
            folder.mkdir()
            chat = folder / "study-group.txt"
            shutil.copy(CHAT, chat)
            output = Path(tmp) / "output"
            state = Path(tmp) / "state.json"

            with patch.dict(os.environ, {"CHAT_SUMMARIZER_STATE": str(state)}):
                code, out, _err = self._run(folder, output, [])

                self.assertEqual(code, 0)
                digest = output / f"digest_{date.today().isoformat()}.md"
                content = digest.read_text(encoding="utf-8")
                self.assertIn("## study-group", content)
                self.assertIn("Fake summary.", content)
                self.assertIn("Fake summary.", out)
                self.assertIn(f"✓ Done! Summary saved to {digest.resolve()}", out)
                self.assertIsNotNone(load_last_summary(str(chat.resolve())))

    def test_single_file_uses_saved_date_as_prompt_default(self) -> None:
        with TemporaryDirectory() as tmp:
            folder = Path(tmp) / "input"
            folder.mkdir()
            chat = folder / "study-group.txt"
            shutil.copy(CHAT, chat)
            state = Path(tmp) / "state.json"

            with patch.dict(os.environ, {"CHAT_SUMMARIZER_STATE": str(state)}):
                save_last_summary(str(chat.resolve()), datetime(2026, 10, 21, 12, 0))
                captured = {}

                def text_prompt(_message, **kwargs):
                    captured["default"] = kwargs.get("default")
                    return Prompt("")

                paths = iter([str(folder), str(folder)])
                with patch("main._list_models", return_value=["test-model"]), patch(
                    "main.call_ollama", side_effect=fake_ollama
                ), patch("questionary.select", return_value=Prompt("test-model")), patch(
                    "questionary.path", side_effect=lambda *a, **k: Prompt(next(paths))
                ), patch("questionary.text", side_effect=text_prompt):
                    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                        self.assertEqual(main.interactive(), 0)

                self.assertEqual(captured["default"], "2026-10-21")

    def test_multiple_files_use_per_file_state_without_date_prompt(self) -> None:
        with TemporaryDirectory() as tmp:
            folder = Path(tmp) / "input"
            folder.mkdir()
            first = folder / "first.txt"
            second = folder / "second.txt"
            shutil.copy(CHAT, first)
            shutil.copy(CHAT, second)
            output = Path(tmp) / "output"
            state = Path(tmp) / "state.json"

            with patch.dict(os.environ, {"CHAT_SUMMARIZER_STATE": str(state)}):
                save_last_summary(str(first.resolve()), datetime(2030, 1, 1))

                def text_prompt(*_args, **_kwargs):
                    raise AssertionError("date prompt should be skipped for folders")

                paths = iter([str(folder), str(output)])
                with patch("main._list_models", return_value=["test-model"]), patch(
                    "main.call_ollama", side_effect=fake_ollama
                ), patch("questionary.select", return_value=Prompt("test-model")), patch(
                    "questionary.path", side_effect=lambda *a, **k: Prompt(next(paths))
                ), patch("questionary.text", side_effect=text_prompt):
                    with redirect_stdout(io.StringIO()) as out, redirect_stderr(
                        io.StringIO()
                    ) as err:
                        code = main.interactive()

                self.assertEqual(code, 0)
                content = (output / f"digest_{date.today().isoformat()}.md").read_text(
                    encoding="utf-8"
                )
                self.assertNotIn("## first", content)
                self.assertIn("## second", content)
                self.assertIn("No new messages in first.txt", err.getvalue())
                self.assertIn("Fake summary.", out.getvalue())

    def test_model_failure_returns_error_without_writing_digest(self) -> None:
        with TemporaryDirectory() as tmp:
            folder = Path(tmp) / "input"
            folder.mkdir()
            shutil.copy(CHAT, folder / "broken.txt")
            output = Path(tmp) / "output"
            state = Path(tmp) / "state.json"

            with patch.dict(os.environ, {"CHAT_SUMMARIZER_STATE": str(state)}):
                code, _out, err = self._run(folder, output, [], chat_fn=failing_ollama)

            self.assertEqual(code, 1)
            self.assertFalse((output / f"digest_{date.today().isoformat()}.md").exists())
            self.assertIn("Failed to summarize broken.txt", err)
            self.assertIn("model exploded", err)


if __name__ == "__main__":
    unittest.main()
