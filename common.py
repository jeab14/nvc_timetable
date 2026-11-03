"""ส่วนกลางของหน้าเว็บ: session, ผู้ใช้ปัจจุบัน, ภาคเรียน, สไตล์ และตัวช่วยแสดงผล"""
from __future__ import annotations

from contextlib import contextmanager

import pandas as pd
import streamlit as st
from sqlalchemy import select

from .. import auth, config, db
from ..models import Term, Timetable, User
from ..permissions import ROLE_LABELS, Ctx, PermissionDenied
from ..timetable_service import STATUS_LABEL
from .icons import ic, mi

THEME_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Sarabun:wght@400;500;600;700&display=swap');
:root{--navy:#0b3d91;--blue:#1d4ed8;--sky:#e8f1ff;--sky2:#f4f8ff;--line:#dbe5f5;--text:#0f172a;--muted:#64748b;
      --ok:#15803d;--okbg:#dcfce7;--warn:#b45309;--warnbg:#fef3c7;--err:#b91c1c;--errbg:#fee2e2}
html, body, [class*="css"], .stMarkdown, .stText, button, input, textarea, select, label {
  font-family:'Sarabun','Noto Sans Thai','Tahoma',sans-serif !important;}
/* พื้นหลังไล่เฉดอ่อน ๆ */
[data-testid="stApp"]{background:linear-gradient(180deg,#e9f2ff 0%,#f5f9ff 260px,#ffffff 100%) !important;}
[data-testid="stHeader"]{background:transparent !important;}
.block-container{padding-top:1.4rem !important;padding-bottom:3rem !important;max-width:1400px}
/* ---------------- Sidebar ---------------- */
[data-testid="stSidebar"]{background:#ffffff !important;border-right:1px solid var(--line);}
[data-testid="stSidebarNav"] [data-testid="stNavSectionHeader"], [data-testid="stSidebarNav"] header{
  color:var(--muted) !important;font-size:12px !important;font-weight:700 !important;letter-spacing:.3px;margin-top:6px}
[data-testid="stSidebarNav"] a{border-radius:10px !important;margin:0;min-height:36px}
[data-testid="stSidebarNav"] a span{color:#1e293b !important;font-size:14.5px}
[data-testid="stSidebarNav"] a:hover{background:var(--sky2) !important}
[data-testid="stSidebarNav"] a[aria-current="page"]{background:var(--sky) !important;box-shadow:inset 3px 0 0 var(--blue)}
[data-testid="stSidebarNav"] a[aria-current="page"] span{color:var(--navy) !important;font-weight:700}
.nvc-usercard{border:1px solid var(--line);border-radius:14px;padding:12px;background:linear-gradient(180deg,#f7faff,#ffffff);display:flex;gap:10px;align-items:center}
.nvc-avatar{width:40px;height:40px;border-radius:50%;background:linear-gradient(135deg,var(--navy),#3b82f6);color:#fff;
  display:flex;align-items:center;justify-content:center;font-weight:700;font-size:17px;flex:0 0 40px}
.nvc-usercard .n{font-weight:700;color:var(--text);font-size:14px;line-height:1.25}
.nvc-usercard .r{font-size:12.5px;color:var(--muted)}
.nvc-college{font-size:12px;color:var(--muted);text-align:center;margin-top:8px}
/* ---------------- ส่วนหัวหน้า ---------------- */
.st-key-nvc_header{background:linear-gradient(110deg,#0b3d91 0%,#1d4ed8 55%,#3b82f6 100%);border-radius:18px;
  padding:16px 22px 14px !important;margin-bottom:14px;box-shadow:0 8px 24px -12px rgba(11,61,145,.55)}
.st-key-nvc_header *{color:#fff}
.nvc-crumb{font-size:12.5px;color:#cfe0ff !important;letter-spacing:.2px}
.nvc-crumb b{color:#fff !important;font-weight:600}
.nvc-title{font-size:26px;font-weight:700;margin:2px 0 0;line-height:1.25;color:#fff !important}
.nvc-sub{font-size:14px;color:#dbe8ff !important;margin-top:2px}
.st-key-nvc_header [data-testid="stSelectbox"] [data-baseweb="select"] > div,
.st-key-nvc_header [data-testid="stSelectbox"] div:has(> input){background:#ffffff !important;border-color:#ffffff !important;border-radius:10px !important}
.st-key-nvc_header [data-testid="stSelectbox"] input,
.st-key-nvc_header [data-testid="stSelectbox"] [data-baseweb="select"] *{color:#0b3d91 !important;-webkit-text-fill-color:#0b3d91 !important;font-weight:600}
.st-key-nvc_header [data-testid="stSelectbox"] svg{fill:#0b3d91 !important;color:#0b3d91 !important}
.st-key-nvc_header label p{font-size:12px !important;color:#cfe0ff !important}
.nvc-chip{display:flex;align-items:center;gap:8px;background:rgba(255,255,255,.14);border:1px solid rgba(255,255,255,.35);
  border-radius:999px;padding:4px 12px 4px 4px;font-size:13px;margin-top:2px;max-width:100%;overflow:hidden}
.nvc-chip .t{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;line-height:1.2}
.nvc-chip .t small{display:block;font-size:11.5px;color:#cfe0ff !important}
.nvc-chip .a{width:26px;height:26px;border-radius:50%;background:#fff;color:var(--navy) !important;display:flex;align-items:center;justify-content:center;font-weight:700}
/* ---------------- การ์ด / หัวข้อ ---------------- */
.nvc-card{background:#fff;border:1px solid var(--line);border-radius:14px;padding:14px 16px;box-shadow:0 1px 3px rgba(15,23,42,.05)}
.nvc-kpi{display:flex;gap:12px;align-items:center;background:#fff;border:1px solid var(--line);border-radius:14px;padding:14px 16px;
  box-shadow:0 1px 3px rgba(15,23,42,.05);height:100%}
.nvc-kpis{display:grid;grid-template-columns:repeat(var(--n),minmax(0,1fr));gap:12px;margin-bottom:4px}
.nvc-kpi .i{width:44px;height:44px;border-radius:12px;background:var(--sky);display:flex;align-items:center;justify-content:center;font-size:22px;flex:0 0 44px}
.nvc-kpi .v{font-size:26px;font-weight:700;color:var(--navy);line-height:1.05}
.nvc-kpi .l{font-size:13px;color:var(--muted)}
.nvc-kpi.ok .i{background:var(--okbg)} .nvc-kpi.warn .i{background:var(--warnbg)} .nvc-kpi.err .i{background:var(--errbg)}
.nvc-kpi.err .v{color:var(--err)} .nvc-kpi.ok .v{color:var(--ok)}
.nvc-sec{display:flex;align-items:center;gap:8px;margin:18px 0 8px}
.nvc-sec .t{font-size:18px;font-weight:700;color:var(--navy)}
.nvc-sec .c{font-size:13px;color:var(--muted);margin-left:4px}
.nvc-sec::after{content:"";flex:1;height:1px;background:var(--line);margin-left:8px}
.nvc-empty{border:1.5px dashed #c7d7f2;border-radius:14px;padding:22px;text-align:center;color:var(--muted);background:#fbfdff}
.nvc-empty .i{font-size:30px}
.nvc-steps{display:grid;grid-template-columns:repeat(4,1fr);gap:10px}
.nvc-step{border:1px solid var(--line);border-radius:14px;padding:12px 14px;background:#fff;position:relative}
.nvc-step .n{width:26px;height:26px;border-radius:50%;display:inline-flex;align-items:center;justify-content:center;font-weight:700;font-size:13px;background:#e2e8f0;color:#334155;margin-right:6px}
.nvc-step.done{border-color:#bbf7d0;background:#f6fef9}.nvc-step.done .n{background:var(--ok);color:#fff}
.nvc-step.now{border-color:#93c5fd;background:#f3f8ff;box-shadow:0 0 0 3px #dbeafe}.nvc-step.now .n{background:var(--blue);color:#fff}
.nvc-step .t{font-weight:700;color:var(--text);font-size:14.5px}
.nvc-step .d{font-size:12.5px;color:var(--muted);margin-top:4px}
.nvc-bar{height:8px;border-radius:99px;background:#e8eef9;overflow:hidden;margin-top:4px}
.nvc-bar span{display:block;height:100%;border-radius:99px}
.nvc-count{font-size:13px;color:var(--muted);margin:2px 0 6px}
.nvc-ferr{color:var(--err);font-size:13px;margin:-6px 0 8px;display:flex;gap:4px}
.nvc-fok{color:var(--ok);font-size:13px;margin:-6px 0 8px}
.nvc-hint{color:var(--muted);font-size:12.5px;margin:-6px 0 8px}
/* ---------------- ไอคอน (Material Symbols ที่มากับ Streamlit) ---------------- */
.mi{font-family:'Material Symbols Rounded' !important;font-weight:normal;font-style:normal;font-size:1.2em;line-height:1;display:inline-block;
  vertical-align:-0.22em;letter-spacing:normal;text-transform:none;white-space:nowrap;direction:ltr;-webkit-font-smoothing:antialiased;
  font-feature-settings:'liga';font-variation-settings:'FILL' 0,'wght' 400,'GRAD' 0,'opsz' 24}
.nvc-title .mi{font-size:28px;margin-right:6px;vertical-align:-5px;opacity:.95}
.nvc-sec .mi{font-size:22px;color:var(--blue);vertical-align:-5px}
.nvc-kpi .i .mi{font-size:24px;color:var(--navy)}
.nvc-kpi.ok .i .mi{color:var(--ok)} .nvc-kpi.warn .i .mi{color:var(--warn)} .nvc-kpi.err .i .mi{color:var(--err)}
.nvc-empty .i .mi{font-size:36px;color:#94a3b8}
.badge .mi{font-size:15px;vertical-align:-3px;margin-right:2px}
.nvc-ferr .mi,.nvc-fok .mi{font-size:16px;vertical-align:-3px}
.nvc-conf{border:1px solid #fecaca;background:#fff5f5;border-radius:12px;padding:10px 12px;margin:6px 0;color:#7f1d1d}
.nvc-conf b{color:#991b1b}.nvc-conf .mi{color:#dc2626;font-size:18px;vertical-align:-4px;margin-right:4px}
/* ---------------- ป้ายสถานะ ---------------- */
.badge{display:inline-block;border-radius:999px;padding:2px 10px;font-size:12.5px;font-weight:600;border:1px solid transparent}
.b-ok{background:var(--okbg);color:#166534;border-color:#bbf7d0}.b-warn{background:var(--warnbg);color:#92400e;border-color:#fde68a}
.b-err{background:var(--errbg);color:#991b1b;border-color:#fecaca}.b-info{background:var(--sky);color:var(--navy);border-color:#cfe0ff}
/* ---------------- ปุ่ม ---------------- */
button[kind="primary"], [data-testid="stBaseButton-primary"], [data-testid="stBaseButton-primaryFormSubmit"]{
  background:linear-gradient(180deg,#2563eb,#1d4ed8) !important;border:1px solid #1d4ed8 !important;color:#fff !important;
  font-weight:600 !important;box-shadow:0 4px 12px -6px rgba(29,78,216,.7) !important}
button[kind="primary"]:hover, [data-testid="stBaseButton-primary"]:hover{background:linear-gradient(180deg,#1d4ed8,#0b3d91) !important}
[data-testid="stBaseButton-secondary"], [data-testid="stBaseButton-secondaryFormSubmit"], [data-testid="stDownloadButton"] button{
  background:#fff !important;border:1px solid #c7d7f2 !important;color:var(--navy) !important;font-weight:600 !important}
[data-testid="stBaseButton-secondary"]:hover, [data-testid="stDownloadButton"] button:hover{background:var(--sky2) !important;border-color:#93b4ec !important}
button:disabled{opacity:.55 !important;box-shadow:none !important}
/* ---------------- อื่น ๆ ---------------- */
[data-testid="stExpander"] details{border-radius:12px !important;border-color:var(--line) !important;background:#fff}
[data-testid="stTabs"] [data-baseweb="tab-list"]{gap:2px;border-bottom:1px solid var(--line)}
[data-testid="stTabs"] [data-baseweb="tab"]{padding:8px 12px;border-radius:10px 10px 0 0}
[data-testid="stTabs"] [aria-selected="true"]{background:var(--sky)}
[data-testid="stMetricValue"]{color:var(--navy)}
[data-testid="stAlert"]{border-radius:12px}
h3{color:var(--navy)}
/* ---------------- มือถือ / แท็บเล็ต ---------------- */
@media (max-width: 900px){
  .nvc-steps{grid-template-columns:repeat(2,1fr)}
  .nvc-kpis{grid-template-columns:repeat(3,minmax(0,1fr))}
  .nvc-title{font-size:22px}
}
@media (max-width: 640px){
  .block-container{padding-left:.8rem !important;padding-right:.8rem !important;padding-top:3.2rem !important}
  .nvc-kpis{grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}
  .st-key-nvc_header{padding:12px 14px !important;border-radius:14px}
  .nvc-title{font-size:20px}
  .nvc-kpi{padding:10px 12px}.nvc-kpi .v{font-size:22px}.nvc-kpi .i{width:36px;height:36px;font-size:18px;flex-basis:36px}
  .nvc-steps{grid-template-columns:1fr}
}
/* ================= ธีมแบบแถบเมนูด้านบน (navy) ================= */
[data-testid="stApp"]{background:#f4f7fc !important;}
[data-testid="stHeader"]{background:linear-gradient(90deg,#0b1f4d 0%,#13307a 55%,#1d4ed8 100%) !important;height:64px !important;
  box-shadow:0 4px 18px -8px rgba(11,31,77,.6);border-bottom:3px solid #3b82f6}
[data-testid="stHeader"] [data-testid="stToolbar"]{height:64px;align-items:center}
[data-testid="stHeaderLogo"]{height:38px !important;max-height:38px !important}
[data-testid="stTopNav"] button, [data-testid="stTopNav"] a{color:#e2ebff !important;border-radius:10px !important;font-weight:600 !important}
[data-testid="stTopNav"] button p, [data-testid="stTopNav"] a span, [data-testid="stTopNav"] a p{color:#e2ebff !important;font-size:15px !important;font-weight:600 !important}
[data-testid="stTopNav"] svg{color:#bcd0ff !important;fill:currentColor}
[data-testid="stTopNav"] button:hover, [data-testid="stTopNav"] a:hover{background:rgba(255,255,255,.12) !important}
[data-testid="stTopNav"] a[aria-current="page"]{background:rgba(255,255,255,.16) !important;box-shadow:inset 0 -3px 0 #60a5fa}
[data-testid="stTopNav"] a[aria-current="page"] span,[data-testid="stTopNav"] a[aria-current="page"] p{color:#ffffff !important}
[data-testid="stToolbarActions"] button, [data-testid="stMainMenu"] button{color:#e2ebff !important}
[data-testid="stExpandSidebarButton"], [data-testid="stExpandSidebarButton"] *{color:#ffffff !important}
[data-testid="stExpandSidebarButton"]{background:rgba(255,255,255,.14) !important;border-radius:10px !important}
[data-testid="stSidebarNav"] a[aria-current="page"]{background:#e8f0ff !important}
.block-container{padding-top:5.2rem !important;max-width:1440px}
/* ---- ส่วนหัวหน้า: การ์ดสีขาวแบบในภาพตัวอย่าง ---- */
.st-key-nvc_header{background:#ffffff !important;border:1px solid #e3eaf6;border-radius:18px;padding:18px 22px !important;
  box-shadow:0 2px 10px -4px rgba(15,23,42,.08) !important;margin-bottom:16px}
.st-key-nvc_header *{color:inherit}
.nvc-pill{display:inline-block;background:#e8f0ff;color:#1d4ed8 !important;font-weight:700;font-size:12.5px;border-radius:999px;padding:3px 12px;margin-bottom:6px}
.st-key-nvc_header .nvc-title{color:#0b1f4d !important;font-size:27px}
.st-key-nvc_header .nvc-title .mi{color:#1d4ed8 !important}
.st-key-nvc_header .nvc-sub{color:#475569 !important}
.st-key-nvc_header label p{color:#475569 !important;font-size:12.5px !important}
.st-key-nvc_header [data-testid="stSelectbox"] [data-baseweb="select"] > div{background:#f8fbff !important;border-color:#d6e2f5 !important}
.st-key-nvc_header [data-testid="stSelectbox"] [data-baseweb="select"] *{color:#0b1f4d !important;-webkit-text-fill-color:#0b1f4d !important}
.st-key-nvc_header [data-testid="stSelectbox"] svg{fill:#0b1f4d !important;color:#0b1f4d !important}
.st-key-nvc_header .nvc-chip{background:#f1f6ff;border:1px solid #d6e2f5;color:#0b1f4d;padding:6px 12px}
.st-key-nvc_header .nvc-chip .t{color:#0b1f4d !important;font-weight:600}
.st-key-nvc_header .nvc-chip .t small{color:#64748b !important}
.nvc-chip .dot{width:9px;height:9px;border-radius:50%;background:#22c55e;box-shadow:0 0 0 3px #dcfce7;flex:0 0 9px}
.st-key-nvc_logout button{background:linear-gradient(180deg,#ef4444,#dc2626) !important;border:1px solid #dc2626 !important;color:#fff !important}
.st-key-nvc_logout button p, .st-key-nvc_logout button span{color:#fff !important}
.st-key-nvc_logout button:hover{background:#b91c1c !important}
.st-key-nvc_logout button{white-space:nowrap !important;padding:0 10px !important;min-height:40px}
.st-key-nvc_logout button p{white-space:nowrap !important;font-size:14px !important}
@media (max-width:1280px){.st-key-nvc_logout button [data-testid="stMarkdownContainer"]{display:none}}
/* เมนูด้านบนกระชับ ให้แสดงครบไม่ถูกย่อเป็น "more" */
[data-testid="stTopNav"] button, [data-testid="stTopNav"] a{padding:4px 10px !important;white-space:nowrap !important}
[data-testid="stTopNav"] button p, [data-testid="stTopNav"] a span, [data-testid="stTopNav"] a p{font-size:14.5px !important;white-space:nowrap !important}
/* ---- ปุ่มหลักโทนน้ำเงินเข้ม ---- */
button[kind="primary"], [data-testid="stBaseButton-primary"], [data-testid="stBaseButton-primaryFormSubmit"]{
  background:linear-gradient(180deg,#1e40af,#13307a) !important;border-color:#13307a !important}
[data-testid="stExpander"] details, .nvc-card, .nvc-kpi{border-radius:16px !important}
@media (max-width: 640px){
  .block-container{padding-top:4.8rem !important}
  .st-key-nvc_header{padding:14px !important}
  .st-key-nvc_header .nvc-title{font-size:21px}
}
</style>
"""

STATUS_BADGE = {
    "success": "b-ok", "published": "b-ok", "approved": "b-ok", "pending_review": "b-info", "draft": "b-warn",
    "partial": "b-warn", "validation_failed": "b-err", "infeasible": "b-err", "not_scheduled": "b-info",
    "superseded": "b-info", "running": "b-info", "deleted": "b-err",
}
VALIDATION_BADGE = {"pass": ("b-ok", ic("check_circle") + "ผ่านทั้งหมด"), "warn": ("b-warn", ic("warning") + "ผ่าน แต่มีคำเตือน"),
                    "fail": ("b-err", ic("cancel") + "ไม่ผ่าน")}


def badge(text: str, cls: str) -> str:
    return f'<span class="badge {cls}">{text}</span>'


def status_badge(status: str) -> str:
    return badge(STATUS_LABEL.get(status, status), STATUS_BADGE.get(status, "b-info"))


def validation_badge(v: str | None) -> str:
    if not v:
        return badge(ic("pending") + "ยังไม่ได้ตรวจสอบ", "b-info")
    cls, txt = VALIDATION_BADGE[v]
    return badge(txt, cls)


def _initials(name: str) -> str:
    import re

    n = re.sub(r"^(นางสาว|นาง|นาย|ว่าที่ร้อยตรี|ดร\.)\s*", "", (name or "").strip())
    return (n[:1] or "?").upper()


def banner(title: str, subtitle: str = "") -> None:
    """ส่วนหัวมาตรฐานของทุกหน้า: ป้ายหมวด ชื่อหน้า คำอธิบาย | ภาคเรียน ผู้ใช้ และปุ่มออกจากระบบ"""
    import html as _h

    cur = st.session_state.get("_page") or {}
    section, icon = cur.get("section", ""), cur.get("icon", "")
    if cur.get("title") and title not in ("ตารางเรียนของฉัน", "ตารางสอนของฉัน"):
        title = cur["title"]  # ใช้ชื่อเดียวกับเมนู เพื่อให้รู้ว่าอยู่หน้าใด
    ctx = current_ctx()
    with st.container(key="nvc_header"):
        left, right = st.columns([2.6, 1.4], vertical_alignment="center")
        left.markdown(
            (f'<span class="nvc-pill">{_h.escape(section)}</span>' if section else "")
            + f'<div class="nvc-title">{ic(icon) if icon else ""}{_h.escape(title)}</div>'
            + (f'<div class="nvc-sub">{_h.escape(subtitle)}</div>' if subtitle else ""),
            unsafe_allow_html=True,
        )
        with right:
            opts = term_options() if ctx else []
            if opts:
                ids = [o[0] for o in opts]
                labels = {o[0]: o[1] + (" · ปัจจุบัน" if o[2] else "") for o in opts}
                cur_t = current_term_id()
                sel = st.selectbox("ปีการศึกษา / ภาคเรียน", ids, index=ids.index(cur_t) if cur_t in ids else 0,
                                   format_func=lambda i: labels[i], key="term_select")
                if sel != st.session_state.get("term_id"):
                    st.session_state["term_id"] = sel
                    st.rerun()
            if ctx:
                u, o = st.columns([1.35, 1], vertical_alignment="center")
                u.markdown(f'<div class="nvc-chip"><span class="dot"></span><span class="t">{_h.escape(ctx.display_name)}'
                           f'<small>{_h.escape(ROLE_LABELS.get(ctx.role, ctx.role))}</small></span></div>', unsafe_allow_html=True)
                with o.container(key="nvc_logout"):
                    if st.button("ออกจากระบบ", icon=mi("logout"), key="hdr_logout", width="stretch", help="ออกจากระบบ"):
                        for k in list(st.session_state.keys()):
                            del st.session_state[k]
                        st.rerun()
    show_flash()


def flash(message: str, kind: str = "success") -> None:
    """เก็บข้อความแจ้งผลไว้แสดงหลังโหลดหน้าใหม่ (ไม่หายไปพร้อม st.rerun)"""
    st.session_state.setdefault("_flash", []).append((kind, message))


def show_flash() -> None:
    for kind, msg in st.session_state.pop("_flash", []):
        icon = mi({"success": "check_circle", "warning": "warning", "error": "error", "info": "info"}.get(kind, "info"))
        getattr(st, kind if kind in ("success", "warning", "error", "info") else "info")(msg, icon=icon)


def section(title: str, icon: str = "", caption: str = "") -> None:
    """หัวข้อย่อยรูปแบบเดียวกันทั้งระบบ"""
    import html as _h

    st.markdown(f'<div class="nvc-sec"><span class="t">{ic(icon) + " " if icon else ""}{_h.escape(title)}</span>'
                + (f'<span class="c">{_h.escape(caption)}</span>' if caption else "") + "</div>", unsafe_allow_html=True)


def kpi(col, icon: str, value, label: str, tone: str = "") -> None:
    col.markdown(f'<div class="nvc-kpi {tone}"><div class="i">{ic(icon)}</div><div><div class="v">{value}</div>'
                 f'<div class="l">{label}</div></div></div>', unsafe_allow_html=True)


def kpi_row(items: list[tuple]) -> None:
    """แถวการ์ดตัวเลขสำคัญ: [(ไอคอน, ค่า, ป้าย, โทน), ...] — จอเล็กจัดเป็น 2 คอลัมน์อัตโนมัติ"""
    cards = "".join(f'<div class="nvc-kpi {t[3] if len(t) > 3 else ""}"><div class="i">{ic(t[0])}</div><div><div class="v">{t[1]}</div>'
                    f'<div class="l">{t[2]}</div></div></div>' for t in items)
    st.markdown(f'<div class="nvc-kpis" style="--n:{len(items)}">{cards}</div>', unsafe_allow_html=True)


def empty_state(message: str, icon: str = "inbox") -> None:
    st.markdown(f'<div class="nvc-empty"><div class="i">{ic(icon)}</div>{message}</div>', unsafe_allow_html=True)


def field_error(message: str) -> None:
    """ข้อความผิดพลาดใต้ช่องกรอกที่เกี่ยวข้อง"""
    st.markdown(f'<div class="nvc-ferr">{ic("error")} {message}</div>', unsafe_allow_html=True)


def field_ok(message: str) -> None:
    st.markdown(f'<div class="nvc-fok">{ic("check_circle")} {message}</div>', unsafe_allow_html=True)


def field_hint(message: str) -> None:
    st.markdown(f'<div class="nvc-hint">{message}</div>', unsafe_allow_html=True)


def count_caption(shown: int, total: int, unit: str = "รายการ") -> None:
    st.markdown(f'<div class="nvc-count">แสดง <b>{shown:,}</b> จาก {total:,} {unit}</div>', unsafe_allow_html=True)


def page_link(url_path: str, label: str, icon: str | None = None, **kw) -> None:
    """ลิงก์ไปหน้าอื่น (แสดงเฉพาะหน้าที่ผู้ใช้มีสิทธิ์)"""
    pg = (st.session_state.get("_pages") or {}).get(url_path)
    if pg is not None:
        st.page_link(pg, label=label, icon=mi(icon) if icon else None, **kw)


def apply_theme() -> None:
    st.markdown(THEME_CSS, unsafe_allow_html=True)


@st.cache_resource(show_spinner=False)
def _init_database() -> tuple[bool, str]:
    db.configure()
    ok, msg = db.check_connection()
    if ok:
        db.init_db()
    return ok, msg


def ensure_db() -> None:
    ok, msg = _init_database()
    if not ok:
        _init_database.clear()
        st.error(msg, icon=mi("cloud_off"))
        st.info("ตรวจสอบค่า DATABASE_URL ใน Secrets ของ Streamlit Cloud หรือสถานะของบริการฐานข้อมูล แล้วกดโหลดหน้าใหม่")
        st.stop()


@contextmanager
def tx():
    """ธุรกรรมฐานข้อมูลสำหรับหน้าเว็บ (rollback อัตโนมัติเมื่อเกิดข้อผิดพลาด)"""
    with db.session_scope() as s:
        yield s


def current_ctx() -> Ctx | None:
    uid = st.session_state.get("uid")
    if not uid:
        return None
    with tx() as s:
        u = s.get(User, uid)
        if u is None or not u.active:
            st.session_state.pop("uid", None)
            return None
        return auth.ctx_from_user(u)


def require_ctx(*roles: str) -> Ctx:
    ctx = current_ctx()
    if ctx is None:
        st.warning("กรุณาเข้าสู่ระบบ")
        st.stop()
    if roles and ctx.role not in roles:
        st.error("คุณไม่มีสิทธิ์เข้าถึงหน้านี้")
        st.stop()
    return ctx


def term_options() -> list[tuple[int, str, bool]]:
    with tx() as s:
        rows = s.scalars(select(Term).order_by(Term.year.desc(), Term.semester.desc())).all()
        return [(t.id, t.label, t.is_current) for t in rows]


def current_term_id() -> int | None:
    tid = st.session_state.get("term_id")
    opts = term_options()
    ids = [o[0] for o in opts]
    if tid in ids:
        return tid
    cur = next((o[0] for o in opts if o[2]), ids[0] if ids else None)
    st.session_state["term_id"] = cur
    return cur


def require_term() -> int:
    tid = current_term_id()
    if tid is None:
        st.warning("ยังไม่มีปีการศึกษา/ภาคเรียนในระบบ — ผู้ดูแลระบบต้องสร้างที่หน้า “ตั้งค่า” ก่อน")
        st.stop()
    return tid


def term_label(tid: int | None) -> str:
    for o in term_options():
        if o[0] == tid:
            return o[1]
    return "-"


def sidebar_user(ctx: Ctx) -> None:
    """โลโก้บนแถบเมนูด้านบน (ชื่อเดิมคงไว้เพื่อไม่ให้กระทบส่วนที่เรียกใช้)"""
    brand = config.ASSETS_DIR / "brand_dark.png"
    if brand.exists():
        st.logo(str(brand), size="large", icon_image=str(brand))


def run_safely(fn, success: str | None = None, spinner: str | None = None) -> bool:
    """เรียกฟังก์ชันบริการ และแสดงข้อความผิดพลาดแบบเข้าใจง่าย"""
    from ..timetable_service import ConflictError, StaleError

    try:
        with st.spinner(spinner or "กำลังดำเนินการ…"):
            fn()
        if success:
            flash(success)  # แสดงที่ส่วนบนของหน้าหลังโหลดใหม่
        return True
    except PermissionDenied as e:
        st.error(str(e), icon=mi("block"))
    except ConflictError as e:
        show_conflicts(e.conflicts)
    except StaleError as e:
        st.warning(str(e))
    except ValueError as e:
        st.error(str(e), icon=mi("error"))
    return False


def show_conflicts(conflicts: list[dict]) -> None:
    import html as _h

    st.error("ไม่สามารถบันทึกได้ — พบปัญหาต่อไปนี้", icon=mi("block"))
    for c in conflicts:
        st.markdown(f'<div class="nvc-conf">{ic("error")}<b>{_h.escape(c["reason"])}</b><br>รายละเอียด: {_h.escape(c["detail"])}</div>',
                    unsafe_allow_html=True)


def timetable_options(ctx: Ctx, term_id: int) -> list[Timetable]:
    from ..timetable_service import list_timetables

    with tx() as s:
        rows = list_timetables(s, ctx, term_id)
        s.expunge_all()
        return rows


def tt_label(t: Timetable) -> str:
    scope = " [เฉพาะแผนก]" if t.scope_department_id else ""
    return f"ฉบับที่ {t.version} — {t.name or ''}{scope} · {STATUS_LABEL.get(t.status, t.status)}"


def df_show(rows: list[dict] | pd.DataFrame, height: int | None = None, key: str | None = None):
    df = rows if isinstance(rows, pd.DataFrame) else pd.DataFrame(rows)
    if df.empty:
        st.caption("— ไม่มีข้อมูล —")
        return
    kw = {"hide_index": True}
    if height:
        kw["height"] = height
    if key:
        kw["key"] = key
    st.dataframe(df, **kw)


def level_icon(level: str) -> str:
    """ป้ายระดับแบบข้อความ (ใช้ในตารางข้อมูลซึ่งแสดงไอคอนไม่ได้)"""
    return {"error": "ต้องแก้ไข", "warning": "ตรวจสอบ", "info": "ผ่าน"}.get(level, level)
