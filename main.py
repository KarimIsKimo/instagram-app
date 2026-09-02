import os
import httpx
from fastapi import FastAPI, Request, Response, HTTPException
from google import genai

app = FastAPI()

# Credentials loaded from Render Environment Variables
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "")
PAGE_ACCESS_TOKEN = os.getenv("PAGE_ACCESS_TOKEN", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

client = genai.Client(api_key=GEMINI_API_KEY)

@app.get("/webhook")
async def verify_webhook(request: Request):
    """Answers Meta's verification challenge during initial setup."""
    mode = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge")

    if mode == "subscribe" and token == VERIFY_TOKEN:
        return Response(content=challenge, media_type="text/plain")
    raise HTTPException(status_code=403, detail="Verification failed")

@app.post("/webhook")
async def handle_instagram_messages(request: Request):
    """Receives incoming Instagram DMs and sends an AI reply."""
    body = await request.json()
    print("Received webhook payload:", body)

    if body.get("object") == "instagram":
        for entry in body.get("entry", []):
            for messaging_event in entry.get("messaging", []):
                
                # Ensure the event is an incoming message and not an echo or delivery receipt
                message_data = messaging_event.get("message")
                if message_data and not message_data.get("is_echo"):
                    sender_id = messaging_event.get("sender", {}).get("id")
                    message_text = message_data.get("text")
                    
                    if sender_id and message_text:
                        print(f"Message received from {sender_id}: {message_text}")
                        reply_text = get_ai_reply(message_text)
                        await send_reply(sender_id, reply_text)
                        
        return {"status": "success"}
    return Response(content="Not an Instagram event", status_code=200)

def get_ai_reply(user_text: str) -> str:
    """Generates a reply using the updated Gemini Flash model."""
    system_instruction = (
        "You are an Instagram assistant for our clinic. "
        "Keep replies warm, friendly, helpful, and concise. Use emojis."
    )
    try:
        response = client.models.generate_content(
            model="gemini-3.6-flash",
            contents=user_text,
            config={"system_instruction": system_instruction}
        )
        return response.text
    except Exception as e:
        print(f"Gemini API Error: {e}")
        return "Hello! Thanks for reaching out. A member of our team will get back to you shortly."

async def send_reply(recipient_id: str, text: str):
    """Sends the message back to the user via Meta Graph API."""
    url = "https://graph.facebook.com/v21.0/me/messages"
    headers = {
        "Authorization": f"Bearer {PAGE_ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }
    payload = {
        "recipient": {"id": recipient_id},
        "message": {"text": text}
    }
    
    async with httpx.AsyncClient() as http_client:
        response = await http_client.post(url, headers=headers, json=payload)
        print("Meta send response:", response.status_code, response.text)
        response.raise_for_status()
