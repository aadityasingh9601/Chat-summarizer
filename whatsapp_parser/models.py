from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


class MessageKind(StrEnum):
    TEXT = "text"
    MEDIA = "media"
    SYSTEM = "system"
    DELETED = "deleted"
    CALL = "call"


class MediaKind(StrEnum):
    IMAGE = "image"
    VIDEO = "video"
    AUDIO = "audio"
    VOICE = "voice"
    STICKER = "sticker"
    DOCUMENT = "document"
    GIF = "gif"
    FILE = "file"
    CONTACT = "contact"
    LOCATION = "location"
    POLL = "poll"


class DateOrder(StrEnum):
    DAY_FIRST = "day_first"
    MONTH_FIRST = "month_first"
    YEAR_FIRST = "year_first"


class Clock(StrEnum):
    H12 = "12h"
    H24 = "24h"


class ChatFamily(StrEnum):
    BRACKET = "bracket"
    DASH = "dash"
    YEAR_FIRST = "year_first"


@dataclass
class ChatFormat:
    family: ChatFamily
    date_order: DateOrder
    clock: Clock
    ambiguous: bool = False
    pattern: re.Pattern[str] | None = field(default=None, repr=False, compare=False)

    def describe(self) -> str:
        clock = "12h clock" if self.clock is Clock.H12 else "24h clock"
        return f"{self.family.value} / {self.date_order.value} / {clock}"


@dataclass
class Message:
    timestamp: datetime
    sender: str | None
    text: str
    kind: MessageKind = MessageKind.TEXT
    media_kind: MediaKind | None = None
    raw: str = ""

    @property
    def is_message(self) -> bool:
        return self.kind in (MessageKind.TEXT, MessageKind.MEDIA)


@dataclass
class Chat:
    messages: list[Message]
    format: ChatFormat
    participants: list[str] = field(default_factory=list)
    source: str | None = None
    preamble: list[str] = field(default_factory=list)
    skipped_lines: int = 0
    invalid_timestamps: int = 0

    @property
    def date_order_ambiguous(self) -> bool:
        return self.format.ambiguous

    @property
    def text_messages(self) -> list[Message]:
        return [m for m in self.messages if m.is_message]

    @property
    def start(self) -> datetime | None:
        return min((m.timestamp for m in self.messages), default=None)

    @property
    def end(self) -> datetime | None:
        return max((m.timestamp for m in self.messages), default=None)

    def to_dicts(self) -> list[dict[str, object]]:
        from .render import to_dicts

        return to_dicts(self)

    def to_json(self, indent: int | None = 2) -> str:
        from .render import to_json

        return to_json(self, indent=indent)

    def to_transcript(self, include_system: bool = False) -> str:
        from .render import to_transcript

        return to_transcript(self, include_system=include_system)

    def to_ollama_messages(
        self,
        style: str = "turns",
        include_system: bool = False,
        max_chars: int | None = None,
        system_prompt: str | None = None,
        task: str | None = None,
    ) -> list[dict[str, str]]:
        from .render import to_ollama_messages

        return to_ollama_messages(
            self,
            style=style,
            include_system=include_system,
            max_chars=max_chars,
            system_prompt=system_prompt,
            task=task,
        )

    def summary(self) -> dict[str, object]:
        return {
            "source": self.source,
            "format": self.format.describe(),
            "date_order_ambiguous": self.date_order_ambiguous,
            "participants": self.participants,
            "messages": len(self.messages),
            "text_messages": len(self.text_messages),
            "start": self.start.isoformat(sep=" ") if self.start else None,
            "end": self.end.isoformat(sep=" ") if self.end else None,
            "skipped_lines": self.skipped_lines,
            "invalid_timestamps": self.invalid_timestamps,
        }
