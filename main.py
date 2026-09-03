import os
import re
import httpx
import time
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

client = genai.Client(api_key=GEMINI_API_KEY)
BASE_URL = "https://instagram-app-o2v3.onrender.com"

user_chats = {}

SYSTEM_INSTRUCTION = """
You are a friendly and professional receptionist at "عيادات جوثن" (Jothen Clinics) on Instagram.

=== ⏰ TIMEZONE ===
- Timezone: Egypt Local Time (Africa/Cairo).
- Understand all dates/times relative to this timezone.

=== 🖼️ MANDATORY IMAGE TAG RULES (CRITICAL) ===
You MUST append the corresponding image tag at the end of your message whenever these topics come up:
1. Inquiries about branches, locations, or addresses:
   -> MUST APPEND: [IMAGE: branches]
2. Inquiries about packages, offers, deals, or general laser pricing (e.g., "packages", "offers", "عروض", "باقات"):
   -> MUST APPEND: [IMAGE: women_packages]
3. Inquiries about specific individual body areas (e.g., "bikini", "underarm", "areas", "مناطق"):
   -> MUST APPEND: [IMAGE: women_areas]
4. Inquiries specifically about men's offers/pricing:
   -> MUST APPEND: [IMAGE: men_offers]

DO NOT say "Here are our packages/offers" without appending [IMAGE: women_packages]. The tag is strictly required for the image to be delivered.

=== 🧠 Conversational Flow & Memory ===
- You are in an ongoing conversation. NEVER repeat greetings or re-introduce yourself.
- If the user says "let me check" or "I will confirm with you", respond warmly: "تمام تحت أمرك، وقت ما تحب تنورنا."
- If the user types in Franco-Arabic, respond in natural Egyptian Arabic script.

=== 🌐 Language Rule ===
- ALWAYS reply in the SAME language the user speaks:
  * Arabic: Natural Egyptian Arabic (لهجة مصرية عامية بسيطة), completely gender-neutral (using "حضرتك" and "إبلاغكم").
  * English: Clear, warm, professional English.
- Keep responses short, structured, and use light emojis.

=== 📅 Working Days ===
- Saturday to Thursday, 12:00 PM to 10:00 PM (Friday is off).
- السبت للخميس من 12 ظهراً لـ 10 مساءً (الجمعة إجازة).

=== 📍 Branches and Contact ===
1. فرع روكسي (Roxy): 55 شارع الخليفة المأمون، أمام سينما روكسي. 📱 01156391111
2. فرع مدينة نصر (Madinet Nasr): عيادة 104، 8 شارع د. حسن الشريف. 📱 01022227818
3. فرع التجمع الخامس (Tagamoa): فيرست ميديكال بارك، عيادة 102. 📱 01023554897
4. فرع الرحاب (El-Rehab): المركز الطبي 3، عيادة 201. 📱 01011103333
5. فرع حدائق الأهرام (Hadaye2 El Ahram): البوابة الرابعة مينا، شارع الجيش الرئيسي، رقم 413. 📱 01032280016

=== 🤖 Booking Requests (Step-by-Step) ===
- You cannot confirm calendar slots directly.
- Required details: Branch, Phone Number, and Preferred Date/Time.
- If a patient wants to book, ask using this format:
  أهلاً بحضرتك 🌷
  شكراً لتواصلك مع عيادات جوثن.
  برجاء إرسال:
  ▪️ الفرع الأقرب
  ▪️ رقم الموبايل
  ▪️ اليوم والوقت المناسب
  وذلك لتأكيد الحجز وإبلاغكم بأقرب موعد متاح.
- If details arrive across multiple messages, retain the collected details and ask ONLY for what is missing.
- Once all details are gathered across the conversation, confirm reception will call shortly and append:
  [NOTIFY: Name/Phone, Branch, Date and Time]
"""

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
async def handle_instagram_messages(request: Request, background_tasks: BackgroundTasks):
    body = await request.json()
    if body.get("object") == "instagram":
        for entry in body.get("entry", []):
            for messaging_event in entry.get("messaging", []):
                message_data = messaging_event.get("message")
                if message_data and not message_data.get("is_echo"):
                    sender_id = messaging_event.get("sender", {}).get("id")
                    message_text = message_data.get("text")
                    if sender_id and message_text:
                        background_tasks.add_task(process_and_reply, sender_id, message_text)
    return {"status": "success"}

async def process_and_reply(sender_id: str, message_text: str):
    reply_text = get_ai_reply(sender_id, message_text)

    # Extract tags generated by the AI
    image_tags = re.findall(r'\[IMAGE:(.*?)\]', reply_text)
    notify_match = re.search(r'\[NOTIFY:(.*?)\]', reply_text)
    patient_details = notify_match.group(1).strip() if notify_match else None

    # Code-level fallback: if AI forgot the tag when user asked about packages/offers
    lower_user = message_text.lower().strip()
    if not image_tags:
        if any(w in lower_user for w in ["package", "packages", "offer", "offers", "باقات", "عروض", "اسعار", "أسعار"]):
            image_tags.append("women_packages")
        elif any(w in lower_user for w in ["branch", "branches", "مكانكم", "فروع", "عنوان"]):
            image_tags.append("branches")
        elif any(w in lower_user for w in ["area", "areas", "مناطق"]):
            image_tags.append("women_areas")

    clean_text = re.sub(r'\[IMAGE:.*?\]', '', reply_text)
    clean_text = re.sub(r'\[NOTIFY:.*?\]', '', clean_text).strip()

    if clean_text:
        await send_text_reply(sender_id, clean_text)

    # Deduplicate image tags while preserving order
    unique_tags = list(dict.fromkeys(image_tags))
    for img in unique_tags:
        image_url = f"{BASE_URL}/images/{img.strip()}.jpg"
        await send_image_reply(sender_id, image_url)

    if patient_details:
        print(f"🚨 NEW BOOKING REQUEST: {patient_details}")
        await send_whatsapp_alert(patient_details)

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
            return response.text
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

    return "أهلاً بحضرتك 🌷 شكراً لتواصلك مع عيادات جوثن. ثواني وفريق الاستقبال هيكون معاك."

async def send_text_reply(recipient_id: str, text: str):
    url = "https://graph.instagram.com/v21.0/me/messages"
    headers = {"Authorization": f"Bearer {PAGE_ACCESS_TOKEN.strip()}", "Content-Type": "application/json"}
    payload = {"recipient": {"id": recipient_id}, "message": {"text": text}}
    async with httpx.AsyncClient() as http_client:
        response = await http_client.post(url, headers=headers, json=payload)
        if response.status_code != 200:
            print(f"❌ Instagram Text Error: {response.text}")

async def send_image_reply(recipient_id: str, image_url: str):
    url = "https://graph.instagram.com/v21.0/me/messages"
    headers = {"Authorization": f"Bearer {PAGE_ACCESS_TOKEN.strip()}", "Content-Type": "application/json"}
    payload = {"recipient": {"id": recipient_id}, "message": {"attachment": {"type": "image", "payload": {"url": image_url}}}}
    async with httpx.AsyncClient() as http_client:
        response = await http_client.post(url, headers=headers, json=payload)
        if response.status_code != 200:
            print(f"❌ Instagram Image Error: {response.text}")

async def send_whatsapp_alert(patient_details: str):
    if not WA_PHONE_NUMBER_ID or not WA_ACCESS_TOKEN:
        return
    url = f"https://graph.facebook.com/v21.0/{WA_PHONE_NUMBER_ID}/messages"
    headers = {"Authorization": f"Bearer {WA_ACCESS_TOKEN.strip()}", "Content-Type": "application/json"}
    payload = {
        "messaging_product": "whatsapp",
        "to": STAFF_PHONE_NUMBER,
        "type": "template",
        "template": {
            "name": "new_booking_alert",
            "language": {"code": "en_US"},
            "components": [{"type": "body", "parameters": [{"type": "text", "text": patient_details}]}]
        }
    }
    async with httpx.AsyncClient() as http_client:
        response = await http_client.post(url, headers=headers, json=payload)
        if response.status_code != 200:
            print(f"❌ WhatsApp Error: {response.text}")
