"""Interactive WhatsApp digest builder.

Run ``python main.py`` with no arguments for the interactive flow. Passing
arguments keeps the original command-line behaviour, so the existing
``main(argv)`` entry point still works for scripts and tests.

Optional defaults are read from ``.chat_digest_config.json`` next to this file
(override with ``CHAT_DIGEST_CONFIG``). Edit that file manually to change the
default model or folders; the interactive flow never writes to it.
"""

from __future__ import annotations

import json
import os
import sys
import threading
from datetime import date, datetime
from functools import partial
from pathlib import Path

from whatsapp_parser import parse_file
from whatsapp_parser.cli import main as _cli_main
from whatsapp_parser.state import load_last_summary, save_last_summary
from whatsapp_parser.summarize import (
    DEFAULT_MAX_CHARS,
    DEFAULT_OVERLAP_CHARS,
    call_ollama,
    summarize,
)

SPINNER_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
CONFIG_FILENAME = ".chat_digest_config.json"


def _config_path() -> Path:
    override = os.environ.get("CHAT_DIGEST_CONFIG")
    if override:
        return Path(override).expanduser()
    return Path(__file__).resolve().parent / CONFIG_FILENAME


def _load_defaults() -> dict[str, str]:
    path = _config_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    defaults: dict[str, str] = {}
    for key in ("model", "input_folder", "output_folder"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            defaults[key] = value.strip()
    return defaults


def _chat_files(folder: Path) -> list[Path]:
    files = [
        path
        for path in folder.iterdir()
        if path.is_file()
        and path.suffix.lower() in (".txt", ".zip")
        and not path.name.startswith("._")
    ]
    return sorted(files, key=lambda path: path.name.casefold())


def _build_digest(results: list[tuple[str, str]], day: date | None = None) -> str:
    today = (day or date.today()).isoformat()
    sections = [f"# WhatsApp digest — {today}"]
    for name, summary in results:
        sections.append(f"## {name}\n\n{summary.strip()}")
    return "\n\n".join(sections) + "\n"


def _last_summary_date(path: Path) -> date | None:
    saved = load_last_summary(str(path.resolve()))
    return saved.date() if saved is not None else None


def _list_models() -> list[str] | None:
    try:
        import ollama
    except ImportError:
        print(
            "The ollama Python package is not installed (pip install ollama).",
            file=sys.stderr,
        )
        return None

    try:
        response = ollama.list()
    except Exception:
        print("Ollama is not running. Please start Ollama and try again.", file=sys.stderr)
        return None

    raw = response.get("models", []) if isinstance(response, dict) else getattr(
        response, "models", []
    )
    models: list[str] = []
    for item in raw:
        if isinstance(item, dict):
            name = item.get("model") or item.get("name")
        else:
            name = getattr(item, "model", None) or getattr(item, "name", None)
        if name and name not in models:
            models.append(name)
    return models


def _validate_input_folder(value: str, default: str = ""):
    text = value.strip() or default.strip()
    if not text:
        return "Please enter the folder containing the WhatsApp .txt/.zip exports."
    path = Path(text).expanduser()
    if not path.exists():
        return "Folder does not exist."
    if not path.is_dir():
        return "Please select a folder."
    if not _chat_files(path):
        return "No .txt or .zip files present in the folder."
    return True


def _validate_output_folder(value: str, default: str = ""):
    text = value.strip() or default.strip()
    if not text:
        return "Please enter an output folder."
    path = Path(text).expanduser()
    if path.exists() and not path.is_dir():
        return f"Not a folder: {path}"
    return True


def _validate_date(value: str):
    text = value.strip()
    if not text:
        return True
    try:
        datetime.strptime(text, "%Y-%m-%d")
    except ValueError:
        return "Use YYYY-MM-DD (or press Enter to summarize all messages)."
    return True


def _ask(prompt):
    try:
        return prompt.ask()
    except (EOFError, KeyboardInterrupt):
        return None


def _ask_path(questionary, label: str, default: str, validator):
    message = f"{label} [{default}]:" if default else f"{label}:"
    answer = _ask(questionary.path(message, only_directories=True, validate=validator))
    if answer is None:
        return None
    return answer.strip() or default


def _is_tty() -> bool:
    try:
        return sys.stdin.isatty() and sys.stdout.isatty()
    except Exception:
        return False


class _AutoPrompter:
    """Non-interactive prompter that returns saved defaults."""

    def __init__(self, defaults: dict[str, str], models: list[str], input_dir_hint: str = ""):
        self.defaults = defaults
        self.models = models
        self.input_dir_hint = input_dir_hint

    def select(self, message, choices=None, default=None, **_kwargs):
        if default is not None and default in (choices or []):
            return _Prompt(default)
        if self.defaults.get("model") in (choices or []):
            return _Prompt(self.defaults["model"])
        if choices:
            return _Prompt(choices[0])
        raise RuntimeError("No choices available")

    def path(self, message, only_directories=False, validate=None, default=None, **_kwargs):
        if "input" in message.lower():
            text = self.defaults.get("input_folder") or default or ""
        elif "output" in message.lower():
            text = self.defaults.get("output_folder") or default or ""
        else:
            text = default or ""
        text = str(text).strip()
        if validate is not None:
            result = validate(text)
            if result is not True:
                raise RuntimeError(str(result))
        return _Prompt(text)

    def text(self, message, default="", validate=None, **_kwargs):
        if validate is not None:
            result = validate(default or "")
            if result is not True:
                raise RuntimeError(str(result))
        return _Prompt(default or "")


class _Prompt:
    def __init__(self, value):
        self.value = value

    def ask(self):
        return self.value


class _Spinner:
    """Tiny stderr spinner driven by a daemon thread."""

    def __init__(self, label: str) -> None:
        self.label = label
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._animated = sys.stderr.isatty()

    def __enter__(self) -> "_Spinner":
        if not self._animated:
            print(self.label, file=sys.stderr, flush=True)
            return self
        self._thread = threading.Thread(target=self._spin, daemon=True)
        self._thread.start()
        return self

    def _spin(self) -> None:
        index = 0
        while not self._stop.is_set():
            frame = SPINNER_FRAMES[index % len(SPINNER_FRAMES)]
            print(f"\r{frame} {self.label}", end="", file=sys.stderr, flush=True)
            index += 1
            self._stop.wait(0.1)

    def __exit__(self, *_exc) -> None:
        if self._thread is None:
            return
        self._stop.set()
        self._thread.join()
        clear = "\r" + " " * (len(self.label) + 2) + "\r"
        print(clear, end="", file=sys.stderr, flush=True)


def interactive() -> int:
    try:
        import questionary
    except ImportError:
        print(
            "The questionary package is not installed (pip install questionary).",
            file=sys.stderr,
        )
        return 3

    models = _list_models()
    if models is None:
        return 1
    if not models:
        print(
            "No Ollama models found. Pull one first (for example: ollama pull gemma4:e4b).",
            file=sys.stderr,
        )
        return 1

    defaults = _load_defaults()
    prompter = questionary
    if not _is_tty() and defaults.get("model") and defaults.get("input_folder"):
        prompter = _AutoPrompter(defaults, models)
    saved_model = defaults.get("model")
    model_default = saved_model if saved_model in models else None
    if saved_model and model_default is None and _is_tty():
        print(
            f"Default model {saved_model!r} is not installed; pick another.",
            file=sys.stderr,
        )

    model = _ask(
        prompter.select(
            "Select an Ollama model:",
            choices=models,
            default=model_default,
        )
    )
    if not model:
        print("Cancelled.", file=sys.stderr)
        return 1

    input_default = defaults.get("input_folder", "")
    folder = _ask_path(
        prompter,
        "Folder containing WhatsApp .txt/.zip exports",
        input_default,
        partial(_validate_input_folder, default=input_default),
    )
    if not folder:
        print("Cancelled.", file=sys.stderr)
        return 1

    input_dir = Path(folder).expanduser().resolve()
    files = _chat_files(input_dir)

    start_date: date | None = None
    since_by_file: dict[Path, datetime] = {}
    if len(files) == 1:
        last_date = _last_summary_date(files[0])
        default = last_date.isoformat() if last_date is not None else ""
        answer = _ask(
            prompter.text(
                "Summarize from date (YYYY-MM-DD, Enter for all messages):",
                default=default,
                validate=_validate_date,
            )
        )
        if answer is None:
            print("Cancelled.", file=sys.stderr)
            return 1
        answer = answer.strip()
        if answer:
            start_date = datetime.strptime(answer, "%Y-%m-%d").date()
    else:
        for path in files:
            saved = load_last_summary(str(path.resolve()))
            if saved is not None:
                since_by_file[path] = saved

    output_default = defaults.get("output_folder") or str(input_dir)
    output = _ask_path(
        prompter,
        "Output folder for the digest",
        output_default,
        partial(_validate_output_folder, default=output_default),
    )
    if output is None:
        print("Cancelled.", file=sys.stderr)
        return 1

    output_dir = Path(output or str(input_dir)).expanduser()
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        print(f"Could not create output folder {output_dir}: {exc}", file=sys.stderr)
        return 1

    try:
        chat_fn = call_ollama(model, show_thinking=False)
    except ImportError:
        print(
            "The ollama Python package is not installed (pip install ollama).",
            file=sys.stderr,
        )
        return 3

    results: list[tuple[str, str]] = []
    failures: list[tuple[str, str]] = []

    for path in files:
        try:
            with _Spinner(f"Summarizing {path.name}..."):
                chat = parse_file(path)
                if start_date is not None:
                    chat = chat.filter_from_date(start_date)
                elif path in since_by_file:
                    chat = chat.filter_since(since_by_file[path])

                if not chat.messages:
                    summary = ""
                    empty = True
                else:
                    empty = False
                    summary = summarize(
                        chat,
                        chat_fn,
                        max_chars=DEFAULT_MAX_CHARS,
                        overlap_chars=DEFAULT_OVERLAP_CHARS,
                    )

            if empty:
                print(f"No new messages in {path.name}; skipped.", file=sys.stderr)
                continue

            text = summary.strip()
            print(text)
            print()
            results.append((path.stem, text))
            save_last_summary(str(path.resolve()), datetime.now())
        except KeyboardInterrupt:
            print("\nCancelled.", file=sys.stderr)
            return 130
        except Exception as exc:
            print(f"\n✗ Failed to summarize {path.name}: {exc}", file=sys.stderr)
            failures.append((path.name, str(exc)))

    if not results:
        print("No summaries were generated.", file=sys.stderr)
        return 1

    today = date.today()
    digest = output_dir / f"digest_{today.isoformat()}.md"
    try:
        digest.write_text(_build_digest(results, today), encoding="utf-8", newline="\n")
    except OSError as exc:
        print(f"Could not write {digest}: {exc}", file=sys.stderr)
        return 1

    print(f"✓ Done! Summary saved to {digest.resolve()}")
    if failures:
        print(f"⚠ {len(failures)} file(s) could not be summarized.", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    """Interactive when run without arguments, otherwise the original CLI."""
    if argv is None and len(sys.argv) == 1:
        return interactive()
    return _cli_main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
