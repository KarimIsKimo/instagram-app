"""Offline regression checks; no Gemini, Instagram or WhatsApp calls are sent."""
import asyncio
import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

ROOT = Path(__file__).resolve().parents[1]
fake_google = types.ModuleType("google")
fake_google.genai = types.SimpleNamespace(Client=Mock())
spec = importlib.util.spec_from_file_location("jothen_under_test", ROOT / "main.py")
bot = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = bot
with patch.dict(sys.modules, {"google": fake_google}):
    spec.loader.exec_module(bot)


def reset():
    bot.sender_states.clear()
    bot.phone_alert_states.clear()
    bot.processed_mids.clear()
    bot.user_chats.clear()
    bot.user_profiles.clear()
    bot._last_cleanup = float("-inf")


class PhoneParsingTests(unittest.TestCase):
    def test_egyptian_phone_formats(self):
        cases = [
            "01012345678",
            "رقمي ٠١٠١٢٣٤٥٦٧٨",
            "۰۱۰۱۲۳۴۵۶۷۸",
            "010 1234 5678",
            "010-1234-5678",
            "+20 10 1234 5678",
            "0020 10 1234 5678",
            "201012345678",
            "+20 (0)10 1234 5678",
            "رقمي \u200f٠١٠١٢٣٤٥٦٧٨\u200e",
        ]
        for text in cases:
            with self.subTest(text=text):
                self.assertEqual(bot.extract_phone_numbers(text), ["+201012345678"])

    def test_other_mobile_prefixes(self):
        for prefix in ("011", "012", "015"):
            with self.subTest(prefix=prefix):
                self.assertEqual(
                    bot.extract_phone_numbers(prefix + "12345678"),
                    ["+20" + prefix[1:] + "12345678"],
                )

    def test_international_number(self):
        self.assertEqual(bot.extract_phone_numbers("+966 50 123 4567"), ["+966501234567"])

    def test_two_numbers_and_following_price(self):
        self.assertEqual(
            bot.extract_phone_numbers("01012345678 01112345678"),
            ["+201012345678", "+201112345678"],
        )
        self.assertEqual(
            bot.extract_phone_numbers("+20 10 1234 5678 السعر 2500"),
            ["+201012345678"],
        )
        self.assertEqual(
            bot.extract_phone_numbers("+20 10 1234 5678 2500"),
            ["+201012345678"],
        )

    def test_duplicate_formats_normalized_once(self):
        self.assertEqual(
            bot.extract_phone_numbers("01012345678 أو +201012345678"),
            ["+201012345678"],
        )

    def test_dates_prices_and_invalid_mobiles_are_not_contacts(self):
        for text in (
            "2500", "21/11/2026", "21-11-2026", "8 pm", "1234567890",
            "01312345678", "0101234567", "010123456789", "abc01012345678xyz",
        ):
            with self.subTest(text=text):
                self.assertEqual(bot.extract_phone_numbers(text), [])


class AdmissionTests(unittest.TestCase):
    def setUp(self):
        reset()

    def test_duplicate_webhook_is_dropped(self):
        self.assertTrue(bot.accept_incoming_message("a", "hello", "same"))
        self.assertFalse(bot.accept_incoming_message("a", "different text", "same"))

    def test_duplicate_text_does_not_need_a_message_id(self):
        self.assertTrue(bot.accept_incoming_message("a", "Hello  WORLD", None))
        self.assertFalse(bot.accept_incoming_message("a", " hello world ", None))
        self.assertTrue(bot.accept_incoming_message("b", "hello world", None))

    def test_flood_limit_and_recovery(self):
        with patch.object(bot.time, "monotonic", return_value=100):
            for index in range(bot.SPAM_MAX_MESSAGES):
                self.assertTrue(bot.accept_incoming_message("a", f"text {index}", str(index)))
            self.assertFalse(bot.accept_incoming_message("a", "extra", "extra"))
        with patch.object(bot.time, "monotonic", return_value=161):
            self.assertTrue(bot.accept_incoming_message("a", "extra", "extra"))

    def test_normal_three_message_funnel_is_accepted(self):
        for index, text in enumerate(["عايز احجز", "روكسي", "01012345678"]):
            self.assertTrue(bot.accept_incoming_message("a", text, str(index)))

    def test_empty_and_oversized_messages_rejected(self):
        self.assertFalse(bot.accept_incoming_message("a", " ", "empty"))
        self.assertFalse(bot.accept_incoming_message("a", "x" * 4001, "large"))

    def test_message_id_cache_is_bounded(self):
        with patch.object(bot, "MAX_STATE_ENTRIES", 3):
            for index in range(5):
                self.assertTrue(bot.accept_incoming_message("a", f"text {index}", str(index)))
            self.assertEqual(len(bot.processed_mids), 3)


class AlertTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        reset()
        self.alert = AsyncMock(return_value=True)
        self.stack = [
            patch.object(bot, "send_whatsapp_alert", self.alert),
            patch.object(bot, "get_ig_user_name", AsyncMock(return_value="")),
            patch.object(bot, "send_text_reply", AsyncMock()),
            patch.object(bot, "send_image_direct_upload", AsyncMock()),
            patch.object(bot, "get_ai_reply", Mock(return_value="Which branch suits you?")),
        ]
        for mock in self.stack:
            mock.start()
        self.addCleanup(lambda: [mock.stop() for mock in reversed(self.stack)])

    async def test_phone_only_notifies_without_notify_tag(self):
        await bot.process_and_reply("patient", "01012345678")
        self.alert.assert_awaited_once()
        self.assertIn("+201012345678", self.alert.call_args.args[0])
        self.assertEqual(self.alert.call_args.args[0], "رقم الهاتف: +201012345678")

    async def test_alert_contains_only_name_phone_and_available_details(self):
        bot.user_profiles["patient"] = "Karim Mohsen"
        await bot.process_and_reply("patient", "Ok I will book my number is 01012345678")
        self.assertEqual(
            self.alert.call_args.args[0],
            "الاسم: Karim Mohsen | رقم الهاتف: +201012345678",
        )
        bot.get_ai_reply.return_value = "[NOTIFY: Karim/01012345678, Roxy, tomorrow 5 pm]"
        await bot.process_and_reply("patient", "Roxy tomorrow 5 pm")
        self.assertEqual(
            self.alert.call_args.args[0],
            "الاسم: Karim Mohsen | رقم الهاتف: +201012345678 | الفرع: Roxy | الموعد المفضل: tomorrow 5 pm",
        )

    async def test_arabic_number_notifies(self):
        await bot.process_and_reply("patient", "رقمي ٠١٠١٢٣٤٥٦٧٨")
        self.assertIn("+201012345678", self.alert.call_args.args[0])

    async def test_no_number_no_alert(self):
        await bot.process_and_reply("patient", "I want an appointment")
        self.alert.assert_not_awaited()

    async def test_ai_invented_number_does_not_trigger_alert(self):
        bot.get_ai_reply.return_value = "[NOTIFY: 01012345678, Roxy, tomorrow]"
        await bot.process_and_reply("patient", "I want an appointment")
        self.alert.assert_not_awaited()

    async def test_number_sent_again_does_not_repeat_alert(self):
        await bot.process_and_reply("patient", "01012345678")
        await bot.process_and_reply("patient", "+20 10 1234 5678")
        self.alert.assert_awaited_once()

    async def test_same_phone_from_different_accounts_does_not_repeat(self):
        await bot.process_and_reply("patient-a", "01012345678")
        await bot.process_and_reply("patient-b", "٠١٠١٢٣٤٥٦٧٨")
        self.alert.assert_awaited_once()

    async def test_complete_details_in_first_message_get_one_alert(self):
        bot.get_ai_reply.return_value = "Thanks. [NOTIFY: 01012345678, Roxy, tomorrow 5 pm]"
        await bot.process_and_reply("patient", "01012345678 Roxy tomorrow 5 pm")
        self.alert.assert_awaited_once()
        self.assertIn("Roxy", self.alert.call_args.args[0])
        bot.send_text_reply.assert_awaited_once_with("patient", "Thanks.")

    async def test_one_completed_details_update_without_repeating_phone(self):
        await bot.process_and_reply("patient", "01012345678")
        bot.get_ai_reply.return_value = "[NOTIFY: 01012345678, Roxy, tomorrow 5 pm]"
        await bot.process_and_reply("patient", "Roxy tomorrow 5 pm")
        await bot.process_and_reply("patient", "Thank you")
        self.assertEqual(self.alert.await_count, 2)

    async def test_ai_failure_does_not_lose_number(self):
        bot.get_ai_reply.side_effect = RuntimeError("offline")
        await bot.process_and_reply("patient", "01012345678")
        self.alert.assert_awaited_once()

    async def test_notification_failure_retries_after_cooldown(self):
        self.alert.side_effect = [False, True]
        with patch.object(bot.time, "monotonic", return_value=100):
            await bot.process_and_reply("patient", "01012345678")
            await bot.process_and_reply("patient", "01012345678")
            self.assertEqual(self.alert.await_count, 1)
            self.assertFalse(bot.phone_alert_states["+201012345678"].initial_sent)
        with patch.object(bot.time, "monotonic", return_value=161):
            await bot.process_and_reply("patient", "01012345678")
        self.assertEqual(self.alert.await_count, 2)
        self.assertTrue(bot.phone_alert_states["+201012345678"].initial_sent)

    async def test_failed_completion_does_not_retry_in_a_tight_loop(self):
        self.alert.side_effect = [True, False, True]
        with patch.object(bot.time, "monotonic", return_value=100):
            await bot.process_and_reply("patient", "01012345678")
            bot.get_ai_reply.return_value = "[NOTIFY: 01012345678, Roxy, tomorrow 5 pm]"
            await bot.process_and_reply("patient", "Roxy tomorrow 5 pm")
            await bot.process_and_reply("patient", "Roxy tomorrow 5 pm")
            self.assertEqual(self.alert.await_count, 2)
        with patch.object(bot.time, "monotonic", return_value=161):
            await bot.process_and_reply("patient", "Roxy tomorrow 5 pm")
        self.assertEqual(self.alert.await_count, 3)

    async def test_failed_initial_alert_retries_on_later_message_without_number(self):
        self.alert.side_effect = [False, True]
        with patch.object(bot.time, "monotonic", return_value=100):
            await bot.process_and_reply("patient", "01012345678")
        with patch.object(bot.time, "monotonic", return_value=161):
            await bot.process_and_reply("patient", "Roxy")
        self.assertEqual(self.alert.await_count, 2)
        self.assertIn("+201012345678", self.alert.call_args.args[0])

    async def test_sender_cannot_submit_a_list_of_contacts(self):
        await bot.process_and_reply("patient", "01012345678 01112345678 01212345678")
        self.alert.assert_awaited_once()

    async def test_new_phone_alert_limit(self):
        for phone in ("01012345678", "01112345678", "01212345678", "01512345678"):
            await bot.process_and_reply("patient", phone)
        self.assertEqual(self.alert.await_count, 3)

    async def test_phone_can_notify_again_after_24_hours(self):
        with patch.object(bot.time, "monotonic", return_value=100):
            await bot.process_and_reply("patient", "01012345678")
        with patch.object(bot.time, "monotonic", return_value=86501):
            await bot.process_and_reply("patient", "01012345678")
        self.assertEqual(self.alert.await_count, 2)

    async def test_concurrent_messages_same_phone_notify_once(self):
        await asyncio.gather(
            bot.process_and_reply("a", "01012345678"),
            bot.process_and_reply("b", "+201012345678"),
        )
        self.alert.assert_awaited_once()


class TransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_whatsapp_rejection_is_not_marked_successful(self):
        client = AsyncMock()
        client.post.return_value = types.SimpleNamespace(status_code=400)
        manager = AsyncMock()
        manager.__aenter__.return_value = client
        with (
            patch.object(bot, "WA_ACCESS_TOKEN", "test"),
            patch.object(bot, "WA_PHONE_NUMBER_ID", "test"),
            patch.object(bot, "STAFF_PHONE_NUMBER", "test"),
            patch.object(bot.httpx, "AsyncClient", return_value=manager),
        ):
            self.assertFalse(await bot.send_whatsapp_alert("test"))

    async def test_whatsapp_timeout_is_not_marked_successful(self):
        client = AsyncMock()
        client.post.side_effect = bot.httpx.ReadTimeout("test")
        manager = AsyncMock()
        manager.__aenter__.return_value = client
        with (
            patch.object(bot, "WA_ACCESS_TOKEN", "test"),
            patch.object(bot, "WA_PHONE_NUMBER_ID", "test"),
            patch.object(bot, "STAFF_PHONE_NUMBER", "test"),
            patch.object(bot.httpx, "AsyncClient", return_value=manager),
        ):
            self.assertFalse(await bot.send_whatsapp_alert("test"))

    async def test_missing_settings_fail_without_network(self):
        with patch.object(bot, "STAFF_PHONE_NUMBER", ""):
            self.assertFalse(await bot.send_whatsapp_alert("test"))


class WebhookTests(unittest.TestCase):
    def test_health_identifies_the_clean_alert_build(self):
        from fastapi.testclient import TestClient
        with TestClient(bot.app) as client:
            self.assertEqual(
                client.get("/health").json(),
                {"status": "ok", "version": "phone-alerts-clean-v3"},
            )

    def test_duplicate_delivery_calls_processor_once(self):
        from fastapi.testclient import TestClient
        reset()
        payload = {
            "object": "instagram",
            "entry": [{"messaging": [{
                "sender": {"id": "patient"},
                "message": {"mid": "unique", "text": "01012345678"},
            }]}],
        }
        with patch.object(bot, "process_and_reply", AsyncMock()) as processor:
            with TestClient(bot.app) as client:
                self.assertEqual(client.post("/webhook", json=payload).status_code, 200)
                self.assertEqual(client.post("/webhook", json=payload).status_code, 200)
            processor.assert_awaited_once_with("patient", "01012345678")


if __name__ == "__main__":
    unittest.main(verbosity=2)
