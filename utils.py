"""
utils.py — Shared theming, CSS injection, and UI helper components.
Design system: Deep teal primary, warm amber accent, clean sans-serif typography.
"""

import streamlit as st
import re
from datetime import date, datetime


# ---------------------------------------------------------------------------
# Design Tokens
# ---------------------------------------------------------------------------

PALETTE = {
    "primary":      "#0F4C5C",   # deep teal — brand anchor, matches .streamlit/config.toml
    "primary_lt":   "#1a7a96",
    "accent":       "#E8A838",   # warm amber
    "accent_lt":    "#f5c96d",
    "accent_text":  "#8a6116",   # WCAG AA-safe text variant of accent (raw amber fails on white)
    "success":      "#2E7D32",
    "danger":       "#C62828",
    "warning":      "#F57F17",
    "warning_text": "#9a5b00",   # WCAG AA-safe text variant of warning (raw amber-orange fails on white)
    "info":         "#1565C0",
    "bg":           "#F7F9FA",
    "card":         "#FFFFFF",
    "border":       "#DDE3E7",
    "text":         "#1A2332",
    "muted":        "#6B7A8D",
    "sidebar_bg":   "#0F4C5C",
    "sidebar_txt":  "#EAF4F8",
    "info_bg":      "#E3F2FD",
    "success_bg":   "#E8F5E9",
    "warning_bg":   "#FFF8E1",
    "danger_bg":    "#FFEBEE",
}

# Hero KPI cards (main dashboard headline stats) — a fixed 5-hue categorical
# sequence, assigned by tile POSITION not by value/status, so each metric slot
# reads as a consistent identity across renders. All 5 clear >=4.5:1 white-text
# contrast (validated) except orange at ~4.5:1 exactly — kept bold/large per the
# tile's own type scale to stay legible.
HERO_KPI_COLORS = ["#0F4C5C", "#1565C0", "#2E7D32", "#B85E09", "#B71C1C"]

# Dark-mode overrides — only tokens that need to change; sidebar keeps its
# brand-teal background in both modes, so --primary/--sidebar-* aren't touched.
_DARK_OVERRIDES = {
    "bg":         "#0B1620",
    "card":       "#132430",
    "border":     "#24404b",
    "text":       "#EAF2F5",
    "muted":      "#93A9B6",
    "info_bg":    "#0F2A43",
    "success_bg": "#123420",
    "warning_bg": "#3a2c05",
    "danger_bg":  "#3a1414",
}

_ROOT_VARS = "\n    ".join(f"--{k.replace('_','-')}: {v};" for k, v in PALETTE.items())
_DARK_VARS = "\n        ".join(f"--{k.replace('_','-')}: {v};" for k, v in _DARK_OVERRIDES.items())

_CSS_HEAD = f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=Amiri:wght@400;700&family=Noto+Sans+Bengali:wght@400;500;600;700&display=swap');

:root {{
    {_ROOT_VARS}
    --radius: 10px;
    --shadow: 0 2px 12px rgba(15,76,92,0.10);
}}

@media (prefers-color-scheme: dark) {{
    :root {{
        {_DARK_VARS}
        --shadow: 0 2px 14px rgba(0,0,0,0.45);
    }}
}}
"""

# Plain (non-f) string below — CSS is full of literal { } braces that would
# otherwise be parsed as f-string expressions. Only _CSS_HEAD above needs
# interpolation (the PALETTE-derived :root/dark-mode blocks); everything
# else consumes those tokens exclusively via var(--x).
_CSS_BODY = """
/* ── Global resets ── */
html, body, [class*="css"] {
    font-family: 'Inter', sans-serif !important;
    color: var(--text) !important;
}

/* মাদ্রাসার নাম-হেডিং-এ calligraphy-style font — global Inter reset overাইড
   করতে stylesheet rule ব্যবহার করা হয়েছে, inline style="...!important" না,
   কারণ Streamlit-এর HTML sanitizer inline style attribute-এ !important থাকলে
   পুরো declaration-টাই silently strip করে দেয় (স্ট্যান্ডঅ্যালোন টেস্ট করে
   নিশ্চিত করা হয়েছে) — অথচ <style> ব্লকের ভেতরের rule ঠিকই টিকে থাকে। */
.brand-calligraphy {
    font-family: 'Amiri', 'Noto Sans Bengali', 'Inter', serif !important;
}

/* honeypot ফিল্ড লুকানোর জন্য — bot-দের auto-fill heuristic এই লেবেলে ফাঁদে
   পড়ে, আসল ব্যবহারকারী কখনো দেখেই না। display:none ব্যবহার করা হয়নি কারণ
   কিছু bot বিশেষভাবে সেটা চেক করে এড়িয়ে যায়; position:absolute + off-screen
   placement সাধারণ screen-reader-only প্যাটার্নের মতো, কিন্তু bot ঠিকই field
   হিসেবে দেখে ও পূরণ করে ফেলে। :has() দিয়ে নির্দিষ্ট aria-label-এর ইনপুট
   কন্টেইনার টার্গেট করা হয়েছে, তাই আলাদা কোনো wrapper markup লাগে না। */
div[data-testid="stTextInput"]:has(input[aria-label="Website"]) {
    position: absolute !important;
    left: -9999px !important;
    width: 1px !important;
    height: 1px !important;
    overflow: hidden !important;
}

.main .block-container {
    padding: 1.5rem 2rem 4rem 2rem !important;
    max-width: 1280px !important;
    background: var(--bg) !important;
}

/* ── Sidebar ── */
section[data-testid="stSidebar"] {
    background: linear-gradient(180deg, var(--primary) 0%, #0a3947 100%) !important;
    border-right: none !important;
}
section[data-testid="stSidebar"] * {
    color: var(--sidebar-txt) !important;
}
section[data-testid="stSidebar"] > div:first-child {
    padding-top: 0.5rem;
}

/* Custom scrollbar — subtle, on-brand */
section[data-testid="stSidebar"] ::-webkit-scrollbar { width: 6px; }
section[data-testid="stSidebar"] ::-webkit-scrollbar-thumb {
    background: rgba(255,255,255,0.18);
    border-radius: 10px;
}
section[data-testid="stSidebar"] ::-webkit-scrollbar-thumb:hover {
    background: rgba(255,255,255,0.30);
}

/* Nav buttons — transparent pill, left-aligned, gentle hover slide */
section[data-testid="stSidebar"] .stButton > button {
    background: transparent !important;
    border: 1px solid transparent !important;
    text-align: left !important;
    justify-content: flex-start !important;
    font-size: 0.8rem !important;
    font-weight: 500 !important;
    padding: 0.45rem 0.7rem !important;
    border-radius: 8px !important;
    margin-bottom: 2px !important;
    transition: background 0.15s ease, transform 0.15s ease !important;
    box-shadow: none !important;
}
section[data-testid="stSidebar"] .stButton > button:hover {
    background: rgba(255,255,255,0.10) !important;
    border-color: rgba(255,255,255,0.14) !important;
    transform: translateX(2px);
}
section[data-testid="stSidebar"] .stButton > button:active,
section[data-testid="stSidebar"] .stButton > button:focus:not(:active) {
    box-shadow: none !important;
}

/* Active nav item (rendered as a styled div, not a button — see app.py) */
.sb-nav-active {
    display: block;
    background: rgba(255,255,255,0.14);
    border-left: 3px solid var(--accent);
    border-radius: 0 8px 8px 0;
    padding: 0.45rem 0.7rem 0.45rem calc(0.7rem - 3px);
    font-size: 0.8rem;
    font-weight: 700 !important;
    color: white !important;
    margin-bottom: 2px;
}

/* Nav section labels */
.sb-nav-group {
    font-size: 0.62rem;
    color: rgba(234,244,248,0.42) !important;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    font-weight: 700;
    padding: 0.9rem 0.5rem 0.25rem;
}

/* Profile card */
.sb-profile-card {
    background: rgba(255,255,255,0.08);
    border: 1px solid rgba(255,255,255,0.10);
    border-radius: 10px;
    padding: 0.5rem 0.7rem;
    margin: 4px 0 8px;
    display: flex;
    align-items: center;
    gap: 0.55rem;
}
.sb-profile-avatar {
    width: 30px; height: 30px;
    border-radius: 50%;
    background: var(--accent);
    color: #3d2a00 !important;
    display: flex; align-items: center; justify-content: center;
    font-weight: 700;
    font-size: 0.78rem;
    flex-shrink: 0;
}
.sb-profile-name {
    font-size: 0.76rem;
    font-weight: 600;
    color: white !important;
    line-height: 1.2;
}
.sb-profile-role {
    font-size: 0.62rem;
    color: rgba(234,244,248,0.55) !important;
}

/* Language switcher — blend the segmented control into the dark sidebar */
section[data-testid="stSidebar"] [data-testid="stSegmentedControl"] label {
    font-size: 0.72rem !important;
}
section[data-testid="stSidebar"] div[role="radiogroup"] {
    background: rgba(255,255,255,0.08) !important;
    border-radius: 8px !important;
    padding: 2px !important;
}

/* Logout — quiet by default, warns red on hover (targets the widget's
   auto-generated st-key-<key> class, since wrapping via separate st.markdown
   calls does not actually nest the button in the DOM) */
.st-key-logout_btn button {
    color: rgba(234,244,248,0.75) !important;
}
.st-key-logout_btn button:hover {
    background: rgba(198,40,40,0.25) !important;
    border-color: rgba(198,40,40,0.4) !important;
    color: #ffdcdc !important;
}

.sb-divider {
    border: none;
    border-top: 1px solid rgba(255,255,255,0.10);
    margin: 6px 0;
}
.sb-footer {
    font-size: 0.58rem;
    color: rgba(234,244,248,0.30) !important;
    text-align: center;
    padding: 4px 0 2px;
}

/* ── Cards ── */
.erp-card {
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    padding: 1.25rem 1.5rem;
    box-shadow: var(--shadow);
    margin-bottom: 1rem;
}

/* ── KPI metric tiles ── */
.kpi-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
    gap: 1rem;
    margin-bottom: 1.5rem;
}
.kpi-tile {
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    padding: 1rem 1.25rem;
    text-align: center;
    box-shadow: var(--shadow);
    border-top: 3px solid var(--primary);
    transition: transform 0.15s ease, box-shadow 0.15s ease;
}
.kpi-tile:hover {
    transform: translateY(-2px);
    box-shadow: 0 6px 20px rgba(15,76,92,0.16);
}
.kpi-tile .kpi-val {
    font-size: 2rem;
    font-weight: 700;
    color: var(--primary);
    line-height: 1;
}
.kpi-tile .kpi-label {
    font-size: 0.75rem;
    color: var(--muted);
    text-transform: uppercase;
    letter-spacing: 0.05em;
    margin-top: 0.35rem;
}
.kpi-tile.accent { border-top-color: var(--accent); }
.kpi-tile.accent .kpi-val { color: var(--accent-text); }
.kpi-tile.success { border-top-color: var(--success); }
.kpi-tile.success .kpi-val { color: var(--success); }
.kpi-tile.danger { border-top-color: var(--danger); }
.kpi-tile.danger .kpi-val { color: var(--danger); }
.kpi-tile.warning { border-top-color: var(--warning); }
.kpi-tile.warning .kpi-val { color: var(--warning-text); }

/* ── Hero KPI cards (main dashboard headline stats) ── */
.hero-kpi-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(170px, 1fr));
    gap: 14px;
    margin-bottom: 1.5rem;
}
.hero-kpi-tile {
    border-radius: 14px;
    padding: 1.1rem 1.25rem;
    color: white;
    box-shadow: 0 4px 14px rgba(0,0,0,0.14);
    transition: transform 0.15s ease, box-shadow 0.15s ease;
}
.hero-kpi-tile:hover {
    transform: translateY(-3px);
    box-shadow: 0 10px 24px rgba(0,0,0,0.20);
}
.hero-kpi-icon {
    width: 40px; height: 40px;
    background: rgba(255,255,255,0.20);
    border-radius: 10px;
    display: flex; align-items: center; justify-content: center;
    font-size: 1.25rem;
    margin-bottom: 0.65rem;
}
.hero-kpi-value {
    font-size: 1.9rem;
    font-weight: 800;
    line-height: 1;
}
.hero-kpi-label {
    font-size: 0.72rem;
    font-weight: 600;
    opacity: 0.95;
    margin-top: 0.35rem;
    text-transform: uppercase;
    letter-spacing: 0.04em;
}

/* ── Page header ── */
.page-header {
    display: flex;
    align-items: center;
    gap: 0.75rem;
    margin-bottom: 1.5rem;
    padding-bottom: 0.75rem;
    border-bottom: 2px solid var(--border);
}
.page-header .icon {
    width: 40px; height: 40px;
    background: var(--primary);
    border-radius: 10px;
    display: flex; align-items: center; justify-content: center;
    font-size: 1.3rem;
}
.page-header h1 {
    font-size: 1.4rem !important;
    font-weight: 700 !important;
    margin: 0 !important;
    padding: 0 !important;
    color: var(--text) !important;
}
.page-header .sub {
    font-size: 0.8rem;
    color: var(--muted);
    margin-top: 0.1rem;
}

/* ── Status badges ── */
.badge {
    display: inline-block;
    padding: 0.2rem 0.65rem;
    border-radius: 20px;
    font-size: 0.72rem;
    font-weight: 600;
    letter-spacing: 0.03em;
    text-transform: uppercase;
}
.badge-success { background: var(--success-bg); color: var(--success); }
.badge-warning { background: var(--warning-bg); color: var(--warning-text); }
.badge-danger  { background: var(--danger-bg); color: var(--danger); }
.badge-info    { background: var(--info-bg); color: var(--info); }
.badge-muted   { background: #ECEFF1; color: #546E7A; }

/* ── Step indicator ── */
.step-bar {
    display: flex;
    align-items: center;
    margin-bottom: 1.5rem;
}
.step-item {
    display: flex; align-items: center; gap: 0.4rem;
}
.step-dot {
    width: 28px; height: 28px;
    border-radius: 50%;
    background: var(--border);
    color: var(--muted);
    font-size: 0.8rem;
    font-weight: 700;
    display: flex; align-items: center; justify-content: center;
}
.step-dot.active   { background: var(--primary); color: white; }
.step-dot.done     { background: var(--success); color: white; }
.step-label { font-size: 0.78rem; color: var(--muted); }
.step-label.active { color: var(--primary); font-weight: 600; }
.step-connector {
    flex: 1;
    height: 2px;
    background: var(--border);
    margin: 0 0.5rem;
}
.step-connector.done { background: var(--success); }

/* ── Table tweaks ── */
.stDataFrame { border-radius: var(--radius) !important; overflow: hidden; }

/* ── Form labels ── */
.stTextInput > label, .stSelectbox > label,
.stNumberInput > label, .stDateInput > label,
.stTextArea > label { font-weight: 500; font-size: 0.85rem; }

/* ── Primary button ── */
.stButton > button[kind="primary"],
.stFormSubmitButton > button {
    background: var(--primary) !important;
    color: white !important;
    border: none !important;
    border-radius: 8px !important;
    font-weight: 600 !important;
    padding: 0.5rem 1.5rem !important;
    transition: background 0.2s !important;
}
.stButton > button[kind="primary"]:hover {
    background: var(--primary-lt) !important;
}

/* ── Divider ── */
.section-divider {
    border: none;
    border-top: 1px solid var(--border);
    margin: 1.25rem 0;
}

/* ── Voucher print area ── */
.voucher {
    border: 2px solid var(--primary);
    border-radius: var(--radius);
    padding: 1.5rem;
    font-family: 'Inter', sans-serif;
    max-width: 400px;
}
.voucher .vch-header {
    text-align: center;
    border-bottom: 1px solid var(--border);
    padding-bottom: 0.75rem;
    margin-bottom: 0.75rem;
}
.voucher .vch-row {
    display: flex;
    justify-content: space-between;
    font-size: 0.85rem;
    padding: 0.2rem 0;
}
.voucher .vch-total {
    font-weight: 700;
    font-size: 1.1rem;
    color: var(--primary);
    border-top: 1px solid var(--border);
    margin-top: 0.5rem;
    padding-top: 0.5rem;
}

/* ── Alert boxes ── */
.alert {
    padding: 0.75rem 1rem;
    border-radius: 8px;
    font-size: 0.88rem;
    margin-bottom: 1rem;
}
.alert-info    { background: var(--info-bg); border-left: 4px solid var(--info); }
.alert-success { background: var(--success-bg); border-left: 4px solid var(--success); }
.alert-warning { background: var(--warning-bg); border-left: 4px solid var(--warning); }
.alert-danger  { background: var(--danger-bg); border-left: 4px solid var(--danger); }
</style>
"""

GLOBAL_CSS = _CSS_HEAD + _CSS_BODY


def inject_css():
    st.markdown(GLOBAL_CSS, unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# UI components
# ---------------------------------------------------------------------------

def page_header(icon: str, title: str, subtitle: str = ""):
    st.markdown(
        f"""<div class="page-header">
            <div class="icon">{icon}</div>
            <div><h1>{title}</h1>
            {"<div class='sub'>" + subtitle + "</div>" if subtitle else ""}
            </div>
        </div>""",
        unsafe_allow_html=True,
    )


def kpi_row(metrics: list[dict]):
    """
    metrics = [{"label": "Total Students", "value": 120, "cls": ""}]
    cls options: accent | success | danger | ""
    """
    tiles = ""
    for m in metrics:
        cls = m.get("cls", "")
        tiles += f"""<div class="kpi-tile {cls}">
            <div class="kpi-val">{m['value']}</div>
            <div class="kpi-label">{m['label']}</div>
        </div>"""
    st.markdown(f'<div class="kpi-grid">{tiles}</div>', unsafe_allow_html=True)


def hero_kpi_row(metrics: list[dict]):
    """
    Solid-color headline KPI cards for the main dashboard — an icon badge,
    a large number, and a label, one per tile.

    metrics = [{"icon": "👥", "value": 120, "label": "Students"}]

    Colors are assigned by tile POSITION from HERO_KPI_COLORS, cycling if
    there are more than 5 — this is categorical identity (each slot always
    gets the same hue), not a status encoding, so don't reorder metrics
    between renders if you want the color-to-metric mapping to stay stable.
    """
    tiles = ""
    for i, m in enumerate(metrics):
        color = HERO_KPI_COLORS[i % len(HERO_KPI_COLORS)]
        icon = m.get("icon", "")
        tiles += f"""<div class="hero-kpi-tile" style="background:{color}">
            <div class="hero-kpi-icon">{icon}</div>
            <div class="hero-kpi-value">{m['value']}</div>
            <div class="hero-kpi-label">{m['label']}</div>
        </div>"""
    st.markdown(f'<div class="hero-kpi-grid">{tiles}</div>', unsafe_allow_html=True)


def badge(text: str, kind: str = "info"):
    """kind: success | warning | danger | info | muted"""
    return f'<span class="badge badge-{kind}">{text}</span>'


def card(content_html: str):
    st.markdown(f'<div class="erp-card">{content_html}</div>', unsafe_allow_html=True)


def step_bar(steps: list[str], current: int):
    """current is 0-indexed."""
    html = '<div class="step-bar">'
    for i, label in enumerate(steps):
        if i > 0:
            done_cls = "done" if i <= current else ""
            html += f'<div class="step-connector {done_cls}"></div>'
        if i < current:
            dot_cls, lbl_cls = "done", ""
            num = "✓"
        elif i == current:
            dot_cls, lbl_cls = "active", "active"
            num = str(i + 1)
        else:
            dot_cls, lbl_cls = "", ""
            num = str(i + 1)
        html += f"""<div class="step-item">
            <div class="step-dot {dot_cls}">{num}</div>
            <span class="step-label {lbl_cls}">{label}</span>
        </div>"""
    html += "</div>"
    st.markdown(html, unsafe_allow_html=True)


def alert(msg: str, kind: str = "info"):
    """kind: info | success | warning | danger"""
    st.markdown(f'<div class="alert alert-{kind}">{msg}</div>', unsafe_allow_html=True)


def divider():
    st.markdown('<hr class="section-divider">', unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Session helpers
# ---------------------------------------------------------------------------

def get_tenant_id() -> int:
    return st.session_state.get("tenant_id", 1)


def get_active_session_id() -> int | None:
    return st.session_state.get("active_session_id")


def flatten_html(html: str) -> str:
    """
    Multi-line HTML built from an indented Python f-string renders broken in
    st.markdown(unsafe_allow_html=True): CommonMark treats a line indented 4+
    spaces as a code block, so the whole block shows as escaped text instead
    of real HTML the moment the opening tag has that much leading whitespace.
    Stripping per-line leading whitespace (harmless — HTML ignores it) fixes
    this without needing every HTML-building function to avoid indentation.
    """
    return "\n".join(line.lstrip() for line in html.splitlines())


def print_button(label: str, doc_id: str | None = None, height: int = 55) -> None:
    """
    A working browser-print / "Save as PDF" button.

    Renders via st.components.v1.html() (a real <iframe>, its own JS
    execution context) rather than st.markdown(unsafe_allow_html=True).
    Two real, confirmed Streamlit limitations rule out the markdown route:
    1. A raw `onclick="..."` attribute gets mapped onto React's `onClick`
       prop by Streamlit's renderer, which crashes the moment the button is
       clicked: "Minified React error #231 ... Expected onClick listener to
       be a function, instead got a value of `string` type." Confirmed with
       even the most trivial `st.markdown('<button onclick="...">')`.
    2. A `<script>` tag inserted via st.markdown's innerHTML never executes
       at all — browsers don't run scripts inserted that way, so an
       addEventListener-based rewrite doesn't fix it either.
    Neither limitation applies inside a genuine iframe, which is what
    components.html() provides.

    doc_id=None  -> prints the whole current page (window.parent.print()) —
                    use this when the page already has @media print CSS.
    doc_id="xyz" -> opens a new window containing ONLY the innerHTML of the
                    parent-page element with id="xyz" (styled minimally) and
                    prints that instead. The element must already be on the
                    page, e.g. via st.markdown(f'<div id="xyz">...</div>').
                    Looked up via window.parent.document since the button
                    itself lives in a separate iframe document.
    """
    import streamlit.components.v1 as components

    if doc_id:
        action = f"""
            var w = window.open('', '_blank');
            var c = window.parent.document.getElementById('{doc_id}').innerHTML;
            var h = '<html><head><title>Print</title><style>'
                + 'body{{font-family:Inter,sans-serif;margin:20px}}'
                + 'table{{border-collapse:collapse;width:100%}}'
                + 'td,th{{border:1px solid #ccc;padding:5px 8px;font-size:12px}}'
                + 'th{{background:#0F4C5C;color:white}}'
                + '</style></head><body>' + c + '</body></html>';
            w.document.write(h);
            w.document.close();
            w.print();
        """
    else:
        action = "window.parent.print();"

    components.html(f"""
    <button id="print_btn_widget" style="background:#0F4C5C;color:white;border:none;
            padding:0.5rem 1.5rem;border-radius:8px;font-weight:600;cursor:pointer;
            font-size:0.9rem;font-family:Inter,sans-serif;">{label}</button>
    <script>
    document.getElementById('print_btn_widget').addEventListener('click', function() {{
        {action}
    }});
    </script>
    """, height=height)


# ---------------------------------------------------------------------------
# Validators
# ---------------------------------------------------------------------------

def validate_mobile(m: str) -> bool:
    return bool(re.match(r"^(\+?880|0)?1[3-9]\d{8}$", m.strip())) if m else True


def validate_required(fields: dict) -> list[str]:
    """Returns list of missing field names."""
    return [k for k, v in fields.items() if not v and v != 0]


# ---------------------------------------------------------------------------
# Grade utilities
# ---------------------------------------------------------------------------

GRADE_TABLE = [
    (80, "A+", 5.0),
    (70, "A",  4.0),
    (60, "A-", 3.5),
    (50, "B",  3.0),
    (40, "C",  2.0),
    (33, "D",  1.0),
    (0,  "F",  0.0),
]


def get_grade(percent: float) -> tuple[str, float]:
    for threshold, letter, gpa in GRADE_TABLE:
        if percent >= threshold:
            return letter, gpa
    return "F", 0.0


def months_list():
    return [
        "January", "February", "March", "April", "May", "June",
        "July", "August", "September", "October", "November", "December",
    ]


def current_year():
    return datetime.now().year
