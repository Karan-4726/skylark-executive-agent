import streamlit as st
import pandas as pd
import requests
import plotly.express as px
import plotly.graph_objects as go
import google.generativeai as genai
from agent_config import get_cro_system_prompt

# ---------------------------------------------------------
# 1. PAGE CONFIGURATION & STYLING INJECTION
# ---------------------------------------------------------
st.set_page_config(page_title="Skylark Drones | Executive Agent", page_icon="🛸", layout="wide")

def load_css(file_name):
    """Loads an external CSS stylesheet into the Streamlit app."""
    try:
        with open(file_name) as f:
            st.markdown(f"<style>{f.read()}</style>", unsafe_allow_html=True)
    except FileNotFoundError:
        pass

load_css("style.css")

# ---------------------------------------------------------
# 2. HELPER FUNCTIONS (Formatting, Fetching, Cleaning)
# ---------------------------------------------------------
def format_inr(value):
    """Formats large numbers into Indian Crores (Cr) and Lakhs (L) for executive readability."""
    if pd.isna(value): return "₹0"
    if value >= 10000000: return f"₹{value/10000000:.2f} Cr"
    elif value >= 100000: return f"₹{value/100000:.2f} L"
    return f"₹{value:,.0f}"

@st.cache_data(ttl=300)
def fetch_board_data(token, board_id):
    """Fetches and flattens board data from Monday.com GraphQL API safely."""
    if not token or not board_id: return pd.DataFrame()
    url = "https://api.monday.com/v2"
    headers = {"Authorization": token, "API-Version": "2024-01", "Content-Type": "application/json"}
    
    # Ensure board_id is clean string integer
    clean_board_id = str(board_id).strip()
    query = f"query {{ boards(ids: [{clean_board_id}]) {{ items_page(limit: 500) {{ items {{ name column_values {{ column {{ title }} text }} }} }} }} }}"
    
    try:
        response = requests.post(url, json={'query': query}, headers=headers, timeout=15)
        res_json = response.json()
        
        # Check if Monday.com returned a GraphQL error (like invalid token or board not found)
        if 'errors' in res_json:
            st.error(f"Monday API Error: {res_json['errors'][0].get('message', 'Unknown error')}")
            return pd.DataFrame()
            
        if 'data' not in res_json or not res_json['data'].get('boards'):
            st.error(f"Invalid API Response structure. Check Board ID: {clean_board_id}")
            return pd.DataFrame()
            
        boards_data = res_json['data']['boards']
        if not boards_data or not boards_data[0]:
            return pd.DataFrame()
            
        items = boards_data[0].get('items_page', {}).get('items', [])
        rows = []
        for item in items:
            row_dict = {"Item Name": item.get('name', 'Unnamed')}
            for cv in item.get('column_values', []):
                col_title = cv.get('column', {}).get('title', 'Unknown')
                row_dict[col_title] = cv.get('text', '')
            rows.append(row_dict)
            
        return pd.DataFrame(rows)
    except Exception as e:
        st.error(f"Connection Error: {str(e)}")
        return pd.DataFrame()
def clean_enterprise_data(df):
    """Deep cleans messy live data using vectorization and regex."""
    if df.empty: return df
    
    df = df.apply(lambda x: x.str.strip() if x.dtype == "object" else x)
    
    for col in df.columns:
        if any(keyword in col.lower() for keyword in ['value', 'amount', 'rupees', 'price']):
            df[f"Clean_{col}"] = df[col].astype(str).replace(r'[^\d.]', '', regex=True)
            df[f"Clean_{col}"] = pd.to_numeric(df[f"Clean_{col}"], errors='coerce').fillna(0)
            
    status_cols = [c for c in df.columns if any(k in c.lower() for k in ['status', 'stage', 'probability', 'sector', 'priority'])]
    for col in status_cols:
        df[col] = df[col].astype(str).str.title().replace('Nan', 'Unknown')
        
    return df

# ---------------------------------------------------------
# 3. AUTOMATED SECRETS LOADING (St.Secrets Integration)
# ---------------------------------------------------------
st.sidebar.image("https://cdn-icons-png.flaticon.com/512/3063/3063822.png", width=60)
st.sidebar.title("🛸 System Hub")
st.sidebar.success("Connected to Cloud Secrets Securely")

try:
    monday_token = st.secrets["MONDAY_TOKEN"]
    deals_board_id = st.secrets["DEALS_BOARD_ID"]
    wo_board_id = st.secrets["WO_BOARD_ID"]
    gemini_key = st.secrets["GEMINI_KEY"]
except Exception:
    monday_token = None
    deals_board_id = None
    wo_board_id = None
    gemini_key = None

st.markdown('<div class="main-header">🛸 Skylark Drones | Executive Agent Hub</div>', unsafe_allow_html=True)
st.markdown('<div class="sub-header">Automated Cross-Board Reconciliation & Strategic Intelligence</div>', unsafe_allow_html=True)

if not (monday_token and deals_board_id and wo_board_id and gemini_key):
    st.error("⚠️ Missing cloud secrets. Please ensure `MONDAY_TOKEN`, `DEALS_BOARD_ID`, `WO_BOARD_ID`, and `GEMINI_KEY` are defined in your Streamlit Cloud Secrets settings.")
    st.stop()

# ---------------------------------------------------------
# 4. DATA PIPELINE & BUSINESS LOGIC
# ---------------------------------------------------------
with st.spinner("Synchronizing and sanitizing live Monday.com databases..."):
    raw_deals = fetch_board_data(monday_token, deals_board_id)
    raw_wo = fetch_board_data(monday_token, wo_board_id)
    
    df_deals = clean_enterprise_data(raw_deals)
    df_wo = clean_enterprise_data(raw_wo)

if df_deals.empty or df_wo.empty:
    st.error("Failed to load or clean databases. Please verify your IDs and Token.")
    st.stop()

val_cols = [c for c in df_deals.columns if 'Clean_' in c]
if val_cols:
    df_deals['Numeric_Value'] = df_deals[val_cols[0]]
else:
    df_deals['Numeric_Value'] = 0

stage_cols = [c for c in df_deals.columns if 'stage' in c.lower() or 'status' in c.lower()]
deal_stage_col = stage_cols[0] if stage_cols else "Item Name"

prob_cols = [c for c in df_deals.columns if 'probability' in c.lower()]
prob_col = prob_cols[0] if prob_cols else (stage_cols[0] if stage_cols else None)

exec_cols = [c for c in df_wo.columns if 'execution' in c.lower() or 'status' in c.lower()]
wo_exec_col = exec_cols[0] if exec_cols else "Item Name"

sector_cols = [c for c in df_deals.columns if 'sector' in c.lower() or 'industry' in c.lower()]
deal_sector_col = sector_cols[0] if sector_cols else None

priority_cols = [c for c in df_wo.columns if 'priority' in c.lower()]
wo_priority_col = priority_cols[0] if priority_cols else None

# Cross-Board Reconciliation
if prob_col:
    high_prob_deals = df_deals[df_deals[prob_col].astype(str).str.contains('High', na=False, case=False)]
else:
    high_prob_deals = pd.DataFrame()

wo_names = df_wo['Item Name'].unique() if 'Item Name' in df_wo.columns else []
missing_wos = high_prob_deals[~high_prob_deals['Item Name'].isin(wo_names)] if not high_prob_deals.empty else pd.DataFrame()

# ---------------------------------------------------------
# 5. DASHBOARD UI & VISUAL REPRESENTATIONS
# ---------------------------------------------------------
tab_exec, tab_ai, tab_audit = st.tabs(["📊 Executive Dashboard", "🤖 Strategic AI Agent", "🛡️ Operational Audit"])

with tab_exec:
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Active Deals", len(df_deals))
    c2.metric("Active Work Orders", len(df_wo))
    c3.metric("Total Pipeline", format_inr(df_deals['Numeric_Value'].sum()))
    c4.metric("Execution Gap (Missing WOs)", len(missing_wos))
    
    st.divider()
    
    col1, col2 = st.columns(2)
    with col1:
        st.subheader("Active Pipeline Velocity (Excluding Dead Deals)")
        if deal_stage_col != "Item Name":
            active_pipeline_df = df_deals[~df_deals[deal_stage_col].str.contains('Dead|Lost', case=False, na=False)]
            stage_data = active_pipeline_df.groupby(deal_stage_col)['Numeric_Value'].sum().reset_index()
            
            fig1 = px.funnel(stage_data, x='Numeric_Value', y=deal_stage_col, title="Active Revenue by Stage")
            fig1.update_layout(paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)', font_color='#F8FAFC')
            st.plotly_chart(fig1, use_container_width=True)
        else:
            st.info("Deal Stage column not found for visualization.")
            
    with col2:
        st.subheader("Work Order Execution Distribution")
        if wo_exec_col != "Item Name":
            fig2 = px.pie(df_wo, names=wo_exec_col, hole=0.4, title="Work Order Status Breakdown")
            fig2.update_layout(paper_bgcolor='rgba(0,0,0,0)', font_color='#F8FAFC')
            st.plotly_chart(fig2, use_container_width=True)
        else:
            st.info("Execution Status column not found for visualization.")

    st.divider()

    col3, col4 = st.columns(2)
    with col3:
        st.subheader("Revenue Concentration by Sector")
        if deal_sector_col:
            sector_data = df_deals.groupby(deal_sector_col)['Numeric_Value'].sum().reset_index()
            fig3 = px.bar(sector_data, x=deal_sector_col, y='Numeric_Value', title="Pipeline Value per Sector", color='Numeric_Value', color_continuous_scale='Blues')
            fig3.update_layout(paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)', font_color='#F8FAFC')
            st.plotly_chart(fig3, use_container_width=True)
        else:
            top_deals = df_deals.nlargest(10, 'Numeric_Value')
            fig3 = px.bar(top_deals, x='Item Name', y='Numeric_Value', title="Top 10 High-Value Deals", color='Numeric_Value', color_continuous_scale='Purples')
            fig3.update_layout(paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)', font_color='#F8FAFC')
            st.plotly_chart(fig3, use_container_width=True)

    with col4:
        st.subheader("Work Order Priority Allocation")
        if wo_priority_col:
            priority_data = df_wo[wo_priority_col].value_counts().reset_index()
            priority_data.columns = ['Priority', 'Count']
            fig4 = px.bar(priority_data, x='Priority', y='Count', title="Work Orders by Priority Level", color='Priority')
            fig4.update_layout(paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)', font_color='#F8FAFC')
            st.plotly_chart(fig4, use_container_width=True)
        else:
            fig4 = px.histogram(df_wo, x='Item Name', title="Work Order Distribution Spread")
            fig4.update_layout(paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)', font_color='#F8FAFC')
            st.plotly_chart(fig4, use_container_width=True)

with tab_ai:
    st.subheader("💬 Query the Telemetry Data")
    
    if "messages" not in st.session_state:
        st.session_state.messages = []

    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]): st.markdown(msg["content"])

    if prompt := st.chat_input("Ask about revenue, risks, or specific deals..."):
        st.session_state.messages.append({"role": "user", "content": prompt})
        with st.chat_message("user"): st.markdown(prompt)

        with st.chat_message("assistant"):
            with st.spinner("Analyzing with Gemini Intelligence..."):
                
                pipeline_by_stage = df_deals.groupby(deal_stage_col)['Numeric_Value'].sum().to_dict() if deal_stage_col != "Item Name" else "N/A"
                wo_by_status = df_wo[wo_exec_col].value_counts().to_dict() if wo_exec_col != "Item Name" else "N/A"

                context = get_cro_system_prompt(
                    total_val=df_deals['Numeric_Value'].sum(),
                    active_deals=len(df_deals),
                    total_wos=len(df_wo),
                    missing_wos_count=len(missing_wos),
                    pipeline_stage_dict=pipeline_by_stage,
                    wo_status_dict=wo_by_status,
                    format_inr_func=format_inr
                )
                
                try:
                    genai.configure(api_key=gemini_key)
                    model = genai.GenerativeModel('gemini-2.5-flash')
                    
                    response = model.generate_content(f"{context}\n\nUser Query: {prompt}")
                    reply = response.text
                    
                    st.markdown(reply)
                    st.session_state.messages.append({"role": "assistant", "content": reply})
                except Exception as e:
                    st.error(f"Gemini API Error: {str(e)}")

with tab_audit:
    st.subheader("🛡️ Automated Risk Detection")
    if len(missing_wos) > 0:
        st.markdown(f'<div class="alert-card"><strong>🚨 Revenue Leakage Detected:</strong> Found {len(missing_wos)} High-Probability deals in the pipeline with no corresponding Work Order. Operations should verify if execution has stalled.</div>', unsafe_allow_html=True)
        st.dataframe(missing_wos[['Item Name', deal_stage_col, 'Numeric_Value']], use_container_width=True)
    else:
        st.markdown('<div class="success-card">✅ <strong>Operations Synced:</strong> All high-probability deals have corresponding work orders in the system.</div>', unsafe_allow_html=True)
