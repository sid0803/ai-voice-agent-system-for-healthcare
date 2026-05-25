import json
import logging
import bcrypt

import pandas as pd
import plotly.express as px
import streamlit as st

from src.analytics.dynamodb_client import dynamodb_analytics
from src.diagnostics.health import HealthChecker

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Authentication Helper
# ---------------------------------------------------------------------------
def hash_password(password: str) -> str:
    """Hash a password using bcrypt with a salt."""
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(password.encode(), salt).decode()

def check_login(username, password):
    """Verify credentials against DynamoDB users table."""
    user = dynamodb_analytics.get_user(username)
    if not user:
        return None
    
    stored_hash = user.get("password_hash")
    hospital_id = user.get("hospital_id")
    role = user.get("role", "staff")
    
    if len(stored_hash) == 64:  # Old SHA-256 length migration
        import hashlib
        old_hash = hashlib.sha256(password.encode()).hexdigest()
        if old_hash == stored_hash:
            # Migrate to bcrypt
            new_hash = hash_password(password)
            dynamodb_analytics.save_user(username, new_hash, hospital_id, role)
            logger.info(f"User {username} migrated to bcrypt in DynamoDB.")
            return {"hospital_id": hospital_id, "role": role}
    elif bcrypt.checkpw(password.encode(), stored_hash.encode()):
        return {"hospital_id": hospital_id, "role": role}
    return None

def register_hospital(name, hospital_id, admin_user, admin_pass):
    """Create a new pending tenant and admin account in DynamoDB."""
    from datetime import datetime, timezone, timedelta
    IST = timezone(timedelta(hours=5, minutes=30))
    
    # 1. Pre-flight check
    if dynamodb_analytics.get_tenant(hospital_id):
        logger.warning(f"Registration aborted: Hospital ID {hospital_id} already exists.")
        return False
    if dynamodb_analytics.get_user(admin_user):
        logger.warning(f"Registration aborted: Username {admin_user} already exists.")
        return False

    # 2. Create Tenant
    tenant_data = {
        "hospital_id": hospital_id,
        "hospital_name": name,
        "status": "pending",
        "ingestion_strategy": "hybrid",
        "sync_interval_mins": 10,
        "created_at": datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S IST")
    }
    
    # 3. Create User
    hashed = hash_password(admin_pass)
    
    ok1 = dynamodb_analytics.save_tenant(tenant_data)
    ok2 = dynamodb_analytics.save_user(admin_user, hashed, hospital_id, "admin")
    return ok1 and ok2

@st.cache_data(ttl=60)
def load_analytics(hospital_id: str, days: int = 30):
    """Fetch call metadata for the dashboard from DynamoDB."""
    try:
        items = dynamodb_analytics.load_analytics(hospital_id, days)
        if not items:
            return pd.DataFrame()
        
        df = pd.DataFrame(items)
        if 'timestamp' in df.columns:
            df['timestamp'] = pd.to_datetime(df['timestamp'], errors='coerce')
        return df
    except Exception as e:
        logger.error(f"Failed to load analytics: {e}")
        return pd.DataFrame()

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
if "role" not in st.session_state:
    st.session_state.role = "staff"

# ---------------------------------------------------------------------------
# Login Portal
# ---------------------------------------------------------------------------
if not st.session_state.authenticated:
    st.markdown("""
        <style>
        /* Base Page Styling */
        .main { background-color: #F8FAFC; }
        
        /* Bento Grid Card Styling */
        .bento-card {
            background-color: white;
            padding: 1.5rem;
            border-radius: 16px;
            border: 1px solid #E2E8F0;
            box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.05), 0 2px 4px -1px rgba(0, 0, 0, 0.03);
            margin-bottom: 1rem;
            transition: transform 0.2s ease-in-out;
        }
        .bento-card:hover { 
            transform: translateY(-2px); 
            box-shadow: 0 10px 15px -3px rgba(0, 0, 0, 0.1);
        }
        
        /* Metric Styling */
        .metric-label { color: #64748B; font-weight: 500; font-size: 0.875rem; }
        .metric-value { color: #1E293B; font-weight: 700; font-size: 1.5rem; }
        
        /* AI Insight Banner */
        .insight-banner {
            background: linear-gradient(90deg, #EFF6FF 0%, #DBEAFE 100%);
            border-left: 4px solid #3B82F6;
            padding: 1rem;
            border-radius: 8px;
            margin-bottom: 2rem;
        }
        
        /* Emergency Alert */
        .emergency-pill {
            background-color: #FEE2E2;
            color: #DC2626;
            padding: 0.25rem 0.75rem;
            border-radius: 9999px;
            font-size: 0.75rem;
            font-weight: 600;
        }
        
        /* Sidebar Polish */
        [data-testid="stSidebar"] {
            background-color: #FFFFFF;
            border-right: 1px solid #E2E8F0;
        }
        </style>
    """, unsafe_allow_html=True)
    
    st.title("🏥 InDiiServe Healthcare")
    st.subheader("Autonomous Clinical Intelligence System")

    tab_login, tab_signup = st.tabs(["Login", "Clinic Sign-up"])

    with tab_login:
        st.info("Log in to manage your facility.")
        user = st.text_input("Username", key="login_user")
        pswd = st.text_input("Password", type="password", key="login_pass")
        
        if st.button("Login"):
            res = check_login(user, pswd)
            if res:
                st.session_state.authenticated = True
                st.session_state.hospital_id = res["hospital_id"]
                st.session_state.role = res["role"]
                st.success("Access granted.")
                st.rerun()
            else:
                st.error("Invalid credentials.")

    with tab_signup:
        st.info("Onboard your nursing home or clinic today.")
        with st.form("signup_form"):
            h_name = st.text_input("Hospital Name", placeholder="e.g. Apollo Hub")
            h_id = st.text_input("Requested Facility ID (slug)", placeholder="e.g. apollo_hub")
            reg_user = st.text_input("Admin Username")
            reg_pass = st.text_input("Admin Password", type="password")
            
            if st.form_submit_button("Register Facility"):
                if h_id and h_name and reg_user and reg_pass:
                    if register_hospital(h_name, h_id, reg_user, reg_pass):
                        st.success("Registration successful! Your clinic is now 'PENDING'. A Super-Admin will review your setup shortly.")
                    else:
                        st.error("Facility ID already exists or database error.")
                else:
                    st.warning("All fields are mandatory.")
    
    st.stop()

# ---------------------------------------------------------------------------
# Authenticated Sidebar
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Multi-Tier Sidebar & Navigation
# ---------------------------------------------------------------------------
st.sidebar.title("🛡️ Command Center")

# --- System Health Section ---
with st.sidebar.expander("🩺 System Infrastructure Health", expanded=True):
    if st.button("🔄 Refresh Diagnostics"):
        st.cache_data.clear()
        st.rerun()
        
    diag = HealthChecker.run_full_diagnostic()
    
    # Cloud Status
    aws_ok, aws_msg = diag["aws"]
    st.markdown(f"**AWS Cloud:** {'🟢 Online' if aws_ok else '🔴 Error'}")
    
    # DB Status
    db_ok, db_msg = diag["database"]
    st.markdown(f"**Analytics DB:** {'🟢' if db_ok else '🔴'} {db_msg}")
    
    # Assets Status
    assets_ok = all(diag["assets"].values())
    st.markdown(f"**Voice Assets:** {'🟢 Ready' if assets_ok else '⚠️ Missing'}")
    
    if not assets_ok:
        missing = [k for k,v in diag["assets"].items() if not v]
        st.caption(f"Missing: {', '.join(missing)}")

st.sidebar.markdown("---")

nav = st.sidebar.radio("Navigation", ["Analytics Dashboard", "Integration Settings", "Super-Admin Approval"] if st.session_state.role == 'super_admin' else ["Analytics Dashboard", "Integration Settings"])

st.sidebar.markdown("---")
st.sidebar.write(f"Logged in as: **{st.session_state.role.upper()}**")
if st.sidebar.button("Logout"):
    st.session_state.authenticated = False
    st.rerun()

# ---------------------------------------------------------------------------
# TAB 1: Analytics Dashboard
# ---------------------------------------------------------------------------
if nav == "Analytics Dashboard":
    # 🎨 Custom Bento Header
    col_t1, col_t2 = st.columns([3, 1])
    with col_t1:
        st.title(f"📈 {st.session_state.hospital_id.replace('_', ' ').title()} Command Center")
        st.caption("Autonomous Clinical Intelligence & Operational Triage")
    
    # --- Data Retrieval ---
    days_filter = st.sidebar.slider("Historical View (Days)", 1, 90, 7)
    with st.spinner("Decoding clinical signals..."):
        df = load_analytics(st.session_state.hospital_id, days_filter)
    
    # 🧠 SECTION: AI Command Center (Actionable Insights)
    def get_ai_insights(df):
        insights = []
        if not df.empty:
            now = pd.to_datetime('now')
            calls_today = len(df[pd.to_datetime(df['timestamp']).dt.date == now.date()])
            emergencies = len(df[df['is_emergency'] == True])
            booking_rate = round(df['is_successful_booking'].mean() * 100, 1)
            
            if calls_today > 10:
                insights.append(f"📈 **High Demand**: Calls are peaking. Consider scaling morning shifts.")
            if emergencies > 0:
                insights.append(f"⚠️ **Clinical Alert**: {emergencies} emergency cases detected. Review triage logs.")
            if booking_rate < 40:
                insights.append(f"💡 **Growth Insight**: Booking rate ({booking_rate}%) is below target. Review ASHA follow-up.")
        return insights[:3]

    if not df.empty:
        insights = get_ai_insights(df)
        if insights:
            st.markdown("<div class='insight-banner'>", unsafe_allow_html=True)
            cols = st.columns(len(insights))
            for i, ins in enumerate(insights):
                cols[i].markdown(ins)
            st.markdown("</div>", unsafe_allow_html=True)

    if not df.empty:
        # 🍱 SECTION: Bento Grid Metrics
        m1, m2, m3, m4 = st.columns(4)
        
        with m1:
            st.markdown(f"""<div class="bento-card"><div class="metric-label">📞 TOTAL CALLS</div><div class="metric-value">{len(df):,}</div></div>""", unsafe_allow_html=True)
        with m2:
            successful_pct = round(df['is_successful_booking'].mean() * 100, 1)
            st.markdown(f"""<div class="bento-card"><div class="metric-label">✅ SUCCESS RATE</div><div class="metric-value">{successful_pct}%</div></div>""", unsafe_allow_html=True)
        with m3:
            avg_sec = int(df['duration_seconds'].mean())
            st.markdown(f"""<div class="bento-card"><div class="metric-label">⏱️ AVG. DURATION</div><div class="metric-value">{avg_sec // 60}m {avg_sec % 60}s</div></div>""", unsafe_allow_html=True)
        with m4:
            pos_pct = round((df['sentiment'] == 'Positive').mean() * 100, 1)
            st.markdown(f"""<div class="bento-card"><div class="metric-label">😊 POSITIVE SENTIMENT</div><div class="metric-value">{pos_pct}%</div></div>""", unsafe_allow_html=True)

        # 📊 SECTION: Advanced Analytics (Funnel + Trends)
        st.markdown("---")
        col_f1, col_f2 = st.columns([2, 1])
        
        with col_f1:
            st.subheader("🔥 Conversion Funnel (Operational Intelligence)")
            # 5-Layer Funnel Logic
            total = len(df)
            engaged = len(df[df['duration_seconds'] > 10])
            inquiry = len(df[df['intent'].notna()])
            qualified = len(df[(df['urgency_score'] > 2) | (df['intent'].isin(['Appointment', 'Emergency']))])
            booking = int(df['is_successful_booking'].sum())
            
            import plotly.graph_objects as go
            fig_funnel = go.Figure(go.Funnel(
                y = ["Total Calls", "Engaged", "Inquiry", "Qualified", "Booking"],
                x = [total, engaged, inquiry, qualified, booking],
                textinfo = "value+percent initial",
                marker = {"color": ["#E2E8F0", "#CBD5E1", "#94A3B8", "#475569", "#1E293B"]}
            ))
            fig_funnel.update_layout(margin=dict(l=20, r=20, t=20, b=20), height=300)
            st.plotly_chart(fig_funnel, use_container_width=True)

        with col_f2:
            st.subheader("🚨 Live Clinical Pulse")
            emergencies = df[df['is_emergency'] == True].sort_values('timestamp', ascending=False).head(5)
            if not emergencies.empty:
                for _, row in emergencies.iterrows():
                    st.markdown(f"""
                        <div style="border-left: 3px solid #DC2626; padding-left: 10px; margin-bottom: 10px; background-color: #FFF5F5; padding: 10px; border-radius: 4px;">
                            <span class="emergency-pill">CRITICAL</span><br>
                            <b>{row['phone_number'][:5]}***</b><br>
                            <small>{row['transcript_summary'][:60]}...</small>
                        </div>
                    """, unsafe_allow_html=True)
            else:
                st.success("No active critical alerts in this period.")

        # 📞 SECTION: Call Playback & Insights
        st.markdown("---")
        st.subheader("🕵️ Deep-Dive: Call Backlogs & Transcripts")
        
        # Add Search/Filter for Logs
        search = st.text_input("🔍 Search Transcripts (e.g. 'chest pain', 'billing')")
        display_df = df
        if search:
            display_df = df[df['transcript_summary'].str.contains(search, case=False, na=False)]
            
        st.dataframe(
            display_df[['timestamp', 'phone_number', 'sentiment', 'intent', 'outcome', 'transcript_summary']],
            use_container_width=True,
            column_config={
                "timestamp": st.column_config.DatetimeColumn("Date/Time", format="D MMM, HH:mm"),
                "transcript_summary": st.column_config.TextColumn("AI Summary", width="large")
            }
        )
    else:
        st.info("No call logs found for this period.")

# ---------------------------------------------------------------------------
# TAB 2: Integratielif nav == "Integration Settings":
    st.title("⚙️ Data Sync & HIS Integration")
    st.markdown("Configure how Asha connects to your hospital's system.")
    
    tenant = dynamodb_analytics.get_tenant(st.session_state.hospital_id)
    
    if tenant:
        status = tenant.get("status", "pending")
        strategy = tenant.get("ingestion_strategy", "hybrid")
        config = tenant.get("ingestion_config", {})
        token = tenant.get("push_token", "")
        sheet_id = tenant.get("spreadsheet_id", "")
        
        st.write(f"Current Status: **{status.upper()}**")
        
        with st.form("integration_form"):
            new_strategy = st.selectbox("Ingestion Strategy", ["hybrid", "push", "pull"], index=["hybrid", "push", "pull"].index(strategy))
            new_sheet = st.text_input("Output Google Sheet ID", value=sheet_id or "")
            new_url = st.text_input("Pull Sync URL (REST API)", value=config.get("pull_url") if config else "")
            
            if st.form_submit_button("Update Configuration"):
                tenant["ingestion_strategy"] = new_strategy
                tenant["spreadsheet_id"] = new_sheet
                tenant["ingestion_config"] = {"pull_url": new_url}
                
                if dynamodb_analytics.save_tenant(tenant):
                    st.success("Configuration saved!")
                    st.rerun()
                else:
                    st.error("Failed to save configuration.")

        st.markdown("---")
        st.subheader("🔑 Real-time API Push")
        st.info("Use this token to push data from your HIS to our endpoint: `POST /tenant/sync`.")
        st.code(f"X-Push-Token: {token or 'N/A'}")

# ---------------------------------------------------------------------------
# TAB 3: Super-Admin (Approvals)
# ---------------------------------------------------------------------------
elif nav == "Super-Admin Approval":
    st.title("🛡️ Governance: Super-Admin Dashboard")
    st.markdown("Review and activate clinical facilities.")
    
    tenants = dynamodb_analytics.list_tenants()
    
    if tenants:
        # Sort tenants by created_at descending
        tenants.sort(key=lambda t: t.get("created_at", ""), reverse=True)
        
        for tenant in tenants:
            h_id = tenant["hospital_id"]
            h_name = tenant.get("hospital_name", "Unknown Hospital")
            status = tenant.get("status", "pending")
            
            with st.expander(f"{h_name} [{status.upper()}]"):
                col1, col2, col3 = st.columns(3)
                if status == 'pending':
                    if col1.button("Move to Sandbox", key=f"sb_{h_id}"):
                        import secrets
                        token = secrets.token_hex(32)
                        tenant["status"] = "sandbox"
                        tenant["push_token"] = token
                        dynamodb_analytics.save_tenant(tenant)
                        st.rerun()
                if status == 'sandbox':
                    if col2.button("Go Live", key=f"live_{h_id}"):
                        tenant["status"] = "live"
                        dynamodb_analytics.save_tenant(tenant)
                        st.rerun()
                if col3.button("Suspend", key=f"sus_{h_id}"):
                    tenant["status"] = "pending"
                    dynamodb_analytics.save_tenant(tenant)
                    st.rerun()
    else:
        st.info("No tenants registered.")

st.caption("Asha SaaS Engine v5.0.0 | Production-Ready RBAC | NIST Encryption Standard")
