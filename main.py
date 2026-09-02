import os
import re
import httpx
from fastapi import FastAPI, Request, Response, BackgroundTasks, HTTPException
from fastapi.staticfiles import StaticFiles
from google import genai

app = FastAPI()

os.makedirs("images", exist_ok=True)
app.mount("/images", StaticFiles(directory="images"), name="images")

# Existing Instagram & Gemini credentials
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "")
PAGE_ACCESS_TOKEN = os.getenv("PAGE_ACCESS_TOKEN", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

# New WhatsApp credentials
WA_PHONE_NUMBER_ID = os.getenv("WA_PHONE_NUMBER_ID", "")
WA_ACCESS_TOKEN = os.getenv("WA_ACCESS_TOKEN", "")
STAFF_PHONE_NUMBER = os.getenv("STAFF_PHONE_NUMBER", "")

client = genai.Client(api_key=GEMINI_API_KEY)
BASE_URL = "https://instagram-app-o2v3.onrender.com"

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

    # Trigger the WhatsApp alert
    if patient_details:
        print(f"🚨 NEW BOOKING REQUEST: {patient_details}")
        await send_whatsapp_alert(patient_details)

def get_ai_reply(user_text: str) -> str:
    # (Keep your massive system_instruction string here exactly as it was)
    system_instruction = """
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

    return "أهلاً بيك في جوتن! ثواني وفريق الاستقبال هيكون معاك ويرد على كل استفساراتك."

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
    """Sends a WhatsApp message to the clinic staff with the patient's details."""
    if not WA_PHONE_NUMBER_ID or not WA_ACCESS_TOKEN:
        print("Missing WhatsApp credentials. Alert not sent.")
        return

    url = f"https://graph.facebook.com/v21.0/{WA_PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {WA_ACCESS_TOKEN.strip()}",
        "Content-Type": "application/json"
    }
    
    message_body = f"🚨 *طلب حجز جديد من إنستجرام* 🚨\n\nالبيانات:\n{patient_details}"
    
    payload = {
        "messaging_product": "whatsapp",
        "to": STAFF_PHONE_NUMBER,
        "type": "text",
        "text": {"body": message_body}
    }
    
    async with httpx.AsyncClient() as http_client:
        response = await http_client.post(url, headers=headers, json=payload)
        print("WhatsApp Alert Status:", response.status_code)
