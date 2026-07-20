import streamlit as st
from sqlalchemy import create_engine, Column, Integer, String, Float
from sqlalchemy.orm import declarative_base, sessionmaker
import pandas as pd
import threading
import requests
import telebot
import google.generativeai as genai
from datetime import datetime, timedelta

st.set_page_config(page_title="River Surf Agent", page_icon="🏄‍♂️", layout="wide")

DB_URL = st.secrets["DATABASE_URL"]
if DB_URL.startswith("postgres://"):
    DB_URL = DB_URL.replace("postgres://", "postgresql://", 1)

engine = create_engine(DB_URL)
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

@st.cache_resource
def start_chatbot():
    if not TELEGRAM_TOKEN: return False
    bot = telebot.TeleBot(TELEGRAM_TOKEN)
    
    if GEMINI_API_KEY:
        genai.configure(api_key=GEMINI_API_KEY)
        model = genai.GenerativeModel('gemini-1.5-flash')
    else:
        model = None

    @bot.message_handler(func=lambda message: True)
    def handle_message(message):
        session = SessionLocal()
        user = session.query(User).filter_by(telegram_chat_id=str(message.chat.id)).first()
        if not user:
            bot.reply_to(message, "Whoa there! 🛑 You aren't on the VIP list. Subscribe on the app first!")
            session.close()
            return

        bot.send_chat_action(message.chat.id, 'typing')
        spots = session.query(Spot).all()
        today_date = datetime.utcnow().strftime("%Y-%m-%d")
        target_date = (datetime.utcnow() + timedelta(days=2)).strftime("%Y-%m-%d")
        
        raw_data = ""
        for spot in spots:
            # RADAR SWEEP FIX
            offsets = [0, 0.02, -0.02, 0.04, -0.04]
            lats, lons = [], []
            for d_lat in offsets:
                for d_lon in offsets:
                    lats.append(str(round(spot.latitude + d_lat, 4)))
                    lons.append(str(round(spot.longitude + d_lon, 4)))
            
            api_url = f"https://flood-api.open-meteo.com/v1/flood?latitude={','.join(lats)}&longitude={','.join(lons)}&daily=river_discharge"
            
            try:
                resp = requests.get(api_url).json()
                if not isinstance(resp, list):
                    if 'error' in resp:
                        continue
                    resp = [resp]
                
                best_today, best_future = -1, -1
                for loc in resp:
                    dates = loc.get('daily', {}).get('time', [])
                    discharges = loc.get('daily', {}).get('river_discharge', [])
                    if today_date in dates and target_date in dates:
                        try:
                            idx_today = dates.index(today_date)
                            idx_future = dates.index(target_date)
                            t_flow = discharges[idx_today]
                            f_flow = discharges[idx_future]
                            
                            # Find the massive river volume, ignore the dirt/rain runoff!
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

        if model:
            prompt = (f"You are a stoked river surfer AI assistant. A friend named '{user.name}' texted you: '{message.text}'\n\n"
                      f"Live river flow data:\n{raw_data}\n\n"
                      f"Reply naturally using this data. Use surf slang and emojis. Keep under 4 sentences.")
            try:
                bot.reply_to(message, model.generate_content(prompt).text.strip())
            except Exception:
                bot.reply_to(message, f"Brain glitch! Raw stats:\n{raw_data}")
        else:
            bot.reply_to(message, f"Raw stats:\n{raw_data}")

    def run_polling():
        try: bot.infinity_polling(skip_pending=True)
        except Exception: pass

    threading.Thread(target=run_polling, daemon=True).start()
    return True

start_chatbot()

st.title("🏄‍♂️ River Surf Agent")
st.write("This agent checks the 48-hour forecast and alerts you when spots are firing.")

session = SessionLocal()
spots = session.query(Spot).all()

if spots:
    st.map(pd.DataFrame([{"lat": s.latitude, "lon": s.longitude} for s in spots]))

col1, col2 = st.columns(2)
with col1:
    st.subheader("📍 Add a Secret Spot")
    with st.form("add_spot"):
        s_name = st.text_input("Spot Name")
        s_lat = st.number_input("Latitude", format="%.4f")
        s_lon = st.number_input("Longitude", format="%.4f")
        s_min = st.number_input("Min Flow (m³/s)", step=10.0, value=50.0)
        s_max = st.number_input("Max Flow (m³/s)", step=10.0, value=150.0)
        if st.form_submit_button("Save Spot") and s_name:
            try:
                session.add(Spot(name=s_name, latitude=s_lat, longitude=s_lon, min_flow=s_min, max_flow=s_max))
                session.commit()
                st.success(f"{s_name} added! 🤫")
                st.rerun()
            except Exception:
                session.rollback()
                st.error("Spot already exists.")

with col2:
    st.subheader("📱 Get Surf Alerts")
    with st.form("add_user"):
        u_name = st.text_input("Your Name")
        u_chat_id = st.text_input("Telegram Chat ID", placeholder="e.g. 123456789", help="1. Open Telegram\n2. Search @userinfobot\n3. Click Start\n4. Copy ID here.")
        if st.form_submit_button("Subscribe") and u_name and u_chat_id:
            try:
                session.add(User(name=u_name, telegram_chat_id=u_chat_id))
                session.commit()
                st.success("You are on the list! Go to Telegram, search for our bot, and click 'Start'.")
            except Exception:
                session.rollback()
                st.error("Already subscribed.")
session.close()
