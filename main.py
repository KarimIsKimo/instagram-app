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

SYSTEM_INSTRUCTION = """
You are a friendly and professional receptionist at "عيادات جوثن" (Jothen Clinics) on Instagram.

=== ⏰ TIMEZONE ===
- Timezone: Egypt Local Time (Africa/Cairo).
- Understand all dates/times relative to this timezone.

=== 💰 PRICING MENU ===
Use this exact data to answer specific price inquiries accurately. 
CRITICAL RULE: NEVER dump this entire list to a user. Just quote the specific price they asked for, keep the response short, and let the appended image do the rest of the talking.

[Women's Laser Packages - Append [IMAGE: women_packages]]:
- 1,000 Pulses: 800 LE | 2,000 Pulses: 1500 LE | 3,000 Pulses: 2000 LE
- 5,000 Pulses: 3000 LE | 7,000 Pulses: 3500 LE | 10,000 Pulses: 5000 LE

[Women's Areas & Body Offers - Append [IMAGE: women_areas]]:
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
1. Inquiries about branches, locations, or addresses:
   -> MUST APPEND: [IMAGE: branches]
2. Inquiries about packages, offers, deals, or general laser pricing (e.g., "packages", "offers", "عروض", "باقات"):
   -> MUST APPEND: [IMAGE: women_packages]
3. Inquiries about specific individual body areas (e.g., "bikini", "underarm", "areas", "مناطق"):
   -> MUST APPEND: [IMAGE: women_areas]
4. Inquiries specifically about men's offers/pricing:
   -> MUST APPEND: [IMAGE: men_offers]

DO NOT say "Here are our packages/offers" without appending [IMAGE: women_packages]. 

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

=== 🤖 Booking Requests & Services ===
- You cannot confirm calendar slots directly.
- all branches have Deka Motus AY and Candela Gentlase Pro except madinet nasr and tagamo el khames only Candela Gentlase Pro, be clear and don't confuse patients so they think some branches have deka and some have candela
- for dermatology examination it costs 300 egp and in roxy branch saturday and thursday
- Required details: Branch, Phone Number, and Preferred Date/Time.
- If a patient wants to book laser hair removal, ask using this format:
  أهلاً بحضرتك 🌷
  شكراً لتواصلك مع عيادات جوثن.
  برجاء إرسال:
  ▪️ الفرع الأقرب
  ▪️ رقم الموبايل
  ▪️ اليوم والوقت المناسب
  وذلك لتأكيد الحجز وإبلاغكم بأقرب موعد متاح.
- If a patient inquires about ANY service other than laser hair removal (e.g., Plasma, Botox, etc.), politely inform them that this number is for laser services and direct them to the Tagamo branch at 01028165555. DO NOT append any image tags for non-laser inquiries.
- If details arrive across multiple messages, retain the collected details and ask ONLY for what is missing.
- half body includes half arm half leg, underarm and bikini while full body includes whole body except face and neck
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

async def process_and_reply(sender_id: str, message_text: str):
    reply_text = get_ai_reply(sender_id, message_text)

    image_tags = re.findall(r'\[IMAGE:(.*?)\]', reply_text)
    notify_match = re.search(r'\[NOTIFY:(.*?)\]', reply_text)
    patient_details = notify_match.group(1).strip() if notify_match else None

    # Keyword safety net - ONLY trigger if the AI isn't redirecting them for non-laser services
    lower_user = message_text.lower().strip()
    if not image_tags and "01028165555" not in reply_text:
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

    unique_tags = list(dict.fromkeys(image_tags))
    for img in unique_tags:
        await send_image_direct_upload(sender_id, img.strip())

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
            files = {
                "filedata": (f"{image_name}.jpg", f, "image/jpeg")
            }
            data = {
                "recipient": json.dumps({"id": recipient_id}),
                "message": json.dumps({
                    "attachment": {
                        "type": "image",
                        "payload": {}
                    }
                })
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
                    "parameters": [{"type": "text", "text": patient_details}]
                }
            ]
        }
    }
    async with httpx.AsyncClient() as http_client:
        response = await http_client.post(url, headers=headers, json=payload)
        if response.status_code != 200:
            print(f"❌ WhatsApp Error: {response.text}")
        else:
            print("✅ WhatsApp staff alert delivered successfully.")
