"""หน้าเข้าสู่ระบบ ตั้งค่าเริ่มต้นระบบ (ครั้งแรก) และเปลี่ยนรหัสผ่าน — ไม่มีหน้าสมัครสมาชิก"""
from __future__ import annotations

import base64

import streamlit as st

from .. import auth, config
from .common import banner, require_ctx, tx

LOGIN_CSS = """
<style>
[data-testid="stApp"]{background:radial-gradient(1200px 500px at 50% -10%,#cfe0ff 0%,#eaf2ff 45%,#ffffff 100%) !important}
.st-key-login_card{background:#ffffff;border:1px solid #dbe5f5;border-radius:20px;padding:26px 26px 14px !important;
  box-shadow:0 20px 50px -24px rgba(11,61,145,.45)}
.nvc-login-head{text-align:center;margin:28px 0 14px}
.nvc-login-head .t{font-size:24px;font-weight:700;color:#0b3d91;line-height:1.3}
.nvc-login-head .s{color:#64748b;margin-top:4px;font-size:14px}
</style>
"""


def _logo_html() -> str:
    if config.LOGO_PATH.exists():
        b64 = base64.b64encode(config.LOGO_PATH.read_bytes()).decode()
        return f'<img src="data:image/png;base64,{b64}" style="height:84px;margin-bottom:10px">'
    icon = config.ASSETS_DIR / "brand_icon.png"
    if icon.exists():
        b64 = base64.b64encode(icon.read_bytes()).decode()
        return f'<img src="data:image/png;base64,{b64}" style="height:72px;margin-bottom:10px;border-radius:18px">'
    return '<div class="mi" style="font-size:60px;color:#1d4ed8">calendar_month</div>'


def _err(slot, msg: str) -> None:
    slot.markdown(f'<div class="nvc-ferr"><span class="mi">error</span> {msg}</div>', unsafe_allow_html=True)


def login_page() -> None:
    st.markdown(LOGIN_CSS, unsafe_allow_html=True)
    _, mid, _ = st.columns([1, 1.25, 1])
    with mid:
        st.markdown(
            f"""<div class="nvc-login-head">{_logo_html()}
            <div class="t">ระบบจัดตารางสอนอัตโนมัติ</div>
            <div class="s">{config.COLLEGE_NAME} · {config.APP_NAME_EN}</div></div>""",
            unsafe_allow_html=True,
        )
        with st.container(key="login_card"):
            with st.form("login", border=False):
                u = st.text_input("ชื่อผู้ใช้ หรือรหัสประจำตัว", placeholder="เช่น รหัสครู หรือรหัสนักเรียน")
                e_u = st.empty()
                p = st.text_input("รหัสผ่าน", type="password", placeholder="กรอกรหัสผ่าน")
                e_p = st.empty()
                ok = st.form_submit_button("เข้าสู่ระบบ", type="primary", width="stretch")
            msg_slot = st.empty()
            st.caption(":material/lock: หากลืมรหัสผ่าน กรุณาติดต่อฝ่ายวิชาการเพื่อรีเซ็ตรหัสผ่าน")
        if ok:
            if not u.strip():
                _err(e_u, "กรุณากรอกชื่อผู้ใช้หรือรหัสประจำตัว")
            if not p:
                _err(e_p, "กรุณากรอกรหัสผ่าน")
            if not u.strip() or not p:
                return
            with st.spinner("กำลังตรวจสอบ…"):
                with tx() as s:
                    ctx, msg, must_change = auth.authenticate(s, u, p)
            if ctx is None:
                msg_slot.error(msg)
            else:
                st.session_state["uid"] = ctx.user_id
                st.session_state["must_change"] = must_change
                st.rerun()


def _pw_feedback(slot, pw: str, username: str = "") -> bool:
    """ตรวจรหัสผ่านใหม่และแสดงผลใต้ช่องกรอกทันที"""
    if not pw:
        slot.markdown('<div class="nvc-hint">อย่างน้อย 8 ตัวอักษร และต้องไม่เหมือนชื่อผู้ใช้</div>', unsafe_allow_html=True)
        return False
    probs = auth.password_problems(pw, username)
    if probs:
        _err(slot, " · ".join(probs))
        return False
    slot.markdown('<div class="nvc-fok"><span class="mi">check_circle</span> รหัสผ่านใช้ได้</div>', unsafe_allow_html=True)
    return True


def _match_feedback(slot, p1: str, p2: str) -> bool:
    if not p2:
        return False
    if p1 != p2:
        _err(slot, "รหัสผ่านทั้งสองช่องไม่ตรงกัน")
        return False
    slot.markdown('<div class="nvc-fok"><span class="mi">check_circle</span> ตรงกัน</div>', unsafe_allow_html=True)
    return True


def setup_page() -> None:
    """แสดงเฉพาะเมื่อยังไม่มีบัญชีใดในระบบ: สร้างผู้ดูแลระบบคนแรก"""
    banner("ตั้งค่าระบบครั้งแรก", "สร้างบัญชีผู้ดูแลระบบ (ฝ่ายวิชาการ) คนแรก — หน้านี้จะไม่แสดงอีกเมื่อมีบัญชีแล้ว")
    _, mid, _ = st.columns([1, 2, 1])
    with mid, st.container(border=True):
        name = st.text_input("ชื่อ-นามสกุลผู้ดูแลระบบ", placeholder="เช่น นางสาวสมใจ ใจดี")
        e_name = st.empty()
        user = st.text_input("ชื่อผู้ใช้", value="admin")
        e_user = st.empty()
        p1 = st.text_input("รหัสผ่าน", type="password")
        e_p1 = st.empty()
        p2 = st.text_input("ยืนยันรหัสผ่าน", type="password")
        e_p2 = st.empty()
        ok1 = _pw_feedback(e_p1, p1, user)
        ok2 = _match_feedback(e_p2, p1, p2)
        go = st.button("สร้างบัญชีผู้ดูแลระบบ", type="primary", width="stretch")
        if go:
            bad = False
            if not name.strip():
                _err(e_name, "กรุณากรอกชื่อ-นามสกุล")
                bad = True
            if not user.strip():
                _err(e_user, "กรุณากรอกชื่อผู้ใช้")
                bad = True
            if not ok1 or not ok2:
                if not p2:
                    _err(e_p2, "กรุณายืนยันรหัสผ่าน")
                bad = True
            if bad:
                return
            try:
                with tx() as s:
                    u = auth.bootstrap_admin(s, user, p1, name)
                    uid = u.id
                st.session_state["uid"] = uid
                st.success("สร้างบัญชีผู้ดูแลระบบสำเร็จ")
                st.rerun()
            except Exception as e:
                st.error(str(e))


def change_password_page(forced: bool = False) -> None:
    ctx = require_ctx()
    banner("เปลี่ยนรหัสผ่าน", "บัญชียังใช้รหัสผ่านเริ่มต้น กรุณาเปลี่ยนก่อนใช้งาน" if forced else "รหัสผ่านใหม่ต้องมีอย่างน้อย 8 ตัวอักษร")
    if forced:
        st.warning("เพื่อความปลอดภัย ต้องเปลี่ยนรหัสผ่านเริ่มต้นก่อนเข้าถึงข้อมูลในระบบ")
    _, mid, _ = st.columns([1, 2, 1])
    with mid, st.container(border=True):
        old = st.text_input("รหัสผ่านปัจจุบัน", type="password")
        e_old = st.empty()
        n1 = st.text_input("รหัสผ่านใหม่", type="password")
        e_n1 = st.empty()
        n2 = st.text_input("ยืนยันรหัสผ่านใหม่", type="password")
        e_n2 = st.empty()
        ok1 = _pw_feedback(e_n1, n1, ctx.username)
        if ok1 and old and n1 == old:
            _err(e_n1, "รหัสผ่านใหม่ต้องไม่ซ้ำกับรหัสผ่านเดิม")
            ok1 = False
        ok2 = _match_feedback(e_n2, n1, n2)
        go = st.button("บันทึกรหัสผ่านใหม่", type="primary", width="stretch")
        if go:
            if not old:
                _err(e_old, "กรุณากรอกรหัสผ่านปัจจุบัน")
                return
            if not n2:
                _err(e_n2, "กรุณายืนยันรหัสผ่านใหม่")
            if not (ok1 and ok2):
                return
            try:
                with tx() as s:
                    auth.change_password(s, ctx, old, n1)
                st.session_state["must_change"] = False
                st.success("เปลี่ยนรหัสผ่านเรียบร้อยแล้ว")
                st.rerun()
            except ValueError as e:
                if "รหัสผ่านเดิม" in str(e):
                    _err(e_old, str(e))
                else:
                    st.error(str(e))
    if forced and st.button("ออกจากระบบ"):
        logout()


def logout() -> None:
    for k in list(st.session_state.keys()):
        del st.session_state[k]
    st.rerun()


def logout_page() -> None:
    logout()
