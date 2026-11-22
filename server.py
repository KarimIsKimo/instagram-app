"""Run both independent clinic bots in one Render process.

Render start command:
uvicorn server:app --host 0.0.0.0 --port $PORT --workers 1
"""
import hashlib
import hmac
import json
import logging
import os
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request, Response
from fastapi.staticfiles import StaticFiles

import main as jothen
import neoderma

APP_VERSION = "two-clinics-v1"
LOGGER = logging.getLogger("clinic_router")
app = FastAPI(title="Jothen and Neoderma Instagram receptionist")
IMAGE_ROOT = Path(__file__).resolve().parent / "images"
IMAGE_ROOT.mkdir(exist_ok=True)
app.mount("/images", StaticFiles(directory=str(IMAGE_ROOT)), name="images")

JOTHEN_ACCOUNT_ID = os.getenv("JOTHEN_IG_ACCOUNT_ID", "17841403071455355").strip()
NEODERMA_ACCOUNT_ID = os.getenv("NEODERMA_IG_ACCOUNT_ID", "17841445572106989").strip()
if not JOTHEN_ACCOUNT_ID or not NEODERMA_ACCOUNT_ID or JOTHEN_ACCOUNT_ID == NEODERMA_ACCOUNT_ID:
    raise RuntimeError("Configure two different Instagram professional account IDs")

ACCOUNTS = {
    JOTHEN_ACCOUNT_ID: ("jothen", jothen),
    NEODERMA_ACCOUNT_ID: ("neoderma", neoderma),
}
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "").strip()
META_APP_SECRET = os.getenv("META_APP_SECRET", "").strip()
print(f"Clinic router version: {APP_VERSION}", flush=True)


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "version": APP_VERSION,
        "clinics": {
            name: {"instagram_configured": bool(bot.PAGE_ACCESS_TOKEN.strip())}
            for name, bot in ACCOUNTS.values()
        },
    }


@app.get("/webhook")
async def verify_webhook(request: Request):
    challenge = request.query_params.get("hub.challenge")
    if (
        VERIFY_TOKEN
        and request.query_params.get("hub.mode") == "subscribe"
        and request.query_params.get("hub.verify_token") == VERIFY_TOKEN
        and challenge is not None
    ):
        return Response(content=challenge, media_type="text/plain")
    raise HTTPException(status_code=403, detail="Verification failed")


def _validate_signature(raw_body: bytes, signature: str | None):
    # Optional for compatibility with the existing setup. When configured, every
    # POST must carry a valid signature for the shared Meta app.
    if not META_APP_SECRET:
        return
    expected = "sha256=" + hmac.new(
        META_APP_SECRET.encode("utf-8"), raw_body, hashlib.sha256
    ).hexdigest()
    if not signature or not hmac.compare_digest(signature, expected):
        raise HTTPException(status_code=403, detail="Invalid webhook signature")


async def _process_safely(bot, clinic_name: str, sender_id: str, text: str):
    try:
        await bot.process_and_reply(sender_id, text)
    except Exception as exc:
        # Don't include tokens, phone numbers or customer messages in the log.
        LOGGER.error("Clinic processing failed: clinic=%s error=%s", clinic_name, type(exc).__name__)


@app.post("/webhook")
async def receive_webhook(request: Request, background_tasks: BackgroundTasks):
    raw_body = await request.body()
    _validate_signature(raw_body, request.headers.get("X-Hub-Signature-256"))
    try:
        body = json.loads(raw_body)
    except (ValueError, UnicodeError):
        raise HTTPException(status_code=400, detail="Invalid JSON")
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Expected a JSON object")
    if body.get("object") != "instagram":
        return {"status": "success"}

    entries = body.get("entry", [])
    if not isinstance(entries, list):
        raise HTTPException(status_code=400, detail="Invalid entry list")
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        account_id = str(entry.get("id", ""))
        target = ACCOUNTS.get(account_id)
        if target is None:
            # Unknown IDs are NEVER routed to a default clinic.
            LOGGER.warning("Ignored webhook for an unconfigured Instagram account")
            continue
        clinic_name, bot = target
        if not bot.PAGE_ACCESS_TOKEN.strip():
            LOGGER.error("Clinic token is missing: clinic=%s", clinic_name)
            continue
        events = entry.get("messaging", [])
        if not isinstance(events, list):
            continue
        for event in events:
            if not isinstance(event, dict):
                continue
            message = event.get("message")
            sender = event.get("sender")
            recipient = event.get("recipient")
            if not isinstance(message, dict) or not isinstance(sender, dict):
                continue
            if message.get("is_echo"):
                continue
            # A recipient mismatch must never cross clinic boundaries.
            if recipient is not None:
                if not isinstance(recipient, dict) or str(recipient.get("id", "")) != account_id:
                    continue
            sender_id = sender.get("id")
            text = message.get("text")
            mid = message.get("mid")
            if not isinstance(sender_id, str) or not sender_id or not isinstance(text, str):
                continue
            if mid is not None and not isinstance(mid, str):
                continue
            if not bot.accept_incoming_message(sender_id, text, mid):
                continue
            background_tasks.add_task(_process_safely, bot, clinic_name, sender_id, text)
    return {"status": "success"}
