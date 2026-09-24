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
    """Estilo general (mismo sistema visual que Catálogo / Repo Control Center)."""
    html(f"""
    <style>
    :root {{ --brand-primary:{BRAND_PRIMARY}; --brand-blue:{BRAND_BLUE};
             --brand-accent:{BRAND_ACCENT}; --bg-main:#F6F8FC; --line:#E3EAF6;
             --text-main:#0F172A; --text-muted:#64748B; }}
    .stApp {{ background:var(--bg-main); color:var(--text-main); }}
    header[data-testid="stHeader"] {{ background:transparent; }}
    div[data-testid="stDecoration"], footer {{ display:none !important; }}
    .block-container, [data-testid="stMainBlockContainer"] {{
        max-width:1320px; padding-top:26px; padding-bottom:56px; }}

    section[data-testid="stSidebar"] {{ background:#F3F6FB; border-right:1px solid #DDE6F2; }}
    section[data-testid="stSidebar"] label, section[data-testid="stSidebar"] p {{ color:#172554; }}
    /* navegación como tarjetas */
    [data-testid="stSidebarNav"] a {{
        border-radius:12px; padding:9px 12px; margin:2px 0; border:1px solid transparent;
        transition:background .15s, border-color .15s, box-shadow .15s;
    }}
    [data-testid="stSidebarNav"] a:hover {{ background:#FFFFFF; border-color:var(--line);
        box-shadow:0 4px 12px rgba(15,23,42,.06); }}
    [data-testid="stSidebarNav"] a[aria-current="page"] {{
        background:linear-gradient(120deg,#EEF3FF,#FFFFFF); border-color:#C9DAFF;
        box-shadow:0 6px 18px rgba(35,103,255,.13); }}
    [data-testid="stSidebarNav"] a span {{ font-weight:800; color:#33415A; }}
    [data-testid="stSidebarNav"] a[aria-current="page"] span {{ color:#0B1B46; font-weight:900; }}

    .sb-brand {{ display:flex; align-items:center; gap:12px; padding:12px 14px; margin-bottom:10px;
        background:#FFFFFF; border:1px solid var(--line); border-radius:14px;
        box-shadow:0 6px 16px rgba(15,23,42,.05); }}
    .sb-logo {{ flex:0 0 auto; width:64px; height:34px; display:grid; place-items:center; }}
    .sb-logo img {{ max-width:64px; max-height:34px; object-fit:contain; }}
    .sb-txt {{ min-width:0; line-height:1.25; }}
    .sb-txt b {{ display:block; font-size:13.5px; font-weight:900; color:#0B1B46; }}
    .sb-txt span {{ display:block; font-size:11.5px; color:var(--text-muted); font-weight:650; }}
    .sb-sec {{ font-size:10.5px; font-weight:900; letter-spacing:.13em; text-transform:uppercase;
        color:#93A3BC; margin:16px 4px 6px; }}
    .sb-user {{ display:flex; align-items:center; gap:7px; font-size:12px; color:#475569;
        padding:8px 10px; margin:6px 0 4px; border-radius:11px; background:#EDF1F8; }}
    .sb-user b {{ color:#0B1B46; }}

    .hero {{ position:relative; overflow:hidden; border-radius:22px; padding:26px 30px;
        background:linear-gradient(125deg,#101B70 0%,{BRAND_PRIMARY} 42%,{BRAND_BLUE} 100%);
        color:#FFFFFF; box-shadow:0 24px 48px rgba(16,27,112,.24); margin-bottom:20px; }}
    .hero::after {{ content:""; position:absolute; right:-90px; top:-120px; width:340px;
        height:340px; border-radius:50%; background:rgba(255,255,255,.09); }}
    .hero::before {{ content:""; position:absolute; right:120px; bottom:-150px; width:260px;
        height:260px; border-radius:50%; background:rgba(0,159,227,.18); }}
    .hero h1 {{ margin:0; padding:0; font-size:28px; font-weight:950; color:#FFFFFF; }}
    .hero p {{ margin:8px 0 0; color:#D8E4FF; font-size:14.5px; font-weight:650; max-width:820px; }}
    .hero .eyebrow {{ display:inline-block; margin-bottom:10px; padding:5px 12px;
        border-radius:999px; background:rgba(255,255,255,.16); font-size:11px; font-weight:900;
        letter-spacing:.14em; text-transform:uppercase; }}
    .hero .meta {{ display:flex; flex-wrap:wrap; gap:8px; margin-top:14px; position:relative;
        z-index:1; }}
    .hero .meta span {{ padding:5px 11px; border-radius:999px; background:rgba(255,255,255,.12);
        border:1px solid rgba(255,255,255,.22); font-size:12px; font-weight:800; }}

    .stepper {{ display:flex; gap:8px; margin:0 0 20px; flex-wrap:wrap; }}
    .step {{ flex:1 1 150px; min-width:140px; padding:12px 14px; border-radius:14px;
        background:#FFFFFF; border:1px solid var(--line); box-shadow:0 6px 16px rgba(15,23,42,.05); }}
    .step .n {{ display:inline-grid; place-items:center; width:22px; height:22px; border-radius:50%;
        background:#E8EEFB; color:#5B6B86; font-size:11px; font-weight:950; margin-bottom:6px; }}
    .step .t {{ display:block; font-size:12.5px; font-weight:850; color:#5B6B86; line-height:1.25; }}
    .step .d {{ display:block; font-size:11.5px; font-weight:650; color:#94A3B8; margin-top:2px; }}
    .step.done {{ border-color:#BBF7D0; background:#F0FDF4; }}
    .step.done .n {{ background:#16A34A; color:#FFFFFF; }} .step.done .t {{ color:#166534; }}
    .step.active {{ border-color:{BRAND_BLUE}; background:#F4F8FF;
        box-shadow:0 0 0 1px #BFD4FF, 0 12px 26px rgba(35,103,255,.16); }}
    .step.active .n {{ background:{BRAND_BLUE}; color:#FFFFFF; }} .step.active .t {{ color:#0B1B46; }}

    .card {{ background:#FFFFFF; border:1px solid var(--line); border-radius:18px; padding:18px 20px;
        box-shadow:0 10px 26px rgba(15,23,42,.05); margin-bottom:14px; }}
    div[class*="st-key-card_"] {{ background:#FFFFFF; border:1px solid var(--line);
        border-radius:18px; padding:16px 18px 10px; box-shadow:0 10px 26px rgba(15,23,42,.05); }}

    .kpis {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(170px,1fr)); gap:12px;
        margin-bottom:16px; }}
    .kpi {{ background:#FFFFFF; border:1px solid var(--line); border-radius:16px; padding:15px 17px;
        box-shadow:0 8px 20px rgba(15,23,42,.05); position:relative; overflow:hidden; }}
    .kpi .ico {{ position:absolute; right:14px; top:14px; width:32px; height:32px; border-radius:10px;
        display:grid; place-items:center; background:#EEF3FF; color:{BRAND_PRIMARY}; }}
    .kpi .lbl {{ font-size:11px; font-weight:900; letter-spacing:.1em; text-transform:uppercase;
        color:var(--text-muted); padding-right:36px; }}
    .kpi .val {{ margin-top:8px; font-size:27px; font-weight:950; color:#0B1B46; line-height:1.05; }}
    .kpi .fnt {{ margin-top:5px; font-size:12px; color:var(--text-muted); font-weight:650; }}
    .kpi.accent {{ background:linear-gradient(140deg,#F2F6FF,#FFFFFF); border-color:#C9DAFF; }}

    .gauge-card {{ background:#FFFFFF; border:1px solid var(--line); border-radius:18px;
        padding:12px 18px 16px; box-shadow:0 10px 26px rgba(15,23,42,.05); }}
    .gauge-txt {{ text-align:center; margin-top:-4px; }}
    .gauge-txt b {{ display:block; font-size:14px; font-weight:900; color:#0B1B46; }}
    .gauge-txt span {{ display:block; font-size:12px; color:var(--text-muted); font-weight:650; }}

    .issue {{ border-radius:14px; padding:12px 15px; margin-bottom:8px; border:1px solid;
        display:flex; gap:12px; align-items:flex-start; }}
    .issue b {{ display:block; font-size:13.5px; font-weight:900; margin-bottom:2px; }}
    .issue span {{ font-size:12.5px; font-weight:600; line-height:1.5; }}
    .issue-error {{ background:#FEF2F2; border-color:#FECACA; color:#991B1B; }}
    .issue-warn {{ background:#FFFBEB; border-color:#FDE68A; color:#92400E; }}
    .issue-info {{ background:#F0F7FF; border-color:#BFDBFE; color:#1E40AF; }}
    .issue-ok {{ background:#F0FDF4; border-color:#BBF7D0; color:#166534; }}

    .sec-head {{ display:flex; align-items:center; gap:12px; margin:6px 0 12px; }}
    .sec-ico {{ width:38px; height:38px; flex:0 0 auto; border-radius:12px; display:grid;
        place-items:center; background:#EEF3FF; color:{BRAND_PRIMARY}; }}
    .sec-txt b {{ display:block; font-size:16px; font-weight:900; color:#0B1B46; line-height:1.2; }}
    .sec-txt span {{ display:block; font-size:12.5px; color:var(--text-muted); font-weight:620; }}

    .chips {{ display:flex; flex-wrap:wrap; gap:7px; margin:2px 0 12px; }}
    .chip {{ display:inline-flex; align-items:center; gap:6px; padding:6px 12px; border-radius:999px;
        font-size:11.5px; font-weight:800; border:1px solid; }}
    .chip-ok {{ background:#E7F7EE; border-color:#BBF7D0; color:#0B7A3B; }}
    .chip-warn {{ background:#FFFBEB; border-color:#FDE68A; color:#92400E; }}
    .chip-err {{ background:#FEE2E2; border-color:#FECACA; color:#B91C1C; }}
    .chip-idle {{ background:#F1F5F9; border-color:#E2E8F0; color:#64748B; }}

    table.mini {{ width:100%; border-collapse:collapse; font-size:12.5px; }}
    table.mini th {{ text-align:left; padding:7px 10px; background:#F3F6FB; color:#0B1B46;
        font-weight:900; border-bottom:1px solid var(--line); }}
    table.mini td {{ padding:7px 10px; border-bottom:1px solid #F1F5F9; color:#334155; }}
    table.mini td.num {{ text-align:right; font-variant-numeric:tabular-nums; font-weight:800;
        color:#0B1B46; }}
    table.mini tr:last-child td {{ border-bottom:none; }}
    .bar-in {{ height:6px; border-radius:4px; background:{BRAND_BLUE}; }}

    .stButton button, button[data-testid^="stBaseButton"] {{ border-radius:12px; font-weight:850; }}
    .stDownloadButton button {{ border-radius:14px; min-height:52px; font-weight:950; font-size:15px;
        background:linear-gradient(135deg,#0B7A3B,#16A34A); border:none; color:#FFF; }}
    div[data-testid="stDataFrame"] {{ border-radius:14px; overflow:hidden; border:1px solid var(--line); }}
    div[data-testid="stFileUploader"] section {{ border-radius:14px; border:1.5px dashed #BFD4FF;
        background:#FAFCFF; }}
    div[data-testid="stTabs"] [role="tablist"] {{ gap:6px !important; background:#EDF1F8 !important;
        padding:5px !important; border-radius:14px !important; border:1px solid var(--line) !important;
        margin-bottom:16px !important; }}
    div[data-testid="stTabs"] [data-testid="stTabHighlight"],
    .stTabs [data-baseweb="tab-highlight"], .stTabs [data-baseweb="tab-border"] {{ display:none !important; }}
    div[data-testid="stTabs"] [role="tab"] {{ border-radius:10px; padding:8px 16px; border:none; }}
    div[data-testid="stTabs"] [role="tab"] p {{ font-weight:800; font-size:13px; color:#5B6B86; }}
    div[data-testid="stTabs"] [role="tab"][aria-selected="true"] {{ background:#FFFFFF !important;
        box-shadow:0 3px 10px rgba(15,23,42,.09); }}
    div[data-testid="stTabs"] [role="tab"][aria-selected="true"] p {{ color:#0B1B46; }}
    div[data-testid="stMetric"] {{ background:#FFFFFF; border:1px solid var(--line); border-radius:14px;
        padding:10px 14px; }}
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


# ------------------------------------------------------------------ componentes


def _ico(nombre: str, size: int = 18) -> str:
    from app.components.icons import icon

    return icon(nombre, size)


def hero(
    titulo: str, subtitulo: str, eyebrow: str = "Reposición CD 320", meta: list[str] | None = None
) -> None:
    chips = "".join(f"<span>{escape(m)}</span>" for m in (meta or []) if m)
    html(f"""<div class="hero"><span class="eyebrow">{escape(eyebrow)}</span>
        <h1>{escape(titulo)}</h1><p>{escape(subtitulo)}</p>
        {f'<div class="meta">{chips}</div>' if chips else ""}</div>""")


def stepper(pasos: list[tuple[str, str]], actual: int) -> None:
    """pasos: (título, detalle); ``actual`` es 1-based (los anteriores quedan listos)."""
    partes = []
    for i, (t, d) in enumerate(pasos, start=1):
        cls = "done" if i < actual else ("active" if i == actual else "")
        marca = "✓" if i < actual else str(i)
        partes.append(
            f'<div class="step {cls}"><span class="n">{marca}</span>'
            f'<span class="t">{escape(t)}</span><span class="d">{escape(d)}</span></div>'
        )
    html(f'<div class="stepper">{"".join(partes)}</div>')


def kpi_row(items: list[tuple[str, str, str, str]]) -> None:
    """items: (etiqueta, valor, pie, icono). El primero va resaltado."""
    celdas = []
    for i, (lbl, val, pie, ico) in enumerate(items):
        cls = "kpi accent" if i == 0 else "kpi"
        celdas.append(
            f'<div class="{cls}"><span class="ico">{_ico(ico, 17)}</span>'
            f'<div class="lbl">{escape(lbl)}</div><div class="val">{escape(val)}</div>'
            f'<div class="fnt">{escape(pie)}</div></div>'
        )
    html(f'<div class="kpis">{"".join(celdas)}</div>')


def section(titulo: str, subtitulo: str = "", ico: str = "layers") -> None:
    html(
        f'<div class="sec-head"><span class="sec-ico">{_ico(ico, 19)}</span>'
        f'<span class="sec-txt"><b>{escape(titulo)}</b><span>{escape(subtitulo)}</span></span></div>'
    )


def chips(items: list[tuple[str, str]]) -> None:
    """(estado, texto) con estado ok|warn|err|idle — siempre con ícono + texto."""
    iconos = {"ok": "check", "warn": "alert", "err": "x-circle", "idle": "clock"}
    html(
        '<div class="chips">'
        + "".join(
            f'<span class="chip chip-{e}">{_ico(iconos.get(e, "info"), 13)}{escape(t)}</span>'
            for e, t in items
        )
        + "</div>"
    )


def issue_box(severidad: str, titulo: str, detalle: str) -> None:
    iconos = {"error": "x-circle", "warn": "alert", "info": "info", "ok": "check-circle"}
    html(
        f'<div class="issue issue-{severidad}">{_ico(iconos.get(severidad, "info"), 16)}'
        f"<div><b>{escape(titulo)}</b><span>{escape(detalle)}</span></div></div>"
    )


def mini_tabla(
    filas: list[tuple], columnas: list[str], num: set[int] | None = None, barra: int | None = None
) -> str:
    """Tabla compacta; ``barra`` = índice de la columna numérica a dibujar como barra."""
    num = num or set()
    maximo = max((float(f[barra]) for f in filas), default=0) if barra is not None else 0
    cab = "".join(f"<th>{escape(c)}</th>" for c in columnas)
    cuerpo = []
    for f in filas:
        tds = []
        for j, v in enumerate(f):
            txt = f"{v:,.0f}" if isinstance(v, int | float) else escape(str(v))
            tds.append(f'<td class="num">{txt}</td>' if j in num else f"<td>{txt}</td>")
        if barra is not None:
            ancho = 100 * float(f[barra]) / maximo if maximo else 0
            tds.append(
                f'<td style="width:28%"><div class="bar-in" style="width:{ancho:.0f}%"></div></td>'
            )
        cuerpo.append("<tr>" + "".join(tds) + "</tr>")
    extra = "<th></th>" if barra is not None else ""
    return f'<table class="mini"><tr>{cab}{extra}</tr>{"".join(cuerpo)}</table>'
