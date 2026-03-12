import streamlit as st
import pandas as pd
import requests
import json
import datetime
import time
import random
import math
import html
import re
import copy
import io
import os
import numpy as np
import concurrent.futures
from dateutil.relativedelta import relativedelta
import warnings
import firebase_admin
from firebase_admin import credentials, firestore
from concurrent.futures import ThreadPoolExecutor

try:
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots   # <--- ADD THIS LINE
    HAS_PLOTLY = True
except ImportError:
    HAS_PLOTLY = False

try:
    import extra_streamlit_components as stx
except ImportError:
    st.error("Missing dependency: pip install extra-streamlit-components")
    st.stop()

try:
    import openpyxl
except ImportError:
    st.warning("Missing dependency: pip install openpyxl (Required for Excel downloads)")

# --- CONFIG & SUPPRESSION ---
warnings.simplefilter(action='ignore', category=FutureWarning)
st.set_page_config(page_title="AI Retirement Planner Pro", layout="wide", page_icon="🏦",
                   initial_sidebar_state="expanded")

# --- GLOBAL CONSTANTS ---
GEMINI_MODEL = "gemini-3-flash-preview"

SS_WAGE_BASE_2026, ADDL_MED_TAX_THRESHOLD = 168600, 250000
IRA_LIMIT_BASE, PLAN_401K_LIMIT_BASE = 7000, 23500
CATCHUP_401K_BASE, CATCHUP_IRA_BASE = 7500, 1000
SS_MFJ_TIER1_BASE, SS_MFJ_TIER2_BASE = 32000, 44000
SS_SINGLE_TIER1_BASE, SS_SINGLE_TIER2_BASE = 25000, 34000
MEDICARE_GAP_COST, LTC_SHOCK_COST = 15000, 100000
WIDOW_EXPENSE_MULTIPLIER = 0.60
MEDICARE_CLIFF_SINGLE_DROP = 0.25
ROTH_CASH_BUFFER_MARGIN = 0.95
BUDGET_CATEGORIES = ["Housing / Rent", "Transportation", "Food", "Utilities", "Insurance", "Healthcare",
                     "Entertainment", "Education", "Personal Care", "Subscriptions", "Travel", "Debt Payments", "Other"]

# --- GOOGLE ANALYTICS INJECTION ---
GA_MEASUREMENT_ID = st.secrets.get("GA_MEASUREMENT_ID", "")
if GA_MEASUREMENT_ID:
    st.components.v1.html(
        f"""<script async src="https://www.googletagmanager.com/gtag/js?id={html.escape(GA_MEASUREMENT_ID)}"></script><script>window.dataLayer = window.dataLayer || []; function gtag(){{dataLayer.push(arguments);}} gtag('js', new Date()); gtag('config', '{html.escape(GA_MEASUREMENT_ID)}');</script>""",
        width=0, height=0)


# =====================================================================
# 1. CORE HELPER FUNCTIONS & UI COMPONENTS
# =====================================================================

def update_state(key, val):
    st.session_state[key] = val
    st.session_state['dirty'] = True


def mark_dirty():
    st.session_state['dirty'] = True


def get_completion_status():
    has_profile = bool(st.session_state.get('my_name') and str(st.session_state.get('my_name')).strip() != "")
    has_inc = len(st.session_state.get('income_data', [])) > 0
    has_ast = len(st.session_state.get('liquid_assets_data', [])) > 0 or len(
        st.session_state.get('real_estate_data', [])) > 0
    has_exp = len(st.session_state.get('lifetime_expenses', [])) > 0
    score = sum([has_profile, has_inc, has_ast, has_exp]) * 25
    return {"profile": has_profile, "income": has_inc, "assets": has_ast, "expenses": has_exp, "score": score}


def check_ai_rate_limit():
    now = time.time()
    if 'last_ai_call' in st.session_state:
        if now - st.session_state['last_ai_call'] < 3:
            st.warning("⏳ AI is cooling down. Please wait 3 seconds before requesting again.")
            return False
    st.session_state['last_ai_call'] = now
    return True


def city_autocomplete(label, key_prefix, default_val=""):
    input_key = f"{key_prefix}_input"
    if input_key not in st.session_state: st.session_state[input_key] = default_val
    current_val = st.text_input(label, key=input_key,
                                help="Type a major city. The AI uses this to look up local costs of living, property values, and state taxes.",
                                on_change=mark_dirty)
    if current_val and len(current_val) > 2 and current_val != default_val:
        current_val_clean = re.sub(r'[^a-zA-Z0-9, ]', '', str(current_val))[:100]
        try:
            api_key = st.secrets.get("GOOGLE_MAPS_API_KEY", "")
            if api_key:
                url = f"https://maps.googleapis.com/maps/api/place/autocomplete/json?input={current_val_clean}&types=(cities)&key={api_key}"
                res = requests.get(url).json()
                if res.get("status") == "OK":
                    st.caption("Did you mean:")
                    for p in res.get("predictions", [])[:3]:
                        st.button(p["description"], key=f"{key_prefix}_{p['place_id']}",
                                  on_click=lambda k=input_key, v=p["description"]: st.session_state.update(
                                      {k: v, 'dirty': True}))
        except:
            pass
    return current_val


def clean_df(df, primary_key):
    if not isinstance(df, pd.DataFrame) or df.empty: return []
    valid_rows = []
    for r in df.to_dict('records'):
        clean_r = {}
        for vk, vv in r.items():
            clean_r[vk] = None if pd.isna(vv) or vv is pd.NA else vv
        if primary_key not in clean_r or str(clean_r.get(primary_key, '')).strip() != "":
            valid_rows.append(clean_r)
    return valid_rows


def safe_num(val, default=0.0):
    if val is None or pd.isna(val): return default
    if isinstance(val, (int, float)): return float(val) if not math.isnan(val) else default
    try:
        s = str(val).replace(',', '').strip()
        if not s: return default

        is_accounting_neg = False
        if s.startswith('(') and s.endswith(')'):
            is_accounting_neg = True
            s = s[1:-1].strip()

        match = re.search(r'-?\d*\.?\d+', s)
        if match:
            num = float(match.group(0))
            return -abs(num) if is_accounting_neg else num
        return default
    except Exception:
        return default


def scrub_records(records):
    if not records: return []
    scrubbed = []
    for r in records:
        new_r = {}
        for k, v in r.items():
            if v is None or (isinstance(v, float) and math.isnan(v)):
                new_r[k] = None
            else:
                new_r[k] = v
        scrubbed.append(new_r)
    return scrubbed


def apply_chart_theme(fig, title=""):
    fig.update_layout(
        title=dict(text=title, font=dict(size=16, weight=700, color="#0f172a")),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family="Inter", color="#64748b", size=12), hovermode="x unified",
        hoverlabel=dict(bgcolor="white", bordercolor="#e2e8f0", font_size=13, font_family="Inter"),
        
        # --- FIX: Center the legend globally and push it slightly higher ---
        legend=dict(
            orientation="h", 
            yanchor="bottom", 
            y=1.08, 
            xanchor="center", 
            x=0.5, 
            bgcolor="rgba(0,0,0,0)",
            font=dict(size=12)
        ),
        
        xaxis=dict(showgrid=False, zeroline=False, color="#94a3b8", tickfont=dict(size=11)),
        yaxis=dict(gridcolor="#f1f5f9", zeroline=False, tickformat="$,.0f", color="#94a3b8", tickfont=dict(size=11)),
        
        # --- FIX: Only enforce the top margin (to protect the legend). 
        # Let Plotly auto-calculate Left, Right, and Bottom margins to prevent clipping! ---
        margin=dict(t=90) 
    )
    return fig


def stat_card(label, value, color="indigo", icon=""):
    hex_map = {"indigo": "#4f46e5", "emerald": "#10b981", "amber": "#f59e0b", "rose": "#e11d48"}
    bg_map = {"indigo": "#e0e7ff", "emerald": "#d1fae5", "amber": "#fef3c7", "rose": "#ffe4e6"}
    c = hex_map.get(color, "#4f46e5")
    bg = bg_map.get(color, "#e0e7ff")

    st.markdown(
        f"<div style='background: white; border: 1px solid #e2e8f0; padding: 20px; border-radius: 16px; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.05); display: flex; align-items: center; gap: 16px;'><div style='background: {bg}; width: 52px; height: 52px; border-radius: 12px; display: flex; align-items: center; justify-content: center; font-size: 1.6rem; flex-shrink: 0;'>{icon}</div><div><div style='color: #64748b; font-size: 0.85rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px;'>{html.escape(str(label))}</div><div style='color: #0f172a; font-size: 1.6rem; font-weight: 800; letter-spacing: -0.5px; margin-top: 2px;'>{html.escape(str(value))}</div></div></div>",
        unsafe_allow_html=True)


def section_header(title, subtitle="", icon=""):
    icon_html = f"<div style='background: linear-gradient(135deg, #e0e7ff 0%, #f3e8ff 100%); width: 40px; height: 40px; border-radius: 10px; display: flex; align-items: center; justify-content: center; font-size: 1.2rem; box-shadow: inset 0 0 0 1px rgba(255,255,255,0.5);'>{html.escape(str(icon))}</div>" if icon else ""
    margin_left = "52px" if icon else "0px"
    subtitle_html = f"<p style='margin: 8px 0 0 {margin_left}; color:#64748b; font-size:0.95rem; line-height: 1.5;'>{html.escape(str(subtitle))}</p>" if subtitle else ""
    st.markdown(
        f"<div style='margin: 32px 0 24px 0;'><div style='display:flex; align-items:center; gap:12px;'>{icon_html}<h2 style='margin:0; font-size:1.5rem; font-weight:800; color:#0f172a; letter-spacing: -0.5px;'>{html.escape(str(title))}</h2></div>{subtitle_html}</div>",
        unsafe_allow_html=True)


def info_banner(text, type="info"):
    configs = {
        "info": ("#eff6ff", "#3b82f6", "#1d4ed8", "💡"),
        "warning": ("#fffbeb", "#f59e0b", "#b45309", "⚠️"),
        "danger": ("#fef2f2", "#ef4444", "#b91c1c", "🚨")
    }
    bg, border, text_color, emoji = configs.get(type, configs["info"])
    st.markdown(
        f"<div style='background:{bg}; border-left:4px solid {border}; padding:14px 18px; border-radius:8px; margin-bottom:20px; display: flex; align-items: flex-start; gap: 12px; box-shadow: 0 1px 2px rgba(0,0,0,0.02);'><span style='font-size:1.1rem; line-height: 1.2;'>{emoji}</span><span style='color:{text_color}; font-size:0.95rem; font-weight: 500; line-height: 1.5;'>{html.escape(str(text))}</span></div>",
        unsafe_allow_html=True)


def render_status_bar(deplete_year, deplete_age, final_nw, mc_success_rate=None, success_threshold=2000000):
    if deplete_year is not None:
        bg, icon, msg, sub = "#fef2f2", "🔴", "Liquidity Crisis Detected", f"Assets depleted at age {int(deplete_age)} ({int(deplete_year)}). Adjust retirement age or savings rate."
    elif final_nw > success_threshold:
        bg, icon, msg, sub = "#f0fdf4", "🟢", "Strongly On Track", f"${final_nw:,.0f} projected at end of plan. Consider legacy or gifting strategies."
    elif final_nw > 0:
        bg, icon, msg, sub = "#fffbeb", "🟡", "Solvent but Tight", f"${final_nw:,.0f} margin at end of plan. Small changes could significantly improve outcome."
    else:
        bg, icon, msg, sub = "#fef2f2", "🔴", "Projected Insolvency", "Net worth goes negative before end of plan."
        
    mc_html = f"<div style='margin-top: 10px; display: inline-block; background: white; padding: 4px 12px; border-radius: 999px; font-size: 0.85rem; font-weight: 700; border: 1px solid #e2e8f0; box-shadow: 0 1px 2px rgba(0,0,0,0.05);'>Monte Carlo: <span style='color: #4f46e5;'>{mc_success_rate:.0f}% Success Rate</span></div>" if mc_success_rate is not None else ""
    st.markdown(
        f"<div style='background:{bg}; border-radius:16px; padding:20px 24px; display:flex; align-items:flex-start; gap:16px; margin-bottom:24px; border: 1px solid rgba(0,0,0,0.05); box-shadow: 0 4px 6px -1px rgba(0,0,0,0.05);'><span style='font-size:2.2rem; line-height: 1;'>{icon}</span><div><div style='font-weight:800; font-size:1.2rem; color:#0f172a; letter-spacing: -0.5px;'>{html.escape(msg)}</div><div style='font-size:0.95rem; color:#475569; margin-top:4px; line-height: 1.5;'>{html.escape(sub)}</div>{mc_html}</div></div>",
        unsafe_allow_html=True)


def render_empty_state(section, icon):
    st.markdown(
        f"<div style='text-align:center; padding:48px 24px; background:#f8fafc; border-radius:16px; border:2px dashed #cbd5e1; margin-bottom:24px;'><div style='font-size:3rem; margin-bottom:12px;'>{icon}</div><h3 style='color:#0f172a; margin:0 0 8px; font-weight: 700;'>No {html.escape(section)} Added Yet</h3><p style='color:#64748b; margin:0 0 20px; font-size:0.95rem;'>Use the table to add rows, or click the AI button to auto-populate based on your profile.</p></div>",
        unsafe_allow_html=True)


def render_total(label, series):
    total = pd.to_numeric(series, errors='coerce').fillna(0).sum()
    st.markdown(
        f"<div style='text-align: right; font-weight: 700; color: #4f46e5; font-size: 1.1rem; padding-top: 8px;'>{label}: <span style='color: #0f172a;'>${total:,.0f}</span></div>",
        unsafe_allow_html=True)


# --- DESIGN SYSTEM & CSS ---
if os.path.exists("style.css"):
    with open("style.css", "r") as f:
        st.markdown(f"<style>{f.read()}</style>", unsafe_allow_html=True)
else:
    st.warning("⚠️ `style.css` not found. Please place the CSS rules into `style.css` for proper styling.")

# --- 2. FIREBASE & SESSION INIT ---
if 'firebase_enabled' not in st.session_state:
    st.session_state['firebase_enabled'] = True
    if not firebase_admin._apps:
        try:
            cred = credentials.Certificate(
                dict(st.secrets["firebase"])) if "firebase" in st.secrets else credentials.Certificate(
                'firebase_creds.json')
            firebase_admin.initialize_app(cred)
        except Exception:
            st.session_state['firebase_enabled'] = False
            st.warning("⚠️ Cloud Sync Disabled (Local Mode Active). Firebase initialization failed.")

try:
    if st.session_state['firebase_enabled']: db = firestore.client()
except Exception:
    st.session_state['firebase_enabled'] = False

FIREBASE_WEB_API_KEY, GEMINI_API_KEY = st.secrets.get("FIREBASE_WEB_API_KEY", ""), st.secrets.get("GEMINI_API_KEY", "")

with st.spinner("Authenticating Session..."):
    cookie_manager = stx.CookieManager(key="auth_cookie_manager")
    if cookie_manager.get_all() is None: time.sleep(0.5)


def sign_in(email, password): return requests.post(
    f"https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key={FIREBASE_WEB_API_KEY}",
    json={"email": email, "password": password, "returnSecureToken": True}).json()


def sign_up(email, password): return requests.post(
    f"https://identitytoolkit.googleapis.com/v1/accounts:signUp?key={FIREBASE_WEB_API_KEY}",
    json={"email": email, "password": password, "returnSecureToken": True}).json()


def load_user_data(email):
    if email == "guest_demo" or not st.session_state.get('firebase_enabled', False): return {}
    doc = db.collection('users').document(email).get()
    return doc.to_dict() if doc.exists else {}


def save_profile():
    if st.session_state.get('user_email') == "guest_demo": st.error(
        "Persistent configurations disabled within the demonstration environment."); return
    if not st.session_state.get('firebase_enabled', True): st.error(
        "Cloud saving disabled due to connection issues."); return

    my_age = relativedelta(datetime.date.today(), st.session_state.get('my_dob', datetime.date(1980, 1, 1))).years
    spouse_age = relativedelta(datetime.date.today(), st.session_state.get('spouse_dob', datetime.date(1982, 1,
                                                                                                       1))).years if st.session_state.get(
        'has_spouse') else 0

    user_data = {
        "personal_info": {
            "name": st.session_state.get('my_name', ''),
            "dob": st.session_state.get('my_dob', datetime.date(1980, 1, 1)).strftime("%Y-%m-%d"),
            "age": my_age, "retire_age": st.session_state.get('ret_age', 65),
            "spouse_retire_age": st.session_state.get('s_ret_age', 65),
            "my_life_exp": st.session_state.get('my_life_exp', 95),
            "spouse_life_exp": st.session_state.get('spouse_life_exp', 95),
            "current_city": st.session_state.get('curr_city_flow', ''),
            "has_spouse": st.session_state.get('has_spouse', False),
            "spouse_name": st.session_state.get('spouse_name', ''),
            "spouse_dob": st.session_state.get('spouse_dob', datetime.date(1982, 1, 1)).strftime(
                "%Y-%m-%d") if st.session_state.get('has_spouse') else None, "spouse_age": spouse_age,
            "kids": st.session_state.get('kids_data', [])
        },
        "retire_city": st.session_state.get('retire_city_flow', ''),
        "income": clean_df(pd.DataFrame(st.session_state.get('income_data', [])), "Description"),
        "real_estate": clean_df(pd.DataFrame(st.session_state.get('real_estate_data', [])), "Property Name"),
        "business": clean_df(pd.DataFrame(st.session_state.get('business_data', [])), "Business Name"),
        "liquid_assets": clean_df(pd.DataFrame(st.session_state.get('liquid_assets_data', [])), "Account Name"),
        "liabilities": clean_df(pd.DataFrame(st.session_state.get('liabilities_data', [])), "Debt Name"),
        "lifetime_expenses": clean_df(pd.DataFrame(st.session_state.get('lifetime_expenses', [])), "Description"),
        "assumptions": st.session_state.get('assumptions', {})
    }
    db.collection('users').document(st.session_state['user_email']).set(user_data)
    st.session_state['user_data'] = user_data
    st.session_state['dirty'] = False
    st.toast("✅ Complete Financial Blueprint Synchronized Successfully!")
    st.rerun()


def call_gemini_json(prompt, retries=3):
    if not GEMINI_API_KEY:
        st.error("⚠️ GEMINI_API_KEY is missing. AI operations disabled.")
        return None
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"
    payload = {"contents": [{"parts": [{"text": prompt}]}],
               "generationConfig": {"responseMimeType": "application/json"}}

    for attempt in range(retries):
        try:
            res = requests.post(url, json=payload, timeout=45)
            res.raise_for_status()
            res_json = res.json()
            if "error" in res_json:
                if attempt == retries - 1: st.error(f"⚠️ API Error: {res_json['error'].get('message')}"); return None
                time.sleep(2 ** attempt);
                continue
            text = res_json['candidates'][0]['content']['parts'][0]['text'].strip()
            text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
            text = re.sub(r"\n?```$", "", text).strip()
            parsed = json.loads(text)
            return parsed
        except requests.exceptions.RequestException as e:
            if attempt == retries - 1: st.error(f"⚠️ Network Error connecting to AI: {e}")
            time.sleep(2 ** attempt)
        except json.JSONDecodeError:
            if attempt == retries - 1: st.error("⚠️ AI returned invalid JSON formatting. Please try again.")
            time.sleep(2 ** attempt)
        except Exception as e:
            if attempt == retries - 1: st.error(f"⚠️ Unexpected AI Error: {e}")
            time.sleep(2 ** attempt)
    return None


def call_gemini_text(prompt, retries=3):
    if not GEMINI_API_KEY:
        st.error("⚠️ GEMINI_API_KEY is missing. AI operations disabled.")
        return None
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"
    payload = {"contents": [{"parts": [{"text": prompt}]}]}
    for attempt in range(retries):
        try:
            res = requests.post(url, json=payload, timeout=60)
            res.raise_for_status()
            res_json = res.json()
            if "error" in res_json:
                if attempt == retries - 1: st.error(f"⚠️ API Error: {res_json['error'].get('message')}"); return None
                time.sleep(2 ** attempt);
                continue
            return res_json['candidates'][0]['content']['parts'][0]['text']
        except requests.exceptions.RequestException as e:
            if attempt == retries - 1: st.error(f"⚠️ Network Error connecting to AI: {e}")
            time.sleep(2 ** attempt)
        except KeyError:
            if attempt == retries - 1: st.error("⚠️ Unexpected AI response format.")
            time.sleep(2 ** attempt)
        except Exception as e:
            if attempt == retries - 1: st.error(f"⚠️ Unexpected AI Error: {e}")
            time.sleep(2 ** attempt)
    return None


def initialize_session_state():
    if not st.session_state.get('initialized', False):
        ud = st.session_state.get('user_data', {})
        p_info = ud.get('personal_info', {})

        st.session_state['my_name'] = p_info.get('name', '')
        st.session_state['my_dob'] = datetime.datetime.strptime(p_info.get('dob', '1980-01-01'),
                                                                "%Y-%m-%d").date() if p_info.get(
            'dob') else datetime.date(1980, 1, 1)
        st.session_state['has_spouse'] = p_info.get('has_spouse', False)
        st.session_state['spouse_name'] = p_info.get('spouse_name', '')
        st.session_state['spouse_dob'] = datetime.datetime.strptime(p_info.get('spouse_dob', '1982-01-01'),
                                                                    "%Y-%m-%d").date() if p_info.get(
            'spouse_dob') else datetime.date(1982, 1, 1)

        st.session_state['ret_age'] = int(p_info.get('retire_age', 65))
        st.session_state['s_ret_age'] = int(p_info.get('spouse_retire_age', 65))
        st.session_state['my_life_exp'] = int(p_info.get('my_life_exp', 95))
        st.session_state['spouse_life_exp'] = int(p_info.get('spouse_life_exp', 95))
        st.session_state['kids_data'] = p_info.get('kids', [])
        st.session_state['curr_city_flow'] = p_info.get('current_city', '')
        st.session_state['retire_city_flow'] = ud.get('retire_city', st.session_state.get('curr_city_flow', ''))
        st.session_state['income_data'] = ud.get('income', [])
        st.session_state['real_estate_data'] = ud.get('real_estate', [])
        st.session_state['business_data'] = ud.get('business', [])
        st.session_state['liquid_assets_data'] = ud.get('liquid_assets', [])
        st.session_state['liabilities_data'] = ud.get('liabilities', [])

        life_exp = ud.get('lifetime_expenses', [])
        if not life_exp:
            migrated, current_year = [], datetime.date.today().year
            for c in ud.get('current_expenses', []):
                if c.get("Description"): migrated.append(
                    {"Description": c.get("Description"), "Category": c.get("Category", "Other"),
                     "Frequency": c.get("Frequency", "Monthly"), "Amount ($)": c.get("Amount ($)", 0),
                     "Start Phase": "Now", "Start Year": None, "End Phase": "At Retirement", "End Year": None,
                     "AI Estimate?": c.get("AI Estimate?", False)})
            for r in ud.get('retire_expenses', []):
                if r.get("Description"): migrated.append(
                    {"Description": r.get("Description"), "Category": r.get("Category", "Other"),
                     "Frequency": r.get("Frequency", "Monthly"), "Amount ($)": r.get("Amount ($)", 0),
                     "Start Phase": "At Retirement", "Start Year": None, "End Phase": "End of Life", "End Year": None,
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
            life_exp = migrated if migrated else []
        st.session_state['lifetime_expenses'] = life_exp

        st.session_state['assumptions'] = ud.get('assumptions', {"inflation": 3.0, "inflation_healthcare": 5.5,
                                                                 "inflation_education": 4.5, "market_growth": 7.0,
                                                                 "income_growth": 3.0, "property_growth": 3.0,
                                                                 "rent_growth": 3.0, "current_tax_rate": 5.0,
                                                                 "retire_tax_rate": 0.0, "roth_conversions": False,
                                                                 "roth_target": "24%",
                                                                 "withdrawal_strategy": "Standard",
                                                                 "stress_test": False, "glidepath": True,
                                                                 "medicare_gap": True, "medicare_cliff": True,
                                                                 "ltc_shock": False, "shortfall_rate": 12.0})
        st.session_state['dirty'] = False
        st.session_state['initialized'] = True


if 'user_email' not in st.session_state:
    saved_email = cookie_manager.get(cookie="user_email")
    if saved_email and not st.session_state.get('logged_out_flag'):
        st.session_state['user_email'] = saved_email
        st.session_state['user_data'] = load_user_data(saved_email)
        st.rerun()

    st.markdown(
        "<div style='text-align: center; padding-top: 50px;'><h1>🏦 AI Retirement Planner Pro</h1></div><p style='text-align: center; color: #64748b;'>Secure Login required to access your financial blueprint.</p>",
        unsafe_allow_html=True)
    c1, c2, c3 = st.columns([1, 2, 1])
    with c2:
        tab1, tab2 = st.tabs(["Secure Login", "New Account"])
        with tab1:
            le, lp = st.text_input("Email", key="le"), st.text_input("Password", type="password", key="lp")
            if st.button("Sign In", type="primary", use_container_width=True):
                res = sign_in(le, lp)
                if "idToken" in res:
                    st.session_state.pop('logged_out_flag', None)
                    st.session_state['user_email'] = res['email'];
                    st.session_state['user_data'] = load_user_data(res['email'])
                    cookie_manager.set("user_email", res['email'],
                                       expires_at=datetime.datetime.now() + datetime.timedelta(days=30))
                    time.sleep(0.2);
                    st.rerun()
                else:
                    st.error("Login failed. Please check your email and password.")
        with tab2:
            se, sp = st.text_input("New Email", key="se"), st.text_input("New Password", type="password", key="sp")
            if st.button("Create Account", type="primary", use_container_width=True):
                if len(sp) >= 6:
                    res = sign_up(se, sp)
                    if "idToken" in res:
                        st.session_state.pop('logged_out_flag', None)
                        st.session_state['user_email'] = res['email'];
                        st.session_state['user_data'] = {}
                        cookie_manager.set("user_email", res['email'],
                                           expires_at=datetime.datetime.now() + datetime.timedelta(days=30))
                        time.sleep(0.2);
                        st.rerun()
                else:
                    st.warning("Min 6 characters.")
        st.divider()
        if st.button("🚀 Try the Demo (Guest Mode)", use_container_width=True):
            st.session_state.pop('logged_out_flag', None)
            st.session_state['user_email'] = "guest_demo"
            
            # --- FIX: Pre-load the "Johnson Family" to demonstrate the engine's capabilities immediately ---
            st.session_state['user_data'] = {
                "personal_info": {
                    "name": "John Johnson", "dob": "1984-05-15",
                    "retire_age": 60, "spouse_retire_age": 62,
                    "my_life_exp": 92, "spouse_life_exp": 95,
                    "current_city": "San Francisco, CA, USA",
                    "has_spouse": True, "spouse_name": "Jane Johnson", "spouse_dob": "1986-08-20",
                    "kids": [{"name": "Timmy", "age": 10}, {"name": "Sarah", "age": 8}]
                },
                "retire_city": "Sedona, AZ, USA",
                "income": [
                    {"Description": "John Base Salary", "Category": "Base Salary (W-2)", "Owner": "Me", "Annual Amount ($)": 180000, "Start Year": 2024, "End Year": 2100, "Stop at Ret.?": True, "Override Growth (%)": 3.0},
                    {"Description": "Jane Base Salary", "Category": "Base Salary (W-2)", "Owner": "Spouse", "Annual Amount ($)": 120000, "Start Year": 2024, "End Year": 2100, "Stop at Ret.?": True, "Override Growth (%)": 3.0},
                    {"Description": "John Social Security", "Category": "Social Security", "Owner": "Me", "Annual Amount ($)": 42000, "Start Year": 2051, "End Year": 2100, "Stop at Ret.?": False, "Override Growth (%)": 0},
                    {"Description": "Jane Social Security", "Category": "Social Security", "Owner": "Spouse", "Annual Amount ($)": 36000, "Start Year": 2053, "End Year": 2100, "Stop at Ret.?": False, "Override Growth (%)": 0}
                ],
                "liquid_assets": [
                    {"Account Name": "John 401(k)", "Type": "Traditional 401(k)", "Owner": "Me", "Current Balance ($)": 400000, "Annual Contribution ($/yr)": 15000, "Est. Annual Growth (%)": 7.0, "Stop Contrib at Ret.?": True},
                    {"Account Name": "Jane 401(k)", "Type": "Traditional 401(k)", "Owner": "Spouse", "Current Balance ($)": 300000, "Annual Contribution ($/yr)": 12000, "Est. Annual Growth (%)": 7.0, "Stop Contrib at Ret.?": True},
                    {"Account Name": "Joint Brokerage", "Type": "Brokerage (Taxable)", "Owner": "Joint", "Current Balance ($)": 150000, "Annual Contribution ($/yr)": 6000, "Est. Annual Growth (%)": 7.0, "Stop Contrib at Ret.?": False},
                    {"Account Name": "Emergency Fund", "Type": "HYSA", "Owner": "Joint", "Current Balance ($)": 45000, "Annual Contribution ($/yr)": 0, "Est. Annual Growth (%)": 4.0, "Stop Contrib at Ret.?": False},
                    {"Account Name": "Timmy 529", "Type": "529 Plan", "Owner": "Joint", "Current Balance ($)": 35000, "Annual Contribution ($/yr)": 2400, "Est. Annual Growth (%)": 7.0, "Stop Contrib at Ret.?": False},
                    {"Account Name": "Sarah 529", "Type": "529 Plan", "Owner": "Joint", "Current Balance ($)": 28000, "Annual Contribution ($/yr)": 2400, "Est. Annual Growth (%)": 7.0, "Stop Contrib at Ret.?": False}
                ],
                "real_estate": [
                    {"Property Name": "SF Primary Home", "Is Primary Residence?": True, "Market Value ($)": 1200000, "Mortgage Balance ($)": 650000, "Interest Rate (%)": 3.5, "Mortgage Payment ($)": 3500, "Monthly Expenses ($)": 1200, "Monthly Rent ($)": 0, "Override Prop Growth (%)": 4.0, "Override Rent Growth (%)": 3.0}
                ],
                "business": [],
                "liabilities": [
                    {"Debt Name": "Family SUV", "Type": "Auto", "Current Balance ($)": 24000, "Interest Rate (%)": 4.9, "Monthly Payment ($)": 650}
                ],
                "lifetime_expenses": [
                    {"Description": "Base Living Expenses", "Category": "Other", "Frequency": "Monthly", "Amount ($)": 6000, "Start Phase": "Now", "Start Year": None, "End Phase": "At Retirement", "End Year": None, "AI Estimate?": False},
                    {"Description": "Retirement Go-Go Years", "Category": "Other", "Frequency": "Monthly", "Amount ($)": 8500, "Start Phase": "At Retirement", "Start Year": None, "End Phase": "Custom Year", "End Year": 2054, "AI Estimate?": False},
                    {"Description": "Retirement Slow-Go Years", "Category": "Other", "Frequency": "Monthly", "Amount ($)": 6000, "Start Phase": "Custom Year", "Start Year": 2055, "End Phase": "End of Life", "End Year": None, "AI Estimate?": False},
                    {"Description": "Timmy College", "Category": "Education", "Frequency": "Yearly", "Amount ($)": 35000, "Start Phase": "Custom Year", "Start Year": 2032, "End Phase": "Custom Year", "End Year": 2035, "AI Estimate?": False},
                    {"Description": "Sarah College", "Category": "Education", "Frequency": "Yearly", "Amount ($)": 35000, "Start Phase": "Custom Year", "Start Year": 2034, "End Phase": "Custom Year", "End Year": 2037, "AI Estimate?": False}
                ],
                "assumptions": {
                    "inflation": 3.0, "inflation_healthcare": 5.5, "inflation_education": 4.5, "market_growth": 7.0,
                    "income_growth": 3.0, "property_growth": 3.0, "rent_growth": 3.0, "current_tax_rate": 9.3,
                    "retire_tax_rate": 4.5, "roth_conversions": True, "roth_target": "24%", "withdrawal_strategy": "Standard",
                    "stress_test": False, "glidepath": True, "medicare_gap": True, "medicare_cliff": True,
                    "ltc_shock": False, "shortfall_rate": 12.0
                }
            }
            
            cookie_manager.set("user_email", "guest_demo", expires_at=datetime.datetime.now() + datetime.timedelta(days=1))
            st.toast("Guest mode active: Demo profile loaded successfully!", icon="🚀")
            st.rerun()
    st.stop()

# --- AFTER AUTH: INITIALIZE STATE ---
initialize_session_state()


# --- MODULE LEVEL SIMULATION CORE (CACHED) ---
def build_sim_context():
    current_year = datetime.date.today().year
    my_age = relativedelta(datetime.date.today(), st.session_state.get('my_dob', datetime.date(1980, 1, 1))).years
    spouse_age = relativedelta(datetime.date.today(), st.session_state.get('spouse_dob', datetime.date(1982, 1,
                                                                                                       1))).years if st.session_state.get(
        'has_spouse') else 0
    my_birth_year = st.session_state.get('my_dob', datetime.date(1980, 1, 1)).year
    spouse_birth_year = st.session_state.get('spouse_dob', datetime.date(1982, 1, 1)).year if st.session_state.get(
        'has_spouse') else current_year

    ret_age, s_ret_age = st.session_state.get('ret_age', 65), st.session_state.get('s_ret_age', 65)
    my_life_exp_val, spouse_life_exp_val = st.session_state.get('my_life_exp', 95), (
        st.session_state.get('spouse_life_exp', 95) if st.session_state.get('has_spouse') else 0)

    primary_retire_year = my_birth_year + ret_age
    spouse_retire_year = spouse_birth_year + s_ret_age if st.session_state.get('has_spouse') else 9999
    primary_end_year = my_birth_year + my_life_exp_val
    spouse_end_year = spouse_birth_year + spouse_life_exp_val if st.session_state.get('has_spouse') else current_year

    max_year = max(primary_end_year, spouse_end_year)
    max_years = max_year - current_year

    primary_rmd_age = 73 if my_birth_year <= 1959 else 75
    spouse_rmd_age = 73 if spouse_birth_year <= 1959 else 75

    asm = st.session_state.get('assumptions', {})
    df_re = pd.DataFrame(st.session_state.get('real_estate_data', []))
    owns_home = not df_re[df_re[
                              "Is Primary Residence?"] == True].empty if not df_re.empty and "Is Primary Residence?" in df_re.columns else False
    df_debt = pd.DataFrame(st.session_state.get('liabilities_data', []))
    debt_records = [d for d in df_debt.to_dict('records') if
                    d.get("Debt Name") and safe_num(d.get("Current Balance ($)")) > 0] if not df_debt.empty else []

    return {
        'current_year': current_year, 'my_birth_year': my_birth_year, 'spouse_birth_year': spouse_birth_year,
        'primary_end_year': primary_end_year, 'spouse_end_year': spouse_end_year,
        'has_spouse': st.session_state.get('has_spouse', False),
        'primary_retire_year': primary_retire_year, 'spouse_retire_year': spouse_retire_year,
        'primary_rmd_age': primary_rmd_age, 'spouse_rmd_age': spouse_rmd_age,
        'mkt': float(asm.get('market_growth', 7.0)), 'infl': float(asm.get('inflation', 3.0)),
        'infl_hc': float(asm.get('inflation_healthcare', 5.5)), 'infl_ed': float(asm.get('inflation_education', 4.5)),
        'inc_g': float(asm.get('income_growth', 3.0)), 'prop_g': float(asm.get('property_growth', 3.0)),
        'rent_g': float(asm.get('rent_growth', 3.0)), 'cur_t': float(asm.get('current_tax_rate', 5.0)),
        'ret_t': float(asm.get('retire_tax_rate', 0.0)),
        'stress_test': asm.get('stress_test', False), 'glidepath': asm.get('glidepath', True),
        'medicare_gap': asm.get('medicare_gap', True), 'medicare_cliff': asm.get('medicare_cliff', True),
        'ltc_shock': asm.get('ltc_shock', False), 'shortfall_rate': float(asm.get('shortfall_rate', 12.0)) / 100.0,
        'roth_conversions': asm.get('roth_conversions', False), 'roth_target': asm.get('roth_target', '24%'),
        'active_withdrawal_strategy': asm.get('withdrawal_strategy', 'Standard'),
        'owns_home': owns_home, 'kids_data': st.session_state.get('kids_data', []),
        'max_years': max_years, 'max_year': max_year, 'my_life_exp_val': my_life_exp_val,
        'spouse_life_exp_val': spouse_life_exp_val,
        'ast_records': scrub_records(st.session_state.get('liquid_assets_data', [])),
        'debt_records': scrub_records(debt_records),
        're_records': scrub_records(st.session_state.get('real_estate_data', [])),
        'biz_records': scrub_records(st.session_state.get('business_data', [])),
        'inc_records': scrub_records(st.session_state.get('income_data', [])),
        'exp_records': scrub_records(st.session_state.get('lifetime_expenses', [])),
        'my_age': my_age, 'spouse_age': spouse_age
    }

# --- MODULE LEVEL STATIC RESOURCES & HELPERS ---
IRS_UNIFORM_TABLE = {73: 26.5, 74: 25.5, 75: 24.6, 76: 23.7, 77: 22.9, 78: 22.0, 79: 21.1, 80: 20.2, 81: 19.4,
                     82: 18.5, 83: 17.7, 84: 16.8, 85: 16.0, 86: 15.2, 87: 14.4, 88: 13.7, 89: 12.9, 90: 12.2,
                     91: 11.5, 92: 10.8, 93: 10.1, 94: 9.5, 95: 8.9, 96: 8.4, 97: 7.8, 98: 7.3, 99: 6.8, 100: 6.4,
                     101: 6.0, 102: 5.6, 103: 5.2, 104: 4.9, 105: 4.6, 106: 4.3, 107: 4.1, 108: 3.9, 109: 3.7,
                     110: 3.5, 111: 3.4, 112: 3.3, 113: 3.1, 114: 3.0, 115: 2.9, 116: 2.8, 117: 2.7, 118: 2.5,
                     119: 2.3, 120: 2.0}

def calc_federal_tax(ordinary_income, is_mfj, year_offset, inflation_rate):
    infl_factor = (1 + inflation_rate / 100) ** year_offset
    std_deduction = (29200 if is_mfj else 14600) * infl_factor
    taxable_ordinary = max(0, ordinary_income - std_deduction)

    b_mfj = [(23200, 0.10), (94300, 0.12), (201050, 0.22), (383900, 0.24), (487450, 0.32), (731200, 0.35), (float('inf'), 0.37)]
    b_single = [(11600, 0.10), (47150, 0.12), (100525, 0.22), (191950, 0.24), (243725, 0.32), (609350, 0.35), (float('inf'), 0.37)]
    brackets = b_mfj if is_mfj else b_single

    ord_tax, prev_limit = 0, 0
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

def get_ltcg_rate(ordinary_income, is_mfj, year_offset, inflation_rate):
    infl_factor = (1 + inflation_rate / 100) ** year_offset
    niit_threshold = ADDL_MED_TAX_THRESHOLD * infl_factor
    cg_threshold_0 = 94050 * infl_factor if is_mfj else 47025 * infl_factor
    cg_threshold_15 = 583750 * infl_factor if is_mfj else 518900 * infl_factor

    if ordinary_income < cg_threshold_0: base_rate = 0.0
    elif ordinary_income < cg_threshold_15: base_rate = 0.15
    else: base_rate = 0.20
    
    niit = 0.038 if ordinary_income > niit_threshold else 0.0
    return base_rate + niit

def get_ss_multi(birth_year, claim_year):
    fra = 67 if birth_year >= 1960 else (66 + (min(birth_year - 1954, 10) / 12.0) if birth_year >= 1955 else 66)
    claim_age = claim_year - birth_year
    if claim_age < fra:
        months_early = (fra - claim_age) * 12
        if months_early <= 36: return 1.0 - (months_early * (5 / 9 * 0.01))
        else: return 1.0 - (36 * (5 / 9 * 0.01)) - ((months_early - 36) * (5 / 12 * 0.01))
    elif claim_age > fra:
        months_late = min((claim_age - fra) * 12, (70 - fra) * 12)
        return 1.0 + (months_late * (2 / 3 * 0.01))
    return 1.0

def _withdraw(a, current_shortfall, tax_treatment, ctx, my_current_age, spouse_current_age, active_mfj, year_offset, tax_base_ord, marginal_rate, state_tax_rate, year, yd):
    if a['bal'] <= 0 or current_shortfall <= 0: return current_shortfall, 0.0, 0.0

    eff_tax = 0.0
    if tax_treatment == 'cg':
        is_step_up = ctx['has_spouse'] and (not (year <= ctx['primary_end_year']) or not (year <= ctx['spouse_end_year']))
        eff_tax = 0.0 if is_step_up else (get_ltcg_rate(tax_base_ord, active_mfj, year_offset, ctx['infl']) + (state_tax_rate / 100.0))
    elif tax_treatment == 'ordinary':
        o_acct = a.get('Owner', 'Me')
        o_age = my_current_age if o_acct in ['Me', 'Joint'] else spouse_current_age
        o_ret_yr = ctx['primary_retire_year'] if o_acct in ['Me', 'Joint'] else ctx['spouse_retire_year']
        o_birth = ctx['my_birth_year'] if o_acct in ['Me', 'Joint'] else ctx['spouse_birth_year']
        rule_of_55 = (year >= o_ret_yr) and ((o_ret_yr - o_birth) >= 55)
        penalty = 0.10 if (o_age < 59.5 and not rule_of_55) else 0.0
        eff_tax = min(marginal_rate + (state_tax_rate / 100.0) + penalty, 0.99)
    elif tax_treatment == 'free':
        o_acct = a.get('Owner', 'Me')
        o_age = my_current_age if o_acct in ['Me', 'Joint'] else spouse_current_age
        o_ret_yr = ctx['primary_retire_year'] if o_acct in ['Me', 'Joint'] else ctx['spouse_retire_year']
        o_birth = ctx['my_birth_year'] if o_acct in ['Me', 'Joint'] else ctx['spouse_birth_year']
        rule_of_55 = (year >= o_ret_yr) and ((o_ret_yr - o_birth) >= 55)
        penalty = 0.10 if (a.get('Type') in ['Roth 401(k)', 'Roth 401k/IRA', 'Roth IRA'] and o_age < 59.5 and not rule_of_55) else 0.0
        eff_tax = min(penalty, 0.99)

    req_gross = current_shortfall / max(0.01, (1.0 - eff_tax))
    withdrawn = min(a['bal'], req_gross)
    a['bal'] -= withdrawn
    tax_inc = withdrawn * eff_tax
    net_cash = withdrawn - tax_inc

    if withdrawn > 0:
        yd[f"Income: Withdrawal ({a.get('Account Name', 'Account')})"] = yd.get(f"Income: Withdrawal ({a.get('Account Name', 'Account')})", 0) + withdrawn

    return current_shortfall - net_cash, tax_inc, withdrawn
# ------------------------------------------------

def run_simulation(mkt_sequence, ctx_input):
    ctx = copy.deepcopy(ctx_input)
    
    if ctx['max_years'] <= 0: return [], [], [], {}

    sim_assets = [{"Account Name": a.get("Account Name"), "Type": a.get("Type"), "Owner": a.get("Owner", "Me"),
                   "bal": safe_num(a.get("Current Balance ($)")),
                   "contrib": safe_num(a.get("Annual Contribution ($/yr)")), "growth": a.get("Est. Annual Growth (%)"),
                   "stop_at_ret": a.get("Stop Contrib at Ret.?", True)} for a in ctx['ast_records'] if
                  a.get("Account Name")]
    if not sim_assets: sim_assets = [
        {"Account Name": "Unallocated Cash", "Type": "Checking/Savings", "Owner": "Me", "bal": 0.0, "contrib": 0.0,
         "growth": 0.0, "stop_at_ret": False}]

    sim_debts = [{"bal": safe_num(d.get("Current Balance ($)")), "pmt": safe_num(d.get("Monthly Payment ($)")) * 12,
                  "rate": safe_num(d.get("Interest Rate (%)")) / 100, "name": d.get("Debt Name")} for d in
                 ctx['debt_records'] if d.get("Debt Name")]
    sim_re = [{"name": r.get("Property Name", "Property"), "is_primary": r.get("Is Primary Residence?", False),
               "val": safe_num(r.get("Market Value ($)")), "debt": safe_num(r.get("Mortgage Balance ($)")),
               "pmt": safe_num(r.get("Mortgage Payment ($)")) * 12, "exp": safe_num(r.get("Monthly Expenses ($)")) * 12,
               "rent": safe_num(r.get("Monthly Rent ($)")) * 12,
               "v_growth": float(r.get("Override Prop Growth (%)")) if pd.notna(
                   r.get("Override Prop Growth (%)")) and str(r.get("Override Prop Growth (%)")).strip() != "" else ctx[
                   'prop_g'], "r_growth": float(r.get("Override Rent Growth (%)")) if pd.notna(
            r.get("Override Rent Growth (%)")) and str(r.get("Override Rent Growth (%)")).strip() != "" else ctx[
            'rent_g'], "rate": safe_num(r.get("Interest Rate (%)")) / 100} for r in ctx['re_records'] if
              r.get("Property Name")]
    sim_biz = [{"name": b.get("Business Name"), "val": safe_num(b.get("Total Valuation ($)")),
                "own": safe_num(b.get("Your Ownership (%)")) / 100.0,
                "dist": safe_num(b.get("Annual Distribution ($)")),
                "v_growth": float(b.get("Override Val. Growth (%)")) if pd.notna(
                    b.get("Override Val. Growth (%)")) and str(b.get("Override Val. Growth (%)")).strip() != "" else
                ctx['mkt'], "d_growth": float(b.get("Override Dist. Growth (%)")) if pd.notna(
            b.get("Override Dist. Growth (%)")) and str(b.get("Override Dist. Growth (%)")).strip() != "" else ctx[
            'inc_g']} for b in ctx['biz_records'] if b.get("Business Name")]

    unfunded_debt_bal = 0
    prev_unfunded_debt_bal = 0
    last_irmaa_tier = 0

    primary_ss_record = next(
        (r for r in ctx['inc_records'] if r.get('Category') == 'Social Security' and r.get('Owner') == 'Me'), None)
    spouse_ss_record = next(
        (r for r in ctx['inc_records'] if r.get('Category') == 'Social Security' and r.get('Owner') == 'Spouse'), None)

    if primary_ss_record:
        raw_start = primary_ss_record.get('Start Year')
        primary_ss_start_year = ctx['primary_retire_year'] if (raw_start is None or pd.isna(raw_start) or str(raw_start).strip() == "") else int(safe_num(raw_start))
        
        # --- FIX: Hard Engine Guardrails for SS Claim Age ---
        primary_ss_start_year = max(ctx['my_birth_year'] + 62, min(ctx['my_birth_year'] + 70, primary_ss_start_year))
    else:
        primary_ss_start_year = 9999

    if spouse_ss_record:
        raw_start = spouse_ss_record.get('Start Year')
        spouse_ss_start_year = ctx['spouse_retire_year'] if (raw_start is None or pd.isna(raw_start) or str(raw_start).strip() == "") else int(safe_num(raw_start))
        
        # --- FIX: Hard Engine Guardrails for Spouse SS Claim Age ---
        if ctx['has_spouse']:
            spouse_ss_start_year = max(ctx['spouse_birth_year'] + 62, min(ctx['spouse_birth_year'] + 70, spouse_ss_start_year))
    else:
        spouse_ss_start_year = 9999

    primary_ss_multi = get_ss_multi(ctx['my_birth_year'], primary_ss_start_year)
    spouse_ss_multi = get_ss_multi(ctx['spouse_birth_year'], spouse_ss_start_year)

    primary_ss_frozen_val = 0
    spouse_ss_frozen_val = 0

    sim_res, det_res, nw_det_res = [], [], []
    milestones_by_year = {}

    tapped_brokerage = tapped_trad = tapped_roth = cash_depleted = False
    ss_started_me = ss_started_spouse = irmaa_triggered = spouse_died_notified = me_died_notified = False

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
            milestones_by_year[year].append({"desc": "🏥 Medicare Kicks In (Spouse)", "amt": 0, "type": "system"})

        if is_my_alive and my_current_age == ctx['primary_rmd_age']:
            if year not in milestones_by_year: milestones_by_year[year] = []
            milestones_by_year[year].append({"desc": "🏦 Your RMDs Begin", "amt": 0, "type": "system"})

        if ctx['has_spouse'] and is_spouse_alive and spouse_current_age == ctx['spouse_rmd_age']:
            if year not in milestones_by_year: milestones_by_year[year] = []
            milestones_by_year[year].append({"desc": "🏦 Spouse RMDs Begin", "amt": 0, "type": "system"})

        # --- MAKE SURE THIS LINE EXISTS ---
        is_retired = year >= ctx['primary_retire_year']

        # --- FIX: Pre-initialize all tax keys so Pandas always sees them ---
        yd = {
            "Year": year, "Age (Primary)": my_current_age, "Age (Spouse)": spouse_current_age,
            "Tax Breakdown: Federal": 0.0, "Tax Breakdown: State": 0.0, 
            "Tax Breakdown: FICA": 0.0, "Tax Breakdown: Withdrawals": 0.0,
            "Tax Breakdown: Roth Conversion": 0.0
        }
        nw_yd = {"Year": year, "Age (Primary)": my_current_age, "Age (Spouse)": spouse_current_age}

        # --- NEW: Track Roth Taxes separately for the year ---
        roth_fed_tax_paid = 0.0
        roth_state_tax_paid = 0.0

        annual_inc, annual_ss, pre_tax_ord, total_tax = 0, 0, 0, 0
        earned_income_me, earned_income_spouse = 0, 0
        match_income_by_owner = {"Me": 0, "Spouse": 0, "Joint": 0}

        base_mkt_yr = mkt_sequence[year_offset]
        if ctx['stress_test'] and year == ctx['primary_retire_year']:
            mkt_glide = -25.0
        elif ctx['glidepath'] and is_retired:
            years_retired = year - ctx['primary_retire_year']
            mkt_glide = max(3.0, base_mkt_yr - (math.floor(years_retired / 5) * 1.0))
        else:
            mkt_glide = base_mkt_yr

        active_mfj = True if ctx['has_spouse'] and is_my_alive and is_spouse_alive else False

        rmd_income = 0
        for a in sim_assets:
            if a.get('Type', '').strip() in ['Traditional 401(k)', 'Traditional 401k/IRA', 'Traditional IRA'] and a['bal'] > 0:
                owner = a.get('Owner', 'Me')
                owner_age = my_current_age if owner in ['Me', 'Joint'] else spouse_current_age
                owner_alive = is_my_alive if owner in ['Me', 'Joint'] else is_spouse_alive
                owner_rmd_age = ctx['primary_rmd_age'] if owner in ['Me', 'Joint'] else ctx['spouse_rmd_age']
                if owner_alive and owner_age >= owner_rmd_age:
                    rmd_amt = a['bal'] / IRS_UNIFORM_TABLE.get(owner_age, 2.0)
                    a['bal'] -= rmd_amt
                    rmd_income += rmd_amt
                    pre_tax_ord += rmd_amt

        if rmd_income > 0:
            annual_inc += rmd_income
            yd["Income: RMDs"] = rmd_income

        primary_ss_entitlement, spouse_ss_entitlement = 0, 0
        for inc in ctx['inc_records']:
            owner = inc.get("Owner", "Me")
            cat_name = inc.get("Category", "Other")
            owner_retire_year = ctx['primary_retire_year'] if owner in ["Me", "Joint"] else ctx['spouse_retire_year']

            raw_start = inc.get('Start Year')
            start_year = int(safe_num(raw_start)) if raw_start and not pd.isna(raw_start) and str(raw_start).strip() != "" else ctx['primary_retire_year']

            end_year = safe_num(inc.get('End Year'), 2100)

            is_active = (year >= start_year) and (not inc.get("Stop at Ret.?", False) or year < owner_retire_year)
            if cat_name in ["Social Security", "Pension"]:
                is_active = (year >= start_year) and (year <= end_year)

            if inc.get("Description"):
                base_amt = safe_num(inc.get('Annual Amount ($)'))

                if cat_name == "Social Security":
                    if owner == "Me":
                        if is_my_alive:
                            offset = max(0, year - int(primary_ss_start_year))
                            primary_ss_entitlement = (base_amt * primary_ss_multi) * ((1 + ctx['infl'] / 100) ** offset)
                            primary_ss_frozen_val = primary_ss_entitlement
                        else:
                            primary_ss_entitlement = primary_ss_frozen_val
                    elif owner == "Spouse":
                        if is_spouse_alive:
                            offset = max(0, year - int(spouse_ss_start_year))
                            spouse_ss_entitlement = (base_amt * spouse_ss_multi) * ((1 + ctx['infl'] / 100) ** offset)
                            spouse_ss_frozen_val = spouse_ss_entitlement
                        else:
                            spouse_ss_entitlement = spouse_ss_frozen_val
                    continue

                if not is_active:
                    continue

                g = safe_num(inc.get('Override Growth (%)'), ctx['inc_g'])
                offset_for_growth = max(0, year - max(int(start_year), ctx['current_year']))
                amt = base_amt * ((1 + g / 100) ** offset_for_growth)

                if cat_name == "Employer Match (401k/HSA)":
                    if (owner == "Me" and is_my_alive) or (owner == "Spouse" and is_spouse_alive) or (
                            owner == "Joint" and (is_my_alive or is_spouse_alive)):
                        match_income_by_owner[owner] += amt
                    continue

                if (owner == "Me" and not is_my_alive) or (owner == "Spouse" and not is_spouse_alive) or (
                        owner == "Joint" and not is_my_alive and not is_spouse_alive):
                    continue

                annual_inc += amt
                pre_tax_ord += amt
                yd[f"Income: {cat_name}"] = yd.get(f"Income: {cat_name}", 0) + amt

                if cat_name in ["Base Salary (W-2)", "Bonus / Commission", "Contractor (1099)"]:
                    if owner in ["Me", "Joint"]:
                        earned_income_me += amt
                    elif owner == "Spouse":
                        earned_income_spouse += amt

        active_ss = 0
        if is_my_alive:
            if ctx['has_spouse'] and is_spouse_alive:
                if year >= primary_ss_start_year:
                    active_ss += primary_ss_entitlement
                    if not ss_started_me:
                        if year not in milestones_by_year: milestones_by_year[year] = []
                        milestones_by_year[year].append({"desc": "📈 Social Security Begins (You)", "amt": primary_ss_entitlement, "type": "system"})
                        ss_started_me = True
                if year >= spouse_ss_start_year:
                    active_ss += spouse_ss_entitlement
                    if not ss_started_spouse:
                        if year not in milestones_by_year: milestones_by_year[year] = []
                        milestones_by_year[year].append({"desc": "📈 Social Security Begins (Spouse)", "amt": spouse_ss_entitlement, "type": "system"})
                        ss_started_spouse = True
            elif ctx['has_spouse'] and not is_spouse_alive:
                if year >= primary_ss_start_year:
                    benefit = max(primary_ss_entitlement, spouse_ss_entitlement)
                    active_ss += benefit
                    if not ss_started_me:
                        desc = "📈 Survivor SS Begins" if spouse_ss_entitlement > primary_ss_entitlement else "📈 Social Security Begins (You)"
                        if year not in milestones_by_year: milestones_by_year[year] = []
                        milestones_by_year[year].append({"desc": desc, "amt": benefit, "type": "system"})
                        ss_started_me = True
            else:
                if year >= primary_ss_start_year:
                    active_ss += primary_ss_entitlement
                    if not ss_started_me:
                        if year not in milestones_by_year: milestones_by_year[year] = []
                        milestones_by_year[year].append({"desc": "📈 Social Security Begins (You)", "amt": primary_ss_entitlement, "type": "system"})
                        ss_started_me = True
        elif is_spouse_alive and not is_my_alive:
            if year >= spouse_ss_start_year:
                benefit = max(primary_ss_entitlement, spouse_ss_entitlement)
                active_ss += benefit
                if not ss_started_spouse:
                    desc = "📈 Survivor SS Begins" if primary_ss_entitlement > spouse_ss_entitlement else "📈 Social Security Begins (Spouse)"
                    if year not in milestones_by_year: milestones_by_year[year] = []
                    milestones_by_year[year].append({"desc": desc, "amt": benefit, "type": "system"})
                    ss_started_spouse = True

        if active_ss > 0:
            yd["Income: Social Security"] = active_ss

            ss_provisional_income = pre_tax_ord + (active_ss * 0.5)
            if active_mfj:
                if ss_provisional_income <= SS_MFJ_TIER1_BASE:
                    taxable_ss = 0
                elif ss_provisional_income <= SS_MFJ_TIER2_BASE:
                    taxable_ss = min(0.5 * active_ss, 0.5 * (ss_provisional_income - SS_MFJ_TIER1_BASE))
                else:
                    taxable_ss = min(0.85 * active_ss, 0.85 * (ss_provisional_income - SS_MFJ_TIER2_BASE) + min(0.5 * active_ss, 6000))
            else:
                if ss_provisional_income <= SS_SINGLE_TIER1_BASE:
                    taxable_ss = 0
                elif ss_provisional_income <= SS_SINGLE_TIER2_BASE:
                    taxable_ss = min(0.5 * active_ss, 0.5 * (ss_provisional_income - SS_SINGLE_TIER1_BASE))
                else:
                    taxable_ss = min(0.85 * active_ss, 0.85 * (ss_provisional_income - SS_SINGLE_TIER2_BASE) + min(0.5 * active_ss, 4500))
            pre_tax_ord += taxable_ss
            annual_inc += active_ss
            annual_ss += active_ss

        cur_biz_val, re_equity, total_exp, biz_income_total = 0, 0, 0, 0
        for b in sim_biz:
            if year_offset > 0:
                b['val'] *= (1 + b['v_growth'] / 100)
                b['dist'] *= (1 + b['d_growth'] / 100)
            cur_biz_val += (b['val'] * b['own'])
            annual_inc += b['dist']
            biz_income_total += b['dist']
            yd["Income: Biz Dist"] = yd.get("Income: Biz Dist", 0) + b['dist']

        def get_qbi(pto):
            if biz_income_total <= 0: return 0
            infl_factor = (1 + ctx['infl'] / 100) ** year_offset
            qbi_threshold = (383900 if active_mfj else 191950) * infl_factor
            qbi_phaseout = (483900 if active_mfj else 241950) * infl_factor
            if pto < qbi_threshold:
                return biz_income_total * 0.20
            elif pto < qbi_phaseout:
                return biz_income_total * 0.20 * ((qbi_phaseout - pto) / (qbi_phaseout - qbi_threshold))
            return 0

        # --- QBI DEDUCTION LOGIC ---
        # Note: We calculate QBI twice. First, based on 'pre_tax_ord' to determine our baseline AGI and 
        # figure out exactly how much Roth conversion room we have. 
        # Later, we calculate 'final_qbi' based on the post-conversion AGI. 
        # This is a critical feature: Roth conversions increase AGI, which can push a user into the QBI 
        # phase-out range, effectively increasing the marginal tax cost of the conversion!
        pre_conversion_qbi = get_qbi(pre_tax_ord)

        for r in sim_re:
            if year_offset > 0:
                r['rent'] *= (1 + r['r_growth'] / 100)
                r['exp'] *= (1 + ctx['infl'] / 100)
                r['val'] *= (1 + r['v_growth'] / 100)

            monthly_rate = r['rate'] / 12
            monthly_pmt = r['pmt'] / 12
            interest_paid, actual_mortgage_paid = 0, 0
            for _ in range(12):
                if r['debt'] > 0:
                    m_int = r['debt'] * monthly_rate
                    interest_paid += m_int
                    actual_mortgage_paid += min(r['debt'] + m_int, monthly_pmt)
                    r['debt'] = max(0, r['debt'] - max(0, monthly_pmt - m_int))
                else:
                    break

            if r['debt'] <= 0 and prev_re_debts.get(r['name'], 0) > 0:
                if year not in milestones_by_year: milestones_by_year[year] = []
                milestones_by_year[year].append({"desc": f"🏡 Mortgage Paid Off: {r['name']}", "amt": 0, "type": "system"})
            prev_re_debts[r['name']] = r['debt']
            re_equity += (r['val'] - r['debt'])

            if r['is_primary']:
                total_exp += (r['exp'] + actual_mortgage_paid)
                yd["Expense: Primary Home (Mortgage & Upkeep)"] = yd.get("Expense: Primary Home (Mortgage & Upkeep)", 0) + (r['exp'] + actual_mortgage_paid)
                if r['rent'] > 0:
                    annual_inc += r['rent']
                    yd["Income: Primary Home Rent"] = yd.get("Income: Primary Home Rent", 0) + r['rent']
            else:
                net_re_cashflow = r['rent'] - (r['exp'] + actual_mortgage_paid)
                if net_re_cashflow > 0:
                    annual_inc += net_re_cashflow
                    yd["Income: Net Investment RE Cashflow"] = yd.get("Income: Net Investment RE Cashflow", 0) + net_re_cashflow
                elif net_re_cashflow < 0:
                    total_exp += abs(net_re_cashflow)
                    yd["Expense: Net Investment RE Loss"] = yd.get("Expense: Net Investment RE Loss", 0) + abs(net_re_cashflow)

            pre_tax_ord += max(0, r['rent'] - r['exp'] - interest_paid)

        user_out_of_pocket_contribs = 0
        pre_tax_deductions = 0
        person_401k_contribs = {'Me': 0, 'Spouse': 0, 'Joint': 0}

        plan_401k_limit = PLAN_401K_LIMIT_BASE * ((1 + ctx['infl'] / 100) ** year_offset)
        catchup_401k = CATCHUP_401K_BASE * ((1 + ctx['infl'] / 100) ** year_offset)
        ira_limit = IRA_LIMIT_BASE * ((1 + ctx['infl'] / 100) ** year_offset)
        catchup_ira = CATCHUP_IRA_BASE * ((1 + ctx['infl'] / 100) ** year_offset)

        for owner, match_left in list(match_income_by_owner.items()):
            if match_left <= 0: continue
            for acct_type_target in ['Traditional 401(k)', 'Traditional 401k/IRA', 'Roth 401(k)', 'Roth 401k/IRA', 'HSA']:
                for a in sim_assets:
                    if a.get('Type') == acct_type_target and a.get('Owner') == owner:
                        a['match_contrib_queue'] = a.get('match_contrib_queue', 0) + match_left
                        match_income_by_owner[owner] = 0
                        break
                if match_income_by_owner[owner] == 0: break

        for owner, match_left in match_income_by_owner.items():
            if match_left > 0:
                found_fallback = False
                for a in sim_assets:
                    if a.get('Owner') == owner and a.get('Type') in ['Brokerage (Taxable)', 'HYSA', 'Checking/Savings']:
                        a['match_contrib_queue'] = a.get('match_contrib_queue', 0) + match_left
                        found_fallback = True
                        break
                if not found_fallback and len(sim_assets) > 0:
                    sim_assets[0]['match_contrib_queue'] = sim_assets[0].get('match_contrib_queue', 0) + match_left
                match_income_by_owner[owner] = 0

        for a in sim_assets:
            o_acct = a.get('Owner', 'Me')
            o_birth = ctx['my_birth_year'] if o_acct in ['Me', 'Joint'] else ctx['spouse_birth_year']
            o_ret = ctx['primary_retire_year'] if o_acct in ['Me', 'Joint'] else ctx['spouse_retire_year']
            o_alive = is_my_alive if o_acct in ['Me', 'Joint'] else is_spouse_alive
            added_this_year = 0

            if o_alive and not (a.get('stop_at_ret', True) and year >= o_ret):
                added_this_year = a['contrib']
                if a.get('Type') in ['Traditional 401(k)', 'Traditional 401k/IRA', 'Roth 401(k)', 'Roth 401k/IRA']:
                    limit = plan_401k_limit + (catchup_401k if (year - o_birth) >= 50 else 0)
                    added_this_year = min(added_this_year, max(0, limit - person_401k_contribs[o_acct]))
                    person_401k_contribs[o_acct] += added_this_year
                elif a.get('Type') in ['Traditional IRA', 'Roth IRA']:
                    limit = ira_limit + (catchup_ira if (year - o_birth) >= 50 else 0)
                    added_this_year = min(added_this_year, limit)
                user_out_of_pocket_contribs += added_this_year

                if a.get('Type') in ['Traditional 401(k)', 'Traditional 401k/IRA', 'Traditional IRA', 'HSA']:
                    pre_tax_deductions += added_this_year

            a['approved_oop_contrib'] = added_this_year

        tax_base_ord_pre = max(0, pre_tax_ord - pre_conversion_qbi - pre_tax_deductions)

        # --- LOGGING BRACKETS FOR UI ---
        infl_factor_tax = (1 + ctx['infl'] / 100) ** year_offset
        std_deduction_ui = (29200 if active_mfj else 14600) * infl_factor_tax
        yd["Tax: 0% Limit (Std Ded)"] = std_deduction_ui
        b_lims_ui = {
            "12%": 94300 if active_mfj else 47150,
            "22%": 201050 if active_mfj else 100525,
            "24%": 383900 if active_mfj else 191950,
            "32%": 487450 if active_mfj else 243725
        }
        yd["Tax: 12% Limit"] = b_lims_ui["12%"] * infl_factor_tax + std_deduction_ui
        yd["Tax: 22% Limit"] = b_lims_ui["22%"] * infl_factor_tax + std_deduction_ui
        yd["Tax: 24% Limit"] = b_lims_ui["24%"] * infl_factor_tax + std_deduction_ui
        yd["Tax: 32% Limit"] = b_lims_ui["32%"] * infl_factor_tax + std_deduction_ui
        yd["Tax: Base Ordinary Income"] = tax_base_ord_pre

        for ev in ctx['exp_records']:
            desc = str(ev.get("Description", "")).strip()
            if not desc: continue

            cat = ev.get("Category", "Other")
            if ctx['owns_home'] and cat in ["Housing / Rent", "Debt Payments"]: continue
            if not ctx['owns_home'] and cat == "Debt Payments": continue

            freq = ev.get("Frequency", "Monthly")
            amt = safe_num(ev.get("Amount ($)", 0)) * (12 if freq == "Monthly" else 1)

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

                if ctx['has_spouse'] and not (is_my_alive and is_spouse_alive) and freq != "One-Time" and cat in [
                    "Food", "Utilities", "Transportation"]:
                    survivor_retired = (not is_my_alive and year >= ctx['spouse_retire_year']) or (
                                not is_spouse_alive and year >= ctx['primary_retire_year'])
                    if survivor_retired:
                        inflated_amt *= WIDOW_EXPENSE_MULTIPLIER

                primary_on_medicare = is_my_alive and my_current_age >= 65
                spouse_on_medicare = ctx['has_spouse'] and is_spouse_alive and spouse_current_age >= 65

                if ctx['medicare_cliff'] and cat == "Healthcare":
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

                if any(k in desc.lower() for k in ['college', 'tuition', 'university', 'education', 'school']):
                    amount_to_cover = inflated_amt
                    covered_by_529 = 0
                    target_kid = next(
                        (k['name'].lower() for k in ctx['kids_data'] if k['name'].lower() in desc.lower()), None)

                    if target_kid:
                        for a in sim_assets:
                            if a.get('Type', '').strip() == '529 Plan' and a['bal'] > 0 and re.search(
                                    rf'\b{re.escape(target_kid)}\b', str(a.get('Account Name', '')).lower()):
                                pull = min(a['bal'], amount_to_cover)
                                a['bal'] -= pull
                                amount_to_cover -= pull
                                covered_by_529 += pull
                                if amount_to_cover <= 0: break

                    if amount_to_cover > 0:
                        for a in sim_assets:
                            if a.get('Type', '').strip() == '529 Plan' and a['bal'] > 0:
                                pull = min(a['bal'], amount_to_cover)
                                a['bal'] -= pull
                                amount_to_cover -= pull
                                covered_by_529 += pull
                                if amount_to_cover <= 0: break

                    if covered_by_529 > 0:
                        annual_inc += covered_by_529
                        yd[f"Income: Tax-Free 529 Withdrawal ({desc})"] = covered_by_529

        if ctx['medicare_gap'] and is_retired and my_current_age < 65:
            income_factor = min(1.0, max(0.0, pre_tax_ord / 100000.0))
            gap_cost = (3000 + (MEDICARE_GAP_COST - 3000) * income_factor) * ((1 + ctx['infl_hc'] / 100) ** year_offset)
            total_exp += gap_cost
            yd["Expense: Healthcare (Pre-Medicare Gap Proxy)"] = gap_cost

        if ctx['ltc_shock']:
            if is_my_alive and my_current_age >= (ctx['my_life_exp_val'] - 2):
                ltc_cost = LTC_SHOCK_COST * ((1 + ctx['infl_hc'] / 100) ** year_offset)
                total_exp += ltc_cost
                yd["Expense: Long Term Care Shock (Primary)"] = ltc_cost
            if ctx['has_spouse'] and is_spouse_alive and spouse_current_age >= (ctx['spouse_life_exp_val'] - 2):
                ltc_cost_spouse = LTC_SHOCK_COST * ((1 + ctx['infl_hc'] / 100) ** year_offset)
                total_exp += ltc_cost_spouse
                yd["Expense: Long Term Care Shock (Spouse)"] = ltc_cost_spouse

        debt_bal_total = 0
        for d in sim_debts:
            actual_paid = 0
            for _ in range(12):
                if d['bal'] > 0:
                    m_int = d['bal'] * (d['rate'] / 12)
                    actual_paid += min(d['bal'] + m_int, d['pmt'] / 12)
                    d['bal'] = max(0, d['bal'] - max(0, (d['pmt'] / 12) - m_int))
                else:
                    break

            total_exp += actual_paid
            yd["Expense: Debt Payments"] = yd.get("Expense: Debt Payments", 0) + actual_paid

            if d['bal'] <= 0 and prev_debt_bals.get(d['name'], 0) > 0:
                if year not in milestones_by_year: milestones_by_year[year] = []
                milestones_by_year[year].append({"desc": f"🎉 Debt Paid Off: {d['name']}", "amt": 0, "type": "system"})
            prev_debt_bals[d['name']] = d['bal']
            debt_bal_total += d['bal']

        base_fed_tax_pre_conversion, marginal_rate_pre_conversion = calc_federal_tax(tax_base_ord_pre, active_mfj, year_offset, ctx['infl'])
        state_tax_rate = ctx['cur_t'] if not is_retired else ctx['ret_t']
        base_state_tax_pre_conversion = tax_base_ord_pre * (state_tax_rate / 100.0)

        total_converted = 0
        if ctx['roth_conversions'] and is_retired:
            infl_factor = (1 + ctx['infl'] / 100) ** year_offset
            std_deduction = (29200 if active_mfj else 14600) * infl_factor
            b_limits = {"12%": 94300, "22%": 201050, "24%": 383900, "32%": 487450} if active_mfj else {"12%": 47150, "22%": 100525, "24%": 191950, "32%": 243725}
            target_limit = b_limits.get(ctx['roth_target'], 383900) * infl_factor + std_deduction

            conversion_room = max(0, target_limit - tax_base_ord_pre)
            available_cash = sum(a['bal'] for a in sim_assets if a.get('Type') in ['Checking/Savings', 'HYSA', 'Brokerage (Taxable)', 'Unallocated Cash'])
            locked_outflows = total_exp + user_out_of_pocket_contribs + base_fed_tax_pre_conversion + base_state_tax_pre_conversion
            safe_liquid_cash = max(0, available_cash - locked_outflows)

            est_tax_rate = marginal_rate_pre_conversion + (state_tax_rate / 100.0)
            max_tax_budget = safe_liquid_cash * ROTH_CASH_BUFFER_MARGIN
            max_conversion_by_cash = max_tax_budget / max(0.10, est_tax_rate)
            conversion_room = min(conversion_room, max_conversion_by_cash)

            if conversion_room > 0:
                for a in sim_assets:
                    if a.get('Type') in ['Traditional 401(k)', 'Traditional 401k/IRA', 'Traditional IRA'] and a['bal'] > 0:
                        convert = min(a['bal'], conversion_room - total_converted)
                        if convert > 0:
                            a['bal'] -= convert
                            total_converted += convert

                            # --- FIX: EXACT TAX DELTA CALCULATION ---
                            base_fed, _ = calc_federal_tax(tax_base_ord_pre, active_mfj, year_offset, ctx['infl'])
                            base_state = tax_base_ord_pre * (state_tax_rate / 100.0)
                            
                            proposed_tax_base = tax_base_ord_pre + convert
                            prop_fed, _ = calc_federal_tax(proposed_tax_base, active_mfj, year_offset, ctx['infl'])
                            prop_state = proposed_tax_base * (state_tax_rate / 100.0)
                            
                            tax_cost = (prop_fed + prop_state) - (base_fed + base_state)
                            
                            # --- FIX: Track Roth taxes, but wait to log them to prevent double-counting ---
                            roth_fed_tax_paid += (prop_fed - base_fed)
                            roth_state_tax_paid += (prop_state - base_state)
                            
                            yd["Tax Breakdown: Federal"] = yd.get("Tax Breakdown: Federal", 0) + (prop_fed - base_fed)
                            yd["Tax Breakdown: State"] = yd.get("Tax Breakdown: State", 0) + (prop_state - base_state)

                            for ca in sim_assets:
                                if tax_cost <= 0: break
                                if ca.get('Type') in ['Checking/Savings', 'HYSA', 'Unallocated Cash']:
                                    pull = min(ca['bal'], tax_cost)
                                    ca['bal'] -= pull
                                    tax_cost -= pull

                            for ca in sim_assets:
                                if tax_cost <= 0: break
                                if ca.get('Type') == 'Brokerage (Taxable)':
                                    req_gross = tax_cost / 0.85
                                    pull = min(ca['bal'], req_gross)
                                    ca['bal'] -= pull
                                    net_yield = pull * 0.85
                                    tax_cost -= net_yield
                                    yd["Expense: Taxes"] = yd.get("Expense: Taxes", 0) + (pull - net_yield)
                                    yd["Tax Breakdown: Withdrawals"] = yd.get("Tax Breakdown: Withdrawals", 0) + (pull - net_yield)

                            roth_found = False
                            for ra in sim_assets:
                                if ra.get('Type') in ['Roth 401(k)', 'Roth 401k/IRA', 'Roth IRA'] and ra.get('Owner') == a.get('Owner'):
                                    ra['bal'] += convert
                                    roth_found = True
                                    break
                            if not roth_found:
                                sim_assets.append(
                                    {"Account Name": f"Converted Roth ({a.get('Owner')})", "Type": "Roth IRA",
                                     "Owner": a.get("Owner", "Me"), "bal": convert, "contrib": 0.0,
                                     "growth": a.get('growth'), "stop_at_ret": True})
                            if total_converted >= conversion_room:
                                break

            if total_converted > 0:
                pre_tax_ord += total_converted
                yd["Roth Conversion Amount"] = total_converted

        final_qbi = get_qbi(pre_tax_ord)
        tax_base_ord = max(0, pre_tax_ord - final_qbi - pre_tax_deductions)

        # --- FIX: Smart Asset-Class Volatility Routing (Cash Crash Immunity) ---
        for a in sim_assets:
            # 1. Pop the deferred contributions we calculated earlier in the year
            add = a.pop('approved_oop_contrib', 0)
            match = a.pop('match_contrib_queue', 0)
            
            # 2. Identify if this is a cash-equivalent account
            is_cash_equiv = any(kw in str(a.get('Type', '')).upper() for kw in ['HYSA', 'CASH', 'CD', 'SAVINGS', 'CHECKING', 'MONEY MARKET']) or \
                            any(kw in str(a.get('Account Name', '')).upper() for kw in ['EMERGENCY', 'CASH', 'SAVINGS'])

            # 3. Determine the baseline expected growth of this specific asset
            asset_baseline_g = float(a.get('growth', ctx.get('mkt', 7.0))) if pd.notna(a.get('growth')) and str(a.get('growth')).strip() not in ["", "None"] else ctx.get('mkt', 7.0)

            # 4. Apply the correct volatility and glidepath logic
            if is_cash_equiv:
                # Cash ignores the Monte Carlo market sequence and yields its fixed rate
                actual_growth = asset_baseline_g
            else:
                # Investments take the Monte Carlo sequence, adjusted by their custom premium/discount relative to the global baseline
                # Example: If global baseline is 7%, but this asset is bonds at 5% (a -2% discount)
                # If the Monte Carlo sequence gives us -10% this year, the bonds safely experience -12%
                mc_sequence_year = mkt_sequence[year_offset]
                actual_growth = mc_sequence_year + (asset_baseline_g - ctx.get('mkt', 7.0))
                
                # Apply Glidepath (if enabled) to de-risk investments in retirement
                if ctx.get('glidepath', True) and is_retired:
                    years_in_ret = year - ctx['primary_retire_year']
                    glide_reduction = min(3.0, years_in_ret * 0.2)
                    actual_growth -= glide_reduction

            # 5. Apply the compounding math (Mid-Year Convention for contributions)
            a['bal'] = (a['bal'] + (add + match) * 0.5) * (1 + actual_growth / 100.0) + (add + match) * 0.5

        base_fed_tax, marginal_rate = calc_federal_tax(tax_base_ord, active_mfj, year_offset, ctx['infl'])
        state_tax = tax_base_ord * (state_tax_rate / 100.0)
        fica_tax = 0
        wage_base = SS_WAGE_BASE_2026 * ((1 + ctx['infl'] / 100) ** year_offset)
        
        # IRS thresholds: $250k for MFJ, $200k for Single (historically unindexed, but we index here to prevent extreme long-term bracket creep)
        addl_thresh_base = 250000 if active_mfj else 200000
        addl_med_thresh = addl_thresh_base * ((1 + ctx['infl'] / 100) ** year_offset)
        
        combined_earned_income = 0
        for ei in [earned_income_me, earned_income_spouse]:
            if ei > 0: 
                # Standard SS (6.2% up to wage base) + Standard Medicare (1.45% infinite)
                fica_tax += min(ei, wage_base) * 0.062 + ei * 0.0145
                combined_earned_income += ei

        # --- FIX: Additional Medicare Tax (0.9%) applies to COMBINED household earned income for MFJ ---
        if combined_earned_income > addl_med_thresh:
            fica_tax += (combined_earned_income - addl_med_thresh) * 0.009

        total_tax = base_fed_tax + state_tax + fica_tax
        yd["Expense: Taxes"] = yd.get("Expense: Taxes", 0) + total_tax
        
        # --- FIX: Explicitly separate baseline taxes from Roth Conversion taxes ---
        yd["Tax Breakdown: Federal"] = yd.get("Tax Breakdown: Federal", 0) + max(0, base_fed_tax - roth_fed_tax_paid)
        yd["Tax Breakdown: State"] = yd.get("Tax Breakdown: State", 0) + max(0, state_tax - roth_state_tax_paid)
        yd["Tax Breakdown: Roth Conversion"] = yd.get("Tax Breakdown: Roth Conversion", 0) + (roth_fed_tax_paid + roth_state_tax_paid)
        yd["Tax Breakdown: FICA"] = yd.get("Tax Breakdown: FICA", 0) + fica_tax

        num_medicare = (1 if is_my_alive and my_current_age >= 65 else 0) + (
            1 if is_spouse_alive and spouse_current_age >= 65 else 0)
        if num_medicare > 0:
            magi_for_irmaa = pre_tax_ord
            infl_f = (1 + ctx['infl'] / 100) ** year_offset
            t1, t2, t3, t4, t5 = 103000 * infl_f * (2 if active_mfj else 1), 129000 * infl_f * (2 if active_mfj else 1), 161000 * infl_f * (2 if active_mfj else 1), 193000 * infl_f * (2 if active_mfj else 1), 500000 * infl_f * (1.5 if active_mfj else 1)
            surcharge = 0
            if magi_for_irmaa > t5:
                surcharge = 6500 * infl_f
            elif magi_for_irmaa > t4:
                surcharge = 5500 * infl_f
            elif magi_for_irmaa > t3:
                surcharge = 4000 * infl_f
            elif magi_for_irmaa > t2:
                surcharge = 2500 * infl_f
            elif magi_for_irmaa > t1:
                surcharge = 1000 * infl_f
            if surcharge > 0:
                total_irmaa = surcharge * num_medicare
                total_exp += total_irmaa
                yd["Expense: Medicare IRMAA Surcharge"] = total_irmaa
                if not irmaa_triggered:
                    if year not in milestones_by_year: milestones_by_year[year] = []
                    milestones_by_year[year].append({"desc": "📉 Medicare IRMAA Surcharge Triggered", "amt": total_irmaa, "type": "system"})
                    irmaa_triggered = True
                if total_irmaa > last_irmaa_tier + 500:
                    if year not in milestones_by_year: milestones_by_year[year] = []
                    milestones_by_year[year].append({"desc": "📉 Medicare IRMAA Surcharge Tier Jumped", "amt": total_irmaa, "type": "system"})
                    last_irmaa_tier = total_irmaa

        if user_out_of_pocket_contribs > 0:
            yd["Expense: Portfolio Contributions"] = user_out_of_pocket_contribs

        organic_cash_outflows = total_exp + user_out_of_pocket_contribs + yd["Expense: Taxes"]
        organic_net_cash_flow = annual_inc - organic_cash_outflows
        yd["Organic Net Savings"] = organic_net_cash_flow

        total_withdrawals = 0

        if organic_net_cash_flow > 0:
            yd["Expense: Surplus Reinvested"] = organic_net_cash_flow
            if unfunded_debt_bal > 0:
                payoff = min(organic_net_cash_flow, unfunded_debt_bal)
                unfunded_debt_bal -= payoff
                organic_net_cash_flow -= payoff
            if organic_net_cash_flow > 0 and sim_assets:
                taxable_acct = next((a for a in sim_assets if a.get('Type') == 'Brokerage (Taxable)'), None)
                if not taxable_acct:
                    taxable_acct = next((a for a in sim_assets if a.get('Type') in ['Checking/Savings', 'HYSA', 'Unallocated Cash']), None)

                if taxable_acct:
                    taxable_acct['bal'] += organic_net_cash_flow
                else:
                    new_cash_acct = {"Account Name": "Unallocated Cash", "Type": "Checking/Savings", "Owner": "Me",
                                     "bal": organic_net_cash_flow, "contrib": 0.0, "growth": 0.0, "stop_at_ret": False}
                    sim_assets.append(new_cash_acct)

        elif organic_net_cash_flow < 0:
            shortfall = abs(organic_net_cash_flow)

            for a in sim_assets:
                if shortfall <= 0: break
                if a.get('Type') in ['Checking/Savings', 'HYSA', 'Unallocated Cash']:
                    shortfall, t_inc, wd = _withdraw(a, shortfall, 'free', ctx, my_current_age, spouse_current_age,
                                                     active_mfj, year_offset, tax_base_ord, marginal_rate, state_tax_rate, year, yd)
                    total_withdrawals += wd

            if shortfall > 0 and not cash_depleted and not any(a['bal'] > 0 for a in sim_assets if a.get('Type') in ['Checking/Savings', 'HYSA', 'Unallocated Cash']):
                if year not in milestones_by_year: milestones_by_year[year] = []
                milestones_by_year[year].append({"desc": "⚠️ Liquid Cash Depleted. Withdrawing from investments.", "amt": 0, "type": "system"})
                cash_depleted = True

            for a in sim_assets:
                if shortfall <= 0: break
                if a.get('Type') == 'Brokerage (Taxable)':
                    shortfall, t_inc, wd = _withdraw(a, shortfall, 'cg', ctx, my_current_age, spouse_current_age,
                                                     active_mfj, year_offset, tax_base_ord, marginal_rate, state_tax_rate, year, yd)
                    yd["Expense: Taxes"] = yd.get("Expense: Taxes", 0) + t_inc
                    yd["Tax Breakdown: Withdrawals"] = yd.get("Tax Breakdown: Withdrawals", 0) + t_inc
                    total_withdrawals += wd

            trad_types = ['Traditional 401(k)', 'Traditional 401k/IRA', 'Traditional IRA']
            roth_types = ['Roth 401(k)', 'Roth 401k/IRA', 'Roth IRA']
            tax_free_types = roth_types + ['HSA', 'Crypto', '529 Plan', 'Other']

            seq = trad_types + tax_free_types if 'Standard' in ctx['active_withdrawal_strategy'] else tax_free_types + trad_types

            for t in seq:
                if shortfall <= 0: break
                for a in sim_assets:
                    if a.get('Type') == t:
                        is_trad = t in trad_types
                        if is_trad and not tapped_trad:
                            if year not in milestones_by_year: milestones_by_year[year] = []
                            milestones_by_year[year].append({"desc": "📉 Began Drawing from Traditional 401(k)/IRA", "amt": 0, "type": "system"})
                            tapped_trad = True
                        elif t in roth_types and not tapped_roth:
                            if year not in milestones_by_year: milestones_by_year[year] = []
                            milestones_by_year[year].append({"desc": "📉 Began Drawing from Roth/Tax-Free Assets", "amt": 0, "type": "system"})
                            tapped_roth = True

                        shortfall, t_inc, wd = _withdraw(a, shortfall, 'ordinary' if is_trad else 'free', ctx,
                                                         my_current_age, spouse_current_age, active_mfj, year_offset,
                                                         tax_base_ord, marginal_rate, state_tax_rate, year, yd)
                        yd["Expense: Taxes"] = yd.get("Expense: Taxes", 0) + t_inc
                        yd["Tax Breakdown: Withdrawals"] = yd.get("Tax Breakdown: Withdrawals", 0) + t_inc
                        total_withdrawals += wd

            if shortfall > 0:
                for a in sim_assets:
                    if shortfall <= 0: break
                    if a['bal'] > 0 and a.get('Type') not in ['Checking/Savings', 'HYSA', 'Unallocated Cash', 'Brokerage (Taxable)'] + trad_types + tax_free_types:
                        shortfall, t_inc, wd = _withdraw(a, shortfall, 'ordinary', ctx, my_current_age,
                                                         spouse_current_age, active_mfj, year_offset, tax_base_ord,
                                                         marginal_rate, state_tax_rate, year, yd)
                        yd["Expense: Taxes"] = yd.get("Expense: Taxes", 0) + t_inc
                        yd["Tax Breakdown: Withdrawals"] = yd.get("Tax Breakdown: Withdrawals", 0) + t_inc
                        total_withdrawals += wd

            if shortfall > 0:
                unfunded_debt_bal += shortfall
                yd["Income: Shortfall Debt Funded"] = shortfall

        if unfunded_debt_bal > 0 and prev_unfunded_debt_bal <= 0:
            if year not in milestones_by_year: milestones_by_year[year] = []
            milestones_by_year[year].append({"desc": "🚨 MAJOR SHORTFALL: Retirement Accounts Depleted!", "amt": unfunded_debt_bal, "type": "critical"})

        liquid_assets_total = sum(max(0, a['bal']) for a in sim_assets)
        for a in sim_assets: nw_yd[f"Asset: {a.get('Account Name', 'Account')}"] = max(0, a['bal'])

        net_worth = liquid_assets_total + re_equity + cur_biz_val - debt_bal_total - unfunded_debt_bal
        nw_yd.update({"Total Liquid Assets": liquid_assets_total, "Total Real Estate Equity": re_equity,
                      "Total Business Equity": cur_biz_val, "Total Debt Liabilities": -(debt_bal_total + unfunded_debt_bal), "Total Net Worth": net_worth})

        for a in sim_assets:
            if a['bal'] <= 0 and prev_ast_bals.get(a['Account Name'], 0) > 0 and a.get('Type') == '529 Plan':
                if year not in milestones_by_year: milestones_by_year[year] = []
                milestones_by_year[year].append({"desc": f"🎓 529 Plan Depleted: {a['Account Name']}", "amt": 0, "type": "system"})
            prev_ast_bals[a['Account Name']] = a['bal']

        yd["Net Savings"] = (annual_inc + total_withdrawals + yd.get("Income: Shortfall Debt Funded", 0)) - (
                    total_exp + user_out_of_pocket_contribs + yd.get("Expense: Taxes", 0) + yd.get("Expense: Surplus Reinvested", 0))

        sim_res.append({"Year": year, "Age (Primary)": my_current_age, "Age (Spouse)": spouse_current_age,
                        "Annual Income": annual_inc, "Asset Withdrawals": total_withdrawals,
                        "Annual Expenses": total_exp, "Annual Taxes": yd.get("Expense: Taxes", 0),
                        "Annual Net Savings": yd.get("Organic Net Savings", 0), "Liquid Assets": liquid_assets_total,
                        "Real Estate Equity": re_equity, "Business Equity": cur_biz_val, "Debt": -debt_bal_total,
                        "Unfunded Debt": unfunded_debt_bal, "Net Worth": net_worth})
        det_res.append(yd)
        nw_det_res.append(nw_yd)

        if unfunded_debt_bal > 0:
            unfunded_debt_bal *= (1 + ctx['shortfall_rate'])

    return sim_res, det_res, nw_det_res, milestones_by_year

@st.cache_data(show_spinner=False)
def execute_sim_engine_v8(mkt_sequence_tuple, ctx_json):
    # Deserialize the string back into a dict for the engine
    ctx = json.loads(ctx_json)
    s_res, d_res, nw_res, milestones = run_simulation(list(mkt_sequence_tuple), ctx)
    return pd.DataFrame(s_res), pd.DataFrame(d_res).fillna(0), pd.DataFrame(nw_res).fillna(0), milestones

# =====================================================================
# 3. UI RENDERING PAGES
# =====================================================================

def render_dashboard():
    status = get_completion_status()
    if status['score'] < 100:
        st.markdown(
            "<div style='background: linear-gradient(135deg, #4f46e5 0%, #0ea5e9 100%); border-radius: 16px; padding: 32px; text-align: center; box-shadow: 0 4px 12px rgba(79, 70, 229, 0.2); margin-top: 24px; margin-bottom: 32px; color: white;'><h2 style='margin-top:0; color: white !important; font-weight: 800; font-size: 2rem;'>👋 Welcome to your Financial Blueprint</h2><p style='font-size: 1.1rem; opacity: 0.95; margin-bottom: 0; max-width: 600px; margin-left: auto; margin-right: auto;'>Complete the four foundational steps below to unlock your high-precision Monte Carlo simulation and AI Fiduciary analysis.</p></div>",
            unsafe_allow_html=True)

        c1, c2, c3, c4 = st.columns(4)

        with c1:
            st.markdown(
                f"<div class='card' style='text-align:center; padding:24px 16px; margin-bottom: 12px; border-color: {'#10b981' if status['profile'] else '#e2e8f0'};'><div style='font-size:2.5rem; margin-bottom:12px;'>👤</div><h4 style='margin:0 0 4px 0; font-size:1.1rem;'>1. Basic Info</h4><p style='font-size:0.85rem; color:#64748b; margin:0;'>Age & timeline.</p></div>",
                unsafe_allow_html=True)
            if not status['profile']:
                if st.button("Start Step", key="ob_prof", type="primary", use_container_width=True):
                    st.session_state['current_page'] = "👤 Profile & Family"
                    st.rerun()
            else:
                st.button("✅ Completed", key="ob_prof_done", disabled=True, use_container_width=True)

        with c2:
            st.markdown(
                f"<div class='card' style='text-align:center; padding:24px 16px; margin-bottom: 12px; border-color: {'#10b981' if status['income'] else '#e2e8f0'};'><div style='font-size:2.5rem; margin-bottom:12px;'>💵</div><h4 style='margin:0 0 4px 0; font-size:1.1rem;'>2. Income</h4><p style='font-size:0.85rem; color:#64748b; margin:0;'>Salaries & SS.</p></div>",
                unsafe_allow_html=True)
            if not status['income']:
                if st.button("Add Income", key="ob_inc", type="primary", use_container_width=True):
                    st.session_state['current_page'] = "💵 Income Streams"
                    st.rerun()
            else:
                st.button("✅ Completed", key="ob_inc_done", disabled=True, use_container_width=True)

        with c3:
            st.markdown(
                f"<div class='card' style='text-align:center; padding:24px 16px; margin-bottom: 12px; border-color: {'#10b981' if status['assets'] else '#e2e8f0'};'><div style='font-size:2.5rem; margin-bottom:12px;'>🏦</div><h4 style='margin:0 0 4px 0; font-size:1.1rem;'>3. Assets</h4><p style='font-size:0.85rem; color:#64748b; margin:0;'>Real estate & 401(k).</p></div>",
                unsafe_allow_html=True)
            if not status['assets']:
                if st.button("Add Assets", key="ob_ast", type="primary", use_container_width=True):
                    st.session_state['current_page'] = "🏦 Assets & Debts"
                    st.rerun()
            else:
                st.button("✅ Completed", key="ob_ast_done", disabled=True, use_container_width=True)

        with c4:
            st.markdown(
                f"<div class='card' style='text-align:center; padding:24px 16px; margin-bottom: 12px; border-color: {'#10b981' if status['expenses'] else '#e2e8f0'};'><div style='font-size:2.5rem; margin-bottom:12px;'>💸</div><h4 style='margin:0 0 4px 0; font-size:1.1rem;'>4. Cash Flows</h4><p style='font-size:0.85rem; color:#64748b; margin:0;'>Budgets & goals.</p></div>",
                unsafe_allow_html=True)
            if not status['expenses']:
                if st.button("Add Expenses", key="ob_exp", type="primary", use_container_width=True):
                    st.session_state['current_page'] = "💸 Cash Flows"
                    st.rerun()
            else:
                st.button("✅ Completed", key="ob_exp_done", disabled=True, use_container_width=True)
        st.divider()

    section_header("Executive Summary", "Your complete financial trajectory at a glance.", "🏠")

    sim_ctx = build_sim_context()
    if sim_ctx['my_age'] <= 0:
        render_empty_state("Profile", "👤")
        info_banner("Please complete your Profile (specifically your Date of Birth) to unlock the simulation.",
                    "warning")
        return

    if sim_ctx['max_years'] <= 0:
        st.warning("Your Life Expectancy must be greater than your Current Age to run the simulation.")
        return

    with st.spinner("Running high-precision simulation engine..."):
        mkt_seq = tuple([sim_ctx['mkt']] * (sim_ctx['max_years'] + 1))
        
        # --- FIX: Serialize sim_ctx to JSON to match the new high-speed cache wrapper ---
        df_sim_nominal, df_det_nominal, df_nw_nominal, run_milestones = execute_sim_engine_v8(mkt_seq, json.dumps(sim_ctx))
        
        st.session_state['df_sim_nominal'] = df_sim_nominal
        st.session_state['df_det'] = df_det_nominal
        st.session_state['df_nw'] = df_nw_nominal

    if df_sim_nominal.empty:
        st.error("Simulation returned no data. Please check your profile and start dates.")
        return

    df_sim = df_sim_nominal.copy()
    if st.session_state.get('view_todays_dollars', True):
        discounts = (1 + sim_ctx['infl'] / 100) ** (df_sim['Year'] - sim_ctx['current_year'])
        cols_sim = ["Annual Income", "Asset Withdrawals", "Annual Expenses", "Annual Taxes", "Annual Net Savings",
                    "Liquid Assets", "Real Estate Equity", "Business Equity", "Debt", "Unfunded Debt", "Net Worth"]
        df_sim[cols_sim] = df_sim[cols_sim].div(discounts, axis=0)

    st.session_state['df_sim_display'] = df_sim

    final_nw = df_sim.iloc[-1]['Net Worth']
    current_nw = df_sim.iloc[0]['Net Worth']
    ret_nw_rows = df_sim[df_sim['Year'] == sim_ctx['primary_retire_year']]
    ret_nw = ret_nw_rows['Net Worth'].values[0] if not ret_nw_rows.empty else current_nw

    shortfall_mask = df_sim['Unfunded Debt'] > 0
    deplete_year = df_sim[shortfall_mask]['Year'].min() if not df_sim[shortfall_mask].empty else None
    deplete_age = df_sim[shortfall_mask]['Age (Primary)'].min() if not df_sim[shortfall_mask].empty else None

    mc_success = st.session_state.get('mc_success_rate')
    
    # Scale the $2M target up by inflation if we are looking at nominal future dollars
    target_threshold = 2000000
    if not st.session_state.get('view_todays_dollars', True):
        target_threshold = 2000000 * ((1 + sim_ctx['infl'] / 100) ** sim_ctx['max_years'])
        
    render_status_bar(deplete_year, deplete_age, final_nw, mc_success_rate=mc_success, success_threshold=target_threshold)

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        stat_card("Years to Retirement", max(0, sim_ctx['primary_retire_year'] - sim_ctx['current_year']),
                  color="indigo", icon="⏳")
    with col2:
        stat_card("Current Net Worth", f"${current_nw:,.0f}", color="emerald", icon="💵")
    with col3:
        stat_card("Retirement Net Worth", f"${ret_nw:,.0f}", color="amber", icon="🚀")
    with col4:
        stat_card("End of Plan Net Worth", f"${final_nw:,.0f}", color="rose", icon="🏁")

    st.divider()

    c_sank1, c_sank2 = st.columns([3, 1])
    with c_sank1:
        st.write("#### 🌊 Cash Flow Visualizer")
    with c_sank2:
        sankey_year = st.slider("Select Year", min_value=int(df_det_nominal['Year'].min()),
                                max_value=int(df_det_nominal['Year'].max()), value=int(df_det_nominal['Year'].min()),
                                label_visibility="collapsed")

    row = df_det_nominal[df_det_nominal['Year'] == sankey_year].iloc[0].copy()

    if st.session_state.get('view_todays_dollars', True):
        discount = (1 + sim_ctx['infl'] / 100) ** (row['Year'] - sim_ctx['current_year'])
        for k in row.keys():
            if isinstance(row[k], (int, float)) and k not in ["Age", "Age (Primary)", "Age (Spouse)", "Year"]:
                row[k] /= discount

    inflows = {k.replace('Income: ', ''): v for k, v in row.items() if k.startswith('Income:') and v > 0}
    outflows = {k.replace('Expense: ', ''): v for k, v in row.items() if k.startswith('Expense:') and v > 0}

    in_labels = [f"{html.escape(k)}<br>${v:,.0f}" for k, v in inflows.items()]
    out_labels = [f"{html.escape(k)}<br>${v:,.0f}" for k, v in outflows.items()]
    total_inflow = sum(inflows.values())
    mid_label = f"Total Budget Pool<br>${total_inflow:,.0f}"

    labels = in_labels + [mid_label] + out_labels
    middle_idx = len(inflows)
    source, target, value, node_colors, link_colors = [], [], [], [], []

    for i, (k, v) in enumerate(inflows.items()):
        source.append(i);
        target.append(middle_idx);
        value.append(v)
        node_colors.append('#f43f5e' if 'Shortfall Debt' in k or 'Withdrawal' in k else '#10b981')
        link_colors.append(
            'rgba(244, 63, 94, 0.4)' if 'Shortfall Debt' in k or 'Withdrawal' in k else 'rgba(16, 185, 129, 0.4)')

    node_colors.append('#3b82f6')

    for i, (k, v) in enumerate(outflows.items()):
        source.append(middle_idx);
        target.append(middle_idx + 1 + i);
        value.append(v)
        node_colors.append('#10b981' if k in ['Portfolio Contributions', 'Surplus Reinvested'] else '#f43f5e')
        link_colors.append('rgba(16, 185, 129, 0.4)' if k in ['Portfolio Contributions',
                                                              'Surplus Reinvested'] else 'rgba(244, 63, 94, 0.4)')

    if total_inflow > 0 and HAS_PLOTLY:
        fig_sankey = go.Figure(data=[go.Sankey(arrangement="snap",
                                               node=dict(pad=35, thickness=30, line=dict(color="black", width=0.5),
                                                         label=labels, color=node_colors),
                                               textfont=dict(color="black", size=12),
                                               link=dict(source=source, target=target, value=value,
                                                         color=link_colors))])
        fig_sankey.update_layout(height=900, margin=dict(l=0, r=0, t=30, b=50), font=dict(size=12))
        st.plotly_chart(fig_sankey, use_container_width=True)


def render_profile():
    section_header("Profile & Family Context",
                   "Precision matters. Your exact birth year dictates RMDs, SS scaling, and IRS Catch-Up limits.",
                   "👨‍👩‍👧‍👦")

    c1, c2 = st.columns(2)
    my_name = c1.text_input("Your Name", value=st.session_state.get('my_name', ''), on_change=mark_dirty,
                            key="input_my_name")
    my_dob = c2.date_input("Your Date of Birth", value=st.session_state.get('my_dob', datetime.date(1980, 1, 1)),
                           min_value=datetime.date(1920, 1, 1), max_value=datetime.date.today(), on_change=mark_dirty,
                           key="input_my_dob")
    st.session_state['my_name'], st.session_state['my_dob'] = my_name, my_dob

    st.session_state['curr_city_flow'] = city_autocomplete("Current City of Residence", "curr_city",
                                                           default_val=st.session_state.get('curr_city_flow', ''))
    st.divider()

    has_spouse = st.checkbox("Include a Spouse or Partner? (Enables joint tax brackets)",
                             value=st.session_state.get('has_spouse', False), on_change=mark_dirty,
                             key="input_has_spouse")
    st.session_state['has_spouse'] = has_spouse

    if has_spouse:
        sc1, sc2 = st.columns(2)
        spouse_name = sc1.text_input("Spouse/Partner Name", value=st.session_state.get('spouse_name', ''),
                                     on_change=mark_dirty, key="input_sp_name")
        spouse_dob = sc2.date_input("Spouse Date of Birth",
                                    value=st.session_state.get('spouse_dob', datetime.date(1982, 1, 1)),
                                    min_value=datetime.date(1920, 1, 1), max_value=datetime.date.today(),
                                    on_change=mark_dirty, key="input_sp_dob")
        st.session_state['spouse_name'], st.session_state['spouse_dob'] = spouse_name, spouse_dob

    st.divider()
    st.write("**Dependent Details** *(AI uses ages to drop daycare costs and start college timelines)*")
    num_kids = st.number_input("Number of Dependents (Kids)", 0, 10, len(st.session_state.get('kids_data', [])),
                               on_change=mark_dirty, key="input_num_kids")

    new_kids_data = []
    for i in range(num_kids):
        k1, k2 = st.columns([3, 1])
        kn = k1.text_input(f"Dependent {i + 1} Name", value=st.session_state.get('kids_data', [])[i]['name'] if i < len(
            st.session_state.get('kids_data', [])) else "", key=f"kn_{i}", on_change=mark_dirty)
        ka = k2.number_input(f"Age {i + 1}", 0, 25, st.session_state.get('kids_data', [])[i]['age'] if i < len(
            st.session_state.get('kids_data', [])) else 5, key=f"ka_{i}", on_change=mark_dirty)
        new_kids_data.append({"name": kn, "age": ka})
    st.session_state['kids_data'] = new_kids_data


def render_income():
    section_header("Active Annual Income Streams",
                   "List your current and future income sources. The engine handles inflation and precise tax routing.",
                   "💵")
    info_banner(
        "Employer Match Note: List 401(k) matches here. The engine strips them from your spendable cash flow, but safely deposits them into your portfolios.")

    df_inc = pd.DataFrame(st.session_state.get('income_data', []))
    current_year = datetime.date.today().year
    my_age = relativedelta(datetime.date.today(), st.session_state.get('my_dob', datetime.date(1980, 1, 1))).years
    if df_inc.empty:
        df_inc = pd.DataFrame(
            columns=["Description", "Category", "Owner", "Annual Amount ($)", "Start Year", "End Year", "Stop at Ret.?",
                     "Override Growth (%)"])
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
        column_order=["Description", "Category", "Owner", "Annual Amount ($)", "Start Year", "End Year", "Stop at Ret.?", "Override Growth (%)"],
        column_config={
            "Description": st.column_config.TextColumn("Description"),
            "Category": st.column_config.SelectboxColumn("Category", options=["Base Salary (W-2)", "Bonus / Commission", "Employer Match (401k/HSA)", "Equity / RSUs", "Contractor (1099)", "Dividends", "Social Security", "Pension", "Other"]),
            "Owner": st.column_config.SelectboxColumn("Whose Income?", options=["Me", "Spouse", "Joint"]),
            "Annual Amount ($)": st.column_config.NumberColumn("Amount per Year ($)", step=1000, format="$%d"),
            "Start Year": st.column_config.NumberColumn("Start Year", min_value=1900, max_value=2100, format="%d"),
            "End Year": st.column_config.NumberColumn("End Year", min_value=1900, max_value=2100, format="%d"),
            "Stop at Ret.?": st.column_config.CheckboxColumn("Stop at Retirement?"),
            "Override Growth (%)": st.column_config.NumberColumn("Custom Growth (%)", step=0.1, format="%.1f%%")
        }, num_rows="dynamic", use_container_width=True, key="inc_editor", on_change=mark_dirty
    )
    
    # --- FIX: Extract records and scrub NaN values to prevent infinite st.rerun() loops ---
    edited_inc_records = scrub_records(edited_inc.to_dict('records'))
    
    # --- FIX: UI Validation Pass ---
    for inc in edited_inc_records:
        if inc.get("Category") == "Social Security":
            s_yr = inc.get("Start Year")
            if s_yr is not None and str(s_yr).strip() != "":
                owner = inc.get("Owner", "Me")
                b_yr = st.session_state.get('my_dob', datetime.date(1980, 1, 1)).year if owner in ["Me", "Joint"] else st.session_state.get('spouse_dob', datetime.date(1982, 1, 1)).year
                
                if safe_num(s_yr) < b_yr + 62:
                    st.error(f"🚨 Social Security '{html.escape(str(inc.get('Description', 'Unknown')))}': Earliest claiming age is 62 (Year {b_yr + 62}). The simulation engine will cap this.")
                elif safe_num(s_yr) > b_yr + 70:
                    st.error(f"🚨 Social Security '{html.escape(str(inc.get('Description', 'Unknown')))}': Delayed retirement credits max out at age 70 (Year {b_yr + 70}). The simulation engine will cap this.")

    # --- FIX: Immediate State Commit ---
    # We compare the JSON strings to ignore Python dict object identity mismatches
    if json.dumps(edited_inc_records) != json.dumps(scrub_records(st.session_state.get('income_data', []))):
        st.session_state['income_data'] = edited_inc_records
        st.rerun()

    render_total("Total Pre-Tax Income", edited_inc['Annual Amount ($)'])

    # --- FIX: Safe AI State Machine ---
    col_ai_inc, _ = st.columns([3, 1])
    with col_ai_inc:
        if st.button("✨ Auto-Estimate My Social Security (AI)", type="primary", use_container_width=True):
            st.session_state['trigger_ss_ai'] = True

    if st.session_state.get('trigger_ss_ai'):
        if check_ai_rate_limit():
            try:
                with st.spinner("Asking AI to estimate your Social Security benefits based on your age and income..."):
                    spouse_age = relativedelta(datetime.date.today(), st.session_state.get('spouse_dob',
                                                                                           datetime.date(1982, 1,
                                                                                                         1))).years if st.session_state.get(
                        'has_spouse') else 0
                    curr_inc = pd.to_numeric(edited_inc['Annual Amount ($)'], errors='coerce').fillna(0).sum()
                    if st.session_state.get('has_spouse'):
                        prompt = f"User is {my_age} years old making ${curr_inc}/year. Spouse is {spouse_age} years old. Estimate realistic annual Social Security primary insurance amounts (PIA) at Full Retirement Age for both. Return JSON: {{'ss_amount_me': integer, 'ss_amount_spouse': integer}}"
                    else:
                        prompt = f"User is {my_age} years old making ${curr_inc}/year. Estimate their annual Social Security primary insurance amount (PIA) at Full Retirement Age. Return JSON: {{'ss_amount_me': integer}}"
                    res = call_gemini_json(prompt)
                    if res:
                        current_inc = [row for row in edited_inc.to_dict('records') if
                                       row.get("Category") != "Social Security"]
                        my_birth_year = st.session_state.get('my_dob', datetime.date(1980, 1, 1)).year
                        spouse_birth_year = st.session_state.get('spouse_dob', datetime.date(1982, 1,
                                                                                             1)).year if st.session_state.get(
                            'has_spouse') else current_year
                        if 'ss_amount_me' in res: current_inc.append(
                            {"Description": "Estimated Social Security (Primary)", "Category": "Social Security",
                             "Owner": "Me", "Annual Amount ($)": res['ss_amount_me'], "Start Year": my_birth_year + 67,
                             "End Year": 2100, "Stop at Ret.?": False, "Override Growth (%)": None})
                        if 'ss_amount_spouse' in res and st.session_state.get('has_spouse'): current_inc.append(
                            {"Description": "Estimated Social Security (Spouse)", "Category": "Social Security",
                             "Owner": "Spouse", "Annual Amount ($)": res['ss_amount_spouse'],
                             "Start Year": spouse_birth_year + 67, "End Year": 2100, "Stop at Ret.?": False,
                             "Override Growth (%)": None})
                        st.session_state['income_data'] = current_inc
                        mark_dirty()
            finally:
                st.session_state['trigger_ss_ai'] = False
                st.rerun()


def render_assets():
    section_header("Assets, Debts & Net Worth",
                   "Construct your balance sheet. The AI draws down these buckets dynamically.", "🏦")

    edited_re = pd.DataFrame()
    edited_biz = pd.DataFrame()
    edited_ast = pd.DataFrame()
    edited_debt = pd.DataFrame()

    tab_re, tab_biz, tab_ast, tab_debt = st.tabs(
        ["🏢 Real Estate", "💼 Business Interests", "🏦 Liquid Assets", "💳 Debts & Loans"])

    with tab_re:
        info_banner(
            "Smart Mortgages: Enter your balance, rate, and payment. The math engine automatically pays it down and drops the expense once it hits zero.")
        df_re = pd.DataFrame(st.session_state.get('real_estate_data', []))
        if df_re.empty:
            df_re = pd.DataFrame(
                columns=["Property Name", "Is Primary Residence?", "Market Value ($)", "Mortgage Balance ($)",
                         "Interest Rate (%)", "Mortgage Payment ($)", "Monthly Expenses ($)", "Monthly Rent ($)",
                         "Override Prop Growth (%)", "Override Rent Growth (%)"])
        else:
            df_re = df_re.reindex(
                columns=["Property Name", "Is Primary Residence?", "Market Value ($)", "Mortgage Balance ($)",
                         "Interest Rate (%)", "Mortgage Payment ($)", "Monthly Expenses ($)", "Monthly Rent ($)",
                         "Override Prop Growth (%)", "Override Rent Growth (%)"])

        edited_re = st.data_editor(
            df_re,
            column_order=["Property Name", "Is Primary Residence?", "Market Value ($)", "Mortgage Balance ($)",
                          "Interest Rate (%)", "Mortgage Payment ($)", "Monthly Expenses ($)", "Monthly Rent ($)",
                          "Override Prop Growth (%)", "Override Rent Growth (%)"],
            column_config={
                "Property Name": st.column_config.TextColumn("Property Name"),
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
            }, num_rows="dynamic", use_container_width=True, key="re_editor", on_change=mark_dirty
        )
        st.session_state['real_estate_data'] = edited_re.to_dict('records')

    with tab_biz:
        df_biz = pd.DataFrame(st.session_state.get('business_data', []))
        if df_biz.empty:
            df_biz = pd.DataFrame(
                columns=["Business Name", "Total Valuation ($)", "Your Ownership (%)", "Annual Distribution ($)",
                         "Override Val. Growth (%)", "Override Dist. Growth (%)"])
        else:
            if "Override Val. Growth (%)" not in df_biz.columns: df_biz["Override Val. Growth (%)"] = None
            if "Override Dist. Growth (%)" not in df_biz.columns: df_biz["Override Dist. Growth (%)"] = None
            df_biz = df_biz.reindex(
                columns=["Business Name", "Total Valuation ($)", "Your Ownership (%)", "Annual Distribution ($)",
                         "Override Val. Growth (%)", "Override Dist. Growth (%)"])

        edited_biz = st.data_editor(
            df_biz,
            column_order=["Business Name", "Total Valuation ($)", "Your Ownership (%)", "Annual Distribution ($)",
                          "Override Val. Growth (%)", "Override Dist. Growth (%)"],
            column_config={
                "Total Valuation ($)": st.column_config.NumberColumn("Total Value ($)", step=10000, format="$%d"),
                "Annual Distribution ($)": st.column_config.NumberColumn("Annual Income ($)", step=1000, format="$%d"),
                "Your Ownership (%)": st.column_config.NumberColumn("Your Ownership (%)", min_value=0, max_value=100,
                                                                    format="%d%%"),
                "Override Val. Growth (%)": st.column_config.NumberColumn("Value Growth (%)", step=0.1,
                                                                          format="%.1f%%"),
                "Override Dist. Growth (%)": st.column_config.NumberColumn("Income Growth (%)", step=0.1,
                                                                           format="%.1f%%")
            }, num_rows="dynamic", use_container_width=True, key="biz_editor", on_change=mark_dirty
        )
        st.session_state['business_data'] = edited_biz.to_dict('records')

    with tab_ast:
        info_banner(
            "Contribution Engine Update: Put ONLY your own out-of-pocket contributions here. The AI engine automatically detects 'Employer Matches' from your Income table and securely routes them directly into your 401(k) behind the scenes!")
        df_ast = pd.DataFrame(st.session_state.get('liquid_assets_data', []))
        if df_ast.empty:
            df_ast = pd.DataFrame(
                columns=["Account Name", "Type", "Owner", "Current Balance ($)", "Annual Contribution ($/yr)",
                         "Est. Annual Growth (%)", "Stop Contrib at Ret.?"])
        else:
            if "Annual Contribution ($)" in df_ast.columns: df_ast.rename(
                columns={'Annual Contribution ($)': 'Annual Contribution ($/yr)'}, inplace=True)
            if "Stop Contrib at Ret.?" not in df_ast.columns: df_ast["Stop Contrib at Ret.?"] = True
            df_ast['Type'] = df_ast['Type'].replace(
                {'Traditional 401k/IRA': 'Traditional 401(k)', 'Roth 401k/IRA': 'Roth 401(k)'})
            df_ast = df_ast.reindex(
                columns=["Account Name", "Type", "Owner", "Current Balance ($)", "Annual Contribution ($/yr)",
                         "Est. Annual Growth (%)", "Stop Contrib at Ret.?"])

        edited_ast = st.data_editor(
            df_ast,
            column_order=["Account Name", "Type", "Owner", "Current Balance ($)", "Annual Contribution ($/yr)",
                          "Est. Annual Growth (%)", "Stop Contrib at Ret.?"],
            column_config={
                "Type": st.column_config.SelectboxColumn("Account Type",
                                                         options=["Checking/Savings", "HYSA", "Brokerage (Taxable)",
                                                                  "Traditional 401(k)", "Traditional IRA",
                                                                  "Roth 401(k)", "Roth IRA", "HSA", "Crypto",
                                                                  "529 Plan", "Other"]),
                "Owner": st.column_config.SelectboxColumn("Whose Account?", options=["Me", "Spouse", "Joint"]),
                "Current Balance ($)": st.column_config.NumberColumn("Current Balance ($)", step=5000, format="$%d"),
                "Annual Contribution ($/yr)": st.column_config.NumberColumn("Your Contributions ($/yr)", step=1000,
                                                                            format="$%d"),
                "Est. Annual Growth (%)": st.column_config.NumberColumn("Custom Return (%)", format="%.1f%%"),
                "Stop Contrib at Ret.?": st.column_config.CheckboxColumn("Stop Adding at Ret.?")
            }, num_rows="dynamic", use_container_width=True, key="assets_editor", on_change=mark_dirty
        )
        st.session_state['liquid_assets_data'] = edited_ast.to_dict('records')

    with tab_debt:
        info_banner(
            "Like mortgages, simply provide the balance, rate, and payment. We'll dynamically pay it down to zero.")
        df_debt = pd.DataFrame(st.session_state.get('liabilities_data', []))
        if df_debt.empty:
            df_debt = pd.DataFrame(
                columns=["Debt Name", "Type", "Current Balance ($)", "Interest Rate (%)", "Monthly Payment ($)"])
        else:
            df_debt = df_debt.reindex(
                columns=["Debt Name", "Type", "Current Balance ($)", "Interest Rate (%)", "Monthly Payment ($)"])

        edited_debt = st.data_editor(
            df_debt,
            column_order=["Debt Name", "Type", "Current Balance ($)", "Interest Rate (%)", "Monthly Payment ($)"],
            column_config={
                "Current Balance ($)": st.column_config.NumberColumn("Current Balance ($)", step=1000, format="$%d"),
                "Interest Rate (%)": st.column_config.NumberColumn("Interest Rate (%)", step=0.001, format="%.3f%%"),
                "Monthly Payment ($)": st.column_config.NumberColumn("Monthly Payment ($)", step=100, format="$%d")
            }, num_rows="dynamic", use_container_width=True, key="debt_editor", on_change=mark_dirty
        )
        st.session_state['liabilities_data'] = edited_debt.to_dict('records')

    re_eq = pd.to_numeric(edited_re['Market Value ($)'], errors='coerce').fillna(0).sum() - pd.to_numeric(
        edited_re['Mortgage Balance ($)'], errors='coerce').fillna(0).sum() if not edited_re.empty else 0
    biz_eq = (pd.to_numeric(edited_biz['Total Valuation ($)'], errors='coerce').fillna(0) * (
                pd.to_numeric(edited_biz['Your Ownership (%)'], errors='coerce').fillna(
                    0) / 100)).sum() if not edited_biz.empty else 0
    liq_ast = pd.to_numeric(edited_ast['Current Balance ($)'], errors='coerce').fillna(
        0).sum() if not edited_ast.empty else 0
    total_debt = pd.to_numeric(edited_debt['Current Balance ($)'], errors='coerce').fillna(
        0).sum() if not edited_debt.empty else 0

    st.divider()
    c_met1, c_met2, c_met3, c_met4 = st.columns(4)
    c_met1.metric("Real Estate Equity", f"${re_eq:,.0f}")
    c_met2.metric("Business Equity", f"${biz_eq:,.0f}")
    c_met3.metric("Liquid Assets", f"${liq_ast:,.0f}")
    c_met4.metric("Other Debt", f"${total_debt:,.0f}")


def render_cashflows():
    section_header("Lifetime Cash Flows", "Map out budgets and milestones. Do not double-count housing or debt.", "💸")
    info_banner(
        "Healthcare Note: Assume you are covered by employer-sponsored healthcare while working. The engine automatically builds in Pre-Medicare gaps, Medicare premium cliffs at age 65, and IRMAA surcharges.")

    c_loc1, c_loc2 = st.columns(2)
    with c_loc1:
        curr_city_flow = city_autocomplete("Current City", "curr_city_flow",
                                           default_val=st.session_state.get('curr_city_flow', ''))
        st.session_state['curr_city_flow'] = curr_city_flow
    with c_loc2:
        ret_city_flow = city_autocomplete("Retirement City (Optional)", "retire_city_flow",
                                          default_val=st.session_state.get('retire_city_flow', ''))
        st.session_state['retire_city_flow'] = ret_city_flow

    st.divider()
    df_exp = pd.DataFrame(st.session_state.get('lifetime_expenses', []))

    if df_exp.empty: df_exp = pd.DataFrame(
        columns=["Description", "Category", "Frequency", "Amount ($)", "Start Phase", "Start Year", "End Phase",
                 "End Year", "AI Estimate?"])
    if not df_exp.empty:
        if 'Start Phase' in df_exp.columns and 'Start Year' in df_exp.columns: df_exp.loc[
            df_exp['Start Phase'] != 'Custom Year', 'Start Year'] = None
        if 'End Phase' in df_exp.columns and 'End Year' in df_exp.columns: df_exp.loc[
            df_exp['End Phase'] != 'Custom Year', 'End Year'] = None

    edited_exp = st.data_editor(
        df_exp,
        column_order=["Description", "Category", "Frequency", "Amount ($)", "Start Phase", "Start Year", "End Phase",
                      "End Year", "AI Estimate?"],
        column_config={
            "Description": st.column_config.TextColumn("Description"),
            "Category": st.column_config.SelectboxColumn("Category", options=BUDGET_CATEGORIES),
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
        }, num_rows="dynamic", use_container_width=True, key="exp_ed", on_change=mark_dirty
    )
    st.session_state['lifetime_expenses'] = edited_exp.to_dict('records')

    # --- FIX: Safe AI State Machine ---
    col_ai_cb, _ = st.columns([3, 1])
    with col_ai_cb:
        if st.button("✨ Auto-Estimate Budget & Milestones for selected locations (AI)", type="primary",
                     use_container_width=True):
            st.session_state['trigger_budget_ai'] = True

    if st.session_state.get('trigger_budget_ai'):
        if check_ai_rate_limit():
            try:
                with st.spinner("Analyzing localized CPI data, timelines, and family needs..."):
                    valid = edited_exp[edited_exp["Description"].astype(str) != ""].copy()
                    locked = valid[valid["AI Estimate?"] == False].to_dict('records')
                    locked_desc = [x['Description'] for x in locked]

                    current_year = datetime.date.today().year
                    my_age = relativedelta(datetime.date.today(),
                                           st.session_state.get('my_dob', datetime.date(1980, 1, 1))).years
                    spouse_age = relativedelta(datetime.date.today(), st.session_state.get('spouse_dob',
                                                                                           datetime.date(1982, 1,
                                                                                                         1))).years if st.session_state.get(
                        'has_spouse') else 0
                    k_ctx_list = [f"{k['name']}:{k['age']}" for k in st.session_state.get('kids_data', [])]
                    k_ctx_str = ", ".join(k_ctx_list)
                    f_ctx = f"User({my_age})" + (
                        f", Spouse({st.session_state.get('spouse_name', '')}:{spouse_age})" if st.session_state.get(
                            'has_spouse') else "") + f", Dependents({k_ctx_str})"

                    df_inc = pd.DataFrame(st.session_state.get('income_data', []))
                    curr_inc_total = pd.to_numeric(df_inc['Annual Amount ($)'], errors='coerce').fillna(
                        0).sum() if not df_inc.empty else 0
                    df_ast = pd.DataFrame(st.session_state.get('liquid_assets_data', []))
                    liq_ast_total = pd.to_numeric(df_ast['Current Balance ($)'], errors='coerce').fillna(
                        0).sum() if not df_ast.empty else 0

                    df_re = pd.DataFrame(st.session_state.get('real_estate_data', []))
                    primary_re = df_re[df_re[
                                           "Is Primary Residence?"] == True] if not df_re.empty and "Is Primary Residence?" in df_re.columns else pd.DataFrame()
                    owns_home = not primary_re.empty

                    curr_city_flow_clean = re.sub(r'[^a-zA-Z0-9, ]', '', str(curr_city_flow))[:100]
                    ret_city_flow_clean = re.sub(r'[^a-zA-Z0-9, ]', '', str(ret_city_flow))[:100]

                    if owns_home:
                        ai_exclusion = "STRICT RULE: DO NOT INCLUDE Housing, Rent, Mortgages, Auto Loans, or Debt Payments."
                    else:
                        ai_exclusion = "STRICT RULE: DO NOT INCLUDE Mortgages, Auto Loans, or Debt Payments. CRITICAL INSTRUCTION: You MUST INCLUDE a realistic 'Housing / Rent' expense (Category: 'Housing / Rent')."

                    wealth_ctx = f"The household has a current annual pre-tax income of ${curr_inc_total:,.0f} and liquid assets totaling ${liq_ast_total:,.0f}. VERY IMPORTANT: assume these users are savvy spenders."
                    allowed_cats = ", ".join(BUDGET_CATEGORIES)
                    prompt = f"Current City: {curr_city_flow_clean}. Planned Retirement City: {ret_city_flow_clean}. Family: {f_ctx}. Current Year is {current_year}. {wealth_ctx} Generate a comprehensive list of missing living expenses AND expected future life milestones. {ai_exclusion} Skip these items: {json.dumps(locked_desc)}. Return ONLY a JSON array of objects with keys: 'Description', 'Category' (MUST be exactly one of: {allowed_cats}), 'Frequency' (Monthly/Yearly/One-Time), 'Amount ($)' (number), 'Start Phase' (Now/At Retirement/Custom Year), 'Start Year' (integer or null), 'End Phase' (End of Life/At Retirement/Custom Year), 'End Year' (integer or null), and 'AI Estimate?' (true)."
                    res = call_gemini_json(prompt)
                    if res and isinstance(res, list) and len(res) > 0:
                        st.session_state['lifetime_expenses'] = locked + res
                        mark_dirty()
            finally:
                st.session_state['trigger_budget_ai'] = False
                st.rerun()

    st.divider()
    pre_ret_monthly = 0
    post_ret_monthly = 0
    if not edited_exp.empty:
        for idx, row in edited_exp.iterrows():
            amt = safe_num(row.get("Amount ($)", 0))
            if row.get("Frequency") == "Yearly": amt = amt / 12.0

            if row.get("Frequency") != "One-Time" and row.get("Start Phase") != "Custom Year":
                if row.get("Start Phase") == "Now" and row.get("End Phase") in ["At Retirement",
                                                                                "End of Life"]: pre_ret_monthly += amt
                if (row.get("Start Phase") == "At Retirement" and row.get("End Phase") == "End of Life") or (
                        row.get("Start Phase") == "Now" and row.get(
                    "End Phase") == "End of Life"): post_ret_monthly += amt

    c_met1, c_met2 = st.columns(2)
    c_met1.metric("Avg. Monthly Burn (Pre-Retirement)", f"${pre_ret_monthly:,.0f}",
                  f"${pre_ret_monthly * 12:,.0f} Annually")
    c_met2.metric("Avg. Monthly Burn (Post-Retirement)", f"${post_ret_monthly:,.0f}",
                  f"${post_ret_monthly * 12:,.0f} Annually")


def render_simulation():
    section_header("Simulation", "Fine-tune your timeline and run Monte Carlo scenarios.", "📈")

    def ai_number_input(label, state_key, prompt, col):
        with col:
            sub_c1, sub_c2 = st.columns([5, 2])
            widget_key = f"in_{state_key}"

            val = sub_c1.number_input(label, step=0.1, key=widget_key,
                                      value=float(st.session_state.get('assumptions', {}).get(state_key, 0.0)))
            if val != st.session_state.get('assumptions', {}).get(state_key):
                new_assumptions = st.session_state.get('assumptions', {}).copy()
                new_assumptions[state_key] = val
                st.session_state['assumptions'] = new_assumptions
                mark_dirty()

            sub_c2.markdown("<div style='height: 28px;'></div>", unsafe_allow_html=True)
            if sub_c2.button("✨ AI", key=f"btn_{state_key}", help=f"AI Estimate for {label}", use_container_width=True,
                             type="primary"):
                st.session_state[f'trigger_ai_{state_key}'] = True

            if st.session_state.get(f'trigger_ai_{state_key}'):
                if check_ai_rate_limit():
                    try:
                        with st.spinner("AI estimating..."):
                            enhanced_prompt = prompt + " CRITICAL INSTRUCTION: You MUST return the value as a percentage number between 0 and 100 (e.g., return 5.5 for 5.5%, DO NOT return 0.055). Return ONLY a JSON object."
                            res = call_gemini_json(enhanced_prompt)
                            if res and state_key in res:
                                new_val = float(res[state_key])
                                if 0 < new_val < 0.30: new_val *= 100.0
                                new_assumptions = st.session_state.get('assumptions', {}).copy()
                                new_assumptions[state_key] = new_val
                                st.session_state['assumptions'] = new_assumptions
                                mark_dirty()
                    finally:
                        st.session_state[f'trigger_ai_{state_key}'] = False
                        st.rerun()
            return val

    tab_ages, tab_assumptions, tab_stress = st.tabs(
        ["⏳ Timeline & Ages", "📊 Macro Assumptions", "🌪️ Stress Tests & Taxes"])

    my_age = relativedelta(datetime.date.today(), st.session_state.get('my_dob', datetime.date(1980, 1, 1))).years
    spouse_age = relativedelta(datetime.date.today(), st.session_state.get('spouse_dob', datetime.date(1982, 1,
                                                                                                       1))).years if st.session_state.get(
        'has_spouse') else 0

    with tab_ages:
        cc1, cc2, cc3, cc4 = st.columns(4)
        ret_age = cc1.slider("Retirement Age", max(int(my_age), 1), 100,
                             max(int(my_age), int(st.session_state.get('ret_age', 65))), key="sld_ret_age")
        s_ret_age = cc2.slider("Spouse Retire Age", max(int(spouse_age), 1), 100,
                               max(int(spouse_age), int(st.session_state.get('s_ret_age', 65))),
                               key="sld_s_ret_age") if st.session_state.get('has_spouse') else 65
        my_life_exp = cc3.slider("Your Life Expectancy", max(70, ret_age), 115,
                                 max(ret_age, int(st.session_state.get('my_life_exp', 95))), key="sld_life_exp")
        spouse_life_exp = cc4.slider("Spouse Life Expectancy", max(70, s_ret_age), 115,
                                     max(s_ret_age, int(st.session_state.get('spouse_life_exp', 95))),
                                     key="sld_s_life_exp") if st.session_state.get('has_spouse') else 0

        if ret_age < my_age: info_banner("Retirement age cannot be lower than current age.", "warning")
        if my_life_exp < ret_age: info_banner("Life expectancy cannot be lower than retirement age.", "warning")

        if st.session_state.get('ret_age') != ret_age: update_state('ret_age', ret_age)
        if st.session_state.get('s_ret_age') != s_ret_age: update_state('s_ret_age', s_ret_age)
        if st.session_state.get('my_life_exp') != my_life_exp: update_state('my_life_exp', my_life_exp)
        if st.session_state.get('spouse_life_exp') != spouse_life_exp: update_state('spouse_life_exp', spouse_life_exp)

    with tab_assumptions:
        st.markdown(
            "<div class='card' style='margin-bottom: 24px;'><h3 style='margin-top:0;'>Macroeconomic Assumptions</h3></div>",
            unsafe_allow_html=True)
        ac1, ac2, ac3 = st.columns(3)
        mkt = ai_number_input("Market Growth (%)", 'market_growth',
                              f"What is a realistic conservative long-term annual market growth rate for a diversified retirement portfolio? Return JSON: {{'market_growth': float}}",
                              ac1)
        infl = ai_number_input("General CPI Inflation (%)", 'inflation',
                               f"What is the projected long-term average general US CPI inflation rate? Return JSON: {{'inflation': float}}",
                               ac2)
        inc_g = ai_number_input("Income Growth (%)", 'income_growth',
                                f"What is a realistic annual salary growth/merit increase rate? Return JSON: {{'income_growth': float}}",
                                ac3)

        ac4, ac5, ac6 = st.columns(3)
        curr_city_flow = st.session_state.get('curr_city_flow', '')
        curr_city_flow_clean = re.sub(r'[^a-zA-Z0-9, ]', '', str(curr_city_flow))[:100]
        infl_hc = ai_number_input("Healthcare Inflation (%)", 'inflation_healthcare',
                                  f"What is the projected long-term annual healthcare cost inflation rate in the US? Return JSON: {{'inflation_healthcare': float}}",
                                  ac4)
        infl_ed = ai_number_input("Education Inflation (%)", 'inflation_education',
                                  f"What is the projected long-term annual college tuition inflation rate in the US? Return JSON: {{'inflation_education': float}}",
                                  ac5)
        prop_g = ai_number_input("Property Growth (%)", 'property_growth',
                                 f"Historical average annual real estate appreciation rate for {curr_city_flow_clean}? Return JSON: {{'property_growth': float}}",
                                 ac6)

        ac7, ac8, ac9 = st.columns(3)
        ret_city_flow = st.session_state.get('retire_city_flow', '')
        ret_city_flow_clean = re.sub(r'[^a-zA-Z0-9, ]', '', str(ret_city_flow))[:100]
        curr_inc_total = pd.to_numeric(pd.DataFrame(st.session_state.get('income_data', []))['Annual Amount ($)'],
                                       errors='coerce').fillna(0).sum() if st.session_state.get('income_data') else 0
        rent_g = ai_number_input("Rent Growth (%)", 'rent_growth',
                                 f"Projected average annual rent increase rate for {curr_city_flow_clean}? Return JSON: {{'rent_growth': float}}",
                                 ac7)
        cur_t = ai_number_input(
            "Current State Tax (%)", 
            'current_tax_rate', 
            f"User lives in {curr_city_flow_clean} with ${curr_inc_total:,.0f} income. Suggest effective STATE/LOCAL income tax rate ONLY. Return JSON: {{'current_tax_rate': float}}", 
            ac8
        )
        # Inject the UI warning immediately below the input
        with ac8:
            st.caption("*(Note: Applied to Fed AGI. If your state—like PA or NJ—does not allow 401k deductions, adjust this rate slightly higher.)*")

        ret_t = ai_number_input(
            "Retire State Tax (%)", 
            'retire_tax_rate', 
            f"User plans to retire in {ret_city_flow_clean} with estimated retirement income. Suggest effective STATE/LOCAL income tax rate ONLY. Return JSON: {{'retire_tax_rate': float}}", 
            ac9
        )

        ac10, _, _ = st.columns(3)
        shortfall_rate = ai_number_input("Shortfall Penalty/Borrowing Rate (%)", 'shortfall_rate',
                                         f"What is a realistic personal loan or credit card interest rate for someone forced to borrow during retirement shortfalls? Return JSON: {{'shortfall_rate': float}}",
                                         ac10)

    with tab_stress:
        st.markdown(
            "<div class='card' style='margin-bottom: 24px;'><h3 style='margin-top:0;'>Tax Engine & Stress Tests</h3></div>",
            unsafe_allow_html=True)
        sc1, sc2 = st.columns(2)

        def update_asm_toggle(key, val):
            if st.session_state.get('assumptions', {}).get(key) != val:
                new_asm = st.session_state.get('assumptions', {}).copy()
                new_asm[key] = val
                st.session_state['assumptions'] = new_asm
                mark_dirty()

        with sc1:
            medicare_gap = st.toggle("🏥 Model Pre-Medicare Gap",
                                     value=st.session_state.get('assumptions', {}).get('medicare_gap', True))
            update_asm_toggle('medicare_gap', medicare_gap)
            medicare_cliff = st.toggle("🏥 Apply Medicare Cliff (Drop Healthcare at 65)",
                                       value=st.session_state.get('assumptions', {}).get('medicare_cliff', True))
            update_asm_toggle('medicare_cliff', medicare_cliff)
            glidepath = st.toggle("📉 Apply Investment Glidepath",
                                  value=st.session_state.get('assumptions', {}).get('glidepath', True))
            update_asm_toggle('glidepath', glidepath)
            stress_test = st.toggle("📉 Apply -25% Market Crash at Retirement",
                                    value=st.session_state.get('assumptions', {}).get('stress_test', False))
            update_asm_toggle('stress_test', stress_test)
            ltc_shock = st.toggle("🛏️ Long-Term Care (LTC) Shock",
                                  value=st.session_state.get('assumptions', {}).get('ltc_shock', False))
            update_asm_toggle('ltc_shock', ltc_shock)

        with sc2:
            active_withdrawal_strategy = st.selectbox("Shortfall Withdrawal Sequence",
                                                      options=["Standard (Taxable -> 401k -> Roth)",
                                                               "Roth Preferred (Taxable -> Roth -> 401k)"],
                                                      index=0 if "Standard" in st.session_state.get('assumptions',
                                                                                                    {}).get(
                                                          'withdrawal_strategy', 'Standard') else 1)
            roth_conversions = st.toggle("🔄 Enable Roth Conversion Optimizer",
                                         value=st.session_state.get('assumptions', {}).get('roth_conversions', False))
            roth_target_idx = ["12%", "22%", "24%", "32%"].index(
                st.session_state.get('assumptions', {}).get('roth_target', "24%"))
            roth_target = st.selectbox("Target Bracket to Fill", options=["12%", "22%", "24%", "32%"],
                                       index=roth_target_idx)
            update_asm_toggle('roth_conversions', roth_conversions)
            update_asm_toggle('roth_target', roth_target)
            update_asm_toggle('withdrawal_strategy', active_withdrawal_strategy.split(' ')[0])

    st.divider()
    view_todays_dollars = st.toggle("💵 View Charts in Today's Dollars", value=True,
                                    help="Removes the effect of inflation so you can easily understand what these big future numbers feel like today.")
    st.session_state['view_todays_dollars'] = view_todays_dollars

    sim_ctx = build_sim_context()
    if sim_ctx['my_age'] <= 0:
        st.warning("Please enter a valid Date of Birth in the Profile section to run the simulation.")
    elif sim_ctx['max_years'] <= 0:
        st.warning("Your Life Expectancy must be greater than your Current Age to run the simulation.")
    else:
        mkt_seq = tuple([sim_ctx['mkt']] * (sim_ctx['max_years'] + 1))

        # --- FIX: Serialize context to guarantee stable, instant cache hits ---
        df_sim_nominal, df_det_nominal, df_nw_nominal, run_milestones = execute_sim_engine_v8(mkt_seq, json.dumps(sim_ctx))

        if not df_sim_nominal.empty:
            df_sim, df_det, df_nw = df_sim_nominal.copy(), df_det_nominal.copy(), df_nw_nominal.copy()
            st.session_state['df_sim_nominal'], st.session_state['df_det'], st.session_state[
                'df_nw'] = df_sim_nominal, df_det_nominal, df_nw_nominal

            if view_todays_dollars:
                current_year = datetime.date.today().year
                discounts = (1 + sim_ctx['infl'] / 100) ** (df_sim['Year'] - current_year)
                cols_sim = ["Annual Income", "Asset Withdrawals", "Annual Expenses", "Annual Taxes",
                            "Annual Net Savings", "Liquid Assets", "Real Estate Equity", "Business Equity", "Debt",
                            "Unfunded Debt", "Net Worth"]
                df_sim[cols_sim] = df_sim[cols_sim].div(discounts, axis=0)
                cols_det = [c for c in df_det.columns if
                            c not in ["Age (Primary)", "Age (Spouse)", "Year"] and pd.api.types.is_numeric_dtype(
                                df_det[c])]
                df_det[cols_det] = df_det[cols_det].div(discounts, axis=0)
                cols_nw = [c for c in df_nw.columns if
                           c not in ["Age (Primary)", "Age (Spouse)", "Year"] and pd.api.types.is_numeric_dtype(
                               df_nw[c])]
                df_nw[cols_nw] = df_nw[cols_nw].div(discounts, axis=0)

            numeric_cols_det = [c for c in df_det.columns if pd.api.types.is_numeric_dtype(df_det[c])]
            df_det[numeric_cols_det] = df_det[numeric_cols_det].round(0) + 0.0
            numeric_cols_nw = [c for c in df_nw.columns if pd.api.types.is_numeric_dtype(df_nw[c])]
            df_nw[numeric_cols_nw] = df_nw[numeric_cols_nw].round(0) + 0.0

            st.session_state['df_sim_display'] = df_sim

            final_nw = df_sim.iloc[-1]['Net Worth']
            current_nw = df_sim.iloc[0]['Net Worth']
            ret_nw_rows = df_sim[df_sim['Year'] == sim_ctx['primary_retire_year']]
            ret_nw = ret_nw_rows['Net Worth'].values[0] if not ret_nw_rows.empty else current_nw

            shortfall_mask = df_sim['Unfunded Debt'] > 0
            deplete_year = df_sim[shortfall_mask]['Year'].min() if not df_sim[shortfall_mask].empty else None
            deplete_age = df_sim[shortfall_mask]['Age (Primary)'].min() if not df_sim[shortfall_mask].empty else None

            render_status_bar(deplete_year, deplete_age, final_nw)

            # --- NEW OUTPUT TABS START HERE ---
            out_tab_main, out_tab_sens, out_tab_mc, out_tab_tax, out_tab_logs = st.tabs([
                "📊 Main Projection", "🌪️ Sensitivity", "🎲 Monte Carlo Risk", 
                "🏛️ Tax & Roth Optimizer", "📋 Detailed Logs & Export"
            ])

            with out_tab_main:
                if HAS_PLOTLY:
                    current_year = datetime.date.today().year
                    m_x_normal, m_y_normal, m_text_normal, m_x_system, m_y_system, m_text_system, m_x_alert, m_y_alert, m_text_alert = [], [], [], [], [], [], [], [], []

                    if run_milestones:
                        m_years = sorted(list(run_milestones.keys()))
                        for y in m_years:
                            row = df_sim[df_sim['Year'] == y]
                            nw_val = row['Net Worth'].values[0] if not row.empty else 0
                            events = run_milestones[y]
                            normals, systems, alerts = [e for e in events if e.get('type') == 'normal'], [e for e in events if e.get('type') == 'system'], [e for e in events if e.get('type') == 'critical']
                            discount = (1 + sim_ctx['infl'] / 100) ** (y - current_year) if view_todays_dollars else 1.0

                            if normals:
                                m_x_normal.append(y); m_y_normal.append(nw_val)
                                m_text_normal.append(f"<b>Year {y}:</b><br>" + "<br>".join([f"• {html.escape(m['desc'])} (${m['amt'] / discount:,.0f})" for m in normals]))
                            if systems:
                                m_x_system.append(y); m_y_system.append(nw_val)
                                m_text_system.append(f"<b>System Event ({y}):</b><br>" + "<br>".join([f"• {html.escape(m['desc'])}" for m in systems]))
                            if alerts:
                                m_x_alert.append(y); m_y_alert.append(nw_val)
                                m_text_alert.append(f"<b>⚠️ ALERT ({y}):</b><br>" + "<br>".join([f"• {html.escape(m['desc'])}" for m in alerts]))

                    st.write("#### Net Worth Composition (Smart Asset Drawdown)")
                    fig_nw = go.Figure()
                    ast_cols = [c for c in df_nw.columns if c.startswith("Asset: ")]
                    
                    bar_colors = ['#2563eb', '#059669', '#d97706', '#0d9488', '#db2777', '#eab308', '#0891b2', '#65a30d', '#8b5cf6', '#ea580c']

                    for i, col in enumerate(ast_cols):
                        fig_nw.add_trace(go.Bar(x=df_nw["Year"], y=df_nw[col], name=col.replace("Asset: ", ""), marker_color=bar_colors[i % len(bar_colors)]))

                    fig_nw.add_trace(go.Bar(x=df_nw["Year"], y=df_nw["Total Real Estate Equity"], name='Real Estate Equity', marker_color='#4338ca'))
                    fig_nw.add_trace(go.Bar(x=df_nw["Year"], y=df_nw["Total Business Equity"], name='Business Equity', marker_color='#92400e'))
                    fig_nw.add_trace(go.Bar(x=df_nw["Year"], y=df_nw["Total Debt Liabilities"], name='Total Liabilities (Inc. Shortfalls)', marker_color='#dc2626'))
                    fig_nw.add_trace(go.Scatter(x=df_nw["Year"], y=df_nw["Total Net Worth"], mode='lines', name='Total Net Worth', line=dict(color='#0f172a', width=3, dash='solid')))

                    if m_x_normal: fig_nw.add_trace(go.Scatter(x=m_x_normal, y=m_y_normal, mode='markers', marker=dict(symbol='star', size=14, color='#eab308', line=dict(width=1.5, color='white')), name='User Milestones', hoverinfo='text', text=m_text_normal))
                    if m_x_system: fig_nw.add_trace(go.Scatter(x=m_x_system, y=m_y_system, mode='markers', marker=dict(symbol='star', size=14, color='#3b82f6', line=dict(width=1.5, color='white')), name='System Events', hoverinfo='text', text=m_text_system))
                    if m_x_alert: fig_nw.add_trace(go.Scatter(x=m_x_alert, y=m_y_alert, mode='markers', marker=dict(symbol='star', size=18, color='#ef4444', line=dict(width=2, color='white')), name='Critical Alerts', hoverinfo='text', text=m_text_alert))

                    fig_nw.update_layout(barmode='relative')
                    fig_nw = apply_chart_theme(fig_nw)
                    st.plotly_chart(fig_nw, use_container_width=True)

                    st.write("#### Annual Cash Flow & Progressive Taxes")
                    fig_cf = go.Figure()
                    fig_cf.add_trace(go.Scatter(x=df_sim["Year"], y=df_sim["Annual Income"], mode='lines', name='Organic Income', line=dict(color='#4f46e5', width=3)))
                    fig_cf.add_trace(go.Scatter(x=df_sim["Year"], y=df_sim["Asset Withdrawals"], mode='lines', name='Asset Withdrawals', line=dict(color='#a855f7', width=3, dash='dot')))
                    fig_cf.add_trace(go.Scatter(x=df_sim["Year"], y=df_sim["Annual Expenses"], mode='lines', name='Expenses', line=dict(color='#f43f5e', width=3)))
                    fig_cf.add_trace(go.Scatter(x=df_sim["Year"], y=df_sim["Annual Taxes"], mode='lines', name='Taxes', line=dict(color='#f59e0b', width=3)))
                    fig_cf.add_trace(go.Scatter(x=df_sim["Year"], y=df_sim["Annual Net Savings"], mode='lines', name='Net Cashflow', line=dict(color='#10b981', width=3, dash='dot')))

                    if m_x_normal: fig_cf.add_trace(go.Scatter(x=m_x_normal, y=[0] * len(m_x_normal), mode='markers', marker=dict(symbol='star', size=14, color='#eab308', line=dict(width=1.5, color='white')), name='User Milestones', hoverinfo='text', text=m_text_normal))
                    if m_x_system: fig_cf.add_trace(go.Scatter(x=m_x_system, y=[0] * len(m_x_system), mode='markers', marker=dict(symbol='star', size=14, color='#3b82f6', line=dict(width=1.5, color='white')), name='System Events', hoverinfo='text', text=m_text_system))
                    if m_x_alert: fig_cf.add_trace(go.Scatter(x=m_x_alert, y=[0] * len(m_x_alert), mode='markers', marker=dict(symbol='star', size=18, color='#ef4444', line=dict(width=2, color='white')), name='Critical Alerts', hoverinfo='text', text=m_text_alert))

                    fig_cf = apply_chart_theme(fig_cf)
                    st.plotly_chart(fig_cf, use_container_width=True)
                else:
                    st.info("Please install Plotly to view the charts.")

            # --- ENSURE THIS 'with' STATEMENT IS ALIGNED PERFECTLY WITH THE ONE ABOVE IT ---
            with out_tab_sens:
                st.markdown('<div class="info-text" style="margin-bottom: 20px;">💡 <strong>Tornado Chart (Sensitivity Analysis):</strong> This isolates your biggest risks by stress-testing key variables one at a time. It reveals which assumption moving slightly has the most drastic impact on your Final Net Worth.</div>', unsafe_allow_html=True)
                
                if st.button("✨ Run Sensitivity Analysis", type="primary", use_container_width=True, key="btn_sens"):
                    with st.spinner("Running 10 divergent timelines to map risk..."):
                        base_nw_sens = final_nw
                        
                        base_ctx_json = json.dumps(sim_ctx)
                        
                        sens_scenarios = [
                            ("Market Returns", "mkt", -1.0, 1.0, "%"),
                            ("General Inflation", "infl", -1.0, 1.0, "%"),
                            ("Retirement Age", "ret_age", -2, 2, " yrs"),
                            ("Living Expenses", "expenses", -10, 10, "%"),
                            ("Real Estate Growth", "prop_g", -1.5, 1.5, "%")
                        ]
                        
                        results = []
                        for name, key, down_val, up_val, unit in sens_scenarios:
                            def run_scenario(shift):
                                c = json.loads(base_ctx_json)
                                
                                if key == 'ret_age':
                                    c['primary_retire_year'] += int(shift)
                                    if c['has_spouse']: c['spouse_retire_year'] += int(shift)
                                elif key == 'expenses':
                                    for e in c['exp_records']: 
                                        e['Amount ($)'] = safe_num(e.get('Amount ($)')) * (1 + shift/100.0)
                                else:
                                    c[key] += shift
                                    
                                m_seq = tuple([c['mkt']] * (c['max_years'] + 1))
                                df_s, _, _, _ = execute_sim_engine_v8(m_seq, json.dumps(c))
                                val = df_s.iloc[-1]['Net Worth'] if not df_s.empty else 0
                                
                                if view_todays_dollars:
                                    val /= ((1 + c['infl'] / 100) ** c['max_years'])
                                return val

                            nw_down = run_scenario(down_val)
                            nw_up = run_scenario(up_val)
                            
                            d_down = nw_down - base_nw_sens
                            d_up = nw_up - base_nw_sens
                            
                            if d_up > d_down:
                                p_d, n_d = d_up, d_down
                                p_l = f"+{up_val}{unit}" if up_val > 0 else f"{up_val}{unit}"
                                n_l = f"{down_val}{unit}" if down_val < 0 else f"+{down_val}{unit}"
                            else:
                                p_d, n_d = d_down, d_up
                                p_l = f"{down_val}{unit}" if down_val < 0 else f"+{down_val}{unit}"
                                n_l = f"+{up_val}{unit}" if up_val > 0 else f"{up_val}{unit}"
                                
                            results.append({
                                "Parameter": name,
                                "Spread": abs(p_d - n_d),
                                "Pos_Delta": p_d,
                                "Neg_Delta": n_d,
                                "Pos_Label": p_l,
                                "Neg_Label": n_l
                            })
                            
                        results = sorted(results, key=lambda x: x['Spread'], reverse=False)
                        st.session_state['sens_results'] = results
                        
                if 'sens_results' in st.session_state and HAS_PLOTLY:
                    r_data = st.session_state['sens_results']
                    y_vals = [r['Parameter'] for r in r_data]
                    
                    fig_tor = go.Figure()
                    
                    fig_tor.add_trace(go.Bar(
                        y=y_vals, x=[r['Neg_Delta'] for r in r_data],
                        orientation='h', name='Downside Risk', marker_color='#ef4444',
                        text=[r['Neg_Label'] for r in r_data], textposition='inside', insidetextanchor='end',
                        hoverinfo='x+name'
                    ))
                    
                    fig_tor.add_trace(go.Bar(
                        y=y_vals, x=[r['Pos_Delta'] for r in r_data],
                        orientation='h', name='Upside Potential', marker_color='#10b981',
                        text=[r['Pos_Label'] for r in r_data], textposition='inside', insidetextanchor='start',
                        hoverinfo='x+name'
                    ))
                    
                    fig_tor.update_layout(
                        barmode='relative',
                        xaxis=dict(
                            title='Impact on Final Net Worth', 
                            tickformat='$,.0f', 
                            zeroline=True, 
                            zerolinecolor='#0f172a', 
                            zerolinewidth=2,
                            automargin=True
                        ),
                        yaxis=dict(
                            title='', 
                            automargin=True
                        ),
                        hovermode='y unified',
                        height=600,
                        margin=dict(l=20, r=80, t=50, b=80) 
                    )
                    fig_tor = apply_chart_theme(fig_tor, "Sensitivity Tornado Chart")
                    st.plotly_chart(fig_tor, use_container_width=True)

            # --- AND SAME FOR THIS ONE ---
            with out_tab_mc:
                st.markdown('<div class="info-text" style="margin-bottom: 10px;">💡 <strong>Stress Test Your Plan:</strong> The Monte Carlo simulation runs your exact plan through hundreds of randomized market scenarios to find your true probability of success.</div>', unsafe_allow_html=True)
                
                # --- FIX: Inject clear fiduciary guidance mapping allocation to volatility ---
                st.caption("🎯 **Volatility Guidance by Asset Allocation:** 100% Stocks ≈ **15%** | 80/20 Stocks/Bonds ≈ **12%** | 60/40 Stocks/Bonds ≈ **9%** | 40/60 Stocks/Bonds ≈ **7%**")
                st.markdown("<br>", unsafe_allow_html=True)

                col_mc1, col_mc2, col_mc3 = st.columns([1, 1, 2])
                mc_vol = col_mc1.number_input("Portfolio Volatility (%)", value=15.0, help="Adjust this based on your stock/bond ratio. Lower volatility narrows the spread of outcomes.")
                mc_runs = col_mc2.number_input("Number of Simulations", min_value=10, max_value=500, value=100, step=10)

                with col_mc3:
                    st.markdown("<div style='height: 27px;'></div>", unsafe_allow_html=True)
                    run_mc = st.button("✨ Run Monte Carlo Simulation", type="primary", use_container_width=True)

                if run_mc:
                    with st.spinner(f"Rendering {mc_runs} parallel market sequences (NumPy Vectorized & Thread-Safe)..."):
                        success_count = 0
                        all_nw_paths = []
                        mc_progress = st.progress(0)

                        years_count = sim_ctx['max_years'] + 1
                        
                        # Vectorized sequence generation
                        mc_matrix = np.random.normal(loc=sim_ctx['mkt'], scale=mc_vol, size=(mc_runs, years_count))
                        mc_matrix = np.maximum(-99.0, mc_matrix)
                        random_sequences = [tuple(row) for row in mc_matrix]

                        # --- FIX 1: Serialize context to a string to completely sever Streamlit proxy references ---
                        ctx_json_mc = json.dumps(sim_ctx)
                        
                        # --- FIX 2: Create an isolated worker that deserializes fresh memory per thread ---
                        def thread_worker(seq_tuple, ctx_str):
                            # json.loads guarantees 100% pure Python dicts with zero shared references
                            return run_simulation(list(seq_tuple), json.loads(ctx_str))

                        try:
                            with ThreadPoolExecutor(max_workers=min(mc_runs, 8)) as executor:
                                # Pass the pure JSON string and the worker function
                                futures = {executor.submit(thread_worker, seq, ctx_json_mc): i for i, seq in enumerate(random_sequences)}

                                for completed_idx, future in enumerate(concurrent.futures.as_completed(futures)):
                                    s_res, _, _, _ = future.result()
                                    res_mc = pd.DataFrame(s_res)
                                    
                                    if not res_mc.empty:
                                        nw_path = res_mc["Net Worth"].tolist()
                                        all_nw_paths.append(nw_path)
                                        if res_mc.iloc[-1].get("Unfunded Debt", 0) <= 0: success_count += 1
                                    if completed_idx % max(1, mc_runs // 20) == 0: mc_progress.progress(min(1.0, (completed_idx + 1) / mc_runs))
                        except Exception as e:
                            st.error(f"Simulation failed during multi-threading: {e}")
                        finally:
                            mc_progress.empty()

                        if all_nw_paths:
                            success_rate = (success_count / len(all_nw_paths)) * 100
                            st.session_state['mc_success_rate'] = success_rate

                            path_len = len(all_nw_paths[0])
                            current_year = datetime.date.today().year
                            years_list = [sim_ctx['current_year'] + i for i in range(path_len)]
                            
                            # --- FIX: Fully Vectorized NumPy Percentiles & Discounting ---
                            nw_array = np.array(all_nw_paths)
                            
                            p10_arr = np.percentile(nw_array, 10, axis=0)
                            p50_arr = np.percentile(nw_array, 50, axis=0)
                            p90_arr = np.percentile(nw_array, 90, axis=0)

                            # Vectorize the inflation discounting array as well
                            if view_todays_dollars:
                                discounts = (1 + sim_ctx['infl'] / 100) ** np.arange(path_len)
                                p10_arr = p10_arr / discounts
                                p50_arr = p50_arr / discounts
                                p90_arr = p90_arr / discounts

                            p10 = p10_arr.tolist()
                            p50 = p50_arr.tolist()
                            p90 = p90_arr.tolist()
                            # -------------------------------------------------------------

                            st.markdown(f"<h3 style='text-align: center; color: {'#10b981' if success_rate > 80 else '#f59e0b' if success_rate > 50 else '#f43f5e'};'>Probability of Success: {success_rate:.1f}%</h3>", unsafe_allow_html=True)

                            if HAS_PLOTLY:
                                fig_mc = go.Figure()
                                fig_mc.add_trace(go.Scatter(x=years_list, y=p90, mode='lines', name='90th Percentile (Favorable)', line=dict(color='#10b981', dash='dot')))
                                fig_mc.add_trace(go.Scatter(x=years_list, y=p50, mode='lines', name='50th Percentile (Median)', line=dict(color='#3b82f6', width=3)))
                                fig_mc.add_trace(go.Scatter(x=years_list, y=p10, mode='lines', name='10th Percentile (Severe)', line=dict(color='#f43f5e', dash='dot')))
                                fig_mc = apply_chart_theme(fig_mc, "Stochastic Net Worth Projections")
                                
                                # --- FIX: Expand top margin and center the legend so it doesn't clip on the right ---
                                fig_mc.update_layout(
                                    margin=dict(t=90), # Increase top margin from default 50 to 90
                                    legend=dict(
                                        x=0.5,         # Center horizontally
                                        xanchor='center',
                                        y=1.08,        # Push slightly higher above the grid
                                        yanchor='bottom'
                                    )
                                )
                                st.plotly_chart(fig_mc, use_container_width=True)

            with out_tab_tax:
                st.markdown('<div class="info-text" style="margin-bottom: 20px;">💡 <strong>Dual Tax Dashboard:</strong> The top chart visualizes your progressive tax brackets and how Roth Conversions (if enabled) fill those brackets. The bottom chart breaks down exactly what kind of taxes you are paying each year.</div>', unsafe_allow_html=True)

                if HAS_PLOTLY:
                    # Create a single figure with 2 rows that share the same X axis timeline
                    fig_tax = make_subplots(
                        rows=2, cols=1, 
                        shared_xaxes=True, 
                        vertical_spacing=0.1,
                        subplot_titles=("Roth Optimizer: Ordinary Income vs. Brackets", "Annual Tax Obligations Breakdown")
                    )

                    # --- ROW 1: Stacked Bars (Base Income + Roth Conversions) & Brackets ---
                    fig_tax.add_trace(go.Bar(x=df_det["Year"], y=df_det.get("Tax: Base Ordinary Income", [0]*len(df_det)), name="Base Ordinary Income", marker_color="#3b82f6", legendgroup="1"), row=1, col=1)
                    if "Roth Conversion Amount" in df_det.columns:
                        fig_tax.add_trace(go.Bar(x=df_det["Year"], y=df_det["Roth Conversion Amount"], name="Roth Conversions", marker_color="#8b5cf6", legendgroup="1"), row=1, col=1)

                    fig_tax.add_trace(go.Scatter(x=df_det["Year"], y=df_det.get("Tax: 0% Limit (Std Ded)", [0]*len(df_det)), mode="lines", name="Standard Deduction", line=dict(color="#94a3b8", dash="dot", width=2), legendgroup="1"), row=1, col=1)
                    fig_tax.add_trace(go.Scatter(x=df_det["Year"], y=df_det.get("Tax: 12% Limit", [0]*len(df_det)), mode="lines", name="12% Limit", line=dict(color="#10b981", dash="dot", width=2), legendgroup="1"), row=1, col=1)
                    fig_tax.add_trace(go.Scatter(x=df_det["Year"], y=df_det.get("Tax: 22% Limit", [0]*len(df_det)), mode="lines", name="22% Limit", line=dict(color="#f59e0b", dash="dot", width=2), legendgroup="1"), row=1, col=1)
                    fig_tax.add_trace(go.Scatter(x=df_det["Year"], y=df_det.get("Tax: 24% Limit", [0]*len(df_det)), mode="lines", name="24% Limit", line=dict(color="#ef4444", dash="dot", width=2), legendgroup="1"), row=1, col=1)
                    fig_tax.add_trace(go.Scatter(x=df_det["Year"], y=df_det.get("Tax: 32% Limit", [0]*len(df_det)), mode="lines", name="32% Limit", line=dict(color="#8b5cf6", dash="dot", width=2), legendgroup="1"), row=1, col=1)

                    # --- ROW 2: Tax Obligations Breakdown ---
                    tax_categories = [
                        ("Tax Breakdown: Federal", "#3b82f6", "Baseline Federal Tax"),
                        ("Tax Breakdown: State", "#0ea5e9", "Baseline State Tax"),
                        ("Tax Breakdown: Roth Conversion", "#a855f7", "Roth Conversion Taxes"), # <-- NEW PURPLE BAR
                        ("Tax Breakdown: FICA", "#10b981", "FICA (SS & Medicare)"),
                        ("Tax Breakdown: Withdrawals", "#f59e0b", "Cap Gains & Penalties"),
                        ("Expense: Medicare IRMAA Surcharge", "#ef4444", "Medicare IRMAA Surcharge")
                    ]

                    for col_key, color, name in tax_categories:
                        if col_key in df_det.columns:
                            fig_tax.add_trace(go.Bar(
                                x=df_det["Year"], 
                                y=df_det[col_key], 
                                name=name, 
                                marker_color=color,
                                legendgroup="2"
                            ), row=2, col=1)

                    # Ensure both rows stack the bars independently
                    # --- NEW: Link Subplots with a unified crosshair ---
                    fig_tax.update_layout(
                        barmode='stack', 
                        hovermode='x unified', 
                        height=800,
                        hoverdistance=-1,
                        spikedistance=-1
                    )
                    # Force a vertical line through both rows
                    fig_tax.update_xaxes(
                        showspikes=True, 
                        spikemode="across", 
                        spikesnap="cursor", 
                        showline=True, 
                        showgrid=True,
                        spikecolor="#94a3b8",
                        spikethickness=1,
                        spikedash="solid"
                    )
                    
                    fig_tax = apply_chart_theme(fig_tax)
                    st.plotly_chart(fig_tax, use_container_width=True)
                else:
                    st.info("Please install Plotly to view the charts.")

            with out_tab_logs:
                output = io.BytesIO()
                with pd.ExcelWriter(output, engine='openpyxl') as writer:
                    # --- NEW: Grab Tax Breakdowns for Export ---
                    tax_c = sorted([c for c in df_det.columns if c.startswith("Tax Breakdown:") or c.startswith("Tax: ")])
                    inc_c = sorted([c for c in df_det.columns if c.startswith("Income:") or c.startswith("Roth") or c.startswith("Cashflow:")])
                    exp_c = sorted([c for c in df_det.columns if c.startswith("Expense:")])
                    
                    # Combine all ordered columns
                    ord_det = ["Year", "Age (Primary)", "Age (Spouse)"] + inc_c + exp_c + tax_c + ["Net Savings"]
                    
                    # Filter existing columns to prevent KeyErrors
                    ord_det = [c for c in ord_det if c in df_det.columns]
                    
                    df_det[ord_det].to_excel(writer, sheet_name='Income_Expense_Log', index=False)

                    ast_c = sorted([c for c in df_nw.columns if c.startswith("Asset:")])
                    ord_nw = ["Year", "Age (Primary)", "Age (Spouse)"] + ast_c + ["Total Liquid Assets",
                                                                                  "Total Real Estate Equity",
                                                                                  "Total Business Equity",
                                                                                  "Total Debt Liabilities",
                                                                                  "Total Net Worth"]
                    df_nw[ord_nw].to_excel(writer, sheet_name='Net_Worth_Log', index=False)

                    flat_ctx = []
                    for k, v in sim_ctx.items():
                        if isinstance(v, (list, dict)):
                            flat_ctx.append({"Parameter": k, "Value": json.dumps(v)})
                        else:
                            flat_ctx.append({"Parameter": k, "Value": v})
                    pd.DataFrame(flat_ctx).to_excel(writer, sheet_name='System_State', index=False)

                st.download_button(label="📥 Download Full Simulation (.xlsx)", data=output.getvalue(),
                                   file_name='retirement_simulation.xlsx',
                                   mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                                   type="primary", use_container_width=True)

                t1, t2 = st.tabs(["Income & Expense Log", "Net Worth Log"])
                with t1: st.dataframe(df_det[ord_det].set_index("Year").style.format({c: "${:,.0f}" for c in ord_det if c not in ["Age (Primary)", "Age (Spouse)", "Year"]} | {"Age (Primary)": "{:.0f}", "Age (Spouse)": "{:.0f}"}), use_container_width=True)
                with t2:
                    st.dataframe(df_nw[ord_nw].set_index("Year").style.format(
                        {c: "${:,.0f}" for c in ord_nw if c not in ["Age (Primary)", "Age (Spouse)", "Year"]} | {
                            "Age (Primary)": "{:.0f}", "Age (Spouse)": "{:.0f}"}), use_container_width=True)


def render_ai():
    section_header("AI Fiduciary Health & What-If Simulator", "Analyze your cash flows chronologically to provide tactical, phase-by-phase advice.", "🤖")

    df_sim = st.session_state.get('df_sim_display')
    if df_sim is not None and not df_sim.empty:
        shortfall_mask = df_sim['Unfunded Debt'] > 0
        deplete_year = df_sim[shortfall_mask]['Year'].min() if not df_sim[shortfall_mask].empty else None
        my_age = relativedelta(datetime.date.today(), st.session_state.get('my_dob', datetime.date(1980, 1, 1))).years

        sim_summary = {
            "Current Age": my_age, "Retirement Age": st.session_state.get('ret_age', 65),
            "Life Expectancy": st.session_state.get('my_life_exp', 95),
            "Current Net Worth": df_sim.iloc[0]['Net Worth'], "Final Net Worth": df_sim.iloc[-1]['Net Worth'],
            "Shortfall Year": str(deplete_year) if deplete_year is not None else "None"
        }

        # --- NEW: Grab baseline assumptions for the AI ---
        sim_ctx = build_sim_context()
        ai_assumptions = {
            "Market Growth (%)": sim_ctx['mkt'],
            "General Inflation (%)": sim_ctx['infl'],
            "Healthcare Inflation (%)": sim_ctx['infl_hc'],
            "Education Inflation (%)": sim_ctx['infl_ed'],
            "Current State Tax Rate (%)": sim_ctx['cur_t'],
            "Retirement State Tax Rate (%)": sim_ctx['ret_t'],
            "Withdrawal Strategy": sim_ctx['active_withdrawal_strategy'],
            "Roth Conversions Optimizer": "Enabled" if sim_ctx['roth_conversions'] else "Disabled",
            "Target Roth Bracket": sim_ctx['roth_target']
        }

        timeline_summary = []
        for idx, row in df_sim.iloc[::5].iterrows():
            timeline_summary.append({
                "Age": int(row["Age (Primary)"]), "Income": int(row["Annual Income"]),
                "Expenses": int(row["Annual Expenses"]), "Taxes": int(row["Annual Taxes"]),
                "Liquid_Assets": int(row["Liquid Assets"]), "Net_Worth": int(row["Net Worth"])
            })
        last_row = df_sim.iloc[-1]
        timeline_summary.append({"Age": int(last_row["Age (Primary)"]), "Income": int(last_row["Annual Income"]), "Expenses": int(last_row["Annual Expenses"]), "Taxes": int(last_row["Annual Taxes"]), "Liquid_Assets": int(last_row["Liquid Assets"]), "Net_Worth": int(last_row["Net Worth"])})
    else:
        sim_summary, ai_assumptions, timeline_summary = {}, {}, []

    tab_report, tab_whatif = st.tabs(["📊 Comprehensive Health Report", "🔮 What-If Simulator"])
    
    with tab_report:
        if st.button("✨ Generate Comprehensive AI Report", type="primary", use_container_width=True, key="btn_report"):
            st.session_state['trigger_report_ai'] = True
            
        if st.session_state.get('trigger_report_ai'):
            if check_ai_rate_limit():
                if sim_summary:
                    try:
                        with st.spinner("AI extracting timeseries data and acting as fiduciary advisor..."):
                            # --- FIX: Injected ai_assumptions into the prompt ---
                            prompt = f"""Act as an expert fiduciary financial planner. Review this user's summary: {json.dumps(sim_summary)}, their core economic & strategic assumptions: {json.dumps(ai_assumptions)}, and their chronological 5-year cash flow progression: {json.dumps(timeline_summary)}. 

                            Provide a highly detailed, year-by-year or phase-by-phase tactical analysis based strictly on these parameters. 

                            CRITICAL INSTRUCTION: You MUST include a distinct, bolded section titled "Roth Conversion Strategy Blueprint". In this section, provide actionable, mathematical advice on EXACTLY when they should execute Roth conversions. For example: "Between ages X and Y, convert $Z per year to fill the 24% bracket before RMDs begin at age 75." Be as specific as possible using their exact numbers and tax data.

                            Format your response in clean Markdown."""

                            res = call_gemini_text(prompt)
                            if res:
                                st.session_state['ai_analysis_report'] = res
                                mark_dirty()
                            else:
                                st.error("⚠️ AI Analysis failed to generate.")
                    finally:
                        st.session_state['trigger_report_ai'] = False
                        st.rerun()
                else:
                    st.warning("Please run the baseline simulation first on the Dashboard or Simulation tab.")
                    st.session_state['trigger_report_ai'] = False

        if 'ai_analysis_report' in st.session_state:
            st.markdown(st.session_state['ai_analysis_report'].replace('$', '&#36;'), unsafe_allow_html=True)

    with tab_whatif:
        what_if_query = st.text_area("Ask the AI to simulate a scenario (e.g., 'What if I sold my rental property in 2030 and put the cash in my brokerage?' or 'What if inflation hits 5% for the next decade?')", key="what_if_text")
        
        if st.button("✨ Run What-If Analysis (AI)", type="primary", use_container_width=True, key="btn_whatif"):
            st.session_state['trigger_whatif_ai'] = True

        if st.session_state.get('trigger_whatif_ai'):
            if check_ai_rate_limit():
                if sim_summary and what_if_query:
                    try:
                        with st.spinner("AI processing alternative timelines and computing what-if scenario..."):
                            # --- FIX: Injected ai_assumptions into the prompt ---
                            prompt = f"Act as an expert fiduciary financial planner. Review this user's baseline simulation summary: {json.dumps(sim_summary)}, their baseline economic assumptions: {json.dumps(ai_assumptions)}, and their chronological 5-year cash flow progression: {json.dumps(timeline_summary)}. The user wants to run the following 'what-if' scenario: '{what_if_query}'. Analyze how this change would mathematically and strategically impact their net worth, cash flow, and tax strategy compared to their baseline assumptions. Provide a highly detailed, reasonable estimate and tactical breakdown of this scenario. Format your response in clean Markdown."
                            res = call_gemini_text(prompt)
                            if res:
                                st.session_state['what_if_analysis_report'] = res
                                mark_dirty()
                            else:
                                st.error("⚠️ AI Analysis failed to generate.")
                    finally:
                        st.session_state['trigger_whatif_ai'] = False
                        st.rerun()
                elif not what_if_query:
                    st.warning("Please enter a scenario to simulate.")
                    st.session_state['trigger_whatif_ai'] = False
                else:
                    st.warning("Please run the baseline simulation first.")
                    st.session_state['trigger_whatif_ai'] = False

        if 'what_if_analysis_report' in st.session_state:
            st.markdown(st.session_state['what_if_analysis_report'].replace('$', '&#36;'), unsafe_allow_html=True)


def render_faq():
    section_header("Complete Beginner's Guide & FAQ", "Everything you need to know about the engine.", "📖")

    st.subheader("🌟 GETTING STARTED")
    
    with st.expander("What exactly does this app do, and how is it different from a basic retirement calculator?"):
        st.markdown("""
        Most retirement calculators ask you two questions — "how much do you have saved?" and "when do you want to retire?" — then spit out a single number. This app is fundamentally different. It builds a living, breathing financial model of your entire life — from today until the end of your life expectancy — and simulates every dollar coming in and going out, year by year. 
        
        It accounts for things a basic calculator completely ignores: your mortgage paying itself off over time, your kids going to college, your taxes changing when you retire, Social Security kicking in, Medicare starting at 65, your investment accounts being drawn down in the smartest possible tax order, and hundreds of other real-life events. The result isn't just a number — it's a full financial roadmap with warnings, milestones, and actionable advice.
        """)

    with st.expander("Is this app a replacement for a financial advisor?"):
        st.markdown("""
        No, and it's important to be honest about that. This app is an incredibly powerful planning and education tool — it helps you understand your own numbers, test scenarios, and have much smarter conversations with professionals. 
        
        But it is not a licensed fiduciary advisor and cannot account for every personal circumstance, recent law change, or market condition. Think of it the way you'd think of WebMD: extremely useful for understanding what's happening with your health, but you still want a doctor making the final call on major decisions. Use this app to get clarity, then bring your printouts to a CPA or Certified Financial Planner (CFP) for personalized guidance.
        """)

    with st.expander("How accurate are these projections?"):
        st.markdown("""
        The projections are as accurate as the information you put in, combined with the assumptions you choose. The simulation engine uses real IRS tax brackets, real Social Security claiming rules, and real Medicare cost structures. 
        
        However, it cannot predict the future — no tool can. What it can do is show you the most mathematically likely outcomes based on historical data and your personal numbers. The Monte Carlo feature is specifically designed to stress-test your plan against hundreds of unpredictable futures so you understand your real risk, not just the rosy average-case scenario.
        """)

    st.subheader("👨‍👩‍👧‍👦 YOUR PROFILE & FAMILY")
    
    with st.expander("Why does the app need my exact date of birth instead of just my age?"):
        st.markdown("""
        Because a few months can actually matter quite a bit in retirement planning. Your exact birth year determines three important things:

        * **Your Social Security Full Retirement Age (FRA):** If you were born in 1960 or later, your FRA is 67. Born between 1955-1959, it's somewhere between 66 and 67. This is the age at which you receive your full Social Security benefit — claim earlier and you get permanently less, claim later and you get permanently more.
        * **When your RMDs begin:** RMD stands for Required Minimum Distribution. The IRS forces you to start withdrawing money from your traditional retirement accounts at a certain age — either 73 or 75, depending on your birth year. Getting this wrong by even one year can create a surprise tax bill.
        * **Your IRS Catch-Up Contribution eligibility:** Once you turn 50, you're allowed to contribute extra money to your 401(k) and IRA above the normal limits. The app needs your age to know when to apply this bonus.
        """)

    with st.expander("What changes when I add a spouse?"):
        st.markdown("""
        Quite a lot, actually. Adding a spouse unlocks a completely different set of tax rules that are generally more favorable:

        * **Married Filing Jointly (MFJ)** tax brackets are roughly double the single brackets, meaning you pay a lower tax rate on the same income.
        * **Standard Deduction** doubles from approximately $14,600 to $29,200.
        * **Social Security survivor benefits** activate — if one spouse passes away, the surviving spouse automatically inherits the higher of the two Social Security benefit amounts.
        * **Retirement account strategies** change — the app can now optimize withdrawals across two people's accounts.
        * **Lifestyle cost reduction** — the simulation realistically models that a surviving spouse spends less (roughly 60% of the couple's former expenses) after the other passes.
        """)

    with st.expander("What are 'dependents' used for in the simulation?"):
        st.markdown("""
        The app uses your children's ages to automatically time several important financial events. It models when child-related expenses (extracurricular activities, higher grocery bills, larger utility costs) naturally phase out as each child grows up and leaves home. 
        
        It also uses their ages to calculate exactly when college tuition expenses should start and end in your cash flow. If you have a 529 college savings plan, the app connects it directly to your specific child's tuition costs, drawing down that account automatically when the bills arrive.
        """)

    st.subheader("💵 INCOME & SOCIAL SECURITY")
    
    with st.expander("What is an 'employer 401(k) match' and why is it listed under income instead of savings?"):
        st.markdown("""
        An employer match is essentially free money your company adds to your retirement account when you contribute your own money. For example, your employer might match 50 cents for every dollar you put in, up to 6% of your salary. 
        
        It's listed under "Income" purely so the app can track it separately from your own contributions. This is important because employer match money goes directly into your 401(k) and is never spendable take-home cash. The app is careful to never count it as money you can spend on bills, but it does add it to your growing 401(k) balance behind the scenes so your retirement account grows accurately.
        """)

    with st.expander("What is Social Security, and how does the app calculate my benefit?"):
        st.markdown("""
        Social Security is a federal retirement program you've been paying into your entire working life through payroll taxes. When you retire, you receive a monthly payment for life based on your 35 highest-earning years of work history. 
        
        The critical decision the app helps you model is when to claim:
        * **Claim at 62 (earliest possible):** Your benefit is permanently reduced by up to 30%.
        * **Claim at your Full Retirement Age (66-67):** You receive 100% of your earned benefit.
        * **Claim at 70 (latest recommended):** Your benefit is permanently increased by up to 24%.

        There is no single "right" answer — it depends on your health, other income sources, and whether you're married. The app lets you test different claiming ages to see the lifetime impact.
        """)

    with st.expander("What does 'taxable Social Security' mean? I thought Social Security wasn't taxed?"):
        st.markdown("""
        This is one of the most surprising things people discover about retirement. Social Security can be taxed — up to 85% of your benefit can be added to your taxable income depending on how much other income you have. 
        
        The IRS uses something called "Provisional Income" (basically your other income plus half your Social Security) to determine how much of your benefit is taxable. If your Provisional Income is low enough, your Social Security is completely tax-free. As your other income (from RMDs, investments, rent, etc.) rises, more of your Social Security becomes taxable. The app calculates this automatically every single year using the exact IRS formula.
        """)

    st.subheader("🏦 ASSETS, ACCOUNTS & INVESTING")
    
    with st.expander("What is the difference between all these account types? (401k, IRA, Roth, Brokerage...)"):
        st.markdown("""
        This is one of the most important concepts to understand. All of these are just containers that hold your investments — the difference is purely about when the IRS taxes the money inside them. 
        
        * **Traditional 401(k) and Traditional IRA ("Pay taxes later"):** You put money in before paying taxes. Your money grows tax-free. When you withdraw it in retirement, you pay income taxes on every dollar you take out. The IRS forces you to start withdrawing at age 73 or 75 (RMDs).
        * **Roth 401(k) and Roth IRA ("Pay taxes now, never again"):** You put money in after already paying taxes on it. Your money grows tax-free and you can withdraw it in retirement completely tax-free, with no RMDs ever required.
        * **Brokerage (Taxable) Account ("Pay taxes as you go"):** A regular investment account with no special tax protection. You pay taxes on dividends and capital gains. However, there are no contribution limits and no restrictions on withdrawals. 
        * **HSA (Health Savings Account) ("Triple tax advantage"):** You contribute pre-tax, it grows tax-free, and withdrawals for medical expenses are tax-free. After age 65, you can withdraw for any reason (paying normal income tax).
        * **529 Plan ("Tax-free college savings"):** Money grows tax-free and can be withdrawn completely tax-free when used for qualified education expenses.
        """)

    with st.expander("How does the app decide which account to withdraw from first? (Withdrawal Strategy)"):
        st.markdown("""
        When your expenses exceed your income, you have a "shortfall" and must sell investments. The order in which you sell them has massive tax implications. The engine allows you to choose between two strategies in the "Stress Tests & Taxes" tab:
        
        * **Standard Strategy (Default):** The engine drains liquid cash first, then Taxable Brokerage accounts (paying Capital Gains), then Traditional 401(k)s/IRAs (paying Income Tax), and finally leaves your tax-free Roth accounts to grow for as long as possible.
        * **Roth Preferred:** The engine drains liquid cash, then Taxable Brokerages, but then flips the script: it drains Roth accounts completely *before* touching Traditional 401(k)s. This is sometimes preferred by early retirees trying to keep their taxable income artificially low to qualify for healthcare subsidies.
        """)

    with st.expander("What happens if my plan runs out of money? (Shortfall Borrowing Rate)"):
        st.markdown("""
        If your expenses completely drain all of your liquid assets, brokerage accounts, and retirement accounts, the app does not simply break or stop calculating. 
        
        Instead, it enters **"Liquidity Crisis"** mode. It tracks the missing money as "Unfunded Debt." Because you still need to pay your bills, the engine assumes you must borrow this money via personal loans or credit cards. The Unfunded Debt will compound aggressively year over year based on the **Shortfall Borrowing Rate** you set in your Macro Assumptions (defaulting to 12%). This provides a realistic, mathematically punishing look at what happens when a retiree outlives their money.
        """)

    with st.expander("What are 'RMDs' and why do they matter so much?"):
        st.markdown("""
        RMD stands for Required Minimum Distribution. The IRS has a simple rule: you cannot keep money in a Traditional 401(k) or IRA forever. Starting at age 73 (or 75 if you were born in 1960 or later), you must withdraw a minimum amount every single year, whether you need the money or not. 
        
        Why do they matter? Because every dollar you're forced to withdraw is added to your taxable income that year — which can push you into a higher tax bracket, cause more of your Social Security to become taxable, and trigger Medicare surcharges (IRMAA). 
        """)

    st.subheader("🏡 REAL ESTATE & MORTGAGES")
    
    with st.expander("How does the app handle my mortgage?"):
        st.markdown("""
        You simply enter three things: your current loan balance, your interest rate, and your monthly payment. The app then does something most calculators don't — it mathematically pays down your mortgage month by month, exactly as your bank would, separating out the interest and principal portions correctly. 
        
        When the balance finally hits zero, the mortgage expense automatically disappears from your cash flow for every future year. **You should NOT separately list your mortgage payment in the budget section** — the app handles it entirely through your real estate entry.
        """)

    with st.expander("What's the difference between a 'primary residence' and an 'investment property' in the app?"):
        st.markdown("""
        The app treats these completely differently:
        * **Your primary residence** is where you live. Its mortgage payment and monthly expenses (property taxes, insurance, HOA) flow out as living costs. 
        * **An investment property** is treated as a business. The app calculates the net cash flow — rent collected minus mortgage payment minus expenses — and only the net profit or loss affects your overall cash flow. This prevents investment properties from artificially inflating your apparent lifestyle income.
        """)

    st.subheader("💸 BUDGETS & EXPENSES")
    
    with st.expander("The expense table has 'Start Phase' and 'End Phase' — what do these mean?"):
        st.markdown("""
        These control exactly when each expense is active in your lifetime simulation:

        * **"Now"** means the expense starts today and continues until whatever end phase you choose.
        * **"At Retirement"** means the expense either starts when you retire (like a new travel budget) or ends when you retire (like your work commute costs).
        * **"End of Life"** means the expense continues until the very last year of your simulation.
        * **"Custom Year"** lets you enter a specific year — useful for things like college tuition or a car payment that ends in 2027.

        *A critical rule:* If an expense changes at retirement (like your grocery bill going down slightly), you should create TWO rows — one that goes from "Now" to "At Retirement," and a separate one that goes from "At Retirement" to "End of Life" with the new amount.
        """)

    with st.expander("How does healthcare inflation work, and why is it different from regular inflation?"):
        st.markdown("""
        Regular consumer inflation (food, clothing, electronics, etc.) has historically averaged around 2-3% per year. Healthcare costs, however, have consistently risen at 5-7% per year for decades — nearly double the overall inflation rate. 
        
        This means a healthcare expense that costs $500/month today might cost over $1,300/month in 20 years. The app applies a separate, higher inflation rate specifically to anything categorized as "Healthcare" or "Insurance" to capture this reality. 
        """)

    st.subheader("🏥 HEALTHCARE & MEDICARE")
    
    with st.expander("What is the 'Pre-Medicare Gap' and why is it so expensive?"):
        st.markdown("""
        If you retire before age 65, you face a potentially brutal financial gap. Most working Americans get health insurance through their employer. The moment you retire, that coverage ends. You're now on your own for health insurance until Medicare kicks in at age 65.
        
        Buying private health insurance for a 60-year-old can easily cost $1,000-$2,000+ per month just in premiums. This is called the "Pre-Medicare Gap" and it catches many early retirees completely off guard. The app automatically adds this cost to your simulation if you retire before 65, scaled to your income level.
        """)

    with st.expander("What is 'IRMAA' and why might I have to pay extra for Medicare?"):
        st.markdown("""
        IRMAA stands for Income-Related Monthly Adjustment Amount. It's essentially a Medicare "high earner surcharge." If your income in retirement exceeds certain thresholds (starting around $103,000/year for singles or $206,000/year for couples in 2026 dollars), the government charges you extra for your Medicare premiums. 
        
        The sneaky part: the income the government uses to calculate IRMAA includes your RMDs, Social Security, investment income, and Roth conversions. The app calculates and applies IRMAA automatically every year based on your projected income — which you can see isolated as a red bar on the Tax Breakdown chart.
        """)

    with st.expander("What is 'Long-Term Care' and why is it in the stress tests?"):
        st.markdown("""
        Long-Term Care (LTC) refers to extended help with daily activities — bathing, dressing, eating — typically needed in the final years of life due to illness or cognitive decline. This care is extremely expensive: a private nursing home room in the US costs $90,000-$120,000+ per year on average, and this is almost entirely NOT covered by regular Medicare.
        
        The "LTC Shock" stress test injects a massive medical expense into the final 2-3 years of your simulation to show what happens to your plan if you or a spouse needs this level of care. 
        """)

    st.subheader("📊 TAXES & ROTH CONVERSIONS")
    
    with st.expander("What is a 'progressive' tax system? Why don't I just multiply my income by my tax rate?"):
        st.markdown("""
        The US uses a system where higher income is taxed at progressively higher rates — but crucially, only the portion of your income that falls within each "bracket" is taxed at that bracket's rate. 
        
        Think of it like filling buckets. The first $23,200 of a married couple's income fills the 10% bucket. The next chunk fills the 12% bucket, and so on. Only income above the highest bracket threshold gets taxed at the top rate. This is why your actual tax bill is almost always less than your bracket rate would suggest.
        """)
        
    with st.expander("How do I read the 'Tax Obligations Breakdown' chart?"):
        st.markdown("""
        Because taxes are often the single largest expense in retirement, the app separates them out so you can see exactly where your money is going:
        
        * **Baseline Federal & State Tax:** Your normal income taxes.
        * **FICA (SS & Medicare):** Payroll taxes. You'll notice these completely disappear the year you retire.
        * **Roth Conversion Taxes:** The extra income tax you volunteered to pay to move money from a Traditional to a Roth account.
        * **Cap Gains & Penalties:** Taxes paid when you are forced to sell Brokerage assets, or the 10% IRS penalty if you withdraw from a 401(k) before age 59.5.
        * **Medicare IRMAA Surcharge:** The hidden high-income Medicare penalty. 
        """)

    with st.expander("What is a Roth conversion and how does the Optimizer work?"):
        st.markdown("""
        A Roth conversion is a deliberate decision to move money from your Traditional 401(k) (where you'll owe taxes when you withdraw) into a Roth IRA (where all future growth and withdrawals are tax-free). You pay the income taxes on the converted amount now, in the current year.
        
        **How the Optimizer Works:** When enabled, it identifies the years where your taxable income is below your chosen target bracket (e.g., the 24% bracket). It calculates exactly how much money it can convert to fill that bracket up to the brim, checks if you have enough liquid cash to pay the tax bill, and executes the conversion automatically. 
        """)

    st.subheader("📈 AI, SENSITIVITY & MONTE CARLO SIMULATION")
    
    with st.expander("How do the AI Fiduciary Report and What-If Simulator work?"):
        st.markdown("""
        Instead of generic financial advice, the AI tab passes your *exact* mathematical timeline, your asset balances, and your specific macroeconomic assumptions to an advanced Large Language Model (Gemini).
        
        * **The Comprehensive Report** acts as a fiduciary planner reviewing your baseline file. It will tell you specifically when your highest tax years are, warn you of impending IRMAA cliffs, and lay out an exact blueprint for Roth Conversions.
        * **The What-If Simulator** allows you to type natural language scenarios ("What if I sold my rental property in 2030 and put the cash in my brokerage?"). The AI calculates how that alters your trajectory compared to your baseline plan.
        """)

    with st.expander("What is the Sensitivity 'Tornado' Chart?"):
        st.markdown("""
        A Sensitivity Analysis tests how fragile your plan is to a single variable changing. 
        
        The Tornado Chart runs 10 alternate dimensions of your life simultaneously—tweaking Inflation by 1%, dropping Market Returns by 1%, retiring 2 years early, etc.—and measures how much each tweak changes your Final Net Worth. It is sorted from the biggest impact at the top to the smallest at the bottom. This helps you figure out what you actually need to worry about (e.g., "Market returns matter a lot, but real estate growth barely moves the needle for me").
        """)
        
    with st.expander("What is Sequence of Returns Risk?"):
        st.markdown("""
        This is one of the most misunderstood retirement risks. The order in which you experience market returns matters enormously once you're withdrawing from your portfolio. 
        
        If the market crashes 40% in your first year of retirement and you're forced to sell investments to cover living expenses, you're locking in permanent losses at the worst possible time. Even if the market recovers beautifully over the next decade, you have fewer shares left to benefit from that recovery. The "-25% Market Crash at Retirement" stress test directly simulates this risk.
        """)

    with st.expander("What is Monte Carlo simulation in plain English?"):
        st.markdown("""
        Imagine running your retirement plan 200 times in parallel, but each time the stock market behaves differently — sometimes great, sometimes terrible, sometimes mediocre. 
        
        Monte Carlo simulation takes your real financial plan and runs it through hundreds of randomly generated market scenarios based on historical volatility. The result is a "probability of success" percentage. An 85% success rate means that in 85 out of 100 randomly generated futures, your money lasted until the end of your life expectancy.
        """)

    st.subheader("💾 SAVING & SECURITY")
    
    with st.expander("Is my financial data safe?"):
        st.markdown("""
        Your data is stored in Google Firebase — one of the most secure cloud storage platforms in the world. Your account is protected by email and password authentication. 
        
        That said, no online system is completely immune to risk. This app is a planning tool — **you do not need to enter actual account numbers, social security numbers, or banking credentials anywhere**. Only use estimated balances and income figures.
        """)

    with st.expander("What happens to my data if I use 'Guest Mode'?"):
        st.markdown("""
        In Guest Mode, everything you enter exists only in your current browser session. The moment you close the browser tab or refresh the page, all of your data is permanently gone. Guest Mode is great for a quick exploration of the app's features (which is why it pre-loads the fictional "Johnson Family"), but if you want to save your plan and return to it later, you need to create a free account and click the "Save" button.
        """)


# --- PAGE ROUTING & EXECUTION ---
PAGES = {
    "🏠 Dashboard": render_dashboard,
    "👤 Profile & Family": render_profile,
    "💵 Income Streams": render_income,
    "🏦 Assets & Debts": render_assets,
    "💸 Cash Flows": render_cashflows,
    "📈 Simulation": render_simulation,
    "🤖 AI Advisor": render_ai,
    "📖 User Guide & FAQ": render_faq
}

with st.sidebar:
    st.markdown("<h2 style='text-align: center; color: white; font-family: Inter;'>🏦 Pro Planner</h2>",
                unsafe_allow_html=True)
    st.markdown("<br>", unsafe_allow_html=True)

    status = get_completion_status()
    nav_options = []
    for page_name in list(PAGES.keys()):
        if "Profile" in page_name:
            nav_options.append(f"{'✅ ' if status['profile'] else ''}{page_name}")
        elif "Income" in page_name:
            nav_options.append(f"{'✅ ' if status['income'] else ''}{page_name}")
        elif "Assets" in page_name:
            nav_options.append(f"{'✅ ' if status['assets'] else ''}{page_name}")
        elif "Cash Flows" in page_name:
            nav_options.append(f"{'✅ ' if status['expenses'] else ''}{page_name}")
        else:
            nav_options.append(page_name)

    current_active_idx = 0
    curr_page = st.session_state.get('current_page', '🏠 Dashboard')
    
    for i, opt in enumerate(nav_options):
        clean_opt = opt.replace("✅ ", "")
        if curr_page == clean_opt:
            current_active_idx = i
            break

    selected_nav_item = st.radio("Navigation", nav_options, index=current_active_idx, label_visibility="collapsed")
    clean_page_name = selected_nav_item.replace("✅ ", "")
    
    # --- FIX: Only update state and force a rerun if the USER clicked the radio button ---
    # This prevents the radio widget's old internal state from overriding dashboard button clicks!
    if clean_page_name != curr_page:
        st.session_state['current_page'] = clean_page_name
        st.rerun()

    st.markdown("<br>", unsafe_allow_html=True)

    save_btn_label = "⚠️ Save Changes" if st.session_state.get('dirty', False) else "🚀 Save Profile"
    if st.button(save_btn_label, type="primary", use_container_width=True):
        save_profile()

    if st.button("Logout", type="secondary", use_container_width=True):
        if cookie_manager.get("user_email"): 
            cookie_manager.delete("user_email")
            
        # --- FIX: Preserve app-level state, safely nuke user data & triggers ---
        system_keys = {'firebase_enabled', 'logged_out_flag'}
        for key in list(st.session_state.keys()):
            # Preserve system flags and the invisible cookie manager component state
            if key not in system_keys and not key.startswith('auth_cookie'):
                del st.session_state[key]
                
        st.session_state['logged_out_flag'] = True
        time.sleep(0.5)
        st.rerun()

if st.session_state.get('current_page') in PAGES:
    PAGES[st.session_state['current_page']]()