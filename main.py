import os
import httpx
from fastapi import FastAPI, Request, Response, HTTPException
from google import genai

app = FastAPI()

# Secure environment variables set on your hosting platform
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "your_custom_verification_string")
PAGE_ACCESS_TOKEN = os.getenv("PAGE_ACCESS_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

client = genai.Client(api_key=GEMINI_API_KEY)

@app.get("/webhook")
async def verify_webhook(request: Request):
    """Answers Meta's one-time challenge to verify endpoint ownership."""
    mode = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge")

    if mode == "subscribe" and token == VERIFY_TOKEN:
        return Response(content=challenge, media_type="text/plain")
    raise HTTPException(status_code=403, detail="Verification failed")

@app.post("/webhook")
async def handle_instagram_messages(request: Request):
    """Receives incoming Instagram DMs and routes them to Gemini."""
    body = await request.json()
    
    if body.get("object") == "instagram":
        for entry in body.get("entry", []):
            for messaging_event in entry.get("messaging", []):
                
                # Filter out echos and read receipts to prevent infinite loops
                if "message" in messaging_event and "is_echo" not in messaging_event["message"]:
                    sender_id = messaging_event["sender"]["id"]
                    message_text = messaging_event["message"].get("text")
                    
                    if message_text:
                        reply_text = get_ai_reply(message_text)
                        await send_reply(sender_id, reply_text)
                        
        return {"status": "success"}
    return HTTPException(status_code=404, detail="Not an Instagram event")

def get_ai_reply(user_text: str) -> str:
    system_instruction = (
        "You are an Instagram assistant for our clinic. "
        "Keep replies warm, visually engaging (use emojis), and concise."
    )
    # Externalize your Instagram-specific business rules here
    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=user_text,
        config={"system_instruction": system_instruction}
    )
    return response.text

async def send_reply(recipient_id: str, text: str):
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
        response.raise_for_status()