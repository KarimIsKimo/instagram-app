import os
import re
import httpx
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

# --- NEW: Health Check Endpoint for UptimeRobot ---
@app.get("/health")
async def health_check():
    """Keeps the Render server awake when pinged."""
    return {"status": "ok"}
# --------------------------------------------------

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
    reply_text = get_ai_reply(message_text)

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

def get_ai_reply(user_text: str) -> str:
    system_instruction = """
    You are a friendly and professional receptionist at "Jothen Clinics" on Instagram. 
    Always reply in polite, natural, and warm English. Keep your responses short, structured, and use light emojis.

    === 📅 Working Days ===
    - Saturday to Thursday (Friday is off).

    === 📍 Branches and Phone Numbers ===
    1. Roxy Branch: 55 El-Khalifa El-Mamoun St., in front of Roxy Cinema, above Baby Land. 📱 01156391111
    2. Madinet Nasr Branch: Clinic 104, 8 Dr. Hassan El-Sharif St. 📱 01022227818
    3. Tagamoa Branch: First Medical Park, Clinic 102, next to the Court. 📱 01023554897
    4. El-Rehab Branch: Medical Center 3, above QNB and National Bank, Clinic 201. 📱 01011103333
    5. Hadaye2 El Ahram Branch: 4th Mina Gate, El-Geish Main St., No. 413 Ground Floor, next to Ashraf Supermarket. 📱 01032280016
    * If a patient asks about branches, list them briefly and append: [IMAGE: branches]

    === 🧠 Pricing Rule ===
    - NEVER dump long or cluttered price lists randomly into the text!
    - Keep these prices in mind to answer only if a patient asks for a specific service price (e.g., "How much is the bikini session?").
    - If a patient asks about offers generally, reply warmly and send the proper visual menu image without overwhelming text.

    --- Reference Pricing Menu (For specific answers only) ---
    [Women's Packages]:
    - 1000 Pulses: 800 LE | 2000 Pulses: 1500 LE | 3000 Pulses: 2000 LE | 5000 Pulses: 3000 LE | 7000 Pulses: 3500 LE | 10000 Pulses: 5000 LE
    (When packages are requested, append: [IMAGE: women_packages])

    [Women's Areas]:
    - Underarm: 150 LE | Bikini + Line: 300 LE | Bikini + Underarm + Line: 350 LE
    - Mustache: 100 LE | Face: 250 LE | Face + Chin: 350 LE | Face + Neck: 450 LE
    - Full Body: 2500 LE | Full Body (No Abdomen/Back): 2000 LE | Half Body: 1250 LE
    - Half Arm: 600 LE | Full Arm: 800 LE | Half Lower Leg: 800 LE | Half Upper Leg: 1000 LE | Full Leg: 1500 LE
    (When specific women's areas are requested, append: [IMAGE: women_areas])

    [Men's Offers - Mention ONLY if explicitly requested]:
    - Beard Shaping: 300 LE | Beard & Neck: 500 LE | Beard, Neck & Jawline: 750 LE
    - Full Face: 500 LE | Face with Neck: 750 LE
    - Underarm: 400 LE | Boxer: 500 LE | Boxer & Line: 650 LE | Boxer & Underarm: 750 LE | Boxer, Underarm & Beard Shaping: 1000 LE
    - Pilonidal Sinus: 750 LE | Ear: 250 LE | Shoulder/Chest/Back: 1000 LE
    - Full Body: 5000 pulses for 4000 LE / 6000 pulses for 5000 LE
    (When men's offers are explicitly requested, append: [IMAGE: men_offers])

    === 🤖 Booking Requests ===
    - You are strictly forbidden from confirming appointments in the calendar yourself.
    - If a patient wants to book, politely ask for: (Full Name, Phone Number, Preferred Branch, and Desired Service/Area).
    - As soon as they provide all these details, tell them our reception team will contact them right away to schedule the appointment, and append this tag to your exact message:
      [NOTIFY: Name, Phone, Branch, Service]
    """
    
    for model_name in ["gemini-3.6-flash", "gemini-3.5-flash-lite"]:
        try:
            response = client.models.generate_content(
                model=model_name,
                contents=user_text,
                config={"system_instruction": system_instruction}
            )
            return response.text
        except Exception as e:
            print(f"Error calling {model_name}: {e}")
            continue

    return "Welcome to Jothen Clinics! One of our reception team members will be with you shortly to answer all your inquiries."

async def send_text_reply(recipient_id: str, text: str):
    url = f"https://graph.instagram.com/v21.0/me/messages"
    headers = {"Authorization": f"Bearer {PAGE_ACCESS_TOKEN.strip()}", "Content-Type": "application/json"}
    payload = {"recipient": {"id": recipient_id}, "message": {"text": text}}
    async with httpx.AsyncClient() as http_client:
        await http_client.post(url, headers=headers, json=payload)

async def send_image_reply(recipient_id: str, image_url: str):
    url = f"https://graph.instagram.com/v21.0/me/messages"
    headers = {"Authorization": f"Bearer {PAGE_ACCESS_TOKEN.strip()}", "Content-Type": "application/json"}
    payload = {"recipient": {"id": recipient_id}, "message": {"attachment": {"type": "image", "payload": {"url": image_url}}}}
    async with httpx.AsyncClient() as http_client:
        await http_client.post(url, headers=headers, json=payload)

async def send_whatsapp_alert(patient_details: str):
    """Sends a WhatsApp Template message to bypass the 24-hour rule."""
    if not WA_PHONE_NUMBER_ID or not WA_ACCESS_TOKEN:
        print("Missing WhatsApp credentials. Alert not sent.")
        return

    url = f"https://graph.facebook.com/v21.0/{WA_PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {WA_ACCESS_TOKEN.strip()}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "messaging_product": "whatsapp",
        "to": STAFF_PHONE_NUMBER,
        "type": "template",
        "template": {
            "name": "new_booking_alert", 
            "language": {
                "code": "en_US"  # Ensure this matches your Meta template language code ("ar", "en", or "en_US")
            },
            "components": [
                {
                    "type": "body",
                    "parameters": [
                        {
                            "type": "text",
                            "text": patient_details
                        }
                    ]
                }
            ]
        }
    }
    
    async with httpx.AsyncClient() as http_client:
        response = await http_client.post(url, headers=headers, json=payload)
        print("WhatsApp Alert Status:", response.status_code)
        if response.status_code != 200:
            print("WhatsApp Error:", response.text)
