import argparse
import json
import sys
from pathlib import Path

from . import DateOrder, dropped_messages, parse_file

SUBCOMMANDS = ("parse", "summarize", "demo")
VERSION = "0.1.0"

DEFAULT_MODEL = "qwen3:0.6b"
DEFAULT_MAX_CHARS = 12000
DEFAULT_TASK = (
    "Summarize the supplied WhatsApp group chat messages for me.\n\n"
    "Prioritize:\n"
    "- Decisions and changes\n"
    "- Upcoming events, dates, and deadlines\n"
    "- Tasks, commitments, and who owns them\n"
    "- Important questions that remain unanswered\n"
    "- Announcements that require the user's attention\n"
    "- Requests or questions aimed at me\n"
    "- Plans with dates/times\n"
    "- Deadlines\n\n"
    "Rules:\n"
    "- Do not invent facts or infer commitments that were not made.\n"
    "- Preserve names, dates, times, amounts, and deadlines accurately.\n"
    "- Merge repeated messages and omit casual chatter, greetings, memes, forwards.\n"
    "- If nothing important happened, reply exactly: Nothing important.\n"
    "- Never invent details.\n\n"
    "Output:\n"
    "- 2–5 concise bullet points.\n"
    "- Include the group name as a heading.\n"
    "- Clearly label action items when applicable."
)


def _date_order(value: str) -> DateOrder | None:
    return {"auto": None, "day": DateOrder.DAY_FIRST, "month": DateOrder.MONTH_FIRST}[value]


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


def cmd_parse(args: argparse.Namespace) -> int:
    chat = parse_file(args.file, date_order=_date_order(args.date_order))

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
    chat = parse_file(args.file, date_order=_date_order(args.date_order))

    task = args.task or DEFAULT_TASK
    messages = chat.to_ollama_messages(
        style=args.style,
        include_system=args.include_system,
        max_chars=args.max_chars,
        task=task,
    )

    kept = len(chat.messages) - dropped_messages(
        chat, style=args.style, include_system=args.include_system, max_chars=args.max_chars
    )
    dropped = len(chat.messages) - kept
    print(
        f"parsed {len(chat.messages)} messages, {len(chat.participants)} participants "
        f"({chat.format.describe()})",
        file=sys.stderr,
    )
    if dropped > 0:
        print(
            f"warning: {dropped} of {len(chat.messages)} messages did not fit the "
            f"{args.max_chars} character budget and were omitted",
            file=sys.stderr,
        )

    try:
        import ollama
    except ImportError:
        print("error: the ollama package is not installed (pip install ollama)", file=sys.stderr)
        return 3

    try:
        response = ollama.chat(
            model=args.model,
            messages=messages,
            stream=not args.no_stream,
            think=True if args.think else None,
        )
    except Exception as exc:
        print(
            f"error: could not reach the ollama server ({exc}). "
            "Is it running? Start it with: ollama serve",
            file=sys.stderr,
        )
        return 3

    chunks: list[str] = []
    if args.no_stream:
        message = response["message"]
        if message.get("thinking"):
            print(message["thinking"], file=sys.stderr)
        content = message.get("content") or ""
        chunks.append(content)
        print(content)
    else:
        for part in response:
            message = part.get("message", {})
            if message.get("thinking"):
                print(message["thinking"], end="", file=sys.stderr, flush=True)
            content = message.get("content")
            if content:
                chunks.append(content)
                print(content, end="", flush=True)
    print()

    summary = "".join(chunks).strip()
    if args.save:
        _write_out(args.save, summary + "\n")
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
        help=f"character budget for the chat payload (default: {DEFAULT_MAX_CHARS})",
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
