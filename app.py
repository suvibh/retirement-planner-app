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
    div[data-testid="element-container"]:has(.save-btn-marker),
    div[data-testid="stElementContainer"]:has(.save-btn-marker),
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

    /* Save Button subtle styling */
    div[data-testid="element-container"]:has(.save-btn-marker) + div[data-testid="element-container"] button,
    div[data-testid="stElementContainer"]:has(.save-btn-marker) + div[data-testid="stElementContainer"] button {
        background-color: #f0fdf4 !important;
        border: 1px solid #bbf7d0 !important;
        color: #166534 !important;
        font-weight: 600 !important;
        border-radius: 8px !important;
        transition: all 0.2s ease !important;
    }
    div[data-testid="element-container"]:has(.save-btn-marker) + div[data-testid="element-container"] button:hover,
    div[data-testid="stElementContainer"]:has(.save-btn-marker) + div[data-testid="stElementContainer"] button:hover {
        background-color: #dcfce7 !important;
        border-color: #86efac !important;
        transform: translateY(-2px);
        box-shadow: 0 4px 12px rgba(22, 101, 52, 0.15) !important;
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

    st.markdown('<div class="save-btn-marker"></div>', unsafe_allow_html=True)
    if st.button("💾 Save Profile", key="sv_1"):
        save_requested = True
        st.toast("✅ Profile Saved!", icon="💾")

# --- 2. INCOME ---
with st.expander("💵 2. Your Income Streams", expanded=False):
    st.markdown(
        '<div class="info-text">💡 <strong>Employer Match Note:</strong> Employer 401(k) matches are considered part of your total compensation, but are <strong>not</strong> spendable cash income. Professionally, you should list the match here for visibility and then "mirror" it as an <strong>Annual Addition</strong> in your Assets table below to correctly grow your balance without inflating your monthly grocery budget.</div>',
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
        }, num_rows="dynamic", use_container_width=True, hide_index=True, key="inc_editor"
    )
    render_total("Total Pre-Tax Income", f"${edited_inc['Annual Amount ($)'].sum():,.0f}")

    col_ai_inc, col_sv_inc = st.columns([3, 1])
    with col_ai_inc:
        st.markdown('<div class="ai-btn-marker"></div>', unsafe_allow_html=True)
        if st.button("✨ Auto-Estimate My Social Security (AI)", use_container_width=True):
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
    with col_sv_inc:
        st.markdown('<div class="save-btn-marker"></div>', unsafe_allow_html=True)
        if st.button("💾 Save Income", key="sv_2", use_container_width=True):
            save_requested = True
            st.toast("✅ Income Saved!", icon="💾")

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
            }, num_rows="dynamic", use_container_width=True, hide_index=True, key="re_editor"
        )

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
                "Override Val. Growth (%)": st.column_config.NumberColumn("Value Growth (%)", step=0.1,
                                                                          format="%.1f%%"),
                "Override Dist. Growth (%)": st.column_config.NumberColumn("Income Growth (%)", step=0.1,
                                                                           format="%.1f%%")
            }, num_rows="dynamic", use_container_width=True, hide_index=True, key="biz_editor"
        )

    with tab_ast:
        st.markdown(
            '<div class="info-text">💡 <strong>Withdrawal Priority:</strong> You can select your exact drawdown strategy (e.g. Standard vs Roth Preferred) in the Interactive Dashboard below. The system always drains Cash and Brokerage assets before touching any retirement accounts.</div>',
            unsafe_allow_html=True)
        df_ast = pd.DataFrame(ud.get('liquid_assets', []))
        if df_ast.empty:
            df_ast = pd.DataFrame([{"Account Name": "Primary 401(k)", "Type": "Traditional 401k/IRA", "Owner": "Me",
                                    "Current Balance ($)": 0, "Annual Contribution ($/yr)": 0,
                                    "Est. Annual Growth (%)": 7.0, "Stop Contrib at Ret.?": True}])
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
                                                                  "Traditional 401k/IRA", "Roth 401k/IRA", "HSA",
                                                                  "Crypto", "529 Plan", "Other"]),
                "Owner": st.column_config.SelectboxColumn("Whose Account?", options=["Me", "Spouse", "Joint"]),
                "Current Balance ($)": st.column_config.NumberColumn("Current Balance ($)", step=5000, format="$%d"),
                "Annual Contribution ($/yr)": st.column_config.NumberColumn("Annual Additions ($/yr)", step=1000,
                                                                            format="$%d",
                                                                            help="Include both your contributions and any employer matches here."),
                "Est. Annual Growth (%)": st.column_config.NumberColumn("Expected Return (%)", format="%.1f%%"),
                "Stop Contrib at Ret.?": st.column_config.CheckboxColumn("Stop Adding at Ret.?",
                                                                         help="Check this if you will stop saving into this account once the owner retires.")
            }, num_rows="dynamic", use_container_width=True, hide_index=True, key="assets_editor"
        )

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
            }, num_rows="dynamic", use_container_width=True, hide_index=True, key="debt_editor"
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
    if st.button("💾 Save Assets & Debts", key="sv_3", use_container_width=True):
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

    # --- MIGRATION LOGIC (Old separate lists to unified list) ---
    if 'lifetime_expenses' not in st.session_state:
        migrated = []
        for c in ud.get('current_expenses', []):
            if c.get("Description"):
                migrated.append({"Description": c.get("Description"), "Category": c.get("Category", "Other"),
                                 "Frequency": c.get("Frequency", "Monthly"), "Amount ($)": c.get("Amount ($)", 0),
                                 "Start Phase": "Now", "Start Year": current_year, "End Phase": "End of Life",
                                 "End Year": current_year + 50, "AI Estimate?": c.get("AI Estimate?", False)})
        for r in ud.get('retire_expenses', []):
            if r.get("Description"):
                migrated.append({"Description": r.get("Description"), "Category": r.get("Category", "Other"),
                                 "Frequency": r.get("Frequency", "Monthly"), "Amount ($)": r.get("Amount ($)", 0),
                                 "Start Phase": "At Retirement", "Start Year": current_year + 20,
                                 "End Phase": "End of Life", "End Year": current_year + 50,
                                 "AI Estimate?": r.get("AI Estimate?", False)})
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
                         "Start Phase": "Now", "Start Year": current_year, "End Phase": "End of Life",
                         "End Year": current_year + 50, "AI Estimate?": False}]
        st.session_state['lifetime_expenses'] = migrated

    df_exp = pd.DataFrame(st.session_state['lifetime_expenses'])

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
        }, num_rows="dynamic", use_container_width=True, hide_index=True, key="exp_ed"
    )

    col_ai_cb, col_sv_cb = st.columns([3, 1])
    with col_ai_cb:
        st.markdown('<div class="ai-btn-marker"></div>', unsafe_allow_html=True)
        if st.button("✨ Auto-Estimate Budget & Milestones for selected current and future locations (AI)",
                     use_container_width=True):
            with st.spinner("Analyzing localized CPI data, timelines, and family needs..."):
                valid = edited_exp[edited_exp["Description"].astype(str) != ""].copy()
                locked = valid[valid["AI Estimate?"] == False].to_dict('records')
                locked_desc = [x['Description'] for x in locked]
                wealth_ctx = f"The household has a current annual pre-tax income of ${curr_inc_total:,.0f} and liquid assets totaling ${liq_ast_total:,.0f}. VERY IMPORTANT: While you should scale the budget to reflect this wealth, assume these users are savvy spenders and aggressive savers (comfortable but smart with money), so avoid over-inflating lifestyle costs unnecessarily."
                allowed_cats = ", ".join(budget_categories)
                prompt = f"Current City: {curr_city_flow}. Planned Retirement City: {ret_city_flow}. Family: {k_ctx}. Current Year is {current_year}. {wealth_ctx} Generate a comprehensive list of missing living expenses AND expected future life milestones (like college or weddings). {ai_exclusion} CRITICAL INSTRUCTIONS: 1) Medical expenses (IRMAA, Medicare Cliff, Pre-Medicare gap, LTC) are handled automatically by the simulation engine; only provide modest baseline out-of-pocket healthcare costs. 2) Model 'Empty Nesting': phase out child-heavy groceries, utility expenses, and ANY K-12 extracurriculars/lessons using 'Custom Year' End Phases exactly when the youngest child turns 18. 3) ALL College/University expenses MUST be categorized strictly as 'Education' (not 'Other') so they receive the 5% education inflation penalty. NOTE: Start and End Years are INCLUSIVE. For a standard 4-year college, the End Year must be exactly 3 years after the Start Year (e.g., Start 2032, End 2035 is 4 years). 4) Model Retirement Lifestyle Phases: split travel and entertainment into 'Go-Go Years' (high spend, starts at retirement, lasts 10 years, calculate costs based on {ret_city_flow}), 'Slow-Go Years' (medium spend, lasts next 10 years), and 'No-Go Years' (low spend) using 'Custom Year' Start/End phases. Skip these items as they are already accounted for: {json.dumps(locked_desc)}. Return ONLY a JSON array of objects with keys: 'Description', 'Category' (MUST be exactly one of: {allowed_cats}. If unsure, default to 'Other'), 'Frequency' (Monthly/Yearly/One-Time), 'Amount ($)' (number), 'Start Phase' (Now/At Retirement/Custom Year), 'Start Year' (integer), 'End Phase' (End of Life/At Retirement/Custom Year), 'End Year' (integer), and 'AI Estimate?' (true)."
                res = call_gemini_json(prompt)
                if res and isinstance(res, list) and len(res) > 0:
                    st.session_state['lifetime_expenses'] = locked + res
                    st.rerun()
                else:
                    st.error("⚠️ AI returned an invalid format. Please try again.")
    with col_sv_cb:
        st.markdown('<div class="save-btn-marker"></div>', unsafe_allow_html=True)
        if st.button("💾 Save Cash Flows", key="sv_4", use_container_width=True):
            save_requested = True
            st.session_state['lifetime_expenses'] = edited_exp.to_dict('records')
            st.toast("✅ Cash Flows Saved!", icon="💾")

# --- 5. INTERACTIVE DASHBOARD & SIMULATION ---
with st.expander("📈 5. Interactive Retirement Simulation & Analytics", expanded=True):
    st.markdown("### 🎛️ Simulation Command Center")
    tab_time, tab_macro, tab_adv = st.tabs(["⏳ Timelines", "📊 Macro & Taxes", "⚙️ Advanced Scenarios"])

    with tab_time:
        cc1, cc2, cc3, cc4 = st.columns(4)
        ret_age = cc1.slider("Retirement Age", int(my_age), 100, int(p_info.get('retire_age', 65)))
        s_ret_age = cc2.slider("Spouse Retire Age", int(spouse_age), 100,
                               int(p_info.get('spouse_retire_age', 65))) if has_spouse else None
        my_life_exp = cc3.slider("Your Life Expectancy", 70, 115, int(p_info.get('my_life_exp', 95)))
        spouse_life_exp = cc4.slider("Spouse Life Expectancy", 70, 115,
                                     int(p_info.get('spouse_life_exp', 95))) if has_spouse else None

    # --- ASSUMPTIONS BLOCK ---
    with tab_macro:
        st.markdown(
            '<div class="info-text">💡 <strong>AI Estimation:</strong> Click the ✨ AI button next to any field to have the AI estimate a realistic, localized value based on historical data and your profile!</div>',
            unsafe_allow_html=True)


        def ai_number_input(label, state_key, default_val, prompt, col):
            with col:
                sub_c1, sub_c2 = st.columns([5, 2])

                widget_key = f"in_{state_key}"

                # We need an empty placeholder for the number input so we can render it AFTER the button is evaluated
                input_placeholder = sub_c1.empty()

                # CSS trick to align the button with the input field label
                sub_c2.markdown("<div style='height: 28px;'></div>", unsafe_allow_html=True)
                sub_c2.markdown('<div class="ai-btn-marker"></div>', unsafe_allow_html=True)

                if sub_c2.button("✨ AI", key=f"btn_{state_key}", help=f"AI Estimate for {label}",
                                 use_container_width=True):
                    with st.spinner("AI estimating..."):
                        enhanced_prompt = prompt + " CRITICAL INSTRUCTION: You MUST return the value as a percentage number between 0 and 100 (e.g., return 5.5 for 5.5%, DO NOT return 0.055)."
                        res = call_gemini_json(enhanced_prompt)
                        if res and state_key in res:
                            new_val = float(res[state_key])
                            # Failsafe for rogue AI formatting (if it still returns 0.04 instead of 4.0)
                            if 0 < new_val < 0.30:
                                new_val *= 100.0
                            st.session_state['assumptions'][state_key] = new_val
                            # Safe state injection before widget initialization
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
                                  help="Reduces the Market Growth rate by 1% for every 5 years you are into retirement, simulating a shift to bonds.")
            stress_test = st.toggle("📉 Apply 20% Market Crash at Retirement", value=False,
                                    help="Simulates 'Sequence of Returns Risk' by dropping your portfolio by 20% in the first 3 years of retirement.")
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

    st.markdown('<div class="save-btn-marker"></div>', unsafe_allow_html=True)
    if st.button("💾 Save All Settings", key="sv_7"):
        save_requested = True
        st.session_state['assumptions']['roth_conversions'] = roth_conversions
        st.session_state['assumptions']['roth_target'] = roth_target
        st.session_state['assumptions']['withdrawal_strategy'] = active_withdrawal_strategy.split(' ')[0]
        st.toast("✅ Settings Saved!", icon="💾")

    st.divider()

    view_todays_dollars = st.toggle("💵 View Charts in Today's Dollars", value=False,
                                    help="Removes the effect of inflation so you can easily understand what these big future numbers feel like today.")

    # --- SIMULATION ENGINE ---
    if my_age > 0:

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
        def get_ss_multi(birth_year, r_age):
            fra = 67 if birth_year >= 1960 else (66 + (min(birth_year - 1954, 10) / 12.0) if birth_year >= 1955 else 66)
            if r_age < fra:
                months_early = (fra - r_age) * 12
                if months_early <= 36:
                    return 1.0 - (months_early * (5 / 9 * 0.01))
                else:
                    return 1.0 - (36 * (5 / 9 * 0.01)) - ((months_early - 36) * (5 / 12 * 0.01))
            elif r_age > fra:
                months_late = min((r_age - fra) * 12, 36)
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
                            "contrib": safe_num(a.get("Annual Contribution ($/yr)")),
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
        base_sim_re = [{"name": r.get("Property Name", "Property"), "is_primary": r.get("Is Primary Residence?", False),
                        "val": safe_num(r.get("Market Value ($)")), "debt": safe_num(r.get("Mortgage Balance ($)")),
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

            for year_offset in range(max_years + 1):
                year = current_year + year_offset
                my_current_age = year - my_birth_year
                spouse_current_age = year - spouse_birth_year if has_spouse else 0

                is_my_alive = year <= primary_end_year
                is_spouse_alive = has_spouse and (year <= spouse_end_year)

                if not is_my_alive and not is_spouse_alive:
                    break

                # Death Milestones
                if has_spouse and not is_spouse_alive and not spouse_died_notified:
                    if year not in milestones_by_year: milestones_by_year[year] = []
                    milestones_by_year[year].append(
                        {"desc": "💀 Spouse Passes Away (Step-up Basis Applied)", "amt": 0, "type": "critical"})
                    spouse_died_notified = True

                if not is_my_alive and not me_died_notified:
                    if year not in milestones_by_year: milestones_by_year[year] = []
                    milestones_by_year[year].append({"desc": "💀 You Pass Away", "amt": 0, "type": "critical"})
                    me_died_notified = True

                # System Milestones Logic
                if year == primary_retire_year and is_my_alive:
                    if year not in milestones_by_year: milestones_by_year[year] = []
                    milestones_by_year[year].append({"desc": "🎓 You Retire", "amt": 0, "type": "system"})

                if has_spouse and year == spouse_retire_year and is_spouse_alive:
                    if year not in milestones_by_year: milestones_by_year[year] = []
                    milestones_by_year[year].append({"desc": "🎓 Spouse Retires", "amt": 0, "type": "system"})

                if is_my_alive and my_current_age == 65:
                    if year not in milestones_by_year: milestones_by_year[year] = []
                    milestones_by_year[year].append({"desc": "🏥 Medicare Kicks In (You)", "amt": 0, "type": "system"})

                if has_spouse and is_spouse_alive and spouse_current_age == 65:
                    if year not in milestones_by_year: milestones_by_year[year] = []
                    milestones_by_year[year].append(
                        {"desc": "🏥 Medicare Kicks In (Spouse)", "amt": 0, "type": "system"})

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

                annual_inc, annual_ss, pre_tax_ord, pre_tax_cg = 0, 0, 0, 0
                earned_income_me, earned_income_spouse, match_income = 0, 0, 0

                # Compound high-interest debt if we enter crisis mode
                unfunded_debt_bal *= 1.18

                base_mkt_yr = mkt_sequence[year_offset]
                active_mkt = base_mkt_yr
                if glidepath and is_retired:
                    years_retired = year - primary_retire_year
                    active_mkt = max(3.0, base_mkt_yr - (math.floor(years_retired / 5) * 1.0))
                if stress_test and (primary_retire_year <= year < primary_retire_year + 3): active_mkt = -20.0

                active_mfj = True if has_spouse and is_my_alive and is_spouse_alive else False

                # Capital Gains Step-Up Basis (Death of a spouse wipes out capital gains proxy tax)
                brokerage_tax_rate = 0.05
                if has_spouse and (not is_my_alive or not is_spouse_alive):
                    brokerage_tax_rate = 0.0

                # Income
                primary_ss_amt = 0
                spouse_ss_amt = 0

                for inc in edited_inc.to_dict('records'):
                    owner = inc.get("Owner", "Me")
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
                        amt = base_amt * ((1 + g / 100) ** year_offset)

                        if cat_name == "Social Security":
                            if owner == "Me":
                                primary_ss_amt = amt * my_ss_multi
                            elif owner == "Spouse":
                                spouse_ss_amt = amt * spouse_ss_multi
                            continue

                        # Hide 401(k) match from income completely, but track it to avoid penalizing cash flows later
                        if cat_name == "Employer Match (401k/HSA)":
                            match_income += amt
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

                # Spousal SS Survivor Benefits
                active_ss = 0
                if is_my_alive and is_spouse_alive:
                    active_ss = primary_ss_amt + spouse_ss_amt
                elif is_my_alive and not is_spouse_alive:
                    active_ss = max(primary_ss_amt, spouse_ss_amt)
                elif is_spouse_alive and not is_my_alive:
                    active_ss = max(primary_ss_amt, spouse_ss_amt)

                if active_ss > 0:
                    annual_inc += active_ss
                    annual_ss += active_ss
                    yd["Income: Social Security"] = active_ss

                    if primary_ss_amt > 0 and not ss_started_me and is_my_alive:
                        if year not in milestones_by_year: milestones_by_year[year] = []
                        milestones_by_year[year].append(
                            {"desc": "📈 Social Security Begins (You)", "amt": primary_ss_amt, "type": "system"})
                        ss_started_me = True
                    if spouse_ss_amt > 0 and not ss_started_spouse and is_spouse_alive:
                        if year not in milestones_by_year: milestones_by_year[year] = []
                        milestones_by_year[year].append(
                            {"desc": "📈 Social Security Begins (Spouse)", "amt": spouse_ss_amt, "type": "system"})
                        ss_started_spouse = True

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
                cur_biz_val, biz_dist_total, re_equity = 0, 0, 0
                total_exp = 0  # Initialize general expenses

                for b in sim_biz:
                    if year_offset > 0:
                        b['val'] *= (1 + b['v_growth'] / 100)
                        b['dist'] *= (1 + b['d_growth'] / 100)
                    cur_biz_val += (b['val'] * b['own'])
                    annual_inc += b['dist']
                    # QBI Deduction Proxy: ~20% of business pass-through income is generally tax-deductible in the US
                    pre_tax_ord += (b['dist'] * 0.80)
                    yd["Income: Biz Dist"] = yd.get("Income: Biz Dist", 0) + b['dist']

                for r in sim_re:
                    if year_offset > 0:
                        r['rent'] *= (1 + r['r_growth'] / 100)
                        r['exp'] *= (1 + infl / 100)
                        r['val'] *= (1 + r['v_growth'] / 100)

                    interest_paid = 0
                    if r['debt'] > 0:
                        interest_paid = r['debt'] * r['rate']
                        principal = max(0, r['pmt'] - interest_paid)
                        r['debt'] = max(0, r['debt'] - principal)

                    # Trigger Mortgage Payoff Milestone
                    if r['debt'] <= 0 and prev_re_debts.get(r['name'], 0) > 0:
                        if year not in milestones_by_year: milestones_by_year[year] = []
                        milestones_by_year[year].append(
                            {"desc": f"🏡 Mortgage Paid Off: {r['name']}", "amt": 0, "type": "system"})
                    prev_re_debts[r['name']] = r['debt']

                    re_equity += (r['val'] - r['debt'])

                    # Cash flow routing: Primary vs Investment
                    if r['is_primary']:
                        # Primary home: expenses flow directly to general budget.
                        primary_costs = r['exp'] + r['pmt']
                        total_exp += primary_costs
                        yd["Expense: Primary Home (Mortgage & Upkeep)"] = yd.get(
                            "Expense: Primary Home (Mortgage & Upkeep)", 0) + primary_costs

                        # If you are house-hacking your primary residence, add rent to income
                        if r['rent'] > 0:
                            annual_inc += r['rent']
                            yd["Income: Primary Home Rent"] = yd.get("Income: Primary Home Rent", 0) + r['rent']
                    else:
                        # Investment Property: Treated in a bubble
                        net_re_cashflow = r['rent'] - (r['exp'] + r['pmt'])
                        if net_re_cashflow > 0:
                            # Positive cash flow feeds into your wallet
                            annual_inc += net_re_cashflow
                            yd["Income: Net Investment RE Cashflow"] = yd.get("Income: Net Investment RE Cashflow",
                                                                              0) + net_re_cashflow
                        elif net_re_cashflow < 0:
                            # Negative cash flow (loss) drains your wallet
                            total_exp += abs(net_re_cashflow)
                            yd["Expense: Net Investment RE Loss"] = yd.get("Expense: Net Investment RE Loss", 0) + abs(
                                net_re_cashflow)

                    # Schedule E Proxy: Real Estate is taxed on NET income, not gross.
                    taxable_rent = max(0, r['rent'] - r['exp'] - interest_paid)
                    pre_tax_ord += taxable_rent

                # --- UNIFIED LIFETIME CASH FLOWS ENGINE ---
                # Check for Medicare Gap trigger specifically for Health insurance
                medicare_gap_applied_this_year = False

                for ev in edited_exp.to_dict('records'):
                    desc = str(ev.get("Description", "")).strip()
                    if not desc: continue

                    # Exclude housing/debt if owned (to prevent double counting)
                    cat = ev.get("Category", "Other")
                    if owns_home and cat in ["Housing / Rent", "Debt Payments"]: continue
                    if not owns_home and cat == "Debt Payments": continue

                    freq = ev.get("Frequency", "Monthly")
                    amt = safe_num(ev.get("Amount ($)", 0))
                    if freq == "Monthly": amt *= 12

                    start_phase = ev.get("Start Phase", "Now")
                    end_phase = ev.get("End Phase", "End of Life")

                    actual_start = current_year
                    if start_phase == "At Retirement":
                        actual_start = primary_retire_year
                    elif start_phase == "Custom Year":
                        actual_start = safe_num(ev.get("Start Year"), current_year)

                    actual_end = max_year
                    if end_phase == "At Retirement":
                        actual_end = primary_retire_year - 1
                    elif end_phase == "Custom Year":
                        actual_end = safe_num(ev.get("End Year"), max_year)

                    is_active = False
                    if freq == "One-Time":
                        is_active = (year == actual_start)
                    else:
                        is_active = (actual_start <= year <= actual_end)

                    if is_active:
                        cat_infl = infl_hc if cat in ["Healthcare", "Insurance"] else (
                            infl_ed if cat == "Education" else infl)
                        inflated_amt = amt * ((1 + cat_infl / 100) ** year_offset)

                        # Drop recurring living expenses if widow(er)
                        if has_spouse and not (is_my_alive and is_spouse_alive) and freq != "One-Time" and cat not in [
                            "Education", "Debt Payments"]:
                            inflated_amt *= 0.6

                        # Medicare Cliff logic on standard Healthcare items
                        if medicare_cliff and cat == "Healthcare" and my_current_age >= 65:
                            inflated_amt *= 0.50

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

                            # Pass 1: Fuzzy Name Matching
                            for a in sim_assets:
                                if a.get('Type') == '529 Plan' and a['bal'] > 0:
                                    acct_name_clean = re.sub(r'[^a-zA-Z0-9\s]', '',
                                                             str(a.get('Account Name', ''))).lower()
                                    desc_clean = re.sub(r'[^a-zA-Z0-9\s]', '', desc).lower()
                                    acct_words = [re.sub(r's$', '', w) for w in acct_name_clean.split() if
                                                  len(w) > 2 and w not in ['plan', 'account', '529', 'savings']]

                                    match = False
                                    for w in acct_words:
                                        if w in desc_clean: match = True; break

                                    if match:
                                        if a['bal'] >= amount_to_cover:
                                            a['bal'] -= amount_to_cover
                                            covered_by_529 += amount_to_cover
                                            amount_to_cover = 0;
                                            break
                                        else:
                                            amount_to_cover -= a['bal']
                                            covered_by_529 += a['bal']
                                            a['bal'] = 0

                            # Pass 2: Fallback
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
                if medicare_gap and is_retired and my_current_age < 65:
                    gap_cost = 15000 * ((1 + infl_hc / 100) ** year_offset)
                    total_exp += gap_cost
                    yd["Expense: Healthcare (Pre-Medicare Gap Proxy)"] = gap_cost

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
                        yd["Expense: Debt Payments"] = yd.get("Expense: Debt Payments", 0) + d['pmt']

                    # Trigger Debt Payoff Milestone
                    if d['bal'] <= 0 and prev_debt_bals.get(d['name'], 0) > 0:
                        if year not in milestones_by_year: milestones_by_year[year] = []
                        milestones_by_year[year].append(
                            {"desc": f"🎉 Debt Paid Off: {d['name']}", "amt": 0, "type": "system"})
                    prev_debt_bals[d['name']] = d['bal']

                    debt_bal_total += d['bal']

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

                # Tax Calculations Base
                base_fed_tax, marginal_rate = calc_federal_tax(pre_tax_ord, 0, active_mfj, year_offset, infl)
                state_tax_rate = cur_t if not is_retired else ret_t

                # Roth Conversion Optimizer Guardrails
                if roth_conversions and is_retired:
                    infl_factor = (1 + infl / 100) ** year_offset
                    std_deduction = (29200 if active_mfj else 14600) * infl_factor

                    b_limits_mfj = {"12%": 94300, "22%": 201050, "24%": 383900, "32%": 487450}
                    b_limits_single = {"12%": 47150, "22%": 100525, "24%": 191950, "32%": 243725}

                    b_limits = b_limits_mfj if active_mfj else b_limits_single
                    target_limit = b_limits.get(roth_target, 383900) * infl_factor
                    target_max_income = target_limit + std_deduction

                    conversion_room = max(0, target_max_income - pre_tax_ord)

                    # GUARDRAIL: Only convert what can be comfortably paid by existing liquid cash
                    available_cash = sum(a['bal'] for a in sim_assets if
                                         a.get('Type') in ['Checking/Savings', 'HYSA', 'Brokerage (Taxable)',
                                                           'Unallocated Cash'])
                    est_tax_rate = marginal_rate + (state_tax_rate / 100.0)
                    max_tax_budget = available_cash * 0.50
                    max_conversion_by_cash = max_tax_budget / max(0.10, est_tax_rate)

                    conversion_room = min(conversion_room, max_conversion_by_cash)
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
                            # Recalculate taxes after conversion
                            base_fed_tax, marginal_rate = calc_federal_tax(pre_tax_ord, 0, active_mfj, year_offset,
                                                                           infl)

                # Medicare IRMAA Surcharges Proxy
                num_on_medicare = 0
                if is_my_alive and my_current_age >= 65: num_on_medicare += 1
                if is_spouse_alive and spouse_current_age >= 65: num_on_medicare += 1

                if num_on_medicare > 0:
                    infl_factor = (1 + infl / 100) ** year_offset
                    t1 = 206000 * infl_factor if active_mfj else 103000 * infl_factor
                    t2 = 258000 * infl_factor if active_mfj else 129000 * infl_factor
                    t3 = 322000 * infl_factor if active_mfj else 161000 * infl_factor
                    t4 = 386000 * infl_factor if active_mfj else 193000 * infl_factor
                    t5 = 750000 * infl_factor if active_mfj else 500000 * infl_factor

                    surcharge = 0
                    if pre_tax_ord > t5:
                        surcharge = 6500 * infl_factor
                    elif pre_tax_ord > t4:
                        surcharge = 5500 * infl_factor
                    elif pre_tax_ord > t3:
                        surcharge = 4000 * infl_factor
                    elif pre_tax_ord > t2:
                        surcharge = 2500 * infl_factor
                    elif pre_tax_ord > t1:
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

                # Finalize Tax Setup
                state_tax = pre_tax_ord * (state_tax_rate / 100.0)

                # FICA Tax applies per person against the SS wage base
                fica_tax = 0
                ss_wage_base = 168600 * ((1 + infl / 100) ** year_offset)
                for ei in [earned_income_me, earned_income_spouse]:
                    if ei > 0:
                        ss_tax = min(ei, ss_wage_base) * 0.062
                        med_tax = ei * 0.0145
                        addl_med_tax = max(0, ei - 250000) * 0.009
                        fica_tax += ss_tax + med_tax + addl_med_tax

                total_tax = base_fed_tax + state_tax + fica_tax
                yd["Expense: Taxes"] = total_tax

                # Robust Shortfall / Withdrawal Math
                employee_contributions = max(0, asset_contributions - match_income)
                if employee_contributions > 0:
                    yd["Expense: Portfolio Contributions"] = employee_contributions

                cash_outflows = total_exp + employee_contributions + total_tax
                net_cash_flow = annual_inc - cash_outflows
                yd["Net Savings"] = net_cash_flow

                if net_cash_flow > 0:
                    yd["Expense: Unallocated Surplus Saved"] = net_cash_flow
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

                    # --- Sequence 1b: Taxable Brokerage (5% Tax on Gains Proxy / Step-up Basis if deceased) ---
                    if shortfall > 0:
                        for a in sim_assets:
                            if shortfall <= 0: break
                            if a.get('Type') == 'Brokerage (Taxable)' and a['bal'] > 0:
                                if not tapped_brokerage:
                                    if year not in milestones_by_year: milestones_by_year[year] = []
                                    milestones_by_year[year].append(
                                        {"desc": "📉 Began Drawing from Taxable Brokerage", "amt": 0, "type": "system"})
                                    tapped_brokerage = True

                                eff_tax = brokerage_tax_rate
                                req_gross = shortfall / (1.0 - eff_tax)

                                if a['bal'] >= req_gross:
                                    a['bal'] -= req_gross
                                    tax_inc = req_gross - shortfall
                                    yd["Expense: Taxes"] += tax_inc
                                    yd[f"Income: Withdrawal ({a.get('Account Name', 'Brokerage')})"] = req_gross
                                    shortfall = 0
                                else:
                                    withdrawn = a['bal']
                                    a['bal'] = 0
                                    tax_inc = withdrawn * eff_tax
                                    net_cash = withdrawn - tax_inc
                                    yd["Expense: Taxes"] += tax_inc
                                    yd[f"Income: Withdrawal ({a.get('Account Name', 'Brokerage')})"] = withdrawn
                                    shortfall -= net_cash

                    # Select logic based on Withdrawal Strategy
                    if 'Standard' in active_withdrawal_strategy:
                        # --- Sequence 2: Tax-Deferred (Traditional 401k) ---
                        if shortfall > 0:
                            for a in sim_assets:
                                if shortfall <= 0: break
                                if a.get('Type') == 'Traditional 401k/IRA' and a['bal'] > 0:
                                    if not tapped_trad:
                                        if year not in milestones_by_year: milestones_by_year[year] = []
                                        milestones_by_year[year].append(
                                            {"desc": "📉 Began Drawing from Traditional 401(k)/IRA", "amt": 0,
                                             "type": "system"})
                                        tapped_trad = True

                                    owner = a.get('Owner', 'Me')
                                    owner_age = my_current_age if owner in ['Me', 'Joint'] else spouse_current_age
                                    owner_retire_age = ret_age if owner in ['Me', 'Joint'] else (
                                        s_ret_age if has_spouse else 9999)

                                    # Rule of 55: Penalty waived if retiring at 55 or later AND reaching that age
                                    rule_of_55 = (owner_retire_age >= 55 and owner_age >= owner_retire_age)
                                    penalty = 0.10 if (owner_age < 59.5 and not rule_of_55) else 0.0

                                    eff_tax = min(marginal_rate + (state_tax_rate / 100.0) + penalty, 0.99)
                                    req_gross = shortfall / (1.0 - eff_tax)

                                    if a['bal'] >= req_gross:
                                        a['bal'] -= req_gross
                                        tax_inc = req_gross - shortfall
                                        yd["Expense: Taxes"] += tax_inc
                                        yd[f"Income: Withdrawal ({a.get('Account Name', '401k')})"] = req_gross
                                        shortfall = 0
                                    else:
                                        withdrawn = a['bal']
                                        a['bal'] = 0
                                        tax_inc = withdrawn * eff_tax
                                        net_cash = withdrawn - tax_inc
                                        yd["Expense: Taxes"] += tax_inc
                                        yd[f"Income: Withdrawal ({a.get('Account Name', '401k')})"] = withdrawn
                                        shortfall -= net_cash

                        # --- Sequence 3: Tax-Free (Roth/HSA) ---
                        if shortfall > 0:
                            for a in sim_assets:
                                if shortfall <= 0: break
                                if a.get('Type') in ['Roth 401k/IRA', 'HSA', 'Crypto', '529 Plan', 'Other'] and a[
                                    'bal'] > 0:
                                    if not tapped_roth:
                                        if year not in milestones_by_year: milestones_by_year[year] = []
                                        milestones_by_year[year].append(
                                            {"desc": "📉 Began Drawing from Roth/Tax-Free Assets", "amt": 0,
                                             "type": "system"})
                                        tapped_roth = True

                                    owner = a.get('Owner', 'Me')
                                    owner_age = my_current_age if owner in ['Me', 'Joint'] else spouse_current_age
                                    owner_retire_age = ret_age if owner in ['Me', 'Joint'] else (
                                        s_ret_age if has_spouse else 9999)

                                    rule_of_55 = (owner_retire_age >= 55 and owner_age >= owner_retire_age)
                                    penalty = 0.10 if (a.get(
                                        'Type') == 'Roth 401k/IRA' and owner_age < 59.5 and not rule_of_55) else 0.0

                                    eff_tax = min(penalty, 0.99)
                                    req_gross = shortfall / (1.0 - eff_tax)

                                    if a['bal'] >= req_gross:
                                        a['bal'] -= req_gross
                                        tax_inc = req_gross - shortfall
                                        yd["Expense: Taxes"] += tax_inc
                                        yd[f"Income: Withdrawal ({a.get('Account Name', 'Roth')})"] = req_gross
                                        shortfall = 0
                                    else:
                                        withdrawn = a['bal']
                                        a['bal'] = 0
                                        tax_inc = withdrawn * eff_tax
                                        net_cash = withdrawn - tax_inc
                                        yd["Expense: Taxes"] += tax_inc
                                        yd[f"Income: Withdrawal ({a.get('Account Name', 'Roth')})"] = withdrawn
                                        shortfall -= net_cash

                    else:
                        # --- ROTH PREFERRED STRATEGY ---
                        # --- Sequence 2: Tax-Free (Roth/HSA) ---
                        if shortfall > 0:
                            for a in sim_assets:
                                if shortfall <= 0: break
                                if a.get('Type') in ['Roth 401k/IRA', 'HSA', 'Crypto', '529 Plan', 'Other'] and a[
                                    'bal'] > 0:
                                    if not tapped_roth:
                                        if year not in milestones_by_year: milestones_by_year[year] = []
                                        milestones_by_year[year].append(
                                            {"desc": "📉 Began Drawing from Roth/Tax-Free Assets", "amt": 0,
                                             "type": "system"})
                                        tapped_roth = True

                                    owner = a.get('Owner', 'Me')
                                    owner_age = my_current_age if owner in ['Me', 'Joint'] else spouse_current_age
                                    owner_retire_age = ret_age if owner in ['Me', 'Joint'] else (
                                        s_ret_age if has_spouse else 9999)

                                    rule_of_55 = (owner_retire_age >= 55 and owner_age >= owner_retire_age)
                                    penalty = 0.10 if (a.get(
                                        'Type') == 'Roth 401k/IRA' and owner_age < 59.5 and not rule_of_55) else 0.0

                                    eff_tax = min(penalty, 0.99)
                                    req_gross = shortfall / (1.0 - eff_tax)

                                    if a['bal'] >= req_gross:
                                        a['bal'] -= req_gross
                                        tax_inc = req_gross - shortfall
                                        yd["Expense: Taxes"] += tax_inc
                                        yd[f"Income: Withdrawal ({a.get('Account Name', 'Roth')})"] = req_gross
                                        shortfall = 0
                                    else:
                                        withdrawn = a['bal']
                                        a['bal'] = 0
                                        tax_inc = withdrawn * eff_tax
                                        net_cash = withdrawn - tax_inc
                                        yd["Expense: Taxes"] += tax_inc
                                        yd[f"Income: Withdrawal ({a.get('Account Name', 'Roth')})"] = withdrawn
                                        shortfall -= net_cash

                        # --- Sequence 3: Tax-Deferred (Traditional 401k) ---
                        if shortfall > 0:
                            for a in sim_assets:
                                if shortfall <= 0: break
                                if a.get('Type') == 'Traditional 401k/IRA' and a['bal'] > 0:
                                    if not tapped_trad:
                                        if year not in milestones_by_year: milestones_by_year[year] = []
                                        milestones_by_year[year].append(
                                            {"desc": "📉 Began Drawing from Traditional 401(k)/IRA", "amt": 0,
                                             "type": "system"})
                                        tapped_trad = True

                                    owner = a.get('Owner', 'Me')
                                    owner_age = my_current_age if owner in ['Me', 'Joint'] else spouse_current_age
                                    owner_retire_age = ret_age if owner in ['Me', 'Joint'] else (
                                        s_ret_age if has_spouse else 9999)

                                    rule_of_55 = (owner_retire_age >= 55 and owner_age >= owner_retire_age)
                                    penalty = 0.10 if (owner_age < 59.5 and not rule_of_55) else 0.0

                                    eff_tax = min(marginal_rate + (state_tax_rate / 100.0) + penalty, 0.99)
                                    req_gross = shortfall / (1.0 - eff_tax)

                                    if a['bal'] >= req_gross:
                                        a['bal'] -= req_gross
                                        tax_inc = req_gross - shortfall
                                        yd["Expense: Taxes"] += tax_inc
                                        yd[f"Income: Withdrawal ({a.get('Account Name', '401k')})"] = req_gross
                                        shortfall = 0
                                    else:
                                        withdrawn = a['bal']
                                        a['bal'] = 0
                                        tax_inc = withdrawn * eff_tax
                                        net_cash = withdrawn - tax_inc
                                        yd["Expense: Taxes"] += tax_inc
                                        yd[f"Income: Withdrawal ({a.get('Account Name', '401k')})"] = withdrawn
                                        shortfall -= net_cash

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
                prev_unfunded_debt_bal = unfunded_debt_bal

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

                sim_res.append(
                    {"Year": year, "Age": my_current_age, "Annual Income": annual_inc, "Annual Expenses": total_exp,
                     "Annual Taxes": yd["Expense: Taxes"], "Annual Net Savings": yd["Net Savings"],
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
            # Create Dataframes before any charting logic
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

            # APPLY DISCOUNTING IF TOGGLED (Inline directly on data structs)
            if view_todays_dollars:
                for i in range(len(sim_results)):
                    discount = (1 + infl / 100) ** i
                    for col in ["Annual Income", "Annual Expenses", "Annual Taxes", "Annual Net Savings",
                                "Liquid Assets", "Real Estate Equity", "Business Equity", "Debt", "Unfunded Debt",
                                "Net Worth"]:
                        sim_results[i][col] /= discount
                    for k in detailed_results[i].keys():
                        if k not in ["Age", "Year"]: detailed_results[i][k] /= discount
                    for k in nw_detailed_results[i].keys():
                        if k not in ["Age", "Year"] and not isinstance(nw_detailed_results[i][k], str):
                            nw_detailed_results[i][k] /= discount

            df_sim = pd.DataFrame(sim_results)
            df_det = pd.DataFrame(detailed_results).fillna(0)
            df_nw = pd.DataFrame(nw_detailed_results).fillna(0)

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
                st.plotly_chart(fig_nw, use_container_width=True)

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
                st.plotly_chart(fig_cf, use_container_width=True)

                # --- SANKEY DIAGRAM ---
                st.divider()
                st.write("#### 🌊 Cash Flow Sankey Snapshot")
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
                               k.startswith('Income:') and v > 0}
                    outflows = {k.replace('Expense: ', ''): v for k, v in row.items() if
                                k.startswith('Expense:') and v > 0}

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
                        node_colors.append(
                            '#10b981' if k in ['Portfolio Contributions', 'Unallocated Surplus Saved'] else '#f43f5e')
                        link_colors.append('rgba(16, 185, 129, 0.4)' if k in ['Portfolio Contributions',
                                                                              'Unallocated Surplus Saved'] else 'rgba(244, 63, 94, 0.4)')

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
                    st.plotly_chart(fig_sankey, use_container_width=True)

            # --- DATA AUDIT TABLES ---
            st.divider()
            csv = df_sim.to_csv(index=False).encode('utf-8')
            st.download_button(label="📥 Download Full Simulation (.csv)", data=csv,
                               file_name='retirement_simulation.csv', mime='text/csv', type="secondary")

            t1, t2 = st.tabs(["Income & Expense Log", "Net Worth Log"])
            with t1:
                st.subheader("Detailed Tax & Expense Log")
                inc_c = sorted([c for c in df_det.columns if c.startswith("Income:") or c.startswith("Roth")])
                exp_c = sorted([c for c in df_det.columns if c.startswith("Expense:")])
                ord_det = ["Year", "Age"] + inc_c + exp_c + ["Net Savings"]
                st.dataframe(df_det[ord_det].set_index("Year").style.format(
                    {c: "${:,.0f}" for c in ord_det if c not in ["Age", "Year"]} | {"Age": "{:.0f}"}),
                             use_container_width=True)

            with t2:
                st.subheader("Detailed Net Worth Log")
                st.markdown(
                    "Track the exact, year-by-year balance of every single asset account and liability to trace your drawdowns and growth.")
                ast_c = sorted([c for c in df_nw.columns if c.startswith("Asset:")])
                ord_nw = ["Year", "Age"] + ast_c + ["Total Liquid Assets", "Total Real Estate Equity",
                                                    "Total Business Equity", "Total Debt Liabilities",
                                                    "Total Net Worth"]
                st.dataframe(df_nw[ord_nw].set_index("Year").style.format(
                    {c: "${:,.0f}" for c in ord_nw if c not in ["Age", "Year"]} | {"Age": "{:.0f}"}),
                             use_container_width=True)

# --- AI FIDUCIARY REPORT (BOTTOM ANCHORED) ---
st.markdown("---")
st.markdown("### 🤖 AI Fiduciary Health Report")
st.markdown(
    '<div class="info-text">💡 This engine extracts a 5-year interval timeseries snapshot of your entire financial life (Age, Net Worth, Liquid Cash, Income, Expenses, and Taxes). The AI acts as a fiduciary and analyzes your cash flows chronologically to provide tactical, phase-by-phase advice on Roth conversions, sequence of returns, and tax optimization.</div>',
    unsafe_allow_html=True)
c_ai_rep, _ = st.columns([1, 2])
with c_ai_rep:
    st.markdown('<div class="ai-btn-marker"></div>', unsafe_allow_html=True)
    if st.button("✨ Generate Comprehensive AI Report", use_container_width=True):
        with st.spinner("AI extracting timeseries data and acting as fiduciary advisor..."):
            if 'sim_results' in locals() and len(sim_results) > 0:
                sim_summary = {
                    "Current Age": my_age, "Retirement Age": ret_age, "Life Expectancy": my_life_exp_val,
                    "Current Net Worth": df_sim_nominal.iloc[0]['Net Worth'],
                    "Final Net Worth": df_sim_nominal.iloc[-1]['Net Worth'],
                    "Shortfall Year": str(deplete_year) if deplete_year is not None else "None"
                }

                # Compress 50 years of data into 5-year leaps so the AI can digest the timeline without context limits
                timeline_summary = []
                for idx, row in df_sim_nominal.iloc[::5].iterrows():
                    timeline_summary.append({
                        "Age": int(row["Age"]),
                        "Income": int(row["Annual Income"]),
                        "Expenses": int(row["Annual Expenses"]),
                        "Taxes": int(row["Annual Taxes"]),
                        "Liquid_Assets": int(row["Liquid Assets"]),
                        "Net_Worth": int(row["Net Worth"])
                    })
                # Always append the final year
                last_row = df_sim_nominal.iloc[-1]
                timeline_summary.append({"Age": int(last_row["Age"]), "Income": int(last_row["Annual Income"]),
                                         "Expenses": int(last_row["Annual Expenses"]),
                                         "Taxes": int(last_row["Annual Taxes"]),
                                         "Liquid_Assets": int(last_row["Liquid Assets"]),
                                         "Net_Worth": int(last_row["Net Worth"])})

                prompt = f"Act as an expert fiduciary financial planner. Review this user's summary: {json.dumps(sim_summary)} and their chronological 5-year cash flow progression: {json.dumps(timeline_summary)}. Provide a highly detailed, year-by-year or phase-by-phase tactical analysis. Focus on specific strategies they can use to optimize their tax buckets (e.g., when exactly to execute Roth conversions before RMDs begin), sequence of withdrawals, and managing the gaps between retirement and Social Security/Medicare. Return ONLY valid JSON exactly like this: {{\"analysis\": \"your detailed markdown text here, using \\n for line breaks\"}}"
                res = call_gemini_json(prompt)
                if res and 'analysis' in res:
                    st.session_state['ai_analysis_report'] = res['analysis']
                else:
                    st.error("⚠️ AI Analysis failed to generate.")
            else:
                st.warning("Please run the simulation first.")

if 'ai_analysis_report' in st.session_state:
    report_content = st.session_state['ai_analysis_report'].replace('\\n', '\n').replace('$', r'\$')
    st.info(f"{report_content}")

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
            "lifetime_expenses": clean(edited_exp, "Description"),
            "assumptions": {**st.session_state['assumptions'], "inflation": infl, "inflation_healthcare": infl_hc,
                            "inflation_education": infl_ed, "market_growth": mkt, "income_growth": inc_g,
                            "property_growth": prop_g, "rent_growth": rent_g, "current_tax_rate": cur_t,
                            "retire_tax_rate": ret_t, "roth_conversions": roth_conversions, "roth_target": roth_target,
                            "withdrawal_strategy": active_withdrawal_strategy.split(' ')[0]}
        }
        db.collection('users').document(st.session_state['user_email']).set(user_data, merge=True)
        st.session_state['user_data'] = user_data
        st.success("✅ Complete Financial Blueprint Synchronized Successfully!")