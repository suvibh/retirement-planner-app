import streamlit as st
import pandas as pd
import requests
import json
import datetime
import warnings
import re
import firebase_admin
from firebase_admin import credentials, firestore

try:
    import plotly.graph_objects as go
    HAS_PLOTLY = True
except ImportError:
    HAS_PLOTLY = False

# --- SILENCE PANDAS WARNINGS CAUSED BY STREAMLIT DATA EDITOR ---
warnings.simplefilter(action='ignore', category=FutureWarning)

# --- NEW: Import the Cookie Manager for persistent sessions ---
try:
    import extra_streamlit_components as stx
except ImportError:
    st.error("Missing library for keeping you logged in. Please run this in your terminal:\npip install extra-streamlit-components")
    st.stop()

st.set_page_config(page_title="Retirement Planner", layout="wide")

# --- 1. FIREBASE INITIALIZATION ---
if not firebase_admin._apps:
    try:
        # 1. Cloud Deployment (Streamlit Secrets)
        if "firebase" in st.secrets:
            cred_dict = dict(st.secrets["firebase"])
            # Safely force literal \n characters to act as actual newlines for the private key
            cred_dict["private_key"] = cred_dict["private_key"].replace("\\n", "\n")
            cred = credentials.Certificate(cred_dict)
            firebase_admin.initialize_app(cred)
            
        # 2. Local Testing (Physical File)
        else:
            cred = credentials.Certificate('firebase_creds.json')
            firebase_admin.initialize_app(cred)
            
    except Exception as e:
        st.error(f"🚨 Firebase failed to initialize: {e}")
        st.stop() # Immediately halts the app so you don't get ugly Traceback text

# Safely connect to the database now that we know initialization succeeded
try:
    db = firestore.client()
except Exception as e:
    st.error(f"🚨 Failed to connect to Firestore: {e}")
    st.stop()

FIREBASE_WEB_API_KEY = st.secrets.get("FIREBASE_WEB_API_KEY", "")
GEMINI_API_KEY = st.secrets.get("GEMINI_API_KEY", "")

# Initialize Cookie Manager
cookie_manager = stx.CookieManager()

# --- 2. AUTH & DATA LOAD FUNCTIONS ---
def sign_up_with_email_and_password(email, password):
    url = f"https://identitytoolkit.googleapis.com/v1/accounts:signUp?key={FIREBASE_WEB_API_KEY}"
    payload = {"email": email, "password": password, "returnSecureToken": True}
    return requests.post(url, json=payload).json()

def sign_in_with_email_and_password(email, password):
    url = f"https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key={FIREBASE_WEB_API_KEY}"
    payload = {"email": email, "password": password, "returnSecureToken": True}
    return requests.post(url, json=payload).json()

def load_user_data(email):
    # Guest mode bypasses the database
    if email == "guest_demo":
        return {}
    doc = db.collection('users').document(email).get()
    if doc.exists:
        return doc.to_dict()
    return {}

# --- AI HELPER FUNCTION (GEMINI) ---
def call_gemini_json(prompt):
    if not GEMINI_API_KEY:
        st.error("Missing GEMINI_API_KEY in .streamlit/secrets.toml")
        return None
        
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-3-flash-preview:generateContent?key={GEMINI_API_KEY}"
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseMimeType": "application/json"
        }
    }
    try:
        res = requests.post(url, json=payload).json()
        
        if "error" in res:
            st.error(f"API Error: {res['error'].get('message', res['error'])}")
            return None
            
        candidates = res.get('candidates', [])
        if not candidates:
            return None
            
        content = candidates[0].get('content', {})
        parts = content.get('parts', [])
        
        if not parts:
            return None
            
        text = parts[0].get('text', '')
        
        if text:
            text = text.strip()
            if text.startswith("```"):
                text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
                text = re.sub(r"\n?```$", "", text)
                text = text.strip()
            
            try:
                parsed_data = json.loads(text)
            except json.JSONDecodeError:
                # Try to extract array or object using regex fallback
                match_arr = re.search(r'\[.*\]', text, re.DOTALL)
                match_obj = re.search(r'\{.*\}', text, re.DOTALL)
                try:
                    if match_arr and (not match_obj or text.find('[') < text.find('{')):
                        parsed_data = json.loads(match_arr.group(0))
                    elif match_obj:
                        parsed_data = json.loads(match_obj.group(0))
                    else:
                        return None
                except:
                    return None
            
            # If the AI hallucinated a dict wrapping a list (e.g. {"events": [...]}), extract it
            if isinstance(parsed_data, dict) and len(parsed_data) == 1:
                first_val = list(parsed_data.values())[0]
                if isinstance(first_val, list):
                    return first_val
                
            return parsed_data
        else:
            return None
            
    except Exception as e:
        st.error(f"AI Generation Error: {e}")
        return None

# --- 3. SESSION HANDLING (LOGIN) ---
if 'user_email' not in st.session_state:
    saved_email = cookie_manager.get(cookie="user_email")
    if saved_email:
        st.session_state['user_email'] = saved_email
        st.session_state['user_data'] = load_user_data(saved_email)
        st.rerun()
        
    st.title("🔒 Retirement Planner Portal")
    tab1, tab2 = st.tabs(["Login", "Sign Up"])
    with tab1:
        st.subheader("Welcome Back")
        login_email = st.text_input("Email", key="login_email")
        login_password = st.text_input("Password", type="password", key="login_password")
        if st.button("Login", type="primary"):
            res = sign_in_with_email_and_password(login_email, login_password)
            if "idToken" in res:
                st.session_state['user_email'] = res['email']
                st.session_state['user_data'] = load_user_data(res['email'])
                expire_date = datetime.datetime.now() + datetime.timedelta(days=30)
                cookie_manager.set("user_email", res['email'], expires_at=expire_date)
                st.rerun()
            else: st.error("Login failed. Check credentials.")
    with tab2:
        st.subheader("Create an Account")
        signup_email = st.text_input("Email", key="signup_email")
        signup_password = st.text_input("Password", type="password", key="signup_password")
        if st.button("Sign Up"):
            if len(signup_password) >= 6:
                res = sign_up_with_email_and_password(signup_email, signup_password)
                if "idToken" in res:
                    st.session_state['user_email'] = res['email']
                    st.session_state['user_data'] = {} 
                    expire_date = datetime.datetime.now() + datetime.timedelta(days=30)
                    cookie_manager.set("user_email", res['email'], expires_at=expire_date)
                    st.success("Account created!")
                    st.rerun()
                else:
                    st.error("Sign up failed. Email might already exist.")
            else:
                st.warning("Password must be at least 6 characters.")
                
    st.divider()
    st.markdown("### 👀 Just looking around?")
    if st.button("🚀 Try Demo (No Account Required)", width="stretch"):
        st.session_state['user_email'] = "guest_demo"
        st.session_state['user_data'] = {} 
        st.rerun()
        
    st.caption("Disclaimer: This tool is for educational and simulation purposes only and does not constitute formal financial advice.")
    st.stop()

# --- 4. MAIN INTERFACE LOGIC & ONBOARDING ---
if 'onboarding_shown' not in st.session_state:
    st.toast("Welcome! Try clicking the ✨ AI buttons to auto-fill your budget and assumptions.", icon="🤖")
    st.session_state['onboarding_shown'] = True

st.sidebar.success(f"Logged in as: \n**{st.session_state['user_email']}**")
if st.sidebar.button("Log Out"):
    if cookie_manager.get("user_email"):
        cookie_manager.delete("user_email") 
    st.session_state.clear() 
    st.rerun()

st.sidebar.divider()
st.sidebar.caption("Privacy & Settings")
if st.sidebar.button("🚨 Delete Account & Data"):
    if st.session_state['user_email'] != "guest_demo":
        try:
            db.collection('users').document(st.session_state['user_email']).delete()
        except:
            pass
    if cookie_manager.get("user_email"):
        cookie_manager.delete("user_email")
    st.session_state.clear()
    st.rerun()

ud = st.session_state.get('user_data', {})
p_info = ud.get('personal_info', {})
saved_kids = p_info.get('kids', [])

if 'current_expenses' not in st.session_state: st.session_state['current_expenses'] = ud.get('current_expenses', [])
if 'retire_expenses' not in st.session_state: st.session_state['retire_expenses'] = ud.get('retire_expenses', [])
if 'one_time_events' not in st.session_state: st.session_state['one_time_events'] = ud.get('one_time_events', [])
if 'assumptions' not in st.session_state:
    st.session_state['assumptions'] = ud.get('assumptions', {
        "inflation": 3.0, "market_growth": 7.0, "income_growth": 3.0, 
        "property_growth": 3.0, "rent_growth": 3.0, "current_tax_rate": 22.0, "retire_tax_rate": 15.0,
        "rmd_age": 75 # SECURE 2.0 Default
    })

def set_city(input_key, city_name): st.session_state[input_key] = city_name

def city_autocomplete(label, key_prefix, default_val=""):
    input_key = f"{key_prefix}_input"
    if input_key not in st.session_state: st.session_state[input_key] = default_val

    current_val = st.text_input(label, key=input_key)

    if current_val and len(current_val) > 2 and current_val != default_val:
        try:
            api_key = st.secrets.get("GOOGLE_MAPS_API_KEY", "")
            if api_key:
                url = f"[https://maps.googleapis.com/maps/api/place/autocomplete/json?input=](https://maps.googleapis.com/maps/api/place/autocomplete/json?input=){current_val}&types=(cities)&key={api_key}"
                res = requests.get(url).json()
                if res.get("status") == "OK":
                    predictions = res.get("predictions", [])
                    exact_match = any(current_val == p["description"] for p in predictions)
                    if not exact_match:
                        st.caption("Suggestions:")
                        for pred in predictions[:3]:
                            st.button(
                                pred["description"], 
                                key=f"{key_prefix}_{pred['place_id']}",
                                on_click=set_city,
                                args=(input_key, pred["description"])
                            )
        except Exception:
            pass 
    return current_val

def render_total(label, text):
    st.markdown(f"<div style='text-align: right; font-weight: 600; color: #4b5563; font-size: 1.05rem; padding-top: 5px;'>{label}: <span style='color: #111827;'>{text}</span></div>", unsafe_allow_html=True)

# ==========================================
#              THE UI SECTIONS
# ==========================================
st.title("🚀 Retirement Planner")

save_requested = False

# --- CUSTOM CSS FOR PRETTY EXPANDERS USING :has() ---
st.markdown("""
<style>
    [data-testid="stExpander"] { border-radius: 10px !important; box-shadow: 0px 2px 6px rgba(0,0,0,0.05) !important; margin-bottom: 15px !important; border: 1px solid rgba(128, 128, 128, 0.2) !important; }
    [data-testid="stExpander"] summary p { font-weight: 700 !important; font-size: 1.15rem !important; }
    [data-testid="stExpander"]:has(.card-1) { background: linear-gradient(to right, rgba(59, 130, 246, 0.08), transparent); border-left: 6px solid #3b82f6 !important; }
    [data-testid="stExpander"]:has(.card-2) { background: linear-gradient(to right, rgba(16, 185, 129, 0.08), transparent); border-left: 6px solid #10b981 !important; }
    [data-testid="stExpander"]:has(.card-3) { background: linear-gradient(to right, rgba(139, 92, 246, 0.08), transparent); border-left: 6px solid #8b5cf6 !important; }
    [data-testid="stExpander"]:has(.card-4) { background: linear-gradient(to right, rgba(20, 184, 166, 0.08), transparent); border-left: 6px solid #14b8a6 !important; }
    [data-testid="stExpander"]:has(.card-5) { background: linear-gradient(to right, rgba(244, 63, 94, 0.08), transparent); border-left: 6px solid #f43f5e !important; }
    [data-testid="stExpander"]:has(.card-6) { background: linear-gradient(to right, rgba(245, 158, 11, 0.08), transparent); border-left: 6px solid #f59e0b !important; }
    [data-testid="stExpander"]:has(.card-7) { background: linear-gradient(to right, rgba(6, 182, 212, 0.08), transparent); border-left: 6px solid #06b6d4 !important; }
    [data-testid="stExpander"]:has(.card-8) { background: linear-gradient(to right, rgba(100, 116, 139, 0.08), transparent); border-left: 6px solid #64748b !important; }
    [data-testid="stExpander"]:has(.card-9) { background: linear-gradient(to right, rgba(79, 70, 229, 0.08), transparent); border-left: 6px solid #4f46e5 !important; }
</style>
""", unsafe_allow_html=True)

# --- SECTION 1: PERSONAL INFO ---
with st.expander("👨‍👩‍👧‍👦 1. Personal & Family Info", expanded=False):
    st.markdown('<div class="card-1"></div>', unsafe_allow_html=True)
    col1, col2 = st.columns(2)
    with col1:
        st.subheader("You")
        my_name = st.text_input("Name", value=p_info.get('name', ''), placeholder="Enter name")
        my_age = st.number_input("Your Age", 18, 100, p_info.get('age', 40))
        
    with col2:
        st.subheader("Current Location")
        curr_city = city_autocomplete("Current City", "curr_city", default_val=p_info.get('current_city', ''))

    st.divider()
    has_spouse = st.checkbox("Do you have a Spouse or Partner?", value=p_info.get('has_spouse', False))
    spouse_name = ""
    spouse_age = 0
    if has_spouse:
        c1, c2 = st.columns(2)
        spouse_name = c1.text_input("Spouse/Partner Name", value=p_info.get('spouse_name', ''))
        spouse_age = c2.number_input("Spouse Age", 18, 100, p_info.get('spouse_age', 40))

    st.divider()
    num_kids = st.number_input("Number of Children/Dependents", 0, 10, len(saved_kids))
    kids_data = []
    if num_kids > 0:
        st.write("**Children Details**")
        for i in range(num_kids):
            kcol1, kcol2 = st.columns([3, 1])
            saved_k_name = saved_kids[i]['name'] if i < len(saved_kids) else ""
            saved_k_age = saved_kids[i]['age'] if i < len(saved_kids) else 5
            k_name = kcol1.text_input(f"Child {i+1} Name", value=saved_k_name, key=f"k_name_{i}")
            k_age = kcol2.number_input(f"Age", 0, 25, saved_k_age, key=f"k_age_{i}")
            kids_data.append({"name": k_name, "age": k_age})
            
    st.divider()
    if st.button("💾 Save Profile", key="save_1"): save_requested = True

# --- SECTION 2: INCOME ---
with st.expander("💵 2. Annual Income Streams", expanded=False):
    st.markdown('<div class="card-2"></div>', unsafe_allow_html=True)
    inc_cats = ["Base Salary (W-2)", "Bonus / Commission", "Employer Match (401k/HSA)", "Equity / RSUs", "Side Gig / Freelance (1099)", "Dividends", "Interest", "Pension / Annuity", "Social Security", "Alimony / Child Support", "VA Benefits", "Other"]
    owners = ["Me", "Spouse", "Joint"]
    
    saved_income = ud.get('income', [])
    if saved_income:
        df_income = pd.DataFrame(saved_income)
        if "Override Growth (%)" not in df_income.columns: df_income["Override Growth (%)"] = None
        if "Start Age" not in df_income.columns: df_income["Start Age"] = my_age
        if "End Age" not in df_income.columns: df_income["End Age"] = p_info.get('retire_age', 65)
    else:
        df_income = pd.DataFrame([{"Description": "", "Category": "Base Salary (W-2)", "Owner": "Me", "Annual Amount ($)": 0, "Start Age": my_age, "End Age": p_info.get('retire_age', 65), "Override Growth (%)": None}])
        
    edited_income_df = st.data_editor(
        df_income,
        column_config={
            "Description": st.column_config.TextColumn("Description (e.g., 'My Salary', 'Book Royalties')"),
            "Category": st.column_config.SelectboxColumn("Category", options=inc_cats),
            "Owner": st.column_config.SelectboxColumn("Owner", options=owners),
            "Annual Amount ($)": st.column_config.NumberColumn("Annual Amount ($)", min_value=0, step=1000, format="$%d"),
            "Start Age": st.column_config.NumberColumn("Start Age", min_value=18, max_value=100, help="When this income stream begins"),
            "End Age": st.column_config.NumberColumn("End Age", min_value=18, max_value=100, help="When this income stream ends (e.g., retirement)"),
            "Override Growth (%)": st.column_config.NumberColumn("Override Growth (%)", step=0.5, format="%.1f%%", help="Leave blank to use global default income growth")
        },
        num_rows="dynamic", width="stretch", hide_index=True, key="inc_editor"
    )
    
    total_inc = edited_income_df["Annual Amount ($)"].sum()
    render_total("Total Annual Income", f"${total_inc:,.0f}")
    
    if st.button("💾 Save Profile", key="save_2"): save_requested = True

# --- SECTION 3: REAL ESTATE & BUSINESS ---
with st.expander("🏢 3. Real Estate & Business Ownership", expanded=False):
    st.markdown('<div class="card-3"></div>', unsafe_allow_html=True)
    st.subheader("Real Estate Portfolio")
    saved_re = ud.get('real_estate', [])
    
    if saved_re:
        df_re = pd.DataFrame(saved_re)
        if "Is Primary Residence?" not in df_re.columns: df_re.insert(1, "Is Primary Residence?", False)
        if "Mortgage Payment ($)" not in df_re.columns: df_re.insert(5, "Mortgage Payment ($)", 0.0)
        if "Override Prop Growth (%)" not in df_re.columns: df_re["Override Prop Growth (%)"] = None
        if "Override Rent Growth (%)" not in df_re.columns: df_re["Override Rent Growth (%)"] = None
    else:
        df_re = pd.DataFrame([{"Property Name": "", "Is Primary Residence?": False, "Market Value ($)": 0, "Mortgage Balance ($)": 0, "Interest Rate (%)": 0.0, "Mortgage Payment ($)": 0, "Monthly Rent ($)": 0, "Monthly Expenses ($)": 0, "Override Prop Growth (%)": None, "Override Rent Growth (%)": None}])
        
    edited_re_df = st.data_editor(
        df_re,
        column_config={
            "Property Name": st.column_config.TextColumn("Property Name"),
            "Is Primary Residence?": st.column_config.CheckboxColumn("Primary Residence?", default=False),
            "Market Value ($)": st.column_config.NumberColumn("Market Value ($)", step=10000, format="$%d"),
            "Mortgage Balance ($)": st.column_config.NumberColumn("Mortgage Balance ($)", step=10000, format="$%d"),
            "Interest Rate (%)": st.column_config.NumberColumn("Interest Rate (%)", step=0.1, format="%.2f%%"),
            "Mortgage Payment ($)": st.column_config.NumberColumn("Mortgage Payment ($)", step=100, format="$%d", help="Your required monthly P&I payment"),
            "Monthly Rent ($)": st.column_config.NumberColumn("Monthly Rent ($)", step=100, format="$%d", help="Leave $0 if primary residence"),
            "Monthly Expenses ($)": st.column_config.NumberColumn("Monthly Expenses ($)", step=100, format="$%d", help="HOA, Property Taxes, Insurance"),
            "Override Prop Growth (%)": st.column_config.NumberColumn("Override Prop Growth (%)", step=0.5, format="%.1f%%", help="Leave blank to use default"),
            "Override Rent Growth (%)": st.column_config.NumberColumn("Override Rent Growth (%)", step=0.5, format="%.1f%%", help="Leave blank to use default")
        },
        num_rows="dynamic", width="stretch", hide_index=True, key="re_editor"
    )
    
    total_re_val = pd.to_numeric(edited_re_df["Market Value ($)"], errors='coerce').fillna(0).sum()
    total_re_equity = total_re_val - pd.to_numeric(edited_re_df["Mortgage Balance ($)"], errors='coerce').fillna(0).sum()
    
    # Strictly calculate Net Rental Income for non-primary properties only
    investment_re_df = edited_re_df[edited_re_df["Is Primary Residence?"] == False]
    inv_rent = pd.to_numeric(investment_re_df["Monthly Rent ($)"], errors='coerce').fillna(0).sum()
    inv_pmt = pd.to_numeric(investment_re_df["Mortgage Payment ($)"], errors='coerce').fillna(0).sum()
    inv_exp = pd.to_numeric(investment_re_df["Monthly Expenses ($)"], errors='coerce').fillna(0).sum()
    net_rental_income = inv_rent - inv_pmt - inv_exp
    
    render_total("Real Estate Portfolio", f"Value: ${total_re_val:,.0f} &nbsp;|&nbsp; Equity: ${total_re_equity:,.0f} &nbsp;|&nbsp; Net Mo. Income: ${net_rental_income:,.0f}")

    st.divider()
    st.subheader("Private Business Equity")
    saved_biz = ud.get('business', [])
    df_biz = pd.DataFrame(saved_biz) if saved_biz else pd.DataFrame([{"Business Name": "", "Total Valuation ($)": 0, "Your Ownership (%)": 100, "Annual Distribution ($)": 0}])
    edited_biz_df = st.data_editor(
        df_biz,
        column_config={
            "Business Name": st.column_config.TextColumn("Business Name"),
            "Total Valuation ($)": st.column_config.NumberColumn("Total Valuation ($)", step=10000, format="$%d"),
            "Your Ownership (%)": st.column_config.NumberColumn("Your Ownership (%)", min_value=0, max_value=100, step=1),
            "Annual Distribution ($)": st.column_config.NumberColumn("Annual Distribution ($)", step=1000, format="$%d")
        },
        num_rows="dynamic", width="stretch", hide_index=True, key="biz_editor"
    )
    
    total_biz_eq = (pd.to_numeric(edited_biz_df["Total Valuation ($)"], errors='coerce').fillna(0) * (pd.to_numeric(edited_biz_df["Your Ownership (%)"], errors='coerce').fillna(0) / 100)).sum()
    render_total("Total Business Equity", f"${total_biz_eq:,.0f}")

    st.divider()
    if st.button("💾 Save Profile", key="save_3"): save_requested = True

# --- SECTION 4: LIQUID ASSETS & DEBT ---
with st.expander("🏦 4. Liquid Assets & Debt", expanded=False):
    st.markdown('<div class="card-4"></div>', unsafe_allow_html=True)
    st.subheader("Liquid Assets & Investments")
    asset_cats = ["Checking/Savings", "HYSA", "Brokerage (Taxable)", "Traditional 401k/IRA", "Roth 401k/IRA", "HSA", "Crypto", "529 Plan", "Other"]
    owners = ["Me", "Spouse", "Joint"]
    saved_assets = ud.get('liquid_assets', [])
    df_assets = pd.DataFrame(saved_assets) if saved_assets else pd.DataFrame([{"Account Name": "", "Type": "Traditional 401k/IRA", "Owner": "Me", "Current Balance ($)": 0, "Annual Contribution ($)": 0, "Est. Annual Growth (%)": 7.0}])
    
    if "Annual Contribution ($)" not in df_assets.columns:
        df_assets.insert(4, "Annual Contribution ($)", 0)

    edited_assets_df = st.data_editor(
        df_assets,
        column_config={
            "Account Name": st.column_config.TextColumn("Account Name (e.g., 'Vanguard 401k')"),
            "Type": st.column_config.SelectboxColumn("Account Type", options=asset_cats),
            "Owner": st.column_config.SelectboxColumn("Owner", options=owners),
            "Current Balance ($)": st.column_config.NumberColumn("Current Balance ($)", step=5000, format="$%d"),
            "Annual Contribution ($)": st.column_config.NumberColumn("Annual Contribution ($)", step=1000, format="$%d", help="How much you actively save into this account per year"),
            "Est. Annual Growth (%)": st.column_config.NumberColumn("Est. Annual Growth (%)", step=0.5, format="%.1f%%")
        },
        num_rows="dynamic", width="stretch", hide_index=True, key="assets_editor"
    )
    
    total_liquid = pd.to_numeric(edited_assets_df["Current Balance ($)"], errors='coerce').fillna(0).sum()
    render_total("Total Liquid Assets", f"${total_liquid:,.0f}")

    st.divider()
    st.subheader("Debt & Liabilities (Excluding Mortgages)")
    debt_cats = ["Credit Card", "Student Loan", "Auto Loan", "Personal Loan", "Medical Debt", "Other"]
    saved_debt = ud.get('liabilities', [])
    df_debt = pd.DataFrame(saved_debt) if saved_debt else pd.DataFrame([{"Debt Name": "", "Type": "Student Loan", "Current Balance ($)": 0, "Interest Rate (%)": 0.0, "Monthly Payment ($)": 0}])
    edited_debt_df = st.data_editor(
        df_debt,
        column_config={
            "Debt Name": st.column_config.TextColumn("Debt Name (e.g., 'Chase Sapphire')"),
            "Type": st.column_config.SelectboxColumn("Debt Type", options=debt_cats),
            "Current Balance ($)": st.column_config.NumberColumn("Current Balance ($)", step=1000, format="$%d"),
            "Interest Rate (%)": st.column_config.NumberColumn("Interest Rate (%)", step=0.1, format="%.2f%%"),
            "Monthly Payment ($)": st.column_config.NumberColumn("Min. Monthly Payment ($)", step=50, format="$%d")
        },
        num_rows="dynamic", width="stretch", hide_index=True, key="debt_editor"
    )
    
    total_debt_bal = pd.to_numeric(edited_debt_df["Current Balance ($)"], errors='coerce').fillna(0).sum()
    total_debt_pmts = pd.to_numeric(edited_debt_df["Monthly Payment ($)"], errors='coerce').fillna(0).sum()
    render_total("Total Debt", f"Balance: ${total_debt_bal:,.0f} &nbsp;|&nbsp; Mo. Payments: ${total_debt_pmts:,.0f}")

    st.divider()
    if st.button("💾 Save Profile", key="save_4"): save_requested = True

# ==========================================
# PREPARE SMART CONTEXT FOR AI (Housing, Kids, Debt)
# ==========================================

kids_context = "No children."
if kids_data:
    ages = [str(k['age']) for k in kids_data]
    kids_context = f"Household includes {len(kids_data)} child(ren), ages: {', '.join(ages)}. CRITICAL: You MUST factor in high full-time daycare/preschool costs for children under 5, and standard school/activity costs for older kids."

valid_debts = [d for d in edited_debt_df.to_dict('records') if str(d.get("Debt Name", "")) != ""]
debt_context = "No known debt payments."
if valid_debts:
    debt_strs = [f"- {d['Type']} ({d['Debt Name']}): ${d['Monthly Payment ($)']}/month" for d in valid_debts]
    debt_context = "CRITICAL: The user has the following EXACT monthly debt obligations. You MUST include these exact amounts as line items in your JSON output under the 'Debt Payments' category:\n" + "\n".join(debt_strs)

housing_context_current = "They currently rent their primary residence. Estimate a realistic 'Housing' rent payment for their city."
housing_context_retire = "Assume they will rent a property in retirement unless otherwise noted."

if not edited_re_df.empty and "Is Primary Residence?" in edited_re_df.columns:
    primary_homes = edited_re_df[edited_re_df["Is Primary Residence?"] == True]
    if not primary_homes.empty:
        primary_mortgage_pmt = pd.to_numeric(primary_homes["Mortgage Payment ($)"], errors='coerce').fillna(0).sum()
        primary_exp = pd.to_numeric(primary_homes["Monthly Expenses ($)"], errors='coerce').fillna(0).sum()
        total_housing_cost = primary_mortgage_pmt + primary_exp
        
        housing_context_current = f"CRITICAL HOUSING OVERRIDE: They OWN their primary residence. Their total combined monthly mortgage payment + fixed property expenses (HOA, Tax, Ins) is EXACTLY ${total_housing_cost:,.0f}/month. You MUST use EXACTLY ${total_housing_cost:,.0f} for their 'Housing' category. Do NOT deviate from this number."
        housing_context_retire = f"They currently OWN their primary residence. Consider if they will carry a mortgage into retirement or downsize based on retiring in the planned city. Adjust housing expenses accordingly based on general property values."

# --- SECTION 5: CURRENT EXPENSES (AI DRIVEN) ---
with st.expander("💸 5. Current Expenses & AI Budget Builder", expanded=False):
    st.markdown('<div class="card-5"></div>', unsafe_allow_html=True)
    
    saved_curr_exp = st.session_state['current_expenses']
    if saved_curr_exp:
        df_curr_exp = pd.DataFrame(saved_curr_exp)
        if "AI Estimate?" not in df_curr_exp.columns:
            df_curr_exp["AI Estimate?"] = False
    else:
        df_curr_exp = pd.DataFrame([{"Description": "", "Category": "Housing", "Frequency": "Monthly", "Amount ($)": 0, "AI Estimate?": False}])
        
    df_curr_exp["AI Estimate?"] = df_curr_exp["AI Estimate?"].fillna(False).astype(bool)

    edited_curr_exp_df = st.data_editor(
        df_curr_exp,
        column_config={
            "Description": st.column_config.TextColumn("Description"),
            "Category": st.column_config.SelectboxColumn("Category", options=["Housing", "Transportation", "Food", "Utilities", "Insurance", "Healthcare", "Entertainment", "Education", "Debt Payments", "Other"]),
            "Frequency": st.column_config.SelectboxColumn("Frequency", options=["Monthly", "Yearly"]),
            "Amount ($)": st.column_config.NumberColumn("Amount ($)", step=100, format="$%d"),
            "AI Estimate?": st.column_config.CheckboxColumn("🤖 AI Estimate?", default=False, help="Uncheck to lock this row. Locked rows are never overwritten by the AI.")
        },
        num_rows="dynamic", width="stretch", hide_index=True, key="curr_exp_editor"
    )
    
    curr_monthly_sum = pd.to_numeric(edited_curr_exp_df[edited_curr_exp_df["Frequency"] == "Monthly"]["Amount ($)"], errors='coerce').fillna(0).sum()
    curr_yearly_to_monthly = pd.to_numeric(edited_curr_exp_df[edited_curr_exp_df["Frequency"] == "Yearly"]["Amount ($)"], errors='coerce').fillna(0).sum() / 12
    curr_total_monthly = curr_monthly_sum + curr_yearly_to_monthly
    render_total("Est. Total Monthly Expenses", f"${curr_total_monthly:,.0f} / mo")

    if st.button("✨ Auto-Estimate Missing Expenses (AI)"):
        with st.spinner("Analyzing cost of living to complete your budget..."):
            valid_rows_df = edited_curr_exp_df[edited_curr_exp_df["Description"].astype(str) != ""].copy()
            valid_rows_df["Amount ($)"] = pd.to_numeric(valid_rows_df["Amount ($)"], errors='coerce').fillna(0)
            valid_rows_df["AI Estimate?"] = valid_rows_df["AI Estimate?"].fillna(False).astype(bool)
            user_rows = valid_rows_df.to_dict('records')
            
            city_context = curr_city if curr_city else "a typical US city"
            prompt = f"""
            You are an expert financial planner building a realistic monthly budget.
            User Profile: Household of {1 + (1 if has_spouse else 0) + len(kids_data)} people living in {city_context}.
            
            USER DATA INJECTIONS:
            1. {kids_context}
            2. {housing_context_current}
            3. {debt_context}
            
            Here is a JSON array of the user's CURRENT expenses list (some rows may be incomplete):
            {json.dumps(user_rows)}
            
            Rules for updating the list:
            1. If an item has "Amount ($)" as 0 OR "AI Estimate?" is true, predict a realistic cost in today's dollars based on the "Description", AND assign it the most accurate "Category". Set "AI Estimate?" to true.
            2. If an item has "Amount ($)" > 0 AND "AI Estimate?" is false, DO NOT change the "Amount ($)". Ensure its "Category" is accurate. Keep "AI Estimate?" as false.
            3. Generate any critically MISSING standard expenses required to form a fully complete budget (e.g., if Groceries or Utilities are missing, add them). Set their "AI Estimate?" to true.
            
            Return ONLY a JSON array of the ENTIRE updated list of objects strictly matching this schema:
            "Description" (string)
            "Category" (string, MUST be exactly one of: "Housing", "Transportation", "Food", "Utilities", "Insurance", "Healthcare", "Entertainment", "Education", "Debt Payments", "Other")
            "Frequency" (string, exactly one of: Monthly, Yearly)
            "Amount ($)" (number)
            "AI Estimate?" (boolean)
            """
            result = call_gemini_json(prompt)
            if result is not None:
                st.session_state['current_expenses'] = result
                if 'curr_exp_editor' in st.session_state:
                    del st.session_state['curr_exp_editor']
                st.rerun()

    st.divider()
    if st.button("💾 Save Profile", key="save_5"): save_requested = True

# --- SECTION 6: MILESTONES & TIME-BOUND EVENTS ---
with st.expander("🎉 6. Milestones & Personalized AI timelines", expanded=False):
    st.markdown('<div class="card-6"></div>', unsafe_allow_html=True)
    st.write("Plan for large occurrences (buying a car) or medium-term phases (like 4 years of college tuition).")
    
    saved_events = st.session_state.get('one_time_events', [])
    formatted_events = []
    
    for ev in saved_events:
        new_ev = ev.copy()
        if "Expected Year" in new_ev:
            new_ev["Start Date (MM/YYYY)"] = f"01/{new_ev['Expected Year']}"
            new_ev["End Date (MM/YYYY)"] = ""
            new_ev["Frequency"] = "One-Time"
            del new_ev["Expected Year"]
        if "Frequency" not in new_ev: new_ev["Frequency"] = "One-Time"
        if "Start Date (MM/YYYY)" not in new_ev: new_ev["Start Date (MM/YYYY)"] = ""
        if "End Date (MM/YYYY)" not in new_ev: new_ev["End Date (MM/YYYY)"] = ""
        if "AI Estimate?" not in new_ev: new_ev["AI Estimate?"] = False
        formatted_events.append(new_ev)

    current_year = datetime.date.today().year
    current_month = datetime.date.today().month
    default_date = f"{current_month:02d}/{current_year}"

    df_events = pd.DataFrame(formatted_events) if formatted_events else pd.DataFrame([
        {"Description": "", "Type": "Expense", "Frequency": "One-Time", "Amount ($)": 0, "Start Date (MM/YYYY)": default_date, "End Date (MM/YYYY)": "", "AI Estimate?": False}
    ])
    
    df_events["AI Estimate?"] = df_events["AI Estimate?"].fillna(False).astype(bool)

    edited_events_df = st.data_editor(
        df_events,
        column_config={
            "Description": st.column_config.TextColumn("Event Description"),
            "Type": st.column_config.SelectboxColumn("Type", options=["Expense", "Income / Windfall"]),
            "Frequency": st.column_config.SelectboxColumn("Frequency", options=["One-Time", "Monthly", "Yearly"]),
            "Amount ($)": st.column_config.NumberColumn("Amount ($)", step=1000, format="$%d"),
            "Start Date (MM/YYYY)": st.column_config.TextColumn("Start (MM/YYYY)", validate=r"^(0?[1-9]|1[0-2])\/[0-9]{4}$"),
            "End Date (MM/YYYY)": st.column_config.TextColumn("End (MM/YYYY)", validate=r"^(0?[1-9]|1[0-2])\/[0-9]{4}$"),
            "AI Estimate?": st.column_config.CheckboxColumn("🤖 AI Estimate?", default=False, help="Uncheck to lock this row from AI overwrites.")
        },
        num_rows="dynamic", width="stretch", hide_index=True, key="events_editor"
    )
    
    total_event_cost = pd.to_numeric(edited_events_df[edited_events_df["Type"] == "Expense"]["Amount ($)"], errors='coerce').fillna(0).sum()
    render_total("Total Milestone Costs", f"${total_event_cost:,.0f}")

    if st.button("✨ Auto-Estimate Unlocked Event Costs (AI)", key="estimate_events_ai"):
        valid_events_df = edited_events_df[edited_events_df["Description"].astype(str) != ""].copy()
        valid_events_df["Amount ($)"] = pd.to_numeric(valid_events_df["Amount ($)"], errors='coerce').fillna(0)
        valid_events_df["AI Estimate?"] = valid_events_df["AI Estimate?"].fillna(False).astype(bool)
        valid_events = valid_events_df.to_dict('records')
        
        if not valid_events:
            st.warning("Please add at least one event with a Description first!")
        else:
            with st.spinner("Researching realistic costs and timelines for your milestones..."):
                city_context = curr_city if curr_city else "a typical US city"
                events_json = json.dumps(valid_events)
                
                family_members = [f"User (Age {my_age})"]
                if has_spouse:
                    family_members.append(f"Spouse {spouse_name} (Age {spouse_age})")
                for i, k in enumerate(kids_data):
                    k_name = k.get('name') or f"Child {i+1}"
                    family_members.append(f"{k_name} (Age {k['age']})")
                family_context_str = ", ".join(family_members)
                
                prompt = f"""
                You are an expert financial planner. The current date is {current_month:02d}/{current_year}. The user lives in {city_context}.
                
                FAMILY CONTEXT (Ages are as of current year {current_year}):
                {family_context_str}
                
                Here is a JSON array of their planned future life events:
                {events_json}
                
                CRITICAL INSTRUCTIONS FOR UPDATING ROWS:
                1. Read the "Description" carefully. Match any names to the FAMILY CONTEXT to determine how many years in the future the event will occur.
                   - College starts around age 18. Weddings around age 28.
                2. If "Amount ($)" is 0 OR "AI Estimate?" is true, completely OVERWRITE the following fields based on the nature of the event:
                   - "Type": Must be "Expense" (for costs) or "Income / Windfall" (for inheritance, selling business).
                   - "Frequency": "One-Time" (wedding, car, remodel), "Monthly" (braces), or "Yearly" (college tuition).
                   - "Start Date (MM/YYYY)": Calculate the exact future month and year (MUST INCLUDE LEADING ZERO FOR MONTH, e.g., '09/2030').
                   - "End Date (MM/YYYY)": If Frequency is Yearly/Monthly, calculate the end date. If Frequency is One-Time, this MUST be an empty string "".
                   - "Amount ($)": Provide a highly realistic estimate in today's dollars for {city_context}.
                   - Set "AI Estimate?" to true.
                3. For rows where "Amount ($)" > 0 AND "AI Estimate?" is false: DO NOT modify them at all.
                
                Return ONLY a raw JSON array. DO NOT wrap it in ```json blocks. Strictly match this exact schema:
                [
                  {{
                    "Description": "string",
                    "Type": "string",
                    "Frequency": "string",
                    "Amount ($)": number,
                    "Start Date (MM/YYYY)": "string",
                    "End Date (MM/YYYY)": "string",
                    "AI Estimate?": boolean
                  }}
                ]
                """
                result = call_gemini_json(prompt)
                if result is not None:
                    st.session_state['one_time_events'] = result
                    if 'events_editor' in st.session_state:
                        del st.session_state['events_editor']
                    st.rerun()

    st.divider()
    if st.button("💾 Save Profile", key="save_6"): save_requested = True

# --- 7. RETIREMENT SIMULATION & ASSUMPTIONS ---
with st.expander("🔮 7. Retirement Simulation & Assumptions", expanded=False):
    st.markdown('<div class="card-7"></div>', unsafe_allow_html=True)
    
    st.subheader("Timeline & Location Planning")
    
    c_age1, c_age2, c_age3 = st.columns(3)
    with c_age1:
        retire_age = st.slider("Your Target Retirement Age", min_value=int(my_age), max_value=100, value=max(int(my_age), int(p_info.get('retire_age', 65))))
        
    spouse_retire_age = None
    if has_spouse:
        with c_age2:
            spouse_retire_age = st.slider("Spouse Target Retirement Age", min_value=int(spouse_age), max_value=100, value=max(int(spouse_age), int(p_info.get('spouse_retire_age', 65))))
    
    with c_age3:
        life_expectancy = st.slider("Plan Life Expectancy (End Age)", min_value=70, max_value=120, value=int(p_info.get('life_expectancy', 95)))
            
    retire_city = city_autocomplete("Planned Retirement City", "retire_city", default_val=ud.get('retire_city', curr_city))
    if st.checkbox("Same as current city", value=(retire_city == curr_city)):
        retire_city = curr_city

    st.divider()
    st.subheader("Global Future Assumptions")
    st.write("These rates will be applied globally to your projections unless you specifically overrode them in the individual asset/income tables above.")
    
    if st.button("✨ Auto-Estimate Assumptions (AI)", key="estimate_assumptions_btn"):
        with st.spinner("Analyzing macroeconomic data..."):
            retire_context = retire_city if retire_city else "a typical US city"
            prompt = f"""
            You are a top-tier macroeconomic analyst. Based on historical data and future outlooks for {retire_context}, 
            suggest realistic long-term percentage rates for financial planning.
            Return ONLY a JSON object with these exact keys (numbers only, e.g. 3.5):
            "inflation", "market_growth", "income_growth", "property_growth", "rent_growth"
            """
            result = call_gemini_json(prompt)
            if result:
                # Fallback in case the AI inexplicably wraps the object in an array
                if isinstance(result, list) and len(result) > 0: result = result[0]
                
                if isinstance(result, dict):
                    st.session_state['assumptions'].update({
                        "inflation": float(result.get("inflation", 3.0)),
                        "market_growth": float(result.get("market_growth", 7.0)),
                        "income_growth": float(result.get("income_growth", 3.0)),
                        "property_growth": float(result.get("property_growth", 3.0)),
                        "rent_growth": float(result.get("rent_growth", 3.0))
                    })
                    st.rerun()
                
    c1, c2, c3 = st.columns(3)
    inflation_rate = c1.number_input("Est. Average Inflation (%)", value=float(st.session_state['assumptions'].get('inflation', 3.0)), step=0.5)
    market_growth = c2.number_input("Est. Market Portfolio Growth (%)", value=float(st.session_state['assumptions'].get('market_growth', 7.0)), step=0.5)
    income_growth = c3.number_input("Default Income Growth (%)", value=float(st.session_state['assumptions'].get('income_growth', 3.0)), step=0.5)

    c4, c5, _ = st.columns(3)
    property_growth = c4.number_input("Default Property Value Growth (%)", value=float(st.session_state['assumptions'].get('property_growth', 3.0)), step=0.5)
    rent_growth = c5.number_input("Default Rent Growth (%)", value=float(st.session_state['assumptions'].get('rent_growth', 3.0)), step=0.5)
    
    st.divider()
    st.subheader("Simulated Retirement Expenses")
    st.write(f"What will life cost when you stop working in **{retire_city}**?")
    
    saved_ret_exp = st.session_state['retire_expenses']
    if saved_ret_exp:
        df_ret_exp = pd.DataFrame(saved_ret_exp)
        if "AI Estimate?" not in df_ret_exp.columns:
            df_ret_exp["AI Estimate?"] = False
    else:
        df_ret_exp = pd.DataFrame([{"Description": "", "Category": "Housing", "Frequency": "Monthly", "Amount ($)": 0, "AI Estimate?": False}])
        
    df_ret_exp["AI Estimate?"] = df_ret_exp["AI Estimate?"].fillna(False).astype(bool)

    edited_ret_exp_df = st.data_editor(
        df_ret_exp,
        column_config={
            "Description": st.column_config.TextColumn("Description"),
            "Category": st.column_config.SelectboxColumn("Category", options=["Housing", "Transportation", "Food", "Utilities", "Insurance", "Healthcare", "Entertainment", "Education", "Debt Payments", "Other"]),
            "Frequency": st.column_config.SelectboxColumn("Frequency", options=["Monthly", "Yearly"]),
            "Amount ($)": st.column_config.NumberColumn("Amount ($)", step=100, format="$%d"),
            "AI Estimate?": st.column_config.CheckboxColumn("🤖 AI Estimate?", default=False, help="Uncheck to lock this row from being overwritten by the AI.")
        },
        num_rows="dynamic", width="stretch", hide_index=True, key="ret_exp_editor"
    )
    
    ret_monthly_sum = pd.to_numeric(edited_ret_exp_df[edited_ret_exp_df["Frequency"] == "Monthly"]["Amount ($)"], errors='coerce').fillna(0).sum()
    ret_yearly_to_monthly = pd.to_numeric(edited_ret_exp_df[edited_ret_exp_df["Frequency"] == "Yearly"]["Amount ($)"], errors='coerce').fillna(0).sum() / 12
    ret_total_monthly = ret_monthly_sum + ret_yearly_to_monthly
    render_total("Est. Retirement Monthly Expenses", f"${ret_total_monthly:,.0f} / mo")

    if st.button("✨ Auto-Estimate Missing Retirement Expenses (AI)"):
        with st.spinner("Simulating retirement landscape..."):
            valid_rows_df = edited_ret_exp_df[edited_ret_exp_df["Description"].astype(str) != ""].copy()
            valid_rows_df["Amount ($)"] = pd.to_numeric(valid_rows_df["Amount ($)"], errors='coerce').fillna(0)
            valid_rows_df["AI Estimate?"] = valid_rows_df["AI Estimate?"].fillna(False).astype(bool)
            user_rows = valid_rows_df.to_dict('records')
            
            retire_context = retire_city if retire_city else "a typical US city"
            
            timeline_str = f"The user plans to retire at age {retire_age} (in {retire_age - my_age} years)."
            if has_spouse:
                timeline_str += f" Their spouse plans to retire at age {spouse_retire_age}."
                
            # Fallback contexts if kids_context/housing_context isn't available
            k_ctx = kids_context if 'kids_context' in locals() else "No children."
            h_ctx = housing_context_retire if 'housing_context_retire' in locals() else "Will rent."

            prompt = f"""
            You are a retirement modeling AI. The user plans to retire in {retire_context} with a household of {1 + (1 if has_spouse else 0)}.
            {timeline_str}
            
            USER DATA INJECTIONS:
            1. {k_ctx} (Note: Children will likely be older/independent in retirement. Drop daycare costs, but consider adult dependent costs if applicable).
            2. {h_ctx}
            3. Assume all standard consumer debts listed previously are paid off by retirement. Do not list Debt Payments unless it's a mortgage.
            
            Here is a JSON array of the user's RETIREMENT expenses list (some rows may be incomplete):
            {json.dumps(user_rows)}
            
            Rules for updating the list:
            1. If an item has "Amount ($)" as 0 OR "AI Estimate?" is true, predict a realistic cost in today's dollars based on the "Description", AND assign it the most accurate "Category". Set "AI Estimate?" to true.
            2. If an item has "Amount ($)" > 0 AND "AI Estimate?" is false, DO NOT change the "Amount ($)". Ensure its "Category" is accurate. Keep "AI Estimate?" as false.
            3. Generate any critically MISSING standard expenses required to form a fully complete budget for RETIREMENT (e.g., adding higher healthcare/travel if missing). Set their "AI Estimate?" to true.
            
            Return ONLY a JSON array of the ENTIRE updated list of objects strictly matching this schema:
            "Description" (string)
            "Category" (string, MUST be exactly one of: "Housing", "Transportation", "Food", "Utilities", "Insurance", "Healthcare", "Entertainment", "Education", "Debt Payments", "Other")
            "Frequency" (string, exactly one of: Monthly, Yearly)
            "Amount ($)" (number)
            "AI Estimate?" (boolean)
            """
            result = call_gemini_json(prompt)
            if result is not None:
                st.session_state['retire_expenses'] = result
                if 'ret_exp_editor' in st.session_state:
                    del st.session_state['ret_exp_editor']
                st.rerun()

    st.divider()
    if st.button("💾 Save Profile", key="save_7"): save_requested = True


# --- 8. TAXES, SOCIAL SECURITY & RMDS ---
with st.expander("⚖️ 8. Tax, Social Security & RMD Strategy", expanded=False):
    st.markdown('<div class="card-8"></div>', unsafe_allow_html=True)
    st.write("Configure your macro-retirement variables. Let the AI build an optimized strategy based on your income and age.")
    
    t1, t2, t3 = st.columns(3)
    
    # Retrieve current values, correct them if AI incorrectly saved them as a decimal
    cur_tax_val = float(st.session_state['assumptions'].get('current_tax_rate', 22.0))
    if 0 < cur_tax_val <= 1.0: cur_tax_val *= 100
    
    ret_tax_val = float(st.session_state['assumptions'].get('retire_tax_rate', 15.0))
    if 0 < ret_tax_val <= 1.0: ret_tax_val *= 100
    
    cur_tax = t1.number_input("Current Effective Tax Rate (%)", value=cur_tax_val, step=0.1)
    ret_tax = t2.number_input("Retirement Effective Tax Rate (%)", value=ret_tax_val, step=0.1)
    rmd_start = t3.number_input("RMD Start Age (73-75)", value=int(st.session_state['assumptions'].get('rmd_age', 75)), step=1)
    
    if st.button("✨ Auto-Estimate Strategy (AI)"):
        with st.spinner("Calculating tax drag, SECURE 2.0 RMD rules, and Social Security benefits..."):
            income_total = edited_income_df['Annual Amount ($)'].sum()
            prompt = f"""
            User is {my_age} years old in 2026, lives in {curr_city}, and plans to retire at {retire_age}. 
            Current annual income: ${income_total:,.0f}. 
            
            Based on standard federal/state data and the SECURE 2.0 Act, suggest:
            1. Blended EFFECTIVE current working tax rate (percentage number, NOT decimal. E.g. 24.0).
            2. Projected effective retirement tax rate (percentage number).
            3. RMD start age (73 if born 1951-1959, 75 if born 1960+).
            4. Estimated future annual Social Security benefit in today's dollars based on their current income level.
            5. Recommended age to start drawing Social Security (62, 67, or 70).
            
            Return ONLY a valid JSON object matching this exact schema:
            {{"current_tax_rate": float (as a whole number percentage, e.g. 24.5 NOT 0.245), "retire_tax_rate": float, "rmd_age": int, "ss_annual_amount": number, "ss_start_age": int}}
            """
            res = call_gemini_json(prompt)
            if res:
                # Fallback in case the AI inexplicably wraps the object in an array
                if isinstance(res, list) and len(res) > 0: res = res[0]
                
                if isinstance(res, dict):
                    # Sanitize any rogue decimals from the AI immediately
                    ai_cur = float(res.get('current_tax_rate', cur_tax))
                    ai_ret = float(res.get('retire_tax_rate', ret_tax))
                    if 0 < ai_cur <= 1.0: ai_cur *= 100
                    if 0 < ai_ret <= 1.0: ai_ret *= 100
                        
                    st.session_state['assumptions']['current_tax_rate'] = ai_cur
                    st.session_state['assumptions']['retire_tax_rate'] = ai_ret
                    st.session_state['assumptions']['rmd_age'] = int(res.get('rmd_age', rmd_start))
                    
                    # Seamlessly inject Social Security into the Income table
                    ss_amt = float(res.get('ss_annual_amount', 0))
                    if ss_amt > 0:
                        ss_age = int(res.get('ss_start_age', 67))
                        inc_list = st.session_state.get('income', [])
                        
                        ss_found = False
                        for i in inc_list:
                            if i.get('Category') == 'Social Security':
                                i['Annual Amount ($)'] = ss_amt
                                i['Start Age'] = ss_age
                                i['End Age'] = 100
                                ss_found = True
                                break
                                
                        if not ss_found:
                            inc_list.append({
                                "Description": "AI Estimated Social Security",
                                "Category": "Social Security",
                                "Owner": "Me",
                                "Annual Amount ($)": ss_amt,
                                "Start Age": ss_age,
                                "End Age": 100,
                                "Override Growth (%)": None
                            })
                            
                        st.session_state['income'] = inc_list
                        if 'inc_editor' in st.session_state: del st.session_state['inc_editor']
                    
                    st.rerun()
                
    st.divider()
    if st.button("💾 Save Profile", key="save_8"): save_requested = True


# --- 9. DASHBOARD & PROJECTIONS (REACTIVE ENGINE) ---
with st.expander("📈 9. Dashboard & Projections", expanded=True):
    st.markdown('<div class="card-9"></div>', unsafe_allow_html=True)
    
    st.write("### Advanced Analytics & Scenarios")
    s1, s2 = st.columns(2)
    with s1:
        stress_test = st.toggle("📉 Stress Test: Simulate 20% Market Crash in first 3 years of retirement", value=False)
    with s2:
        medicare_cliff = st.toggle("🏥 Medicare Cliff: Drop healthcare costs by 50% at age 65", value=True)
        
    st.divider()
    
    st.info("ℹ️ **Simulation Details:** Social Security triggers automatically based on your Income Table settings. **IRS Required Minimum Distributions (RMDs)** trigger automatically at the age set in Section 8. Both are strictly logged in the breakdown table below!")
    
    if my_age > 0 and life_expectancy > my_age:
        
        # Helper function to safely parse ANY value (including strings and NaNs) into a float without crashing
        def safe_num(val, default=0.0):
            if val is None or val == "":
                return default
            try:
                f = float(val)
                import math
                return default if (pd.isna(f) or math.isnan(f)) else f
            except Exception:
                return default

        # IRS Uniform Lifetime Table (Approximate divisors for RMD calculations starting at age 73/75)
        irs_uniform_table = { 
            73: 26.5, 74: 25.5, 75: 24.6, 76: 23.7, 77: 22.9, 78: 22.0, 79: 21.1, 80: 20.2, 
            81: 19.4, 82: 18.5, 83: 17.7, 84: 16.8, 85: 16.0, 86: 15.2, 87: 14.4, 88: 13.7, 
            89: 12.9, 90: 12.2, 91: 11.5, 92: 10.8, 93: 10.1, 94: 9.5, 95: 8.9, 96: 8.4, 
            97: 7.8, 98: 7.3, 99: 6.8, 100: 6.4, 101: 6.0, 102: 5.6, 103: 5.2, 104: 4.9, 
            105: 4.6, 106: 4.3, 107: 4.1, 108: 3.9, 109: 3.7, 110: 3.5, 111: 3.4, 112: 3.3, 
            113: 3.1, 114: 3.0, 115: 2.9, 116: 2.8, 117: 2.7, 118: 2.5, 119: 2.3, 120: 2.0 
        }

        # 1. State Initialization
        sim_assets = []
        for a in edited_assets_df.to_dict('records'):
            if a.get("Account Name"):
                gr_raw = a.get("Est. Annual Growth (%)")
                sim_assets.append({
                    "Account Name": a.get("Account Name"),
                    "Type": a.get("Type"),
                    "bal": safe_num(a.get("Current Balance ($)")),
                    "contrib": safe_num(a.get("Annual Contribution ($)")),
                    "growth": safe_num(gr_raw) if pd.notna(gr_raw) and gr_raw != "" else market_growth
                })
                
        # Safety catch-all for positive cash flow if no assets are defined
        if len(sim_assets) == 0:
            sim_assets.append({
                "Account Name": "Unallocated Cash",
                "Type": "Checking/Savings",
                "bal": 0.0,
                "contrib": 0.0,
                "growth": 0.0
            })
                
        sim_debts = []
        for d in edited_debt_df.to_dict('records'):
            if d.get("Debt Name"):
                sim_debts.append({
                    "bal": safe_num(d.get("Current Balance ($)")),
                    "pmt": safe_num(d.get("Monthly Payment ($)")) * 12,
                    "rate": safe_num(d.get("Interest Rate (%)")) / 100
                })
                
        sim_re = []
        for r in edited_re_df.to_dict('records'):
            if r.get("Property Name"):
                v_gr_raw = r.get("Override Prop Growth (%)")
                r_gr_raw = r.get("Override Rent Growth (%)")
                sim_re.append({
                    "val": safe_num(r.get("Market Value ($)")),
                    "debt": safe_num(r.get("Mortgage Balance ($)")),
                    "pmt": safe_num(r.get("Mortgage Payment ($)")) * 12,
                    "exp": safe_num(r.get("Monthly Expenses ($)")) * 12,
                    "rent": safe_num(r.get("Monthly Rent ($)")) * 12,
                    "v_growth": safe_num(v_gr_raw) if pd.notna(v_gr_raw) and v_gr_raw != "" else property_growth,
                    "r_growth": safe_num(r_gr_raw) if pd.notna(r_gr_raw) and r_gr_raw != "" else rent_growth,
                    "rate": safe_num(r.get("Interest Rate (%)")) / 100
                })
                
        sim_biz = []
        for b in edited_biz_df.to_dict('records'):
            if b.get("Business Name"):
                sim_biz.append({
                    "name": b.get("Business Name"),
                    "val": safe_num(b.get("Total Valuation ($)")),
                    "own": safe_num(b.get("Your Ownership (%)")) / 100.0,
                    "dist": safe_num(b.get("Annual Distribution ($)"))
                })
                
        sim_results = []
        detailed_results = []
        
        # Strip Housing and Debt from base expenses so the loop doesn't double count them!
        clean_curr_exp = edited_curr_exp_df[~edited_curr_exp_df["Category"].isin(["Housing", "Debt Payments"])]
        curr_exp_by_cat = {}
        for row in clean_curr_exp.to_dict('records'):
            cat = row.get("Category", "Other")
            freq = row.get("Frequency", "Monthly")
            amt = safe_num(row.get("Amount ($)"))
            ann_amt = amt * 12 if freq == "Monthly" else amt
            curr_exp_by_cat[cat] = curr_exp_by_cat.get(cat, 0) + ann_amt
            
        clean_ret_exp = edited_ret_exp_df[~edited_ret_exp_df["Category"].isin(["Housing", "Debt Payments"])]
        ret_exp_by_cat = {}
        for row in clean_ret_exp.to_dict('records'):
            cat = row.get("Category", "Other")
            freq = row.get("Frequency", "Monthly")
            amt = safe_num(row.get("Amount ($)"))
            ann_amt = amt * 12 if freq == "Monthly" else amt
            ret_exp_by_cat[cat] = ret_exp_by_cat.get(cat, 0) + ann_amt
        
        # 2. Year by Year Simulation Loop
        current_year = datetime.date.today().year
        for age in range(int(my_age), int(life_expectancy) + 1):
            year = current_year + (age - my_age)
            is_retired = age >= int(retire_age)
            
            year_detail = {"Age": age, "Year": year}
            
            annual_inc = 0
            annual_ss = 0
            
            # --- Dynamic Market Growth (Stress Test) ---
            current_mkt_growth = market_growth
            if stress_test and is_retired and age < (int(retire_age) + 3):
                current_mkt_growth = -20.0
            
            # --- Income ---
            for inc in edited_income_df.to_dict('records'):
                if inc.get("Description"):
                    start = safe_num(inc.get('Start Age'), 18)
                    end = safe_num(inc.get('End Age'), 100)
                    if start <= age <= end:
                        g_raw = inc.get('Override Growth (%)')
                        g = safe_num(g_raw) if pd.notna(g_raw) and g_raw != "" else income_growth
                        amt = safe_num(inc.get('Annual Amount ($)'))
                        inflated_inc = amt * ((1 + g/100) ** (age - my_age))
                        annual_inc += inflated_inc
                        cat_name = inc.get("Category", "Other")
                        year_detail[f"Income: {cat_name}"] = year_detail.get(f"Income: {cat_name}", 0) + inflated_inc
                        if cat_name == "Social Security":
                            annual_ss += inflated_inc
                        
            # --- 401(k) / IRA RMDs (Required Minimum Distributions) ---
            rmd_income = 0
            rmd_target_age = int(st.session_state['assumptions'].get('rmd_age', 75))
            if age >= rmd_target_age: # SECURE 2.0 Act pushes RMD age to 75 for most working people today
                factor = irs_uniform_table.get(age, 2.0)
                for a in sim_assets:
                    if a.get('Type') == 'Traditional 401k/IRA' and a['bal'] > 0:
                        rmd_amt = a['bal'] / factor
                        a['bal'] -= rmd_amt
                        rmd_income += rmd_amt
                        
            if rmd_income > 0:
                annual_inc += rmd_income
                year_detail["Income: 401(k)/IRA RMDs"] = rmd_income
                
            # --- Business Update (ITERATIVE) ---
            cur_biz_val = 0
            biz_dist_total = 0
            for b in sim_biz:
                # Apply growth dynamically year over year
                if age > int(my_age):
                    b['val'] *= (1 + current_mkt_growth/100)
                    b['dist'] *= (1 + income_growth/100)
                
                cur_biz_val += b['val'] * b['own']
                annual_inc += b['dist']
                biz_dist_total += b['dist']
                
            if biz_dist_total > 0:
                year_detail["Income: Business Distributions"] = biz_dist_total
                        
            # --- Real Estate Update (ITERATIVE) ---
            re_equity = 0
            re_exp_total = 0
            for r in sim_re:
                if age > int(my_age):
                    r['rent'] *= (1 + r['r_growth']/100)
                    r['exp'] *= (1 + inflation_rate/100)
                    r['val'] *= (1 + r['v_growth']/100)
                    
                annual_inc += r['rent']
                if r['rent'] > 0: year_detail["Income: RE Rent"] = year_detail.get("Income: RE Rent", 0) + r['rent']
                
                re_exp_total += r['exp']
                if r['exp'] > 0: year_detail["Expense: RE Upkeep/Tax"] = year_detail.get("Expense: RE Upkeep/Tax", 0) + r['exp']
                
                if r['debt'] > 0:
                    interest = r['debt'] * r['rate']
                    principal = r['pmt'] - interest
                    if principal < 0: principal = 0
                    r['debt'] -= principal
                    if r['debt'] < 0: r['debt'] = 0
                    re_exp_total += r['pmt']
                    year_detail["Expense: RE Mortgage"] = year_detail.get("Expense: RE Mortgage", 0) + r['pmt']
                    
                re_equity += (r['val'] - r['debt'])
                
            # --- Expenses & Debts ---
            total_exp = re_exp_total
            active_exp_dict = ret_exp_by_cat if is_retired else curr_exp_by_cat
            for cat, base_amt in active_exp_dict.items():
                inflated_exp = base_amt * ((1 + inflation_rate/100) ** (age - my_age))
                
                # Apply Medicare Cliff
                if medicare_cliff and cat == "Healthcare" and age >= 65:
                    inflated_exp *= 0.50 
                    
                total_exp += inflated_exp
                year_detail[f"Expense: {cat}"] = year_detail.get(f"Expense: {cat}", 0) + inflated_exp
                
            debt_bal_total = 0
            for d in sim_debts:
                if d['bal'] > 0:
                    interest = d['bal'] * d['rate']
                    principal = d['pmt'] - interest
                    if principal < 0: principal = 0
                    d['bal'] -= principal
                    if d['bal'] < 0: d['bal'] = 0
                    total_exp += d['pmt']
                    year_detail["Expense: Debt Payments"] = year_detail.get("Expense: Debt Payments", 0) + d['pmt']
                debt_bal_total += d['bal']
                
            # --- Milestones ---
            for ev in edited_events_df.to_dict('records'):
                if ev.get("Description"):
                    sd = str(ev.get('Start Date (MM/YYYY)', ''))
                    ed = str(ev.get('End Date (MM/YYYY)', ''))
                    try: sy = int(sd.split('/')[-1]) if '/' in sd else 0
                    except: sy = 0
                    try: ey = int(ed.split('/')[-1]) if '/' in ed else sy
                    except: ey = sy
                    
                    if sy <= year <= ey and sy != 0:
                        amt = safe_num(ev.get('Amount ($)')) * ((1 + inflation_rate/100) ** (age - my_age))
                        if ev.get('Type') == 'Expense':
                            total_exp += amt
                            year_detail[f"Expense: Milestone ({ev.get('Description')})"] = year_detail.get(f"Expense: Milestone ({ev.get('Description')})", 0) + amt
                        else:
                            annual_inc += amt
                            year_detail[f"Income: Milestone ({ev.get('Description')})"] = year_detail.get(f"Income: Milestone ({ev.get('Description')})", 0) + amt
            
            # --- Taxes & Cash Flow ---
            tax_rate = cur_tax if not is_retired else ret_tax
            
            # Taxable income (exclude pre-tax items like Employer Match)
            taxable_inc = annual_inc - year_detail.get("Income: Employer Match (401k/HSA)", 0)
            if taxable_inc < 0: taxable_inc = 0
            
            # DIVIDE BY 100 SO 22.0 BECOMES 0.22
            annual_taxes = taxable_inc * (tax_rate / 100.0)
            year_detail["Expense: Taxes"] = annual_taxes
            
            # Calculate true Annual Net Savings for the charts
            annual_net_savings = annual_inc - total_exp - annual_taxes
            year_detail["Net Savings"] = annual_net_savings
            
            # --- Asset Flow ---
            liquid_assets_total = 0
            asset_contributions = 0
            
            if not is_retired:
                for a in sim_assets:
                    a['bal'] += a['contrib']
                    asset_contributions += a['contrib']
            
            # Deduct the expected baseline contributions from remaining cash flow 
            ncf = annual_net_savings - asset_contributions
            
            for a in sim_assets: 
                # Apply stress test crash exclusively to market-exposed liquid assets
                if stress_test and is_retired and age < (int(retire_age) + 3) and a.get('Type') not in ['Checking/Savings', 'HYSA', 'Unallocated Cash']:
                    active_growth = -20.0
                else:
                    active_growth = a['growth']
                a['bal'] *= (1 + active_growth/100)
                
            if ncf > 0 and len(sim_assets) > 0:
                sim_assets[0]['bal'] += ncf  # Surplus goes to first asset bucket
            elif ncf < 0:
                shortfall = abs(ncf)
                for a in sim_assets:
                    if shortfall <= 0: break
                    
                    # If withdrawing from a Traditional 401k/IRA, we must gross-up the withdrawal to pay taxes
                    is_taxable_acct = (a.get('Type') == 'Traditional 401k/IRA')
                    eff_tax_rate = min(tax_rate / 100.0, 0.99) # Cap to avoid division by zero
                    multiplier = 1.0 / (1.0 - eff_tax_rate) if is_taxable_acct else 1.0
                    
                    req_gross_withdrawal = shortfall * multiplier
                    
                    if a['bal'] >= req_gross_withdrawal:
                        a['bal'] -= req_gross_withdrawal
                        if is_taxable_acct:
                            extra_tax = req_gross_withdrawal - shortfall
                            annual_taxes += extra_tax
                            year_detail["Expense: Taxes"] = year_detail.get("Expense: Taxes", 0) + extra_tax
                            annual_net_savings -= extra_tax
                            year_detail["Net Savings"] = annual_net_savings
                        shortfall = 0
                    else:
                        gross_withdrawn = a['bal']
                        a['bal'] = 0
                        if is_taxable_acct:
                            net_cash = gross_withdrawn * (1.0 - eff_tax_rate)
                            extra_tax = gross_withdrawn - net_cash
                            annual_taxes += extra_tax
                            year_detail["Expense: Taxes"] = year_detail.get("Expense: Taxes", 0) + extra_tax
                            annual_net_savings -= extra_tax
                            year_detail["Net Savings"] = annual_net_savings
                            shortfall -= net_cash
                        else:
                            shortfall -= gross_withdrawn
                        
            for a in sim_assets:
                liquid_assets_total += a['bal']
                
            net_worth = liquid_assets_total + re_equity + cur_biz_val - debt_bal_total
            
            sim_results.append({
                "Age": age,
                "Year": year,
                "Annual Income": annual_inc,
                "Social Security (Included)": annual_ss,
                "RMDs (Included)": rmd_income,
                "Annual Expenses": total_exp,
                "Annual Taxes": annual_taxes,
                "Annual Net Savings": annual_net_savings,
                "Liquid Assets": liquid_assets_total,
                "Real Estate Equity": re_equity,
                "Business Equity": cur_biz_val,
                "Debt": -debt_bal_total, # Rendered as negative on stacked chart to visually pull down net worth
                "Net Worth": net_worth
            })
            
            detailed_results.append(year_detail)
            
        # 3. Render Charts (Plotly for formatted, interactive, stacked views)
        df_sim = pd.DataFrame(sim_results)
        final_nw = df_sim.iloc[-1]['Net Worth']
        
        # TOP SCORECARD
        if final_nw >= 1000000:
            st.success(f"🟢 **On Track:** Your projected Net Worth at Age {life_expectancy} is **${final_nw:,.0f}**. Your assets outlive your life expectancy comfortably.")
        elif final_nw > 0:
            st.warning(f"🟡 **Caution:** Your projected Net Worth at Age {life_expectancy} is **${final_nw:,.0f}**. You are solvent, but with a narrow margin of safety.")
        else:
            depletion_age = df_sim[df_sim['Net Worth'] <= 0]['Age'].min()
            st.error(f"🔴 **Shortfall Alert:** Your assets are projected to deplete entirely at Age **{depletion_age}**.")
            
        if HAS_PLOTLY:
            st.write("**Annual Cash Flow (Income vs Expenses vs Taxes vs Savings)**")
            fig_cf = go.Figure()
            fig_cf.add_trace(go.Scatter(x=df_sim["Age"], y=df_sim["Annual Income"], mode='lines', name='Income', line=dict(color='#3b82f6', width=3)))
            fig_cf.add_trace(go.Scatter(x=df_sim["Age"], y=df_sim["Annual Expenses"], mode='lines', name='Expenses', line=dict(color='#ef4444', width=3)))
            fig_cf.add_trace(go.Scatter(x=df_sim["Age"], y=df_sim["Annual Taxes"], mode='lines', name='Taxes', line=dict(color='#f59e0b', width=3)))
            fig_cf.add_trace(go.Scatter(x=df_sim["Age"], y=df_sim["Annual Net Savings"], mode='lines', name='Net Savings', line=dict(color='#10b981', width=3)))
            
            fig_cf.update_layout(
                hovermode="x unified",
                yaxis=dict(tickformat="$,.0f"),
                margin=dict(l=0, r=0, t=30, b=0),
                legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
            )
            st.plotly_chart(fig_cf, width='stretch')

            st.write("**Net Worth Projection (Stacked Asset Growth vs Debt)**")
            fig_nw = go.Figure()
            
            # Stacked Assets
            fig_nw.add_trace(go.Scatter(x=df_sim["Age"], y=df_sim["Liquid Assets"], mode='lines', stackgroup='one', name='Liquid Assets', fillcolor='rgba(20, 184, 166, 0.5)', line=dict(color='#14b8a6')))
            fig_nw.add_trace(go.Scatter(x=df_sim["Age"], y=df_sim["Real Estate Equity"], mode='lines', stackgroup='one', name='Real Estate Equity', fillcolor='rgba(139, 92, 246, 0.5)', line=dict(color='#8b5cf6')))
            fig_nw.add_trace(go.Scatter(x=df_sim["Age"], y=df_sim["Business Equity"], mode='lines', stackgroup='one', name='Business Equity', fillcolor='rgba(245, 158, 11, 0.5)', line=dict(color='#f59e0b')))
            
            # Debt (Negative Stack)
            fig_nw.add_trace(go.Scatter(x=df_sim["Age"], y=df_sim["Debt"], mode='lines', stackgroup='two', name='Debt Liabilities', fillcolor='rgba(239, 68, 68, 0.5)', line=dict(color='#ef4444')))
            
            # Total Net Worth Line
            fig_nw.add_trace(go.Scatter(x=df_sim["Age"], y=df_sim["Net Worth"], mode='lines', name='Total Net Worth', line=dict(color='#111827', width=3, dash='dot')))
            
            fig_nw.update_layout(
                hovermode="x unified",
                yaxis=dict(tickformat="$,.0f"),
                margin=dict(l=0, r=0, t=30, b=0),
                legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
            )
            st.plotly_chart(fig_nw, width='stretch')
            
        else:
            st.warning("Install `plotly` to view highly formatted, stacked area charts. Rendering standard charts as fallback.")
            st.write("**Annual Cash Flow**")
            st.line_chart(df_sim.set_index("Age")[["Annual Income", "Annual Expenses", "Annual Taxes", "Annual Net Savings"]], width="stretch")
            st.write("**Net Worth Projection**")
            st.area_chart(df_sim.set_index("Age")[["Liquid Assets", "Real Estate Equity", "Business Equity", "Debt"]], width="stretch")
            
        st.divider()
        c_dl, _ = st.columns([1, 2])
        with c_dl:
            csv = df_sim.to_csv(index=False).encode('utf-8')
            st.download_button(label="📥 Download Full Simulation (.csv)", data=csv, file_name='retirement_simulation.csv', mime='text/csv', type="primary")

        st.subheader("Raw Simulation Data")
        
        # Re-order columns so SS and RMDs sit next to Annual Income
        cols_ordered = ["Annual Income", "Social Security (Included)", "RMDs (Included)", "Annual Expenses", "Annual Taxes", "Annual Net Savings", "Liquid Assets", "Real Estate Equity", "Business Equity", "Debt", "Net Worth", "Year"]
        df_sim_display = df_sim.set_index("Age")[cols_ordered]
        
        # Display raw data elegantly formatted
        format_dict = {
            "Annual Income": "${:,.0f}",
            "Social Security (Included)": "${:,.0f}",
            "RMDs (Included)": "${:,.0f}",
            "Annual Expenses": "${:,.0f}",
            "Annual Taxes": "${:,.0f}",
            "Annual Net Savings": "${:,.0f}",
            "Liquid Assets": "${:,.0f}",
            "Real Estate Equity": "${:,.0f}",
            "Business Equity": "${:,.0f}",
            "Debt": "${:,.0f}",
            "Net Worth": "${:,.0f}",
            "Year": "{:.0f}"
        }
        st.dataframe(df_sim_display.style.format(format_dict), width="stretch")
        
        st.divider()
        st.subheader("Detailed Income & Expense Breakdown")
        st.write("A transparent, granular view of every single income and expense category per year.")
        
        df_det = pd.DataFrame(detailed_results).fillna(0)
        
        # Sort columns to group incomes and expenses nicely
        cols = list(df_det.columns)
        income_cols = sorted([c for c in cols if c.startswith("Income:")])
        expense_cols = sorted([c for c in cols if c.startswith("Expense:")])
        
        ordered_cols = ["Age", "Year"] + income_cols + expense_cols + ["Net Savings"]
        df_det = df_det[ordered_cols]
        
        format_dict_det = {c: "${:,.0f}" for c in ordered_cols if c not in ["Age", "Year"]}
        format_dict_det["Year"] = "{:.0f}"
        
        st.dataframe(df_det.set_index("Age").style.format(format_dict_det), width="stretch")
        
    else:
        st.info("Please fill out your Age and Life Expectancy above to view projections.")


# --- FINAL MASTER SAVE LOGIC ---
st.markdown("---")
if st.button("🚀 Save Full Profile to Cloud", type="primary", width="stretch", key="save_main") or save_requested:
    if st.session_state['user_email'] == "guest_demo":
        st.error("Cannot save data to the cloud while in Demo Mode. Please sign up to save your profile.")
    else:
        def clean_df(df, key_col):
            if df.empty: return []
            valid_rows = df[df[key_col].astype(str) != ""]
            records = valid_rows.to_dict('records')
            for row in records:
                for k, v in row.items():
                    if pd.isna(v):
                        row[k] = None
            return records

        c_income = clean_df(edited_income_df, "Description")
        c_re     = clean_df(edited_re_df, "Property Name")
        c_biz    = clean_df(edited_biz_df, "Business Name")
        c_assets = clean_df(edited_assets_df, "Account Name")
        c_debt   = clean_df(edited_debt_df, "Debt Name")
        c_curr_exp = clean_df(edited_curr_exp_df, "Description")
        c_events   = clean_df(edited_events_df, "Description")
        c_ret_exp  = clean_df(edited_ret_exp_df, "Description")

        user_data = {
            "personal_info": {
                "name": my_name,
                "age": my_age,
                "retire_age": retire_age,
                "life_expectancy": life_expectancy,
                "current_city": curr_city,
                "has_spouse": has_spouse,
                "spouse_name": spouse_name,
                "spouse_age": spouse_age,
                "spouse_retire_age": spouse_retire_age if has_spouse else None,
                "kids": kids_data
            },
            "retire_city": retire_city,
            "assumptions": {
                "inflation": inflation_rate, 
                "market_growth": market_growth,
                "income_growth": income_growth,
                "property_growth": property_growth,
                "rent_growth": rent_growth,
                "current_tax_rate": cur_tax,
                "retire_tax_rate": ret_tax,
                "rmd_age": rmd_start
            },
            "income": c_income,
            "real_estate": c_re,
            "business": c_biz,
            "liquid_assets": c_assets,
            "liabilities": c_debt,
            "current_expenses": c_curr_exp,
            "one_time_events": c_events,
            "retire_expenses": c_ret_exp
        }
        
        try:
            doc_ref = db.collection('users').document(st.session_state['user_email'])
            doc_ref.set(user_data, merge=True)
            
            # Keep session state updated
            for key, val in user_data.items():
                st.session_state['user_data'][key] = val
            st.session_state['current_expenses'] = c_curr_exp
            st.session_state['retire_expenses'] = c_ret_exp
            st.session_state['one_time_events'] = c_events
            st.session_state['assumptions'] = user_data["assumptions"]
                
            st.success("✅ Financial profile securely saved to the Cloud!")
        except Exception as e:
            st.error(f"Database error: {e}")