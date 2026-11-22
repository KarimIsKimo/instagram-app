import os
import re
import json
import httpx
import time
import asyncio
import hashlib
import unicodedata
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from pathlib import Path
from fastapi import FastAPI, Request, Response, BackgroundTasks, HTTPException
from fastapi.staticfiles import StaticFiles
from google import genai

app = FastAPI()
APP_VERSION = "neoderma-v1"
print(f"Neoderma bot version: {APP_VERSION}", flush=True)

os.makedirs("images", exist_ok=True)
app.mount("/images", StaticFiles(directory="images"), name="images")

VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "")
PAGE_ACCESS_TOKEN = os.getenv("NEODERMA_IG_ACCESS_TOKEN", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

WA_PHONE_NUMBER_ID = os.getenv("WA_PHONE_NUMBER_ID", "")
WA_ACCESS_TOKEN = os.getenv("WA_ACCESS_TOKEN", "")
STAFF_PHONE_NUMBER = os.getenv("NEODERMA_STAFF_PHONE_NUMBER", "").strip() or os.getenv("STAFF_PHONE_NUMBER", "").strip()

IMAGE_DIRECTORY = Path(__file__).resolve().parent / "images" / "neoderma"
IMAGE_FILES = {"women_packages": "women_offers.jpg", "women_areas": "women_offers.jpg", "men_offers": "men_offers.jpg"}
MODEL_NAME = os.getenv("NEODERMA_GEMINI_MODEL", os.getenv("GEMINI_MODEL", "gemini-3.6-flash"))

client = genai.Client(api_key=GEMINI_API_KEY)

user_chats = {}
processed_mids = OrderedDict()
user_profiles = {}  # Cache to store user names so we only fetch them once

SYSTEM_INSTRUCTION = """
You are the friendly, professional Instagram receptionist for "نيوديرما كلينك" (NeoDerma Clinic).
This is a receptionist funnel. Staff make and confirm all appointments.

CLINIC FACTS
- One clinic: Trivium Business Complex, New Cairo / التجمع الخامس، تريفيوم بيزنس كومبلكس.
- Directions: https://maps.app.goo.gl/p3RpS8k2KuvgFWXH7
- Saturday through Thursday, 12:00 PM to 9:00 PM. Closed Friday. Timezone Africa/Cairo.
- Reception WhatsApp: 01028165555. Other reception phone: 01028085555.
- Website: https://www.neodermaeg.com/
- Nurses are present. Do not claim there are no nurses or that every service is performed by a doctor.
  If asked who specifically performs a procedure, or the gender/availability of a staff member, ask reception.
- Do not invent room/floor/unit numbers, doctor names, doctor schedules or confirmed time slots.
- Do not use Jothen's clinic name, branch list, contacts, prices, HR number or promotions.
- A public reception number is NOT automatically the destination for internal WhatsApp alerts.

DEVICES
- Hair removal: Candela GentleLase Pro U (Alexandrite) and Deka Again. NEVER call the Deka machine "Motus" or "Motus AY".
- Lutronic Spectra: pigment work, tattoo removal and carbon laser. Do NOT present it as a routine hair-removal machine.
- Lutronic eCO2 and Deka DOT: fractional CO2 / resurfacing.
- CANTA Hydra Beauty / Hydra Facial: facial cleansing and hydration.
- Clatuu: cryolipolysis / body contouring. RF platforms: tightening. InBody: body composition.
- Give brief, general descriptions only. Device and treatment suitability are assessed by the clinical team.
- Never invent laser parameters, guaranteed results, number of sessions or named staff availability.
- Do not promise a machine is available for a particular appointment until reception confirms.

SERVICES WE HANDLE
Laser hair removal, cryolipolysis / cryo sculpting and body contouring, fractional laser, Hydra facials,
tattoo removal, carbon peel / carbon laser, Botox, fillers, chemical peels, dermatology enquiries,
RF tightening and InBody / nutrition enquiries.
Answer basic service and device questions here. Do NOT send non-laser enquiries to Jothen.
For diagnosis, prescriptions, device settings, procedure suitability or unlisted prices, direct the
customer to NeoDerma reception at 01028165555 or offer to pass their phone number to staff.
Do not diagnose images or prescribe. For an urgent medical complaint, advise urgent clinical assessment.
For employment/CV requests, use NeoDerma reception; do not invent a dedicated HR number.
Do not promise self-service treatment.

EXACT CLINIC-SUPPLIED PRICES — EGP, ONE SESSION UNLESS STATED
The following are the prices supplied by the clinic owner. The supplied artwork says "summer",
but no live season/campaign dates were confirmed. Quote the prices without inventing seasonal
urgency, an expiry date, appointment scarcity, a discount percentage or a Black Friday offer.
Do not apply Jothen Black Friday settings to NeoDerma.
Never calculate or invent prices for unlisted areas, combined areas, extra pulses or other services.

WOMEN — AREAS [IMAGE: women_areas]
- Full body: 3750.
- Full body excluding abdomen and back: 3000.
- Half body: 2500.
- Mustache: 250.
- Full leg: 2000.
- Upper leg: 1500.
- Half leg: 1250.
- Full arm: 1000.
- Face: 350.
- Face and chin: 500.
- Underarm: 350.
- Bikini: 400.
- Bikini + bikini line + underarm: 600.
- Abdomen: 1500.
- Back: 1500.
Do not equate "bikini" with "bikini plus line" unless the combo explicitly includes the line.

WOMEN — PULSES [IMAGE: women_packages]
- 4000 pulses: 3500.
- 2000 pulses: 1800.
- 1500 pulses: 1500.
- 1000 pulses: 1250.
- 700 pulses: 1000.
The poster labels these offers "one session". Do not invent multi-visit validity or pulse carryover.

MEN — AREAS [IMAGE: men_offers]
- Beard: 400. Do not rename this to a separately priced beard-outline/neck bundle.
- Underarm: 400.
- Ear: 250.
- Front of neck: 400.
- Back of neck: 400.
- Boxer: 600.
- Nasoor / pilonidal-sinus hair-removal area: 750. This is not a promise to treat the underlying disease.

MEN — COMBOS [IMAGE: men_offers]
- Boxer + line: 750, includes free protector.
- Boxer + line + underarm: 1000, includes free protector.
- Beard + boxer + line + underarm: 1250, includes free protector.
Only the three listed combos explicitly include the free protector. Do not invent other accessory prices.

MEN — PULSES [IMAGE: men_offers]
Men CAN buy the listed pulse packages at NeoDerma:
- 1000 pulses: 1500.
- 3000 pulses: 3000.
- 6000 pulses: 5000.
Never quote women's pulse prices to men.

MEN — OTHER OFFERS [IMAGE: men_offers]
- Men's facial session: 1000. Do not automatically call this Hydra Facial; reception confirms the exact facial.
- Groom package: 5000, labelled "2 months":
  3 sessions boxer + line + underarm;
  2 skin-cleansing sessions;
  2 carbon-laser sessions;
  free consultation with a consultant;
  20% off any additional service.
Do not extend the free protector to the groom package unless reception confirms.

PRICE AND IMAGE BEHAVIOR
- Quote ONLY what the customer asked about; never dump a price list.
- Women area question: append [IMAGE: women_areas].
- Women pulse question: append [IMAGE: women_packages].
- Men's listed offers/prices, pulses or groom package: append [IMAGE: men_offers].
- Both women tags use the same supplied women's poster; one copy per reply is sufficient.
- If a gender-specific price is ambiguous, ask whether they want men's or women's prices.
  Do not infer gender from an Instagram name alone.
- No price images for unrelated services, location questions, generic greetings or medical complaints.
- No branch or machine images are configured. Provide the address/map link or a short device description instead.
- If a customer asks to see the laser menu without specifying men/women, ask which one first.
- Do not send both price posters as a default.

LANGUAGE AND FLOW
- Reply in the language of the customer's latest message: natural Egyptian Arabic or clear English.
- Use respectful, neutral Arabic with حضرتك when gender is unclear.
- Do not repeat greetings or introduce yourself each message.
- Keep replies brief and helpful; answer the question before suggesting a reception handoff.
- Do not pressure customers for data after they say they will think, check or return later.

CONTACT REQUESTS / STAFF HANDOFF
- A real phone number in any customer message is enough for the application to notify staff.
  Service and preferred date/time are helpful but OPTIONAL for passing the number along.
- There is one location: do NOT ask the customer to select a branch.
- Ask only for missing information, in their language. Name is optional.
- Once the customer has supplied a phone number, requested service and preferred day/time,
  append exactly [NOTIFY: Name/Phone, Service, Preferred day and time].
  Use exactly three comma-separated fields; use semicolons inside a field if necessary.
  Do not include a greeting, Instagram ID, explanation or internal instructions inside this tag.
- The application may send one completed-details update after a phone-only alert.
- Never invent a phone number or copy NeoDerma reception's number into NOTIFY as the customer's phone.
- Never say a booking is confirmed, that a slot is held, or promise reception will respond within a fixed time.
- Never claim a WhatsApp alert was sent successfully unless the application supplies success status.
"""


# All protection state is temporary and resets when this process restarts.
# Optional environment settings; the existing deployment works without new keys.
SPAM_WINDOW_SECONDS = 60
SPAM_MAX_MESSAGES = max(1, int(os.getenv("SPAM_MAX_MESSAGES", "12")))
SPAM_DUPLICATE_SECONDS = 30
MAX_MESSAGE_CHARS = 4000
PHONE_ALERT_COOLDOWN_SECONDS = max(
    60, int(os.getenv("PHONE_ALERT_COOLDOWN_SECONDS", "86400"))
)
PHONE_ALERT_RETRY_SECONDS = 60
NEW_PHONE_ALERT_WINDOW_SECONDS = 3600
MAX_NEW_PHONE_ALERTS_PER_SENDER = 3
MESSAGE_ID_TTL_SECONDS = 86400
SENDER_STATE_TTL_SECONDS = 3600
MAX_STATE_ENTRIES = 10000

_DIGIT_TRANSLATION = str.maketrans(
    "٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹",
    "01234567890123456789",
)
# An Egyptian mobile, or an explicitly international number. Separators and
# Arabic/Persian digits are accepted; dates and prices are not local numbers.
_PHONE_SEPARATOR = r"[ \t().-]*"
_EGYPT_PHONE = re.compile(
    rf"(?<![\w+])(?:0{_PHONE_SEPARATOR}1{_PHONE_SEPARATOR}[0125]"
    rf"(?:{_PHONE_SEPARATOR}[0-9]){{8}}|"
    rf"(?:(?:\+|00){_PHONE_SEPARATOR})?2{_PHONE_SEPARATOR}0{_PHONE_SEPARATOR}"
    rf"(?:0{_PHONE_SEPARATOR})?1{_PHONE_SEPARATOR}[0125]"
    rf"(?:{_PHONE_SEPARATOR}[0-9]){{8}})(?!\w)"
)
_INTERNATIONAL_PHONE = re.compile(
    r"(?<![\w+])(?:\+|00)[ \t]*[1-9](?:[0-9 \t().-]{6,35}[0-9])(?!\w)"
)


@dataclass
class SenderState:
    last_seen: float
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    message_times: deque = field(default_factory=deque)
    fingerprints: dict = field(default_factory=dict)
    new_phone_alert_times: deque = field(default_factory=deque)
    phone: str = ""


@dataclass
class PhoneAlertState:
    last_seen: float
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    initial_sent: bool = False
    complete_sent: bool = False
    last_attempt: float = float("-inf")
    last_attempt_succeeded: bool = False
    last_sent: float = 0.0


sender_states = OrderedDict()
phone_alert_states = OrderedDict()
_last_cleanup = float("-inf")


def extract_phone_numbers(text: str) -> list[str]:
    text = unicodedata.normalize("NFKC", text).translate(_DIGIT_TRANSLATION)
    # Ignore invisible direction marks often copied with Arabic phone numbers.
    text = re.sub(r"[\u200e\u200f\u061c]", "", text)
    matches = list(_EGYPT_PHONE.finditer(text))
    # Find Egyptian contacts first, so a following price or second contact does
    # not get swallowed by the more general international-number pattern.
    remainder = list(text)
    for match in matches:
        remainder[match.start():match.end()] = ["x"] * (match.end() - match.start())
    matches.extend(_INTERNATIONAL_PHONE.finditer("".join(remainder)))
    matches.sort(key=lambda match: match.start())
    found = []
    for match in matches:
        candidate = match.group(0).strip()
        digits = re.sub(r"\D", "", candidate)
        if candidate.startswith("00"):
            digits = digits[2:]
        if re.fullmatch(r"01[0125][0-9]{8}", digits):
            normalized = "+20" + digits[1:]
        elif re.fullmatch(r"20(?:0)?1[0125][0-9]{8}", digits):
            # Also accept the common +20 (0)10... notation.
            normalized = "+20" + digits[2:].removeprefix("0")
        elif candidate.startswith(("+", "00")) and re.fullmatch(
            r"[1-9][0-9]{7,14}", digits
        ):
            normalized = "+" + digits
        else:
            continue
        if normalized not in found:
            found.append(normalized)
    return found


def _evict_oldest_idle(cache, *, sender_cache=False):
    for key, state in cache.items():
        if not state.lock.locked():
            del cache[key]
            if sender_cache:
                user_chats.pop(key, None)
                user_profiles.pop(key, None)
            return True
    return False


def _prune_protection_state(now: float):
    global _last_cleanup
    if now - _last_cleanup < 60:
        return
    _last_cleanup = now
    while processed_mids:
        mid, seen_at = next(iter(processed_mids.items()))
        if now - seen_at < MESSAGE_ID_TTL_SECONDS:
            break
        processed_mids.pop(mid, None)
    for sender_id, state in list(sender_states.items()):
        if not state.lock.locked() and now - state.last_seen >= SENDER_STATE_TTL_SECONDS:
            sender_states.pop(sender_id, None)
            user_chats.pop(sender_id, None)
            user_profiles.pop(sender_id, None)
    for phone, state in list(phone_alert_states.items()):
        reference = state.last_sent if state.initial_sent else state.last_seen
        if not state.lock.locked() and now - reference >= PHONE_ALERT_COOLDOWN_SECONDS:
            phone_alert_states.pop(phone, None)


def get_sender_state(sender_id: str) -> SenderState:
    now = time.monotonic()
    _prune_protection_state(now)
    state = sender_states.get(sender_id)
    if state is None:
        if len(sender_states) >= MAX_STATE_ENTRIES:
            if not _evict_oldest_idle(sender_states, sender_cache=True):
                raise RuntimeError("Too many active conversations")
        state = SenderState(last_seen=now)
        sender_states[sender_id] = state
    state.last_seen = now
    sender_states.move_to_end(sender_id)
    return state


def accept_incoming_message(sender_id: str, text: str, mid: str | None) -> bool:
    """Silently drop webhook duplicates, repeated text, oversized input and floods."""
    now = time.monotonic()
    _prune_protection_state(now)
    if not text.strip() or len(text) > MAX_MESSAGE_CHARS:
        return False
    if mid:
        seen_at = processed_mids.get(mid)
        if seen_at is not None and now - seen_at < MESSAGE_ID_TTL_SECONDS:
            return False
    try:
        state = get_sender_state(sender_id)
    except RuntimeError:
        return False
    while state.message_times and now - state.message_times[0] >= SPAM_WINDOW_SECONDS:
        state.message_times.popleft()
    if len(state.message_times) >= SPAM_MAX_MESSAGES:
        return False
    canonical_text = " ".join(
        unicodedata.normalize("NFKC", text).translate(_DIGIT_TRANSLATION).casefold().split()
    )
    fingerprint = hashlib.sha256(canonical_text.encode("utf-8")).hexdigest()
    state.fingerprints = {
        key: timestamp
        for key, timestamp in state.fingerprints.items()
        if now - timestamp < SPAM_DUPLICATE_SECONDS
    }
    if fingerprint in state.fingerprints:
        return False
    state.message_times.append(now)
    state.fingerprints[fingerprint] = now
    if mid:
        processed_mids[mid] = now
        processed_mids.move_to_end(mid)
        while len(processed_mids) > MAX_STATE_ENTRIES:
            processed_mids.popitem(last=False)
    return True


def _get_phone_alert_state(phone: str) -> PhoneAlertState:
    now = time.monotonic()
    state = phone_alert_states.get(phone)
    if state and not state.lock.locked():
        reference = state.last_sent if state.initial_sent else state.last_seen
        if now - reference >= PHONE_ALERT_COOLDOWN_SECONDS:
            phone_alert_states.pop(phone, None)
            state = None
    if state is None:
        if len(phone_alert_states) >= MAX_STATE_ENTRIES:
            if not _evict_oldest_idle(phone_alert_states):
                raise RuntimeError("Too many active phone alerts")
        state = PhoneAlertState(last_seen=now)
        phone_alert_states[phone] = state
    state.last_seen = now
    phone_alert_states.move_to_end(phone)
    return state


def _single_line(value: str, limit: int = 500) -> str:
    return " ".join(value.split())[:limit]


async def notify_staff_once(
    sender_id: str,
    sender_state: SenderState,
    message_text: str,
    patient_details: str | None = None,
) -> bool:
    """Notify for a real customer-supplied number, with one optional details update."""
    phone = sender_state.phone
    if not phone:
        return False  # Never trust an AI-generated phone number on its own.
    try:
        alert = _get_phone_alert_state(phone)
    except RuntimeError:
        return False
    async with alert.lock:
        now = time.monotonic()
        complete = bool(patient_details)
        if alert.initial_sent and (not complete or alert.complete_sent):
            return True
        # A details update may follow a successful initial alert immediately.
        # Failed attempts always wait before retrying.
        if now - alert.last_attempt < PHONE_ALERT_RETRY_SECONDS:
            if not (alert.initial_sent and complete and alert.last_attempt_succeeded):
                return False
        while (
            sender_state.new_phone_alert_times
            and now - sender_state.new_phone_alert_times[0] >= NEW_PHONE_ALERT_WINDOW_SECONDS
        ):
            sender_state.new_phone_alert_times.popleft()
        if not alert.initial_sent:
            if len(sender_state.new_phone_alert_times) >= MAX_NEW_PHONE_ALERTS_PER_SENDER:
                return False
            # Count attempts too, so failures cannot be abused to flood the API.
            sender_state.new_phone_alert_times.append(now)
        alert.last_attempt = now
        alert.last_attempt_succeeded = False
        # The approved WhatsApp template already supplies the heading.
        details = ["العيادة: Neoderma"]
        name = user_profiles.get(sender_id)
        if name:
            details.append(f"الاسم: {_single_line(name, 100)}")
        details.append(f"رقم الهاتف: {phone}")
        if patient_details:
            # NOTIFY contains Name/Phone, Service, Date and Time, in that order.
            # Name and phone above are authoritative; only add the other fields.
            parts = re.split(r"\s*[,،]\s*", patient_details, maxsplit=2)
            if len(parts) == 3:
                if parts[1].strip():
                    details.append(f"الخدمة: {_single_line(parts[1], 120)}")
                if parts[2].strip():
                    details.append(f"الموعد المفضل: {_single_line(parts[2], 160)}")
        sent = await send_whatsapp_alert(" | ".join(details))
        alert.last_attempt_succeeded = sent
        if sent:
            alert.initial_sent = True
            alert.complete_sent = complete
            alert.last_sent = now
        return sent



def get_clinic_context() -> str:
    return "[CLINIC: NEODERMA] Use only NeoDerma facts and the clinic-supplied prices. No live seasonal campaign is configured."

@app.get("/health")
async def health_check():
    return {"status": "ok", "version": APP_VERSION}

@app.get("/webhook")
async def verify_webhook(request: Request):
    mode = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge")
    if mode == "subscribe" and token == VERIFY_TOKEN:
        return Response(content=challenge, media_type="text/plain")
    raise HTTPException(status_code=403, detail="Verification failed")

@app.post("/webhook")
async def handle_instagram_messages(request: Request, backgroundTasks: BackgroundTasks):
    body = await request.json()
    if body.get("object") == "instagram":
        for entry in body.get("entry", []):
            for messaging_event in entry.get("messaging", []):
                message_data = messaging_event.get("message")
                if message_data and not message_data.get("is_echo"):
                    sender_id = messaging_event.get("sender", {}).get("id")
                    message_text = message_data.get("text")
                    mid = message_data.get("mid")
                    if (
                        isinstance(sender_id, str)
                        and isinstance(message_text, str)
                        and accept_incoming_message(sender_id, message_text, mid)
                    ):
                        backgroundTasks.add_task(process_and_reply, sender_id, message_text)
    return {"status": "success"}

async def get_ig_user_name(sender_id: str) -> str:
    """Fetches the user's public Instagram name and caches it."""
    if sender_id in user_profiles:
        return user_profiles[sender_id]
        
    url = f"https://graph.instagram.com/{sender_id}"
    params = {"fields": "name", "access_token": PAGE_ACCESS_TOKEN.strip()}
    
    try:
        async with httpx.AsyncClient(timeout=10.0) as http_client:
            response = await http_client.get(url, params=params)
            if response.status_code == 200:
                name = response.json().get("name", "")
                if name:
                    user_profiles[sender_id] = name
                    return name
    except Exception as e:
        print(f"⚠️ Could not fetch name for {sender_id}: {e}")
        
    return ""

async def process_and_reply(sender_id: str, message_text: str):
    # Serialize a customer's messages so concurrent Gemini calls cannot mix history.
    state = get_sender_state(sender_id)
    async with state.lock:
        try:
            await _process_and_reply(sender_id, message_text, state)
        finally:
            state.last_seen = time.monotonic()


async def _process_and_reply(sender_id: str, message_text: str, state: SenderState):
    numbers = extract_phone_numbers(message_text)
    if numbers:
        # One contact per message prevents a pasted list from flooding staff.
        state.phone = numbers[0]
    
    # Fetch the user's name and append it to the message context
    user_name = await get_ig_user_name(sender_id)
    name_context = f"[Context: User's name is {user_name}]\n" if user_name else ""

    enriched_text = f"""{name_context}{message_text}

{get_clinic_context()}

[REMINDER]: Use the customer's latest language. Keep answers short. Quote only confirmed NeoDerma prices.
A phone number alone is sufficient for a staff alert. Do not require a branch. Men's pulse packages ARE available.
Do not promise booking confirmation or staff response times. Append a price image tag only for the relevant laser price menu.
For unlisted service prices or clinical assessment, use NeoDerma reception: 01028165555.
A complete-details tag has exactly three comma-separated fields: [NOTIFY: Name/Phone, Service, Preferred day and time]."""

    try:
        # The SDK call and its retry sleep run in a worker, not the server event loop.
        reply_text = await asyncio.to_thread(get_ai_reply, sender_id, enriched_text)
    except Exception as exc:
        print(f"⚠️ Reply generation failed: {type(exc).__name__}")
        reply_text = "تعذر الرد الآلي حالياً. برجاء التواصل مع استقبال الفرع." if re.search(r"[\u0600-\u06ff]", message_text) else "Automatic replies are temporarily unavailable. Please contact branch reception."
    reply_text = reply_text or ""

    notify_match = re.search(r'\[NOTIFY:(.*?)\]', reply_text, flags=re.DOTALL)
    patient_details = notify_match.group(1).strip() if notify_match else None
    if state.phone:
        # Extraction from the CUSTOMER'S text is sufficient; NOTIFY is optional.
        # A complete request gets a single complete alert, not two back-to-back.
        # Later messages can retry a failed send after the retry delay.
        await notify_staff_once(sender_id, state, message_text, patient_details)


    raw_image_tags = re.findall(r'\[IMAGE:(.*?)\]', reply_text)
    image_tags = [tag.strip() for tag in raw_image_tags]

    # Deliberately no gender-blind keyword fallback to women's offers.
    image_tags = [tag for tag in image_tags if tag in IMAGE_FILES]

    clean_text = re.sub(r'\[IMAGE:.*?\]', '', reply_text)
    clean_text = re.sub(r'\[NOTIFY:.*?\]', '', clean_text, flags=re.DOTALL).strip()

    if clean_text:
        await send_text_reply(sender_id, clean_text)

    # Both women tags share one poster: de-duplicate by the physical file.
    unique_tags = []
    seen_files = set()
    for tag in image_tags:
        filename = IMAGE_FILES[tag]
        if filename not in seen_files:
            seen_files.add(filename)
            unique_tags.append(tag)
    for img in unique_tags:
        await send_image_direct_upload(sender_id, img)


def get_ai_reply(sender_id: str, user_text: str) -> str:
    if sender_id not in user_chats:
        user_chats[sender_id] = client.chats.create(
            model=MODEL_NAME,
            config={"system_instruction": SYSTEM_INSTRUCTION}
        )

    chat = user_chats[sender_id]

    for attempt in range(2):
        try:
            response = chat.send_message(user_text)
            return response.text or ""
        except Exception as e:
            print(f"⚠️ Chat Error (attempt {attempt + 1}): {e}")
            if "503" in str(e) or "429" in str(e):
                time.sleep(2)
            else:
                user_chats[sender_id] = client.chats.create(
                    model=MODEL_NAME,
                    config={"system_instruction": SYSTEM_INSTRUCTION}
                )
                chat = user_chats[sender_id]

    if re.search(r"[\u0600-\u06ff]", user_text):
        return "تعذر الرد الآلي حالياً. برجاء التواصل مع استقبال الفرع."
    return "Automatic replies are temporarily unavailable. Please contact branch reception."

async def send_text_reply(recipient_id: str, text: str):
    url = "https://graph.instagram.com/v21.0/me/messages"
    headers = {"Authorization": f"Bearer {PAGE_ACCESS_TOKEN.strip()}", "Content-Type": "application/json"}
    payload = {"recipient": {"id": recipient_id}, "message": {"text": text}}
    async with httpx.AsyncClient() as http_client:
        response = await http_client.post(url, headers=headers, json=payload)
        if response.status_code != 200:
            print(f"❌ Instagram Text Error: {response.text}")

async def send_image_direct_upload(recipient_id: str, image_name: str):
    url = "https://graph.instagram.com/v21.0/me/messages"
    params = {"access_token": PAGE_ACCESS_TOKEN.strip()}
    filename = IMAGE_FILES.get(image_name)
    if not filename:
        return
    file_path = IMAGE_DIRECTORY / filename

    if not os.path.exists(file_path):
        print(f"❌ File not found on disk: {file_path}")
        return

    async with httpx.AsyncClient(timeout=30.0) as http_client:
        with open(file_path, "rb") as f:
            files = {"filedata": (f"{image_name}.jpg", f, "image/jpeg")}
            data = {
                "recipient": json.dumps({"id": recipient_id}),
                "message": json.dumps({"attachment": {"type": "image", "payload": {}}}),
            }
            response = await http_client.post(url, params=params, data=data, files=files)

        if response.status_code != 200:
            print(f"❌ Instagram Upload Error [{response.status_code}]: {response.text}")
        else:
            print(f"✅ Image {image_name}.jpg delivered successfully.")

async def send_whatsapp_alert(patient_details: str) -> bool:
    if not WA_PHONE_NUMBER_ID or not WA_ACCESS_TOKEN or not STAFF_PHONE_NUMBER:
        print("⚠️ Staff alert unavailable: check WhatsApp environment settings.")
        return False
    url = f"https://graph.facebook.com/v21.0/{WA_PHONE_NUMBER_ID}/messages"
    headers = {"Authorization": f"Bearer {WA_ACCESS_TOKEN.strip()}", "Content-Type": "application/json"}
    payload = {
        "messaging_product": "whatsapp",
        "to": STAFF_PHONE_NUMBER,
        "type": "template",
        "template": {
            "name": "new_booking_alert",
            "language": {"code": "ar_EG"},
            "components": [
                {
                    "type": "body",
                    "parameters": [{"type": "text", "text": patient_details}],
                }
            ],
        },
    }
    try:
        async with httpx.AsyncClient(timeout=15.0) as http_client:
            response = await http_client.post(url, headers=headers, json=payload)
    except httpx.HTTPError as exc:
        print(f"❌ WhatsApp request failed: {type(exc).__name__}")
        return False
    if not 200 <= response.status_code < 300:
        print(f"❌ WhatsApp alert rejected (HTTP {response.status_code}).")
        return False
    print("✅ WhatsApp staff alert accepted.")
    return True