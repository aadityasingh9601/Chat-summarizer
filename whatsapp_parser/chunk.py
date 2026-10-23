from __future__ import annotations

from .models import Chat, Message, MessageKind
from .render import render_message_line

DAY_HEADER_COST = len("=== 0000-00-00 ===") + 1
MAX_OVERLAP_FRACTION = 4


def _visible(chat: Chat, include_system: bool) -> list[Message]:
    return [
        message
        for message in chat.messages
        if include_system or message.kind is not MessageKind.SYSTEM
    ]


def _line_cost(message: Message, clock, previous_day) -> int:
    cost = len(render_message_line(message, clock)) + 1
    if previous_day is None or message.timestamp.date() != previous_day:
        cost += DAY_HEADER_COST
    return cost


def _overlap_tail(messages: list[Message], overlap_chars: int, clock) -> list[Message]:
    """Return the trailing messages of ``messages`` that fit the overlap budget."""
    if overlap_chars <= 0:
        return []
    tail: list[Message] = []
    used = 0
    for message in reversed(messages):
        cost = _line_cost(message, clock, message.timestamp.date())
        if tail and used + cost > overlap_chars:
            break
        tail.append(message)
        used += cost
    tail.reverse()
    return tail


def _subchat(chat: Chat, messages: list[Message]) -> Chat:
    participants: list[str] = []
    for message in messages:
        if message.sender and message.sender not in participants:
            participants.append(message.sender)
    return Chat(
        messages=messages,
        format=chat.format,
        participants=participants,
        source=chat.source,
    )


def chunk_chat(
    chat: Chat,
    max_chars: int,
    overlap_chars: int = 0,
    *,
    include_system: bool = False,
) -> list[Chat]:
    """Split a chat into message-aligned chunks of at most ``max_chars``.

    Messages are never split, so multi-line bodies and indented continuation
    lines stay intact. Each chunk after the first repeats the trailing
    ``overlap_chars`` of the previous chunk (whole messages only) so the model
    keeps context across boundaries.
    """
    if max_chars <= 0:
        raise ValueError("max_chars must be positive")

    messages = _visible(chat, include_system)
    if not messages:
        return []

    clock = chat.format.clock
    overlap_chars = min(overlap_chars, max_chars // MAX_OVERLAP_FRACTION)
    chunks: list[Chat] = []
    index = 0
    total = len(messages)
    overlap: list[Message] = []

    while index < total:
        current: list[Message] = list(overlap)
        used = 0
        day = None
        for message in current:
            used += _line_cost(message, clock, day)
            day = message.timestamp.date()

        added = 0
        while index < total:
            message = messages[index]
            cost = _line_cost(message, clock, day)
            if added > 0 and used + cost > max_chars:
                break
            current.append(message)
            used += cost
            day = message.timestamp.date()
            index += 1
            added += 1

        chunks.append(_subchat(chat, current))
        overlap = _overlap_tail(current, overlap_chars, clock)

    return chunks
