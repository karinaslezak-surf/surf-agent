import streamlit as st
from sqlalchemy import create_engine, Column, Integer, String, Float
from sqlalchemy.orm import declarative_base, sessionmaker
import pandas as pd
import threading
import requests
import telebot
import anthropic
from datetime import datetime, timedelta
import time
import os
import base64

st.set_page_config(page_title="River currentson", layout="wide")

DB_URL = st.secrets.get("DATABASE_URL", "")
if not DB_URL:
    st.error("⚠️ No database url found, please set your streamlit secrets")
    st.stop()

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
    station_id = Column(String)
    source = Column(String)
    min_flow = Column(Float)
    max_flow = Column(Float)

class User(Base):
    __tablename__ = 'users'
    id = Column(Integer, primary_key=True)
    name = Column(String)
    telegram_chat_id = Column(String, unique=True)

SessionLocal = sessionmaker(bind=engine)

def init_db():
    session = SessionLocal()
    try:
        session.query(Spot.station_id).first()
    except Exception:
        session.rollback()
        Spot.__table__.drop(engine, checkfirst=True)
    finally:
        session.close()

    Base.metadata.create_all(engine)
    session = SessionLocal()
    try:
        if session.query(Spot).count() == 0:
            default_spots = [
                Spot(name="bremgarten (reuss)", latitude=47.3557, longitude=8.3405, station_id="2018", source="bafu", min_flow=150.0, max_flow=300.0),
                Spot(name="thun - mühleschleuse (aare)", latitude=46.7578, longitude=7.6294, station_id="2135", source="bafu", min_flow=100.0, max_flow=250.0),
                Spot(name="thun - scherzligschleuse (aare)", latitude=46.7563, longitude=7.6333, station_id="2135", source="bafu", min_flow=100.0, max_flow=250.0),
                Spot(name="limmat", latitude=47.4049, longitude=8.4589, station_id="2243", source="bafu", min_flow=80.0, max_flow=150.0),
                Spot(name="the riverwave (ebensee)", latitude=47.8286, longitude=13.7548, station_id="16572", source="ehyd", min_flow=50.0, max_flow=150.0)
            ]
            session.add_all(default_spots)
            session.commit()
    finally:
        session.close()

init_db()

TELEGRAM_TOKEN = st.secrets.get("TELEGRAM_TOKEN", "").strip()
ANTHROPIC_API_KEY = st.secrets.get("ANTHROPIC_API_KEY", "").strip()

bot_username = ""
if TELEGRAM_TOKEN:
    try:
        bot_info = requests.get(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getMe", timeout=5).json()
        if bot_info.get("ok"):
            bot_username = bot_info["result"]["username"]
    except Exception:
        pass

claude_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY) if ANTHROPIC_API_KEY else None

def generate_ai_reply(prompt_text):
    if not claude_client: return None
    
    models_to_try = [
        "claude-haiku-4-5-20251001",
        "claude-sonnet-4-6",
        "claude-3-5-haiku-20241022",
        "claude-3-5-sonnet-20241022"
    ]
    
    last_error = ""
    for m in models_to_try:
        try:
            response = claude_client.messages.create(
                model=m,
                max_tokens=150,
                temperature=0.7,
                messages=[{"role": "user", "content": prompt_text}]
            )
            return response.content[0].text.strip()
        except Exception as e:
            last_error = str(e)
            continue
            
    raise Exception(f"All claude models failed, exact error: {last_error}")

@st.cache_resource
def start_chatbot(token):
    if not token:
        return
        
    try: requests.get(f"https://api.telegram.org/bot{token}/deleteWebhook?drop_pending_updates=True", timeout=5)
    except: pass

    bot = telebot.TeleBot(token)

    @bot.message_handler(commands=['start', 'help'])
    def send_welcome(message):
        try: bot.send_chat_action(message.chat.id, 'typing')
        except: pass
        bot.reply_to(message, "Yeww! 🤙 Hi, I'm River. River Currentson, your surf agent, text me anytime to check the waves")

    @bot.message_handler(func=lambda message: True)
    def handle_message(message):
        try: bot.send_chat_action(message.chat.id, 'typing')
        except: pass
        
        session = SessionLocal()
        chat_id = str(message.chat.id).strip()
        status_msg = None
        
        try:
            user = session.query(User).filter_by(telegram_chat_id=chat_id).first()
            
            if not user:
                bot.reply_to(message, f"🛑 Whoa there! Your chat ID is {chat_id}, but you aren't on the VIP list, subscribe on the app first")
                return

            status_msg = bot.reply_to(message, "🏄‍♂️ Paddling out to check the radar")

            spots = session.query(Spot).all()
            today_date = datetime.utcnow().strftime("%Y-%m-%d")
            target_date = (datetime.utcnow() + timedelta(days=2)).strftime("%Y-%m-%d")
            
            raw_data = ""
            backup_msg = "---\n🌊 Raw station data:\n\n"
            
            for spot in spots:
                best_today, best_future = -1, -1
                
                try:
                    if spot.source == "bafu":
                        bafu_url = f"https://api.existenz.ch/bafu/station/{spot.station_id}"
                        b_resp = requests.get(bafu_url, timeout=10).json()
                        if 'parameters' in b_resp and 'abfluss' in b_resp['parameters']:
                            best_today = float(b_resp['parameters']['abfluss']['value'])
                    elif spot.source == "ehyd":
                        pass
                except Exception as e:
                    print(f"Official sensor error for {spot.name}: {e}")

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
                    
                    om_today = -1
                    for loc in resp:
                        dates = loc.get('daily', {}).get('time', [])
                        discharges = loc.get('daily', {}).get('river_discharge', [])
                        if today_date in dates and target_date in dates:
                            try:
                                t_flow = discharges[dates.index(today_date)]
                                f_flow = discharges[dates.index(target_date)]
                                if t_flow is not None and t_flow > om_today:
                                    om_today = t_flow
                                if f_flow is not None and f_flow > best_future:
                                    best_future = f_flow
                            except ValueError:
                                pass
                                
                    if best_today == -1 and om_today != -1:
                        best_today = om_today
                        
                except Exception as api_err:
                    print(f"Open-meteo error for {spot.name}: {api_err}")

                t_str = round(best_today, 1) if best_today != -1 else "n/a"
                f_str = round(best_future, 1) if best_future != -1 else "n/a"
                
                raw_data += f"- {spot.name}: {t_str} m³/s today, {f_str} m³/s in 2 days (ideal is {spot.min_flow}-{spot.max_flow})\n"
                
                is_good = "🟢" if (best_future != -1 and spot.min_flow <= best_future <= spot.max_flow) else "🔴"
                backup_msg += f"{is_good} {spot.name}\nToday: {t_str} m³/s | In 2 days: {f_str} m³/s\n(Ideal: {spot.min_flow} to {spot.max_flow})\n\n"

            final_text = backup_msg
            if ANTHROPIC_API_KEY:
                prompt = (f"Act as River Currentson, a knowledgeable and laid-back river surf agent. A friend named '{user.name}' texted you: '{message.text}'\n\n"
                          f"Live river flow data:\n{raw_data}\n\n"
                          f"Write exactly 1 or 2 short sentences (maximum 30 words total). Give a quick summary of the overall conditions and one clear recommendation on where to surf. "
                          f"Do not list the flow numbers, as the raw data is attached below. Be helpful, and use one surf or dinosaur emoji")
                
                try:
                    ai_response = generate_ai_reply(prompt)
                    if ai_response: final_text = f"{ai_response}\n\n{backup_msg}"
                except Exception as ai_e:
                    safe_error = str(ai_e).replace('*', '').replace('_', '').replace('[', '').replace(']', '')
                    final_text = f"🤖 AI error: {safe_error[:150]}...\n\n{backup_msg}"
            
            try: bot.edit_message_text(chat_id=message.chat.id, message_id=status_msg.message_id, text=final_text)
            except: bot.reply_to(message, final_text)
            
        except Exception as e:
            print(f"Bot crash: {e}")
            if status_msg: 
                try: bot.edit_message_text(chat_id=message.chat.id, message_id=status_msg.message_id, text=f"🤕 Wipeout! {e}")
                except: pass
        finally:
            session.close()

    def run_polling():
        while True:
            try: bot.infinity_polling(skip_pending=False)
            except Exception as e: 
                print(f"Polling error: {e}")
                time.sleep(3)

    threading.Thread(target=run_polling, daemon=True).start()

start_chatbot(TELEGRAM_TOKEN)

# --- streamlit ui ---
img_path = "raptor1.png"
if os.path.exists(img_path):
    st.image(img_path, width=450)

st.title("Hi, I'm River Currentson, your surf agent")

st.write("I monitor the 48-hour forecasts and notify you when the local spots reach perfect flow")

session = SessionLocal()
try:
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
                    session.add(Spot(name=s_name, latitude=s_lat, longitude=s_lon, station_id="", source="open-meteo", min_flow=s_min, max_flow=s_max))
                    session.commit()
                    st.success(f"{s_name} added 🤫")
                    session.close()
                    st.rerun()
                except Exception:
                    session.rollback()
                    st.error("Spot already exists")

    with col2:
        st.subheader("📱 Get surf alerts")
        
        st.markdown(
            """
            **How to find your chat ID:**
            1. Open Telegram and search for [**@userinfobot**](https://t.me/userinfobot) (or click the link)
            2. Tap **Start**
            3. Copy the ID number it replies with and paste it below
            """
        )
        
        with st.form("add_user"):
            u_name = st.text_input("Your name")
            u_chat_id = st.text_input("Telegram chat ID", placeholder="e.g. 123456789")
            if st.form_submit_button("Subscribe") and u_name and u_chat_id:
                try:
                    session.add(User(name=u_name, telegram_chat_id=u_chat_id.strip()))
                    session.commit()
                    st.success("You are on the list, go to Telegram, search for our bot, and click start")
                    session.close()
                except Exception:
                    session.rollback()
                    st.error("Already subscribed")
                    
        if bot_username:
            st.info(f"💡 **Want instant updates?** Once you subscribe, you can click here to message [**@{bot_username}**](https://t.me/{bot_username}) anytime and ask 'how are the waves'")

finally:
    session.close()
