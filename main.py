import os
import httpx
from fastapi import FastAPI, Request, Response, BackgroundTasks, HTTPException
from google import genai

app = FastAPI()

VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "")
PAGE_ACCESS_TOKEN = os.getenv("PAGE_ACCESS_TOKEN", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

client = genai.Client(api_key=GEMINI_API_KEY)

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
                
                # Ignore echoes from our own bot
                if message_data and not message_data.get("is_echo"):
                    sender_id = messaging_event.get("sender", {}).get("id")
                    message_text = message_data.get("text")

                    if sender_id and message_text:
                        # Process response asynchronously so Meta gets an instant 200 OK
                        background_tasks.add_task(process_and_reply, sender_id, message_text)

    # Return immediately to stop Meta from retrying and creating duplicate messages
    return {"status": "success"}

async def process_and_reply(sender_id: str, message_text: str):
    reply_text = get_ai_reply(message_text)
    await send_reply(sender_id, reply_text)

def get_ai_reply(user_text: str) -> str:
    system_instruction = (
        "أنت المساعد الذكي لعيادتنا على إنستجرام. "
        "اجعل الردود ودودة، واضحة، ومنظمة، وباللهجة العربية المناسبة أو بنفس لغة العميل. "
        "استخدم الإيموجي بشكل لطيف."
    )
    # Attempt primary model first, fallback if unavailable
    for model_name in ["gemini-3.6-flash", "gemini-2.5-flash"]:
        try:
            response = client.models.generate_content(
                model=model_name,
                contents=user_text,
                config={"system_instruction": system_instruction}
            )
            return response.text
        except Exception as e:
            print(f"Error with {model_name}: {e}")
            continue

    return "أهلاً بك! نشكر تواصلك معنا. سيقوم أحد مسؤولي الاستقبال بالرد على استفسارك في أقرب وقت."

async def send_reply(recipient_id: str, text: str):
    url = "https://graph.instagram.com/v21.0/me/messages"
    headers = {
        "Authorization": f"Bearer {PAGE_ACCESS_TOKEN.strip()}",
        "Content-Type": "application/json"
    }
    payload = {
        "recipient": {"id": recipient_id},
        "message": {"text": text}
    }

    async with httpx.AsyncClient() as http_client:
        response = await http_client.post(url, headers=headers, json=payload)
        print("Instagram send status:", response.status_code)
        response.raise_for_status()
