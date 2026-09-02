import os
import re
import httpx
from fastapi import FastAPI, Request, Response, BackgroundTasks, HTTPException
from fastapi.staticfiles import StaticFiles
from google import genai

app = FastAPI()

# Mount the local images directory so Meta can access the files
os.makedirs("images", exist_ok=True)
app.mount("/images", StaticFiles(directory="images"), name="images")

VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "")
PAGE_ACCESS_TOKEN = os.getenv("PAGE_ACCESS_TOKEN", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

client = genai.Client(api_key=GEMINI_API_KEY)

# Your live Render URL
BASE_URL = "https://instagram-app-o2v3.onrender.com"

@app.get("/webhook")
async def verify_webhook(request: Request):
    """Answers Meta's verification challenge."""
    mode = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge")

    if mode == "subscribe" and token == VERIFY_TOKEN:
        return Response(content=challenge, media_type="text/plain")
    raise HTTPException(status_code=403, detail="Verification failed")

@app.post("/webhook")
async def handle_instagram_messages(request: Request, background_tasks: BackgroundTasks):
    """Receives incoming messages and delegates processing to avoid timeouts."""
    body = await request.json()

    if body.get("object") == "instagram":
        for entry in body.get("entry", []):
            for messaging_event in entry.get("messaging", []):
                message_data = messaging_event.get("message")
                
                # Process only incoming messages from users (ignore echoes)
                if message_data and not message_data.get("is_echo"):
                    sender_id = messaging_event.get("sender", {}).get("id")
                    message_text = message_data.get("text")

                    if sender_id and message_text:
                        background_tasks.add_task(process_and_reply, sender_id, message_text)

    return {"status": "success"}

async def process_and_reply(sender_id: str, message_text: str):
    reply_text = get_ai_reply(message_text)

    # 1. Action: Send an image if the AI appended a tag
    image_match = re.search(r'\[IMAGE:(.*?)\]', reply_text)
    if image_match:
        image_name = image_match.group(1).strip()
        reply_text = re.sub(r'\[IMAGE:.*?\]', '', reply_text).strip()
        image_url = f"{BASE_URL}/images/{image_name}.jpg"

        await send_text_reply(sender_id, reply_text)
        await send_image_reply(sender_id, image_url)

    # 2. Action: Notify the staff if booking details were collected
    elif "[NOTIFY:" in reply_text:
        notify_match = re.search(r'\[NOTIFY:(.*?)\]', reply_text)
        if notify_match:
            patient_details = notify_match.group(1).strip()
            reply_text = re.sub(r'\[NOTIFY:.*?\]', '', reply_text).strip()

            await send_text_reply(sender_id, reply_text)
            print(f"🚨 NEW BOOKING REQUEST: {patient_details}")
            # The actual Email/Telegram notification logic will go here

    # 3. Action: Send standard text response
    else:
        await send_text_reply(sender_id, reply_text)

def get_ai_reply(user_text: str) -> str:
    system_instruction = """
    أنت موظف استقبال ودود وشاطر في عيادتنا على إنستجرام. 
    مهمتك ترد على استفسارات العملاء وتساعدهم وتوجههم، وضروري جداً تتكلم بلهجة "مصرية عامية" بسيطة وطبيعية كأنك إنسان حقيقي، وبلاش لغة عربية فصحى معقدة. خلي ردودك قصيرة واستخدم إيموجيز خفيفة.

    قاعدة هامة: ممنوع تماماً تأكد حجز أو ميعاد في الجدول من نفسك. دورك بس تاخد بياناتهم عشان الاستقبال يكلمهم.

    الصور المتاحة (لازم تحط الكود ده في آخر ردك لو العميل سأل عن الحاجات دي):
    1. لو العميل سأل عن أنواع الأجهزة اللي بنستخدمها، اشرحلة ببساطة وضيف: [IMAGE: machines]
    2. لو سيدة سألت عن باقات العروض الشاملة، ضيف: [IMAGE: women_packages]
    3. لو سيدة سألت عن عروض مناطق معينة في الجسم، ضيف: [IMAGE: women_areas]
    4. لو العميل سأل عن فروعنا وعناويننا، ضيف: [IMAGE: branches]
    5. لو راجل سأل عن عروض الرجال، ضيف: [IMAGE: men_offers]

    طلبات الحجز:
    لو العميل طلب يحجز أو سأل عن الحجز، اطلب منه بلطف يبعتلك:
    - الاسم بالكامل
    - رقم الموبايل
    - الفرع الأقرب ليه
    وبمجرد ما يكتب البيانات دي كلها، قوله إن فريق الاستقبال هيكلمه فوراً عشان يحدد معاه الميعاد المناسب، وبعدين ضيف الكود ده في آخر رسالتك بالظبط عشان السيستم يبلغنا:
    [NOTIFY: الاسم، رقم الهاتف، الفرع]
    """
    for model_name in ["gemini-3.6-flash", "gemini-2.5-flash"]:
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

    return "أهلاً بيك! ثواني وفريق الاستقبال هيكون معاك ويرد على كل استفساراتك."

async def send_text_reply(recipient_id: str, text: str):
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
        response.raise_for_status()

async def send_image_reply(recipient_id: str, image_url: str):
    url = "https://graph.instagram.com/v21.0/me/messages"
    headers = {
        "Authorization": f"Bearer {PAGE_ACCESS_TOKEN.strip()}",
        "Content-Type": "application/json"
    }
    payload = {
        "recipient": {"id": recipient_id},
        "message": {
            "attachment": {
                "type": "image",
                "payload": {"url": image_url}
            }
        }
    }
    async with httpx.AsyncClient() as http_client:
        response = await http_client.post(url, headers=headers, json=payload)
        print("Image Send Status:", response.status_code)
        response.raise_for_status()
