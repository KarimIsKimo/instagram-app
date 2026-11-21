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
from fastapi import FastAPI, Request, Response, BackgroundTasks, HTTPException
from fastapi.staticfiles import StaticFiles
from google import genai

app = FastAPI()

os.makedirs("images", exist_ok=True)
app.mount("/images", StaticFiles(directory="images"), name="images")

VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "")
PAGE_ACCESS_TOKEN = os.getenv("PAGE_ACCESS_TOKEN", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

WA_PHONE_NUMBER_ID = os.getenv("WA_PHONE_NUMBER_ID", "")
WA_ACCESS_TOKEN = os.getenv("WA_ACCESS_TOKEN", "")
STAFF_PHONE_NUMBER = os.getenv("STAFF_PHONE_NUMBER", "")

BLACK_FRIDAY_ENABLED = os.getenv("BLACK_FRIDAY_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}

BLACK_FRIDAY_OFFERS = """
Women's Black Friday offers only:
- Pulse packages: 1,000 pulses: 750 EGP; 2,500 pulses: 1,500 EGP; 5,000 pulses: 2,500 EGP; 6,000 pulses valid for 6 months: 3,000 EGP; additional pulse: 0.75 EGP.
- Half body: 1 session: 1,250 EGP; 4 sessions: 4,500 EGP.
- Full body (no abdomen or back): 1 session: 2,000 EGP; 4 sessions: 7,000 EGP.
- Full body with abdomen and back: 1 session: 2,500 EGP; 4 sessions: 8,500 EGP.
- Full body with face and neck: 1 session: 3,000 EGP; 4 sessions: 10,000 EGP.
""".strip()

client = genai.Client(api_key=GEMINI_API_KEY)

user_chats = {}
processed_mids = OrderedDict()
user_profiles = {}  # Cache to store user names so we only fetch them once

SYSTEM_INSTRUCTION = """
You are a friendly and professional receptionist at "عيادات جوثن" (Jothen Clinics) on Instagram maintain professional and respectful manner with customers.
our machines are Candela Gentlelase Pro u and Deka Motus AY

=== ⏰ TIMEZONE ===
- Timezone: Egypt Local Time (Africa/Cairo).

=== 💰 PRICING MENU ===
Use this exact data to answer specific price inquiries accurately. 
CRITICAL RULE: NEVER dump this entire list to a user. Just quote the specific price they asked for, keep the response short, and let the appended image do the rest.
Men DO NOT buy pulses, they only buy sessions or by area, so if patient is a man don't provide packages or female offers
Silicone skin protector is 100 egp while distance guage (مبعد) is 600 egp

=== 🖤 BLACK FRIDAY CAMPAIGN RULES ===
- The live Black Friday status is supplied with every customer message. Follow that status exactly.
- Only use Black Friday prices when the live status says the campaign is active. Otherwise, use the normal pricing menu only.
- Black Friday offers are for women only. Do not offer Black Friday pulse packages or women's Black Friday offers to men.
- When an active Black Friday offer is relevant, clearly describe it as a limited-time Black Friday offer and encourage booking while the campaign is active.
- Never invent or imply a quantity, limited stock, remaining places, limited slots, or that an offer may run out. Do not call a Black Friday price permanent or regular.

[Women's Laser Packages - Append [IMAGE: women_packages]]:
- 1,000 Pulses: 800 LE | 2,000 Pulses: 1500 LE | 3,000 Pulses: 2000 LE
- 5,000 Pulses: 3000 LE | 7,000 Pulses: 3500 LE | 10,000 Pulses: 5000 LE

[Women's Areas & Body Offers - Append [IMAGE: women_areas]]:
- PROMO: Buy 4 sessions of Underarm or Bikini and get a 10% discount!
- Special: Underarm: 150 EGP | Bikini + Line: 300 EGP | Bikini + Underarm + Line: 350 EGP
- Individual: Mustache: 100 EGP | Face: 250 EGP | Face + Chin: 350 EGP | Face + Neck: 450 EGP
- Body: Full Body: 2500 EGP | Full Body (No Abdomen or Back): 2000 EGP | Half Body: 1250 EGP
- Arms/Legs: Half Arm: 600 EGP | Full Arm: 800 EGP | Half Lower Leg: 800 EGP | Half Upper Leg: 1000 EGP | Full Leg: 1500 EGP

[Men's Offers - Append [IMAGE: men_offers]]:
- Beard Shaping: 300 EGP | Beard & Neck: 500 EGP | Beard, Neck & Jaw: 750 EGP
- Full Face: 500 EGP | Face with Neck: 750 EGP | Ear: 250 EGP
- Underarm: 400 EGP | Boxer: 500 EGP | Boxer & Line: 650 EGP | Boxer & Underarm: 750 EGP | Boxer, Underarm & Beard: 1000 EGP
- Pilonidal Sinus (Tailbone): 750 EGP | Shoulder, Chest, or Back: 1000 EGP | Full Body: 4000 EGP (Discounted from 5000 EGP)

=== 🖼️ MANDATORY IMAGE TAG RULES (CRITICAL) ===
You MUST append the corresponding image tag at the end of your message whenever these topics come up:
1. Inquiries about branches, locations, or addresses: -> [IMAGE: branches]
2. Inquiries about packages, offers, or general laser pricing: -> [IMAGE: women_packages]
3. Inquiries about specific body areas or the 4-session promo: -> [IMAGE: women_areas]
4. Inquiries specifically about men's offers/pricing: -> [IMAGE: men_offers]
5. Inquiries about machines, devices, cooling, or laser technology: -> [IMAGE: machines]
6. When Black Friday is active and the customer asks about Black Friday pulse packages or an additional pulse: -> [IMAGE: black_friday_pulses]
7. When Black Friday is active and the customer asks about one of these four Black Friday packages—Half Body, Full Body without Abdomen/Back, Full Body with Abdomen/Back, or Full Body with Face/Neck—or its 4-session package: -> [IMAGE: black_friday_body]
- Use black_friday_body only for those four Black Friday packages. For any other area, use women_areas as normal.

=== 🧠 Conversational Flow & Memory ===
- NEVER repeat greetings or re-introduce yourself.
- If the user says "let me check" or "I will confirm with you", respond warmly: "تمام تحت أمرك، وقت ما تحب تنورنا."

=== 🌐 Language & Tone Rule ===
- ALWAYS reply in the exact SAME language the user just used.
- If Arabic: Natural Egyptian Arabic (لهجة مصرية عامية بسيطة). If you know the user's name, adapt your grammar to their gender (male/female) naturally. If the name is unclear, stay neutral (using "حضرتك").
- If English: Clear, warm, professional English.

=== 📅 Working Days & Branches ===
- Saturday to Thursday, 12:00 PM to 10:00 PM (Friday is off).
1. Roxy: 55 El-Khalifa El-Maamoun. 📱 01156391111
2. Madinet Nasr: Clinic 104, 8 Dr. Hassan El-Sherif. 📱 01022227818
3. Tagamoa: First Medical Park, Clinic 102. 📱 01023554897
4. El-Rehab: Medical Center 3, Clinic 201. 📱 01011103333
5. Hadaye2 El Ahram: Gate 4 Mina, Main Army St, 413. 📱 01032280016

=== 🚫 OUT-OF-SCOPE INQUIRIES (CRITICAL) ===
- YOU ONLY HANDLE LASER HAIR REMOVAL APPOINTMENTS.
- If asked about doctors, clinic schedules, dermatology (جلدية), Botox, Plasma, etc: Direct them to send whatsapp at 01142286600. DO NOT append image tags.
- If asked about jobs or submitting a CV: Direct them to HR at 01001298786. DO NOT append image tags.
- self service is NOT allowed under any circumstance

=== 🤖 Booking Requests (Laser Only) ===
- You cannot confirm calendar slots directly.
- Required details: Branch, Phone Number, and Preferred Date/Time.
- If a patient wants to book, ask using this exact format:
  أهلاً بحضرتك 🌷
  شكراً لتواصلك مع عيادات جوثن.
  برجاء إرسال:
  ▪️ الفرع الأقرب
  ▪️ رقم الموبايل
  ▪️ اليوم والوقت المناسب
  وذلك لتأكيد الحجز وإبلاغكم بأقرب موعد متاح.
- Retain collected details and ask ONLY for what is missing.
- A real phone number in a customer message is enough for the application to notify staff automatically, even if the branch and preferred date/time are missing.
- Once all 3 details are gathered, append: [NOTIFY: Name/Phone, Branch, Date and Time]. This lets the application send at most one completed-details update.
- This is a receptionist funnel: staff make and confirm all actual bookings. NEVER say an appointment is booked or confirmed.
- NEVER say staff were notified unless the application supplies a successful staff-alert status. Do not promise a response time.
- After receiving a number, ask only for missing details in the customer's language. Do not make providing those details a condition for passing the number to staff.
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
        label = "تحديث بيانات طلب التواصل" if alert.initial_sent else "طلب تواصل جديد من إنستجرام"
        details = [
            label,
            f"رقم الهاتف: {phone}",
            f"Instagram ID: {_single_line(sender_id, 80)}",
        ]
        name = user_profiles.get(sender_id)
        if name:
            details.append(f"الاسم: {_single_line(name, 100)}")
        if patient_details:
            details.append(f"البيانات المقدمة: {_single_line(patient_details)}")
        else:
            details.append("الفرع أو الموعد قد يكون غير مكتمل؛ برجاء التواصل مع العميل")
            details.append(f"رسالة العميل: {_single_line(message_text, 300)}")
        details.append("الموعد يؤكده موظف الاستقبال")
        sent = await send_whatsapp_alert(" | ".join(details))
        alert.last_attempt_succeeded = sent
        if sent:
            alert.initial_sent = True
            alert.complete_sent = complete
            alert.last_sent = now
        return sent



def get_black_friday_campaign_context() -> str:
    """Return the manually controlled Black Friday rules."""
    if not BLACK_FRIDAY_ENABLED:
        return (
            "[LIVE BLACK FRIDAY STATUS: INACTIVE]\n"
            "Black Friday is not enabled. Do not mention Black Friday or use Black Friday prices. "
            "Use the normal pricing menu only."
        )

    return (
        "[LIVE BLACK FRIDAY STATUS: ACTIVE]\n"
        "This is a limited-time Black Friday campaign that is currently active.\n"
        f"{BLACK_FRIDAY_OFFERS}\n"
        "For these offers only: describe the relevant offer as a limited-time Black Friday offer and "
        "invite the customer to book while the campaign is active. Never state or imply limited stock, "
        "limited quantity, remaining slots, or that the offer may run out. Do not change any normal prices, "
        "workflows, or gender restrictions."
    )

@app.get("/health")
async def health_check():
    return {"status": "ok"}

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

{get_black_friday_campaign_context()}

[STRICT AUTOMATED REMINDER]:
1. Match the user's language EXACTLY (reply in English if they use English).
2. Use the user's name to infer gender and adjust Arabic grammar naturally.
3. DO NOT dump full price lists. Quote only the specific requested price.
4. NEVER invent doctors or schedules. For non-laser medical inquiries, refer to 01142286600.
5. For HR/CV inquiries, refer to 01001298786.
6. ALWAYS include the required [IMAGE: ...] tag when mentioning prices, packages, branches, or machines/devices.
7. For active Black Friday pulse offers, append black_friday_pulses. For only the four Black Friday body packages named in the image rules, append black_friday_body. For all other areas, keep using women_areas.
8. For active Black Friday offers, use only the live campaign prices above. State the offer is limited-time, but never claim stock or appointment scarcity."""

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

    lower_user = message_text.lower().strip()
    if not image_tags and "01142286600" not in reply_text and "01001298786" not in reply_text:
        if BLACK_FRIDAY_ENABLED and any(w in lower_user for w in ["pulse", "pulses", "نبضة", "نبضات", "نبض"]):
            image_tags.append("black_friday_pulses")
        elif BLACK_FRIDAY_ENABLED and any(w in lower_user for w in ["half body", "full body", "نصف الجسم", "نص الجسم", "جسم كامل", "الجسم كامل"]):
            image_tags.append("black_friday_body")
        elif any(w in lower_user for w in ["machine", "machines", "device", "devices", "جهاز", "اجهزة", "أجهزة", "نوع الجهاز"]):
            image_tags.append("machines")
        elif any(w in lower_user for w in ["area", "areas", "مناطق", "bikini", "underarm", "بكيني", "اندر ارم"]):
            image_tags.append("women_areas")
        elif any(w in lower_user for w in ["branch", "branches", "مكانكم", "فروع", "عنوان"]):
            image_tags.append("branches")
        elif any(w in lower_user for w in ["package", "packages", "offer", "offers", "باقات", "عروض", "اسعار", "أسعار"]):
            image_tags.append("women_packages")

    clean_text = re.sub(r'\[IMAGE:.*?\]', '', reply_text)
    clean_text = re.sub(r'\[NOTIFY:.*?\]', '', clean_text, flags=re.DOTALL).strip()

    if clean_text:
        await send_text_reply(sender_id, clean_text)

    unique_tags = list(dict.fromkeys(image_tags))
    for img in unique_tags:
        await send_image_direct_upload(sender_id, img)


def get_ai_reply(sender_id: str, user_text: str) -> str:
    if sender_id not in user_chats:
        user_chats[sender_id] = client.chats.create(
            model="gemini-3.6-flash",
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
                    model="gemini-3.6-flash",
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
    file_path = f"images/{image_name}.jpg"

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
