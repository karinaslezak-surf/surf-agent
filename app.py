import streamlit as st
from sqlalchemy import create_engine, Column, Integer, String, Float
from sqlalchemy.orm import declarative_base, sessionmaker
import pandas as pd
import threading
import requests
import telebot
import google.generativeai as genai
from datetime import datetime, timedelta
import time
import os
import base64

st.set_page_config(page_title="River Currentson", page_icon="🦖", layout="wide")

DB_URL = st.secrets.get("DATABASE_URL", "")
if DB_URL.startswith("postgres://"):
    DB_URL = DB_URL.replace("postgres://", "postgresql://", 1)

engine = create_engine(DB_URL, pool_pre_ping=True)
Base = declarative_base()

class Spot(Base):
    __tablename__ = 'spots'
    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True)
    latitude = Column(Float)
    longitude = Column(Float)
    min_flow = Column(Float)
    max_flow = Column(Float)

class User(Base):
    __tablename__ = 'users'
    id = Column(Integer, primary_key=True)
    name = Column(String)
    telegram_chat_id = Column(String, unique=True)

SessionLocal = sessionmaker(bind=engine)

def init_db():
    Base.metadata.create_all(engine)
    session = SessionLocal()
    if session.query(Spot).count() == 0:
        default_spots = [
            Spot(name="Bremgarten (Reuss)", latitude=47.3513, longitude=8.3446, min_flow=150.0, max_flow=300.0),
            Spot(name="Limmat (Zurich)", latitude=47.3892, longitude=8.5137, min_flow=80.0, max_flow=150.0),
            Spot(name="Thun (Aare)", latitude=46.7579, longitude=7.6279, min_flow=100.0, max_flow=250.0),
            Spot(name="The Riverwave (Ebensee)", latitude=47.8112, longitude=13.7735, min_flow=50.0, max_flow=150.0)
        ]
        session.add_all(default_spots)
        session.commit()
    session.close()

init_db()

TELEGRAM_TOKEN = st.secrets.get("TELEGRAM_TOKEN")
GEMINI_API_KEY = st.secrets.get("GEMINI_API_KEY")

bot_username = ""
if TELEGRAM_TOKEN:
    try:
        bot_info = requests.get(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getMe", timeout=5).json()
        if bot_info.get("ok"):
            bot_username = bot_info["result"]["username"]
    except Exception:
        pass

if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

def generate_ai_reply(prompt_text):
    if not GEMINI_API_KEY: return None
    
    # FIX: We now strictly use the 2.0 model which we know your key has access to!
    safe_models = ['gemini-2.0-flash']
    
    safety_settings = [
        {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
    ]
    
    errors = []
    for m_name in safe_models:
        try:
            model = genai.GenerativeModel(m_name)
            response = model.generate_content(prompt_text, safety_settings=safety_settings)
            try:
                return response.text.strip()
            except ValueError:
                errors.append(f"[{m_name}: Blocked by safety filters]")
                continue
        except Exception as e:
            err_msg = str(e).lower()
            # SMART DETECTOR: If you hit the speed limit, stop instantly so you don't get blocked!
            if "429" in err_msg or "quota" in err_msg:
                raise Exception("Whoa! Google AI speed limit reached. Give me 60 seconds to catch my breath! 🏄‍♂️")
            
            err_short = str(e).split('\n')[0][:60]
            errors.append(f"[{m_name}: {err_short}]")
            continue 
            
    raise Exception(f"{' | '.join(errors)}")

@st.cache_resource
def start_chatbot():
    if not TELEGRAM_TOKEN:
        return
        
    bot = telebot.TeleBot(TELEGRAM_TOKEN)

    @bot.message_handler(commands=['start', 'help'])
    def send_welcome(message):
        bot.reply_to(message, "Hi, I'm River Currentson. Your personal AI surf agent. Text me anytime to check the waves!")

    @bot.message_handler(func=lambda message: True)
    def handle_message(message):
        try:
            bot.send_chat_action(message.chat.id, 'typing')
            
            session = SessionLocal()
            chat_id = str(message.chat.id).strip()
            user = session.query(User).filter_by(telegram_chat_id=chat_id).first()
            
            if not user:
                bot.reply_to(message, f"🛑 Whoa there! Your chat ID is {chat_id}, but you aren't on the VIP list. Subscribe on the app first!")
                session.close()
                return

            spots = session.query(Spot).all()
            today_date = datetime.utcnow().strftime("%Y-%m-%d")
            target_date = (datetime.utcnow() + timedelta(days=2)).strftime("%Y-%m-%d")
            
            raw_data = ""
            for spot in spots:
                offsets = [0, 0.02, -0.02, 0.04, -0.04]
                lats, lons = [], []
                for d_lat in offsets:
                    for d_lon in offsets:
                        lats.append(str(round(spot.latitude + d_lat, 4)))
                        lons.append(str(round(spot.longitude + d_lon, 4)))
                
                api_url = f"https://flood-api.open-meteo.com/v1/flood?latitude={','.join(lats)}&longitude={','.join(lons)}&daily=river_discharge"
                
                try:
                    resp = requests.get(api_url, timeout=10).json()
                    if not isinstance(resp, list):
                        if 'error' in resp: continue
                        resp = [resp]
                    
                    best_today, best_future = -1, -1
                    for loc in resp:
                        dates = loc.get('daily', {}).get('time', [])
                        discharges = loc.get('daily', {}).get('river_discharge', [])
                        if today_date in dates and target_date in dates:
                            try:
                                t_flow = discharges[dates.index(today_date)]
                                f_flow = discharges[dates.index(target_date)]
                                if t_flow is not None and t_flow > best_today:
                                    best_today = t_flow
                                    best_future = f_flow if f_flow is not None else 0
                            except ValueError:
                                pass
                    
                    t_str = round(best_today, 1) if best_today != -1 else "N/A"
                    f_str = round(best_future, 1) if best_future != -1 else "N/A"
                    raw_data += f"- {spot.name}: {t_str} m³/s today, {f_str} m³/s in 2 days. (Ideal is {spot.min_flow}-{spot.max_flow})\n"
                except Exception:
                    pass
            session.close()

            if GEMINI_API_KEY:
                prompt = (f"Act as River Currentson, a knowledgeable and laid-back river surf agent. A friend named '{user.name}' texted you: '{message.text}'\n\n"
                          f"Live river flow data:\n{raw_data}\n\n"
                          f"Reply naturally using this data. Be helpful and reliable. Use a surf or dinosaur emoji occasionally. Keep it under 4 sentences.")
                
                try:
                    ai_response = generate_ai_reply(prompt)
                    bot.reply_to(message, ai_response)
                except Exception as ai_e:
                    # Special catch for our speed limit message!
                    if "speed limit" in str(ai_e):
                        bot.reply_to(message, str(ai_e))
                    else:
                        bot.reply_to(message, f"Raw stats (AI Error: {str(ai_e)}):\n{raw_data}")
            else:
                bot.reply_to(message, f"Raw stats:\n{raw_data}")
        
        except Exception as e:
            try: bot.reply_to(message, f"🤖 Oops! Core bot glitch! Error: {str(e)}")
            except: pass

    def run_polling():
        try: bot.remove_webhook() 
        except Exception: pass
            
        while True:
            try: bot.infinity_polling(skip_pending=True)
            except Exception: time.sleep(3)

    threading.Thread(target=run_polling, daemon=True).start()

start_chatbot()

# --- STREAMLIT UI ---
if os.path.exists("trex.png"):
    with open("trex.png", "rb") as image_file:
        encoded_string = base64.b64encode(image_file.read()).decode()
    st.markdown(
        f'''
        <div style="display: flex; align-items: center; margin-bottom: 20px;">
            <img src="data:image/png;base64,{encoded_string}" style="height: 2.2rem; width: auto; margin-right: 15px;">
            <h1 style="margin: 0; padding: 0;">Hi, I'm River Currentson, your surf agent.</h1>
        </div>
        ''', 
        unsafe_allow_html=True
    )
else:
    st.title("🦖 Hi, I'm River Currentson, your surf agent.")

st.write("I monitor the 48-hour forecasts and notify you when the local spots reach perfect flow.")

session = SessionLocal()
spots = session.query(Spot).all()

if spots:
    st.map(pd.DataFrame([{"lat": s.latitude, "lon": s.longitude} for s in spots]))

col1, col2 = st.columns(2)
with col1:
    st.subheader("📍 Add a secret spot")
    with st.form("add_spot"):
        s_name = st.text_input("Spot name")
        s_lat = st.number_input("Latitude", format="%.4f")
        s_lon = st.number_input("Longitude", format="%.4f")
        s_min = st.number_input("Min flow (m³/s)", step=10.0, value=50.0)
        s_max = st.number_input("Max flow (m³/s)", step=10.0, value=150.0)
        if st.form_submit_button("Save spot") and s_name:
            try:
                session.add(Spot(name=s_name, latitude=s_lat, longitude=s_lon, min_flow=s_min, max_flow=s_max))
                session.commit()
                st.success(f"{s_name} added! 🤫")
                st.rerun()
            except Exception:
                session.rollback()
                st.error("Spot already exists.")

with col2:
    st.subheader("📱 Get surf alerts")
    
    st.markdown(
        """
        **How to find your Chat ID:**
        1. Open Telegram and search for [**@userinfobot**](https://t.me/userinfobot) (or click the link).
        2. Tap **Start**.
        3. Copy the ID number it replies with and paste it below!
        """
    )
    
    with st.form("add_user"):
        u_name = st.text_input("Your name")
        u_chat_id = st.text_input("Telegram chat ID", placeholder="e.g. 123456789")
        if st.form_submit_button("Subscribe") and u_name and u_chat_id:
            try:
                session.add(User(name=u_name, telegram_chat_id=u_chat_id.strip()))
                session.commit()
                st.success("You are on the list! Go to Telegram, search for our bot, and click 'Start'.")
            except Exception:
                session.rollback()
                st.error("Already subscribed.")
                
    if bot_username:
        st.info(f"💡 **Want instant updates?** Once you subscribe, you can click here to message [**@{bot_username}**](https://t.me/{bot_username}) anytime and ask *'How are the waves?'*")
    else:
        st.info("💡 **Want instant updates?** Once you subscribe, you can message the bot on Telegram anytime and ask *'How are the waves?'*")

session.close()
