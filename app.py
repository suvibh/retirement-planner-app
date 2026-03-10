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
from concurrent.futures import ThreadPoolExecutor

try:
    import plotly.graph_objects as go

    HAS_PLOTLY = True
except ImportError:
    HAS_PLOTLY = False

# --- CONFIG & SUPPRESSION ---
warnings.simplefilter(action='ignore', category=FutureWarning)
st.set_page_config(page_title="AI Retirement Planner Pro", layout="wide", page_icon="🏦")

# --- GLOBAL CONSTANTS (2026 IRS Proxies) ---
SS_WAGE_BASE_2026 = 168600
ADDL_MED_TAX_THRESHOLD = 250000
IRA_LIMIT_BASE = 7000
PLAN_401K_LIMIT_BASE = 23500
CATCHUP_401K_BASE = 7500
CATCHUP_IRA_BASE = 1000
MEDICARE_GAP_COST = 15000
LTC_SHOCK_COST = 100000
SHORTFALL_PENALTY_RATE = 0.12  # 12% Annual Penalty on Unfunded Debt
WIDOW_EXPENSE_MULTIPLIER = 0.60
MEDICARE_CLIFF_SINGLE_DROP = 0.25  # 25% drop per spouse going on Medicare
ROTH_CASH_BUFFER_MARGIN = 0.95

# --- GOOGLE ANALYTICS INJECTION ---
GA_MEASUREMENT_ID = st.secrets.get("GA_MEASUREMENT_ID", "")
if GA_MEASUREMENT_ID:
    ga_script = f"""
    <!-- Google tag (gtag.js) -->
    <script async src="https://www.googletagmanager.com/gtag/js?id={GA_MEASUREMENT_ID}"></script>
    <script>
      window.dataLayer = window.dataLayer || [];
      function gtag(){{dataLayer.push(arguments);}}
      gtag('js', new Date());
      gtag('config', '{GA_MEASUREMENT_ID}');
    </script>
    """
    st.components.v1.html(ga_script, width=0, height=0)

# --- CUSTOM CSS FOR PREMIUM LOOK ---
st.markdown("""
<style>
    .stApp { background-color: #f8fafc; }
    h1, h2, h3 { color: #1e293b !important; font-family: 'Inter', sans-serif; font-weight: 800 !important; }
    [data-testid="stExpander"] { background-color: white !important; border: 1px solid #e2e8f0 !important; border-radius: 12px !important; box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.05) !important; margin-bottom: 1rem !important; }
    .stButton > button { border-radius: 8px !important; transition: all 0.2s ease !important; }

    [data-testid="stMetricValue"] { font-size: 1.8rem !important; font-weight: 700 !important; color: #4f46e5 !important; }
    .info-text { font-size: 0.95rem; color: #334155; margin-bottom: 15px; border-left: 4px solid #3b82f6; padding-left: 12px; background-color: #eff6ff; padding: 10px; padding-bottom: 12px; padding-right: 10px; border-radius: 0 8px 8px 0; line-height: 1.5;}

    [data-testid="stMetricValue"] { font-size: 1.6rem !important; }
    [data-testid="stMetricLabel"] { font-weight: 600 !important; color: #475569 !important; }

    /* Hide marker containers to prevent layout spacing issues */
    div[data-testid="element-container"]:has(.ai-btn-marker),
    div[data-testid="stElementContainer"]:has(.ai-btn-marker),
    div[data-testid="element-container"]:has(.main-save-btn-marker),
    div[data-testid="stElementContainer"]:has(.main-save-btn-marker) {
        display: none;
    }

    /* AI Button subtle styling */
    div[data-testid="element-container"]:has(.ai-btn-marker) + div[data-testid="element-container"] button,
    div[data-testid="stElementContainer"]:has(.ai-btn-marker) + div[data-testid="stElementContainer"] button {
        background: linear-gradient(90deg, #4f46e5 0%, #7c3aed 100%) !important;
        border: none !important;
        color: white !important;
        font-weight: 700 !important;
        border-radius: 8px !important;
        transition: all 0.2s ease !important;
        box-shadow: 0 4px 14px 0 rgba(79, 70, 229, 0.39) !important;
    }
    div[data-testid="element-container"]:has(.ai-btn-marker) + div[data-testid="element-container"] button:hover,
    div[data-testid="stElementContainer"]:has(.ai-btn-marker) + div[data-testid="stElementContainer"] button:hover {
        transform: translateY(-2px);
        box-shadow: 0 6px 20px 0 rgba(79, 70, 229, 0.39) !important;
    }

    /* Main Action Button (Bottom Save) */
    div[data-testid="element-container"]:has(.main-save-btn-marker) + div[data-testid="element-container"] button,
    div[data-testid="stElementContainer"]:has(.main-save-btn-marker) + div[data-testid="stElementContainer"] button {
        background: linear-gradient(90deg, #10b981 0%, #059669 100%) !important;
        color: white !important;
        border: none !important;
        font-weight: 700 !important;
        border-radius: 8px !important;
        box-shadow: 0 4px 14px 0 rgba(16, 185, 129, 0.39) !important;
        transition: all 0.2s ease !important;
    }
    div[data-testid="element-container"]:has(.main-save-btn-marker) + div[data-testid="element-container"] button:hover,
    div[data-testid="stElementContainer"]:has(.main-save-btn-marker) + div[data-testid="stElementContainer"] button:hover {
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


def call_gemini_json(prompt, retries=3):
    if not GEMINI_API_KEY:
        st.error("⚠️ GEMINI_API_KEY is missing in Streamlit Secrets. AI operations are temporarily disabled.")
        return None
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-preview-09-2025:generateContent?key={GEMINI_API_KEY}"
    payload = {"contents": [{"parts": [{"text": prompt}]}],
               "generationConfig": {"responseMimeType": "application/json"}}

    for attempt in range(retries):
        try:
            res = requests.post(url, json=payload).json()
            if "error" in res:
                if attempt == retries - 1:
                    st.error(f"⚠️ API Error: {res['error'].get('message')}")
                    return None
                time.sleep(2 ** attempt)
                continue

            text = res['candidates'][0]['content']['parts'][0]['text'].strip()
            text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
            text = re.sub(r"\n?```$", "", text).strip()
            parsed = json.loads(text)
            if isinstance(parsed, dict) and len(parsed) == 1 and isinstance(list(parsed.values())[0], list): return \
            list(parsed.values())[0]
            return parsed
        except Exception:
            time.sleep(2 ** attempt)
    return None


def subtract_years(dt, years):
    return dt - relativedelta(years=years)


def safe_num(val, default=0.0):
    try:
        if val is None: return default
        if pd.isna(val): return default
        if str(val).strip() == "": return default
        return float(val)
    except Exception:
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
    if st.button("🚀 Try the Demo (Guest Mode)", width="stretch"):
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
                                                                                                   "roth_target": "24%",
                                                                                                   "withdrawal_strategy": "Standard"})


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


def render_total(label, series):
    total = pd.to_numeric(series, errors='coerce').fillna(0).sum()
    st.markdown(
        f"<div style='text-align: right; font-weight: 600; color: #4f46e5; font-size: 1.1rem;'>{label}: <span style='color: #111827;'>${total:,.0f}</span></div>",
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
    if st.button("Log Out", width="stretch"):
        cookie_manager.delete("user_email")
        time.sleep(0.2)
        st.session_state.clear()
        st.rerun()

# --- 1. PERSONAL INFO ---
with st.expander("👨‍👩‍👧‍👦 1. Your Profile & Family Context", expanded=False):
    st.markdown(
        '<div class="info-text">💡 <strong>Why Date of Birth?</strong> Precision matters. Your exact birth year dictates your SECURE 2.0 RMD age (73 vs 75), IRS Catch-Up Contribution limits, and Social Security Full Retirement Age (FRA). Selecting "Include Spouse" activates the Married Filing Jointly (MFJ) Standard Deduction ($29,200) and wider tax brackets.</div>',
        unsafe_allow_html=True)

    tab_me, tab_spouse, tab_kids = st.tabs(["👤 About You", "💍 Spouse / Partner", "👶 Dependents"])

    with tab_me:
        c1, c2 = st.columns(2)
        my_name = c1.text_input("Your Name", value=p_info.get('name', ''))
        saved_dob = p_info.get('dob')
        default_dob = datetime.datetime.strptime(saved_dob, "%Y-%m-%d").date() if saved_dob else subtract_years(
            datetime.date.today(), int(p_info.get('age', 40)))
        my_dob = c2.date_input("Your Date of Birth", value=default_dob, min_value=datetime.date(1920, 1, 1),
                               max_value=datetime.date.today())
        my_age = relativedelta(datetime.date.today(), my_dob).years
        my_birth_year = my_dob.year

    with tab_spouse:
        has_spouse = st.checkbox("Include a Spouse or Partner? (Enables joint tax brackets)",
                                 value=p_info.get('has_spouse', False))
        spouse_name, spouse_dob, spouse_age, spouse_birth_year = "", None, 0, current_year
        if has_spouse:
            sc1, sc2 = st.columns(2)
            spouse_name = sc1.text_input("Spouse/Partner Name", value=p_info.get('spouse_name', ''))
            s_saved_dob = p_info.get('spouse_dob')
            s_default_dob = datetime.datetime.strptime(s_saved_dob,
                                                       "%Y-%m-%d").date() if s_saved_dob else subtract_years(
                datetime.date.today(), int(p_info.get('spouse_age', 40)))
            spouse_dob = sc2.date_input("Spouse Date of Birth", value=s_default_dob,
                                        min_value=datetime.date(1920, 1, 1), max_value=datetime.date.today())
            spouse_age = relativedelta(datetime.date.today(), spouse_dob).years
            spouse_birth_year = spouse_dob.year

    with tab_kids:
        saved_kids = p_info.get('kids', [])
        num_kids = st.number_input("Number of Kids/Dependents", 0, 10, len(saved_kids))
        kids_data = []
        if num_kids > 0: st.write("**Kids' Details**")
        for i in range(num_kids):
            k1, k2 = st.columns([3, 1])
            kn = k1.text_input(f"Child {i + 1} Name", value=saved_kids[i]['name'] if i < len(saved_kids) else "",
                               key=f"kn_{i}")
            ka = k2.number_input(f"Age {i + 1}", 0, 25, saved_kids[i]['age'] if i < len(saved_kids) else 5,
                                 key=f"ka_{i}")
            kids_data.append({"name": kn, "age": ka})

# --- 2. INCOME ---
with st.expander("💵 2. Your Income Streams", expanded=False):
    st.markdown(
        '<div class="info-text">💡 <strong>Employer Match Note:</strong> Employer 401(k) matches are considered part of your total compensation, but are <strong>not</strong> spendable cash income. Professionally, you should list the match here for visibility. The engine will intelligently auto-deposit it into your assets so your balances grow correctly.</div>',
        unsafe_allow_html=True)

    df_inc = pd.DataFrame(st.session_state.get('income', ud.get('income', [])))
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

    # Validation Warning for missing End Years to prevent infinite income loops
    for idx, inc in edited_inc.iterrows():
        if not inc.get("Stop at Ret.?") and (pd.isna(inc.get("End Year")) or str(inc.get("End Year")).strip() == ""):
            st.warning(
                f"⚠️ Income '{inc.get('Description', 'Unknown')}': 'Stop at Retirement' is unchecked, but no 'End Year' is provided. This income will continue indefinitely until the simulation ends.")

    render_total("Total Pre-Tax Income", edited_inc['Annual Amount ($)'])

    col_ai_inc, _ = st.columns([3, 1])
    with col_ai_inc:
        st.markdown('<div class="ai-btn-marker"></div>', unsafe_allow_html=True)
        if st.button("✨ Auto-Estimate My Social Security (AI)", width="stretch"):
            st.session_state['income'] = edited_inc.to_dict('records')  # Save UI state before rerun

            with st.spinner("Asking AI to estimate your Social Security benefits based on your age and income..."):
                curr_inc = pd.to_numeric(edited_inc['Annual Amount ($)'], errors='coerce').fillna(0).sum()
                if has_spouse:
                    prompt = f"User is {my_age} years old making ${curr_inc}/year. Spouse is {spouse_age} years old. Estimate realistic annual Social Security primary insurance amounts (PIA) at Full Retirement Age for both. Return JSON: {{'ss_amount_me': integer, 'ss_amount_spouse': integer}}"
                else:
                    prompt = f"User is {my_age} years old making ${curr_inc}/year. Estimate their annual Social Security primary insurance amount (PIA) at Full Retirement Age. Return JSON: {{'ss_amount_me': integer}}"
                res = call_gemini_json(prompt)
                if res:
                    current_inc = df_inc.to_dict('records')
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
                    st.session_state['income'] = current_inc
                    st.rerun()

# --- 3. ASSETS, LIABILITIES & NET WORTH ---
with st.expander("🏦 3. Assets, Debts & Net Worth", expanded=False):
    tab_re, tab_biz, tab_ast, tab_debt = st.tabs(
        ["🏢 Real Estate", "💼 Business Interests", "🏦 Liquid Assets", "💳 Debts & Loans"])

    with tab_re:
        st.markdown(
            '<div class="info-text">💡 <strong>Smart Mortgages:</strong> Just tell us your loan balance, interest rate, and monthly payment. The math engine automatically pays down your loan over time and drops the expense entirely once it hits zero! <em>(Make sure you don\'t list your mortgage again in the budget section below).</em></div>',
            unsafe_allow_html=True)
        df_re = pd.DataFrame(ud.get('real_estate', []))
        if df_re.empty:
            df_re = pd.DataFrame(
                [{"Property Name": "Primary Home", "Is Primary Residence?": True, "Market Value ($)": 0,
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
                "Property Name": st.column_config.TextColumn("Property Name",
                                                             help="A simple label for your property (e.g. 'Beach House' or 'Main St')."),
                "Is Primary Residence?": st.column_config.CheckboxColumn("Primary Home?", default=False),
                "Market Value ($)": st.column_config.NumberColumn("Market Value ($)", step=10000, format="$%d"),
                "Mortgage Balance ($)": st.column_config.NumberColumn("Mortgage Balance ($)", step=10000, format="$%d"),
                "Interest Rate (%)": st.column_config.NumberColumn("Interest Rate (%)", step=0.001, format="%.3f%%"),
                "Mortgage Payment ($)": st.column_config.NumberColumn("Monthly P&I ($)", step=100, format="$%d"),
                "Monthly Expenses ($)": st.column_config.NumberColumn("Taxes/Ins/HOA ($)", step=100, format="$%d"),
                "Monthly Rent ($)": st.column_config.NumberColumn("Monthly Rent ($)", step=100, format="$%d"),
                "Override Prop Growth (%)": st.column_config.NumberColumn("Property Growth (%)", step=0.1,
                                                                          format="%.1f%%"),
                "Override Rent Growth (%)": st.column_config.NumberColumn("Rent Growth (%)", step=0.1, format="%.1f%%")
            }, num_rows="dynamic", width="stretch", hide_index=True, key="re_editor"
        )

        # Validation Warning: Check if mortgage payments cover interest
        for idx, r in edited_re.iterrows():
            bal = safe_num(r.get('Mortgage Balance ($)'))
            rate = safe_num(r.get('Interest Rate (%)'))
            pmt = safe_num(r.get('Mortgage Payment ($)'))
            if bal > 0 and rate > 0 and pmt > 0:
                monthly_interest = (bal * (rate / 100.0)) / 12.0
                if pmt < monthly_interest:
                    st.warning(
                        f"⚠️ Property '{r.get('Property Name', 'Unknown')}': Your monthly payment (${pmt:,.0f}) is less than the monthly interest generated (${monthly_interest:,.0f}). This loan balance will grow forever.")

    with tab_biz:
        df_biz = pd.DataFrame(ud.get('business', []))
        if df_biz.empty:
            df_biz = pd.DataFrame([{"Business Name": "", "Total Valuation ($)": 0, "Your Ownership (%)": 100,
                                    "Annual Distribution ($)": 0, "Override Val. Growth (%)": None,
                                    "Override Dist. Growth (%)": None}])
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
                "Override Val. Growth (%)": st.column_config.NumberColumn("Value Growth (%)", step=0.1, format="%.1f%%",
                                                                          help="This private asset will inherently not follow the portfolio glidepath. Use this to set a static growth rate."),
                "Override Dist. Growth (%)": st.column_config.NumberColumn("Income Growth (%)", step=0.1,
                                                                           format="%.1f%%")
            }, num_rows="dynamic", width="stretch", hide_index=True, key="biz_editor"
        )

    with tab_ast:
        st.markdown(
            '<div class="info-text">💡 <strong>Contribution Engine Update:</strong> Put ONLY your own out-of-pocket contributions here. The AI engine automatically detects "Employer Matches" from your Income table and securely routes them directly into your 401(k) behind the scenes!</div>',
            unsafe_allow_html=True)
        df_ast = pd.DataFrame(ud.get('liquid_assets', []))
        if df_ast.empty:
            df_ast = pd.DataFrame([{"Account Name": "Primary 401(k)", "Type": "Traditional 401(k)", "Owner": "Me",
                                    "Current Balance ($)": 0, "Annual Contribution ($/yr)": 0,
                                    "Est. Annual Growth (%)": None, "Stop Contrib at Ret.?": True}])
        else:
            if "Annual Contribution ($)" in df_ast.columns: df_ast.rename(
                columns={'Annual Contribution ($)': 'Annual Contribution ($/yr)'}, inplace=True)
            if "Stop Contrib at Ret.?" not in df_ast.columns: df_ast["Stop Contrib at Ret.?"] = True

            # Smoothly migrate legacy types
            df_ast['Type'] = df_ast['Type'].replace(
                {'Traditional 401k/IRA': 'Traditional 401(k)', 'Roth 401k/IRA': 'Roth 401(k)'})
            df_ast = df_ast.reindex(
                columns=["Account Name", "Type", "Owner", "Current Balance ($)", "Annual Contribution ($/yr)",
                         "Est. Annual Growth (%)", "Stop Contrib at Ret.?"])

        edited_ast = st.data_editor(
            df_ast,
            column_config={
                "Type": st.column_config.SelectboxColumn("Account Type",
                                                         options=["Checking/Savings", "HYSA", "Brokerage (Taxable)",
                                                                  "Traditional 401(k)", "Traditional IRA",
                                                                  "Roth 401(k)", "Roth IRA", "HSA", "Crypto",
                                                                  "529 Plan", "Other"]),
                "Owner": st.column_config.SelectboxColumn("Whose Account?", options=["Me", "Spouse", "Joint"]),
                "Current Balance ($)": st.column_config.NumberColumn("Current Balance ($)", step=5000, format="$%d"),
                "Annual Contribution ($/yr)": st.column_config.NumberColumn("Your Contributions ($/yr)", step=1000,
                                                                            format="$%d",
                                                                            help="Include ONLY your out-of-pocket contributions. The AI engine automatically adds Employer Matches from your Income table."),
                "Est. Annual Growth (%)": st.column_config.NumberColumn("Custom Return (%)", format="%.1f%%",
                                                                        help="Leave blank to use global market growth assumptions."),
                "Stop Contrib at Ret.?": st.column_config.CheckboxColumn("Stop Adding at Ret.?",
                                                                         help="Check this if you will stop saving into this account once the owner retires.")
            }, num_rows="dynamic", width="stretch", hide_index=True, key="assets_editor"
        )

        # Validation Warning: Check if 401k/IRA contributions wildly exceed normal limits
        for idx, a in edited_ast.iterrows():
            if a.get('Type') in ['Traditional 401(k)', 'Roth 401(k)', 'Traditional IRA', 'Roth IRA']:
                contrib = safe_num(a.get('Annual Contribution ($/yr)'))
                if contrib > 31500:  # Broad threshold covering catchups
                    st.warning(
                        f"⚠️ Account '{a.get('Account Name', 'Unknown')}': Contribution of ${contrib:,.0f}/yr exceeds standard IRS maximums. The simulation engine will automatically cap these contributions to legal limits for accuracy.")

    with tab_debt:
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

# --- AI CONTEXT PREP ---
k_ctx_list = [f"{k['name']}:{k['age']}" for k in kids_data]
k_ctx_str = ", ".join(k_ctx_list)

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
    f", Spouse({spouse_name}:{spouse_age})" if has_spouse else "") + f", Dependents({k_ctx_str})"
budget_categories = ["Housing / Rent", "Transportation", "Food", "Utilities", "Insurance", "Healthcare",
                     "Entertainment", "Education", "Personal Care", "Subscriptions", "Travel", "Debt Payments", "Other"]

# --- 4. LIFESTYLE CASH FLOWS ---
with st.expander("💸 4. Lifetime Cash Flows (Budgets & Milestones)", expanded=False):
    st.markdown(
        '<div class="info-text">💡 <strong>Using the Cash Flow Engine:</strong> We highly recommend clicking the <strong>✨ Auto-Estimate Budget & Milestones (AI)</strong> button below first. The AI will generate a complete baseline of lifetime expenses and milestones based on your family profile, current city, and retirement city. Once populated, you can add your own custom rows, modify the AI\'s amounts, or delete anything that doesn\'t fit your lifestyle.<br><br><strong>Healthcare Note:</strong> Assume you are covered by employer-sponsored healthcare while working. The simulation engine automatically builds in major post-retirement medical costs (like Pre-Medicare coverage gaps, Medicare premium cliffs at age 65, IRMAA surcharges, and Long-Term Care). You only need to enter modest baseline out-of-pocket costs for healthcare here.</div>',
        unsafe_allow_html=True)

    c_loc1, c_loc2 = st.columns(2)
    with c_loc1:
        curr_city_flow = city_autocomplete("Current City", "curr_city_flow", default_val=p_info.get('current_city', ''))
    with c_loc2:
        ret_city_flow = city_autocomplete("Retirement City (Optional)", "retire_city_flow",
                                          default_val=ud.get('retire_city', curr_city_flow))

    st.divider()

    if 'lifetime_expenses' not in st.session_state:
        migrated = []
        for c in ud.get('current_expenses', []):
            if c.get("Description"):
                migrated.append({"Description": c.get("Description"), "Category": c.get("Category", "Other"),
                                 "Frequency": c.get("Frequency", "Monthly"), "Amount ($)": c.get("Amount ($)", 0),
                                 "Start Phase": "Now", "Start Year": None, "End Phase": "At Retirement",
                                 "End Year": None, "AI Estimate?": c.get("AI Estimate?", False)})
        for r in ud.get('retire_expenses', []):
            if r.get("Description"):
                migrated.append({"Description": r.get("Description"), "Category": r.get("Category", "Other"),
                                 "Frequency": r.get("Frequency", "Monthly"), "Amount ($)": r.get("Amount ($)", 0),
                                 "Start Phase": "At Retirement", "Start Year": None, "End Phase": "End of Life",
                                 "End Year": None, "AI Estimate?": r.get("AI Estimate?", False)})
        for m in ud.get('one_time_events', []):
            if m.get("Description"):
                try:
                    sy_int = int(str(m.get("Start Date (MM/YYYY)", "")).split('/')[-1])
                except:
                    sy_int = current_year
                try:
                    ey_int = int(str(m.get("End Date (MM/YYYY)", "")).split('/')[-1])
                except:
                    ey_int = sy_int
                migrated.append({"Description": m.get("Description"), "Category": "Other",
                                 "Frequency": m.get("Frequency", "One-Time"), "Amount ($)": m.get("Amount ($)", 0),
                                 "Start Phase": "Custom Year", "Start Year": sy_int, "End Phase": "Custom Year",
                                 "End Year": ey_int, "AI Estimate?": m.get("AI Estimate?", False)})

        if not migrated:
            migrated = [{"Description": "Groceries", "Category": "Food", "Frequency": "Monthly", "Amount ($)": 0,
                         "Start Phase": "Now", "Start Year": None, "End Phase": "End of Life", "End Year": None,
                         "AI Estimate?": False}]
        st.session_state['lifetime_expenses'] = migrated

    df_exp = pd.DataFrame(st.session_state['lifetime_expenses'])

    # Force clean nulls for non-custom phases to keep UI clean
    if not df_exp.empty:
        if 'Start Phase' in df_exp.columns and 'Start Year' in df_exp.columns:
            df_exp.loc[df_exp['Start Phase'] != 'Custom Year', 'Start Year'] = None
        if 'End Phase' in df_exp.columns and 'End Year' in df_exp.columns:
            df_exp.loc[df_exp['End Phase'] != 'Custom Year', 'End Year'] = None

    edited_exp = st.data_editor(
        df_exp,
        column_config={
            "Description": st.column_config.TextColumn("Description"),
            "Category": st.column_config.SelectboxColumn("Category", options=budget_categories),
            "Frequency": st.column_config.SelectboxColumn("Frequency", options=["Monthly", "Yearly", "One-Time"]),
            "Amount ($)": st.column_config.NumberColumn("Amount ($)", step=100, format="$%d"),
            "Start Phase": st.column_config.SelectboxColumn("Starts", options=["Now", "At Retirement", "Custom Year"]),
            "Start Year": st.column_config.NumberColumn("Start Year (If Custom)", format="%d", min_value=1900,
                                                        max_value=2100),
            "End Phase": st.column_config.SelectboxColumn("Ends",
                                                          options=["End of Life", "At Retirement", "Custom Year"]),
            "End Year": st.column_config.NumberColumn("End Year (If Custom)", format="%d", min_value=1900,
                                                      max_value=2100),
            "AI Estimate?": st.column_config.CheckboxColumn("🤖 AI?")
        }, num_rows="dynamic", width="stretch", hide_index=True, key="exp_ed"
    )

    col_ai_cb, _ = st.columns([3, 1])
    with col_ai_cb:
        st.markdown('<div class="ai-btn-marker"></div>', unsafe_allow_html=True)
        if st.button("✨ Auto-Estimate Budget & Milestones for selected current and future locations (AI)",
                     width="stretch"):
            # Save UI state before rerun
            st.session_state['lifetime_expenses'] = edited_exp.to_dict('records')

            with st.spinner("Analyzing localized CPI data, timelines, and family needs..."):
                valid = edited_exp[edited_exp["Description"].astype(str) != ""].copy()
                locked = valid[valid["AI Estimate?"] == False].to_dict('records')
                locked_desc = [x['Description'] for x in locked]
                wealth_ctx = f"The household has a current annual pre-tax income of ${curr_inc_total:,.0f} and liquid assets totaling ${liq_ast_total:,.0f}. VERY IMPORTANT: While you should scale the budget to reflect this wealth, assume these users are savvy spenders and aggressive savers (comfortable but smart with money), so avoid over-inflating lifestyle costs unnecessarily."
                allowed_cats = ", ".join(budget_categories)
                prompt = f"Current City: {curr_city_flow}. Planned Retirement City: {ret_city_flow}. Family: {f_ctx}. Current Year is {current_year}. {wealth_ctx} Generate a comprehensive list of missing living expenses AND expected future life milestones (like college or weddings). {ai_exclusion} CRITICAL INSTRUCTIONS: 1) Medical expenses (IRMAA, Medicare Cliff, Pre-Medicare gap, LTC) are handled automatically by the simulation engine; only provide modest baseline out-of-pocket healthcare costs. 2) Model 'Empty Nesting': phase out child-heavy groceries, utility expenses, and ANY K-12 extracurriculars/lessons using 'Custom Year' End Phases exactly when the youngest child turns 18. 3) ALL College/University expenses MUST be categorized strictly as 'Education' (not 'Other') so they receive the 5% education inflation penalty. NOTE: Start and End Years are INCLUSIVE. For a standard 4-year college, the End Year must be exactly 3 years after the Start Year (e.g., Start 2032, End 2035 is 4 years). 4) Model Retirement Lifestyle Phases: split travel and entertainment into 'Go-Go Years' (high spend, starts at retirement, lasts 10 years, calculate costs based on {ret_city_flow}), 'Slow-Go Years' (medium spend, lasts next 10 years), and 'No-Go Years' (low spend) using 'Custom Year' Start/End phases. 5) STRICT PHASE SHIFTING: Never overlap the same living expense category. If an expense changes at retirement, the 'Now' version MUST have 'End Phase' set to 'At Retirement', and the new version MUST have 'Start Phase' set to 'At Retirement'. If an expense continues unchanged forever, set it to 'Now' until 'End of Life'. Skip these items as they are already accounted for: {json.dumps(locked_desc)}. Return ONLY a JSON array of objects with keys: 'Description', 'Category' (MUST be exactly one of: {allowed_cats}. If unsure, default to 'Other'), 'Frequency' (Monthly/Yearly/One-Time), 'Amount ($)' (number), 'Start Phase' (Now/At Retirement/Custom Year), 'Start Year' (integer, ONLY if 'Start Phase' is 'Custom Year', otherwise null), 'End Phase' (End of Life/At Retirement/Custom Year), 'End Year' (integer, ONLY if 'End Phase' is 'Custom Year', otherwise null), and 'AI Estimate?' (true)."
                res = call_gemini_json(prompt)
                if res and isinstance(res, list) and len(res) > 0:
                    st.session_state['lifetime_expenses'] = locked + res
                    st.rerun()
                else:
                    st.error("⚠️ AI returned an invalid format. Please try again.")

# --- 5. INTERACTIVE DASHBOARD & SIMULATION ---
with st.expander("📈 5. Interactive Retirement Simulation & Analytics", expanded=True):
    st.markdown("### 🎛️ Simulation Command Center")
    tab_time, tab_macro, tab_adv = st.tabs(["⏳ Timelines", "📊 Macro & Taxes", "⚙️ Advanced Scenarios"])

    with tab_time:
        cc1, cc2, cc3, cc4 = st.columns(4)
        ret_age = cc1.slider("Retirement Age", max(int(my_age), 1), 100,
                             max(int(my_age), int(p_info.get('retire_age', 65))))
        s_ret_age = cc2.slider("Spouse Retire Age", max(int(spouse_age), 1), 100,
                               max(int(spouse_age), int(p_info.get('spouse_retire_age', 65)))) if has_spouse else 65
        my_life_exp = cc3.slider("Your Life Expectancy", max(70, ret_age), 115,
                                 max(ret_age, int(p_info.get('my_life_exp', 95))))
        spouse_life_exp = cc4.slider("Spouse Life Expectancy", max(70, s_ret_age), 115,
                                     max(s_ret_age, int(p_info.get('spouse_life_exp', 95)))) if has_spouse else None

    # --- ASSUMPTIONS BLOCK ---
    with tab_macro:
        st.markdown(
            '<div class="info-text">💡 <strong>AI Estimation:</strong> Click the ✨ AI button next to any field to have the AI estimate a realistic, localized value based on historical data and your profile!</div>',
            unsafe_allow_html=True)


        def ai_number_input(label, state_key, default_val, prompt, col):
            with col:
                sub_c1, sub_c2 = st.columns([5, 2])

                widget_key = f"in_{state_key}"
                input_placeholder = sub_c1.empty()

                # CSS trick to align the button with the input field label
                sub_c2.markdown("<div style='height: 28px;'></div>", unsafe_allow_html=True)
                sub_c2.markdown('<div class="ai-btn-marker"></div>', unsafe_allow_html=True)

                if sub_c2.button("✨ AI", key=f"btn_{state_key}", help=f"AI Estimate for {label}", width="stretch"):
                    with st.spinner("AI estimating..."):
                        enhanced_prompt = prompt + " CRITICAL INSTRUCTION: You MUST return the value as a percentage number between 0 and 100 (e.g., return 5.5 for 5.5%, DO NOT return 0.055)."
                        res = call_gemini_json(enhanced_prompt)
                        if res and state_key in res:
                            new_val = float(res[state_key])
                            if 0 < new_val < 0.30: new_val *= 100.0
                            st.session_state['assumptions'][state_key] = new_val
                            st.session_state[widget_key] = new_val
                            st.rerun()

                if widget_key not in st.session_state:
                    st.session_state[widget_key] = float(st.session_state['assumptions'].get(state_key, default_val))

                val = input_placeholder.number_input(label, step=0.1, key=widget_key)
                st.session_state['assumptions'][state_key] = val
                return val


        ac1, ac2, ac3 = st.columns(3)
        mkt = ai_number_input("Market Growth (%)", 'market_growth', 7.5,
                              f"What is a realistic conservative long-term annual market growth rate for a diversified retirement portfolio? Return JSON: {{'market_growth': float}}",
                              ac1)
        infl = ai_number_input("General CPI Inflation (%)", 'inflation', 2.5,
                               f"What is the projected long-term average general US CPI inflation rate? Return JSON: {{'inflation': float}}",
                               ac2)
        inc_g = ai_number_input("Income Growth (%)", 'income_growth', 3.2,
                                f"What is a realistic annual salary growth/merit increase rate? Return JSON: {{'income_growth': float}}",
                                ac3)

        ac4, ac5, ac6 = st.columns(3)
        infl_hc = ai_number_input("Healthcare Inflation (%)", 'inflation_healthcare', 5.5,
                                  f"What is the projected long-term annual healthcare cost inflation rate in the US? Return JSON: {{'inflation_healthcare': float}}",
                                  ac4)
        infl_ed = ai_number_input("Education Inflation (%)", 'inflation_education', 4.5,
                                  f"What is the projected long-term annual college tuition inflation rate in the US? Return JSON: {{'inflation_education': float}}",
                                  ac5)
        prop_g = ai_number_input("Property Growth (%)", 'property_growth', 2.5,
                                 f"Historical average annual real estate appreciation rate for {curr_city_flow}? Return JSON: {{'property_growth': float}}",
                                 ac6)

        ac7, ac8, ac9 = st.columns(3)
        rent_g = ai_number_input("Rent Growth (%)", 'rent_growth', 3.0,
                                 f"Projected average annual rent increase rate for {curr_city_flow}? Return JSON: {{'rent_growth': float}}",
                                 ac7)
        cur_t = ai_number_input("Current State Tax (%)", 'current_tax_rate', 5.0,
                                f"User lives in {curr_city_flow} with ${curr_inc_total:,.0f} income. Suggest effective STATE/LOCAL income tax rate ONLY. Return JSON: {{'current_tax_rate': float}}",
                                ac8)

        ret_city_state = st.session_state.get('retire_city_flow', ret_city_flow)
        ret_t = ai_number_input("Retire State Tax (%)", 'retire_tax_rate', 0.0,
                                f"User plans to retire in {ret_city_state} with estimated retirement income. Suggest effective STATE/LOCAL income tax rate ONLY. Return JSON: {{'retire_tax_rate': float}}",
                                ac9)

    with tab_adv:
        st.markdown(
            '<div class="info-text">💡 <strong>Tax Engine & Stress Tests:</strong> Our engine uses 2026 IRS tax brackets and dynamically calculates Federal, State, and FICA taxes. It also integrates Medicare IRMAA surcharges, Capital Gains Step-Up Basis on death, and Spousal Social Security survivor benefits.</div>',
            unsafe_allow_html=True)
        sc1, sc2 = st.columns(2)
        with sc1:
            st.write("**Simulation Stressors**")
            medicare_gap = st.toggle("🏥 Model Pre-Medicare Gap", value=True,
                                     help="If retiring before 65, adds a significant Private Health Insurance expense until Medicare eligibility.")
            medicare_cliff = st.toggle("🏥 Apply Medicare Cliff (Drop Healthcare at 65)", value=True,
                                       help="Automatically reduces 'Healthcare' budget line items by 50% when you turn 65 to simulate Medicare kicking in.")
            glidepath = st.toggle("📉 Apply Investment Glidepath", value=True,
                                  help="Reduces the Market Growth rate by 1% for every 5 years you are into retirement on your Traditional 401(k)s and Brokerages, simulating a shift to bonds.")
            stress_test = st.toggle("📉 Apply -25% Market Crash at Retirement", value=False,
                                    help="Simulates a severe Sequence of Returns Risk by dropping your portfolio by exactly -25% in the first year of your retirement.")
            ltc_shock = st.toggle("🛏️ Long-Term Care (LTC) Shock", value=False,
                                  help="Injects a massive $100k/yr medical expense into the final 3 years of your simulated life expectancy.")

        with sc2:
            st.write("**Tax & Withdrawal Optimization**")
            active_withdrawal_strategy = st.selectbox("Shortfall Withdrawal Sequence",
                                                      options=["Standard (Taxable -> 401k -> Roth)",
                                                               "Roth Preferred (Taxable -> Roth -> 401k)"],
                                                      index=0 if "Standard" in st.session_state['assumptions'].get(
                                                          'withdrawal_strategy', 'Standard') else 1,
                                                      help="Determines which retirement accounts are drained first when your cash and taxable brokerages are empty.")

            roth_conversions = st.toggle("🔄 Enable Roth Conversion Optimizer",
                                         value=st.session_state['assumptions'].get('roth_conversions', False),
                                         help="Automatically converts Traditional 401(k) funds to Roth during low-income years to minimize lifetime RMD taxes. The AI will only convert what you can afford to pay taxes on out of your current cash/brokerage accounts.")
            roth_target_idx = ["12%", "22%", "24%", "32%"].index(
                st.session_state['assumptions'].get('roth_target', "24%"))
            roth_target = st.selectbox("Target Bracket to Fill", options=["12%", "22%", "24%", "32%"],
                                       index=roth_target_idx,
                                       help="The AI will convert just enough Traditional funds each year to reach the very top of this selected tax bracket.")

            # Record toggles to state
            st.session_state['assumptions']['roth_conversions'] = roth_conversions
            st.session_state['assumptions']['roth_target'] = roth_target
            st.session_state['assumptions']['withdrawal_strategy'] = active_withdrawal_strategy.split(' ')[0]

    st.divider()

    view_todays_dollars = st.toggle("💵 View Charts in Today's Dollars", value=False,
                                    help="Removes the effect of inflation so you can easily understand what these big future numbers feel like today.")

    # --- SIMULATION ENGINE ---
    if my_age == 0:
        st.warning("Please enter a valid Date of Birth to generate the simulation.")
    else:

        # --- PROGRESSIVE IRS FEDERAL TAX CALCULATOR ---
        def calc_federal_tax(ordinary_income, is_mfj, year_offset, inflation_rate):
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

            return ord_tax, marginal_rate


        # --- LONG TERM CAPITAL GAINS TAX CALCULATOR ---
        def get_ltcg_rate(ordinary_income, is_mfj, year_offset, inflation_rate):
            infl_factor = (1 + inflation_rate / 100) ** year_offset
            niit_threshold = ADDL_MED_TAX_THRESHOLD * infl_factor
            cg_threshold_0 = (94050 if is_mfj else 47025) * infl_factor
            cg_threshold_15 = (583750 if is_mfj else 518900) * infl_factor

            if ordinary_income < cg_threshold_0:
                base_rate = 0.0
            elif ordinary_income < cg_threshold_15:
                base_rate = 0.15
            else:
                base_rate = 0.20

            niit = 0.038 if ordinary_income > niit_threshold else 0.0
            return base_rate + niit


        # --- SOCIAL SECURITY FRA MATH ---
        def get_ss_multi(birth_year, claim_year):
            fra = 67 if birth_year >= 1960 else (66 + (min(birth_year - 1954, 10) / 12.0) if birth_year >= 1955 else 66)
            claim_age = claim_year - birth_year
            if claim_age < fra:
                months_early = (fra - claim_age) * 12
                if months_early <= 36:
                    return 1.0 - (months_early * (5 / 9 * 0.01))
                else:
                    return 1.0 - (36 * (5 / 9 * 0.01)) - ((months_early - 36) * (5 / 12 * 0.01))
            elif claim_age > fra:
                months_late = min((claim_age - fra) * 12, (70 - fra) * 12)  # Strict cap at age 70
                return 1.0 + (months_late * (2 / 3 * 0.01))
            return 1.0


        irs_uniform_table = {73: 26.5, 74: 25.5, 75: 24.6, 76: 23.7, 77: 22.9, 78: 22.0, 79: 21.1, 80: 20.2, 81: 19.4,
                             82: 18.5, 83: 17.7, 84: 16.8, 85: 16.0, 86: 15.2, 87: 14.4, 88: 13.7, 89: 12.9, 90: 12.2,
                             91: 11.5, 92: 10.8, 93: 10.1, 94: 9.5, 95: 8.9, 96: 8.4, 97: 7.8, 98: 7.3, 99: 6.8,
                             100: 6.4, 101: 6.0, 102: 5.6, 103: 5.2, 104: 4.9, 105: 4.6, 106: 4.3, 107: 4.1, 108: 3.9,
                             109: 3.7, 110: 3.5, 111: 3.4, 112: 3.3, 113: 3.1, 114: 3.0, 115: 2.9, 116: 2.8, 117: 2.7,
                             118: 2.5, 119: 2.3, 120: 2.0}

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

        # Pre-convert DFs to optimize inner loop performance
        inc_records = edited_inc.to_dict('records')
        exp_records = edited_exp.to_dict('records')
        ast_records = edited_ast.to_dict('records')

        # Filter purely empty debt records to prevent zero-balances continuously entering amortization logic
        debt_records = [d for d in edited_debt.to_dict('records') if
                        d.get("Debt Name") and safe_num(d.get("Current Balance ($)")) > 0]
        re_records = edited_re.to_dict('records')
        biz_records = edited_biz.to_dict('records')

        # Package environment context to entirely decouple the simulation loop for ThreadPoolExecutor
        sim_ctx = {
            'current_year': current_year, 'my_birth_year': my_birth_year, 'spouse_birth_year': spouse_birth_year,
            'primary_end_year': primary_end_year, 'spouse_end_year': spouse_end_year, 'has_spouse': has_spouse,
            'primary_retire_year': primary_retire_year, 'spouse_retire_year': spouse_retire_year,
            'primary_rmd_age': primary_rmd_age, 'spouse_rmd_age': spouse_rmd_age, 'mkt': mkt, 'infl': infl,
            'infl_hc': infl_hc, 'infl_ed': infl_ed, 'inc_g': inc_g, 'prop_g': prop_g, 'rent_g': rent_g,
            'cur_t': cur_t, 'ret_t': ret_t, 'stress_test': stress_test, 'glidepath': glidepath,
            'medicare_gap': medicare_gap, 'medicare_cliff': medicare_cliff, 'ltc_shock': ltc_shock,
            'roth_conversions': roth_conversions, 'roth_target': roth_target,
            'active_withdrawal_strategy': active_withdrawal_strategy,
            'owns_home': owns_home, 'kids_data': kids_data, 'max_years': max_years, 'max_year': max_year,
            'my_life_exp_val': my_life_exp_val, 'spouse_life_exp_val': spouse_life_exp_val,
            'ast_records': ast_records, 'debt_records': debt_records, 're_records': re_records,
            'biz_records': biz_records, 'inc_records': inc_records, 'exp_records': exp_records
        }


        # --- CORE SIMULATION ENGINE ---
        def run_simulation(mkt_sequence, ctx):

            # Base State Initialization (Fresh for every run)
            sim_assets = [{"Account Name": a.get("Account Name"), "Type": a.get("Type"), "Owner": a.get("Owner", "Me"),
                           "bal": safe_num(a.get("Current Balance ($)")),
                           "contrib": safe_num(a.get("Annual Contribution ($/yr)")),
                           "growth": a.get("Est. Annual Growth (%)"),
                           "stop_at_ret": a.get("Stop Contrib at Ret.?", True)} for a in ctx['ast_records'] if
                          a.get("Account Name")]
            if not sim_assets: sim_assets = [
                {"Account Name": "Unallocated Cash", "Type": "Checking/Savings", "Owner": "Me", "bal": 0.0,
                 "contrib": 0.0, "growth": 0.0, "stop_at_ret": False}]

            sim_debts = [
                {"bal": safe_num(d.get("Current Balance ($)")), "pmt": safe_num(d.get("Monthly Payment ($)")) * 12,
                 "rate": safe_num(d.get("Interest Rate (%)")) / 100, "name": d.get("Debt Name")} for d in
                ctx['debt_records'] if d.get("Debt Name")]
            sim_re = [{"name": r.get("Property Name", "Property"), "is_primary": r.get("Is Primary Residence?", False),
                       "val": safe_num(r.get("Market Value ($)")), "debt": safe_num(r.get("Mortgage Balance ($)")),
                       "pmt": safe_num(r.get("Mortgage Payment ($)")) * 12,
                       "exp": safe_num(r.get("Monthly Expenses ($)")) * 12,
                       "rent": safe_num(r.get("Monthly Rent ($)")) * 12,
                       "v_growth": float(r.get("Override Prop Growth (%)")) if pd.notna(
                           r.get("Override Prop Growth (%)")) and str(
                           r.get("Override Prop Growth (%)")).strip() != "" else ctx['prop_g'],
                       "r_growth": float(r.get("Override Rent Growth (%)")) if pd.notna(
                           r.get("Override Rent Growth (%)")) and str(
                           r.get("Override Rent Growth (%)")).strip() != "" else ctx['rent_g'],
                       "rate": safe_num(r.get("Interest Rate (%)")) / 100} for r in ctx['re_records'] if
                      r.get("Property Name")]
            sim_biz = [{"name": b.get("Business Name"), "val": safe_num(b.get("Total Valuation ($)")),
                        "own": safe_num(b.get("Your Ownership (%)")) / 100.0,
                        "dist": safe_num(b.get("Annual Distribution ($)")),
                        "v_growth": float(b.get("Override Val. Growth (%)")) if pd.notna(
                            b.get("Override Val. Growth (%)")) and str(
                            b.get("Override Val. Growth (%)")).strip() != "" else ctx['mkt'],
                        "d_growth": float(b.get("Override Dist. Growth (%)")) if pd.notna(
                            b.get("Override Dist. Growth (%)")) and str(
                            b.get("Override Dist. Growth (%)")).strip() != "" else ctx['inc_g']} for b in
                       ctx['biz_records'] if b.get("Business Name")]

            unfunded_debt_bal = 0
            prev_unfunded_debt_bal = 0
            last_irmaa_tier = 0

            # Pre-calculate SS actual starting entitlements (Base Amount * FRA multiplier)
            primary_ss_record = next(
                (r for r in ctx['inc_records'] if r.get('Category') == 'Social Security' and r.get('Owner') == 'Me'),
                None)
            spouse_ss_record = next((r for r in ctx['inc_records'] if
                                     r.get('Category') == 'Social Security' and r.get('Owner') == 'Spouse'), None)

            primary_ss_start_year = safe_num(primary_ss_record['Start Year'],
                                             ctx['current_year']) if primary_ss_record else 9999
            spouse_ss_start_year = safe_num(spouse_ss_record['Start Year'],
                                            ctx['current_year']) if spouse_ss_record else 9999

            primary_ss_multi = get_ss_multi(ctx['my_birth_year'], primary_ss_start_year)
            spouse_ss_multi = get_ss_multi(ctx['spouse_birth_year'], spouse_ss_start_year)

            sim_res, det_res, nw_det_res = [], [], []
            milestones_by_year = {}

            # Drawdown trackers for dynamic milestones
            tapped_brokerage = False
            tapped_trad = False
            tapped_roth = False
            cash_depleted = False
            ss_started_me = False
            ss_started_spouse = False
            irmaa_triggered = False
            spouse_died_notified = False
            me_died_notified = False

            # Track previous balances to detect exact payoff/depletion years
            prev_debt_bals = {d['name']: d['bal'] for d in sim_debts}
            prev_re_debts = {r['name']: r['debt'] for r in sim_re}
            prev_ast_bals = {a['Account Name']: a['bal'] for a in sim_assets}

            for year_offset in range(ctx['max_years'] + 1):
                year = ctx['current_year'] + year_offset
                my_current_age = year - ctx['my_birth_year']
                spouse_current_age = year - ctx['spouse_birth_year'] if ctx['has_spouse'] else 0

                is_my_alive = year <= ctx['primary_end_year']
                is_spouse_alive = ctx['has_spouse'] and (year <= ctx['spouse_end_year'])

                if not is_my_alive and not is_spouse_alive:
                    break

                # 1. Unfunded Debt Accrues Interest (Prior year balance compounds first)
                prev_unfunded_debt_bal = unfunded_debt_bal
                if unfunded_debt_bal > 0:
                    unfunded_debt_bal *= (1 + SHORTFALL_PENALTY_RATE)

                    # 2. Base Setup & Milestones
                if ctx['has_spouse'] and not is_spouse_alive and not spouse_died_notified:
                    if year not in milestones_by_year: milestones_by_year[year] = []
                    milestones_by_year[year].append(
                        {"desc": "💀 Spouse Passes Away (Step-up Basis Applied)", "amt": 0, "type": "critical"})
                    spouse_died_notified = True

                if not is_my_alive and not me_died_notified:
                    if year not in milestones_by_year: milestones_by_year[year] = []
                    milestones_by_year[year].append({"desc": "💀 You Pass Away", "amt": 0, "type": "critical"})
                    me_died_notified = True

                if year == ctx['primary_retire_year'] and is_my_alive:
                    if year not in milestones_by_year: milestones_by_year[year] = []
                    milestones_by_year[year].append({"desc": "🎓 You Retire", "amt": 0, "type": "system"})

                if ctx['has_spouse'] and year == ctx['spouse_retire_year'] and is_spouse_alive:
                    if year not in milestones_by_year: milestones_by_year[year] = []
                    milestones_by_year[year].append({"desc": "🎓 Spouse Retires", "amt": 0, "type": "system"})

                if is_my_alive and my_current_age == 65:
                    if year not in milestones_by_year: milestones_by_year[year] = []
                    milestones_by_year[year].append({"desc": "🏥 Medicare Kicks In (You)", "amt": 0, "type": "system"})

                if ctx['has_spouse'] and is_spouse_alive and spouse_current_age == 65:
                    if year not in milestones_by_year: milestones_by_year[year] = []
                    milestones_by_year[year].append(
                        {"desc": "🏥 Medicare Kicks In (Spouse)", "amt": 0, "type": "system"})

                if is_my_alive and my_current_age == ctx['primary_rmd_age']:
                    if year not in milestones_by_year: milestones_by_year[year] = []
                    milestones_by_year[year].append({"desc": "🏦 Your RMDs Begin", "amt": 0, "type": "system"})

                if ctx['has_spouse'] and is_spouse_alive and spouse_current_age == ctx['spouse_rmd_age']:
                    if year not in milestones_by_year: milestones_by_year[year] = []
                    milestones_by_year[year].append({"desc": "🏦 Spouse RMDs Begin", "amt": 0, "type": "system"})

                # Retire check for lifestyle drops
                is_retired = year >= ctx['primary_retire_year']

                yd = {"Year": year, "Age (Primary)": my_current_age, "Age (Spouse)": spouse_current_age}
                nw_yd = {"Year": year, "Age (Primary)": my_current_age, "Age (Spouse)": spouse_current_age}

                annual_inc, annual_ss, pre_tax_ord = 0, 0, 0
                earned_income_me, earned_income_spouse = 0, 0
                match_income_by_owner = {"Me": 0, "Spouse": 0, "Joint": 0}

                # 3. Market Returns (Stress Test vs Glidepath)
                base_mkt_yr = mkt_sequence[year_offset]
                if ctx['stress_test'] and year == ctx['primary_retire_year']:
                    mkt_glide = -25.0
                    mkt_roth = -25.0
                elif ctx['glidepath'] and is_retired:
                    years_retired = year - ctx['primary_retire_year']
                    mkt_glide = max(3.0, base_mkt_yr - (math.floor(years_retired / 5) * 1.0))
                    mkt_roth = base_mkt_yr
                else:
                    mkt_glide = base_mkt_yr
                    mkt_roth = base_mkt_yr

                active_mfj = True if ctx['has_spouse'] and is_my_alive and is_spouse_alive else False

                # 4. RMDs (Calculated strictly on Prior Year Dec 31st Balance before any new year growth or contribs)
                rmd_income = 0
                for a in sim_assets:
                    if a.get('Type') in ['Traditional 401(k)', 'Traditional IRA'] and a['bal'] > 0:
                        owner = a.get('Owner', 'Me')
                        owner_age = my_current_age if owner in ['Me', 'Joint'] else spouse_current_age
                        owner_alive = is_my_alive if owner in ['Me', 'Joint'] else is_spouse_alive
                        owner_rmd_age = ctx['primary_rmd_age'] if owner in ['Me', 'Joint'] else ctx['spouse_rmd_age']

                        if owner_alive and owner_age >= owner_rmd_age:
                            factor = irs_uniform_table.get(owner_age, 2.0)
                            rmd_amt = a['bal'] / factor
                            a['bal'] -= rmd_amt
                            rmd_income += rmd_amt
                            pre_tax_ord += rmd_amt

                if rmd_income > 0:
                    annual_inc += rmd_income
                    yd["Income: RMDs"] = rmd_income

                # 5. Income Generation
                primary_ss_entitlement = 0
                spouse_ss_entitlement = 0

                for inc in ctx['inc_records']:
                    owner = inc.get("Owner", "Me")
                    cat_name = inc.get("Category", "Other")
                    stop_at_ret = inc.get("Stop at Ret.?", False)

                    owner_retire_year = ctx['primary_retire_year'] if owner in ["Me", "Joint"] else ctx[
                        'spouse_retire_year']
                    start_year = safe_num(inc.get('Start Year'), ctx['current_year'])
                    end_year = safe_num(inc.get('End Year'), 2100)

                    if cat_name in ["Social Security", "Pension"]: stop_at_ret = False

                    is_active = False
                    if stop_at_ret:
                        is_active = (year >= start_year) and (year < owner_retire_year)
                    else:
                        is_active = (start_year <= year <= end_year)

                    if inc.get("Description"):
                        g = safe_num(inc.get('Override Growth (%)'), ctx['inc_g'])
                        base_amt = safe_num(inc.get('Annual Amount ($)'))

                        if cat_name == "Social Security":
                            # Process entitlement internally regardless of is_active to allow survivor logic to accurately inherit
                            if owner == "Me":
                                adjusted_base = base_amt * primary_ss_multi
                                offset = max(0, year - primary_ss_start_year)
                                primary_ss_entitlement = adjusted_base * ((1 + ctx['infl'] / 100) ** offset)
                            elif owner == "Spouse":
                                adjusted_base = base_amt * spouse_ss_multi
                                offset = max(0, year - spouse_ss_start_year)
                                spouse_ss_entitlement = adjusted_base * ((1 + ctx['infl'] / 100) ** offset)
                            continue

                        if not is_active: continue

                        offset_for_growth = max(0, year - ctx['current_year'])
                        amt = base_amt * ((1 + g / 100) ** offset_for_growth)

                        # Hide 401(k) match from spendable income completely, but track it to auto-deposit to assets
                        if cat_name == "Employer Match (401k/HSA)":
                            if (owner == "Me" and is_my_alive) or (owner == "Spouse" and is_spouse_alive) or (
                                    owner == "Joint" and (is_my_alive or is_spouse_alive)):
                                match_income_by_owner[owner] += amt
                            continue

                        if owner == "Me" and not is_my_alive: continue
                        if owner == "Spouse" and not is_spouse_alive: continue
                        if owner == "Joint" and not is_my_alive and not is_spouse_alive: continue

                        annual_inc += amt
                        yd[f"Income: {cat_name}"] = yd.get(f"Income: {cat_name}", 0) + amt
                        pre_tax_ord += amt
                        if cat_name in ["Base Salary (W-2)", "Bonus / Commission", "Contractor (1099)"]:
                            if owner in ["Me", "Joint"]:
                                earned_income_me += amt
                            elif owner == "Spouse":
                                earned_income_spouse += amt

                # Spousal SS Survivor Benefits & Taxation (Tax Torpedo Logic)
                active_ss = 0
                if is_my_alive and is_spouse_alive:
                    if year >= primary_ss_start_year: active_ss += primary_ss_entitlement
                    if year >= spouse_ss_start_year: active_ss += spouse_ss_entitlement

                    if primary_ss_entitlement > 0 and year >= primary_ss_start_year and not ss_started_me:
                        if year not in milestones_by_year: milestones_by_year[year] = []
                        milestones_by_year[year].append(
                            {"desc": "📈 Social Security Begins (You)", "amt": primary_ss_entitlement, "type": "system"})
                        ss_started_me = True
                    if spouse_ss_entitlement > 0 and year >= spouse_ss_start_year and not ss_started_spouse:
                        if year not in milestones_by_year: milestones_by_year[year] = []
                        milestones_by_year[year].append(
                            {"desc": "📈 Social Security Begins (Spouse)", "amt": spouse_ss_entitlement,
                             "type": "system"})
                        ss_started_spouse = True
                elif is_my_alive and not is_spouse_alive:
                    primary_actual = primary_ss_entitlement if year >= primary_ss_start_year else 0
                    survivor_benefit = max(primary_ss_entitlement,
                                           spouse_ss_entitlement)  # Per SSA rules, survivor inherits the higher of the two
                    active_ss = max(primary_actual, survivor_benefit)

                    if active_ss > 0 and not ss_started_me:
                        if year not in milestones_by_year: milestones_by_year[year] = []
                        milestones_by_year[year].append(
                            {"desc": "📈 Social Security Survivor Benefit Begins", "amt": active_ss, "type": "system"})
                        ss_started_me = True
                elif is_spouse_alive and not is_my_alive:
                    spouse_actual = spouse_ss_entitlement if year >= spouse_ss_start_year else 0
                    survivor_benefit = max(primary_ss_entitlement, spouse_ss_entitlement)
                    active_ss = max(spouse_actual, survivor_benefit)

                    if active_ss > 0 and not ss_started_spouse:
                        if year not in milestones_by_year: milestones_by_year[year] = []
                        milestones_by_year[year].append(
                            {"desc": "📈 Social Security Survivor Benefit Begins", "amt": active_ss, "type": "system"})
                        ss_started_spouse = True

                if active_ss > 0:
                    annual_inc += active_ss
                    annual_ss += active_ss
                    yd["Income: Social Security"] = active_ss

                    # Calculate Provisional Income & Taxable SS Portion using strict IRS tier band math
                    ss_provisional_income = pre_tax_ord + (active_ss * 0.5)
                    if active_mfj:
                        if ss_provisional_income <= 32000:
                            taxable_ss = 0
                        elif ss_provisional_income <= 44000:
                            taxable_ss = min(0.5 * active_ss, 0.5 * (ss_provisional_income - 32000))
                        else:
                            tier1_max = min(0.5 * active_ss, 6000)
                            taxable_ss = min(0.85 * active_ss, 0.85 * (ss_provisional_income - 44000) + tier1_max)
                    else:
                        if ss_provisional_income <= 25000:
                            taxable_ss = 0
                        elif ss_provisional_income <= 34000:
                            taxable_ss = min(0.5 * active_ss, 0.5 * (ss_provisional_income - 25000))
                        else:
                            tier1_max = min(0.5 * active_ss, 4500)
                            taxable_ss = min(0.85 * active_ss, 0.85 * (ss_provisional_income - 34000) + tier1_max)
                    pre_tax_ord += taxable_ss

                # 6. Business & Real Estate
                cur_biz_val, re_equity = 0, 0
                total_exp = 0  # Initialize general expenses
                biz_income_total = 0

                for b in sim_biz:
                    if year_offset > 0:
                        b['val'] *= (1 + b['v_growth'] / 100)  # Private biz doesn't follow active_mkt glidepath
                        b['dist'] *= (1 + b['d_growth'] / 100)
                    cur_biz_val += (b['val'] * b['own'])
                    annual_inc += b['dist']
                    biz_income_total += b['dist']
                    yd["Income: Biz Dist"] = yd.get("Income: Biz Dist", 0) + b['dist']

                # Phase-out compliant QBI Deduction Proxy
                qbi_deduction = 0
                if biz_income_total > 0:
                    infl_factor = (1 + ctx['infl'] / 100) ** year_offset
                    qbi_threshold = (383900 if active_mfj else 191950) * infl_factor
                    qbi_phaseout = (483900 if active_mfj else 241950) * infl_factor

                    if pre_tax_ord < qbi_threshold:
                        qbi_deduction = biz_income_total * 0.20
                    elif pre_tax_ord < qbi_phaseout:
                        ratio = (qbi_phaseout - pre_tax_ord) / (qbi_phaseout - qbi_threshold)
                        qbi_deduction = biz_income_total * 0.20 * ratio
                    else:
                        qbi_deduction = 0

                for r in sim_re:
                    if year_offset > 0:
                        r['rent'] *= (1 + r['r_growth'] / 100)
                        r['exp'] *= (1 + ctx['infl'] / 100)
                        r['val'] *= (1 + r['v_growth'] / 100)

                    # Exact Monthly Amortization
                    monthly_rate = r['rate'] / 12
                    monthly_pmt = r['pmt'] / 12
                    interest_paid = 0
                    actual_mortgage_paid = 0
                    for _ in range(12):
                        if r['debt'] > 0:
                            monthly_interest = r['debt'] * monthly_rate
                            interest_paid += monthly_interest
                            principal_paid = max(0, monthly_pmt - monthly_interest)
                            actual_mortgage_paid += min(r['debt'] + monthly_interest, monthly_pmt)
                            r['debt'] = max(0, r['debt'] - principal_paid)
                        else:
                            break

                    # Trigger Mortgage Payoff Milestone
                    if r['debt'] <= 0 and prev_re_debts.get(r['name'], 0) > 0:
                        if year not in milestones_by_year: milestones_by_year[year] = []
                        milestones_by_year[year].append(
                            {"desc": f"🏡 Mortgage Paid Off: {r['name']}", "amt": 0, "type": "system"})
                    prev_re_debts[r['name']] = r['debt']

                    re_equity += (r['val'] - r['debt'])

                    # Cash flow routing: Primary vs Investment
                    if r['is_primary']:
                        primary_costs = r['exp'] + actual_mortgage_paid
                        total_exp += primary_costs
                        yd["Expense: Primary Home (Mortgage & Upkeep)"] = yd.get(
                            "Expense: Primary Home (Mortgage & Upkeep)", 0) + primary_costs
                        if r['rent'] > 0:
                            annual_inc += r['rent']
                            yd["Income: Primary Home Rent"] = yd.get("Income: Primary Home Rent", 0) + r['rent']
                    else:
                        net_re_cashflow = r['rent'] - (r['exp'] + actual_mortgage_paid)
                        if net_re_cashflow > 0:
                            annual_inc += net_re_cashflow
                            yd["Income: Net Investment RE Cashflow"] = yd.get("Income: Net Investment RE Cashflow",
                                                                              0) + net_re_cashflow
                        elif net_re_cashflow < 0:
                            total_exp += abs(net_re_cashflow)
                            yd["Expense: Net Investment RE Loss"] = yd.get("Expense: Net Investment RE Loss", 0) + abs(
                                net_re_cashflow)

                    taxable_rent = max(0, r['rent'] - r['exp'] - interest_paid)
                    pre_tax_ord += taxable_rent

                # Tax Base Ord protects QBI deduction without mutating the actual pre_tax_ord (needed accurately for IRMAA MAGI)
                tax_base_ord = max(0, pre_tax_ord - qbi_deduction)

                # 7. Unified Lifetime Cash Flows Engine (Expenses)
                for ev in ctx['exp_records']:
                    desc = str(ev.get("Description", "")).strip()
                    if not desc: continue

                    cat = ev.get("Category", "Other")
                    if ctx['owns_home'] and cat in ["Housing / Rent", "Debt Payments"]: continue
                    if not ctx['owns_home'] and cat == "Debt Payments": continue

                    freq = ev.get("Frequency", "Monthly")
                    amt = safe_num(ev.get("Amount ($)", 0))
                    if freq == "Monthly": amt *= 12

                    start_phase = ev.get("Start Phase", "Now")
                    end_phase = ev.get("End Phase", "End of Life")

                    actual_start = ctx['current_year']
                    if start_phase == "At Retirement":
                        actual_start = ctx['primary_retire_year']
                    elif start_phase == "Custom Year":
                        actual_start = safe_num(ev.get("Start Year"), ctx['current_year'])

                    actual_end = ctx['max_year']
                    if end_phase == "At Retirement":
                        actual_end = ctx['primary_retire_year'] - 1
                    elif end_phase == "Custom Year":
                        actual_end = safe_num(ev.get("End Year"), ctx['max_year'])

                    is_active = False
                    if freq == "One-Time":
                        is_active = (year == actual_start)
                    else:
                        is_active = (actual_start <= year <= actual_end)

                    if is_active:
                        cat_infl = ctx['infl_hc'] if cat in ["Healthcare", "Insurance"] else (
                            ctx['infl_ed'] if cat == "Education" else ctx['infl'])
                        inflated_amt = amt * ((1 + cat_infl / 100) ** year_offset)

                        # Drop recurring lifestyle expenses if widow(er) -> Applies to lifestyle only
                        if ctx['has_spouse'] and not (
                                is_my_alive and is_spouse_alive) and freq != "One-Time" and cat not in ["Education",
                                                                                                        "Debt Payments",
                                                                                                        "Healthcare",
                                                                                                        "Insurance",
                                                                                                        "Housing / Rent"]:
                            if year >= ctx['primary_retire_year']:
                                inflated_amt *= WIDOW_EXPENSE_MULTIPLIER

                        # Medicare Cliff logic (Proportional Reduction)
                        primary_on_medicare = is_my_alive and my_current_age >= 65
                        spouse_on_medicare = ctx['has_spouse'] and is_spouse_alive and spouse_current_age >= 65

                        if ctx['medicare_cliff'] and cat == "Healthcare":
                            # Only apply the cliff if the user's start year was explicitly pre-medicare
                            if actual_start < (ctx['my_birth_year'] + 65):
                                reduction = 1.0
                                if primary_on_medicare: reduction -= MEDICARE_CLIFF_SINGLE_DROP
                                if spouse_on_medicare: reduction -= MEDICARE_CLIFF_SINGLE_DROP
                                inflated_amt *= max(0.5, reduction)

                        total_exp += inflated_amt

                        if freq == "One-Time":
                            yd[f"Expense: Milestone ({desc})"] = inflated_amt
                            if year not in milestones_by_year: milestones_by_year[year] = []
                            milestones_by_year[year].append({"desc": desc, "amt": inflated_amt, "type": "normal"})
                        else:
                            yd[f"Expense: {cat}"] = yd.get(f"Expense: {cat}", 0) + inflated_amt

                        # 529 Plan Routing Logic
                        is_education = any(
                            k in desc.lower() for k in ['college', 'tuition', 'university', 'education', 'school'])
                        if is_education:
                            amount_to_cover = inflated_amt
                            covered_by_529 = 0

                            target_kid = None
                            for k in ctx['kids_data']:
                                if k['name'].lower() in desc.lower():
                                    target_kid = k['name'].lower()
                                    break

                            # Pass 1: Strict Kid Name Match (Regex boundaries prevent substrings)
                            if target_kid:
                                for a in sim_assets:
                                    if a.get('Type') == '529 Plan' and a['bal'] > 0 and re.search(
                                            rf'\b{re.escape(target_kid)}\b', str(a.get('Account Name', '')).lower()):
                                        if a['bal'] >= amount_to_cover:
                                            a['bal'] -= amount_to_cover
                                            covered_by_529 += amount_to_cover
                                            amount_to_cover = 0;
                                            break
                                        else:
                                            amount_to_cover -= a['bal']
                                            covered_by_529 += a['bal']
                                            a['bal'] = 0

                            # Pass 2: Fallback to any generic 529
                            if amount_to_cover > 0:
                                for a in sim_assets:
                                    if a.get('Type') == '529 Plan' and a['bal'] > 0:
                                        if a['bal'] >= amount_to_cover:
                                            a['bal'] -= amount_to_cover
                                            covered_by_529 += amount_to_cover
                                            amount_to_cover = 0;
                                            break
                                        else:
                                            amount_to_cover -= a['bal']
                                            covered_by_529 += a['bal']
                                            a['bal'] = 0

                            if covered_by_529 > 0:
                                annual_inc += covered_by_529
                                yd[f"Income: Tax-Free 529 Withdrawal ({desc})"] = covered_by_529

                # Global Medicare Gap applied exactly once per year if conditions met
                if ctx['medicare_gap'] and is_retired and my_current_age < 65:
                    # ACA Subsidy proxy: if passive income > 100k, pay full $15k gap penalty. Otherwise scale it.
                    subsidy_factor = min(1.0, max(0.0, pre_tax_ord / 100000.0))
                    gap_cost = (MEDICARE_GAP_COST * subsidy_factor) * ((1 + ctx['infl_hc'] / 100) ** year_offset)
                    total_exp += gap_cost
                    yd["Expense: Healthcare (Pre-Medicare Gap Proxy)"] = gap_cost

                # LTC Shock
                if ctx['ltc_shock']:
                    if is_my_alive and my_current_age >= (ctx['my_life_exp_val'] - 2):
                        ltc_cost = LTC_SHOCK_COST * ((1 + ctx['infl_hc'] / 100) ** year_offset)
                        total_exp += ltc_cost
                        yd["Expense: Long Term Care Shock (Primary)"] = ltc_cost
                    if ctx['has_spouse'] and is_spouse_alive and spouse_current_age >= (ctx['spouse_life_exp_val'] - 2):
                        ltc_cost_spouse = LTC_SHOCK_COST * ((1 + ctx['infl_hc'] / 100) ** year_offset)
                        total_exp += ltc_cost_spouse
                        yd["Expense: Long Term Care Shock (Spouse)"] = ltc_cost_spouse

                # Debt Amortization (Generic Liabilities)
                debt_bal_total = 0
                for d in sim_debts:
                    monthly_rate = d['rate'] / 12
                    monthly_pmt = d['pmt'] / 12
                    actual_paid = 0
                    for _ in range(12):
                        if d['bal'] > 0:
                            monthly_interest = d['bal'] * monthly_rate
                            principal_paid = max(0, monthly_pmt - monthly_interest)
                            actual_paid += min(d['bal'] + monthly_interest, monthly_pmt)
                            d['bal'] = max(0, d['bal'] - principal_paid)
                        else:
                            break
                    total_exp += actual_paid
                    yd["Expense: Debt Payments"] = yd.get("Expense: Debt Payments", 0) + actual_paid

                    if d['bal'] <= 0 and prev_debt_bals.get(d['name'], 0) > 0:
                        if year not in milestones_by_year: milestones_by_year[year] = []
                        milestones_by_year[year].append(
                            {"desc": f"🎉 Debt Paid Off: {d['name']}", "amt": 0, "type": "system"})
                    prev_debt_bals[d['name']] = d['bal']
                    debt_bal_total += d['bal']

                # 8. Out-of-Pocket Asset Contributions & Base Federal Taxes
                user_out_of_pocket_contribs = 0
                person_401k_contribs = {'Me': 0, 'Spouse': 0, 'Joint': 0}

                # IRA / 401k Limit Tracker
                plan_401k_limit = PLAN_401K_LIMIT_BASE * ((1 + ctx['infl'] / 100) ** year_offset)
                catchup_401k = CATCHUP_401K_BASE * ((1 + ctx['infl'] / 100) ** year_offset)
                ira_limit = IRA_LIMIT_BASE * ((1 + ctx['infl'] / 100) ** year_offset)
                catchup_ira = CATCHUP_IRA_BASE * ((1 + ctx['infl'] / 100) ** year_offset)

                # Pre-calculate what out-of-pocket contributions will actually hit the cash flow requirement
                for a in sim_assets:
                    owner = a.get('Owner', 'Me')
                    owner_retire_year = ctx['primary_retire_year'] if owner in ['Me', 'Joint'] else ctx[
                        'spouse_retire_year']
                    owner_age = my_current_age if owner in ['Me', 'Joint'] else spouse_current_age
                    is_owner_alive = is_my_alive if owner in ['Me', 'Joint'] else is_spouse_alive

                    if is_owner_alive:
                        stop_contrib = a.get('stop_at_ret', True)
                        if not (stop_contrib and year >= owner_retire_year):
                            added_this_year = a['contrib']

                            # Hard cap IRS Limits Per Person
                            if a.get('Type') in ['Traditional 401(k)', 'Roth 401(k)']:
                                limit = plan_401k_limit + (catchup_401k if owner_age >= 50 else 0)
                                added_this_year = min(added_this_year, max(0, limit - person_401k_contribs[owner]))
                                person_401k_contribs[owner] += added_this_year
                            elif a.get('Type') in ['Traditional IRA', 'Roth IRA']:
                                limit = ira_limit + (catchup_ira if owner_age >= 50 else 0)
                                added_this_year = min(added_this_year, limit)  # Simplified IRA limit tracker

                            user_out_of_pocket_contribs += added_this_year

                state_tax_rate = ctx['cur_t'] if not is_retired else ctx['ret_t']
                # Determine baseline pre-conversion tax for the cash guardrail calculation
                base_fed_tax_pre_conversion, marginal_rate = calc_federal_tax(tax_base_ord, active_mfj, year_offset,
                                                                              ctx['infl'])
                base_state_tax_pre_conversion = tax_base_ord * (state_tax_rate / 100.0)

                # 9. Roth Conversion Optimizer
                total_converted = 0
                if ctx['roth_conversions'] and is_retired:
                    infl_factor = (1 + ctx['infl'] / 100) ** year_offset
                    std_deduction = (29200 if active_mfj else 14600) * infl_factor

                    b_limits_mfj = {"12%": 94300, "22%": 201050, "24%": 383900, "32%": 487450}
                    b_limits_single = {"12%": 47150, "22%": 100525, "24%": 191950, "32%": 243725}

                    b_limits = b_limits_mfj if active_mfj else b_limits_single
                    target_limit = b_limits.get(ctx['roth_target'], 383900) * infl_factor
                    target_max_income = target_limit + std_deduction

                    conversion_room = max(0, target_max_income - tax_base_ord)

                    # EXACT GUARDRAIL: We deduct all known expenses, out-of-pocket contributions, and base taxes from cash before attempting conversion
                    available_cash = sum(a['bal'] for a in sim_assets if
                                         a.get('Type') in ['Checking/Savings', 'HYSA', 'Brokerage (Taxable)',
                                                           'Unallocated Cash'])
                    locked_outflows = total_exp + user_out_of_pocket_contribs + base_fed_tax_pre_conversion + base_state_tax_pre_conversion
                    safe_liquid_cash = max(0, available_cash - locked_outflows)

                    est_tax_rate = marginal_rate + (state_tax_rate / 100.0)
                    max_tax_budget = safe_liquid_cash * ROTH_CASH_BUFFER_MARGIN
                    max_conversion_by_cash = max_tax_budget / max(0.10, est_tax_rate)

                    conversion_room = min(conversion_room, max_conversion_by_cash)

                    if conversion_room > 0:
                        for a in sim_assets:
                            if a.get('Type') in ['Traditional 401(k)', 'Traditional IRA'] and a['bal'] > 0:
                                convert_amt = min(a['bal'], conversion_room - total_converted)
                                if convert_amt > 0:
                                    a['bal'] -= convert_amt
                                    total_converted += convert_amt

                                    roth_found = False
                                    for roth_a in sim_assets:
                                        if roth_a.get('Type') in ['Roth 401(k)', 'Roth IRA'] and roth_a.get(
                                                'Owner') == a.get('Owner'):
                                            roth_a['bal'] += convert_amt
                                            roth_found = True
                                            break
                                    if not roth_found:
                                        sim_assets.append({
                                            "Account Name": f"Converted Roth ({a.get('Owner')})",
                                            "Type": "Roth IRA",
                                            "Owner": a.get("Owner", "Me"),
                                            "bal": convert_amt,
                                            "contrib": 0.0,
                                            "growth": a.get('growth'),
                                            "stop_at_ret": True
                                        })
                                if total_converted >= conversion_room:
                                    break

                        if total_converted > 0:
                            pre_tax_ord += total_converted
                            tax_base_ord += total_converted
                            yd["Roth Conversion Amount"] = total_converted

                # 10. Finalize Taxes (Including conversions) & Run IRMAA
                base_fed_tax, marginal_rate = calc_federal_tax(tax_base_ord, active_mfj, year_offset, ctx['infl'])
                state_tax = tax_base_ord * (state_tax_rate / 100.0)

                # FICA Tax (Indexed for inflation)
                fica_tax = 0
                ss_wage_base = SS_WAGE_BASE_2026 * ((1 + ctx['infl'] / 100) ** year_offset)
                addl_med_tax_threshold = ADDL_MED_TAX_THRESHOLD * ((1 + ctx['infl'] / 100) ** year_offset)
                for ei in [earned_income_me, earned_income_spouse]:
                    if ei > 0:
                        ss_tax = min(ei, ss_wage_base) * 0.062
                        med_tax = ei * 0.0145
                        addl_med_tax = max(0, ei - addl_med_tax_threshold) * 0.009
                        fica_tax += ss_tax + med_tax + addl_med_tax

                total_tax = base_fed_tax + state_tax + fica_tax
                yd["Expense: Taxes"] = total_tax

                # Medicare IRMAA Surcharges Proxy (uses pre_tax_ord UNADJUSTED by QBI, but correctly includes Conversions)
                num_on_medicare = 0
                if is_my_alive and my_current_age >= 65: num_on_medicare += 1
                if is_spouse_alive and spouse_current_age >= 65: num_on_medicare += 1

                magi_for_irmaa = pre_tax_ord  # IRS rules specify MAGI includes Roth conversions, but we add back QBI implicitly because pre_tax_ord didn't have QBI deducted

                if num_on_medicare > 0:
                    infl_factor = (1 + ctx['infl'] / 100) ** year_offset
                    t1 = 206000 * infl_factor if active_mfj else 103000 * infl_factor
                    t2 = 258000 * infl_factor if active_mfj else 129000 * infl_factor
                    t3 = 322000 * infl_factor if active_mfj else 161000 * infl_factor
                    t4 = 386000 * infl_factor if active_mfj else 193000 * infl_factor
                    t5 = 750000 * infl_factor if active_mfj else 500000 * infl_factor

                    surcharge = 0
                    if magi_for_irmaa > t5:
                        surcharge = 6500 * infl_factor
                    elif magi_for_irmaa > t4:
                        surcharge = 5500 * infl_factor
                    elif magi_for_irmaa > t3:
                        surcharge = 4000 * infl_factor
                    elif magi_for_irmaa > t2:
                        surcharge = 2500 * infl_factor
                    elif magi_for_irmaa > t1:
                        surcharge = 1000 * infl_factor

                    total_irmaa = surcharge * num_on_medicare
                    if total_irmaa > 0:
                        total_exp += total_irmaa
                        yd["Expense: Medicare IRMAA Surcharge"] = total_irmaa
                        if not irmaa_triggered:
                            if year not in milestones_by_year: milestones_by_year[year] = []
                            milestones_by_year[year].append(
                                {"desc": "📉 Medicare IRMAA Surcharge Triggered", "amt": total_irmaa, "type": "system"})
                            irmaa_triggered = True

                        # Trigger milestone if we jump tiers
                        if total_irmaa > last_irmaa_tier + 500:
                            if year not in milestones_by_year: milestones_by_year[year] = []
                            milestones_by_year[year].append(
                                {"desc": "📉 Medicare IRMAA Surcharge Tier Jumped", "amt": total_irmaa,
                                 "type": "system"})
                            last_irmaa_tier = total_irmaa

                # 11. Employer Match Routing & Mid-Year Asset Growth
                person_401k_contribs = {'Me': 0, 'Spouse': 0, 'Joint': 0}  # reset for actual loop processing

                # Distribute matches specifically to 401k/HSA/Roth first
                for acct_type_target in ['Traditional 401(k)', 'Roth 401(k)', 'HSA']:
                    for a in sim_assets:
                        if a.get('Type') == acct_type_target:
                            owner = a.get('Owner', 'Me')
                            match_avail = match_income_by_owner.get(owner, 0)
                            if match_avail > 0:
                                a['match_contrib_queue'] = match_avail
                                match_income_by_owner[owner] = 0

                # Fallback: If no 401k exists, don't delete the employer match, route it to brokerage/cash
                for owner, match_left in match_income_by_owner.items():
                    if match_left > 0:
                        found_fallback = False
                        for a in sim_assets:
                            if a.get('Owner') == owner and a.get('Type') in ['Brokerage (Taxable)', 'HYSA',
                                                                             'Checking/Savings']:
                                a['match_contrib_queue'] = a.get('match_contrib_queue', 0) + match_left
                                found_fallback = True
                                break
                        if not found_fallback and len(sim_assets) > 0:
                            sim_assets[0]['match_contrib_queue'] = sim_assets[0].get('match_contrib_queue',
                                                                                     0) + match_left
                        match_income_by_owner[owner] = 0

                for a in sim_assets:
                    custom_g = a.get('growth')
                    is_glidepath_applicable = a.get('Type') in ['Traditional 401(k)', 'Traditional IRA',
                                                                'Brokerage (Taxable)']
                    is_cash_account = a.get('Type') in ['Checking/Savings', 'HYSA', 'Unallocated Cash']

                    if is_cash_account:
                        a_growth = float(custom_g) if pd.notna(custom_g) and custom_g != "" else 0.0
                    elif is_glidepath_applicable:
                        a_growth = float(custom_g) if pd.notna(custom_g) and custom_g != "" else mkt_glide
                    else:
                        a_growth = float(custom_g) if pd.notna(custom_g) and custom_g != "" else mkt_roth

                    owner = a.get('Owner', 'Me')
                    owner_retire_year = ctx['primary_retire_year'] if owner in ['Me', 'Joint'] else ctx[
                        'spouse_retire_year']
                    owner_age = my_current_age if owner in ['Me', 'Joint'] else spouse_current_age
                    is_owner_alive = is_my_alive if owner in ['Me', 'Joint'] else is_spouse_alive

                    added_this_year = 0
                    if is_owner_alive:
                        stop_contrib = a.get('stop_at_ret', True)
                        if not (stop_contrib and year >= owner_retire_year):
                            added_this_year = a['contrib']

                            if a.get('Type') in ['Traditional 401(k)', 'Roth 401(k)']:
                                limit = plan_401k_limit + (catchup_401k if owner_age >= 50 else 0)
                                added_this_year = min(added_this_year, max(0, limit - person_401k_contribs[owner]))
                                person_401k_contribs[owner] += added_this_year
                            elif a.get('Type') in ['Traditional IRA', 'Roth IRA']:
                                limit = ira_limit + (catchup_ira if owner_age >= 50 else 0)
                                added_this_year = min(added_this_year, limit)

                    match_to_add = a.get('match_contrib_queue', 0)
                    a['match_contrib_queue'] = 0

                    # 50/50 Mid Year Convention
                    a['bal'] += (added_this_year * 0.5) + (match_to_add * 0.5)
                    a['bal'] *= (1 + a_growth / 100)
                    a['bal'] += (added_this_year * 0.5) + (match_to_add * 0.5)

                # 12. Robust Shortfall / Withdrawal Math
                if user_out_of_pocket_contribs > 0:
                    yd["Expense: Portfolio Contributions"] = user_out_of_pocket_contribs

                cash_outflows = total_exp + user_out_of_pocket_contribs + total_tax
                net_cash_flow = annual_inc - cash_outflows
                yd["Net Savings"] = net_cash_flow

                if net_cash_flow > 0:
                    yd["Cashflow: Surplus Reinvested"] = net_cash_flow
                    # Surplus Handling & RMD Reinvestment
                    if unfunded_debt_bal > 0:
                        payoff = min(net_cash_flow, unfunded_debt_bal)
                        unfunded_debt_bal -= payoff
                        net_cash_flow -= payoff
                    if net_cash_flow > 0 and len(sim_assets) > 0:
                        brokerage_accts = [a for a in sim_assets if a.get('Type') == 'Brokerage (Taxable)']
                        if brokerage_accts:
                            brokerage_accts[0]['bal'] += net_cash_flow
                        else:
                            cash_accts = [a for a in sim_assets if
                                          a.get('Type') in ['Checking/Savings', 'HYSA', 'Unallocated Cash']]
                            if cash_accts:
                                cash_accts[0]['bal'] += net_cash_flow
                            else:
                                sim_assets[0]['bal'] += net_cash_flow
                elif net_cash_flow < 0:
                    shortfall = abs(net_cash_flow)

                    def _withdraw(a, current_shortfall, tax_treatment):
                        nonlocal tapped_brokerage, tapped_trad, tapped_roth, total_tax
                        if a['bal'] <= 0: return current_shortfall

                        eff_tax = 0.0
                        req_gross = current_shortfall

                        if tax_treatment == 'cg':
                            if not tapped_brokerage:
                                if year not in milestones_by_year: milestones_by_year[year] = []
                                milestones_by_year[year].append(
                                    {"desc": "📉 Began Drawing from Taxable Brokerage", "amt": 0, "type": "system"})
                                tapped_brokerage = True
                            is_step_up = ctx['has_spouse'] and (not is_my_alive or not is_spouse_alive)
                            eff_tax = 0.0 if is_step_up else (
                                        get_ltcg_rate(tax_base_ord, active_mfj, year_offset, ctx['infl']) + (
                                            state_tax_rate / 100.0))
                            req_gross = current_shortfall / max(0.01, (1.0 - eff_tax))

                        elif tax_treatment == 'ordinary':
                            if not tapped_trad:
                                if year not in milestones_by_year: milestones_by_year[year] = []
                                milestones_by_year[year].append(
                                    {"desc": "📉 Began Drawing from Traditional 401(k)/IRA", "amt": 0, "type": "system"})
                                tapped_trad = True
                            owner_acct = a.get('Owner', 'Me')
                            owner_age_acct = my_current_age if owner_acct in ['Me', 'Joint'] else spouse_current_age
                            owner_retire_age_acct = ctx['primary_retire_year'] - ctx['my_birth_year'] if owner_acct in [
                                'Me', 'Joint'] else ctx['spouse_retire_year'] - ctx['spouse_birth_year']
                            rule_of_55 = (owner_retire_age_acct >= 55 and owner_age_acct >= owner_retire_age_acct)
                            penalty = 0.10 if (owner_age_acct < 59.5 and not rule_of_55) else 0.0
                            eff_tax = min(marginal_rate + (state_tax_rate / 100.0) + penalty, 0.99)
                            req_gross = current_shortfall / max(0.01, (1.0 - eff_tax))

                        elif tax_treatment == 'free':
                            if not tapped_roth:
                                if year not in milestones_by_year: milestones_by_year[year] = []
                                milestones_by_year[year].append(
                                    {"desc": "📉 Began Drawing from Roth/Tax-Free Assets", "amt": 0, "type": "system"})
                                tapped_roth = True
                            owner_acct = a.get('Owner', 'Me')
                            owner_age_acct = my_current_age if owner_acct in ['Me', 'Joint'] else spouse_current_age
                            owner_retire_age_acct = ctx['primary_retire_year'] - ctx['my_birth_year'] if owner_acct in [
                                'Me', 'Joint'] else ctx['spouse_retire_year'] - ctx['spouse_birth_year']
                            rule_of_55 = (owner_retire_age_acct >= 55 and owner_age_acct >= owner_retire_age_acct)
                            penalty = 0.10 if (a.get('Type') in ['Roth 401(k)',
                                                                 'Roth IRA'] and owner_age_acct < 59.5 and not rule_of_55) else 0.0
                            eff_tax = min(penalty, 0.99)
                            req_gross = current_shortfall / max(0.01, (1.0 - eff_tax))

                        if a['bal'] >= req_gross:
                            a['bal'] -= req_gross
                            tax_inc = req_gross - current_shortfall
                            total_tax += tax_inc
                            yd["Expense: Taxes"] = yd.get("Expense: Taxes", 0) + tax_inc
                            yd[f"Income: Withdrawal ({a.get('Account Name', 'Account')})"] = req_gross
                            return 0
                        else:
                            withdrawn = a['bal']
                            a['bal'] = 0
                            tax_inc = withdrawn * eff_tax
                            net_cash = withdrawn - tax_inc
                            total_tax += tax_inc
                            yd["Expense: Taxes"] = yd.get("Expense: Taxes", 0) + tax_inc
                            yd[f"Income: Withdrawal ({a.get('Account Name', 'Account')})"] = withdrawn
                            return current_shortfall - net_cash

                    # --- Sequence 1a: Checking/Savings/HYSA (0% Tax) ---
                    cash_was_available = any(a['bal'] > 0 for a in sim_assets if
                                             a.get('Type') in ['Checking/Savings', 'HYSA', 'Unallocated Cash'])
                    for a in sim_assets:
                        if shortfall <= 0: break
                        if a.get('Type') in ['Checking/Savings', 'HYSA', 'Unallocated Cash']:
                            if a['bal'] >= shortfall:
                                withdrawn = shortfall
                                a['bal'] -= shortfall
                                yd[f"Income: Withdrawal ({a.get('Account Name', 'Cash')})"] = withdrawn
                                shortfall = 0
                            else:
                                withdrawn = a['bal']
                                yd[f"Income: Withdrawal ({a.get('Account Name', 'Cash')})"] = withdrawn
                                shortfall -= a['bal']
                                a['bal'] = 0

                    if shortfall > 0 and cash_was_available and not cash_depleted:
                        cash_still_available = sum(a['bal'] for a in sim_assets if
                                                   a.get('Type') in ['Checking/Savings', 'HYSA', 'Unallocated Cash'])
                        if cash_still_available <= 0:
                            if year not in milestones_by_year: milestones_by_year[year] = []
                            milestones_by_year[year].append(
                                {"desc": "⚠️ Cash Reserves Depleted. Now drawing from investments.", "amt": 0,
                                 "type": "system"})
                            cash_depleted = True

                    # --- Sequence 1b: Taxable Brokerage ---
                    if shortfall > 0:
                        for a in sim_assets:
                            if shortfall <= 0: break
                            if a.get('Type') == 'Brokerage (Taxable)':
                                shortfall = _withdraw(a, shortfall, 'cg')

                    # --- Sequence 2 & 3: Order depends on Strategy ---
                    if 'Standard' in ctx['active_withdrawal_strategy']:
                        if shortfall > 0:
                            for a in sim_assets:
                                if shortfall <= 0: break
                                if a.get('Type') in ['Traditional 401(k)', 'Traditional IRA']: shortfall = _withdraw(a,
                                                                                                                     shortfall,
                                                                                                                     'ordinary')
                        if shortfall > 0:
                            for a in sim_assets:
                                if shortfall <= 0: break
                                if a.get('Type') in ['Roth 401(k)', 'Roth IRA', 'HSA', 'Crypto', '529 Plan',
                                                     'Other']: shortfall = _withdraw(a, shortfall, 'free')
                    else:
                        if shortfall > 0:
                            for a in sim_assets:
                                if shortfall <= 0: break
                                if a.get('Type') in ['Roth 401(k)', 'Roth IRA', 'HSA', 'Crypto', '529 Plan',
                                                     'Other']: shortfall = _withdraw(a, shortfall, 'free')
                        if shortfall > 0:
                            for a in sim_assets:
                                if shortfall <= 0: break
                                if a.get('Type') in ['Traditional 401(k)', 'Traditional IRA']: shortfall = _withdraw(a,
                                                                                                                     shortfall,
                                                                                                                     'ordinary')

                    # --- Sequence 4: Complete Liquidity Failure -> Shortfall Debt ---
                    if shortfall > 0:
                        unfunded_debt_bal += shortfall
                        yd["Income: Shortfall Debt Funded"] = shortfall

                # Check for Critical Shortfall Alert
                if unfunded_debt_bal > 0 and prev_unfunded_debt_bal == 0:
                    if year not in milestones_by_year: milestones_by_year[year] = []
                    milestones_by_year[year].append(
                        {"desc": "🚨 MAJOR SHORTFALL: Retirement Accounts Depleted!", "amt": unfunded_debt_bal,
                         "type": "critical"})

                liquid_assets_total = 0
                for a in sim_assets:
                    # Ensure no floating point math drags balance below absolute zero
                    a['bal'] = max(0, a['bal'])
                    liquid_assets_total += a['bal']
                    nw_yd[f"Asset: {a.get('Account Name', 'Account')}"] = a['bal']

                net_worth = liquid_assets_total + re_equity + cur_biz_val - debt_bal_total - unfunded_debt_bal

                nw_yd["Total Liquid Assets"] = liquid_assets_total
                nw_yd["Total Real Estate Equity"] = re_equity
                nw_yd["Total Business Equity"] = cur_biz_val
                nw_yd["Total Debt Liabilities"] = -(debt_bal_total + unfunded_debt_bal)
                nw_yd["Total Net Worth"] = net_worth

                # Check for 529 Depletion Milestones
                for a in sim_assets:
                    if a['bal'] <= 0 and prev_ast_bals.get(a['Account Name'], 0) > 0:
                        if a.get('Type') == '529 Plan':
                            if year not in milestones_by_year: milestones_by_year[year] = []
                            milestones_by_year[year].append(
                                {"desc": f"🎓 529 Plan Depleted: {a['Account Name']}", "amt": 0, "type": "system"})
                    prev_ast_bals[a['Account Name']] = a['bal']

                sim_res.append({"Year": year, "Age (Primary)": my_current_age, "Age (Spouse)": spouse_current_age,
                                "Annual Income": annual_inc, "Annual Expenses": total_exp,
                                "Annual Taxes": yd.get("Expense: Taxes", 0), "Annual Net Savings": yd["Net Savings"],
                                "Liquid Assets": liquid_assets_total,
                                "Real Estate Equity": re_equity, "Business Equity": cur_biz_val,
                                "Debt": -debt_bal_total, "Unfunded Debt": unfunded_debt_bal, "Net Worth": net_worth})
                det_res.append(yd)
                nw_det_res.append(nw_yd)

            return sim_res, det_res, nw_det_res, milestones_by_year


        @st.cache_data(show_spinner=False)
        def run_cached_simulation(mkt_sequence_tuple, ctx_str, _email):
            ctx = json.loads(ctx_str)
            mkt_sequence = list(mkt_sequence_tuple)
            s_res, d_res, nw_res, milestones = run_simulation(mkt_sequence, ctx)
            return pd.DataFrame(s_res), pd.DataFrame(d_res).fillna(0), pd.DataFrame(nw_res).fillna(0), milestones


        # --- EXECUTE BASE DETERMINISTIC RUN ---
        deterministic_seq = tuple([mkt] * (max_years + 1))
        ctx_json_str = json.dumps(sim_ctx, sort_keys=True)

        df_sim_nominal, df_det_nominal, df_nw_nominal, run_milestones = run_cached_simulation(deterministic_seq,
                                                                                              ctx_json_str,
                                                                                              st.session_state.get(
                                                                                                  'user_email',
                                                                                                  'guest'))

        # --- UI RENDER: DASHBOARD ---
        if not df_sim_nominal.empty:
            df_sim = df_sim_nominal.copy()
            df_det = df_det_nominal.copy()
            df_nw = df_nw_nominal.copy()

            final_nw = df_sim.iloc[-1]['Net Worth']
            shortfall_mask = df_sim['Unfunded Debt'] > 0
            deplete_year = df_sim[shortfall_mask]['Year'].min() if not df_sim[shortfall_mask].empty else None
            deplete_age = df_sim[shortfall_mask]['Age (Primary)'].min() if not df_sim[shortfall_mask].empty else None

            c_status, c_ai_btn = st.columns([3, 2])
            with c_status:
                if deplete_year is not None:
                    st.error(
                        f"🔴 **Liquidity Crisis:** You completely exhaust your liquid cash in **Year {int(deplete_year)}** (Age {int(deplete_age)}) and begin accumulating high-interest shortfall debt.")
                elif final_nw >= 1000000:
                    st.success(
                        f"🟢 **On Track:** Projected Net Worth at timeline end is **${final_nw:,.0f}**. Your assets comfortably outlive your life expectancy.")
                elif final_nw > 0:
                    st.warning(
                        f"🟡 **Caution:** Projected Net Worth at timeline end is **${final_nw:,.0f}**. You are solvent, but with a narrow margin of safety.")

            # APPLY DISCOUNTING IF TOGGLED (Vectorized execution for extreme performance)
            if view_todays_dollars:
                discounts = (1 + infl / 100) ** (df_sim['Year'] - current_year)

                cols_sim = ["Annual Income", "Annual Expenses", "Annual Taxes", "Annual Net Savings", "Liquid Assets",
                            "Real Estate Equity", "Business Equity", "Debt", "Unfunded Debt", "Net Worth"]
                df_sim[cols_sim] = df_sim[cols_sim].div(discounts, axis=0)

                cols_det = [c for c in df_det.columns if
                            c not in ["Age (Primary)", "Age (Spouse)", "Year"] and pd.api.types.is_numeric_dtype(
                                df_det[c])]
                df_det[cols_det] = df_det[cols_det].div(discounts, axis=0)

                cols_nw = [c for c in df_nw.columns if
                           c not in ["Age (Primary)", "Age (Spouse)", "Year"] and pd.api.types.is_numeric_dtype(
                               df_nw[c])]
                df_nw[cols_nw] = df_nw[cols_nw].div(discounts, axis=0)

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

                # Plot individual granular asset buckets dynamically
                ast_cols = [c for c in df_nw.columns if c.startswith("Asset: ")]
                fill_colors = ['rgba(45, 212, 191, 0.6)', 'rgba(56, 189, 248, 0.6)', 'rgba(129, 140, 248, 0.6)',
                               'rgba(167, 139, 250, 0.6)', 'rgba(232, 121, 249, 0.6)', 'rgba(251, 113, 133, 0.6)',
                               'rgba(52, 211, 153, 0.6)', 'rgba(251, 191, 36, 0.6)', 'rgba(163, 230, 53, 0.6)',
                               'rgba(250, 204, 21, 0.6)']
                line_colors = ['#2dd4bf', '#38bdf8', '#818cf8', '#a78bfa', '#e879f9', '#fb7185', '#34d399', '#fbbf24',
                               '#a3e635', '#facc15']

                for i, col in enumerate(ast_cols):
                    asset_name = col.replace("Asset: ", "")
                    fig_nw.add_trace(go.Scatter(
                        x=df_nw["Year"], y=df_nw[col], mode='lines', stackgroup='one', name=asset_name,
                        fillcolor=fill_colors[i % len(fill_colors)],
                        line=dict(color=line_colors[i % len(line_colors)], width=1.5)
                    ))

                fig_nw.add_trace(
                    go.Scatter(x=df_nw["Year"], y=df_nw["Total Real Estate Equity"], mode='lines', stackgroup='one',
                               name='Real Estate Equity', fillcolor='rgba(139, 92, 246, 0.5)',
                               line=dict(color='#8b5cf6', width=1.5)))
                fig_nw.add_trace(
                    go.Scatter(x=df_nw["Year"], y=df_nw["Total Business Equity"], mode='lines', stackgroup='one',
                               name='Business Equity', fillcolor='rgba(245, 158, 11, 0.5)',
                               line=dict(color='#f59e0b', width=1.5)))
                fig_nw.add_trace(
                    go.Scatter(x=df_nw["Year"], y=df_nw["Total Debt Liabilities"], mode='lines', stackgroup='two',
                               name='Total Liabilities (Inc. Shortfalls)', fillcolor='rgba(244, 63, 94, 0.5)',
                               line=dict(color='#f43f5e', width=1.5)))
                fig_nw.add_trace(
                    go.Scatter(x=df_nw["Year"], y=df_nw["Total Net Worth"], mode='lines', name='Total Net Worth',
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
                st.plotly_chart(fig_nw, width="stretch")

                st.write("#### Annual Cash Flow & Progressive Taxes")
                fig_cf = go.Figure()
                fig_cf.add_trace(
                    go.Scatter(x=df_sim["Year"], y=df_sim["Annual Income"], mode='lines', name='Organic Income',
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
                st.plotly_chart(fig_cf, width="stretch")

                # --- SANKEY DIAGRAM ---
                st.divider()
                sankey_title = "#### 🌊 Cash Flow Sankey Snapshot" + (" (Today's $)" if view_todays_dollars else "")
                st.write(sankey_title)
                st.markdown(
                    '<div class="info-text">💡 <strong>Follow the Money:</strong> Select any year on the slider to see exactly where your money comes from and where it goes.</div>',
                    unsafe_allow_html=True)

                min_yr = int(df_sim['Year'].min())
                max_yr = int(df_sim['Year'].max())

                if min_yr < max_yr:
                    sankey_year = st.slider("Select Year for Cash Flow Snapshot", min_value=min_yr, max_value=max_yr,
                                            value=min_yr, key="sankey_slider")
                else:
                    sankey_year = min_yr

                if sankey_year in df_det['Year'].values:
                    row = df_det[df_det['Year'] == sankey_year].iloc[0]

                    inflows = {k.replace('Income: ', ''): v for k, v in row.items() if
                               k.startswith('Income:') and v > 0 and k != 'Income: Shortfall Debt Funded'}
                    outflows = {k.replace('Expense: ', ''): v for k, v in row.items() if
                                k.startswith('Expense:') and v > 0 and k not in ['Expense: Unallocated Surplus Saved']}

                    net_savings = row.get('Net Savings', 0)
                    if net_savings > 0:
                        outflows['Cashflow: Surplus Reinvested'] = net_savings
                    elif net_savings < 0:
                        inflows['Shortfall Debt Funded'] = abs(net_savings)

                    in_labels = [f"{k}<br>${v:,.0f}" for k, v in inflows.items()]
                    out_labels = [f"{k}<br>${v:,.0f}" for k, v in outflows.items()]
                    total_inflow = sum(inflows.values())
                    mid_label = f"Total Cash Pool<br>${total_inflow:,.0f}"

                    labels = in_labels + [mid_label] + out_labels
                    middle_idx = len(inflows)

                    source = []
                    target = []
                    value = []
                    node_colors = []
                    link_colors = []

                    # Build Inflows -> Middle
                    for i, (k, v) in enumerate(inflows.items()):
                        source.append(i)
                        target.append(middle_idx)
                        value.append(v)
                        node_colors.append('#f43f5e' if k == 'Shortfall Debt Funded' else '#10b981')
                        link_colors.append(
                            'rgba(244, 63, 94, 0.4)' if k == 'Shortfall Debt Funded' else 'rgba(16, 185, 129, 0.4)')

                    node_colors.append('#3b82f6')  # Middle node color

                    # Build Middle -> Outflows
                    for i, (k, v) in enumerate(outflows.items()):
                        source.append(middle_idx)
                        target.append(middle_idx + 1 + i)
                        value.append(v)
                        node_colors.append('#10b981' if k in ['Portfolio Contributions',
                                                              'Cashflow: Surplus Reinvested'] else '#f43f5e')
                        link_colors.append('rgba(16, 185, 129, 0.4)' if k in ['Portfolio Contributions',
                                                                              'Cashflow: Surplus Reinvested'] else 'rgba(244, 63, 94, 0.4)')

                    fig_sankey = go.Figure(data=[go.Sankey(
                        arrangement="snap",
                        node=dict(
                            pad=35,
                            thickness=30,
                            line=dict(color="black", width=0.5),
                            label=labels,
                            color=node_colors
                        ),
                        textfont=dict(color="black", size=12),
                        link=dict(
                            source=source,
                            target=target,
                            value=value,
                            color=link_colors
                        )
                    )])
                    fig_sankey.update_layout(height=750, margin=dict(l=0, r=0, t=30, b=0), font=dict(size=12))
                    st.plotly_chart(fig_sankey, width="stretch")

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

            with col_mc3:
                st.markdown("<div style='height: 27px;'></div>", unsafe_allow_html=True)
                st.markdown('<div class="ai-btn-marker"></div>', unsafe_allow_html=True)
                if st.button("✨ Run Monte Carlo Simulation", width="stretch"):
                    with st.spinner(f"Rendering {mc_runs} parallel market sequences (Multi-threaded)..."):
                        success_count = 0
                        all_nw_paths = []
                        mc_progress = st.progress(0)

                        # Generate random market sequences in advance
                        random_sequences = [[random.gauss(mkt, mc_vol) for _ in range(max_years + 1)] for _ in
                                            range(mc_runs)]

                        # Execute deeply nested simulation via fast ThreadPool
                        try:
                            with ThreadPoolExecutor(max_workers=min(mc_runs, 8)) as executor:
                                futures = [executor.submit(run_simulation, seq, copy.deepcopy(sim_ctx)) for seq in
                                           random_sequences]

                                for i, future in enumerate(futures):
                                    res, _, _, _ = future.result()
                                    nw_path = [step["Net Worth"] for step in res]
                                    all_nw_paths.append(nw_path)

                                    # A plan is successful if it finishes without carrying shortfall debt
                                    if res[-1].get("Unfunded Debt", 0) <= 0:
                                        success_count += 1

                                    if i % max(1, mc_runs // 20) == 0:
                                        mc_progress.progress((i + 1) / mc_runs)
                        except Exception as e:
                            st.error(f"Simulation failed during multi-threading: {e}")
                        finally:
                            mc_progress.empty()

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
                            fig_mc.add_trace(go.Scatter(x=years_list, y=p90, mode='lines',
                                                        name='90th Percentile (Favorable Timeline)',
                                                        line=dict(color='#10b981', dash='dot')))
                            fig_mc.add_trace(go.Scatter(x=years_list, y=p50, mode='lines',
                                                        name='50th Percentile (Median Expectation)',
                                                        line=dict(color='#3b82f6', width=3)))
                            fig_mc.add_trace(go.Scatter(x=years_list, y=p10, mode='lines',
                                                        name='10th Percentile (Severe Contraction)',
                                                        line=dict(color='#f43f5e', dash='dot')))
                            fig_mc.update_layout(title="Stochastic Net Worth Projections", hovermode="x unified",
                                                 yaxis=dict(tickformat="$,.0f"), margin=dict(l=0, r=0, t=40, b=0),
                                                 legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right",
                                                             x=1))
                            st.plotly_chart(fig_mc, width="stretch")

            # --- DATA AUDIT TABLES ---
            st.divider()
            csv = df_sim.to_csv(index=False).encode('utf-8')
            st.download_button(label="📥 Download Full Simulation (.csv)", data=csv,
                               file_name='retirement_simulation.csv', mime='text/csv', type="secondary")

            t1, t2 = st.tabs(["Income & Expense Log", "Net Worth Log"])
            with t1:
                st.subheader("Detailed Tax & Expense Log")
                inc_c = sorted([c for c in df_det.columns if
                                c.startswith("Income:") or c.startswith("Roth") or c.startswith("Cashflow:")])
                exp_c = sorted([c for c in df_det.columns if c.startswith("Expense:")])
                ord_det = ["Year", "Age (Primary)", "Age (Spouse)"] + inc_c + exp_c + ["Net Savings"]
                st.dataframe(df_det[ord_det].set_index("Year").style.format(
                    {c: "${:,.0f}" for c in ord_det if c not in ["Age (Primary)", "Age (Spouse)", "Year"]} | {
                        "Age (Primary)": "{:.0f}", "Age (Spouse)": "{:.0f}"}), width="stretch")

            with t2:
                st.subheader("Detailed Net Worth Log")
                st.markdown(
                    "Track the exact, year-by-year balance of every single asset account and liability to trace your drawdowns and growth.")
                ast_c = sorted([c for c in df_nw.columns if c.startswith("Asset:")])
                ord_nw = ["Year", "Age (Primary)", "Age (Spouse)"] + ast_c + ["Total Liquid Assets",
                                                                              "Total Real Estate Equity",
                                                                              "Total Business Equity",
                                                                              "Total Debt Liabilities",
                                                                              "Total Net Worth"]
                st.dataframe(df_nw[ord_nw].set_index("Year").style.format(
                    {c: "${:,.0f}" for c in ord_nw if c not in ["Age (Primary)", "Age (Spouse)", "Year"]} | {
                        "Age (Primary)": "{:.0f}", "Age (Spouse)": "{:.0f}"}), width="stretch")

# --- 10. AI FIDUCIARY REPORT & WHAT-IF SIMULATOR ---
with st.expander("🤖 10. AI Fiduciary Health & What-If Simulator", expanded=False):
    st.markdown(
        '<div class="info-text">💡 This engine extracts a 5-year interval timeseries snapshot of your entire financial life (Age, Net Worth, Liquid Cash, Income, Expenses, and Taxes). The AI acts as a fiduciary and analyzes your cash flows chronologically to provide tactical, phase-by-phase advice on Roth conversions, sequence of returns, and tax optimization.</div>',
        unsafe_allow_html=True)

    tab_report, tab_whatif = st.tabs(["📊 Comprehensive Health Report", "🔮 What-If Simulator"])

    # Store standard data extraction here so both tabs can securely access it
    if 'df_sim' in locals() and not df_sim.empty:
        sim_summary = {
            "Current Age": my_age, "Retirement Age": ret_age, "Life Expectancy": my_life_exp_val,
            "Current Net Worth": df_sim.iloc[0]['Net Worth'], "Final Net Worth": df_sim.iloc[-1]['Net Worth'],
            "Shortfall Year": str(deplete_year) if deplete_year is not None else "None"
        }

        # Compress 50 years of data into 5-year leaps so the AI can digest the timeline without context limits
        timeline_summary = []
        for idx, row in df_sim.iloc[::5].iterrows():
            timeline_summary.append({
                "Age": int(row["Age (Primary)"]),
                "Income": int(row["Annual Income"]),
                "Expenses": int(row["Annual Expenses"]),
                "Taxes": int(row["Annual Taxes"]),
                "Liquid_Assets": int(row["Liquid Assets"]),
                "Net_Worth": int(row["Net Worth"])
            })
        # Always append the final year
        last_row = df_sim.iloc[-1]
        timeline_summary.append({"Age": int(last_row["Age (Primary)"]), "Income": int(last_row["Annual Income"]),
                                 "Expenses": int(last_row["Annual Expenses"]), "Taxes": int(last_row["Annual Taxes"]),
                                 "Liquid_Assets": int(last_row["Liquid Assets"]),
                                 "Net_Worth": int(last_row["Net Worth"])})
    else:
        sim_summary = {}
        timeline_summary = []

    with tab_report:
        st.markdown('<div class="ai-btn-marker"></div>', unsafe_allow_html=True)
        if st.button("✨ Generate Comprehensive AI Report", width="stretch", key="btn_report"):
            if sim_summary:
                with st.spinner("AI extracting timeseries data and acting as fiduciary advisor..."):
                    prompt = f"Act as an expert fiduciary financial planner. Review this user's summary: {json.dumps(sim_summary)} and their chronological 5-year cash flow progression: {json.dumps(timeline_summary)}. Provide a highly detailed, year-by-year or phase-by-phase tactical analysis. Focus on specific strategies they can use to optimize their tax buckets (e.g., when exactly to execute Roth conversions before RMDs begin), sequence of withdrawals, and managing the gaps between retirement and Social Security/Medicare. Return ONLY valid JSON exactly like this: {{\"analysis\": \"your detailed markdown text here, using \\n for line breaks\"}}"
                    res = call_gemini_json(prompt)
                    if res and 'analysis' in res:
                        st.session_state['ai_analysis_report'] = res['analysis']
                    else:
                        st.error("⚠️ AI Analysis failed to generate.")
            else:
                st.warning("Please run the baseline simulation first.")

        if 'ai_analysis_report' in st.session_state:
            st.info(st.session_state['ai_analysis_report'].replace('\\n', '\n').replace('$', r'\$'))

    with tab_whatif:
        what_if_query = st.text_area(
            "Ask the AI to simulate a scenario (e.g., 'What if I sold my rental property in 2030 and put the cash in my brokerage?' or 'What if I added $50k in income starting in 2029?')",
            key="what_if_text")

        st.markdown('<div class="ai-btn-marker"></div>', unsafe_allow_html=True)
        if st.button("✨ Run What-If Analysis (AI)", width="stretch", key="btn_whatif"):
            if sim_summary and what_if_query:
                with st.spinner("AI processing alternative timelines and computing what-if scenario..."):
                    prompt = f"Act as an expert fiduciary financial planner. Review this user's baseline simulation summary: {json.dumps(sim_summary)} and their chronological 5-year cash flow progression: {json.dumps(timeline_summary)}. The user wants to run the following 'what-if' scenario: '{what_if_query}'. Analyze how this change would mathematically and strategically impact their net worth, cash flow, and tax strategy compared to the baseline. Provide a highly detailed, reasonable estimate and tactical breakdown of this scenario. Return ONLY valid JSON exactly like this: {{\"analysis\": \"your detailed markdown text here, using \\n for line breaks\"}}"
                    res = call_gemini_json(prompt)
                    if res and 'analysis' in res:
                        st.session_state['what_if_analysis_report'] = res['analysis']
                    else:
                        st.error("⚠️ AI Analysis failed to generate.")
            elif not what_if_query:
                st.warning("Please enter a scenario to simulate.")
            else:
                st.warning("Please run the baseline simulation first.")

        if 'what_if_analysis_report' in st.session_state:
            st.success(st.session_state['what_if_analysis_report'].replace('\\n', '\n').replace('$', r'\$'))

# --- 11. FAQ SECTION ---
with st.expander("📖 11. Complete Beginner's Guide & FAQ", expanded=False):
    st.markdown("""
    ### 🌟 GETTING STARTED
    **Q: What exactly does this app do, and how is it different from a basic retirement calculator?**
    **A:** Most retirement calculators ask you two questions — "how much do you have saved?" and "when do you want to retire?" — then spit out a single number. This app is fundamentally different. It builds a living, breathing financial model of your entire life — from today until the end of your life expectancy — and simulates every dollar coming in and going out, year by year.
    It accounts for things a basic calculator completely ignores: your mortgage paying itself off over time, your kids going to college, your taxes changing when you retire, Social Security kicking in, Medicare starting at 65, your investment accounts being drawn down in the smartest possible tax order, and hundreds of other real-life events. The result isn't just a number — it's a full financial roadmap with warnings, milestones, and actionable advice.

    **Q: Is this app a replacement for a financial advisor?**
    **A:** No, and it's important to be honest about that. This app is an incredibly powerful planning and education tool — it helps you understand your own numbers, test scenarios, and have much smarter conversations with professionals. But it is not a licensed fiduciary advisor and cannot account for every personal circumstance, recent law change, or market condition. Think of it the way you'd think of WebMD: extremely useful for understanding what's happening with your health, but you still want a doctor making the final call on major decisions. Use this app to get clarity, then bring your printouts to a CPA or Certified Financial Planner (CFP) for personalized guidance.

    **Q: How accurate are these projections?**
    **A:** The projections are as accurate as the information you put in, combined with the assumptions you choose. The simulation engine uses real IRS tax brackets, real Social Security claiming rules, and real Medicare cost structures. However, it cannot predict the future — no tool can. What it can do is show you the most mathematically likely outcomes based on historical data and your personal numbers. The Monte Carlo feature (explained later) is specifically designed to stress-test your plan against hundreds of unpredictable futures so you understand your real risk, not just the rosy average-case scenario.

    ### 👨‍👩‍👧‍👦 YOUR PROFILE & FAMILY
    **Q: Why does the app need my exact date of birth instead of just my age?**
    **A:** Because a few months can actually matter quite a bit in retirement planning. Your exact birth year determines three important things:
    * **Your Social Security Full Retirement Age (FRA):** If you were born in 1960 or later, your FRA is 67. Born between 1955-1959, it's somewhere between 66 and 67. This is the age at which you receive your full Social Security benefit — claim earlier and you get permanently less, claim later and you get permanently more.
    * **When your RMDs begin:** RMD stands for Required Minimum Distribution. The IRS forces you to start withdrawing money from your traditional retirement accounts at a certain age — either 73 or 75, depending on your birth year. Getting this wrong by even one year can create a surprise tax bill.
    * **Your IRS Catch-Up Contribution eligibility:** Once you turn 50, you're allowed to contribute extra money to your 401(k) and IRA above the normal limits. The app needs your age to know when to apply this bonus.

    **Q: What changes when I add a spouse?**
    **A:** Quite a lot, actually. Adding a spouse unlocks a completely different set of tax rules that are generally more favorable:
    * Married Filing Jointly (MFJ) tax brackets are roughly double the single brackets, meaning you pay a lower tax rate on the same income.
    * Standard Deduction doubles from approximately $14,600 to $29,200.
    * Social Security survivor benefits activate — if one spouse passes away, the surviving spouse automatically inherits the higher of the two Social Security benefit amounts.
    * Retirement account strategies change — the app can now optimize withdrawals across two people's accounts.
    * Lifestyle cost reduction — the simulation realistically models that a surviving spouse spends less (roughly 60% of the couple's former expenses) after the other passes.

    **Q: What are "dependents" used for in the simulation?**
    **A:** The app uses your children's ages to automatically time several important financial events. It models when child-related expenses (extracurricular activities, higher grocery bills, larger utility costs) naturally phase out as each child grows up and leaves home. It also uses their ages to calculate exactly when college tuition expenses should start and end in your cash flow. If you have a 529 college savings plan, the app connects it directly to your specific child's tuition costs, drawing down that account automatically when the bills arrive.

    ### 💵 INCOME & SOCIAL SECURITY
    **Q: What is an "employer 401(k) match" and why is it listed under income instead of savings?**
    **A:** An employer match is essentially free money your company adds to your retirement account when you contribute your own money. For example, your employer might match 50 cents for every dollar you put in, up to 6% of your salary. It's listed under "Income" purely so the app can track it separately from your own contributions — this is important because employer match money goes directly into your 401(k) and is never spendable take-home cash. The app is careful to never count it as money you can spend on bills, but it does add it to your growing 401(k) balance behind the scenes so your retirement account grows accurately.

    **Q: What is Social Security, and how does the app calculate my benefit?**
    **A:** Social Security is a federal retirement program you've been paying into your entire working life through payroll taxes (you'll see it labeled "FICA" on your pay stub). When you retire, you receive a monthly payment for life based on your 35 highest-earning years of work history. The app's AI can estimate your benefit based on your current age and income. The exact amount is also available on your personal Social Security statement at ssa.gov, which is always the most accurate source.

    The critical decision the app helps you model is when to claim:
    * Claim at 62 (earliest possible): Your benefit is permanently reduced by up to 30%.
    * Claim at your Full Retirement Age (66-67): You receive 100% of your earned benefit.
    * Claim at 70 (latest recommended): Your benefit is permanently increased by up to 24%.

    There is no single "right" answer — it depends on your health, other income sources, and whether you're married. The app lets you test different claiming ages to see the lifetime impact.

    **Q: What does "taxable Social Security" mean? I thought Social Security wasn't taxed?**
    **A:** This is one of the most surprising things people discover about retirement. Social Security can be taxed — up to 85% of your benefit can be added to your taxable income depending on how much other income you have. The IRS uses something called "Provisional Income" (basically your other income plus half your Social Security) to determine how much of your benefit is taxable. If your Provisional Income is low enough, your Social Security is completely tax-free. As your other income (from RMDs, investments, rent, etc.) rises, more of your Social Security becomes taxable. The app calculates this automatically every single year using the exact IRS formula — this is something most retirement calculators completely ignore, and it can represent tens of thousands of dollars in unexpected taxes.

    **Q: What is a pension and how is it different from a 401(k)?**
    **A:** A pension (sometimes called a "Defined Benefit" plan) is a retirement plan — common in government jobs, teaching, and some union jobs — where your employer guarantees you a specific monthly payment for life when you retire, regardless of what the stock market does. A 401(k) (called a "Defined Contribution" plan) is an account where you and your employer put money in and invest it, but the final amount you end up with depends entirely on how the market performed. Pensions are becoming increasingly rare in the private sector. If you have one, enter it as an income stream that starts at your retirement date.

    ### 🏦 ASSETS, ACCOUNTS & INVESTING
    **Q: What is the difference between all these account types? (401k, IRA, Roth, Brokerage...)**
    **A:** This is one of the most important concepts to understand. All of these are just containers that hold your investments — the difference is purely about when the IRS taxes the money inside them:
    * **Traditional 401(k) and Traditional IRA — "Pay taxes later":** You put money in before paying taxes (it reduces your taxable income today). Your money grows tax-free inside the account. When you withdraw it in retirement, you pay income taxes on every dollar you take out. The IRS forces you to start withdrawing at age 73 or 75 (called RMDs).
    * **Roth 401(k) and Roth IRA — "Pay taxes now, never again":** You put money in after already paying taxes on it. Your money grows tax-free and you can withdraw it in retirement completely tax-free, with no RMDs ever required. This is generally better if you expect to be in a higher tax bracket in retirement than you are today.
    * **Brokerage (Taxable) Account — "Pay taxes as you go":** A regular investment account with no special tax protection. You pay taxes on dividends and interest each year, and when you sell investments for a profit, you pay Capital Gains tax. However, there are no contribution limits and no restrictions on withdrawals. Also benefits from a special "step-up in basis" rule when you pass away (explained later).
    * **HSA (Health Savings Account) — "Triple tax advantage":** If you have a high-deductible health insurance plan, an HSA is arguably the best account available. You contribute pre-tax, it grows tax-free, and withdrawals for medical expenses are tax-free. After age 65, you can withdraw for any reason (just pay income tax, like a Traditional IRA). Many financial planners call this a "stealth retirement account."
    * **529 Plan — "Tax-free college savings":** Money invested in a 529 plan grows tax-free and can be withdrawn completely tax-free when used for qualified education expenses (tuition, room and board, books). The app automatically drains your 529 plans when college tuition expenses hit your cash flow timeline.

    **Q: What are "RMDs" and why do they matter so much?**
    **A:** RMD stands for Required Minimum Distribution. The IRS has a simple rule: you cannot keep money in a Traditional 401(k) or IRA forever. Starting at age 73 (or 75 if you were born in 1960 or later), you must withdraw a minimum amount every single year, whether you need the money or not. The amount is calculated by dividing your account balance by a life expectancy factor from an IRS table.

    Why do they matter? Because every dollar you're forced to withdraw is added to your taxable income that year — which can push you into a higher tax bracket, cause more of your Social Security to become taxable, and trigger Medicare surcharges (IRMAA). People with large Traditional 401(k) balances can face an unexpected "tax time bomb" in their 70s. This is exactly why the Roth Conversion feature exists — to proactively move money out of your Traditional accounts in low-tax years before RMDs force the issue.

    **Q: What are contribution limits? Why does the app warn me if I'm contributing too much?**
    **A:** The IRS sets strict annual limits on how much you can contribute to retirement accounts. For 2026, these are approximately:
    * 401(k): $23,500/year ($31,000 if you're 50 or older, thanks to "catch-up contributions")
    * IRA: $7,000/year ($8,000 if you're 50 or older)

    If you enter contributions above these limits, the app warns you because contributing over the limit triggers IRS penalties. The simulation automatically caps your contributions at the legal maximum so your projections remain realistic, even if you entered a higher number.

    ### 🏡 REAL ESTATE & MORTGAGES
    **Q: How does the app handle my mortgage?**
    **A:** You simply enter three things: your current loan balance, your interest rate, and your monthly payment. The app then does something most calculators don't — it mathematically pays down your mortgage month by month, exactly as your bank would, separating out the interest and principal portions correctly. When the balance finally hits zero, the mortgage expense automatically disappears from your cash flow for every future year. You should NOT separately list your mortgage payment in the budget section — the app handles it entirely through your real estate entry.

    **Q: What is "home equity" and how does it affect my net worth?**
    **A:** Home equity is simply what you'd have left over if you sold your home and paid off the mortgage: Market Value minus Mortgage Balance. If your home is worth $400,000 and you owe $250,000, you have $150,000 in equity. As the years go by, your equity grows in two ways: your mortgage balance decreases as you make payments, and your home's market value (hopefully) increases with property appreciation. The app tracks both of these automatically and includes your home equity in your total net worth calculation.

    **Q: What's the difference between a "primary residence" and an "investment property" in the app?**
    **A:** The app treats these completely differently:
    * **Your primary residence is where you live.** Its mortgage payment and monthly expenses (property taxes, insurance, HOA) flow out as living costs. Any rental income you happen to earn from it (like renting a room) flows in as income.
    * **An investment property is treated as a business.** The app calculates the net cash flow — rent collected minus mortgage payment minus expenses — and only the net profit or loss affects your overall cash flow. If the property generates $2,000/month in rent but costs $1,800/month in mortgage and expenses, only the $200 net profit shows up in your income. This prevents investment properties from artificially inflating your apparent lifestyle income.

    ### 💸 BUDGETS & EXPENSES
    **Q: The expense table has "Start Phase" and "End Phase" — what do these mean?**
    **A:** These control exactly when each expense is active in your lifetime simulation:
    * "Now" means the expense starts today and continues until whatever end phase you choose.
    * "At Retirement" means the expense either starts when you retire (like a new travel budget) or ends when you retire (like your work commute costs).
    * "End of Life" means the expense continues until the very last year of your simulation.
    * "Custom Year" lets you enter a specific year — useful for things like college tuition (starts in 2029, ends in 2032) or a car payment that ends in 2027.

    A critical rule: if an expense changes at retirement (like your grocery bill going down slightly), you should create TWO rows — one that goes from "Now" to "At Retirement," and a separate one that goes from "At Retirement" to "End of Life" with the new amount. Do not try to make one row cover both phases.

    **Q: Should I include my mortgage payment in the budget section?**
    **A:** No. If you've entered your property in the Real Estate section with your mortgage balance and payment, the app already handles all of that math. Adding it again in the budget would double-count it and completely distort your cash flow projections. The same applies to any other debts you've entered in the Debts section — car loans, student loans, etc. entered there should NOT appear in the budget.

    **Q: The AI generated a lot of expenses automatically. Should I trust them?**
    **A:** The AI estimates are a solid starting point based on your city, family size, and income level — but you should always review and adjust them. Think of the AI as a well-informed assistant who has never actually lived your life. It might overestimate your dining out budget if you cook at home, or underestimate your travel budget if you're an avid traveler. The "🤖 AI?" checkbox column helps you quickly identify which rows were AI-generated versus ones you entered yourself. Always go through the list critically and adjust anything that doesn't match your actual lifestyle.

    **Q: How does healthcare inflation work, and why is it different from regular inflation?**
    **A:** Regular consumer inflation (food, clothing, electronics, etc.) has historically averaged around 2-3% per year. Healthcare costs, however, have consistently risen at 5-7% per year for decades — nearly double the overall inflation rate. This means a healthcare expense that costs $500/month today might cost over $1,300/month in 20 years if healthcare inflation continues at historical rates. The app applies a separate, higher inflation rate specifically to anything categorized as "Healthcare" or "Insurance" to capture this reality. This is one of the most important reasons not to underestimate your future healthcare costs.

    ### 🏥 HEALTHCARE & MEDICARE
    **Q: What is the "Pre-Medicare Gap" and why is it so expensive?**
    **A:** If you retire before age 65, you face a potentially brutal financial gap. Most working Americans get health insurance through their employer — it's one of the most valuable parts of your compensation package, and your employer typically pays the majority of the premium. The moment you retire, that coverage ends. You're now on your own for health insurance until Medicare kicks in at age 65.

    Buying private health insurance for a 60-year-old can easily cost $1,000-$2,000+ per month just in premiums, before copays and deductibles. This is called the "Pre-Medicare Gap" and it catches many early retirees completely off guard. The app automatically adds this cost to your simulation if you retire before 65, scaled to your income level (since lower-income retirees may qualify for ACA subsidies that reduce the cost).

    **Q: What is Medicare? Is it free?**
    **A:** Medicare is the federal health insurance program for Americans 65 and older. It is definitely not free, though it's generally much cheaper than private insurance. It has several parts:
    * Part A (Hospital): Usually free if you've worked 10+ years.
    * Part B (Doctor visits, outpatient): Costs approximately $185/month in 2026, automatically deducted from your Social Security check.
    * Part D (Prescription drugs): Additional monthly premium, varies by plan.
    * Medigap/Supplement: Optional private insurance to cover what Medicare doesn't.

    The app models Medicare kicking in at age 65 and automatically reduces your healthcare budget at that point to reflect the generally lower costs compared to private insurance.

    **Q: What is "IRMAA" and why might I have to pay extra for Medicare?**
    **A:** IRMAA stands for Income-Related Monthly Adjustment Amount. It's essentially a Medicare "high earner surcharge." If your income in retirement exceeds certain thresholds (starting around $103,000/year for singles or $206,000/year for couples in 2026 dollars), the government charges you extra for your Medicare Part B and Part D premiums. The surcharges can be significant — anywhere from an extra $1,000 to $6,500+ per year.

    Here's the sneaky part: the income the government uses to calculate IRMAA includes your RMDs, Social Security, investment income, and Roth conversions. So people who did everything "right" by accumulating large retirement accounts can end up triggering IRMAA surcharges they never anticipated. The app calculates and applies IRMAA automatically every year based on your projected income — which is a feature most retirement planning tools don't include at all.

    **Q: What is "Long-Term Care" and why is it in the stress tests?**
    **A:** Long-Term Care (LTC) refers to extended help with daily activities — bathing, dressing, eating, managing medications — typically needed in the final years of life due to illness, disability, or cognitive decline like dementia. This care is extremely expensive: a private nursing home room in the US costs $90,000-$120,000+ per year on average, and this is almost entirely NOT covered by regular Medicare.

    The "LTC Shock" stress test injects a large medical expense into the final 2-3 years of your simulation to show what happens to your plan if you or a spouse needs this level of care. It's a sobering but important test — long-term care is the single largest unexpected expense that derails retirement plans, and the odds of needing some form of it in your lifetime are higher than most people realize (roughly 70% of people over 65 will need some level of long-term care).

    ### 📊 TAXES
    **Q: What is a "progressive" tax system? Why don't I just multiply my income by my tax rate?**
    **A:** The US uses a system where higher income is taxed at progressively higher rates — but crucially, only the portion of your income that falls within each "bracket" is taxed at that bracket's rate. The common misconception is that if you're in the "24% tax bracket," all your income is taxed at 24%. That's wrong.

    Think of it like filling buckets. The first $23,200 of a married couple's income fills the 10% bucket — taxed at just 10%. The next chunk of income fills the 12% bucket, and so on. Only income above the highest bracket threshold gets taxed at the top rate. This is why your actual tax bill is almost always less than your bracket rate would suggest, and why tax planning — like Roth conversions timed to stay within a lower bracket — can save significant money.

    **Q: What is the difference between "marginal tax rate" and "effective tax rate"?**
    **A:** Your marginal tax rate is the rate you'd pay on your next dollar of income — it's your "top bracket." If you're a married couple making $200,000, your marginal rate is 22% (meaning the last dollars of income are taxed at 22%).

    Your effective tax rate is what you actually pay divided by your total income — your real average tax burden. Because of the progressive system and standard deduction, that same couple earning $200,000 might only pay an effective rate of 13-15% despite being in the 22% bracket.

    The distinction matters enormously for Roth conversions. If you convert $10,000 from your Traditional IRA to Roth, the tax cost is your marginal rate on that $10,000 — not your effective rate. Planning to stay in lower marginal brackets can save thousands.

    **Q: What is FICA and why does it disappear when I retire?**
    **A:** FICA stands for Federal Insurance Contributions Act — it's the payroll tax that funds Social Security and Medicare. If you look at your pay stub right now, you'll see 6.2% going to Social Security (on income up to $168,600) and 1.45% going to Medicare — a total of 7.65% of every paycheck. Your employer pays an equal matching amount on your behalf.

    The moment you retire and stop receiving W-2 wages, FICA taxes disappear completely. This is one of the reasons why your tax burden often drops significantly in early retirement — even if your investment income is similar to your working income, you no longer owe FICA on it. The app correctly applies FICA during your working years and removes it at retirement.

    **Q: What is the "Standard Deduction" and how does it help me?**
    **A:** The Standard Deduction is a flat amount the IRS lets you subtract from your income before calculating your taxes — no receipts required. For 2026, it's approximately $14,600 for single filers and $29,200 for married couples filing jointly. In practical terms, this means a married couple can have up to $29,200 in income and pay zero federal income tax. This is incredibly important in retirement planning because it creates a window of essentially tax-free income each year that can be used strategically for Roth conversions.

    **Q: What is the "QBI Deduction" for business owners?**
    **A:** If you own a business (sole proprietorship, S-Corp, partnership, or LLC), the IRS allows you to deduct up to 20% of your qualified business income before calculating your taxes — this is the Qualified Business Income (QBI) deduction, created by the 2017 Tax Cuts and Jobs Act. For example, if your business generates $100,000 in income, you might only pay taxes on $80,000 of it. The app automatically calculates and applies this deduction, phasing it out at higher income levels as the IRS requires.

    ### 🔄 ROTH CONVERSIONS
    **Q: What is a Roth conversion in plain English?**
    **A:** A Roth conversion is a deliberate decision to move money from your Traditional 401(k) or IRA (where you'll owe taxes when you withdraw) into a Roth IRA (where all future growth and withdrawals are tax-free). You pay the income taxes on the converted amount now, in the current year.

    Why would anyone voluntarily pay taxes early? Because of timing. If you retire at 65 but your RMDs don't start until 73, you have an 8-year window where your taxable income is relatively low. Converting during those years means you pay taxes at a lower rate than you would when RMDs force you to withdraw at potentially higher rates later. It's essentially buying future tax-free income at a discount.

    **Q: How does the "Roth Conversion Optimizer" in this app work?**
    **A:** When you enable the Roth Conversion Optimizer, it automatically identifies the years in your simulation where your taxable income is below your chosen target tax bracket ceiling. It then calculates exactly how much Traditional 401(k) money it can convert to Roth without pushing you over that bracket threshold. Crucially, it checks whether you actually have enough cash in your savings or brokerage accounts to pay the resulting tax bill — if you don't, it skips the conversion rather than creating artificial debt. Think of it as a tireless tax accountant working on your behalf every single year of your retirement.

    ### 📈 INVESTMENT ASSUMPTIONS
    **Q: What does "market growth rate" mean, and what's a realistic number to use?**
    **A:** The market growth rate is the annual return you expect your investments to earn on average. The US stock market (S&P 500) has historically returned approximately 10% per year before inflation, or about 7% after inflation, over very long periods. However, this average masks enormous year-to-year swings — markets have dropped 30-50% in bad years and gained 25-30% in great years. A commonly used planning assumption is 6-8% for a diversified portfolio. The app defaults to 7% as a reasonable middle ground. Being too optimistic here is one of the most dangerous mistakes in retirement planning — consider using 5-6% for a more conservative estimate.

    **Q: What is "inflation" and why does it matter so much over 30 years?**
    **A:** Inflation is the rate at which prices rise over time — meaning the purchasing power of your money shrinks. At 3% annual inflation, something that costs $100 today will cost about $243 in 30 years. This has a devastating effect on fixed income streams that don't grow. A $50,000/year retirement income that felt comfortable in 2026 might feel quite tight in 2046 if it hasn't kept up with inflation. The app inflates every expense each year at your chosen inflation rate, which gives you a realistic picture of what your money will actually buy in the future rather than creating false optimism with today's dollars.

    **Q: What does "View in Today's Dollars" do on the charts?**
    **A:** When this toggle is OFF, the charts show you raw future dollar amounts — which look large because inflation has inflated them. A $3 million net worth in 2055 sounds impressive, but if inflation averaged 3% over 30 years, it only has the purchasing power of about $1.2 million today.

    When you turn this toggle ON, the app mathematically "deflates" every future number back to what it would feel like in today's purchasing power. This makes it much easier to intuitively understand whether future amounts are actually comfortable or not. Many financial planners recommend planning primarily in today's dollars for exactly this reason.

    **Q: What is the "Investment Glidepath" and why would I want it?**
    **A:** A glidepath is the gradual shift from aggressive (mostly stocks) to conservative (mostly bonds) investing as you age. The logic is straightforward: a 35-year-old can ride out a market crash because they have 30 years for the market to recover. A 75-year-old who experiences a 40% market crash has far fewer years to recover and may be forced to sell at a loss to cover living expenses. The app's glidepath toggle simulates this shift by automatically reducing the assumed market return rate on your Traditional 401(k) and brokerage accounts by 1% for every 5 years you spend in retirement. It's a conservative safety feature that prevents your late-retirement projections from being unrealistically optimistic.

    **Q: What is "Sequence of Returns Risk"?**
    **A:** This is one of the most misunderstood retirement risks. The order in which you experience market returns matters enormously once you're withdrawing from your portfolio — not just the average return. If the market crashes 40% in your first year of retirement and you're forced to sell investments to cover living expenses, you're locking in permanent losses at the worst possible time. Even if the market recovers beautifully over the next decade, you have fewer shares left to benefit from that recovery. Someone who retires in a bull market year and experiences the same average returns over 30 years can end up with dramatically more money than someone who retires in a crash year, purely due to timing luck. The "-25% Market Crash at Retirement" stress test directly simulates this risk so you can see your plan's vulnerability.

    ### 🎲 MONTE CARLO SIMULATION
    **Q: What is Monte Carlo simulation in plain English?**
    **A:** Imagine running your retirement plan 200 times in parallel, but each time the stock market behaves differently — sometimes great, sometimes terrible, sometimes mediocre. Monte Carlo simulation does exactly that, mathematically. It takes your real financial plan and runs it through hundreds of randomly generated market scenarios based on historical volatility patterns.

    The result is a "probability of success" percentage — how often your plan survived without running out of money across all those scenarios. An 85% success rate means that in 85 out of 100 randomly generated futures, your money lasted. A 60% success rate means you're essentially flipping a coin, and you should probably adjust your plan.

    **Q: What is "volatility" and what number should I use?**
    **A:** Volatility measures how wildly your investment returns swing from year to year. The S&P 500 has historically had a volatility (standard deviation) of about 15-17% — meaning in any given year, returns are typically within about 15% of the average in either direction. A portfolio with more bonds has lower volatility (around 5-8%). Higher volatility means more dramatic swings, which creates more scenarios where bad luck at the wrong moment destroys your plan. For a stock-heavy retirement portfolio, 15% is a reasonable default. For a balanced portfolio (50/50 stocks and bonds), 10% is more appropriate.

    **Q: What does "probability of success" actually mean? Is 100% the goal?**
    **A:** Not necessarily. A 100% success rate sounds ideal, but achieving it often means either working much longer than needed, saving far more than necessary, or spending far less in retirement than you could comfortably afford. Most financial planners consider an 85-90% success rate to be a well-calibrated retirement plan — it means you've accounted for realistic risk without being so conservative that you sacrifice your quality of life. Below 70% is generally considered concerning. Below 50% means the plan needs significant revision. Think of it less as a binary pass/fail and more as a dial you can adjust by retiring a year later, spending slightly less, or saving a bit more.

    ### 💾 SAVING & SECURITY
    **Q: Is my financial data safe?**
    **A:** Your data is stored in Google Firebase — one of the most secure cloud storage platforms in the world, used by millions of applications globally. Your account is protected by email and password authentication. That said, no online system is completely immune to risk, so we recommend using a strong, unique password and not entering information you wouldn't be comfortable with your financial advisor seeing. This app is a planning tool — you don't need to enter actual account numbers, social security numbers, or banking credentials anywhere. Only use estimated balances and income figures.

    **Q: What happens to my data if I use "Guest Mode"?**
    **A:** In Guest Mode, everything you enter exists only in your current browser session. The moment you close the browser tab or refresh the page, all of your data is permanently gone. Guest Mode is great for a quick exploration of the app's features, but if you want to save your plan and return to it later, you need to create a free account and click the "Save" button at the bottom of the page.

    **Q: How often should I update my plan?**
    **A:** At minimum, once a year — ideally around tax time when your financial documents are fresh. You should also update it immediately after any major life event: a new job or significant raise, a marriage or divorce, having or adopting a child, buying or selling a home, receiving an inheritance, or making a major change to your retirement timeline. Your plan is only as useful as it is current. A financial model built on last year's numbers in a year of major life change is worse than useless — it gives you false confidence.

    **Q: What does the "Save Full Profile to Cloud Server" button do?**
    **A:** This button takes every single piece of information you've entered across all sections of the app — your income, assets, debts, expenses, family details, and simulation assumptions — and saves it permanently to your secure account in the cloud. The next time you log in from any device, everything will be exactly where you left it. Until you click this button, your changes exist only in your current browser session. It's strongly recommended to save after every meaningful session.
    """)

# --- FINAL SAVE CORE ---
st.markdown("---")
st.markdown('<div class="main-save-btn-marker"></div>', unsafe_allow_html=True)
if st.button("🚀 Save Full Profile to Cloud Server", type="primary", width="stretch", key="save_main"):
    if st.session_state['user_email'] == "guest_demo":
        st.error("Persistent configurations disabled within the demonstration environment.")
    else:
        def clean(df, k):
            if not isinstance(df, pd.DataFrame): return df
            if df.empty: return []
            valid_rows = []
            for r in df.to_dict('records'):
                clean_r = {}
                for vk, vv in r.items():
                    try:
                        if pd.isna(vv) or vv is pd.NA:
                            clean_r[vk] = None
                        else:
                            clean_r[vk] = vv
                    except (TypeError, ValueError):
                        clean_r[vk] = vv
                if any(str(v).strip() for v in clean_r.values() if v is not None):
                    valid_rows.append(clean_r)
            return valid_rows


        user_data = {
            "personal_info": {"name": my_name, "dob": my_dob.strftime("%Y-%m-%d"), "age": my_age, "retire_age": ret_age,
                              "spouse_retire_age": s_ret_age, "my_life_exp": my_life_exp_val,
                              "spouse_life_exp": spouse_life_exp_val, "current_city": curr_city_flow,
                              "has_spouse": has_spouse,
                              "spouse_name": spouse_name,
                              "spouse_dob": spouse_dob.strftime("%Y-%m-%d") if has_spouse else None,
                              "spouse_age": spouse_age, "kids": kids_data},
            "retire_city": ret_city_flow, "income": clean(edited_inc, "Description"),
            "real_estate": clean(edited_re, "Property Name"), "business": clean(edited_biz, "Business Name"),
            "liquid_assets": clean(edited_ast, "Account Name"), "liabilities": clean(edited_debt, "Debt Name"),
            "lifetime_expenses": clean(edited_exp, "Description"),
            "assumptions": {**st.session_state['assumptions'], "inflation": infl, "inflation_healthcare": infl_hc,
                            "inflation_education": infl_ed, "market_growth": mkt, "income_growth": inc_g,
                            "property_growth": prop_g, "rent_growth": rent_g, "current_tax_rate": cur_t,
                            "retire_tax_rate": ret_t, "roth_conversions": roth_conversions, "roth_target": roth_target,
                            "withdrawal_strategy": active_withdrawal_strategy.split(' ')[0]}
        }
        db.collection('users').document(st.session_state['user_email']).set(user_data)
        st.session_state['user_data'] = user_data
        st.success("✅ Complete Financial Blueprint Synchronized Successfully!")