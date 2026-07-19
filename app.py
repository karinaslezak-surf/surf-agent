import streamlit as st
from sqlalchemy import create_engine, Column, Integer, String, Float
from sqlalchemy.orm import declarative_base, sessionmaker
import pandas as pd

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

st.title("🏄‍♂️ River Currentson")
st.write("This agent checks the 48-hour forecast and alerts you when river surf spots are firing")

session = SessionLocal()

spots = session.query(Spot).all()
if spots:
    map_data = pd.DataFrame([{"lat": s.latitude, "lon": s.longitude} for s in spots])
    st.map(map_data)

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
                st.success(f"{s_name} added secretly! 🤫")
                st.rerun()
            except:
                session.rollback()
                st.error("Spot already exists.")

with col2:
    st.subheader("🚨 Get surf alerts")
    st.markdown("Message **@userinfobot** on Telegram to get your Chat ID")
    with st.form("add_user"):
        u_name = st.text_input("Your name")
        u_chat_id = st.text_input("Telegram Chat ID")
        if st.form_submit_button("Subscribe") and u_name and u_chat_id:
            try:
                session.add(User(name=u_name, telegram_chat_id=u_chat_id))
                session.commit()
                st.success("You are on the alert list!")
            except:
                session.rollback()
                st.error("Already subscribed.")
session.close()
