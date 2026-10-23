import os
import unittest
import zipfile
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from whatsapp_parser import (
    ChatFamily,
    Clock,
    DateOrder,
    MediaKind,
    MessageKind,
    dropped_messages,
    parse_file,
    parse_text,
)

FIXTURES = Path(__file__).parent / "fixtures"


class WindowsDesktopTests(unittest.TestCase):
    def setUp(self) -> None:
        self.chat = parse_file(FIXTURES / "windows_desktop_us.txt")

    def test_format_detection(self) -> None:
        self.assertIs(self.chat.format.family, ChatFamily.BRACKET)
        self.assertIs(self.chat.format.clock, Clock.H12)
        self.assertIs(self.chat.format.date_order, DateOrder.MONTH_FIRST)
        self.assertFalse(self.chat.date_order_ambiguous)

    def test_participants(self) -> None:
        self.assertEqual(
            self.chat.participants,
            ["Alice", "Bob", "Dr. Strange", "+1 202-555-0147"],
        )

    def test_message_count_and_range(self) -> None:
        self.assertEqual(len(self.chat.messages), 11)
        self.assertEqual(self.chat.start, datetime(2026, 10, 20, 15, 36, 0))
        self.assertEqual(self.chat.end, datetime(2026, 10, 21, 9, 2, 12))

    def test_timestamp_parsing(self) -> None:
        self.assertEqual(self.chat.messages[1].timestamp, datetime(2026, 10, 20, 15, 37, 39))
        self.assertEqual(self.chat.messages[2].timestamp, datetime(2026, 10, 20, 15, 38, 2))

    def test_multiline_message_is_stitched(self) -> None:
        message = self.chat.messages[3]
        self.assertEqual(message.sender, "Alice")
        self.assertEqual(message.text, "Cool.\nI'll bring dessert.")

    def test_media_placeholder(self) -> None:
        message = self.chat.messages[4]
        self.assertIs(message.kind, MessageKind.MEDIA)
        self.assertIs(message.media_kind, MediaKind.FILE)
        self.assertEqual(message.text, "[file]")

    def test_deleted_message(self) -> None:
        message = self.chat.messages[6]
        self.assertIs(message.kind, MessageKind.DELETED)
        self.assertEqual(message.text, "[deleted]")

    def test_attachment_placeholder(self) -> None:
        message = self.chat.messages[7]
        self.assertIs(message.kind, MessageKind.MEDIA)
        self.assertIs(message.media_kind, MediaKind.IMAGE)
        self.assertEqual(message.text, "[image]")

    def test_sender_with_colon(self) -> None:
        message = self.chat.messages[8]
        self.assertEqual(message.sender, "Dr. Strange")
        self.assertEqual(message.text, "Time is a construct.")

    def test_phone_number_sender(self) -> None:
        message = self.chat.messages[9]
        self.assertEqual(message.sender, "+1 202-555-0147")
        self.assertEqual(message.text, "is this the place?")

    def test_system_message_has_no_sender(self) -> None:
        message = self.chat.messages[0]
        self.assertIs(message.kind, MessageKind.SYSTEM)
        self.assertIsNone(message.sender)
        self.assertIn("created group", message.text)

    def test_preamble_is_not_a_message(self) -> None:
        self.assertEqual(len(self.chat.preamble), 1)
        self.assertIn("end-to-end encrypted", self.chat.preamble[0])

    def test_twelve_oclock_boundaries(self) -> None:
        chat = parse_text(
            "[10/20/26, 12:00:00 AM] Alice: midnight\n"
            "[10/20/26, 12:30:00 PM] Alice: noon\n"
        )
        self.assertEqual(chat.messages[0].timestamp, datetime(2026, 10, 20, 0, 0, 0))
        self.assertEqual(chat.messages[1].timestamp, datetime(2026, 10, 20, 12, 30, 0))


class WindowsDesktopGroupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.chat = parse_file(FIXTURES / "windows_desktop_group.txt")

    def test_dash_prefixed_system_lines_have_no_sender(self) -> None:
        first = self.chat.messages[0]
        self.assertIs(first.kind, MessageKind.SYSTEM)
        self.assertIsNone(first.sender)
        self.assertEqual(first.text, "+91 90000 00001 created the group")

    def test_dash_prefix_is_stripped_from_every_system_line(self) -> None:
        for message in self.chat.messages:
            self.assertFalse(message.text.startswith("- "), message.text)

    def test_normal_messages_keep_their_sender(self) -> None:
        message = self.chat.messages[3]
        self.assertEqual(message.sender, "+91 90000 00002")
        self.assertEqual(message.text, "where do we have to give the interview?")

    def test_multiline_message_is_stitched(self) -> None:
        message = self.chat.messages[-1]
        self.assertIn("Dear Candidate,\nWe hope you are doing well.", message.text)

    def test_participants_exclude_system_lines(self) -> None:
        self.assertEqual(self.chat.participants, ["+91 90000 00002", "+91 90000 00001"])


class OtherPlatformTests(unittest.TestCase):
    def test_macos_day_first_24h(self) -> None:
        chat = parse_file(FIXTURES / "macos_desktop.txt")
        self.assertIs(chat.format.date_order, DateOrder.DAY_FIRST)
        self.assertIs(chat.format.clock, Clock.H24)
        self.assertFalse(chat.date_order_ambiguous)
        self.assertEqual(chat.messages[0].timestamp, datetime(2026, 7, 31, 9, 45, 17))
        self.assertEqual(chat.messages[4].timestamp, datetime(2026, 8, 1, 11, 2, 0))

    def test_ios_image_omitted(self) -> None:
        chat = parse_file(FIXTURES / "ios_ddmm.txt")
        message = chat.messages[3]
        self.assertIs(message.kind, MessageKind.MEDIA)
        self.assertIs(message.media_kind, MediaKind.IMAGE)
        self.assertEqual(message.text, "[image]")

    def test_android_dash_family(self) -> None:
        chat = parse_file(FIXTURES / "android_dash.txt")
        self.assertIs(chat.format.family, ChatFamily.DASH)
        self.assertIs(chat.format.date_order, DateOrder.DAY_FIRST)
        self.assertIs(chat.format.clock, Clock.H24)
        self.assertEqual(chat.messages[0].timestamp, datetime(2026, 10, 20, 3, 37, 0))
        self.assertEqual(chat.messages[2].text, "Cool.\nI'll bring dessert.")

    def test_year_first_variant(self) -> None:
        chat = parse_text("[2024/07/09, 08:01:49] Alice: hello\n")
        self.assertIs(chat.format.date_order, DateOrder.YEAR_FIRST)
        self.assertEqual(chat.messages[0].timestamp, datetime(2024, 7, 9, 8, 1, 49))


class AmbiguityTests(unittest.TestCase):
    TEXT = "[11/09/26, 3:37:39 PM] Alice: hello\n[12/10/26, 4:00:00 PM] Bob: hi\n"

    def test_defaults_to_month_first(self) -> None:
        chat = parse_text(self.TEXT)
        self.assertTrue(chat.date_order_ambiguous)
        self.assertIs(chat.format.date_order, DateOrder.MONTH_FIRST)
        self.assertEqual(chat.messages[0].timestamp, datetime(2026, 11, 9, 15, 37, 39))
        self.assertEqual(chat.messages[1].timestamp, datetime(2026, 12, 10, 16, 0, 0))

    def test_day_first_can_be_forced(self) -> None:
        chat = parse_text(self.TEXT, date_order=DateOrder.DAY_FIRST)
        self.assertFalse(chat.date_order_ambiguous)
        self.assertEqual(chat.messages[0].timestamp, datetime(2026, 9, 11, 15, 37, 39))
        self.assertEqual(chat.messages[1].timestamp, datetime(2026, 10, 12, 16, 0, 0))

    def test_unambiguous_file_is_not_flagged(self) -> None:
        chat = parse_text("[11/09/26, 3:37:39 PM] Alice: hello\n[25/09/26, 4:00:00 PM] Bob: hi\n")
        self.assertFalse(chat.date_order_ambiguous)
        self.assertIs(chat.format.date_order, DateOrder.DAY_FIRST)


class EncodingTests(unittest.TestCase):
    def test_bom_crlf_and_lrm_are_handled(self) -> None:
        raw = (
            "\ufeff\u200e[10/20/26, 3:37:39 PM] Alice: hi\r\n"
            "\u200e[10/20/26, 3:38:02 PM] Bob: yo\r\n"
        )
        chat = parse_text(raw)
        self.assertEqual(len(chat.messages), 2)
        self.assertEqual(chat.messages[0].sender, "Alice")
        self.assertEqual(chat.messages[1].text, "yo")

    def test_missing_file(self) -> None:
        with self.assertRaises(FileNotFoundError):
            parse_file(FIXTURES / "does_not_exist.txt")

    def test_garbage_file_raises(self) -> None:
        with self.assertRaises(ValueError):
            parse_text("hello world\nnothing to see here\n")

    def test_empty_file_raises(self) -> None:
        with self.assertRaises(ValueError):
            parse_text("")


class ZipTests(unittest.TestCase):
    def test_zip_picks_transcript_and_skips_resource_fork(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            transcript = root / "_chat.txt"
            transcript.write_text(
                "[10/20/26, 3:37:39 PM] Alice: real content\n", encoding="utf-8"
            )
            fork = root / "._chat.txt"
            fork.write_text("resource fork garbage\n" * 500, encoding="utf-8")
            archive = root / "chat.zip"
            with zipfile.ZipFile(archive, "w") as zf:
                zf.write(transcript, "_chat.txt")
                zf.write(fork, "._chat.txt")

            chat = parse_file(archive)
            self.assertEqual(len(chat.messages), 1)
            self.assertEqual(chat.messages[0].text, "real content")


class RenderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.chat = parse_file(FIXTURES / "windows_desktop_us.txt")

    def test_transcript_groups_by_day(self) -> None:
        transcript = self.chat.to_transcript()
        self.assertIn("=== 2026-10-20 ===", transcript)
        self.assertIn("=== 2026-10-21 ===", transcript)
        self.assertNotIn("created group", transcript)

    def test_transcript_can_include_system_events(self) -> None:
        transcript = self.chat.to_transcript(include_system=True)
        self.assertIn("created group", transcript)

    def test_ollama_turns_shape(self) -> None:
        messages = self.chat.to_ollama_messages(style="turns")
        self.assertEqual(messages[0]["role"], "system")
        self.assertIn("summarizer", messages[0]["content"].lower())
        self.assertTrue(all(m["role"] == "user" for m in messages[1:]))
        self.assertTrue(
            messages[1]["content"].startswith("Alice (10/20/26 03:37 PM): Hey! Are we still on")
        )

    def test_ollama_system_message_frames_the_chat_as_data(self) -> None:
        system = self.chat.to_ollama_messages(style="turns")[0]["content"]
        self.assertIn("untrusted data", system)
        self.assertIn("2–5", system)

    def test_ollama_task_is_added_to_system_message(self) -> None:
        system = self.chat.to_ollama_messages(style="turns", task="Find the jokes.")[0]["content"]
        self.assertIn("Find the jokes.", system)

    def test_ollama_explicit_system_prompt_wins(self) -> None:
        messages = self.chat.to_ollama_messages(
            style="turns", system_prompt="You are a pirate.", task="ignored"
        )
        self.assertEqual(messages[0]["content"], "You are a pirate.")

    def test_ollama_transcript_style(self) -> None:
        messages = self.chat.to_ollama_messages(style="transcript")
        self.assertEqual(len(messages), 2)
        self.assertIn("=== 2026-10-20 ===", messages[1]["content"])

    def test_ollama_max_chars_truncates(self) -> None:
        messages = self.chat.to_ollama_messages(style="turns", max_chars=80)
        self.assertIn("omitted to fit the context budget", messages[-1]["content"])

    def test_ollama_transcript_truncates_on_message_boundaries(self) -> None:
        messages = self.chat.to_ollama_messages(style="transcript", max_chars=120)
        body = messages[1]["content"]
        self.assertIn("[truncated:", body)
        self.assertNotIn("Dr. Strange", body)

    def test_dropped_messages_counts_omitted_messages(self) -> None:
        dropped = dropped_messages(self.chat, style="turns", max_chars=80)
        self.assertGreater(dropped, 0)
        self.assertEqual(dropped_messages(self.chat, style="turns", max_chars=None), 0)

    def test_to_dicts_and_json(self) -> None:
        rows = self.chat.to_dicts()
        self.assertEqual(len(rows), 11)
        self.assertEqual(rows[1]["sender"], "Alice")
        self.assertEqual(rows[1]["timestamp"], "2026-10-20 15:37:39")
        self.assertEqual(rows[1]["kind"], "text")
        self.assertIn('"sender": "Alice"', self.chat.to_json())


class MediaAndForwardedTests(unittest.TestCase):
    def test_media_caption_is_kept(self) -> None:
        chat = parse_text(
            "[10/11/26, 8:30:15 PM] Alice: <image omitted> Stickney Crater\n\nhttps://example.com/a"
        )
        message = chat.messages[0]
        self.assertIs(message.kind, MessageKind.MEDIA)
        self.assertIn("[image]", message.text)
        self.assertIn("Stickney Crater", message.text)
        self.assertIn("https://example.com/a", message.text)

    def test_forwarded_tag_is_normalized(self) -> None:
        chat = parse_text("[10/11/26, 1:47:33 PM] Alice: [Forwarded] Mercury is visible")
        self.assertEqual(chat.messages[0].text, "[forwarded] Mercury is visible")

    def test_forwarded_media_keeps_both_tags(self) -> None:
        chat = parse_text("[10/11/26, 8:44:02 PM] Alice: [Forwarded] <image omitted>")
        message = chat.messages[0]
        self.assertIs(message.kind, MessageKind.MEDIA)
        self.assertEqual(message.text, "[forwarded] [image]")

    def test_inline_attached_placeholder_is_replaced(self) -> None:
        chat = parse_text(
            "[27/07/2026, 09:43:05] Payer: Paid using PhonePe UPI.\n"
            "Explore the app now: https://phon.pe/download\n"
            "<attached: 00000003-PHOTO-2026-07-27.jpg>"
        )
        text = chat.messages[0].text
        self.assertNotIn("<attached:", text)
        self.assertIn("[image]", text)
        self.assertIn("PhonePe", text)

    def test_multiline_continuation_is_indented_in_transcript(self) -> None:
        chat = parse_text(
            "[10/11/26, 8:30:15 PM] Alice: first line\n\ncontinued here\n[10/11/26, 8:31:00 PM] Bob: ok"
        )
        transcript = chat.to_transcript()
        self.assertIn("Alice (08:30 PM): first line\n    \n    continued here", transcript)
        self.assertIn("Bob (08:31 PM): ok", transcript)


class DateFilterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.chat = parse_text(
            "\n".join(
                [
                    "[10/11/26, 9:00:00 AM] Alice: day one",
                    "[10/12/26, 9:00:00 AM] Bob: day two",
                    "[10/13/26, 9:00:00 AM] Alice: day three",
                ]
            )
        )

    def test_filter_from_date_keeps_from_that_day(self) -> None:
        filtered = self.chat.filter_from_date(datetime(2026, 10, 12).date())
        self.assertEqual([m.text for m in filtered.messages], ["day two", "day three"])

    def test_filter_since_is_strictly_after(self) -> None:
        filtered = self.chat.filter_since(datetime(2026, 10, 12, 9, 0, 0))
        self.assertEqual([m.text for m in filtered.messages], ["day three"])

    def test_filter_none_returns_same_chat(self) -> None:
        self.assertIs(self.chat.filter_since(None), self.chat)
        self.assertIs(self.chat.filter_from_date(None), self.chat)


class StateTests(unittest.TestCase):
    def test_round_trip(self) -> None:
        from whatsapp_parser.state import load_last_summary, save_last_summary

        with TemporaryDirectory() as tmp:
            old = os.environ.get("CHAT_SUMMARIZER_STATE")
            os.environ["CHAT_SUMMARIZER_STATE"] = str(Path(tmp) / "state.json")
            try:
                self.assertIsNone(load_last_summary("/chat/a.txt"))
                save_last_summary("/chat/a.txt", datetime(2026, 10, 11, 12, 0))
                self.assertEqual(load_last_summary("/chat/a.txt"), datetime(2026, 10, 11, 12, 0))
            finally:
                if old is None:
                    os.environ.pop("CHAT_SUMMARIZER_STATE", None)
                else:
                    os.environ["CHAT_SUMMARIZER_STATE"] = old


if __name__ == "__main__":
    unittest.main()
