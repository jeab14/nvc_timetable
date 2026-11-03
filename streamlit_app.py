"""ระบบจัดตารางสอนอัตโนมัติ วิทยาลัยอาชีวศึกษานครราชสีมา (NVC Smart Timetable)

ไฟล์เริ่มต้นสำหรับ Streamlit / Streamlit Cloud:  streamlit run streamlit_app.py
"""
from __future__ import annotations

import streamlit as st

from nvc import config
from nvc.models import User
from nvc.permissions import ADMIN, DEPT_ADMIN, TEACHER
from nvc.ui.icons import PAGE, mi
from nvc.ui import common, p_admin, p_auth, p_dashboard, p_data, p_import, p_manual, p_schedule, p_view

st.set_page_config(page_title=config.APP_NAME_EN + " | " + config.COLLEGE_NAME, page_icon=":material/calendar_month:", layout="wide")
common.apply_theme()
common.ensure_db()


def main() -> None:
    with common.tx() as s:
        from nvc import auth

        first_run = not auth.has_any_user(s)
    if first_run:
        st.navigation([st.Page(p_auth.setup_page, title="ตั้งค่าระบบครั้งแรก", icon=mi(PAGE["setup"]))], position="hidden").run()
        return
    ctx = common.current_ctx()
    if ctx is None:
        st.navigation([st.Page(p_auth.login_page, title="เข้าสู่ระบบ", icon=mi(PAGE["login"]))], position="hidden").run()
        return
    with common.tx() as s:
        u = s.get(User, ctx.user_id)
        must_change = bool(u.must_change_password)
    if must_change:
        st.navigation([st.Page(lambda: p_auth.change_password_page(forced=True), title="เปลี่ยนรหัสผ่าน", icon=mi(PAGE["password"]), url_path="force_pw")], position="hidden").run()
        return
    common.sidebar_user(ctx)

    registry: dict = {}

    def P(fn, *, title, icon, url_path, default=False):
        pg = st.Page(fn, title=title, icon=icon, url_path=url_path, default=default)
        registry[url_path] = pg
        return pg

    home = P(p_dashboard.page, title="แดชบอร์ด", icon=mi(PAGE["dashboard"]), url_path="dashboard", default=True)
    mine = P(p_view.my_page, title="ตารางของฉัน", icon=mi(PAGE["my"]), url_path="my")
    view = P(p_view.view_page, title="ตารางเรียน / พิมพ์ตาราง", icon=mi(PAGE["view"]), url_path="view")
    subjects = P(p_view.subjects_page, title="เปรียบเทียบรายวิชา", icon=mi(PAGE["subjects"]), url_path="subjects")
    deleted = P(p_schedule.deleted_page, title="ประวัติการลบตาราง", icon=mi(PAGE["deleted"]), url_path="deleted")
    compare = P(p_view.compare_page, title="เปรียบเทียบตารางครู", icon=mi(PAGE["compare"]), url_path="compare")
    history = P(p_view.history_page, title="ประวัติตาราง", icon=mi(PAGE["history"]), url_path="history")
    requests = P(p_admin.requests_page, title="คำขอเปลี่ยนแปลงตาราง", icon=mi(PAGE["requests"]), url_path="requests")
    pw = P(p_auth.change_password_page, title="เปลี่ยนรหัสผ่าน", icon=mi(PAGE["password"]), url_path="password")
    manual = P(p_admin.help_page, title="คู่มือการใช้งาน", icon=mi(PAGE["manual"]), url_path="manual")
    out = P(p_auth.logout_page, title="ออกจากระบบ", icon=mi(PAGE["logout"]), url_path="logout")

    imp = P(p_import.page, title="นำเข้าข้อมูล", icon=mi(PAGE["import"]), url_path="import")
    data = P(p_data.page, title="จัดการข้อมูล", icon=mi(PAGE["data"]), url_path="data")
    ready = P(p_schedule.readiness_page, title="ตรวจสอบความพร้อม", icon=mi(PAGE["readiness"]), url_path="readiness")
    auto = P(p_schedule.auto_page, title="จัดตารางอัตโนมัติ", icon=mi(PAGE["auto"]), url_path="auto")
    man = P(p_manual.page, title="จัด/แก้ไขตาราง", icon=mi(PAGE["edit"]), url_path="edit")
    vers = P(p_schedule.versions_page, title="ฉบับตาราง & เผยแพร่", icon=mi(PAGE["versions"]), url_path="versions")
    users = P(p_admin.users_page, title="บัญชีผู้ใช้งาน", icon=mi(PAGE["users"]), url_path="users")
    sett = P(p_admin.settings_page, title="ตั้งค่า", icon=mi(PAGE["settings"]), url_path="settings")
    aud = P(p_admin.audit_page, title="ประวัติการดำเนินการ", icon=mi(PAGE["audit"]), url_path="audit")

    # เมนูด้านบน: หน้าที่ใช้บ่อยเป็นลิงก์ตรง ที่เหลือจัดกลุ่มเป็นเมนูย่อย (ปุ่มออกจากระบบอยู่มุมขวาของส่วนหัวทุกหน้า)
    if ctx.role == ADMIN:
        pages = {
            "": [home, view],
            "จัดตาราง": [auto, man, vers, requests, deleted],
            "ข้อมูล": [imp, data, ready],
            "ดูตาราง": [mine, history],
            "ระบบ": [users, sett, aud, manual, pw],
        }
    elif ctx.role == DEPT_ADMIN:
        pages = {
            "": [home, view],
            "จัดตาราง": [ready, auto, man, vers, requests],
            "ดูตาราง": [mine, compare, history],
            "บัญชี": [manual, pw],
        }
    elif ctx.role == TEACHER:
        more = [subjects, history, requests] + ([man] if ctx.can_edit_timetable else [])
        pages = {"": [home, mine, view], "เพิ่มเติม": more, "บัญชี": [manual, pw]}
    else:  # STUDENT
        pages = {"": [mine, view, history], "บัญชี": [manual, pw]}
    _ = out  # หน้าออกจากระบบยังใช้ได้ผ่าน URL /logout
    # เก็บรายการหน้า (สำหรับลิงก์ทางลัด) และหน้าปัจจุบัน (สำหรับส่วนหัว/เส้นทางเมนู)
    allowed = {id(pg) for sec in pages.values() for pg in sec}
    st.session_state["_pages"] = {k: v for k, v in registry.items() if id(v) in allowed}
    current = st.navigation(pages, position="top")
    sec_name = next((name for name, items in pages.items() if any(x.title == current.title for x in items)), "")
    st.session_state["_page"] = {"section": sec_name, "title": current.title, "icon": (current.icon or "").replace(":material/", "").strip(":")}
    current.run()


main()
