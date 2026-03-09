import streamlit as st
import pandas as pd
import requests
import json
import datetime
import time
import copy
import random
from dateutil.relativedelta import relativedelta
import warnings
import re
import firebase_admin
from firebase_admin import credentials, firestore
import math

try:
    import plotly.graph_objects as go

    HAS_PLOTLY = True
except ImportError:
    HAS_PLOTLY = False

# --- CONFIG & SUPPRESSION ---
warnings.simplefilter(action='ignore', category=FutureWarning)
st.set_page_config(page_title="AI Retirement Planner Pro", layout="wide", page_icon="🏦")

# --- CUSTOM CSS FOR PREMIUM LOOK ---
st.markdown("""
<style>
    .stApp { background-color: #f8fafc; }
    h1, h2, h3 { color: #1e293b !important; font-family: 'Inter', sans-serif; font-weight: 800 !important; }
    [data-testid="stExpander"] { background-color: white !important; border: 1px solid #e2e8f0 !important; border-radius: 12px !important; box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.05) !important; margin-bottom: 1rem !important; }
    .stButton > button { border-radius: 8px !important; transition: all 0.2s ease !important; }
    div[data-testid="column"]:has(button:contains("✨")) button { background: linear-gradient(90deg, #4f46e5 0%, #7c3aed 100%) !important; color: white !important; border: none !important; font-weight: 600 !important; box-shadow: 0 4px 14px 0 rgba(79, 70, 229, 0.39) !important; }
    div[data-testid="column"]:has(button:contains("✨")) button:hover { transform: translateY(-2px); box-shadow: 0 6px 20px 0 rgba(79, 70, 229, 0.39) !important; }
    [data-testid="stMetricValue"] { font-size: 1.8rem !important; font-weight: 700 !important; color: #4f46e5 !important; }
    .info-text { font-size: 0.95rem; color: #334155; margin-bottom: 15px; border-left: 4px solid #3b82f6; padding-left: 12px; background-color: #eff6ff; padding: 10px; padding-bottom: 12px; padding-right: 10px; border-radius: 0 8px 8px 0; line-height: 1.5;}

    [data-testid="stMetricValue"] { font-size: 1.6rem !important; }
    [data-testid="stMetricLabel"] { font-weight: 600 !important; color: #475569 !important; }

    /* Hide marker containers to prevent layout spacing issues */
    div[data-testid="element-container"]:has(.ai-btn-marker),
    div[data-testid="element-container"]:has(.save-btn-marker),
    div[data-testid="element-container"]:has(.main-save-btn-marker) {
        display: none;
    }

    /* AI Button subtle styling */
    div[data-testid="element-container"]:has(.ai-btn-marker) + div[data-testid="element-container"] button {
        background-color: #f5f3ff !important;
        border: 1px solid #c7d2fe !important;
        color: #4f46e5 !important;
        font-weight: 600 !important;
        border-radius: 8px !important;
        transition: all 0.2s ease !important;
    }
    div[data-testid="element-container"]:has(.ai-btn-marker) + div[data-testid="element-container"] button:hover {
        background-color: #e0e7ff !important;
        border-color: #818cf8 !important;
        transform: translateY(-2px);
        box-shadow: 0 4px 12px rgba(79, 70, 229, 0.15) !important;
    }

    /* Save Button subtle styling */
    div[data-testid="element-container"]:has(.save-btn-marker) + div[data-testid="element-container"] button {
        background-color: #f0fdf4 !important;
        border: 1px solid #bbf7d0 !important;
        color: #166534 !important;
        font-weight: 600 !important;
        border-radius: 8px !important;
        transition: all 0.2s ease !important;
    }
    div[data-testid="element-container"]:has(.save-btn-marker) + div[data-testid="element-container"] button:hover {
        background-color: #dcfce7 !important;
        border-color: #86efac !important;
        transform: translateY(-2px);
        box-shadow: 0 4px 12px rgba(22, 101, 52, 0.15) !important;
    }

    /* Main Action Button (Bottom Save) */
    div[data-testid="element-container"]:has(.main-save-btn-marker) + div[data-testid="element-container"] button {
        background: linear-gradient(90deg, #10b981 0%, #059669 100%) !important;
        color: white !important;
        border: none !important;
        font-weight: 700 !important;
        border-radius: 8px !important;
        box-shadow: 0 4px 14px 0 rgba(16, 185, 129, 0.39) !important;
        transition: all 0.2s ease !important;
    }
    div[data-testid="element-container"]:has(.main-save-btn-marker) + div[data-testid="element-container"] button:hover {
        transform: translateY(-2px);
        box-shadow: 0 6px 20px 0 rgba(16, 185, 129, 0.39) !important;
    }
</style>
""", unsafe_allow_html=True)

# --- 1. FIREBASE & SESSION CORE ---
try:
    import extra_streamlit_components as stx
except ImportError:
    st.error("Missing dependency: pip install extra-streamlit-components")
    st.stop()

if not firebase_admin._apps:
    try:
        if "firebase" in st.secrets:
            cred_dict = dict(st.secrets["firebase"])
            cred_dict["private_key"] = cred_dict["private_key"].replace("\\n", "\n")
            cred = credentials.Certificate(cred_dict)
            firebase_admin.initialize_app(cred)
        else:
            cred = credentials.Certificate('firebase_creds.json')
            firebase_admin.initialize_app(cred)
    except Exception as e:
        st.error(f"🚨 Firebase Initialization Failed: {e}")
        st.stop()

try:
    db = firestore.client()
except Exception as e:
    st.error(f"🚨 Firestore Connection Failed: {e}")
    st.stop()

FIREBASE_WEB_API_KEY = st.secrets.get("FIREBASE_WEB_API_KEY", "")
GEMINI_API_KEY = st.secrets.get("GEMINI_API_KEY", "")
cookie_manager = stx.CookieManager(key="auth_cookie_manager")

if cookie_manager.get_all() is None:
    st.stop()


def sign_in_with_email_and_password(email, password):
    url = f"https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key={FIREBASE_WEB_API_KEY}"
    return requests.post(url, json={"email": email, "password": password, "returnSecureToken": True}).json()


def sign_up_with_email_and_password(email, password):
    url = f"https://identitytoolkit.googleapis.com/v1/accounts:signUp?key={FIREBASE_WEB_API_KEY}"
    return requests.post(url, json={"email": email, "password": password, "returnSecureToken": True}).json()


def load_user_data(email):
    if email == "guest_demo": return {}
    doc = db.collection('users').document(email).get()
    return doc.to_dict() if doc.exists else {}


def call_gemini_json(prompt):
    if not GEMINI_API_KEY:
        st.error("⚠️ GEMINI_API_KEY is missing in Streamlit Secrets. AI operations are temporarily disabled.")
        return None
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-3-flash-preview:generateContent?key={GEMINI_API_KEY}"
    payload = {"contents": [{"parts": [{"text": prompt}]}],
               "generationConfig": {"responseMimeType": "application/json"}}
    try:
        res = requests.post(url, json=payload).json()
        if "error" in res:
            st.error(f"⚠️ API Error: {res['error'].get('message')}")
            return None
        text = res['candidates'][0]['content']['parts'][0]['text'].strip()
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text).strip()
        parsed = json.loads(text)
        if isinstance(parsed, dict) and len(parsed) == 1 and isinstance(list(parsed.values())[0], list): return \
        list(parsed.values())[0]
        return parsed
    except Exception as e:
        st.error(f"⚠️ Failed to parse AI response. Expected structured JSON.")
        return None


def subtract_years(dt, years):
    try:
        return dt.replace(year=dt.year - years)
    except ValueError:
        return dt.replace(year=dt.year - years, day=28)


def safe_num(val, default=0.0):
    try:
        return float(val) if val is not None and not pd.isna(val) else default
    except:
        return default


# --- AUTH LAYER ---
if 'user_email' not in st.session_state:
    saved_email = cookie_manager.get(cookie="user_email")
    if saved_email:
        st.session_state['user_email'] = saved_email
        st.session_state['user_data'] = load_user_data(saved_email)
        st.rerun()

    st.title("🏦 AI Retirement Planner Pro")
    st.markdown("#### *Your personal, AI-powered guide to a secure and stress-free retirement.*")

    tab1, tab2 = st.tabs(["Secure Login", "New Account"])
    with tab1:
        le = st.text_input("Email Address", key="le")
        lp = st.text_input("Password", type="password", key="lp")
        if st.button("Sign In", type="primary"):
            res = sign_in_with_email_and_password(le, lp)
            if "idToken" in res:
                st.session_state['user_email'] = res['email']
                st.session_state['user_data'] = load_user_data(res['email'])
                cookie_manager.set("user_email", res['email'],
                                   expires_at=datetime.datetime.now() + datetime.timedelta(days=30))
                time.sleep(0.2)
                st.rerun()
            else:
                st.error("Login failed. Please check your email and password.")
    with tab2:
        se = st.text_input("Email Address", key="se")
        sp = st.text_input("Password", type="password", key="sp")
        if st.button("Create Account"):
            if len(sp) >= 6:
                res = sign_up_with_email_and_password(se, sp)
                if "idToken" in res:
                    st.session_state['user_email'] = res['email']
                    st.session_state['user_data'] = {}
                    cookie_manager.set("user_email", res['email'],
                                       expires_at=datetime.datetime.now() + datetime.timedelta(days=30))
                    time.sleep(0.2)
                    st.rerun()
            else:
                st.warning("Password must be at least 6 characters long.")

    st.divider()
    if st.button("🚀 Try the Demo (Guest Mode)", use_container_width=True):
        st.session_state['user_email'] = "guest_demo"
        st.session_state['user_data'] = {}
        st.rerun()
    st.stop()

# --- STATE INIT & GLOBAL FUNCTIONS ---
if 'onboarding_shown' not in st.session_state:
    st.toast("Welcome! Hover over the (?) icons if you ever need help understanding the math.", icon="👋")
    st.session_state['onboarding_shown'] = True

current_year = datetime.date.today().year
ud = st.session_state.get('user_data', {})
p_info = ud.get('personal_info', {})
if 'current_expenses' not in st.session_state: st.session_state['current_expenses'] = ud.get('current_expenses', [])
if 'retire_expenses' not in st.session_state: st.session_state['retire_expenses'] = ud.get('retire_expenses', [])
if 'one_time_events' not in st.session_state: st.session_state['one_time_events'] = ud.get('one_time_events', [])
if 'assumptions' not in st.session_state: st.session_state['assumptions'] = ud.get('assumptions', {"inflation": 3.0,
                                                                                                   "inflation_healthcare": 5.5,
                                                                                                   "inflation_education": 4.5,
                                                                                                   "market_growth": 7.0,
                                                                                                   "income_growth": 3.0,
                                                                                                   "property_growth": 3.0,
                                                                                                   "rent_growth": 3.0,
                                                                                                   "current_tax_rate": 5.0,
                                                                                                   "retire_tax_rate": 0.0,
                                                                                                   "roth_conversions": False,
                                                                                                   "roth_target": "24%"})


def city_autocomplete(label, key_prefix, default_val=""):
    input_key = f"{key_prefix}_input"
    if input_key not in st.session_state: st.session_state[input_key] = default_val
    current_val = st.text_input(label, key=input_key,
                                help="Type a major city. The AI uses this to look up local costs of living, property values, and state taxes.")
    if current_val and len(current_val) > 2 and current_val != default_val:
        try:
            api_key = st.secrets.get("GOOGLE_MAPS_API_KEY", "")
            if api_key:
                url = f"https://maps.googleapis.com/maps/api/place/autocomplete/json?input={current_val}&types=(cities)&key={api_key}"
                res = requests.get(url).json()
                if res.get("status") == "OK":
                    predictions = res.get("predictions", [])
                    if not any(current_val == p["description"] for p in predictions):
                        st.caption("Did you mean:")
                        for p in predictions[:3]:
                            st.button(p["description"], key=f"{key_prefix}_{p['place_id']}",
                                      on_click=lambda k=input_key, v=p["description"]: st.session_state.update({k: v}))
        except:
            pass
    return current_val


def render_total(label, text):
    st.markdown(
        f"<div style='text-align: right; font-weight: 600; color: #4f46e5; font-size: 1.1rem;'>{label}: <span style='color: #111827;'>{text}</span></div>",
        unsafe_allow_html=True)


# ==========================================
#              THE UI SECTIONS
# ==========================================

c_title, c_logout = st.columns([4, 1])
with c_title:
    st.title("🏦 AI Retirement Planner Pro")
    st.markdown("##### *Your personal, AI-powered guide to a secure and stress-free retirement.*")
with c_logout:
    st.markdown(
        f"<div style='text-align: right; font-size: 0.9rem; color: #64748b; padding-top: 10px;'>Logged in as: <b>{st.session_state['user_email']}</b></div>",
        unsafe_allow_html=True)
    if st.button("Log Out", use_container_width=True):
        cookie_manager.delete("user_email")
        time.sleep(0.2)
        st.session_state.clear()
        st.rerun()

save_requested = False

# --- 1. PERSONAL INFO ---
with st.expander("👨‍👩‍👧‍👦 1. About You & Your Family", expanded=True):
    st.markdown(
        '<div class="info-text">💡 <strong>Why we ask this:</strong> Your exact birth year helps us accurately calculate IRS rules like when you <em>must</em> start taking money out of retirement accounts (RMDs). Adding a spouse lets us use the bigger "Married Filing Jointly" tax deduction, and listing kids helps the AI predict when childcare ends and college starts.</div>',
        unsafe_allow_html=True)

    c1, c2 = st.columns(2)
    my_name = c1.text_input("Your Name", value=p_info.get('name', ''))

    saved_dob = p_info.get('dob')
    default_dob = datetime.datetime.strptime(saved_dob, "%Y-%m-%d").date() if saved_dob else subtract_years(
        datetime.date.today(), int(p_info.get('age', 40)))
    my_dob = c2.date_input("Your Date of Birth", value=default_dob, min_value=datetime.date(1920, 1, 1),
                           max_value=datetime.date.today())
    my_age = relativedelta(datetime.date.today(), my_dob).years
    my_birth_year = my_dob.year

    curr_city = city_autocomplete("Where do you live? (City)", "curr_city", default_val=p_info.get('current_city', ''))

    st.divider()
    has_spouse = st.checkbox("Include a Spouse or Partner? (Enables joint tax brackets)",
                             value=p_info.get('has_spouse', False))
    spouse_name, spouse_dob, spouse_age, spouse_birth_year = "", None, 0, current_year
    if has_spouse:
        sc1, sc2 = st.columns(2)
        spouse_name = sc1.text_input("Spouse/Partner Name", value=p_info.get('spouse_name', ''))
        s_saved_dob = p_info.get('spouse_dob')
        s_default_dob = datetime.datetime.strptime(s_saved_dob, "%Y-%m-%d").date() if s_saved_dob else subtract_years(
            datetime.date.today(), int(p_info.get('spouse_age', 40)))
        spouse_dob = sc2.date_input("Spouse Date of Birth", value=s_default_dob, min_value=datetime.date(1920, 1, 1),
                                    max_value=datetime.date.today())
        spouse_age = relativedelta(datetime.date.today(), spouse_dob).years
        spouse_birth_year = spouse_dob.year

    st.divider()
    saved_kids = p_info.get('kids', [])
    num_kids = st.number_input("Number of Kids/Dependents", 0, 10, len(saved_kids))
    kids_data = []
    if num_kids > 0: st.write("**Kids' Details**")
    for i in range(num_kids):
        k1, k2 = st.columns([3, 1])
        kn = k1.text_input(f"Child {i + 1} Name", value=saved_kids[i]['name'] if i < len(saved_kids) else "",
                           key=f"kn_{i}")
        ka = k2.number_input(f"Age {i + 1}", 0, 25, saved_kids[i]['age'] if i < len(saved_kids) else 5, key=f"ka_{i}")
        kids_data.append({"name": kn, "age": ka})

    st.markdown('<div class="save-btn-marker"></div>', unsafe_allow_html=True)
    if st.button("💾 Save Profile", key="sv_1"):
        save_requested = True
        st.toast("✅ Profile Saved!", icon="💾")

# --- 2. INCOME ---
with st.expander("💵 2. Your Income Streams", expanded=False):
    st.markdown(
        '<div class="info-text">💡 <strong>How we handle your money:</strong> We automatically separate your regular paycheck (like W-2s) from investment income because the IRS taxes them differently.<br><br><strong>Social Security:</strong> Use the AI button below, or type in what you expect to get at your "Full Retirement Age" (usually 67). Our math engine will automatically adjust that number up or down depending on the exact year you decide to retire!</div>',
        unsafe_allow_html=True)

    df_inc = pd.DataFrame(ud.get('income', []))
    if df_inc.empty:
        df_inc = pd.DataFrame(
            [{"Description": "Base Salary", "Category": "Base Salary (W-2)", "Owner": "Me", "Annual Amount ($)": 0,
              "Start Year": current_year, "End Year": current_year + max(0, 65 - my_age), "Stop at Ret.?": True,
              "Override Growth (%)": None}])
    else:
        if "Start Age" in df_inc.columns:
            df_inc["Start Year"] = current_year + (pd.to_numeric(df_inc["Start Age"], errors='coerce') - my_age)
            df_inc["End Year"] = current_year + (pd.to_numeric(df_inc["End Age"], errors='coerce') - my_age)
            df_inc = df_inc.drop(columns=["Start Age", "End Age"])

        if "Stop at Ret.?" not in df_inc.columns: df_inc["Stop at Ret.?"] = False
        df_inc = df_inc.reindex(
            columns=["Description", "Category", "Owner", "Annual Amount ($)", "Start Year", "End Year", "Stop at Ret.?",
                     "Override Growth (%)"])

    st.markdown('<div class="ai-btn-marker"></div>', unsafe_allow_html=True)
    if st.button("✨ Auto-Estimate My Social Security (AI)"):
        with st.spinner("Asking AI to estimate your Social Security benefits based on your age and income..."):
            curr_inc = sum([safe_num(x.get('Annual Amount ($)', 0)) for x in ud.get('income', [])])
            if has_spouse:
                prompt = f"User is {my_age} years old making ${curr_inc}/year. Spouse is {spouse_age} years old. Estimate realistic annual Social Security primary insurance amounts (PIA) at Full Retirement Age for both. Return JSON: {{'ss_amount_me': integer, 'ss_amount_spouse': integer}}"
            else:
                prompt = f"User is {my_age} years old making ${curr_inc}/year. Estimate their annual Social Security primary insurance amount (PIA) at Full Retirement Age. Return JSON: {{'ss_amount_me': integer}}"
            res = call_gemini_json(prompt)
            if res:
                current_inc = df_inc.to_dict('records')
                # Primary SS automatically starts at their FRA year defaults (can be adjusted)
                if 'ss_amount_me' in res:
                    current_inc.append(
                        {"Description": "Estimated Social Security (Primary)", "Category": "Social Security",
                         "Owner": "Me", "Annual Amount ($)": res['ss_amount_me'], "Start Year": my_birth_year + 67,
                         "End Year": 2100, "Stop at Ret.?": False, "Override Growth (%)": None})
                if 'ss_amount_spouse' in res and has_spouse:
                    current_inc.append(
                        {"Description": "Estimated Social Security (Spouse)", "Category": "Social Security",
                         "Owner": "Spouse", "Annual Amount ($)": res['ss_amount_spouse'],
                         "Start Year": spouse_birth_year + 67, "End Year": 2100, "Stop at Ret.?": False,
                         "Override Growth (%)": None})
                st.session_state['user_data']['income'] = current_inc
                st.rerun()

    edited_inc = st.data_editor(
        df_inc,
        column_config={
            "Description": st.column_config.TextColumn("Description"),
            "Category": st.column_config.SelectboxColumn("Category", options=["Base Salary (W-2)", "Bonus / Commission",
                                                                              "Employer Match (401k/HSA)",
                                                                              "Equity / RSUs", "Contractor (1099)",
                                                                              "Dividends", "Social Security", "Pension",
                                                                              "Other"]),
            "Owner": st.column_config.SelectboxColumn("Whose Income?", options=["Me", "Spouse", "Joint"]),
            "Annual Amount ($)": st.column_config.NumberColumn("Amount per Year ($)", step=1000, format="$%d"),
            "Start Year": st.column_config.NumberColumn("Start Year", min_value=1900, max_value=2100, format="%d"),
            "End Year": st.column_config.NumberColumn("End Year", min_value=1900, max_value=2100, format="%d"),
            "Stop at Ret.?": st.column_config.CheckboxColumn("Stop at Retirement?",
                                                             help="If checked, this income will automatically turn off when the specific owner reaches their chosen retirement year."),
            "Override Growth (%)": st.column_config.NumberColumn("Custom Growth (%)", step=0.1, format="%.1f%%")
        }, num_rows="dynamic", width="stretch", hide_index=True, key="inc_editor"
    )
    render_total("Total Pre-Tax Income", f"${edited_inc['Annual Amount ($)'].sum():,.0f}")

    st.markdown('<div class="save-btn-marker"></div>', unsafe_allow_html=True)
    if st.button("💾 Save Income", key="sv_2"):
        save_requested = True
        st.toast("✅ Income Saved!", icon="💾")

# --- 3. ASSETS, LIABILITIES & NET WORTH ---
with st.expander("🏦 3. Assets, Debts & Net Worth", expanded=False):
    st.subheader("Real Estate")
    st.markdown(
        '<div class="info-text">💡 <strong>Smart Mortgages:</strong> Just tell us your loan balance, interest rate, and monthly payment. The math engine automatically pays down your loan over time and drops the expense entirely once it hits zero! <em>(Make sure you don\'t list your mortgage again in the budget section below).</em></div>',
        unsafe_allow_html=True)
    df_re = pd.DataFrame(ud.get('real_estate', []))
    if df_re.empty:
        df_re = pd.DataFrame([{"Property Name": "Primary Home", "Is Primary Residence?": True, "Market Value ($)": 0,
                               "Mortgage Balance ($)": 0, "Interest Rate (%)": 0.0, "Mortgage Payment ($)": 0,
                               "Monthly Expenses ($)": 0, "Monthly Rent ($)": 0, "Override Prop Growth (%)": None,
                               "Override Rent Growth (%)": None}])
    else:
        df_re = df_re.reindex(
            columns=["Property Name", "Is Primary Residence?", "Market Value ($)", "Mortgage Balance ($)",
                     "Interest Rate (%)", "Mortgage Payment ($)", "Monthly Expenses ($)", "Monthly Rent ($)",
                     "Override Prop Growth (%)", "Override Rent Growth (%)"])

    edited_re = st.data_editor(
        df_re,
        column_config={
            "Property Name": st.column_config.TextColumn("Property Name"),
            "Is Primary Residence?": st.column_config.CheckboxColumn("Primary Home?", default=False),
            "Market Value ($)": st.column_config.NumberColumn("Market Value ($)", step=10000, format="$%d"),
            "Mortgage Balance ($)": st.column_config.NumberColumn("Mortgage Balance ($)", step=10000, format="$%d"),
            "Interest Rate (%)": st.column_config.NumberColumn("Interest Rate (%)", step=0.001, format="%.3f%%"),
            "Mortgage Payment ($)": st.column_config.NumberColumn("Monthly P&I ($)", step=100, format="$%d"),
            "Monthly Expenses ($)": st.column_config.NumberColumn("Taxes/Ins/HOA ($)", step=100, format="$%d"),
            "Monthly Rent ($)": st.column_config.NumberColumn("Monthly Rent ($)", step=100, format="$%d"),
            "Override Prop Growth (%)": st.column_config.NumberColumn("Property Growth (%)", step=0.1, format="%.1f%%"),
            "Override Rent Growth (%)": st.column_config.NumberColumn("Rent Growth (%)", step=0.1, format="%.1f%%")
        }, num_rows="dynamic", width="stretch", hide_index=True, key="re_editor"
    )

    st.divider()
    st.subheader("Business Interests & Private Equity")
    df_biz = pd.DataFrame(ud.get('business', []))
    if df_biz.empty:
        df_biz = pd.DataFrame(
            [{"Business Name": "", "Total Valuation ($)": 0, "Your Ownership (%)": 100, "Annual Distribution ($)": 0,
              "Override Val. Growth (%)": None, "Override Dist. Growth (%)": None}])
    else:
        if "Override Val. Growth (%)" not in df_biz.columns: df_biz["Override Val. Growth (%)"] = None
        if "Override Dist. Growth (%)" not in df_biz.columns: df_biz["Override Dist. Growth (%)"] = None
        df_biz = df_biz.reindex(
            columns=["Business Name", "Total Valuation ($)", "Your Ownership (%)", "Annual Distribution ($)",
                     "Override Val. Growth (%)", "Override Dist. Growth (%)"])

    edited_biz = st.data_editor(
        df_biz,
        column_config={
            "Total Valuation ($)": st.column_config.NumberColumn("Total Value ($)", step=10000, format="$%d"),
            "Annual Distribution ($)": st.column_config.NumberColumn("Annual Income ($)", step=1000, format="$%d"),
            "Your Ownership (%)": st.column_config.NumberColumn("Your Ownership (%)", min_value=0, max_value=100,
                                                                format="%d%%"),
            "Override Val. Growth (%)": st.column_config.NumberColumn("Value Growth (%)", step=0.1, format="%.1f%%"),
            "Override Dist. Growth (%)": st.column_config.NumberColumn("Income Growth (%)", step=0.1, format="%.1f%%")
        }, num_rows="dynamic", width="stretch", hide_index=True, key="biz_editor"
    )

    st.divider()
    st.subheader("Liquid Savings & Investments")
    st.markdown(
        '<div class="info-text">💡 <strong>Smart Withdrawals:</strong> If you need cash in retirement, the system is smart. It pulls from regular taxable accounts first (paying long-term capital gains tax), then traditional 401(k)s (paying ordinary income tax), and saves your tax-free Roth accounts for last to maximize your wealth.</div>',
        unsafe_allow_html=True)
    df_ast = pd.DataFrame(ud.get('liquid_assets', []))
    if df_ast.empty:
        df_ast = pd.DataFrame(
            [{"Account Name": "Primary 401(k)", "Type": "Traditional 401k/IRA", "Owner": "Me", "Current Balance ($)": 0,
              "Annual Contribution ($/yr)": 0, "Est. Annual Growth (%)": 7.0, "Stop Contrib at Ret.?": True}])
    else:
        if "Annual Contribution ($)" in df_ast.columns: df_ast.rename(
            columns={'Annual Contribution ($)': 'Annual Contribution ($/yr)'}, inplace=True)
        if "Stop Contrib at Ret.?" not in df_ast.columns: df_ast["Stop Contrib at Ret.?"] = True
        df_ast = df_ast.reindex(
            columns=["Account Name", "Type", "Owner", "Current Balance ($)", "Annual Contribution ($/yr)",
                     "Est. Annual Growth (%)", "Stop Contrib at Ret.?"])

    edited_ast = st.data_editor(
        df_ast,
        column_config={
            "Type": st.column_config.SelectboxColumn("Account Type",
                                                     options=["Checking/Savings", "HYSA", "Brokerage (Taxable)",
                                                              "Traditional 401k/IRA", "Roth 401k/IRA", "HSA", "Crypto",
                                                              "529 Plan", "Other"]),
            "Owner": st.column_config.SelectboxColumn("Whose Account?", options=["Me", "Spouse", "Joint"]),
            "Current Balance ($)": st.column_config.NumberColumn("Current Balance ($)", step=5000, format="$%d"),
            "Annual Contribution ($/yr)": st.column_config.NumberColumn("Annual Additions ($/yr)", step=1000,
                                                                        format="$%d",
                                                                        help="How much you save into this account every year."),
            "Est. Annual Growth (%)": st.column_config.NumberColumn("Expected Return (%)", format="%.1f%%"),
            "Stop Contrib at Ret.?": st.column_config.CheckboxColumn("Stop Adding at Ret.?",
                                                                     help="Check this if you will stop saving into this account once the owner retires.")
        }, num_rows="dynamic", width="stretch", hide_index=True, key="assets_editor"
    )

    st.divider()
    st.subheader("Other Debts & Loans")
    st.markdown(
        '<div class="info-text">💡 Just like your mortgage, simply provide the balance, rate, and payment. We\'ll dynamically pay it down to zero for you in the background.</div>',
        unsafe_allow_html=True)
    df_debt = pd.DataFrame(ud.get('liabilities', []))
    if df_debt.empty:
        df_debt = pd.DataFrame(
            [{"Debt Name": "Auto Loan", "Type": "Auto Loan", "Current Balance ($)": 0, "Interest Rate (%)": 0.0,
              "Monthly Payment ($)": 0}])
    else:
        df_debt = df_debt.reindex(
            columns=["Debt Name", "Type", "Current Balance ($)", "Interest Rate (%)", "Monthly Payment ($)"])

    edited_debt = st.data_editor(
        df_debt,
        column_config={
            "Current Balance ($)": st.column_config.NumberColumn("Current Balance ($)", step=1000, format="$%d"),
            "Interest Rate (%)": st.column_config.NumberColumn("Interest Rate (%)", step=0.001, format="%.3f%%"),
            "Monthly Payment ($)": st.column_config.NumberColumn("Monthly Payment ($)", step=100, format="$%d")
        }, num_rows="dynamic", width="stretch", hide_index=True, key="debt_editor"
    )

    # Calculate Live Net Worth
    re_eq = pd.to_numeric(edited_re['Market Value ($)'], errors='coerce').fillna(0).sum() - pd.to_numeric(
        edited_re['Mortgage Balance ($)'], errors='coerce').fillna(0).sum()
    biz_eq = (pd.to_numeric(edited_biz['Total Valuation ($)'], errors='coerce').fillna(0) * (
                pd.to_numeric(edited_biz['Your Ownership (%)'], errors='coerce').fillna(0) / 100)).sum()
    liq_ast = pd.to_numeric(edited_ast['Current Balance ($)'], errors='coerce').fillna(0).sum()
    total_debt = pd.to_numeric(edited_debt['Current Balance ($)'], errors='coerce').fillna(0).sum()
    net_worth = re_eq + biz_eq + liq_ast - total_debt

    st.divider()
    c_met1, c_met2, c_met3, c_met4 = st.columns(4)
    c_met1.metric("Real Estate Equity", f"${re_eq:,.0f}")
    c_met2.metric("Business Equity", f"${biz_eq:,.0f}")
    c_met3.metric("Liquid Assets", f"${liq_ast:,.0f}")
    c_met4.metric("Other Debt", f"${total_debt:,.0f}")
    st.markdown(
        f"<div style='text-align: center; padding: 15px; margin-top: 15px; background: #eff6ff; border-radius: 8px;'><h3 style='margin:0; color: #1e293b;'>Total Estimated Net Worth: <span style='color: #3b82f6;'>${net_worth:,.0f}</span></h3></div>",
        unsafe_allow_html=True)

    st.markdown('<div class="save-btn-marker"></div>', unsafe_allow_html=True)
    if st.button("💾 Save Assets & Debts", key="sv_3"):
        save_requested = True
        st.toast("✅ Assets & Debts Saved!", icon="💾")

# --- AI CONTEXT PREP ---
k_ctx = f"{len(kids_data)} dependents ages {', '.join([str(k['age']) for k in kids_data])}."
primary_re = edited_re[edited_re["Is Primary Residence?"] == True]
h_pmt = pd.to_numeric(primary_re["Mortgage Payment ($)"], errors='coerce').fillna(0).sum()
h_exp = pd.to_numeric(primary_re["Monthly Expenses ($)"], errors='coerce').fillna(0).sum()
owns_home = not primary_re.empty

curr_inc_total = pd.to_numeric(edited_inc['Annual Amount ($)'], errors='coerce').fillna(0).sum()
liq_ast_total = pd.to_numeric(edited_ast['Current Balance ($)'], errors='coerce').fillna(0).sum()

if owns_home:
    h_ctx = f"Primary housing costs are ${h_pmt + h_exp:,.0f}/mo (Already accounted for)."
    ai_exclusion = "STRICT RULE: DO NOT INCLUDE Housing, Rent, Mortgages, Auto Loans, or Debt Payments in this list. They are explicitly tracked via balance sheet parameters."
else:
    h_ctx = "User is currently renting."
    ai_exclusion = "STRICT RULE: DO NOT INCLUDE Mortgages, Auto Loans, or Debt Payments. HOWEVER, YOU MUST INCLUDE a realistic 'Housing / Rent' expense reflecting current local market rates."

f_ctx = f"User({my_age})" + (
    f", Spouse({spouse_name}:{spouse_age})" if has_spouse else "") + f", Dependents({', '.join([f'{k['name']}:{k['age']}' for k in kids_data])})"
budget_categories = ["Housing / Rent", "Transportation", "Food", "Utilities", "Insurance", "Healthcare",
                     "Entertainment", "Education", "Personal Care", "Subscriptions", "Travel", "Debt Payments", "Other"]

# --- 4. CURRENT EXPENSES ---
with st.expander("💸 4. Current Budget & Expenses", expanded=False):
    st.markdown(
        '<div class="info-text">💡 <strong>Double-Counting Guard:</strong> The simulation automatically ignores "Housing" (if you own) and "Debt Payments" listed below, as it pulls the exact costs from your Real Estate and Liabilities sections above!</div>',
        unsafe_allow_html=True)
    df_c = pd.DataFrame(st.session_state['current_expenses'])
    if df_c.empty:
        df_c = pd.DataFrame([{"Description": "Groceries", "Category": "Food", "Frequency": "Monthly", "Amount ($)": 0,
                              "AI Estimate?": False}])
    else:
        if "AI Estimate?" not in df_c.columns: df_c["AI Estimate?"] = False
        df_c = df_c.reindex(columns=["Description", "Category", "Frequency", "Amount ($)", "AI Estimate?"])

    edited_c = st.data_editor(
        df_c,
        column_config={
            "Category": st.column_config.SelectboxColumn("Category", options=budget_categories),
            "Frequency": st.column_config.SelectboxColumn("Frequency", options=["Monthly", "Yearly"]),
            "Amount ($)": st.column_config.NumberColumn("Amount ($)", step=100, format="$%d"),
            "AI Estimate?": st.column_config.CheckboxColumn("🤖 AI Estimate?")
        }, num_rows="dynamic", width="stretch", hide_index=True, key="cur_ed"
    )

    cur_m_total, cur_y_total = 0, 0
    for r in edited_c.to_dict('records'):
        if str(r.get("Description", "")).strip() != "":
            amt = safe_num(r.get("Amount ($)"))
            if r.get("Frequency") == "Monthly":
                cur_m_total += amt
                cur_y_total += amt * 12
            else:
                cur_y_total += amt
                cur_m_total += amt / 12
    render_total("Est. Total Baseline Budget", f"${cur_m_total:,.0f} / mo  |  ${cur_y_total:,.0f} / yr")

    st.markdown('<div class="ai-btn-marker"></div>', unsafe_allow_html=True)
    if st.button("✨ Auto-Estimate Budget for " + (curr_city if curr_city else "Your Area") + " (AI)"):
        with st.spinner("Analyzing localized CPI data and family needs..."):
            valid = edited_c[edited_c["Description"].astype(str) != ""].copy()
            locked = valid[valid["AI Estimate?"] == False].to_dict('records')
            locked_desc = [x['Description'] for x in locked]
            wealth_ctx = f"The household has a current annual pre-tax income of ${curr_inc_total:,.0f} and liquid assets totaling ${liq_ast_total:,.0f}. VERY IMPORTANT: While you should scale the budget to reflect this wealth, assume these users are savvy spenders and aggressive savers (comfortable but smart with money), so avoid over-inflating lifestyle costs unnecessarily."
            allowed_cats = ", ".join(budget_categories)
            prompt = f"City: {curr_city}. Family: {k_ctx} Housing: {h_ctx}. {wealth_ctx} Generate 10-15 missing living expenses to create a complete monthly budget. {ai_exclusion} Skip these items as they are already accounted for: {json.dumps(locked_desc)}. Return ONLY a JSON array of objects with keys: 'Description', 'Category' (MUST be exactly one of: {allowed_cats}. If unsure, default to 'Other'), 'Frequency' (Monthly/Yearly), 'Amount ($)' (number), 'AI Estimate?' (true)."
            res = call_gemini_json(prompt)
            if res and isinstance(res, list) and len(res) > 0:
                st.session_state['current_expenses'] = locked + res
                st.rerun()
            else:
                st.error("⚠️ AI returned an invalid format. Please try again.")

    st.markdown('<div class="save-btn-marker"></div>', unsafe_allow_html=True)
    if st.button("💾 Save Budget", key="sv_4"):
        save_requested = True
        st.toast("✅ Budget Saved!", icon="💾")

# --- 5. MILESTONES ---
with st.expander("🎉 5. AI Life Milestone Forecaster", expanded=False):
    st.markdown(
        '<div class="info-text">💡 <strong>Multi-Year Events & 529s:</strong> Ensure you set an End Date for events that span multiple years (like a 4-year degree). The engine will automatically drain any 529 Plans first to pay for expenses with "College", "School" or "Tuition" in the description!</div>',
        unsafe_allow_html=True)
    df_m = pd.DataFrame(st.session_state['one_time_events'])
    current_date_str = f"{datetime.date.today().month:02d}/{datetime.date.today().year}"
    if df_m.empty:
        df_m = pd.DataFrame(
            [{"Description": "Child College Tuition", "Type": "Expense", "Frequency": "Yearly", "Amount ($)": 0,
              "Start Date (MM/YYYY)": current_date_str, "End Date (MM/YYYY)": "", "AI Estimate?": False}])
    else:
        if "AI Estimate?" not in df_m.columns: df_m["AI Estimate?"] = False
        df_m = df_m.reindex(
            columns=["Description", "Type", "Frequency", "Amount ($)", "Start Date (MM/YYYY)", "End Date (MM/YYYY)",
                     "AI Estimate?"])

    edited_m = st.data_editor(
        df_m,
        column_config={
            "Type": st.column_config.SelectboxColumn("Type", options=["Expense", "Income / Windfall"]),
            "Frequency": st.column_config.SelectboxColumn("Frequency", options=["One-Time", "Monthly", "Yearly"]),
            "Amount ($)": st.column_config.NumberColumn("Amount ($)", step=1000, format="$%d"),
            "Start Date (MM/YYYY)": st.column_config.TextColumn("Start (MM/YYYY)",
                                                                validate=r"^(0?[1-9]|1[0-2])\/[0-9]{4}$"),
            "End Date (MM/YYYY)": st.column_config.TextColumn("End (MM/YYYY)",
                                                              validate=r"^(0?[1-9]|1[0-2])\/[0-9]{4}$"),
            "AI Estimate?": st.column_config.CheckboxColumn("🤖 AI Estimate?")
        }, num_rows="dynamic", width="stretch", hide_index=True, key="mil_ed"
    )

    st.markdown('<div class="ai-btn-marker"></div>', unsafe_allow_html=True)
    if st.button("✨ Forecast Milestone Timelines & Costs (AI)"):
        with st.spinner("AI is mapping out your timeline and projecting future costs..."):
            valid = edited_m[edited_m["Description"].astype(str) != ""].to_dict('records')
            prompt = f"Family Context: {f_ctx}. Current Date: {current_date_str}. Calculate Start/End dates (MM/YYYY) and future Amounts in today's dollars for: {json.dumps(valid)}. Note: For multi-year events like College, ensure the End Date correctly reflects the duration (e.g. 4 years later). Return ONLY a JSON array."
            res = call_gemini_json(prompt)
            if res and isinstance(res, list):
                st.session_state['one_time_events'] = res
                st.rerun()

    st.markdown('<div class="save-btn-marker"></div>', unsafe_allow_html=True)
    if st.button("💾 Save Milestones", key="sv_5"):
        save_requested = True
        st.toast("✅ Milestones Saved!", icon="💾")

# --- 6. RETIREMENT SIMULATION & ASSUMPTIONS ---
with st.expander("🔮 6. Global Macroeconomic Assumptions & Retirement Sim", expanded=False):
    st.markdown(
        '<div class="info-text">💡 <strong>Global Overrides:</strong> Any specific growth percentages you typed into the Income, Real Estate, or Assets tables above will automatically override these global default rates.</div>',
        unsafe_allow_html=True)

    c1, c2 = st.columns(2)
    with c1:
        st.write("**Your Timeline**")
        ret_age = st.slider("Retirement Age", int(my_age), 100, int(p_info.get('retire_age', 65)))
        my_life_exp = st.slider("Your Life Expectancy", 70, 115, int(p_info.get('my_life_exp', 95)))

    s_ret_age = None
    spouse_life_exp = 0
    with c2:
        if has_spouse:
            st.write("**Spouse Timeline**")
            s_ret_age = st.slider("Spouse Retire Age", int(spouse_age), 100, int(p_info.get('spouse_retire_age', 65)))
            spouse_life_exp = st.slider("Spouse Life Expectancy", 70, 115, int(p_info.get('spouse_life_exp', 95)))

    ret_city = city_autocomplete("Where will you retire? (Search any city globally)", "retire_city",
                                 default_val=ud.get('retire_city', curr_city))

    st.markdown('<div class="ai-btn-marker"></div>', unsafe_allow_html=True)
    if st.button("✨ Auto-Set Macroeconomic Assumptions for " + (ret_city if ret_city else "Retirement") + " (AI)"):
        with st.spinner(f"Analyzing economic forecast and historical data for {ret_city}..."):
            prompt = f"Forecast long-term annual percentages for {ret_city if ret_city else 'the US'}. Return JSON object with keys: 'inflation', 'market_growth', 'income_growth', 'property_growth', 'rent_growth'."
            res = call_gemini_json(prompt)
            if res and isinstance(res, dict):
                st.session_state['assumptions'].update(res)
                st.rerun()

    c4, c5, c6 = st.columns(3)
    infl = c4.number_input("General CPI Inflation (%)",
                           value=float(st.session_state['assumptions'].get('inflation', 3.0)))
    infl_hc = c5.number_input("Healthcare Inflation (%)",
                              value=float(st.session_state['assumptions'].get('inflation_healthcare', 5.5)))
    infl_ed = c6.number_input("Education Inflation (%)",
                              value=float(st.session_state['assumptions'].get('inflation_education', 4.5)))

    c7, c8 = st.columns(2)
    mkt = c7.number_input("Market Growth (%)", value=float(st.session_state['assumptions'].get('market_growth', 7.0)))
    inc_g = c8.number_input("Income Growth (%)", value=float(st.session_state['assumptions'].get('income_growth', 3.0)))

    st.session_state['assumptions']['property_growth'] = st.number_input("Property Growth (%)", value=float(
        st.session_state['assumptions'].get('property_growth', 3.0)))
    st.session_state['assumptions']['rent_growth'] = st.number_input("Rent Growth (%)", value=float(
        st.session_state['assumptions'].get('rent_growth', 3.0)))

    st.divider()
    st.markdown(
        '<div class="info-text">💡 <strong>Double-Counting Guard:</strong> As with the current budget, "Housing" and "Debt Payments" are pulled automatically from your assets and excluded from this list.</div>',
        unsafe_allow_html=True)
    df_r = pd.DataFrame(st.session_state['retire_expenses'])
    if df_r.empty:
        df_r = pd.DataFrame(
            [{"Description": "Healthcare", "Category": "Healthcare", "Frequency": "Monthly", "Amount ($)": 0,
              "AI Estimate?": False}])
    else:
        if "AI Estimate?" not in df_r.columns: df_r["AI Estimate?"] = False
        df_r = df_r.reindex(columns=["Description", "Category", "Frequency", "Amount ($)", "AI Estimate?"])

    edited_r = st.data_editor(
        df_r,
        column_config={
            "Description": st.column_config.TextColumn("Description"),
            "Category": st.column_config.SelectboxColumn("Category", options=budget_categories),
            "Frequency": st.column_config.SelectboxColumn("Frequency", options=["Monthly", "Yearly"]),
            "Amount ($)": st.column_config.NumberColumn("Amount ($)", step=100, format="$%d"),
            "AI Estimate?": st.column_config.CheckboxColumn("🤖 AI Estimate?")
        }, num_rows="dynamic", width="stretch", hide_index=True, key="ret_exp_ed"
    )

    ret_m_total, ret_y_total = 0, 0
    for r in edited_r.to_dict('records'):
        if str(r.get("Description", "")).strip() != "":
            amt = safe_num(r.get("Amount ($)"))
            if r.get("Frequency") == "Monthly":
                ret_m_total += amt
                ret_y_total += amt * 12
            else:
                ret_y_total += amt
                ret_m_total += amt / 12
    render_total("Est. Total Retirement Budget", f"${ret_m_total:,.0f} / mo  |  ${ret_y_total:,.0f} / yr")

    st.markdown('<div class="ai-btn-marker"></div>', unsafe_allow_html=True)
    if st.button("✨ Simulate Realistic Lifestyle Costs in " + (ret_city if ret_city else "Retirement") + " (AI)"):
        with st.spinner(f"Modelling specific living costs for {ret_city}..."):
            valid = edited_r[edited_r["Description"].astype(str) != ""].copy()
            locked = valid[valid["AI Estimate?"] == False].to_dict('records')
            locked_desc = [x['Description'] for x in locked]
            wealth_ctx = f"The household will have a projected Net Worth built from a current income of ${curr_inc_total:,.0f} and current liquid assets of ${liq_ast_total:,.0f}. Assume a comfortable but smart, savvy-saver retirement lifestyle. Do not estimate overly extravagant expenses."
            allowed_cats = ", ".join(budget_categories)
            prompt = f"Retirement context: {ret_city}. Household size drops to {1 + (1 if has_spouse else 0)}. {wealth_ctx} Generate 10-15 missing living expenses to create a complete retirement budget. {ai_exclusion} Skip these items as they are already accounted for: {json.dumps(locked_desc)}. Return ONLY a JSON array of objects with keys: 'Description', 'Category' (MUST be exactly one of: {allowed_cats}. If unsure, default to 'Other'), 'Frequency' (Monthly/Yearly), 'Amount ($)' (number), 'AI Estimate?' (true)."
            res = call_gemini_json(prompt)
            if res and isinstance(res, list) and len(res) > 0:
                st.session_state['retire_expenses'] = locked + res
                st.rerun()
            else:
                st.error("⚠️ AI returned an invalid format. Please try again.")

    st.markdown('<div class="save-btn-marker"></div>', unsafe_allow_html=True)
    if st.button("💾 Save Assumptions", key="sv_6"):
        save_requested = True
        st.toast("✅ Assumptions Saved!", icon="💾")

# --- 7. ADVANCED SCENARIOS & TAXES ---
with st.expander("⚖️ 7. AI Based Advanced Retirement Scenarios", expanded=False):
    st.markdown(
        '<div class="info-text">💡 <strong>Tax Engine & Stress Tests:</strong> Our engine uses 2026 IRS tax brackets and dynamically calculates Federal, State, and FICA taxes. Real Estate income is intelligently taxed net of expenses and mortgage interest (Schedule E), and Business distributions include a simulated 20% Qualified Business Income (QBI) deduction. You can also stress-test your plan with things like a market crash or a long-term care event.</div>',
        unsafe_allow_html=True)

    col1, col2 = st.columns(2)
    with col1:
        st.write("**Simulation Stressors**")
        medicare_gap = st.toggle("🏥 Model Pre-Medicare Gap", value=True,
                                 help="If retiring before 65, adds a significant Private Health Insurance expense until Medicare eligibility.")
        medicare_cliff = st.toggle("🏥 Apply Medicare Cliff (Drop Healthcare at 65)", value=True,
                                   help="Automatically reduces 'Healthcare' budget line items by 50% when you turn 65 to simulate Medicare kicking in.")
        glidepath = st.toggle("📉 Apply Investment Glidepath", value=True,
                              help="Reduces the Market Growth rate by 1% for every 5 years you are into retirement, simulating a shift to bonds.")
        stress_test = st.toggle("📉 Apply 20% Market Crash at Retirement", value=False,
                                help="Simulates 'Sequence of Returns Risk' by dropping your portfolio by 20% in the first 3 years of retirement.")
        ltc_shock = st.toggle("🛏️ Long-Term Care (LTC) Shock", value=False,
                              help="Injects a massive $100k/yr medical expense into the final 3 years of your simulated life expectancy.")

    with col2:
        st.write("**State Tax / Effective Adjustments**")
        cur_t = st.number_input("Current State Tax Adjustment (%)",
                                value=float(st.session_state['assumptions'].get('current_tax_rate', 5.0)),
                                help="ONLY enter your effective State/Local rate here. Do NOT include Federal Tax, as the engine calculates Federal dynamically.")
        ret_t = st.number_input("Retirement State Tax Adjustment (%)",
                                value=float(st.session_state['assumptions'].get('retire_tax_rate', 0.0)),
                                help="Are you moving to a tax-free state in retirement? Adjust here.")

        st.markdown('<div class="ai-btn-marker"></div>', unsafe_allow_html=True)
        if st.button("✨ Auto-Estimate State Tax (AI)"):
            with st.spinner("Calculating local state tax rates..."):
                prompt = f"User in {curr_city}. Total Pre-Tax Income: ${curr_inc_total:,.0f}. Suggest the effective STATE AND LOCAL tax rate ONLY (do not include Federal Tax). Return JSON: {{'current_tax_rate': float, 'retire_tax_rate': float}}"
                res = call_gemini_json(prompt)
                if res and isinstance(res, dict):
                    st.session_state['assumptions']['current_tax_rate'] = res.get('current_tax_rate', cur_t)
                    st.session_state['assumptions']['retire_tax_rate'] = res.get('retire_tax_rate', ret_t)
                    st.rerun()

        st.divider()
        st.write("**Tax Optimization**")
        roth_conversions = st.toggle("🔄 Enable Roth Conversion Optimizer",
                                     value=st.session_state['assumptions'].get('roth_conversions', False),
                                     help="Automatically converts Traditional 401(k) funds to Roth during low-income years to minimize lifetime RMD taxes.")
        roth_target_idx = ["12%", "22%", "24%", "32%"].index(st.session_state['assumptions'].get('roth_target', "24%"))
        roth_target = st.selectbox("Target Bracket to Fill", options=["12%", "22%", "24%", "32%"],
                                   index=roth_target_idx,
                                   help="The AI will convert just enough Traditional funds each year to reach the very top of this selected tax bracket.")

    st.markdown('<div class="save-btn-marker"></div>', unsafe_allow_html=True)
    if st.button("💾 Save Settings", key="sv_7"):
        save_requested = True
        st.session_state['assumptions']['roth_conversions'] = roth_conversions
        st.session_state['assumptions']['roth_target'] = roth_target
        st.toast("✅ Settings Saved!", icon="💾")

# --- 8. EXHAUSTIVE DASHBOARD ENGINE & TAX LOGIC ---
with st.expander("📈 8. Advanced Simulation & Analytics Dashboard", expanded=True):
    if my_age > 0:

        prop_g = float(st.session_state['assumptions'].get('property_growth', 3.0))
        rent_g = float(st.session_state['assumptions'].get('rent_growth', 3.0))


        # --- PROGRESSIVE IRS FEDERAL TAX CALCULATOR ---
        def calc_federal_tax(ordinary_income, cap_gains, is_mfj, year_offset, inflation_rate):
            infl_factor = (1 + inflation_rate / 100) ** year_offset
            std_deduction = (29200 if is_mfj else 14600) * infl_factor
            taxable_ordinary = max(0, ordinary_income - std_deduction)

            b_mfj = [(23200, 0.10), (94300, 0.12), (201050, 0.22), (383900, 0.24), (487450, 0.32), (731200, 0.35),
                     (float('inf'), 0.37)]
            b_single = [(11600, 0.10), (47150, 0.12), (100525, 0.22), (191950, 0.24), (243725, 0.32), (609350, 0.35),
                        (float('inf'), 0.37)]
            brackets = b_mfj if is_mfj else b_single

            ord_tax = 0
            prev_limit = 0
            for limit, rate in brackets:
                adj_limit = limit * infl_factor
                if taxable_ordinary > prev_limit:
                    taxable_in_bracket = min(taxable_ordinary, adj_limit) - prev_limit
                    ord_tax += taxable_in_bracket * rate
                prev_limit = adj_limit

            marginal_rate = 0.10
            for limit, rate in brackets:
                if taxable_ordinary < limit * infl_factor:
                    marginal_rate = rate
                    break
            if taxable_ordinary > brackets[-1][0] * infl_factor: marginal_rate = 0.37

            cg_tax = 0
            if cap_gains > 0:
                cg_threshold = 94000 * infl_factor if is_mfj else 47000 * infl_factor
                if taxable_ordinary > cg_threshold: cg_tax = cap_gains * 0.15

            return ord_tax + cg_tax, marginal_rate


        # --- SOCIAL SECURITY FRA MATH ---
        def get_ss_multi(birth_year, retire_age):
            fra = 67 if birth_year >= 1960 else (66 + (min(birth_year - 1954, 10) / 12.0) if birth_year >= 1955 else 66)
            if retire_age < fra:
                months_early = (fra - retire_age) * 12
                if months_early <= 36:
                    return 1.0 - (months_early * (5 / 9 * 0.01))
                else:
                    return 1.0 - (36 * (5 / 9 * 0.01)) - ((months_early - 36) * (5 / 12 * 0.01))
            elif retire_age > fra:
                months_late = min((retire_age - fra) * 12, 36)
                return 1.0 + (months_late * (2 / 3 * 0.01))
            return 1.0


        my_ss_multi = get_ss_multi(my_birth_year, ret_age)
        spouse_ss_multi = get_ss_multi(spouse_birth_year, s_ret_age) if has_spouse else 1.0

        irs_uniform_table = {73: 26.5, 74: 25.5, 75: 24.6, 76: 23.7, 77: 22.9, 78: 22.0, 79: 21.1, 80: 20.2, 81: 19.4,
                             82: 18.5, 83: 17.7, 84: 16.8, 85: 16.0, 86: 15.2, 87: 14.4, 88: 13.7, 89: 12.9, 90: 12.2,
                             91: 11.5, 92: 10.8, 93: 10.1, 94: 9.5, 95: 8.9, 96: 8.4, 97: 7.8, 98: 7.3, 99: 6.8,
                             100: 6.4, 101: 6.0, 102: 5.6, 103: 5.2, 104: 4.9, 105: 4.6, 106: 4.3, 107: 4.1, 108: 3.9,
                             109: 3.7, 110: 3.5, 111: 3.4, 112: 3.3, 113: 3.1, 114: 3.0, 115: 2.9, 116: 2.8, 117: 2.7,
                             118: 2.5, 119: 2.3, 120: 2.0}

        # BASE STATE EXTRACTION (For reusability in Monte Carlo)
        base_sim_assets = [{"Account Name": a.get("Account Name"), "Type": a.get("Type"), "Owner": a.get("Owner", "Me"),
                            "bal": safe_num(a.get("Current Balance ($)")),
                            "contrib": safe_num(a.get("Annual Additions ($/yr)")),
                            "growth": safe_num(a.get("Est. Annual Growth (%)"), mkt),
                            "stop_at_ret": a.get("Stop Contrib at Ret.?", True)} for a in edited_ast.to_dict('records')
                           if a.get("Account Name")]
        if not base_sim_assets: base_sim_assets = [
            {"Account Name": "Unallocated Cash", "Type": "Checking/Savings", "Owner": "Me", "bal": 0.0, "contrib": 0.0,
             "growth": 0.0, "stop_at_ret": False}]

        base_sim_debts = [
            {"bal": safe_num(d.get("Current Balance ($)")), "pmt": safe_num(d.get("Monthly Payment ($)")) * 12,
             "rate": safe_num(d.get("Interest Rate (%)")) / 100, "name": d.get("Debt Name")} for d in
            edited_debt.to_dict('records') if d.get("Debt Name")]
        base_sim_re = [{"val": safe_num(r.get("Market Value ($)")), "debt": safe_num(r.get("Mortgage Balance ($)")),
                        "pmt": safe_num(r.get("Mortgage Payment ($)")) * 12,
                        "exp": safe_num(r.get("Monthly Expenses ($)")) * 12,
                        "rent": safe_num(r.get("Monthly Rent ($)")) * 12,
                        "v_growth": safe_num(r.get("Override Prop Growth (%)"), prop_g),
                        "r_growth": safe_num(r.get("Override Rent Growth (%)"), rent_g),
                        "rate": safe_num(r.get("Interest Rate (%)")) / 100} for r in edited_re.to_dict('records') if
                       r.get("Property Name")]
        base_sim_biz = [{"name": b.get("Business Name"), "val": safe_num(b.get("Total Valuation ($)")),
                         "own": safe_num(b.get("Your Ownership (%)")) / 100.0,
                         "dist": safe_num(b.get("Annual Distribution ($)")),
                         "v_growth": safe_num(b.get("Override Val. Growth (%)"), mkt),
                         "d_growth": safe_num(b.get("Override Dist. Growth (%)"), inc_g)} for b in
                        edited_biz.to_dict('records') if b.get("Business Name")]

        curr_exp_by_cat = {}
        for r in edited_c.to_dict('records'):
            if r.get("Description") and (
            r.get("Category") not in ["Housing / Rent", "Debt Payments"] if owns_home else r.get(
                    "Category") != "Debt Payments"):
                cat = r.get("Category", "Other")
                amt = safe_num(r.get("Amount ($)")) * (12 if r.get("Frequency") == "Monthly" else 1)
                curr_exp_by_cat[cat] = curr_exp_by_cat.get(cat, 0) + amt

        ret_exp_by_cat = {}
        for r in edited_r.to_dict('records'):
            if r.get("Description") and (
            r.get("Category") not in ["Housing / Rent", "Debt Payments"] if owns_home else r.get(
                    "Category") != "Debt Payments"):
                cat = r.get("Category", "Other")
                amt = safe_num(r.get("Amount ($)")) * (12 if r.get("Frequency") == "Monthly" else 1)
                ret_exp_by_cat[cat] = ret_exp_by_cat.get(cat, 0) + amt

        current_year = datetime.date.today().year
        my_life_exp_val = my_life_exp if my_life_exp else 95
        spouse_life_exp_val = spouse_life_exp if has_spouse and spouse_life_exp else 0

        primary_retire_year = my_birth_year + ret_age
        spouse_retire_year = spouse_birth_year + s_ret_age if has_spouse else 9999

        primary_end_year = my_birth_year + my_life_exp_val
        spouse_end_year = spouse_birth_year + spouse_life_exp_val if has_spouse else current_year

        max_year = max(primary_end_year, spouse_end_year)
        max_years = max_year - current_year

        primary_rmd_age = 73 if my_birth_year <= 1959 else 75
        spouse_rmd_age = 73 if spouse_birth_year <= 1959 else 75


        # --- CORE SIMULATION ENGINE ---
        def run_simulation(mkt_sequence):
            sim_assets = copy.deepcopy(base_sim_assets)
            sim_debts = copy.deepcopy(base_sim_debts)
            sim_re = copy.deepcopy(base_sim_re)
            sim_biz = copy.deepcopy(base_sim_biz)

            unfunded_debt_bal = 0
            prev_unfunded_debt_bal = 0
            sim_res, det_res, nw_det_res = [], [], []
            milestones_by_year = {}

            for year_offset in range(max_years + 1):
                year = current_year + year_offset
                my_current_age = year - my_birth_year
                spouse_current_age = year - spouse_birth_year if has_spouse else 0

                is_my_alive = year <= primary_end_year
                is_spouse_alive = has_spouse and (year <= spouse_end_year)

                if not is_my_alive and not is_spouse_alive:
                    break

                # System Milestones Logic
                if year == primary_retire_year and is_my_alive:
                    if year not in milestones_by_year: milestones_by_year[year] = []
                    milestones_by_year[year].append({"desc": "🎓 You Retire", "amt": 0, "type": "system"})

                if has_spouse and year == spouse_retire_year and is_spouse_alive:
                    if year not in milestones_by_year: milestones_by_year[year] = []
                    milestones_by_year[year].append({"desc": "🎓 Spouse Retires", "amt": 0, "type": "system"})

                if is_my_alive and my_current_age == primary_rmd_age:
                    if year not in milestones_by_year: milestones_by_year[year] = []
                    milestones_by_year[year].append({"desc": "🏦 Your RMDs Begin", "amt": 0, "type": "system"})

                if has_spouse and is_spouse_alive and spouse_current_age == spouse_rmd_age:
                    if year not in milestones_by_year: milestones_by_year[year] = []
                    milestones_by_year[year].append({"desc": "🏦 Spouse RMDs Begin", "amt": 0, "type": "system"})

                is_retired = year >= primary_retire_year
                is_spouse_retired = has_spouse and (year >= spouse_retire_year)

                yd = {"Year": year, "Age (Primary)": my_current_age}
                nw_yd = {"Year": year, "Age (Primary)": my_current_age}

                annual_inc, annual_ss, pre_tax_ord, pre_tax_cg, earned_income = 0, 0, 0, 0, 0

                # Compound high-interest debt if we enter crisis mode
                unfunded_debt_bal *= 1.18

                base_mkt_yr = mkt_sequence[year_offset]
                active_mkt = base_mkt_yr
                if glidepath and is_retired:
                    years_retired = year - primary_retire_year
                    active_mkt = max(3.0, base_mkt_yr - (math.floor(years_retired / 5) * 1.0))
                if stress_test and (primary_retire_year <= year < primary_retire_year + 3): active_mkt = -20.0

                active_mfj = True if has_spouse and is_my_alive and is_spouse_alive else False

                # Income
                for inc in edited_inc.to_dict('records'):
                    owner = inc.get("Owner", "Me")
                    if owner == "Me" and not is_my_alive: continue
                    if owner == "Spouse" and not is_spouse_alive: continue
                    if owner == "Joint" and not is_my_alive and not is_spouse_alive: continue

                    cat_name = inc.get("Category", "Other")
                    stop_at_ret = inc.get("Stop at Ret.?", False)

                    owner_retire_year = primary_retire_year
                    if owner == "Spouse":
                        owner_retire_year = spouse_retire_year
                    elif owner == "Joint":
                        owner_retire_year = primary_retire_year

                    start_year = safe_num(inc.get('Start Year'), current_year)
                    end_year = safe_num(inc.get('End Year'), 2100)

                    if cat_name == "Social Security": stop_at_ret = False

                    is_active = False
                    if stop_at_ret:
                        is_active = (year >= start_year) and (year < owner_retire_year)
                    else:
                        is_active = (start_year <= year <= end_year)

                    if inc.get("Description") and is_active:
                        g = safe_num(inc.get('Override Growth (%)'), inc_g)
                        base_amt = safe_num(inc.get('Annual Amount ($)'))

                        if cat_name == "Social Security":
                            if owner == "Me":
                                base_amt = base_amt * my_ss_multi
                            elif owner == "Spouse":
                                base_amt = base_amt * spouse_ss_multi

                        amt = base_amt * ((1 + g / 100) ** year_offset)
                        annual_inc += amt
                        yd[f"Income: {cat_name}"] = yd.get(f"Income: {cat_name}", 0) + amt
                        if cat_name == "Social Security": annual_ss += amt
                        if cat_name not in ["Employer Match (401k/HSA)", "Social Security"]: pre_tax_ord += amt
                        if cat_name in ["Base Salary (W-2)", "Bonus / Commission",
                                        "Contractor (1099)"]: earned_income += amt

                # RMDs (Calculated individually)
                rmd_income = 0
                for a in sim_assets:
                    if a.get('Type') == 'Traditional 401k/IRA' and a['bal'] > 0:
                        owner = a.get('Owner', 'Me')
                        owner_age = my_current_age if owner == 'Me' or owner == 'Joint' else spouse_current_age
                        owner_alive = is_my_alive if owner == 'Me' or owner == 'Joint' else is_spouse_alive
                        owner_rmd_age = primary_rmd_age if owner == 'Me' or owner == 'Joint' else spouse_rmd_age

                        if owner_alive and owner_age >= owner_rmd_age:
                            factor = irs_uniform_table.get(owner_age, 2.0)
                            rmd_amt = a['bal'] / factor
                            a['bal'] -= rmd_amt
                            rmd_income += rmd_amt
                            pre_tax_ord += rmd_amt

                if rmd_income > 0:
                    annual_inc += rmd_income
                    yd["Income: RMDs"] = rmd_income

                # Business & Real Estate
                cur_biz_val, biz_dist_total, re_equity, re_exp_total = 0, 0, 0, 0
                for b in sim_biz:
                    if year_offset > 0:
                        b['val'] *= (1 + b['v_growth'] / 100)
                        b['dist'] *= (1 + b['d_growth'] / 100)
                    cur_biz_val += (b['val'] * b['own'])
                    annual_inc += b['dist']
                    # QBI Deduction Proxy: ~20% of business pass-through income is generally tax-deductible in the US
                    pre_tax_ord += (b['dist'] * 0.80)
                    yd["Income: Biz Dist"] = b['dist']

                for r in sim_re:
                    if year_offset > 0: r['rent'] *= (1 + r['r_growth'] / 100); r['exp'] *= (1 + infl / 100); r[
                        'val'] *= (1 + r['v_growth'] / 100)
                    annual_inc += r['rent']
                    pre_tax_ord += r['rent']
                    yd["Income: RE Rent"] = r['rent'] if r['rent'] > 0 else 0
                    re_exp_total += r['exp']
                    yd["Expense: RE Upkeep/Tax"] = r['exp'] if r['exp'] > 0 else 0

                    interest_paid = 0
                    if r['debt'] > 0:
                        interest_paid = r['debt'] * r['rate']
                        principal = max(0, r['pmt'] - interest_paid)
                        r['debt'] = max(0, r['debt'] - principal)
                        re_exp_total += r['pmt']
                        yd["Expense: RE Mortgage"] = r['pmt']
                    re_equity += (r['val'] - r['debt'])

                    # Schedule E Proxy: Real Estate is taxed on NET income, not gross.
                    taxable_rent = max(0, r['rent'] - r['exp'] - interest_paid)
                    pre_tax_ord += taxable_rent

                # Core Expenses & Toggles
                total_exp = re_exp_total
                active_expense_dict = ret_exp_by_cat if is_retired else curr_exp_by_cat
                for cat, base_amt in active_expense_dict.items():
                    cat_infl = infl_hc if cat in ["Healthcare", "Insurance"] else (
                        infl_ed if cat == "Education" else infl)
                    inflated_exp = base_amt * ((1 + cat_infl / 100) ** year_offset)

                    # Drop expenses significantly if widow(er)
                    if has_spouse and not (is_my_alive and is_spouse_alive): inflated_exp *= 0.6

                    # Health Insurance Logic
                    if medicare_gap and is_retired and my_current_age < 65 and cat == "Healthcare": inflated_exp += (
                                15000 * ((1 + infl_hc / 100) ** year_offset))
                    if medicare_cliff and cat == "Healthcare" and my_current_age >= 65: inflated_exp *= 0.50

                    total_exp += inflated_exp
                    yd[f"Expense: {cat}"] = inflated_exp

                # LTC Shock
                if ltc_shock and my_current_age >= (my_life_exp_val - 2) and is_my_alive:
                    ltc_cost = 100000 * ((1 + infl_hc / 100) ** year_offset)
                    total_exp += ltc_cost
                    yd["Expense: Long Term Care Shock"] = ltc_cost

                # Debt Amortization
                debt_bal_total = 0
                for d in sim_debts:
                    if d['bal'] > 0:
                        interest = d['bal'] * d['rate']
                        principal = max(0, d['pmt'] - interest)
                        d['bal'] = max(0, d['bal'] - principal)
                        total_exp += d['pmt']
                        yd["Expense: Debt Payments"] = d['pmt']
                    debt_bal_total += d['bal']

                # Milestones & 529 Routing
                for ev in edited_m.to_dict('records'):
                    if ev.get("Description"):
                        desc = str(ev.get("Description", ""))
                        try:
                            sy = int(str(ev.get('Start Date (MM/YYYY)', '')).split('/')[-1])
                        except:
                            sy = 0

                        try:
                            ey = int(str(ev.get('End Date (MM/YYYY)', '')).split('/')[-1])
                        except:
                            ey = sy

                        freq = ev.get('Frequency', 'One-Time')
                        if freq == 'One-Time':
                            is_active_milestone = (year == sy)
                        else:
                            duration = max(1, ey - sy)
                            is_active_milestone = (sy <= year < sy + duration) if ey > sy else (year == sy)

                        if is_active_milestone and sy != 0:
                            base_amt = safe_num(ev.get('Amount ($)'))
                            if freq == 'Monthly': base_amt *= 12

                            amt = base_amt * ((1 + infl / 100) ** year_offset)

                            if year not in milestones_by_year: milestones_by_year[year] = []
                            milestones_by_year[year].append({"desc": desc, "amt": amt, "type": "normal"})

                            if ev.get('Type') == 'Expense':
                                total_exp += amt
                                yd[f"Expense: Milestone ({desc})"] = amt

                                # 529 Plan routing logic (Two-Pass Heuristic)
                                is_education = any(k in desc.lower() for k in
                                                   ['college', 'tuition', 'university', 'education', 'school'])
                                if is_education:
                                    amount_to_cover = amt
                                    covered_by_529 = 0

                                    # Pass 1: Try to match child name specifically
                                    for a in sim_assets:
                                        if a.get('Type') == '529 Plan' and a['bal'] > 0:
                                            acct_name_clean = re.sub(r'[^a-zA-Z0-9\s]', '',
                                                                     str(a.get('Account Name', ''))).lower()
                                            desc_clean = re.sub(r'[^a-zA-Z0-9\s]', '', desc).lower()
                                            acct_words = [w for w in acct_name_clean.split() if
                                                          len(w) > 3 and w not in ['plan', 'account', '529']]

                                            match = False
                                            for w in acct_words:
                                                if w in desc_clean:
                                                    match = True
                                                    break

                                            if match:
                                                if a['bal'] >= amount_to_cover:
                                                    a['bal'] -= amount_to_cover
                                                    covered_by_529 += amount_to_cover
                                                    amount_to_cover = 0
                                                    break
                                                else:
                                                    amount_to_cover -= a['bal']
                                                    covered_by_529 += a['bal']
                                                    a['bal'] = 0

                                    # Pass 2: Fallback to any remaining 529s
                                    if amount_to_cover > 0:
                                        for a in sim_assets:
                                            if a.get('Type') == '529 Plan' and a['bal'] > 0:
                                                if a['bal'] >= amount_to_cover:
                                                    a['bal'] -= amount_to_cover
                                                    covered_by_529 += amount_to_cover
                                                    amount_to_cover = 0
                                                    break
                                                else:
                                                    amount_to_cover -= a['bal']
                                                    covered_by_529 += a['bal']
                                                    a['bal'] = 0

                                    if covered_by_529 > 0:
                                        annual_inc += covered_by_529
                                        yd[f"Income: Tax-Free 529 Withdrawal ({desc})"] = covered_by_529
                            else:
                                annual_inc += amt
                                pre_tax_ord += amt
                                yd[f"Income: Milestone ({desc})"] = amt

                # Asset Contributions
                asset_contributions = 0
                if not is_retired:
                    for a in sim_assets:
                        owner = a.get('Owner', 'Me')
                        owner_retire_year = primary_retire_year
                        if owner == 'Spouse':
                            owner_retire_year = spouse_retire_year
                        elif owner == 'Joint':
                            owner_retire_year = primary_retire_year

                        is_owner_alive = False
                        if owner == 'Me':
                            is_owner_alive = is_my_alive
                        elif owner == 'Spouse':
                            is_owner_alive = is_spouse_alive
                        else:
                            is_owner_alive = is_my_alive or is_spouse_alive

                        if is_owner_alive:
                            stop_contrib = a.get('stop_at_ret', True)
                            if not (stop_contrib and year >= owner_retire_year):
                                a['bal'] += a['contrib']
                                asset_contributions += a['contrib']

                # Pre-apply market growth
                for a in sim_assets:
                    a_growth = active_mkt if a.get('Type') not in ['Checking/Savings', 'HYSA', 'Unallocated Cash'] else \
                    a['growth']
                    a['bal'] *= (1 + a_growth / 100)

                # Roth Conversion Optimizer
                if roth_conversions and is_retired:
                    infl_factor = (1 + infl / 100) ** year_offset
                    std_deduction = (29200 if active_mfj else 14600) * infl_factor

                    b_limits_mfj = {"12%": 94300, "22%": 201050, "24%": 383900, "32%": 487450}
                    b_limits_single = {"12%": 47150, "22%": 100525, "24%": 191950, "32%": 243725}

                    b_limits = b_limits_mfj if active_mfj else b_limits_single
                    target_limit = b_limits.get(roth_target, 383900) * infl_factor
                    target_max_income = target_limit + std_deduction

                    conversion_room = max(0, target_max_income - pre_tax_ord)
                    total_converted = 0

                    if conversion_room > 0:
                        for a in sim_assets:
                            if a.get('Type') == 'Traditional 401k/IRA' and a['bal'] > 0:
                                convert_amt = min(a['bal'], conversion_room - total_converted)
                                if convert_amt > 0:
                                    a['bal'] -= convert_amt
                                    total_converted += convert_amt

                                    roth_found = False
                                    for roth_a in sim_assets:
                                        if roth_a.get('Type') == 'Roth 401k/IRA' and roth_a.get('Owner') == a.get(
                                                'Owner'):
                                            roth_a['bal'] += convert_amt
                                            roth_found = True
                                            break
                                    if not roth_found:
                                        sim_assets.append({
                                            "Account Name": f"Converted Roth ({a.get('Owner')})",
                                            "Type": "Roth 401k/IRA",
                                            "Owner": a.get("Owner", "Me"),
                                            "bal": convert_amt,
                                            "contrib": 0.0,
                                            "growth": a.get('growth', mkt),
                                            "stop_at_ret": True
                                        })
                                if total_converted >= conversion_room:
                                    break

                        if total_converted > 0:
                            pre_tax_ord += total_converted
                            yd["Roth Conversion Amount"] = total_converted

                # Tax Calculations
                base_fed_tax, marginal_rate = calc_federal_tax(pre_tax_ord, 0, active_mfj, year_offset, infl)
                state_tax_rate = cur_t if not is_retired else ret_t
                state_tax = pre_tax_ord * (state_tax_rate / 100.0)

                # FICA Tax (Simplified approximation for earned income)
                fica_tax = 0
                if earned_income > 0:
                    ss_wage_base = 168600 * ((1 + infl / 100) ** year_offset)
                    ss_tax = min(earned_income, ss_wage_base) * 0.062
                    med_tax = earned_income * 0.0145
                    addl_med_tax = max(0, earned_income - 250000) * 0.009
                    fica_tax = ss_tax + med_tax + addl_med_tax

                total_tax = base_fed_tax + state_tax + fica_tax

                # Robust Shortfall / Withdrawal Math
                cash_outflows = total_exp + asset_contributions + total_tax
                net_cash_flow = annual_inc - cash_outflows

                if net_cash_flow > 0:
                    # Surplus
                    if unfunded_debt_bal > 0:
                        payoff = min(net_cash_flow, unfunded_debt_bal)
                        unfunded_debt_bal -= payoff
                        net_cash_flow -= payoff
                    if net_cash_flow > 0 and len(sim_assets) > 0:
                        sim_assets[0]['bal'] += net_cash_flow
                elif net_cash_flow < 0:
                    shortfall = abs(net_cash_flow)

                    # Sequence 1: Taxable Brokerage / Cash
                    for a in sim_assets:
                        if shortfall <= 0: break
                        if a.get('Type') in ['Checking/Savings', 'HYSA', 'Brokerage (Taxable)', 'Unallocated Cash']:
                            if a.get('Type') == 'Brokerage (Taxable)':
                                req_gross = shortfall / 0.85
                                if a['bal'] >= req_gross:
                                    a['bal'] -= req_gross
                                    total_tax += (req_gross - shortfall)
                                    shortfall = 0
                                else:
                                    withdrawn = a['bal']
                                    a['bal'] = 0
                                    net_cash = withdrawn * 0.85
                                    total_tax += (withdrawn - net_cash)
                                    shortfall -= net_cash
                            else:
                                if a['bal'] >= shortfall:
                                    a['bal'] -= shortfall
                                    shortfall = 0
                                else:
                                    shortfall -= a['bal']
                                    a['bal'] = 0

                    # Sequence 2: Tax-Deferred (Traditional 401k)
                    if shortfall > 0:
                        for a in sim_assets:
                            if shortfall <= 0: break
                            if a.get('Type') == 'Traditional 401k/IRA':
                                eff_tax = min(marginal_rate + (state_tax_rate / 100.0), 0.99)
                                req_gross = shortfall / (1.0 - eff_tax)
                                if a['bal'] >= req_gross:
                                    a['bal'] -= req_gross
                                    total_tax += (req_gross - shortfall)
                                    shortfall = 0
                                else:
                                    withdrawn = a['bal']
                                    a['bal'] = 0
                                    net_cash = withdrawn * (1.0 - eff_tax)
                                    total_tax += (withdrawn - net_cash)
                                    shortfall -= net_cash

                    # Sequence 3: Tax-Free (Roth/HSA)
                    if shortfall > 0:
                        for a in sim_assets:
                            if shortfall <= 0: break
                            if a.get('Type') in ['Roth 401k/IRA', 'HSA', 'Crypto', '529 Plan', 'Other']:
                                if a['bal'] >= shortfall:
                                    a['bal'] -= shortfall
                                    shortfall = 0
                                else:
                                    shortfall -= a['bal']
                                    a['bal'] = 0

                    # Sequence 4: Complete Liquidity Failure -> Credit Card Debt
                    if shortfall > 0:
                        unfunded_debt_bal += shortfall

                # Check for Critical Shortfall Alert
                if unfunded_debt_bal > 0 and prev_unfunded_debt_bal == 0:
                    if year not in milestones_by_year: milestones_by_year[year] = []
                    milestones_by_year[year].append(
                        {"desc": "🚨 MAJOR SHORTFALL: Cash Depleted!", "amt": unfunded_debt_bal, "type": "critical"})
                prev_unfunded_debt_bal = unfunded_debt_bal

                liquid_assets_total = 0
                for a in sim_assets:
                    # Ensure no floating point math drags balance below absolute zero
                    a['bal'] = max(0, a['bal'])
                    liquid_assets_total += a['bal']
                    nw_yd[f"Asset: {a.get('Account Name', 'Account')}"] = a['bal']

                net_worth = liquid_assets_total + re_equity + cur_biz_val - debt_bal_total - unfunded_debt_bal

                yd["Expense: Taxes"] = total_tax
                yd["Net Savings"] = annual_inc - total_exp - total_tax

                nw_yd["Total Liquid Assets"] = liquid_assets_total
                nw_yd["Total Real Estate Equity"] = re_equity
                nw_yd["Total Business Equity"] = cur_biz_val
                nw_yd["Total Debt Liabilities"] = -(debt_bal_total + unfunded_debt_bal)
                if unfunded_debt_bal > 0:
                    yd["Expense: Unfunded Shortfall Debt Interest"] = unfunded_debt_bal
                nw_yd["Total Net Worth"] = net_worth

                sim_res.append(
                    {"Year": year, "Age": my_current_age, "Annual Income": annual_inc, "Annual Expenses": total_exp,
                     "Annual Taxes": total_tax, "Annual Net Savings": yd["Net Savings"],
                     "Liquid Assets": liquid_assets_total,
                     "Real Estate Equity": re_equity, "Business Equity": cur_biz_val,
                     "Debt": -debt_bal_total, "Unfunded Debt": unfunded_debt_bal, "Net Worth": net_worth})
                det_res.append(yd)
                nw_det_res.append(nw_yd)

            return sim_res, det_res, nw_det_res, milestones_by_year


        # --- EXECUTE BASE DETERMINISTIC RUN ---
        deterministic_seq = [mkt] * (max_years + 1)
        sim_results, detailed_results, nw_detailed_results, run_milestones = run_simulation(deterministic_seq)

        # --- UI RENDER: DASHBOARD ---
        if len(sim_results) > 0:
            df_sim_nominal = pd.DataFrame(sim_results)
            final_nw = df_sim_nominal.iloc[-1]['Net Worth']

            shortfall_mask = df_sim_nominal['Unfunded Debt'] > 0
            deplete_year = df_sim_nominal[shortfall_mask]['Year'].min() if not df_sim_nominal[
                shortfall_mask].empty else None
            deplete_age = df_sim_nominal[shortfall_mask]['Age'].min() if not df_sim_nominal[
                shortfall_mask].empty else None

            c_status, c_ai_btn = st.columns([3, 2])
            with c_status:
                if deplete_year is not None:
                    st.error(
                        f"🔴 **Liquidity Crisis:** You completely exhaust your liquid cash in **Year {deplete_year}** (Age {deplete_age}) and begin accumulating high-interest shortfall debt.")
                elif final_nw >= 1000000:
                    st.success(
                        f"🟢 **On Track:** Projected Net Worth at timeline end is **${final_nw:,.0f}**. Your assets comfortably outlive your life expectancy.")
                elif final_nw > 0:
                    st.warning(
                        f"🟡 **Caution:** Projected Net Worth at timeline end is **${final_nw:,.0f}**. You are solvent, but with a narrow margin of safety.")

            with c_ai_btn:
                st.markdown('<div class="ai-btn-marker"></div>', unsafe_allow_html=True)
                if st.button("✨ Generate AI Financial Health Report", use_container_width=True):
                    with st.spinner("AI acting as fiduciary advisor..."):
                        sim_summary = {
                            "Current Age": my_age, "Retirement Age": ret_age, "Life Expectancy": my_life_exp_val,
                            "Current Net Worth": df_sim_nominal.iloc[0]['Net Worth'],
                            "Final Net Worth": df_sim_nominal.iloc[-1]['Net Worth'],
                            "Shortfall Year": str(deplete_year) if deplete_year is not None else "None",
                            "Avg Annual Income": df_sim_nominal['Annual Income'].mean(),
                            "Avg Annual Expenses": df_sim_nominal['Annual Expenses'].mean()
                        }
                        prompt = f"Act as an expert fiduciary financial planner. Review this user's simulation summary: {json.dumps(sim_summary)}. Write a highly detailed, 3-paragraph professional analysis of their financial trajectory. Discuss their sequence of return risks, highlight strengths, and provide specific recommendations (e.g. increase savings, delay retirement, adjust spending). Return ONLY valid JSON exactly like this: {{\"analysis\": \"your markdown text here, using \\n for line breaks\"}}"
                        res = call_gemini_json(prompt)
                        if res and 'analysis' in res:
                            st.session_state['ai_analysis_report'] = res['analysis']
                        else:
                            st.error("⚠️ AI Analysis failed to generate.")

            if 'ai_analysis_report' in st.session_state:
                report_content = st.session_state['ai_analysis_report'].replace('\\n', '\n').replace('$', r'\$')
                st.info(f"### 🤖 AI Advisory Report\n\n{report_content}")

            st.divider()
            c_dash1, c_dash2 = st.columns([3, 1])
            with c_dash1:
                st.markdown(
                    '<div class="info-text">💡 <strong>A note about the numbers:</strong> All charts show "Future Dollars" by default, meaning a $100 grocery bill today will look much larger in 20 years due to inflation. Use the toggle to the right to view everything in today\'s money instead!</div>',
                    unsafe_allow_html=True)
            with c_dash2:
                view_todays_dollars = st.toggle("💵 View in Today's Dollars", value=False,
                                                help="Removes the effect of inflation so you can easily understand what these big future numbers feel like today.")

            # APPLY DISCOUNTING IF TOGGLED
            if view_todays_dollars:
                for i in range(len(sim_results)):
                    discount = (1 + infl / 100) ** i
                    for col in ["Annual Income", "Annual Expenses", "Annual Taxes", "Annual Net Savings",
                                "Liquid Assets", "Real Estate Equity", "Business Equity", "Debt", "Unfunded Debt",
                                "Net Worth"]:
                        sim_results[i][col] /= discount
                    for k in detailed_results[i].keys():
                        if k not in ["Age (Primary)", "Year"]: detailed_results[i][k] /= discount
                    for k in nw_detailed_results[i].keys():
                        if k not in ["Age (Primary)", "Year"] and not isinstance(nw_detailed_results[i][k], str):
                            nw_detailed_results[i][k] /= discount

            df_sim = pd.DataFrame(sim_results)

            if HAS_PLOTLY:
                # Pre-calculate Milestone Chart Markers
                m_x_normal, m_y_normal, m_text_normal = [], [], []
                m_x_system, m_y_system, m_text_system = [], [], []
                m_x_alert, m_y_alert, m_text_alert = [], [], []

                if run_milestones:
                    m_years = sorted(list(run_milestones.keys()))
                    for y in m_years:
                        row = df_sim[df_sim['Year'] == y]
                        nw_val = row['Net Worth'].values[0] if not row.empty else 0

                        events = run_milestones[y]
                        normals = [e for e in events if e.get('type') == 'normal']
                        systems = [e for e in events if e.get('type') == 'system']
                        alerts = [e for e in events if e.get('type') == 'critical']

                        discount = (1 + infl / 100) ** (y - current_year) if view_todays_dollars else 1.0

                        if normals:
                            texts = [f"• {m['desc']} (${m['amt'] / discount:,.0f})" for m in normals]
                            m_x_normal.append(y)
                            m_y_normal.append(nw_val)
                            m_text_normal.append(f"<b>Year {y}:</b><br>" + "<br>".join(texts))

                        if systems:
                            texts = [f"• {m['desc']}" for m in systems]
                            m_x_system.append(y)
                            m_y_system.append(nw_val)
                            m_text_system.append(f"<b>System Event ({y}):</b><br>" + "<br>".join(texts))

                        if alerts:
                            texts = [f"• {m['desc']}" for m in alerts]
                            m_x_alert.append(y)
                            m_y_alert.append(nw_val)
                            m_text_alert.append(f"<b>⚠️ ALERT ({y}):</b><br>" + "<br>".join(texts))

                st.write("#### Net Worth Composition (Smart Asset Drawdown)")
                fig_nw = go.Figure()
                fig_nw.add_trace(go.Scatter(x=df_sim["Year"], y=df_sim["Liquid Assets"], mode='lines', stackgroup='one',
                                            name='Liquid Assets', fillcolor='rgba(20, 184, 166, 0.5)',
                                            line=dict(color='#14b8a6')))
                fig_nw.add_trace(
                    go.Scatter(x=df_sim["Year"], y=df_sim["Real Estate Equity"], mode='lines', stackgroup='one',
                               name='Real Estate Equity', fillcolor='rgba(139, 92, 246, 0.5)',
                               line=dict(color='#8b5cf6')))
                fig_nw.add_trace(
                    go.Scatter(x=df_sim["Year"], y=df_sim["Business Equity"], mode='lines', stackgroup='one',
                               name='Business Equity', fillcolor='rgba(245, 158, 11, 0.5)', line=dict(color='#f59e0b')))
                fig_nw.add_trace(go.Scatter(x=df_sim["Year"], y=-df_sim["Unfunded Debt"] - df_sim["Debt"], mode='lines',
                                            stackgroup='two', name='Total Liabilities (Inc. Shortfalls)',
                                            fillcolor='rgba(244, 63, 94, 0.5)', line=dict(color='#f43f5e')))
                fig_nw.add_trace(
                    go.Scatter(x=df_sim["Year"], y=df_sim["Net Worth"], mode='lines', name='Total Net Worth',
                               line=dict(color='#111827', width=3, dash='dot')))

                # Overlay Milestone Markers
                if m_x_normal:
                    fig_nw.add_trace(go.Scatter(x=m_x_normal, y=m_y_normal, mode='markers',
                                                marker=dict(symbol='star', size=14, color='#eab308',
                                                            line=dict(width=1.5, color='white')),
                                                name='User Milestones', hoverinfo='text', text=m_text_normal))
                if m_x_system:
                    fig_nw.add_trace(go.Scatter(x=m_x_system, y=m_y_system, mode='markers',
                                                marker=dict(symbol='star', size=14, color='#3b82f6',
                                                            line=dict(width=1.5, color='white')), name='System Events',
                                                hoverinfo='text', text=m_text_system))
                if m_x_alert:
                    fig_nw.add_trace(go.Scatter(x=m_x_alert, y=m_y_alert, mode='markers',
                                                marker=dict(symbol='star', size=18, color='#ef4444',
                                                            line=dict(width=2, color='white')), name='Critical Alerts',
                                                hoverinfo='text', text=m_text_alert))

                fig_nw.update_layout(hovermode="x unified", yaxis=dict(tickformat="$,.0f"),
                                     margin=dict(l=0, r=0, t=30, b=0),
                                     legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1))
                st.plotly_chart(fig_nw, use_container_width=True)

                st.write("#### Annual Cash Flow & Progressive Taxes")
                fig_cf = go.Figure()
                fig_cf.add_trace(go.Scatter(x=df_sim["Year"], y=df_sim["Annual Income"], mode='lines', name='Income',
                                            line=dict(color='#4f46e5', width=3)))
                fig_cf.add_trace(
                    go.Scatter(x=df_sim["Year"], y=df_sim["Annual Expenses"], mode='lines', name='Expenses',
                               line=dict(color='#f43f5e', width=3)))
                fig_cf.add_trace(go.Scatter(x=df_sim["Year"], y=df_sim["Annual Taxes"], mode='lines', name='Taxes',
                                            line=dict(color='#f59e0b', width=3)))
                fig_cf.add_trace(
                    go.Scatter(x=df_sim["Year"], y=df_sim["Annual Net Savings"], mode='lines', name='Net Cashflow',
                               line=dict(color='#10b981', width=3, dash='dot')))

                # Overlay Milestone Markers
                if m_x_normal:
                    fig_cf.add_trace(go.Scatter(x=m_x_normal, y=[0] * len(m_x_normal), mode='markers',
                                                marker=dict(symbol='star', size=14, color='#eab308',
                                                            line=dict(width=1.5, color='white')),
                                                name='User Milestones', hoverinfo='text', text=m_text_normal))
                if m_x_system:
                    fig_cf.add_trace(go.Scatter(x=m_x_system, y=[0] * len(m_x_system), mode='markers',
                                                marker=dict(symbol='star', size=14, color='#3b82f6',
                                                            line=dict(width=1.5, color='white')), name='System Events',
                                                hoverinfo='text', text=m_text_system))
                if m_x_alert:
                    fig_cf.add_trace(go.Scatter(x=m_x_alert, y=[0] * len(m_x_alert), mode='markers',
                                                marker=dict(symbol='star', size=18, color='#ef4444',
                                                            line=dict(width=2, color='white')), name='Critical Alerts',
                                                hoverinfo='text', text=m_text_alert))

                fig_cf.update_layout(hovermode="x unified", yaxis=dict(tickformat="$,.0f"),
                                     margin=dict(l=0, r=0, t=30, b=0),
                                     legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1))
                st.plotly_chart(fig_cf, use_container_width=True)

            # --- MONTE CARLO SECTION ---
            st.divider()
            st.subheader("🎲 Monte Carlo Risk Analysis")
            st.markdown(
                '<div class="info-text">💡 <strong>Stress Test Your Plan:</strong> Real markets are bumpy. The Monte Carlo simulation runs your exact plan through hundreds of randomized market scenarios (based on historical volatility) to find your true probability of success.</div>',
                unsafe_allow_html=True)

            col_mc1, col_mc2, col_mc3 = st.columns([1, 1, 2])
            mc_vol = col_mc1.number_input("Portfolio Volatility (%)", value=15.0,
                                          help="Historically, the S&P 500 maintains a volatility (standard deviation) proximal to 15%. Fixed income allocations approximate 5%.")
            mc_runs = col_mc2.number_input("Number of Simulations", min_value=10, max_value=500, value=100, step=10)

            col_mc3.markdown('<div class="ai-btn-marker"></div>', unsafe_allow_html=True)
            if col_mc3.button("✨ Run Monte Carlo Simulation", use_container_width=True):
                with st.spinner(f"Rendering {mc_runs} parallel market sequences..."):
                    success_count = 0
                    all_nw_paths = []

                    for r in range(mc_runs):
                        rand_seq = [random.gauss(mkt, mc_vol) for _ in range(max_years + 1)]
                        res, _, _, _ = run_simulation(rand_seq)

                        nw_path = [step["Net Worth"] for step in res]
                        all_nw_paths.append(nw_path)

                        if nw_path[-1] > 0 and min(nw_path) > 0: success_count += 1

                    success_rate = (success_count / mc_runs) * 100

                    path_len = len(all_nw_paths[0])
                    years_list = [df_sim.iloc[i]["Year"] for i in range(path_len)]
                    p10, p50, p90 = [], [], []

                    for i in range(path_len):
                        step_vals = sorted([path[i] for path in all_nw_paths])
                        discount = (1 + infl / 100) ** i if view_todays_dollars else 1.0
                        p10.append(step_vals[int(mc_runs * 0.10)] / discount)
                        p50.append(step_vals[int(mc_runs * 0.50)] / discount)
                        p90.append(step_vals[int(mc_runs * 0.90)] / discount)

                    st.markdown(
                        f"<h3 style='text-align: center; color: {'#10b981' if success_rate > 80 else '#f59e0b' if success_rate > 50 else '#f43f5e'};'>Probability of Success: {success_rate:.1f}%</h3>",
                        unsafe_allow_html=True)

                    if HAS_PLOTLY:
                        fig_mc = go.Figure()
                        fig_mc.add_trace(
                            go.Scatter(x=years_list, y=p90, mode='lines', name='90th Percentile (Favorable Timeline)',
                                       line=dict(color='#10b981', dash='dot')))
                        fig_mc.add_trace(
                            go.Scatter(x=years_list, y=p50, mode='lines', name='50th Percentile (Median Expectation)',
                                       line=dict(color='#3b82f6', width=3)))
                        fig_mc.add_trace(
                            go.Scatter(x=years_list, y=p10, mode='lines', name='10th Percentile (Severe Contraction)',
                                       line=dict(color='#f43f5e', dash='dot')))
                        fig_mc.update_layout(title="Stochastic Net Worth Projections", hovermode="x unified",
                                             yaxis=dict(tickformat="$,.0f"), margin=dict(l=0, r=0, t=40, b=0),
                                             legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right",
                                                         x=1))
                        st.plotly_chart(fig_mc, use_container_width=True)

            # --- DATA AUDIT TABLES ---
            st.divider()
            csv = df_sim.to_csv(index=False).encode('utf-8')
            st.download_button(label="📥 Download Full Simulation (.csv)", data=csv,
                               file_name='retirement_simulation.csv', mime='text/csv', type="secondary")

            t1, t2 = st.tabs(["Income & Expense Log", "Net Worth Log"])
            with t1:
                st.subheader("Detailed Tax & Expense Log")
                df_det = pd.DataFrame(detailed_results).fillna(0)
                inc_c = sorted([c for c in df_det.columns if c.startswith("Income:") or c.startswith("Roth")])
                exp_c = sorted([c for c in df_det.columns if c.startswith("Expense:")])
                ord_det = ["Year", "Age (Primary)"] + inc_c + exp_c + ["Net Savings"]
                st.dataframe(df_det[ord_det].set_index("Year").style.format(
                    {c: "${:,.0f}" for c in ord_det if c not in ["Age (Primary)", "Year"]} | {
                        "Age (Primary)": "{:.0f}"}), use_container_width=True)

            with t2:
                st.subheader("Detailed Net Worth Log")
                st.markdown(
                    "Track the exact, year-by-year balance of every single asset account and liability to trace your drawdowns and growth.")
                df_nw = pd.DataFrame(nw_detailed_results).fillna(0)
                ast_c = sorted([c for c in df_nw.columns if c.startswith("Asset:")])
                ord_nw = ["Year", "Age (Primary)"] + ast_c + ["Total Liquid Assets", "Total Real Estate Equity",
                                                              "Total Business Equity", "Total Debt Liabilities",
                                                              "Total Net Worth"]
                st.dataframe(df_nw[ord_nw].set_index("Year").style.format(
                    {c: "${:,.0f}" for c in ord_nw if c not in ["Age (Primary)", "Year"]} | {
                        "Age (Primary)": "{:.0f}"}), use_container_width=True)

# --- FINAL SAVE CORE ---
st.markdown("---")
st.markdown('<div class="main-save-btn-marker"></div>', unsafe_allow_html=True)
if st.button("🚀 Save Full Profile to Cloud Server", type="primary", use_container_width=True,
             key="save_main") or save_requested:
    if st.session_state['user_email'] == "guest_demo":
        st.error("Persistent configurations disabled within the demonstration environment.")
    else:
        def clean(df, k):
            if df.empty: return []
            rows = df[df[k].astype(str) != ""].to_dict('records')
            for r in rows:
                for vk, vv in r.items():
                    if pd.isna(vv): r[vk] = None
            return rows


        user_data = {
            "personal_info": {"name": my_name, "dob": my_dob.strftime("%Y-%m-%d"), "age": my_age, "retire_age": ret_age,
                              "spouse_retire_age": s_ret_age, "my_life_exp": my_life_exp_val,
                              "spouse_life_exp": spouse_life_exp_val, "current_city": curr_city,
                              "has_spouse": has_spouse,
                              "spouse_name": spouse_name,
                              "spouse_dob": spouse_dob.strftime("%Y-%m-%d") if has_spouse else None,
                              "spouse_age": spouse_age, "kids": kids_data},
            "retire_city": ret_city, "income": clean(edited_inc, "Description"),
            "real_estate": clean(edited_re, "Property Name"), "business": clean(edited_biz, "Business Name"),
            "liquid_assets": clean(edited_ast, "Account Name"), "liabilities": clean(edited_debt, "Debt Name"),
            "current_expenses": clean(edited_c, "Description"), "one_time_events": clean(edited_m, "Description"),
            "retire_expenses": clean(edited_r, "Description"),
            "assumptions": {**st.session_state['assumptions'], "inflation": infl, "inflation_healthcare": infl_hc,
                            "inflation_education": infl_ed, "market_growth": mkt, "income_growth": inc_g,
                            "property_growth": prop_g, "rent_growth": rent_g, "current_tax_rate": cur_t,
                            "retire_tax_rate": ret_t, "roth_conversions": roth_conversions, "roth_target": roth_target}
        }
        db.collection('users').document(st.session_state['user_email']).set(user_data, merge=True)
        st.session_state['user_data'] = user_data
        st.success("✅ Complete Financial Blueprint Synchronized Successfully!")