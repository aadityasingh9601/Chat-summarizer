from __future__ import annotations

import io
import unittest
from contextlib import redirect_stderr
from datetime import datetime, timedelta
from unittest.mock import patch

from whatsapp_parser.models import Chat, ChatFamily, ChatFormat, Clock, DateOrder, Message
from whatsapp_parser.summarize import (
    MERGE_SYSTEM,
    NOTHING_IMPORTANT,
    call_ollama,
    summarize,
)

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


class FakeChat:
    def __init__(self, reply=None) -> None:
        self.calls: list[list[dict[str, str]]] = []
        self.tokens: list[str] = []
        self.reply = reply or (lambda index, messages: f"Summary {index}")

    def __call__(self, messages, on_token):
        self.calls.append(messages)
        text = self.reply(len(self.calls), messages)
        if on_token is not None:
            on_token(text)
        return text

    def is_merge(self, index: int) -> bool:
        return self.calls[index][0]["content"] == MERGE_SYSTEM


class SummarizeTests(unittest.TestCase):
    def test_single_chunk_does_not_merge(self) -> None:
        chat_fn = FakeChat()
        result = summarize(chat=make_chat(3), chat_fn=chat_fn, max_chars=8000)
        self.assertEqual(result, "Summary 1")
        self.assertEqual(len(chat_fn.calls), 1)
        self.assertNotEqual(chat_fn.calls[0][0]["content"], MERGE_SYSTEM)

    def test_long_chat_maps_then_merges(self) -> None:
        chat_fn = FakeChat()
        progress: list[str] = []
        result = summarize(
            chat=make_chat(60),
            chat_fn=chat_fn,
            max_chars=400,
            overlap_chars=0,
            on_progress=progress.append,
            on_token=chat_fn.tokens.append,
        )
        map_calls = [call for call in chat_fn.calls if call[0]["content"] != MERGE_SYSTEM]
        merge_calls = [call for call in chat_fn.calls if call[0]["content"] == MERGE_SYSTEM]
        self.assertGreater(len(map_calls), 1)
        self.assertEqual(len(merge_calls), 1)
        self.assertEqual(result, f"Summary {len(chat_fn.calls)}")
        self.assertTrue(any("summarizing chunk" in line for line in progress))
        self.assertTrue(any("merging" in line for line in progress))
        self.assertEqual(chat_fn.tokens, [result])

    def test_nothing_important_sections_are_dropped(self) -> None:
        def reply(index: int, messages) -> str:
            return NOTHING_IMPORTANT if index == 1 else "Rent is due Friday."

        chat_fn = FakeChat(reply)
        summarize(chat=make_chat(40), chat_fn=chat_fn, max_chars=300, overlap_chars=0)
        map_calls = [call for call in chat_fn.calls if call[0]["content"] != MERGE_SYSTEM]
        merge_call = next(call for call in chat_fn.calls if call[0]["content"] == MERGE_SYSTEM)
        self.assertGreater(len(map_calls), 1)
        self.assertEqual(merge_call[1]["content"].count("--- Summary"), len(map_calls) - 1)
        self.assertIn("Rent is due Friday.", merge_call[1]["content"])

    def test_all_chunks_nothing_important_skips_merge(self) -> None:
        chat_fn = FakeChat(lambda index, messages: NOTHING_IMPORTANT)
        result = summarize(chat=make_chat(40), chat_fn=chat_fn, max_chars=300, overlap_chars=0)
        self.assertEqual(result, "Nothing important.")
        self.assertFalse(any(call[0]["content"] == MERGE_SYSTEM for call in chat_fn.calls))

    def test_hierarchical_reduce_merges_in_several_levels(self) -> None:
        chat_fn = FakeChat(lambda index, messages: f"Summary {index}")
        progress: list[str] = []
        summarize(
            chat=make_chat(400),
            chat_fn=chat_fn,
            max_chars=200,
            overlap_chars=0,
            on_progress=progress.append,
        )
        merge_calls = [call for call in chat_fn.calls if call[0]["content"] == MERGE_SYSTEM]
        self.assertGreater(len(merge_calls), 1)
        self.assertTrue(any("level 2" in line for line in progress))

    def test_no_chunk_mode_makes_one_call(self) -> None:
        chat_fn = FakeChat()
        result = summarize(
            chat=make_chat(60),
            chat_fn=chat_fn,
            max_chars=100,
            overlap_chars=0,
            chunk=False,
        )
        self.assertEqual(len(chat_fn.calls), 1)
        self.assertIn("truncated", chat_fn.calls[0][1]["content"])
        self.assertEqual(result, "Summary 1")

    def test_failure_propagates(self) -> None:
        def boom(index: int, messages) -> str:
            raise RuntimeError("ollama down")

        with self.assertRaises(RuntimeError):
            summarize(chat=make_chat(3), chat_fn=FakeChat(boom))

    def test_custom_task_replaces_the_default(self) -> None:
        chat_fn = FakeChat()
        summarize(chat=make_chat(3), chat_fn=chat_fn, task="List only action items.")
        self.assertIn("List only action items.", chat_fn.calls[0][0]["content"])

    def test_empty_chat_returns_nothing_important(self) -> None:
        chat = Chat(messages=[], format=FORMAT, participants=[], source="empty.txt")
        chat_fn = FakeChat()
        self.assertEqual(summarize(chat=chat, chat_fn=chat_fn), "Nothing important.")
        self.assertEqual(chat_fn.calls, [])

    def test_call_ollama_hides_thinking_when_requested(self) -> None:
        response = {"message": {"content": "answer", "thinking": "private reasoning"}}
        errors = io.StringIO()
        with patch("ollama.chat", return_value=response) as chat, redirect_stderr(errors):
            reply = call_ollama("test-model", show_thinking=False)([], None)
        self.assertEqual(reply, "answer")
        self.assertEqual(errors.getvalue(), "")
        self.assertEqual(chat.call_args.kwargs["options"], {"num_ctx": 8192})


if __name__ == "__main__":
    unittest.main()
