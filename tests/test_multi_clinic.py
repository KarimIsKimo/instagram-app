"""Offline checks for the shared router and separate clinic behavior."""
import asyncio
import hashlib
import hmac
import importlib
import json
import os
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
fake_google = types.ModuleType("google")
fake_google.genai = types.SimpleNamespace(Client=Mock())
with patch.dict(sys.modules, {"google": fake_google}), patch.dict(
    os.environ,
    {
        "GEMINI_API_KEY": "offline-test",
        "PAGE_ACCESS_TOKEN": "jothen-token",
        "NEODERMA_IG_ACCESS_TOKEN": "neoderma-token",
        "VERIFY_TOKEN": "verify-test",
        "STAFF_PHONE_NUMBER": "test-staff",
    },
):
    server = importlib.import_module("server")
jothen = server.jothen
neo = server.neoderma
from fastapi.testclient import TestClient


def reset(bot):
    bot.sender_states.clear()
    bot.phone_alert_states.clear()
    bot.processed_mids.clear()
    bot.user_chats.clear()
    bot.user_profiles.clear()
    bot._last_cleanup = float("-inf")


def event(account_id, text="hello", sender="same-customer", mid="same-mid", **message_overrides):
    message = {"text": text, "mid": mid, **message_overrides}
    return {
        "id": account_id,
        "messaging": [{
            "sender": {"id": sender},
            "recipient": {"id": account_id},
            "message": message,
        }],
    }


def payload(*entries):
    return {"object": "instagram", "entry": list(entries)}


class RoutingTests(unittest.TestCase):
    def setUp(self):
        reset(jothen)
        reset(neo)
        self.j_patch = patch.object(jothen, "process_and_reply", AsyncMock())
        self.n_patch = patch.object(neo, "process_and_reply", AsyncMock())
        self.secret_patch = patch.object(server, "META_APP_SECRET", "")
        self.j_process = self.j_patch.start()
        self.n_process = self.n_patch.start()
        self.secret_patch.start()
        self.client = TestClient(server.app)
        self.client.__enter__()
        self.addCleanup(lambda: self.client.__exit__(None, None, None))
        self.addCleanup(self.j_patch.stop)
        self.addCleanup(self.n_patch.stop)
        self.addCleanup(self.secret_patch.stop)

    def post(self, *entries):
        return self.client.post("/webhook", json=payload(*entries))

    def test_jothen_only(self):
        self.assertEqual(self.post(event(server.JOTHEN_ACCOUNT_ID)).status_code, 200)
        self.j_process.assert_awaited_once_with("same-customer", "hello")
        self.n_process.assert_not_awaited()

    def test_neoderma_only(self):
        self.assertEqual(self.post(event(server.NEODERMA_ACCOUNT_ID)).status_code, 200)
        self.n_process.assert_awaited_once_with("same-customer", "hello")
        self.j_process.assert_not_awaited()

    def test_mixed_batch_same_sender_and_mid_keeps_clinics_separate(self):
        self.post(event(server.JOTHEN_ACCOUNT_ID), event(server.NEODERMA_ACCOUNT_ID))
        self.j_process.assert_awaited_once()
        self.n_process.assert_awaited_once()

    def test_unknown_account_never_defaults_to_jothen(self):
        self.post(event("unknown"))
        self.j_process.assert_not_awaited()
        self.n_process.assert_not_awaited()

    def test_recipient_mismatch_rejected(self):
        entry = event(server.JOTHEN_ACCOUNT_ID)
        entry["messaging"][0]["recipient"]["id"] = server.NEODERMA_ACCOUNT_ID
        self.post(entry)
        self.j_process.assert_not_awaited()
        self.n_process.assert_not_awaited()

    def test_duplicates_dropped_independently(self):
        entry = event(server.NEODERMA_ACCOUNT_ID)
        self.post(entry)
        self.post(entry)
        self.n_process.assert_awaited_once()

    def test_echoes_ignored(self):
        self.post(event(server.JOTHEN_ACCOUNT_ID, is_echo=True))
        self.j_process.assert_not_awaited()

    def test_missing_neo_token_cannot_use_jothen_token(self):
        with patch.object(neo, "PAGE_ACCESS_TOKEN", ""):
            self.post(event(server.NEODERMA_ACCOUNT_ID))
        self.n_process.assert_not_awaited()
        self.j_process.assert_not_awaited()

    def test_rate_limit_stays_with_its_clinic(self):
        for i in range(neo.SPAM_MAX_MESSAGES + 2):
            self.post(event(server.NEODERMA_ACCOUNT_ID, f"message {i}", mid=str(i)))
        self.assertEqual(self.n_process.await_count, neo.SPAM_MAX_MESSAGES)
        self.post(event(server.JOTHEN_ACCOUNT_ID))
        self.j_process.assert_awaited_once()

    def test_verify_challenge_and_wrong_token(self):
        response = self.client.get("/webhook", params={
            "hub.mode": "subscribe", "hub.verify_token": "verify-test", "hub.challenge": "123",
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.text, "123")
        response = self.client.get("/webhook", params={
            "hub.mode": "subscribe", "hub.verify_token": "wrong", "hub.challenge": "123",
        })
        self.assertEqual(response.status_code, 403)

    def test_empty_verify_token_is_not_accepted(self):
        with patch.object(server, "VERIFY_TOKEN", ""):
            response = self.client.get("/webhook", params={
                "hub.mode": "subscribe", "hub.verify_token": "", "hub.challenge": "123",
            })
        self.assertEqual(response.status_code, 403)

    def test_health_contains_version_and_no_credentials(self):
        result = self.client.get("/health")
        self.assertEqual(result.json()["version"], "two-clinics-v1")
        self.assertTrue(result.json()["clinics"]["neoderma"]["instagram_configured"])
        self.assertNotIn("neoderma-token", result.text)
        self.assertNotIn("jothen-token", result.text)

    def test_invalid_json_and_shape(self):
        self.assertEqual(self.client.post("/webhook", content=b"bad").status_code, 400)
        self.assertEqual(self.client.post("/webhook", json=[]).status_code, 400)

    def test_signature_required_when_secret_configured(self):
        body = json.dumps(payload(event(server.NEODERMA_ACCOUNT_ID))).encode()
        with patch.object(server, "META_APP_SECRET", "test-secret"):
            self.assertEqual(self.client.post("/webhook", content=body).status_code, 403)
            sig = "sha256=" + hmac.new(b"test-secret", body, hashlib.sha256).hexdigest()
            self.assertEqual(
                self.client.post("/webhook", content=body, headers={"X-Hub-Signature-256": sig}).status_code,
                200,
            )
        self.n_process.assert_awaited_once()


class IndependentBotTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        reset(jothen)
        reset(neo)
        self.patches = []
        self.alerts = {}
        for bot in (jothen, neo):
            alert = AsyncMock(return_value=True)
            self.alerts[bot] = alert
            patches = [
                patch.object(bot, "send_whatsapp_alert", alert),
                patch.object(bot, "get_ig_user_name", AsyncMock(return_value="")),
                patch.object(bot, "send_text_reply", AsyncMock()),
                patch.object(bot, "send_image_direct_upload", AsyncMock()),
                patch.object(bot, "get_ai_reply", Mock(return_value="Thanks")),
            ]
            for p in patches:
                p.start()
                self.patches.append(p)
        self.addCleanup(lambda: [p.stop() for p in reversed(self.patches)])

    async def test_same_number_alerts_each_clinic_once(self):
        await jothen.process_and_reply("same-customer", "01012345678")
        await neo.process_and_reply("same-customer", "01012345678")
        await neo.process_and_reply("same-customer", "+201012345678")
        self.alerts[jothen].assert_awaited_once()
        self.alerts[neo].assert_awaited_once()
        self.assertIn("Neoderma", self.alerts[neo].call_args.args[0])
        self.assertNotIn("Instagram ID", self.alerts[neo].call_args.args[0])

    async def test_neoderma_complete_request_has_service_not_branch(self):
        neo.get_ai_reply.return_value = "[NOTIFY: name/01012345678, Botox, Monday 5pm]"
        await neo.process_and_reply("a", "01012345678 Botox Monday 5pm")
        text = self.alerts[neo].call_args.args[0]
        self.assertIn("الخدمة: Botox", text)
        self.assertNotIn("الفرع:", text)

    async def test_no_gender_blind_fallback_to_women_price_poster(self):
        await neo.process_and_reply("a", "What are your men's pulse offers?")
        neo.send_image_direct_upload.assert_not_awaited()

    async def test_women_aliases_send_the_same_poster_once(self):
        neo.get_ai_reply.return_value = "350 EGP [IMAGE: women_areas] [IMAGE: women_packages]"
        await neo.process_and_reply("a", "Women's underarm?")
        neo.send_image_direct_upload.assert_awaited_once_with("a", "women_areas")

    async def test_only_allowlisted_price_images(self):
        neo.get_ai_reply.return_value = "Hello [IMAGE: ../../private] [IMAGE: branches]"
        await neo.process_and_reply("a", "location?")
        neo.send_image_direct_upload.assert_not_awaited()

    async def test_failed_ai_still_passes_number_to_reception(self):
        neo.get_ai_reply.side_effect = RuntimeError("offline")
        await neo.process_and_reply("a", "٠١٠١٢٣٤٥٦٧٨")
        self.alerts[neo].assert_awaited_once()
        self.assertIn("+201012345678", self.alerts[neo].call_args.args[0])

    async def test_clinic_caches_are_distinct_objects(self):
        for name in ("user_chats", "user_profiles", "processed_mids", "sender_states", "phone_alert_states"):
            self.assertIsNot(getattr(jothen, name), getattr(neo, name))


class ClinicContentTests(unittest.TestCase):
    def test_neoderma_has_no_jothen_contacts_or_motus(self):
        prompt = neo.SYSTEM_INSTRUCTION
        for old_contact in ("01142286600", "01001298786", "01156391111"):
            self.assertNotIn(old_contact, prompt)
        self.assertIn("Deka Again", prompt)
        self.assertNotIn("our machines are Candela Gentlelase Pro u and Deka Motus AY", prompt)
        self.assertIn("Men CAN buy", prompt)
        self.assertNotIn("Men DO NOT buy pulses", prompt)
        self.assertIn("12:00 PM to 9:00 PM", prompt)

    def test_supplied_price_tables(self):
        prompt = neo.SYSTEM_INSTRUCTION
        for row in (
            "Full body: 3750", "Full body excluding abdomen and back: 3000",
            "Half body: 2500", "Mustache: 250", "Full leg: 2000", "Upper leg: 1500",
            "Half leg: 1250", "Full arm: 1000", "Face: 350", "Face and chin: 500",
            "Underarm: 350", "Bikini: 400", "Bikini + bikini line + underarm: 600",
            "Abdomen: 1500", "Back: 1500", "4000 pulses: 3500", "2000 pulses: 1800",
            "1500 pulses: 1500", "1000 pulses: 1250", "700 pulses: 1000",
            "Beard: 400", "Underarm: 400", "Ear: 250", "Front of neck: 400",
            "Back of neck: 400", "Boxer: 600", "Boxer + line: 750",
            "Boxer + line + underarm: 1000", "Beard + boxer + line + underarm: 1250",
            "1000 pulses: 1500", "3000 pulses: 3000", "6000 pulses: 5000",
            "Men's facial session: 1000", "Groom package: 5000",
        ):
            with self.subTest(row=row):
                self.assertIn(row, prompt)

    def test_configured_images_exist(self):
        for filename in set(neo.IMAGE_FILES.values()):
            image = neo.IMAGE_DIRECTORY / filename
            self.assertTrue(image.exists())
            self.assertGreater(image.stat().st_size, 10000)


if __name__ == "__main__":
    unittest.main()
