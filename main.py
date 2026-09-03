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

# --- NEW: Memory storage for conversation history ---
user_sessions = {}

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
    # Pass sender_id so the AI knows WHO it's talking to
    reply_text = get_ai_reply(sender_id, message_text)

    image_tags = re.findall(r'\[IMAGE:(.*?)\]', reply_text)
    notify_match = re.search(r'\[NOTIFY:(.*?)\]', reply_text)
    patient_details = notify_match.group(1).strip() if notify_match else None

    clean_text = re.sub(r'\[IMAGE:.*?\]', '', reply_text)
    clean_text = re.sub(r'\[NOTIFY:.*?\]', '', clean_text).strip()

    if clean_text:
        await send_text_reply(sender_id, clean_text)

    for img in image_tags:
        image_url = f"{BASE_URL}/images/{img.strip()}.jpg"
        await send_image_reply(sender_id, image_url)

    if patient_details:
        print(f"🚨 NEW BOOKING REQUEST: {patient_details}")
        await send_whatsapp_alert(patient_details)

def get_ai_reply(sender_id: str, user_text: str) -> str:
    system_instruction = """
    You are a friendly and professional receptionist at "Jothen Clinics" on Instagram. 
    
    === 🧠 Conversational Flow & Memory (CRITICAL) ===
    - You are having an ongoing conversation. NEVER repeat your welcome greeting if the user has already said hello or is in the middle of a chat.
    - NEVER restart the conversation if the user says something like "let me check" or "I will confirm with you". Just say "Take your time!"
    - If the user types in Franco-Arabic, you must still reply in natural Egyptian Arabic script.

    === 🌐 Language Rule ===
    - ALWAYS reply in the SAME language the user speaks to you:
      * If the user writes in Arabic, respond in natural Egyptian Arabic (لهجة مصرية عامية بسيطة)، وبصيغة محايدة وغير جندرية تماماً (باستخدام "حضرتك").
      * If the user writes in English, respond in clear, warm, professional English.
    - Keep responses short, structured, and use light emojis.

    === 📅 Working Days ===
    - Saturday to Thursday, 12:00 PM to 10:00 PM (Friday is off).

    === 📍 Branches and Phone Numbers ===
    1. Roxy Branch: 55 El-Khalifa El-Mamoun St., in front of Roxy Cinema. 📱 01156391111
    2. Madinet Nasr Branch: Clinic 104, 8 Dr. Hassan El-Sharif St. 📱 01022227818
    3. Tagamoa Branch: First Medical Park, Clinic 102. 📱 01023554897
    4. El-Rehab Branch: Medical Center 3, Clinic 201. 📱 01011103333
    5. Hadaye2 El Ahram Branch: 4th Mina Gate, El-Geish Main St., No. 413. 📱 01032280016
    * If asked about branches, list them briefly and append: [IMAGE: branches]

    === 💰 Pricing Rule ===
    - NEVER dump long or cluttered price lists into the chat.
    - If a patient asks about offers generally, reply warmly without listing prices, and send the visual menu image.
    - Only quote specific prices if the patient asks about a single targeted service.

    --- Reference Pricing Menu ---
    [Women's Packages]: 1000 Pulses: 800 LE | 2000 Pulses: 1500 LE | 3000 Pulses: 2000 LE (Append: [IMAGE: women_packages])
    [Women's Areas]: Underarm: 150 LE | Bikini + Line: 300 LE | Face: 250 LE | Full Body: 2500 LE (Append: [IMAGE: women_areas])
    [Men's Offers]: Beard Shaping: 300 LE | Full Face: 500 LE | Underarm: 400 LE (Append: [IMAGE: men_offers])

    === 🤖 Booking Requests (Step-by-Step Collection) ===
    - You are strictly forbidden from confirming appointments in the calendar yourself.
    - To book, you need these 5 pieces of info: Branch, Phone Number, Session Type, Day, and Time.
    - If the user asks to book, politely ask for them using this exact format:
      أهلاً بحضرتك 🌷 شكراً لتواصلك مع عيادات جوتن. برجاء إرسال:
      ▪️ الفرع الأقرب
      ▪️ رقم الموبايل
      ▪️ نوع الجلسة المطلوبة
      ▪️ اليوم المناسب
      ▪️ الوقت المناسب
      وذلك لتأكيد الحجز وإبلاغكم بأقرب موعد متاح.
    - IF THEY PROVIDE DETAILS ONE BY ONE (e.g., they just send their phone number): DO NOT ask for everything again. Look at the chat history, acknowledge what they gave you, and ask ONLY for the missing details.
    - Once you have collected ALL 5 details across the conversation, tell them the reception team will call them shortly, and append this exact tag:
      [NOTIFY: Name/Phone, Branch, Service, Day and Time]
    """

    # --- Initialize memory for new patients ---
    if sender_id not in user_sessions:
        user_sessions[sender_id] = []

    # --- Add user's new message to their history ---
    user_sessions[sender_id].append({"role": "user", "parts": [user_text]})

    # --- Keep only the last 10 messages (5 back-and-forths) to stay fast ---
    if len(user_sessions[sender_id]) > 10:
        user_sessions[sender_id] = user_sessions[sender_id][-10:]
    
    models_to_try = ["gemini-3.6-flash", "gemini-3.5-flash-lite"]
    
    for model_name in models_to_try:
        try:
            response = client.models.generate_content(
                model=model_name,
                contents=user_sessions[sender_id],  # <-- Passing FULL history, not just text!
                config={"system_instruction": system_instruction}
            )
            
            # --- Save the AI's reply to memory ---
            user_sessions[sender_id].append({"role": "model", "parts": [response.text]})
            
            return response.text
        except Exception as e:
            print(f"⚠️ Error calling {model_name}: {e}")
            if "503" in str(e) or "429" in str(e):
                time.sleep(2)
            continue

    return "أهلاً بحضرتك 🌷 شكراً لتواصلك مع عيادات جوتن. ثواني وفريق الاستقبال هيكون معاك."

async def send_text_reply(recipient_id: str, text: str):
    url = f"https://graph.instagram.com/v21.0/me/messages"
    headers = {"Authorization": f"Bearer {PAGE_ACCESS_TOKEN.strip()}", "Content-Type": "application/json"}
    payload = {"recipient": {"id": recipient_id}, "message": {"text": text}}
    async with httpx.AsyncClient() as http_client:
        response = await http_client.post(url, headers=headers, json=payload)
        if response.status_code != 200:
            print(f"❌ Instagram Text Error: {response.text}")

async def send_image_reply(recipient_id: str, image_url: str):
    url = f"https://graph.instagram.com/v21.0/me/messages"
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
        "messaging_product": "whatsapp", "to": STAFF_PHONE_NUMBER, "type": "template",
        "template": {"name": "new_booking_alert", "language": {"code": "en_US"},
        "components": [{"type": "body", "parameters": [{"type": "text", "text": patient_details}]}]}
    }
    async with httpx.AsyncClient() as http_client:
        await http_client.post(url, headers=headers, json=payload)
