import os
import logging
import hashlib

import pandas as pd
import plotly.express as px
import streamlit as st

from src.analytics.rds_client import rds_analytics

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Authentication Helper
# ---------------------------------------------------------------------------
def hash_password(password: str) -> str:
    """Hash a password for database comparison."""
    return hashlib.sha256(password.encode()).hexdigest()

def check_login(username, password):
    """Verify credentials against RDS users table."""
    conn = rds_analytics.get_connection()
    if conn is None:
        st.error("Cannot connect to database for authentication.")
        return None
    
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT hospital_id, is_admin FROM users WHERE username = %s AND password_hash = %s",
                (username, hash_password(password))
            )
            result = cur.fetchone()
            return result # (hospital_id, is_admin) or None
    except Exception as e:
        logger.error(f"Login failure: {e}")
        return None
    finally:
        conn.close()

# ---------------------------------------------------------------------------
# Page configuration
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="InDiiServe Hospital Login",
    page_icon="🏥",
    layout="wide",
)

# Initialize Session State
if "authenticated" not in st.session_state:
    st.session_state.authenticated = False
if "hospital_id" not in st.session_state:
    st.session_state.hospital_id = None
if "is_admin" not in st.session_state:
    st.session_state.is_admin = False

# ---------------------------------------------------------------------------
# Login Portal
# ---------------------------------------------------------------------------
if not st.session_state.authenticated:
    st.markdown("""
        <style>
        .login-box {
            background-color: #f0f2f6;
            padding: 2rem;
            border-radius: 10px;
            max-width: 400px;
            margin: auto;
        }
        </style>
    """, unsafe_allow_html=True)
    
    st.title("🏥 InDiiServe Healthcare")
    st.subheader("Clinic Management Portal")

    with st.container():
        st.info("Please log in to access your clinic's analytics.")
        user = st.text_input("Username")
        pswd = st.text_input("Password", type="password")
        
        if st.button("Login"):
            login_data = check_login(user, pswd)
            if login_data:
                st.session_state.authenticated = True
                st.session_state.hospital_id = login_data[0]
                st.session_state.is_admin = login_data[1]
                st.success("Login successful! Redirecting...")
                st.rerun()
            else:
                st.error("Invalid username or password.")
    
    st.stop()

# ---------------------------------------------------------------------------
# Authenticated Sidebar
# ---------------------------------------------------------------------------
st.sidebar.title(f"🏥 {st.session_state.hospital_id.upper()}")
st.sidebar.markdown(f"**LoggedIn ID:** `{st.session_state.hospital_id}`")
if st.button("Logout"):
    st.session_state.authenticated = False
    st.rerun()

# ---------------------------------------------------------------------------
# Dashboard Logic (Now Tenant-Locked)
# ---------------------------------------------------------------------------
st.title(f"📈 Analytics: {st.session_state.hospital_id.replace('_', ' ').title()}")
st.markdown("Secure Hospital-Isolated Dashboard.")

@st.cache_data(ttl=120)
def load_analytics(hospital_id: str, days: int) -> pd.DataFrame:
    """Query live call data locked to a specific hospital_id."""
    conn = rds_analytics.get_connection()
    if conn is None:
        return pd.DataFrame()

    query = """
        SELECT
            session_id, phone_number, hospital_id, timestamp,
            sentiment, intent, department, outcome,
            duration_seconds, transcript_summary, is_successful_booking
        FROM hospital_analytics
        WHERE timestamp >= NOW() - (%s * INTERVAL '1 day')
          AND hospital_id = %s
        ORDER BY timestamp DESC
        LIMIT 2000;
    """
    try:
        df = pd.read_sql(query, conn, params=(days, hospital_id))
        conn.close()
        return df
    except Exception as e:
        logger.error("Dashboard query failed: %s", e)
        conn.close()
        return pd.DataFrame()

@st.cache_data(ttl=120)
def load_patient_history(phone: str, hospital_id: str) -> pd.DataFrame:
    """Load calls for a patient, restricted to this tenant."""
    conn = rds_analytics.get_connection()
    if conn is None:
        return pd.DataFrame()
    try:
        df = pd.read_sql(
            """
            SELECT timestamp, department, outcome, sentiment, transcript_summary
            FROM hospital_analytics
            WHERE phone_number = %s AND hospital_id = %s
            ORDER BY timestamp DESC LIMIT 50;
            """,
            conn,
            params=(phone, hospital_id),
        )
        conn.close()
        return df
    except Exception as e:
        logger.error("Patient history query failed: %s", e)
        conn.close()
        return pd.DataFrame()

days_filter = st.sidebar.slider("Date Range (Days)", 1, 90, 30)

# ---------------------------------------------------------------------------
# Main Logic
# ---------------------------------------------------------------------------
with st.spinner("Refining data..."):
    df = load_analytics(st.session_state.hospital_id, days_filter)

if df.empty:
    st.info(f"Welcome! No call logs found for '{st.session_state.hospital_id}' yet.")
    st.stop()

# --- KPI CARDS ---
total_calls = len(df)
bookings = int(df["is_successful_booking"].sum()) if "is_successful_booking" in df.columns else 0
avg_duration = int(df["duration_seconds"].mean()) if "duration_seconds" in df.columns else 0
positive_pct = round((df["sentiment"] == "Positive").sum() / total_calls * 100, 1) if total_calls > 0 else 0

c1, c2, c3, c4 = st.columns(4)
c1.metric("📞 Total Calls", f"{total_calls:,}")
c2.metric("✅ Bookings", f"{bookings:,}", f"{round(bookings/total_calls*100, 1)}%" if total_calls else "0%")
c3.metric("⏱️ Avg. Duration", f"{avg_duration // 60}m {avg_duration % 60}s")
c4.metric("😊 Positive %", f"{positive_pct}%")

st.markdown("---")

# --- CHARTS ---
ch1, ch2 = st.columns(2)
with ch1:
    df["date"] = pd.to_datetime(df["timestamp"]).dt.date
    fig_vol = px.line(df.groupby("date").size().reset_index(name="Calls"), x="date", y="Calls", title="Daily Volume")
    st.plotly_chart(fig_vol, use_container_width=True)
with ch2:
    fig_outcome = px.pie(df["outcome"].value_counts().reset_index(), names="Outcome", values="count", title="Outcomes")
    st.plotly_chart(fig_outcome, use_container_width=True)

# --- SEARCH ---
st.subheader("🔍 Local Patient Search")
search_phone = st.text_input("Search clinic history by phone")
if search_phone:
    hist = load_patient_history(search_phone, st.session_state.hospital_id)
    if not hist.empty:
        st.dataframe(hist, use_container_width=True)
    else:
        st.warning("No records found in your clinic.")

st.subheader("📋 Recent Clinic Calls")
st.dataframe(df.head(50), use_container_width=True)

st.caption(f"v4.0.0 | Tenant Locked: {st.session_state.hospital_id} | Production Hardened")
