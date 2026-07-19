import os
import requests
from sqlalchemy import create_engine, Column, Integer, String, Float
from sqlalchemy.orm import declarative_base, sessionmaker
from datetime import datetime, timedelta

DB_URL = os.environ.get("DATABASE_URL")
if DB_URL and DB_URL.startswith("postgres://"):
    DB_URL = DB_URL.replace("postgres://", "postgresql://", 1)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")

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

def send_telegram_message(chat_id, text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    requests.post(url, json={"chat_id": chat_id, "text": text})

def run_agent():
    if not DB_URL or not TELEGRAM_TOKEN:
        print("Missing API Keys!")
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
                        msg = (f"🏄‍♂️ SURF ALERT!\n\n*{spot.name}* is looking PERFECT in 2 days!\n"
                               f"Date: {target_date}\nForecast: {forecast_flow} m³/s\n\nPack your gear!")
                        for user in users:
                            send_telegram_message(user.telegram_chat_id, msg)
        except Exception as e:
            print(f"Error checking {spot.name}: {e}")
            pass
    session.close()

if __name__ == "__main__":
    run_agent()
