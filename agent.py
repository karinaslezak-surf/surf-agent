import os
import sys
import requests
import anthropic
from sqlalchemy import create_engine, Column, Integer, String, Float, or_
from sqlalchemy.orm import declarative_base, sessionmaker
from datetime import datetime, timedelta

DB_URL = os.environ.get("DATABASE_URL")
if DB_URL and DB_URL.startswith("postgres://"):
    DB_URL = DB_URL.replace("postgres://", "postgresql://", 1)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")

if not DB_URL:
    print("No database url found in environment, exiting agent")
    sys.exit(0)

engine = create_engine(DB_URL)
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
    owner_chat_id = Column(String, nullable=True)

class User(Base):
    __tablename__ = 'users'
    id = Column(Integer, primary_key=True)
    name = Column(String)
    telegram_chat_id = Column(String, unique=True)

claude_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY) if ANTHROPIC_API_KEY else None

def get_ai_surf_message(spot_name, target_date, forecast_flow, min_flow, max_flow):
    fallback_msg = (f"Hi, River Currentson here, surf alert 🌊\n\n"
                    f"🟢 {spot_name.title()} is looking perfect in 2 days\n"
                    f"Date: {target_date}\n"
                    f"Pack your gear")
                    
    raw_data_backup = f"\n\n---\n🌊 Raw station data:\n- {spot_name}: {forecast_flow} m³/s (ideal: {min_flow}-{max_flow})"
    
    if not claude_client:
        return fallback_msg + raw_data_backup
        
    prompt = (f"Act as River Currentson, a knowledgeable and laid-back river surf agent. Write exactly 1 or 2 short sentences (maximum 30 words total) to my friends "
              f"telling them the river wave at {spot_name} is pumping in 2 days ({target_date}). "
              f"Give a quick recommendation. Do not list the exact flow numbers, as the raw data is automatically attached below. "
              f"Use one dinosaur or surf emoji")
    
    models_to_try = [
        "claude-haiku-4-5-20251001",
        "claude-sonnet-4-6",
        "claude-3-5-haiku-20241022",
        "claude-3-5-sonnet-20241022"
    ]
    
    for m in models_to_try:
        try:
            response = claude_client.messages.create(
                model=m,
                max_tokens=150,
                temperature=0.7,
                messages=[{"role": "user", "content": prompt}]
            )
            return response.content[0].text.strip() + raw_data_backup
        except Exception as e:
            print(f"Claude error with model {m}: {e}")
            continue
            
    return fallback_msg + raw_data_backup

def send_telegram_message(chat_id, text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        requests.post(url, json={"chat_id": chat_id, "text": text})
    except Exception as e:
        print(f"Telegram message failed: {e}")

def run_agent():
    if not DB_URL or not TELEGRAM_TOKEN:
        return

    Session = sessionmaker(bind=engine)
    session = Session()
    
    try:
        spots = session.query(Spot).all()
        users = session.query(User).all()
        
        if not users:
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
                    msg = get_ai_surf_message(spot.name, target_date, round(best_flow, 1), spot.min_flow, spot.max_flow)
                    for user in users:
                        if not spot.owner_chat_id or spot.owner_chat_id == "" or spot.owner_chat_id == user.telegram_chat_id:
                            send_telegram_message(user.telegram_chat_id, msg)
            except Exception as e:
                print(f"Api fetch failed for {spot.name}: {e}")
                
    finally:
        session.close()

if __name__ == "__main__":
    run_agent()
