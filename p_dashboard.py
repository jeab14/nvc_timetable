"""หน้าแดชบอร์ด — ตัวเลขทั้งหมดมาจากฐานข้อมูลจริง"""
from __future__ import annotations

import html

import streamlit as st
from sqlalchemy import func, select

from .. import readiness
from ..models import ChangeRequest, ClassGroup, Course, Room, StudentMembership, Teacher, Timetable
from ..permissions import ADMIN, DEPT_ADMIN, STUDENT, TEACHER
from ..timetable_service import STATUS_LABEL, latest_published
from .common import banner, kpi_row, page_link, require_ctx, require_term, section, status_badge, term_label, tx, validation_badge

STATUS_COLOR = {"ข้อมูลที่พร้อมจัดตาราง": ("#16a34a", "check_circle"), "ข้อมูลที่ตรวจสอบแล้ว (มีคำเตือน)": ("#d97706", "warning"),
                "ข้อมูลที่ต้องแก้ไข": ("#dc2626", "cancel"), "ข้อมูลที่ยังไม่ครบ": ("#94a3b8", "pending"), "ข้อมูลที่นำเข้าแล้ว": ("#2563eb", "radio_button_checked")}


def page() -> None:
    ctx = require_ctx()
    tid = require_term()
    banner("แดชบอร์ด", f"ภาพรวมการจัดตารางสอน {term_label(tid)}")
    if ctx.role in (ADMIN, DEPT_ADMIN):
        _staff(ctx, tid)
    else:
        _personal(ctx, tid)


def _personal(ctx, tid):
    with tx() as s:
        p = latest_published(s, tid)
        info = (p.version, p.published_at) if p else None
    c1, c2 = st.columns([1.4, 1])
    with c1:
        section("ตารางที่มีผลใช้", "event_available")
        if info:
            when = f" · เผยแพร่เมื่อ {info[1]:%d/%m/%Y %H:%M}" if info[1] else ""
            st.markdown(f'<div class="nvc-card"><b>ตารางฉบับที่ {info[0]}</b> {status_badge("published")}'
                        f'<div style="color:#64748b;font-size:13.5px;margin-top:4px">{term_label(tid)}{when}</div></div>', unsafe_allow_html=True)
        else:
            st.warning("ภาคเรียนนี้ยังไม่มีตารางที่เผยแพร่ — เปลี่ยนภาคเรียนได้ที่มุมขวาบน")
    with c2:
        section("ทางลัด", "bolt")
        page_link("my", "ตารางสอน/ตารางเรียนของฉัน", "person")
        page_link("view", "ดูตาราง ส่งออก และพิมพ์", "calendar_month")
        if ctx.role == TEACHER:
            page_link("subjects", "เปรียบเทียบรายวิชา", "difference")
            page_link("requests", "เสนอการเปลี่ยนแปลงตาราง", "edit_note")
        page_link("history", "ดูตารางย้อนหลัง", "history")
    if ctx.role == STUDENT:
        st.caption("นักเรียนดูได้เฉพาะตารางของตนเองและกลุ่มเรียนของตน")


def _step(n, title, desc, state):
    return f'<div class="nvc-step {state}"><span class="n">{'<span class="mi" style="font-size:16px">check</span>' if state == "done" else n}</span><span class="t">{title}</span><div class="d">{desc}</div></div>'


def _staff(ctx, tid):
    with tx() as s:
        scope = ctx.dept_scope if ctx.role == DEPT_ADMIN else None
        tq = select(func.count(Teacher.id)).where(Teacher.active.is_(True))
        gq = select(func.count(ClassGroup.id)).where(ClassGroup.term_id == tid)
        cq = select(func.count(Course.id)).where(Course.term_id == tid)
        sq = select(func.count(StudentMembership.id)).where(StudentMembership.term_id == tid)
        if scope is not None:
            tq = tq.where(Teacher.department_id.in_(scope))
            gq = gq.where(ClassGroup.department_id.in_(scope))
            cq = cq.where(Course.department_id.in_(scope))
            sq = sq.join(ClassGroup, ClassGroup.id == StudentMembership.group_id).where(ClassGroup.department_id.in_(scope))
        n_t, n_g, n_c, n_s = s.scalar(tq), s.scalar(gq), s.scalar(cq), s.scalar(sq)
        n_r = s.scalar(select(func.count(Room.id)).where(Room.active.is_(True)))
        latest = s.scalars(select(Timetable).where(Timetable.term_id == tid, Timetable.status != "deleted").order_by(Timetable.version.desc())).first()
        pub = latest_published(s, tid)
        pub_v = pub.version if pub else None
        pending = s.scalar(select(func.count(ChangeRequest.id)).where(ChangeRequest.status == "pending"))
        latest_info = None
        if latest:
            rep = latest.solver_report or {}
            vr = latest.validation_report or {}
            latest_info = {"version": latest.version, "status": latest.status, "validation": latest.validation_status,
                           "unplaced": len(rep.get("unplaced") or []),
                           "errors": sum(1 for f in (vr.get("findings") or []) if f["level"] == "error"), "created": latest.created_at,
                           "name": latest.name or ""}
        checks, _ = readiness.run(s, tid)
        status_rows = readiness.data_status(s, tid, checks)

    kpi_row([("person_book", f"{n_t:,}", "ครูทั้งหมด"), ("school", f"{n_s:,}", "นักเรียน"), ("groups", f"{n_g:,}", "กลุ่มเรียน"),
             ("menu_book", f"{n_c:,}", "รายวิชา"), ("meeting_room", f"{n_r:,}", "ห้องเรียน")])

    n_err = sum(1 for x in checks if x.level == "error")
    n_warn = sum(1 for x in checks if x.level == "warning")
    imported = n_c > 0 and n_t > 0 and n_g > 0
    ready = imported and n_err == 0
    scheduled = bool(latest_info and latest_info["status"] in ("success", "pending_review", "approved", "published", "superseded"))
    published = pub_v is not None
    states = []
    first_open = True
    for ok in (imported, ready, scheduled, published):
        if ok:
            states.append("done")
        elif first_open:
            states.append("now")
            first_open = False
        else:
            states.append("")
    section("ขั้นตอนการจัดตาราง", "route", "ทำตามลำดับจากซ้ายไปขวา")
    st.markdown('<div class="nvc-steps">'
                + _step(1, "นำเข้าข้อมูล", f"รายวิชา {n_c} · ครู {n_t} · กลุ่ม {n_g}", states[0])
                + _step(2, "ตรวจสอบความพร้อม", "ไม่มีข้อผิดพลาด" if n_err == 0 else f"ข้อผิดพลาด {n_err} รายการ", states[1])
                + _step(3, "จัดตาราง", f"ฉบับที่ {latest_info['version']} · {STATUS_LABEL[latest_info['status']]}" if latest_info else "ยังไม่ได้จัดตาราง", states[2])
                + _step(4, "อนุมัติและเผยแพร่", f"เผยแพร่ฉบับที่ {pub_v}" if published else "ยังไม่มีตารางที่เผยแพร่", states[3])
                + "</div>", unsafe_allow_html=True)
    lc = st.columns(4)
    with lc[0]:
        page_link("import", "ไปหน้านำเข้าข้อมูล", "upload_file")
    with lc[1]:
        page_link("readiness", "ไปหน้าตรวจสอบความพร้อม", "fact_check")
    with lc[2]:
        page_link("auto", "ไปหน้าจัดตารางอัตโนมัติ", "auto_mode")
    with lc[3]:
        page_link("versions", "ไปหน้าฉบับตาราง & เผยแพร่", "inventory_2")

    left, right = st.columns([1.15, 1], gap="large")
    with left:
        section("ความพร้อมของข้อมูล", "monitoring")
        mx = max([r["จำนวน"] for r in status_rows] + [1])
        rows_html = []
        for r in status_rows:
            color, ic = STATUS_COLOR.get(r["สถานะ"], ("#2563eb", "radio_button_checked"))
            w = 4 if r["จำนวน"] == 0 else max(6, int(r["จำนวน"] / mx * 100))
            rows_html.append(
                f'<div style="margin:8px 0"><div style="display:flex;justify-content:space-between;font-size:14px">'
                f'<span><b>{html.escape(r["หมวดข้อมูล"])}</b> <span style="color:#64748b">· {r["จำนวน"]:,}</span></span>'
                f'<span style="color:{color};font-weight:600"><span class="mi" style="font-size:17px">{ic}</span> {html.escape(r["สถานะ"])}</span></div>'
                f'<div class="nvc-bar"><span style="width:{w}%;background:{color}"></span></div></div>')
        st.markdown('<div class="nvc-card">' + "".join(rows_html) + "</div>", unsafe_allow_html=True)
        if n_err:
            st.error(f"ข้อมูลยังมีข้อผิดพลาด {n_err} รายการ ต้องแก้ไขก่อนจัดตาราง")
        elif n_warn:
            st.warning(f"ข้อมูลพร้อมจัดตาราง แต่มีคำเตือน {n_warn} รายการที่ควรตรวจดู")
        else:
            st.success("ข้อมูลพร้อมจัดตาราง")
    with right:
        section("สถานะการจัดตาราง", "calendar_month")
        if latest_info:
            st.markdown(f'<div class="nvc-card"><div style="font-size:14px;color:#64748b">ตารางฉบับล่าสุด</div>'
                        f'<div style="font-size:20px;font-weight:700;color:#0b3d91">ฉบับที่ {latest_info["version"]} '
                        f'<span style="font-size:14px;font-weight:500;color:#475569">{html.escape(latest_info["name"])}</span></div>'
                        f'<div style="margin:6px 0">{status_badge(latest_info["status"])} {validation_badge(latest_info["validation"])}</div>'
                        f'<div style="font-size:12.5px;color:#64748b">สร้างเมื่อ {latest_info["created"]:%d/%m/%Y %H:%M}</div></div>',
                        unsafe_allow_html=True)
            st.write("")
            kpi_row([("hourglass_empty", latest_info["unplaced"], "ช่วงคาบที่ยังจัดไม่ได้", "warn" if latest_info["unplaced"] else "ok"),
                     ("error" if latest_info["errors"] else "check_circle", latest_info["errors"], "ข้อผิดพลาดที่ตรวจพบ", "err" if latest_info["errors"] else "ok")])
        else:
            st.markdown('<div class="nvc-empty"><div class="i"><span class="mi">calendar_month</span></div>ยังไม่ได้จัดตารางในภาคเรียนนี้</div>', unsafe_allow_html=True)
        st.write("")
        if published:
            st.success(f"ตารางที่เผยแพร่ (มีผลใช้): ฉบับที่ {pub_v}")
        else:
            st.info("ยังไม่มีตารางที่เผยแพร่ในภาคเรียนนี้")
        if pending:
            st.warning(f"มีคำขอเปลี่ยนแปลงตารางจากครูรอพิจารณา {pending} รายการ")
            page_link("requests", "พิจารณาคำขอ", "edit_note")
