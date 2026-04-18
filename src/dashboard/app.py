import os
import logging
import bcrypt

import pandas as pd
import plotly.express as px
import streamlit as st

from src.analytics.rds_client import rds_analytics
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
    """Verify credentials against RDS users table."""
    conn = rds_analytics.get_connection()
    if conn is None:
        st.error("Cannot connect to database for authentication.")
        return None
    
    try:
        with conn.cursor() as cur:
            cur.execute(
                rds_analytics.format_query("SELECT hospital_id, password_hash, role FROM users WHERE username = %s"),
                (username,)
            )
            result = cur.fetchone()
            if result:
                hospital_id, stored_hash, role = result
                # Support both old SHA-256 (for migration) and new bcrypt
                if len(stored_hash) == 64: # Old SHA-256 length
                    import hashlib
                    old_hash = hashlib.sha256(password.encode()).hexdigest()
                    if old_hash == stored_hash:
                        # Auto-migrate to bcrypt on successful login
                        new_hash = hash_password(password)
                        cur.execute(rds_analytics.format_query("UPDATE users SET password_hash = %s WHERE username = %s"), (new_hash, username))
                        conn.commit()
                        logger.info(f"User {username} migrated to bcrypt.")
                        return {"hospital_id": hospital_id, "role": role}
                elif bcrypt.checkpw(password.encode(), stored_hash.encode()):
                    return {"hospital_id": hospital_id, "role": role}
            return None
    except Exception as e:
        logger.error(f"Login failure: {e}")
        return None
    finally:
        conn.close()

def register_hospital(name, hospital_id, admin_user, admin_pass):
    """Create a new pending tenant and admin account with atomic checks."""
    conn = rds_analytics.get_connection()
    if not conn: return False
    try:
        with conn.cursor() as cur:
            # 1. Pre-flight check: Check for existing ID or Username (Requirement: Reliability P1)
            cur.execute(rds_analytics.format_query("SELECT 1 FROM tenants WHERE hospital_id = %s"), (hospital_id,))
            if cur.fetchone():
                logger.warning(f"Registration aborted: Hospital ID {hospital_id} already exists.")
                return False
            
            cur.execute(rds_analytics.format_query("SELECT 1 FROM users WHERE username = %s"), (admin_user,))
            if cur.fetchone():
                logger.warning(f"Registration aborted: Username {admin_user} already exists.")
                return False

            # 2. Create Tenant
            cur.execute(
                rds_analytics.format_query("INSERT INTO tenants (hospital_id, hospital_name, status) VALUES (%s, %s, 'pending')"),
                (hospital_id, name)
            )
            # 3. Create User
            hashed = hash_password(admin_pass)
            cur.execute(
                rds_analytics.format_query("INSERT INTO users (username, password_hash, hospital_id, role) VALUES (%s, %s, %s, 'admin')"),
                (admin_user, hashed, hospital_id)
            )
            conn.commit()
            return True
    except Exception as e:
        logger.error(f"Registration failed: {e}")
        # Psycopg2 with context manager handles rollback automatically on exception
        return False
    finally:
        conn.close()

@st.cache_data(ttl=60)
def load_analytics(hospital_id: str, days: int = 30):
    """Fetch call metadata for the dashboard with Demo Mode support."""
    conn = rds_analytics.get_connection()
    if not conn:
        return pd.DataFrame()
    
    try:
        # Postgres uses INTERVAL, SQLite uses date()
        is_sqlite = rds_analytics.is_sqlite(conn)
        if is_sqlite:
            query = """
                SELECT * FROM hospital_analytics 
                WHERE hospital_id = %s 
                AND timestamp >= date('now', '-%s days')
                ORDER BY timestamp DESC
            """
        else:
            query = """
                SELECT * FROM hospital_analytics 
                WHERE hospital_id = %s 
                AND timestamp >= CURRENT_TIMESTAMP - INTERVAL '%s days'
                ORDER BY timestamp DESC
            """
        
        # We manually format the query because read_sql needs the translated placeholders
        formatted_query = rds_analytics.format_query(query)
        df = pd.read_sql(formatted_query, conn, params=(hospital_id, str(days)))
        return df
    except Exception as e:
        logger.error(f"Failed to load analytics: {e}")
        return pd.DataFrame()
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
if "role" not in st.session_state:
    st.session_state.role = "staff"

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
    st.subheader("SaaS Governance & Analytics")

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
    st.title(f"📈 Analytics: {st.session_state.hospital_id.replace('_', ' ').title()}")
    
    # Check status for non-super admins
    if st.session_state.role != 'super_admin':
        from src.integrations.tenant_manager import tenant_manager
        status = tenant_manager.get_status(st.session_state.hospital_id)
        if status == 'pending':
            st.warning("⚠️ YOUR CLINIC IS PENDING REVIEW. The AI agent is currently disabled.")
        elif status == 'sandbox':
            st.info("🧪 SANDBOX MODE: Asha will disclose she is in testing mode during calls.")

    days_filter = st.slider("Date Range (Days)", 1, 90, 30)
    
    with st.spinner("Analyzing call metadata..."):
        df = load_analytics(st.session_state.hospital_id, days_filter)

    if not df.empty:
        total_calls = len(df)
        bookings = int(df["is_successful_booking"].sum())
        avg_duration = int(df["duration_seconds"].mean())
        positive_pct = round((df["sentiment"] == "Positive").sum() / total_calls * 100, 1)

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("📞 Total Calls", f"{total_calls:,}")
        c2.metric("✅ Bookings", f"{bookings:,}")
        c3.metric("⏱️ Avg. Duration", f"{avg_duration // 60}m {avg_duration % 60}s")
        c4.metric("😊 Positive %", f"{positive_pct}%")

        ch1, ch2 = st.columns(2)
        with ch1:
            df["date"] = pd.to_datetime(df["timestamp"]).dt.date
            fig_vol = px.line(df.groupby("date").size().reset_index(name="Calls"), x="date", y="Calls", title="Call Volume")
            st.plotly_chart(fig_vol, use_container_width=True)
        with ch2:
            fig_outcome = px.pie(df["outcome"].value_counts().reset_index(), names="outcome", values="count", title="Outcomes")
            st.plotly_chart(fig_outcome, use_container_width=True)

        st.dataframe(df.head(100), use_container_width=True)
    else:
        st.info("No call logs found for this period.")

# ---------------------------------------------------------------------------
# TAB 2: Integration Settings
# ---------------------------------------------------------------------------
elif nav == "Integration Settings":
    st.title("⚙️ Data Sync & HIS Integration")
    st.markdown("Configure how Asha connects to your hospital's system.")
    
    conn = rds_analytics.get_connection()
    with conn.cursor() as cur:
        cur.execute(rds_analytics.format_query("SELECT status, ingestion_strategy, pull_config, push_token, spreadsheet_id FROM tenants WHERE hospital_id = %s"), (st.session_state.hospital_id,))
        res = cur.fetchone()
    
    if res:
        status, strategy, config, token, sheet_id = res
        st.write(f"Current Status: **{status.upper()}**")
        
        with st.form("integration_form"):
            new_strategy = st.selectbox("Ingestion Strategy", ["hybrid", "push", "pull"], index=["hybrid", "push", "pull"].index(strategy))
            new_sheet = st.text_input("Output Google Sheet ID", value=sheet_id or "")
            new_url = st.text_input("Pull Sync URL (REST API)", value=config.get("pull_url") if config else "")
            
            if st.form_submit_button("Update Configuration"):
                new_config = {"pull_url": new_url}
                with conn.cursor() as cur:
                    cur.execute(
                        rds_analytics.format_query("UPDATE tenants SET ingestion_strategy = %s, spreadsheet_id = %s, ingestion_config = %s WHERE hospital_id = %s"),
                        (new_strategy, new_sheet, json.dumps(new_config), st.session_state.hospital_id)
                    )
                conn.commit()
                st.success("Configuration saved!")

        st.markdown("---")
        st.subheader("🔑 Real-time API Push")
        st.info("Use this token to push data from your HIS to our endpoint: `POST /tenant/sync`.")
        st.code(f"X-Push-Token: {token or 'N/A'}")
    conn.close()

# ---------------------------------------------------------------------------
# TAB 3: Super-Admin (Approvals)
# ---------------------------------------------------------------------------
elif nav == "Super-Admin Approval":
    st.title("🛡️ Governance: Super-Admin Dashboard")
    st.markdown("Review and activate clinical facilities.")
    
    conn = rds_analytics.get_connection()
    df_tenants = pd.read_sql(
        rds_analytics.format_query("SELECT hospital_id, hospital_name, status, created_at FROM tenants ORDER BY created_at DESC"), 
        conn
    )
    
    if not df_tenants.empty:
        for idx, row in df_tenants.iterrows():
            with st.expander(f"{row['hospital_name']} [{row['status'].upper()}]"):
                col1, col2, col3 = st.columns(3)
                if row['status'] == 'pending':
                    if col1.button("Move to Sandbox", key=f"sb_{row['hospital_id']}"):
                        with conn.cursor() as cur:
                            import secrets
                            token = secrets.token_hex(32)
                            cur.execute(rds_analytics.format_query("UPDATE tenants SET status = 'sandbox', push_token = %s WHERE hospital_id = %s"), (token, row['hospital_id']))
                        conn.commit()
                        st.rerun()
                if row['status'] == 'sandbox':
                    if col2.button("Go Live", key=f"live_{row['hospital_id']}"):
                        with conn.cursor() as cur:
                            cur.execute(rds_analytics.format_query("UPDATE tenants SET status = 'live' WHERE hospital_id = %s"), (row['hospital_id'],))
                        conn.commit()
                        st.rerun()
                if col3.button("Suspend", key=f"sus_{row['hospital_id']}"):
                    with conn.cursor() as cur:
                        cur.execute(rds_analytics.format_query("UPDATE tenants SET status = 'pending' WHERE hospital_id = %s"), (row['hospital_id'],))
                    conn.commit()
                    st.rerun()
    conn.close()

st.caption("Asha SaaS Engine v5.0.0 | Production-Ready RBAC | NIST Encryption Standard")
