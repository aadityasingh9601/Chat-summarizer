from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path

from .detect import build_timestamp, detect_format
from .models import Chat, ChatFormat, DateOrder, MediaKind, Message, MessageKind
from .normalize import iter_text_lines, normalize_line, normalize_text, resolve_transcript

MEDIA_LABELS: dict[MediaKind, str] = {
    MediaKind.IMAGE: "[image]",
    MediaKind.VIDEO: "[video]",
    MediaKind.AUDIO: "[audio]",
    MediaKind.VOICE: "[voice note]",
    MediaKind.STICKER: "[sticker]",
    MediaKind.DOCUMENT: "[document]",
    MediaKind.GIF: "[gif]",
    MediaKind.FILE: "[file]",
    MediaKind.CONTACT: "[contact]",
    MediaKind.LOCATION: "[location]",
    MediaKind.POLL: "[poll]",
}

MEDIA_FILENAME_HINTS: tuple[tuple[re.Pattern[str], MediaKind], ...] = (
    (re.compile(r"PHOTO|IMG[-_]", re.I), MediaKind.IMAGE),
    (re.compile(r"VIDEO|VID[-_]", re.I), MediaKind.VIDEO),
    (re.compile(r"PTT[-_]|VOICE", re.I), MediaKind.VOICE),
    (re.compile(r"AUDIO|AUD[-_]", re.I), MediaKind.AUDIO),
    (re.compile(r"STK[-_]|STICKER", re.I), MediaKind.STICKER),
    (re.compile(r"GIF", re.I), MediaKind.GIF),
    (re.compile(r"DOC|PDF", re.I), MediaKind.DOCUMENT),
)

MEDIA_KEYWORDS: tuple[tuple[re.Pattern[str], MediaKind], ...] = (
    (re.compile(r"contact\s+card|contact\s+omitted", re.I), MediaKind.CONTACT),
    (re.compile(r"location\s+omitted|location\s+shared", re.I), MediaKind.LOCATION),
    (re.compile(r"poll\s+omitted", re.I), MediaKind.POLL),
    (re.compile(r"sticker\s+omitted", re.I), MediaKind.STICKER),
    (re.compile(r"gif\s+omitted", re.I), MediaKind.GIF),
    (re.compile(r"image\s+omitted|photo\s+omitted", re.I), MediaKind.IMAGE),
    (re.compile(r"video\s+omitted", re.I), MediaKind.VIDEO),
    (re.compile(r"audio\s+omitted", re.I), MediaKind.AUDIO),
    (re.compile(r"document\s+omitted", re.I), MediaKind.DOCUMENT),
)

MEDIA_PLACEHOLDER = re.compile(
    r"^(?:<\s*media\s+omitted\s*>|<\s*attached:\s*(?P<attached>.+)>|(?P<named>.+?)\s*\(file attached\))$",
    re.I,
)

DELETED_PATTERNS = {
    "this message was deleted",
    "you deleted this message",
    "message deleted",
}

CALL_PATTERNS = {
    "missed voice call": "[missed voice call]",
    "missed video call": "[missed video call]",
}

SYSTEM_TEMPLATES: tuple[re.Pattern[str], ...] = (
    re.compile(r"^.+ created (the )?group\b", re.I),
    re.compile(r"joined via\b", re.I),
    re.compile(r"^.+ (added|removed) .+$", re.I),
    re.compile(r"^.+ left$", re.I),
    re.compile(r"^You joined\b", re.I),
    re.compile(r"^.+ changed (the |this group's )(subject|icon|description|group)", re.I),
    re.compile(r"^Messages? (and calls )?(to this chat|are now protected)", re.I),
    re.compile(r"^Your security code with .+ changed", re.I),
    re.compile(r"^Messages and calls are end-to-end encrypted", re.I),
    re.compile(r"^Your messages and calls are protected", re.I),
)

WHITESPACE_RUN = re.compile(r"[ \t]+")
BLANK_RUN = re.compile(r"\n{3,}")


def clean_text(text: str) -> str:
    lines = [WHITESPACE_RUN.sub(" ", line).strip() for line in text.split("\n")]
    return BLANK_RUN.sub("\n\n", "\n".join(lines)).strip()


def _media_kind_from_filename(name: str) -> MediaKind:
    for pattern, kind in MEDIA_FILENAME_HINTS:
        if pattern.search(name):
            return kind
    return MediaKind.FILE


def classify(body: str) -> tuple[MessageKind, MediaKind | None, str]:
    stripped = body.strip()
    lowered = stripped.lower()

    if lowered in DELETED_PATTERNS:
        return MessageKind.DELETED, None, "[deleted]"

    if lowered in CALL_PATTERNS:
        return MessageKind.CALL, None, CALL_PATTERNS[lowered]

    media_match = MEDIA_PLACEHOLDER.match(stripped)
    if media_match:
        filename = media_match.group("attached") or media_match.group("named") or ""
        kind = _media_kind_from_filename(filename)
        return MessageKind.MEDIA, kind, MEDIA_LABELS[kind]

    for pattern, keyword_kind in MEDIA_KEYWORDS:
        if pattern.search(stripped):
            return MessageKind.MEDIA, keyword_kind, MEDIA_LABELS[keyword_kind]

    return MessageKind.TEXT, None, body


def _looks_like_system(body: str) -> bool:
    return any(pattern.search(body.strip()) for pattern in SYSTEM_TEMPLATES)


class _Builder:
    def __init__(
        self, timestamp: datetime, sender: str | None, body: str, forced_system: bool = False
    ) -> None:
        self.timestamp = timestamp
        self.sender = sender
        self.forced_system = forced_system
        self.parts: list[str] = [body]

    def append(self, line: str) -> None:
        self.parts.append(line)

    def build(self) -> Message:
        raw = "\n".join(self.parts)
        kind, media_kind, text = classify(raw)
        sender = self.sender
        if kind is MessageKind.TEXT and (self.forced_system or _looks_like_system(raw)):
            kind = MessageKind.SYSTEM
        if kind is MessageKind.SYSTEM:
            sender = None
        return Message(
            timestamp=self.timestamp,
            sender=sender,
            text=clean_text(text),
            kind=kind,
            media_kind=media_kind,
            raw=raw,
        )


def parse_lines(
    lines: Iterable[str],
    fmt: ChatFormat,
    *,
    source: str | None = None,
) -> Chat:
    messages: list[Message] = []
    preamble: list[str] = []
    current: _Builder | None = None
    skipped = 0
    invalid = 0
    pattern = fmt.pattern

    for line in lines:
        line = normalize_line(line)
        match = pattern.match(line) if pattern else None
        if match is None:
            if current is None:
                if line.strip():
                    preamble.append(line.strip())
                    skipped += 1
            else:
                current.append(line)
            continue

        if current is not None:
            messages.append(current.build())
            current = None

        timestamp = build_timestamp(
            match.group("d1"),
            match.group("d2"),
            match.group("d3"),
            int(match.group("h")),
            int(match.group("mi")),
            int(match.group("s") or 0),
            match.group("ampm"),
            fmt.date_order,
        )
        if timestamp is None:
            invalid += 1
            continue

        rest = match.group("rest")
        forced_system = False
        if rest.startswith("- "):
            rest = rest[2:].strip()
            sender = None
            body = rest
            forced_system = True
        elif ": " in rest:
            sender, body = rest.split(": ", 1)
            sender = sender.strip()
            if not sender:
                sender = None
                body = rest
                forced_system = True
        else:
            sender = None
            body = rest
            forced_system = True

        current = _Builder(timestamp, sender, body, forced_system=forced_system)

    if current is not None:
        messages.append(current.build())

    participants: list[str] = []
    for message in messages:
        if message.sender and message.sender not in participants:
            participants.append(message.sender)

    return Chat(
        messages=messages,
        format=fmt,
        participants=participants,
        source=source,
        preamble=preamble,
        skipped_lines=skipped,
        invalid_timestamps=invalid,
    )


def parse_file(
    path: str | Path,
    *,
    date_order: DateOrder | None = None,
    default_order: DateOrder = DateOrder.MONTH_FIRST,
) -> Chat:
    with resolve_transcript(path) as transcript:
        fmt = detect_format(
            iter_text_lines(transcript),
            date_order=date_order,
            default_order=default_order,
        )
        return parse_lines(iter_text_lines(transcript), fmt, source=str(path))


def parse_text(
    text: str,
    *,
    date_order: DateOrder | None = None,
    default_order: DateOrder = DateOrder.MONTH_FIRST,
    source: str | None = None,
) -> Chat:
    lines = normalize_text(text).split("\n")
    fmt = detect_format(iter(lines), date_order=date_order, default_order=default_order)
    return parse_lines(lines, fmt, source=source)
