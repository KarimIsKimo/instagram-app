import os
import re
import json
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

user_chats = {}
processed_mids = set()
user_profiles = {}  # Cache to store user names so we only fetch them once

SYSTEM_INSTRUCTION = """
You are a friendly and professional receptionist at "عيادات جوثن" (Jothen Clinics) on Instagram maintain professional and respectful manner with customers.

=== ⏰ TIMEZONE ===
- Timezone: Egypt Local Time (Africa/Cairo).

=== 💰 PRICING MENU ===
Use this exact data to answer specific price inquiries accurately. 
CRITICAL RULE: NEVER dump this entire list to a user. Just quote the specific price they asked for, keep the response short, and let the appended image do the rest.
Men DO NOT buy pulses, they only buy sessions or by area, so if patient is a man don't provide packages or female offers
Silicone skin protector is 100 egp while distance guage (مبعد) is 600 egp

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
- Once all 3 details are gathered, append: [NOTIFY: Name/Phone, Branch, Date and Time]
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
async def handle_instagram_messages(request: Request, backgroundTasks: BackgroundTasks):
    body = await request.json()
    if body.get("object") == "instagram":
        for entry in body.get("entry", []):
            for messaging_event in entry.get("messaging", []):
                message_data = messaging_event.get("message")
                if message_data and not message_data.get("is_echo"):
                    mid = message_data.get("mid")
                    if mid:
                        if mid in processed_mids:
                            continue
                        processed_mids.add(mid)
                        if len(processed_mids) > 1000:
                            processed_mids.pop()

                    sender_id = messaging_event.get("sender", {}).get("id")
                    message_text = message_data.get("text")
                    if sender_id and message_text:
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
    
    # Fetch the user's name and append it to the message context
    user_name = await get_ig_user_name(sender_id)
    name_context = f"[Context: User's name is {user_name}]\n" if user_name else ""

    enriched_text = f"""{name_context}{message_text}

[STRICT AUTOMATED REMINDER]:
1. Match the user's language EXACTLY (reply in English if they use English).
2. Use the user's name to infer gender and adjust Arabic grammar naturally.
3. DO NOT dump full price lists. Quote only the specific requested price.
4. NEVER invent doctors or schedules. For non-laser medical inquiries, refer to 01142286600.
5. For HR/CV inquiries, refer to 01001298786.
6. ALWAYS include the required [IMAGE: ...] tag when mentioning prices, packages, branches, or machines/devices."""

    reply_text = get_ai_reply(sender_id, enriched_text)

    raw_image_tags = re.findall(r'\[IMAGE:(.*?)\]', reply_text)
    image_tags = [tag.strip() for tag in raw_image_tags]

    notify_match = re.search(r'\[NOTIFY:(.*?)\]', reply_text)
    patient_details = notify_match.group(1).strip() if notify_match else None

    lower_user = message_text.lower().strip()
    if not image_tags and "01142286600" not in reply_text and "01001298786" not in reply_text:
        if any(w in lower_user for w in ["machine", "machines", "device", "devices", "جهاز", "اجهزة", "أجهزة", "نوع الجهاز"]):
            image_tags.append("machines")
        elif any(w in lower_user for w in ["area", "areas", "مناطق", "bikini", "underarm", "بكيني", "اندر ارم"]):
            image_tags.append("women_areas")
        elif any(w in lower_user for w in ["branch", "branches", "مكانكم", "فروع", "عنوان"]):
            image_tags.append("branches")
        elif any(w in lower_user for w in ["package", "packages", "offer", "offers", "باقات", "عروض", "اسعار", "أسعار"]):
            image_tags.append("women_packages")

    clean_text = re.sub(r'\[IMAGE:.*?\]', '', reply_text)
    clean_text = re.sub(r'\[NOTIFY:.*?\]', '', clean_text).strip()

    if clean_text:
        await send_text_reply(sender_id, clean_text)

    unique_tags = list(dict.fromkeys(image_tags))
    for img in unique_tags:
        await send_image_direct_upload(sender_id, img)

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
            "language": {"code": "ar_EG"},
            "components": [
                {
                    "type": "body",
                    "parameters": [{"type": "text", "text": patient_details}],
                }
            ],
        },
    }
    async with httpx.AsyncClient() as http_client:
        response = await http_client.post(url, headers=headers, json=payload)
        if response.status_code != 200:
            print(f"❌ WhatsApp Error: {response.text}")
        else:
            print("✅ WhatsApp staff alert delivered successfully.")
