import streamlit as st
from pathlib import Path
import streamlit.components.v1 as components

st.set_page_config(page_title="LockedInterview", layout="wide")

# ---- Style the OUTER Streamlit page (remove awkward box) ----
st.markdown(
    """
    <style>
    html, body, .stApp {
        height: 100%;
        margin: 0;
        padding: 0;
        background: #f5f5f7;  /* white-ish page */
    }
    .main .block-container {
        padding-top: 0rem;
        padding-bottom: 0rem;
        padding-left: 0rem;
        padding-right: 0rem;
        max-width: 100%;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

# ---- Load CSS + HTML for your actual app (inside iframe) ----
css_path = Path("frontend.css")
html_path = Path("frontend.html")

if not html_path.exists():
    st.error("frontend.html not found in this folder.")
else:
    css = css_path.read_text() if css_path.exists() else ""
    html_raw = html_path.read_text()

    # inject CSS into the head of your HTML so it styles the iframe content
    if "</head>" in html_raw:
        html_with_css = html_raw.replace("</head>", f"<style>{css}</style></head>")
    else:
        html_with_css = f"<style>{css}</style>\n{html_raw}"

    components.html(html_with_css, height=900, scrolling=True)
st.markdown(
    """
    <style>
    html, body, .stApp {
        height: 100%;
        margin: 0;
        padding: 0;
        background: #f5f5f7;  /* white-ish page */
    }
    .main .block-container {
        padding-top: 0rem;
        padding-bottom: 0rem;
        padding-left: 0rem;
        padding-right: 0rem;
        max-width: 100%;
    }
    </style>
    """,
    unsafe_allow_html=True,
)
