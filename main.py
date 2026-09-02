import os
import re
import httpx
from fastapi import FastAPI, Request, Response, BackgroundTasks, HTTPException
from fastapi.staticfiles import StaticFiles
from google import genai
from google.genai import types

app = FastAPI()

# Mount the local images directory so Meta can access the files
os.makedirs("images", exist_ok=True)
app.mount("/images", StaticFiles(directory="images"), name="images")

VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "")
PAGE_ACCESS_TOKEN = os.getenv("PAGE_ACCESS_TOKEN", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

# Initialize Google GenAI SDK
client = genai.Client(api_key=GEMINI_API_KEY)

# Your live Render URL
BASE_URL = "https://instagram-app-o2v3.onrender.com"

# Simple in-memory chat history (Note: for scaling, replace with Redis or DB)
CONVERSATION_HISTORY = {}

SYSTEM_INSTRUCTION = """
أنت موظف استقبال ودود وشاطر في عيادات "جوتن" (Jothen Clinics) على إنستجرام. 
تحدث دائماً بلهجة "مصرية عامية" بسيطة وطبيعية كأنك إنسان حقيقي، وخلّي ردودك قصيرة، منسقة، وخفيفة مع إيموجيز مناسبة.

=== 📅 أيام العمل ===
- من السبت للخميس (الجمعة إجازة).

=== 📍 الفروع وأرقام التليفونات ===
1. فرع مصر الجديدة: ٥٥ الخليفة المأمون أمام سينما روكسي، فوق محل بيبي لاند. 📱 01156391111
2. فرع مدينة نصر: عيادة 104، 8 شارع الدكتور حسن الشريف. 📱 01022227818
3. فرع التجمع الخامس: ميديكال بارك الأول، عيادة ١٠٢ بجوار المحكمة. 📱 01023554897
4. فرع الرحاب: المركز الطبي ٣، فوق البنك الأهلي والقطرى، عيادة ٢٠١. 📱 01011103333
5. فرع حدائق الأهرام: بوابة مينا الرابعة، شارع الجيش الرئيسي، رقم 413 الدور الأرضي، بجوار سوبر ماركت أشرف. 📱 01032280016
* لو سأل العميل عن الفروع، اكتبها باختصار وضيف: [IMAGE: branches]

=== 🧠 قاعدة الأسعار الهامة جداً ===
- إياك أن ترسل قوائم أسعار طويلة أو مكتظة في نص الرسالة بشكل عشوائي!
- احتفظ بالأسعار أدناه في ذاكرتك للإجابة فقط إذا سأل العميل عن سعر خدمة أو منطقة محددة بالذات (مثلاً: "بكام البكيني؟" أو "بكام الجلسة؟").
- إذا سأل العميل عن العروض بشكل عام، رد بشكل لطيف وابعت الصورة المناسبة ليوصل له المنيو البصري بوضوح، من غير رغي كتير.

--- جدول الأسعار المرجعي لك (للإجابة عند السؤال المحدد فقط) ---
[باقات السيدات والشاملة]:
- 1000 نبضة: 800 ج | 2000 نبضة: 1500 ج | 3000 نبضة: 2000 ج | 5000 نبضة: 3000 ج | 7000 نبضة: 3500 ج | 10000 نبضة: 5000 ج
(عند طلب الباقات، أضف: [IMAGE: women_packages])

[مساحات الجسم للسيدات]:
- أنډرآرم: 150 ج | بيجيني + لاين: 300 ج | بيجيني + أنډرآرم + لاين: 350 ج
- شنب: 100 ج | وجه: 250 ج | وجه + دقن: 350 ج | وجه + رقبة: 450 ج
- للجسم كله: 2500 ج | للجسم كله بدون بطن أو ظهر: 2000 ج | نصف جسم: 1250 ج
- نصف ذراع: 600 ج | ذراع كامل: 800 ج | نصف رجل سفلي: 800 ج | نصف رجل علوي: 1000 ج | رجل كاملة: 1500 ج
(عند طلب مناطق السيدات، أضف: [IMAGE: women_areas])

[عروض الرجال - تُذكر فقط لو طُلبت صراحة]:
- تحديد دقن: 300 ج | دقن ورقبة: 500 ج | دقن ورقبة وجو لاين: 750 ج
- وجه كامل: 500 ج | وجه مع رقبة: 750 ج
- أنډرآرم: 400 ج | بوكسر: 500 ج | بوكسر مع لاين: 650 ج | بوكسر وأنډرآرم: 750 ج | بوكسر وأنډرآرم وتحديد دقن: 1000 ج
- عصعاص: 750 ج | أذن: 250 ج | كتف/صدر/ظهر: 1000 ج
- جسم كامل: 5000 نبضة بـ 4000 ج / 6000 نبضة بـ 5000 ج
(عند طلب عروض الرجال بوضوح، أضف: [IMAGE: men_offers])

=== 🤖 طلبات الحجز ===
- ممنوع تماماً تأكيد المواعيد في الجدول من نفسك.
- لو العميل طلب يحجز، اطلب منه بلطف: (الاسم بالكامل، رقم الموبايل، والفرع الأقرب ليه).
- أول ما يكتب البيانات دي، قوله إن الاستقبال هيكلمه فوراً لتأكيد الميعاد، وضيف في آخر رسالتك الكود:
  [NOTIFY: الاسم، رقم الهاتف، الفرع]
"""

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
    reply_text = get_ai_reply(sender_id, message_text)

    # 1. Find ALL requested images in the response
    image_tags = re.findall(r'\[IMAGE:(.*?)\]', reply_text)
    
    # 2. Check if there's a booking notification
    notify_match = re.search(r'\[NOTIFY:(.*?)\]', reply_text)
    patient_details = None
    if notify_match:
        patient_details = notify_match.group(1).strip()

    # 3. Clean the AI's text so the user never sees any secret tags
    clean_text = re.sub(r'\[IMAGE:.*?\]', '', reply_text)
    clean_text = re.sub(r'\[NOTIFY:.*?\]', '', clean_text).strip()

    # 4. Send the text message first (if there is one)
    if clean_text:
        await send_text_reply(sender_id, clean_text)

    # 5. Loop through and send every image the AI requested
    for img in image_tags:
        image_url = f"{BASE_URL}/images/{img.strip()}.jpg"
        await send_image_reply(sender_id, image_url)

    # 6. Trigger the staff alert if booking details exist
    if patient_details:
        print(f"🚨 NEW BOOKING REQUEST: {patient_details}")

def get_ai_reply(sender_id: str, user_text: str) -> str:
    # Maintain simple stateful conversation per sender ID
    if sender_id not in CONVERSATION_HISTORY:
        CONVERSATION_HISTORY[sender_id] = []

    # Keep conversation history context reasonable (last 10 turns)
    CONVERSATION_HISTORY[sender_id] = CONVERSATION_HISTORY[sender_id][-10:]
    CONVERSATION_HISTORY[sender_id].append({"role": "user", "parts": [{"text": user_text}]})

    try:
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=CONVERSATION_HISTORY[sender_id],
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_INSTRUCTION,
                temperature=0.3
            )
        )
        
        reply = response.text
        CONVERSATION_HISTORY[sender_id].append({"role": "model", "parts": [{"text": reply}]})
        return reply

    except Exception as e:
        print(f"Error calling Gemini: {e}")
        return "أهلاً بيك في جوتن! ثواني وفريق الاستقبال هيكون معاك ويرد على كل استفساراتك."

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
        
        # Log exact error payload from Meta if 400 occurs
        if response.is_error:
            print(f"❌ Meta API Error ({response.status_code}): {response.text}")
            
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
        
        if response.is_error:
            print(f"❌ Meta API Error ({response.status_code}): {response.text}")
            
        response.raise_for_status()
