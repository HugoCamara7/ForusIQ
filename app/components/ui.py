"""Estilos compartidos con Catálogo / Repo Control Center (login, barra lateral, logos)."""

from __future__ import annotations

import base64
from html import escape
from pathlib import Path

import streamlit as st

BRAND_PRIMARY = "#17269A"
BRAND_BLUE = "#2367FF"
BRAND_ACCENT = "#009FE3"
NAVY = "#152238"
ASSETS = Path(__file__).resolve().parents[2] / "assets"

#: Marcas de la franja del login, en el mismo orden que Catálogo Control Center.
BRAND_LOGOS = [
    ("Columbia", "logo_columbia.png"),
    ("Hush Puppies", "logo_hushpuppies.png"),
    ("Hush Puppies Kids", "logo_hpk.png"),
    ("Rockford", "logo_rockford.webp"),
    ("Vans", "logo_vans.jpg"),
    ("Patagonia", "logo_patagonia.png"),
    ("Keds", "logo_keds.png"),
    ("Mountain Hardwear", "logo_mhw.png"),
    ("Sorel", "logo_sorel.webp"),
]


def image_data_uri(path: Path) -> str:
    path = Path(path)
    if not path.exists():
        return ""
    mime = {"png": "png", "jpg": "jpeg", "jpeg": "jpeg", "webp": "webp", "svg": "svg+xml"}
    tipo = mime.get(path.suffix.lower().lstrip("."), "png")
    return f"data:image/{tipo};base64,{base64.b64encode(path.read_bytes()).decode()}"


def html(markup: str, sidebar: bool = False) -> None:
    (st.sidebar if sidebar else st).markdown(markup, unsafe_allow_html=True)


def forus_logo_html() -> str:
    src = image_data_uri(ASSETS / "forus_logo.png")
    return (
        f'<img src="{src}" alt="FORUS">'
        if src
        else '<div class="login-forus-fallback">FORUS<small>CONSUMER FANATIC</small></div>'
    )


def brand_strip() -> None:
    chips = []
    for nombre, archivo in BRAND_LOGOS:
        src = image_data_uri(ASSETS / "brands" / archivo)
        if src:
            chips.append(
                f'<span class="brand-chip"><img src="{src}" alt="{escape(nombre)}"></span>'
            )
    if chips:
        html(f'<div class="login-brands">{"".join(chips)}</div>')


def login_styles() -> None:
    html(f"""
    <style>
    [data-testid="stToolbar"], .stDeployButton, header[data-testid="stHeader"] {{ display:none !important; }}
    [data-testid="stAppViewContainer"] {{
        background:
            radial-gradient(1200px 620px at 12% -10%, #24407A 0%, transparent 60%),
            radial-gradient(900px 520px at 92% 8%, #10306E 0%, transparent 58%),
            {NAVY};
    }}
    .main .block-container, [data-testid="stMainBlockContainer"] {{ padding-top:44px; max-width:620px; }}
    .st-key-login_card {{
        width:min(452px, calc(100vw - 32px)); margin:0 auto; overflow:hidden; border-radius:18px;
        background:#FFFFFF; box-shadow:0 34px 90px rgba(3,10,32,.46); color-scheme:light; gap:0;
    }}
    .login-head {{
        padding:34px 32px 32px; text-align:center; color:#FFFFFF;
        background:linear-gradient(180deg, {BRAND_BLUE} 0%, #1757EF 100%);
    }}
    .login-logo-row {{ display:flex; align-items:center; justify-content:center; gap:20px; margin-bottom:22px; }}
    .login-forus-logo {{
        min-width:178px; height:64px; border-radius:12px; background:#FFFFFF;
        display:grid; place-items:center; padding:8px 14px; box-sizing:border-box;
    }}
    .login-forus-logo img {{ max-width:100%; max-height:48px; object-fit:contain; }}
    .login-forus-fallback {{ color:#14306B; font-size:34px; line-height:1; font-weight:950; }}
    .login-forus-fallback small {{ display:block; margin-top:3px; font-size:8px; letter-spacing:.22em; }}
    .login-divider {{ width:1px; height:48px; background:rgba(255,255,255,.62); }}
    .login-app-badge {{
        width:56px; height:56px; border-radius:14px; background:#FFFFFF; display:grid;
        place-items:center; box-shadow:0 10px 22px rgba(15,23,42,.14); font-size:26px;
    }}
    .login-head h1 {{ margin:0; padding:0; font-size:28px; line-height:1.14; font-weight:950; color:#FFFFFF; }}
    .login-head p {{ margin:10px 0 0; color:#EAF2FF; font-size:15px; font-weight:750; }}
    .st-key-login_form_area {{ padding:24px 32px 26px; background:#FFFFFF; color-scheme:light; }}
    .st-key-login_form_area [data-testid="stForm"] {{ border:none; padding:0; }}
    .st-key-login_form_area label {{ color:#1E293B !important; font-weight:850 !important; }}
    .st-key-login_form_area .stTextInput input {{
        border-radius:12px; min-height:48px; background:#F8FAFC !important; font-size:15px;
        border:1px solid #CBD5E1 !important; color:#0F172A !important;
        -webkit-text-fill-color:#0F172A !important;
    }}
    .st-key-login_form_area [data-testid="stFormSubmitButton"],
    .st-key-login_form_area [data-testid="stFormSubmitButton"] > div {{ width:100%; }}
    .st-key-login_form_area button[data-testid^="stBaseButton"] {{
        width:100%; min-height:48px; border-radius:12px; background:{BRAND_BLUE};
        border-color:{BRAND_BLUE}; font-weight:950;
    }}
    .login-brands {{
        display:flex; flex-wrap:wrap; align-items:center; justify-content:center;
        gap:14px 20px; padding:16px 28px 4px; background:#FFFFFF; border-top:1px solid #EEF2F8;
    }}
    .brand-chip {{ display:grid; place-items:center; height:22px; }}
    .brand-chip img {{
        max-height:22px; max-width:76px; object-fit:contain; filter:grayscale(1); opacity:.42;
        transition:filter .2s, opacity .2s;
    }}
    .login-brands:hover .brand-chip img {{ filter:grayscale(0); opacity:.9; }}
    .login-note {{
        padding:12px 32px 30px; text-align:center; color:#64748B; font-size:13px;
        font-weight:750; background:#FFFFFF;
    }}
    .login-foot {{
        margin:26px auto 0; width:min(452px, calc(100vw - 32px)); text-align:center;
        color:#FFFFFF; font-size:14px; line-height:1.7; font-weight:750;
    }}
    .login-foot strong {{ display:block; margin-bottom:6px; font-weight:850; }}
    @media (max-width:560px) {{
        .login-head {{ padding:26px 20px; }} .st-key-login_form_area {{ padding:22px; }}
        .login-head h1 {{ font-size:24px; }}
    }}
    </style>
    """)


def app_styles() -> None:
    html(f"""
    <style>
    :root {{ --brand-primary:{BRAND_PRIMARY}; --brand-blue:{BRAND_BLUE}; --line:#E3EAF6;
             --text-muted:#64748B; }}
    .stApp {{ background:#F6F8FC; }}
    .block-container, [data-testid="stMainBlockContainer"] {{ max-width:1320px; padding-top:28px; }}
    section[data-testid="stSidebar"] {{ background:#F3F6FB; border-right:1px solid #DDE6F2; }}
    .sb-brand {{
        display:flex; align-items:center; gap:12px; padding:12px 14px; margin-bottom:10px;
        background:#FFFFFF; border:1px solid var(--line); border-radius:14px;
        box-shadow:0 6px 16px rgba(15,23,42,.05);
    }}
    .sb-logo {{ flex:0 0 auto; width:64px; height:34px; display:grid; place-items:center; }}
    .sb-logo img {{ max-width:64px; max-height:34px; object-fit:contain; }}
    .sb-txt {{ min-width:0; line-height:1.25; }}
    .sb-txt b {{ display:block; font-size:13.5px; font-weight:900; color:#0B1B46; }}
    .sb-txt span {{ display:block; font-size:11.5px; color:var(--text-muted); font-weight:650; }}
    .sb-user {{
        font-size:12px; color:#475569; padding:8px 10px; margin:6px 0 4px; border-radius:10px;
        background:#FFFFFF; border:1px solid var(--line);
    }}
    .sb-user b {{ color:#0B1B46; }}
    </style>
    """)


def sidebar_brand(subtitulo: str = "") -> None:
    html(
        f"""
    <div class="sb-brand">
        <div class="sb-logo">{forus_logo_html()}</div>
        <div class="sb-txt"><b>Forusight</b><span>{escape(subtitulo)}</span></div>
    </div>
    """,
        sidebar=True,
    )
