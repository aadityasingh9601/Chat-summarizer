from __future__ import annotations

import unittest
from datetime import datetime, timedelta

from whatsapp_parser.chunk import chunk_chat
from whatsapp_parser.models import (
    Chat,
    ChatFamily,
    ChatFormat,
    Clock,
    DateOrder,
    Message,
    MessageKind,
)
from whatsapp_parser.render import render_message_line, to_transcript

FORMAT = ChatFormat(
    family=ChatFamily.BRACKET,
    date_order=DateOrder.MONTH_FIRST,
    clock=Clock.H12,
)


def make_chat(count: int = 20, body: str = "hello there friend") -> Chat:
    start = datetime(2026, 10, 11, 9, 0)
    messages = [
        Message(
            timestamp=start + timedelta(minutes=index),
            sender=f"person{index % 3}",
            text=f"{body} #{index}",
        )
        for index in range(count)
    ]
    return Chat(
        messages=messages,
        format=FORMAT,
        participants=["person0", "person1", "person2"],
        source="test.txt",
    )


def ids(messages: list[Message]) -> list[int]:
    return [id(message) for message in messages]


class ChunkChatTests(unittest.TestCase):
    def test_small_chat_is_one_chunk(self) -> None:
        chat = make_chat(3)
        chunks = chunk_chat(chat, 8000, 1200)
        self.assertEqual(len(chunks), 1)
        self.assertEqual(ids(chunks[0].messages), ids(chat.messages))

    def test_empty_chat_has_no_chunks(self) -> None:
        chat = Chat(messages=[], format=FORMAT, participants=[], source="empty.txt")
        self.assertEqual(chunk_chat(chat, 1000, 100), [])

    def test_every_message_is_present_and_in_order(self) -> None:
        chat = make_chat(40)
        chunks = chunk_chat(chat, 300, 60)
        self.assertGreater(len(chunks), 1)
        seen: list[int] = []
        for chunk in chunks:
            self.assertTrue(chunk.messages)
            timestamps = [message.timestamp for message in chunk.messages]
            self.assertEqual(timestamps, sorted(timestamps))
            seen.extend(ids(chunk.messages))
        self.assertEqual(seen[0], id(chat.messages[0]))
        self.assertEqual(seen[-1], id(chat.messages[-1]))
        for message in chat.messages:
            self.assertIn(id(message), seen)

    def test_messages_are_never_split(self) -> None:
        chat = make_chat(25, body="line one\nline two")
        chunks = chunk_chat(chat, 250, 50)
        originals = {id(message) for message in chat.messages}
        for chunk in chunks:
            for message in chunk.messages:
                self.assertIn(id(message), originals)
                self.assertIn("\n", message.text)

    def test_overlap_repeats_tail_of_previous_chunk(self) -> None:
        chat = make_chat(40)
        chunks = chunk_chat(chat, 300, 80)
        for previous, current in zip(chunks, chunks[1:]):
            previous_ids = ids(previous.messages)
            current_ids = ids(current.messages)
            shared = [value for value in current_ids if value in set(previous_ids)]
            self.assertTrue(shared, "adjacent chunks should share overlap messages")
            self.assertEqual(shared, current_ids[: len(shared)])
            self.assertEqual(shared, previous_ids[len(previous_ids) - len(shared):])

    def test_zero_overlap_has_no_shared_messages(self) -> None:
        chat = make_chat(40)
        chunks = chunk_chat(chat, 300, 0)
        for previous, current in zip(chunks, chunks[1:]):
            self.assertFalse(set(ids(previous.messages)) & set(ids(current.messages)))

    def test_overlap_is_clamped_to_a_quarter_of_the_chunk(self) -> None:
        chat = make_chat(40)
        chunks = chunk_chat(chat, 400, 10_000)
        for previous, current in zip(chunks, chunks[1:]):
            shared = set(ids(previous.messages)) & set(ids(current.messages))
            shared_chars = sum(
                len(render_message_line(message, FORMAT.clock)) + 1
                for message in current.messages
                if id(message) in shared
            )
            self.assertLessEqual(shared_chars, 100)

    def test_chunks_respect_the_character_budget(self) -> None:
        chat = make_chat(40)
        max_chars = 400
        chunks = chunk_chat(chat, max_chars, 80)
        longest = max(
            len(render_message_line(message, FORMAT.clock)) + 1 for message in chat.messages
        )
        for chunk in chunks:
            self.assertLessEqual(len(to_transcript(chunk)), max_chars + longest)

    def test_oversized_message_is_kept_whole(self) -> None:
        chat = make_chat(5)
        chat.messages[2] = Message(
            timestamp=chat.messages[2].timestamp,
            sender="person0",
            text="x" * 2000,
        )
        chunks = chunk_chat(chat, 200, 0)
        found = [chunk for chunk in chunks if id(chat.messages[2]) in ids(chunk.messages)]
        self.assertEqual(len(found), 1)
        self.assertTrue(any(message.text == "x" * 2000 for message in found[0].messages))

    def test_overlap_larger_than_budget_still_makes_progress(self) -> None:
        chat = make_chat(10)
        chunks = chunk_chat(chat, 1000, 900)
        self.assertEqual(len(chunks), 1)

    def test_system_messages_are_excluded_by_default(self) -> None:
        chat = make_chat(5)
        system = Message(
            timestamp=chat.messages[-1].timestamp + timedelta(minutes=1),
            sender=None,
            text="Messages are end-to-end encrypted.",
            kind=MessageKind.SYSTEM,
        )
        chat.messages.append(system)
        chunks = chunk_chat(chat, 8000, 1200)
        self.assertNotIn(id(system), ids(chunks[0].messages))
        with_system = chunk_chat(chat, 8000, 1200, include_system=True)
        self.assertIn(id(system), ids(with_system[0].messages))

    def test_invalid_budget_raises(self) -> None:
        with self.assertRaises(ValueError):
            chunk_chat(make_chat(3), 0)


if __name__ == "__main__":
    unittest.main()
