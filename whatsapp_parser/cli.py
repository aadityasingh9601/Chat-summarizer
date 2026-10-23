import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path

from . import DateOrder, dropped_messages, parse_file, summarize
from .state import load_last_summary, save_last_summary

SUBCOMMANDS = ("parse", "summarize", "demo")
VERSION = "0.1.0"


def _date_order(value: str) -> DateOrder | None:
    return {"auto": None, "day": DateOrder.DAY_FIRST, "month": DateOrder.MONTH_FIRST}[value]


def _parse_date(value: str):
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        try:
            return datetime.strptime(value, "%d/%m/%Y").date()
        except ValueError:
            try:
                return datetime.strptime(value, "%m/%d/%Y").date()
            except ValueError:
                raise argparse.ArgumentTypeError(f"invalid date: {value} (use YYYY-MM-DD or DD/MM/YYYY)")


def _parse_datetime(value: str):
    try:
        return datetime.fromisoformat(value)
    except Exception:
        pass
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%d/%m/%Y %H:%M", "%m/%d/%Y %H:%M"):
        try:
            return datetime.strptime(value, fmt)
        except Exception:
            continue
    raise argparse.ArgumentTypeError(f"invalid datetime: {value}")


DEFAULT_MODEL = "qwen3:1.7b"
DEFAULT_MAX_CHARS = summarize.DEFAULT_MAX_CHARS
DEFAULT_OVERLAP_CHARS = summarize.DEFAULT_OVERLAP_CHARS


def _parse_date(value: str):
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        try:
            return datetime.strptime(value, "%d/%m/%Y").date()
        except ValueError:
            try:
                return datetime.strptime(value, "%m/%d/%Y").date()
            except ValueError:
                raise argparse.ArgumentTypeError(f"invalid date: {value} (use YYYY-MM-DD or DD/MM/YYYY)")


def _parse_datetime(value: str):
    try:
        return datetime.fromisoformat(value)
    except Exception:
        pass
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%d/%m/%Y %H:%M", "%m/%d/%Y %H:%M"):
        try:
            return datetime.strptime(value, fmt)
        except Exception:
            continue
    raise argparse.ArgumentTypeError(f"invalid datetime: {value}")


def _write_out(path: str, payload: str) -> None:
    Path(path).write_text(payload, encoding="utf-8", newline="\n")
    print(f"wrote {path}", file=sys.stderr)


def _summary_text(chat) -> str:
    summary = chat.summary()
    width = max(len(key) for key in summary)
    lines = [f"{key.ljust(width)} : {value}" for key, value in summary.items()]
    if chat.date_order_ambiguous:
        lines.append(
            "\nnote: every date in this file is <= 12, so day/month order could not be "
            "verified.\n      Parsed as month-first; pass --date-order day to override."
        )
    return "\n".join(lines)


def _add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("file", help="path to a WhatsApp export (.txt or .zip)")
    parser.add_argument(
        "--date-order",
        choices=["auto", "day", "month"],
        default="auto",
        help="force the date order; 'auto' detects it from the file (default: auto)",
    )
    parser.add_argument(
        "--include-system",
        action="store_true",
        help="keep WhatsApp system events (encryption notices, group changes) in the output",
    )
    parser.add_argument(
        "--from-date",
        type=_parse_date,
        default=None,
        help="summarize only messages from this date onward (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--since",
        type=_parse_datetime,
        default=None,
        help="summarize only messages after this datetime (ISO format)",
    )


def cmd_parse(args: argparse.Namespace) -> int:
    chat = parse_file(args.file, date_order=_date_order(args.date_order))
    if getattr(args, "from_date", None):
        chat = chat.filter_from_date(args.from_date)
    if getattr(args, "since", None):
        chat = chat.filter_since(args.since)

    if args.json:
        payload = chat.to_json()
    elif args.ollama:
        payload = json.dumps(
            chat.to_ollama_messages(
                style="turns",
                include_system=args.include_system,
                max_chars=args.max_chars,
            ),
            indent=2,
            ensure_ascii=False,
        )
    else:
        payload = _summary_text(chat) + "\n\n" + chat.to_transcript(include_system=args.include_system)

    if args.out:
        _write_out(args.out, payload)
    else:
        print(payload)
    return 0


def cmd_summarize(args: argparse.Namespace) -> int:
    if args.max_chars <= 0:
        print("error: --max-chars must be positive", file=sys.stderr)
        return 2
    if args.overlap_chars < 0:
        print("error: --overlap-chars cannot be negative", file=sys.stderr)
        return 2

    chat = parse_file(args.file, date_order=_date_order(args.date_order))

    source_key = str(Path(args.file).resolve())
    since = getattr(args, "since", None)
    from_date = getattr(args, "from_date", None)
    auto_since = False
    if since is None and from_date is None and not getattr(args, "no_auto_since", False):
        since = load_last_summary(source_key)
        auto_since = since is not None

    if since is not None:
        chat = chat.filter_since(since)
    elif from_date is not None:
        chat = chat.filter_from_date(from_date)

    if len(chat.messages) == 0:
        print("Nothing important.", file=sys.stdout)
        if args.save:
            _write_out(args.save, "Nothing important.\n")
        save_last_summary(source_key, datetime.now())
        return 0

    print(
        f"parsed {len(chat.messages)} messages, {len(chat.participants)} participants "
        f"({chat.format.describe()})",
        file=sys.stderr,
    )
    if auto_since and since is not None:
        print(f"summarizing since last run: {since.isoformat()}", file=sys.stderr)

    if args.no_chunk:
        dropped = dropped_messages(
            chat,
            style=args.style,
            include_system=args.include_system,
            max_chars=args.max_chars,
        )
        if dropped > 0:
            print(
                f"warning: {dropped} of {len(chat.messages)} messages did not fit the "
                f"{args.max_chars} character budget and were omitted",
                file=sys.stderr,
            )

    try:
        chat_fn = summarize.call_ollama(args.model, think=args.think)
    except ImportError:
        print("error: the ollama package is not installed (pip install ollama)", file=sys.stderr)
        return 3

    def on_progress(message: str) -> None:
        print(message, file=sys.stderr)

    on_token = None
    if not args.no_stream:
        on_token = lambda token: print(token, end="", flush=True)  # noqa: E731

    try:
        text = summarize.summarize(
            chat,
            chat_fn,
            max_chars=args.max_chars,
            overlap_chars=args.overlap_chars,
            style=args.style,
            include_system=args.include_system,
            task=args.task,
            chunk=not args.no_chunk,
            on_progress=on_progress,
            on_token=on_token,
        )
    except Exception as exc:
        print(
            f"error: could not reach the ollama server ({exc}). "
            "Is it running? Start it with: ollama serve",
            file=sys.stderr,
        )
        return 3

    text = text.strip()
    if on_token is None:
        print(text)
    else:
        print()

    if args.save:
        _write_out(args.save, text + "\n")
    save_last_summary(source_key, datetime.now())
    return 0


def cmd_demo(_args: argparse.Namespace) -> int:
    try:
        import ollama
    except ImportError:
        print("error: the ollama package is not installed (pip install ollama)", file=sys.stderr)
        return 3

    response = ollama.chat(
        model=DEFAULT_MODEL,
        messages=[{"role": "user", "content": "What is the capital of India?"}],
    )
    print(response["message"]["content"])
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="chat",
        description="Parse WhatsApp exports into LLM-ready messages.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    parse_parser = subparsers.add_parser("parse", help="parse a WhatsApp export")
    _add_common_args(parse_parser)
    parse_parser.add_argument(
        "--json",
        action="store_true",
        help="write the parsed messages as JSON instead of a transcript",
    )
    parse_parser.add_argument(
        "--ollama",
        action="store_true",
        help="write the messages in ollama chat format instead of a transcript",
    )
    parse_parser.add_argument(
        "--max-chars",
        type=int,
        default=None,
        help="cap the character budget of the LLM payload",
    )
    parse_parser.add_argument(
        "--out",
        metavar="FILE",
        default=None,
        help="write the output to FILE as UTF-8 instead of stdout",
    )
    parse_parser.set_defaults(func=cmd_parse)

    summarize_parser = subparsers.add_parser(
        "summarize", help="summarize a WhatsApp export with a local ollama model"
    )
    _add_common_args(summarize_parser)
    summarize_parser.add_argument("--model", default=DEFAULT_MODEL, help="ollama model to use")
    summarize_parser.add_argument(
        "--style",
        choices=["transcript", "turns"],
        default="transcript",
        help="how to feed the chat to the model (default: transcript)",
    )
    summarize_parser.add_argument(
        "--max-chars",
        type=int,
        default=DEFAULT_MAX_CHARS,
        help=(
            "character budget per LLM call; longer chats are split into chunks "
            f"(default: {DEFAULT_MAX_CHARS})"
        ),
    )
    summarize_parser.add_argument(
        "--overlap-chars",
        type=int,
        default=DEFAULT_OVERLAP_CHARS,
        help=(
            "characters of the previous chunk repeated at the start of each chunk "
            f"(default: {DEFAULT_OVERLAP_CHARS})"
        ),
    )
    summarize_parser.add_argument(
        "--no-chunk",
        action="store_true",
        help="summarize in a single call, truncating anything beyond --max-chars",
    )
    summarize_parser.add_argument(
        "--task",
        default=None,
        help="custom instruction for the model (replaces the default bullet summary task)",
    )
    summarize_parser.add_argument(
        "--save", metavar="FILE", default=None, help="also write the summary to FILE as UTF-8"
    )
    summarize_parser.add_argument(
        "--think", action="store_true", help="enable the model's thinking mode"
    )
    summarize_parser.add_argument(
        "--no-stream", action="store_true", help="wait for the full reply instead of streaming"
    )
    summarize_parser.add_argument(
        "--no-auto-since", action="store_true", help="ignore last summary timestamp and summarize all messages"
    )
    summarize_parser.set_defaults(func=cmd_summarize)

    demo_parser = subparsers.add_parser("demo", help="run the ollama smoke test")
    demo_parser.set_defaults(func=cmd_demo)

    return parser


def _with_default_command(argv: list[str]) -> list[str]:
    if not argv:
        return argv
    if any(token in SUBCOMMANDS for token in argv):
        return argv
    if any(token in ("-h", "--help", "--version") for token in argv):
        return argv
    return ["parse", *argv]


def main(argv: list[str] | None = None) -> int:
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    args = parser.parse_args(_with_default_command(raw_argv))
    try:
        return args.func(args)
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except BrokenPipeError:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
