"""Interactive WhatsApp digest builder.

Run ``python main.py`` with no arguments for the interactive flow. Passing
arguments keeps the original command-line behaviour, so the existing
``main(argv)`` entry point still works for scripts and tests.
"""

from __future__ import annotations

import sys
import threading
from datetime import date, datetime
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


def _chat_files(folder: Path) -> list[Path]:
    files = [
        path
        for path in folder.iterdir()
        if path.is_file()
        and path.suffix.lower() == ".txt"
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


def _validate_input_folder(value: str):
    text = value.strip()
    if not text:
        return "Please enter the folder containing the WhatsApp .txt exports."
    path = Path(text).expanduser()
    if not path.is_dir():
        return f"Folder not found: {path}"
    if not _chat_files(path):
        return f"No .txt chat files found in {path}"
    return True


def _validate_output_folder(value: str):
    text = value.strip()
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
            "No Ollama models found. Pull one first (for example: ollama pull qwen3:1.7b).",
            file=sys.stderr,
        )
        return 1

    model = _ask(questionary.select("Select an Ollama model:", choices=models))
    if not model:
        print("Cancelled.", file=sys.stderr)
        return 1

    folder = _ask(
        questionary.path(
            "Folder containing WhatsApp .txt exports:",
            only_directories=True,
            validate=_validate_input_folder,
        )
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
            questionary.text(
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

    output = _ask(
        questionary.path(
            "Output folder for the digest:",
            default=str(input_dir),
            only_directories=True,
            validate=_validate_output_folder,
        )
    )
    if output is None:
        print("Cancelled.", file=sys.stderr)
        return 1

    output_dir = Path(output.strip() or str(input_dir)).expanduser()
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
