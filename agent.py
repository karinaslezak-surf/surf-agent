import os
import requests
import google.generativeai as genai
from sqlalchemy import create_engine, Column, Integer, String, Float
from sqlalchemy.orm import declarative_base, sessionmaker
from datetime import datetime, timedelta

# 1. Load All Keys
DB_URL = os.environ.get("DATABASE_URL")
if DB_URL and DB_URL.startswith("postgres://"):
    DB_URL = DB_URL.replace("postgres://", "postgresql://", 1)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

# 2. Database Setup
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

# 3. Setup Gemini AI
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)
    # Using the flash model because it is lightning fast and free!
    model = genai.GenerativeModel('gemini-1.5-flash')

def get_ai_surf_message(spot_name, target_date, forecast_flow):
    fallback_msg = (f"🏄‍♂️ SURF ALERT!\n\n*{spot_name}* is looking PERFECT in 2 days!\n"
                    f"Date: {target_date}\nForecast: {forecast_flow} m³/s\n\nPack your gear!")
    
    if not GEMINI_API_KEY:
        return fallback_msg
        
    try:
        # Here is where we instruct Gemini how to act!
        prompt = (f"Act as a stoked, highly energetic river surfer. Write a short, funny, "
                  f"and exciting text message (under 3 sentences) to my friends telling them the river wave "
                  f"at {spot_name} is going to be pumping in 2 days ({target_date}). "
                  f"The water flow forecast is {forecast_flow} m³/s. Include some surf slang and emojis, "
                  f"but make sure the spot name, date, and flow numbers are very clear. Do not use hashtags.")
        
        response = model.generate_content(prompt)
        return response.text.strip()
    except Exception as e:
        print(f"Gemini error: {e}")
        return fallback_msg

def send_telegram_message(chat_id, text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    requests.post(url, json={"chat_id": chat_id, "text": text})

def run_agent():
    if not DB_URL or not TELEGRAM_TOKEN:
        print("Missing Database or Telegram Keys!")
        return

    Session = sessionmaker(bind=engine)
    session = Session()
    spots = session.query(Spot).all()
    users = session.query(User).all()
    
    if not users:
        print("No users subscribed yet.")
        return

    target_date = (datetime.utcnow() + timedelta(days=2)).strftime("%Y-%m-%d")

    for spot in spots:
        api_url = f"https://flood-api.open-meteo.com/v1/flood?latitude={spot.latitude}&longitude={spot.longitude}&daily=river_discharge"
        try:
            response = requests.get(api_url)
            if response.status_code == 200:
                data = response.json()
                dates = data['daily']['time']
                discharges = data['daily']['river_discharge']
                
                if target_date in dates:
                    index = dates.index(target_date)
                    forecast_flow = discharges[index]
                    
                    if forecast_flow is not None and spot.min_flow <= forecast_flow <= spot.max_flow:
                        
                        # Generate the AI message!
                        msg = get_ai_surf_message(spot.name, target_date, forecast_flow)
                        
                        for user in users:
                            send_telegram_message(user.telegram_chat_id, msg)
        except Exception as e:
            print(f"Error checking {spot.name}: {e}")
            pass
    session.close()

if __name__ == "__main__":
    run_agent()
