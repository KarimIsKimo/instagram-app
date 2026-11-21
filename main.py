import os
import re
import json
import httpx
import time
import asyncio
import hashlib
import unicodedata
from collections import OrderedDict, deque
from dataclasses import dataclass, field
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

BLACK_FRIDAY_ENABLED = os.getenv("BLACK_FRIDAY_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}

BLACK_FRIDAY_OFFERS = """
Women's Black Friday offers only:
- Pulse packages: 1,000 pulses: 750 EGP; 2,500 pulses: 1,500 EGP; 5,000 pulses: 2,500 EGP; 6,000 pulses valid for 6 months: 3,000 EGP; additional pulse: 0.75 EGP.
- Half body: 1 session: 1,250 EGP; 4 sessions: 4,500 EGP.
- Full body (no abdomen or back): 1 session: 2,000 EGP; 4 sessions: 7,000 EGP.
- Full body with abdomen and back: 1 session: 2,500 EGP; 4 sessions: 8,500 EGP.
- Full body with face and neck: 1 session: 3,000 EGP; 4 sessions: 10,000 EGP.
""".strip()

client = genai.Client(api_key=GEMINI_API_KEY)

user_chats = {}
processed_mids = OrderedDict()
user_profiles = {}  # Cache to store user names so we only fetch them once

SYSTEM_INSTRUCTION = """
You are a friendly and professional receptionist at "عيادات جوثن" (Jothen Clinics) on Instagram maintain professional and respectful manner with customers.
our machines are Candela Gentlelase Pro u and Deka Motus AY

=== ⏰ TIMEZONE ===
- Timezone: Egypt Local Time (Africa/Cairo).

=== 💰 PRICING MENU ===
Use this exact data to answer specific price inquiries accurately. 
CRITICAL RULE: NEVER dump this entire list to a user. Just quote the specific price they asked for, keep the response short, and let the appended image do the rest.
Men DO NOT buy pulses, they only buy sessions or by area, so if patient is a man don't provide packages or female offers
Silicone skin protector is 100 egp while distance guage (مبعد) is 600 egp

=== 🖤 BLACK FRIDAY CAMPAIGN RULES ===
- The live Black Friday status is supplied with every customer message. Follow that status exactly.
- Only use Black Friday prices when the live status says the campaign is active. Otherwise, use the normal pricing menu only.
- Black Friday offers are for women only. Do not offer Black Friday pulse packages or women's Black Friday offers to men.
- When an active Black Friday offer is relevant, clearly describe it as a limited-time Black Friday offer and encourage booking while the campaign is active.
- Never invent or imply a quantity, limited stock, remaining places, limited slots, or that an offer may run out. Do not call a Black Friday price permanent or regular.

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
6. When Black Friday is active and the customer asks about Black Friday pulse packages or an additional pulse: -> [IMAGE: black_friday_pulses]
7. When Black Friday is active and the customer asks about one of these four Black Friday packages—Half Body, Full Body without Abdomen/Back, Full Body with Abdomen/Back, or Full Body with Face/Neck—or its 4-session package: -> [IMAGE: black_friday_body]
- Use black_friday_body only for those four Black Friday packages. For any other area, use women_areas as normal.

=== 🧠 Conversational Flow & Memory ===
- NEVER repeat greetings or re-introduce yourself.
- If the user says "let me check" or "I will confirm with you", respond warmly: "تمام تحت أمرك، وقت ما تحب تنورنا."
