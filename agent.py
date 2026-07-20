import os
import requests
import google.generativeai as genai
from sqlalchemy import create_engine, Column, Integer, String, Float
from sqlalchemy.orm import declarative_base, sessionmaker
from datetime import datetime, timedelta

DB_URL = os.environ.get("DATABASE_URL")
if DB_URL and DB_URL.startswith("postgres://"):
    DB_URL = DB_URL.replace("postgres://", "postgresql://", 1)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

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

if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

def get_ai_surf_message(spot_name, target_date, forecast_flow):
    fallback_msg = (f"🦖 River Currentson here! Surf alert!\n\n*{spot_name}* is looking perfect in 2 days!\n"
                    f"Date: {target_date}\nForecast: {forecast_flow} m³/s\n\nPack your gear!")
    
    if not GEMINI_API_KEY:
        return fallback_msg
        
    prompt = (f"Act as River Currentson, a knowledgeable and laid-back river surf agent. Write a short text (under 3 sentences) to my friends "
              f"telling them the river wave at {spot_name} is pumping in 2 days ({target_date}). "
              f"The water flow forecast is {forecast_flow} m³/s. Be friendly and natural, use a dinosaur or surf emoji. No hashtags.")
    
    # Disable safety filters here too!
    safety_settings = [
        {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
    ]
    
    models = ['gemini-1.5-flash', 'gemini-1.5-flash-8b', 'gemini-1.5-pro']
    
    for m in models:
        try:
            model = genai.GenerativeModel(m)
            return model.generate_content(prompt, safety_settings=safety_settings).text.strip()
        except Exception:
            continue
            
    return fallback_msg

def send_telegram_message(chat_id, text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    requests.post(url, json={"chat_id": chat_id, "text": text})

def run_agent():
    if not DB_URL or not TELEGRAM_TOKEN:
        return

    Session = sessionmaker(bind=engine)
    session = Session()
    spots = session.query(Spot).all()
    users = session.query(User).all()
    
    if not users:
        session.close()
        return

    target_date = (datetime.utcnow() + timedelta(days=2)).strftime("%Y-%m-%d")

    for spot in spots:
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
                
            best_flow = -1
            
            for loc in resp:
                dates = loc.get('daily', {}).get('time', [])
                discharges = loc.get('daily', {}).get('river_discharge', [])
                if target_date in dates:
                    try:
                        idx = dates.index(target_date)
                        f_flow = discharges[idx]
                        if f_flow is not None and f_flow > best_flow:
                            best_flow = f_flow
                    except ValueError:
                        pass
                        
            if best_flow != -1 and spot.min_flow <= best_flow <= spot.max_flow:
                msg = get_ai_surf_message(spot.name, target_date, round(best_flow, 1))
                for user in users:
                    send_telegram_message(user.telegram_chat_id, msg)
        except Exception:
            pass
            
    session.close()

if __name__ == "__main__":
    run_agent()
