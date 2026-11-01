"""หน้าตรวจสอบความพร้อม จัดตารางอัตโนมัติ และจัดการฉบับตาราง (ตรวจสอบ/ส่งตรวจ/อนุมัติ/เผยแพร่)"""
from __future__ import annotations

import pandas as pd
import streamlit as st
from sqlalchemy import select

from .. import audit, readiness, timetable_service as T, validator as V
from ..models import Assignment, GroupCourse, Timetable
from ..permissions import ADMIN, DEPT_ADMIN
from ..views import get_view, options, visible_departments
from ..render import view_html
from .common import (
    banner, df_show, flash, level_icon, require_ctx, require_term, run_safely, status_badge, term_label, timetable_options, tt_label, tx,
    validation_badge,
)


def _dept_picker(ctx, key: str, allow_all: bool = True):
    with tx() as s:
        deps = [(d.id, d.name) for d in visible_departments(s, ctx)]
    if ctx.role == DEPT_ADMIN:
        if not deps:
            st.error("บัญชีของท่านยังไม่ได้กำหนดแผนกที่รับผิดชอบ")
            st.stop()
        return st.selectbox("แผนก", [d[0] for d in deps], format_func=lambda i: dict(deps)[i], key=key)
    opts = ([None] if allow_all else []) + [d[0] for d in deps]
    return st.selectbox("ขอบเขต", opts, format_func=lambda i: "ทั้งวิทยาลัย" if i is None else f"เฉพาะแผนก{dict(deps)[i]}", key=key)


def _checks_view(checks):
    n_e = sum(1 for c in checks if c.level == "error")
    n_w = sum(1 for c in checks if c.level == "warning")
    n_i = sum(1 for c in checks if c.level == "info")
    a, b, c = st.columns(3)
    a.metric(":red[:material/error:] ข้อผิดพลาด (ต้องแก้ไขก่อนจัด)", n_e)
    b.metric(":orange[:material/warning:] คำเตือน (ตรวจสอบแล้วดำเนินการต่อได้)", n_w)
    c.metric(":green[:material/check_circle:] ข้อมูลทั่วไป", n_i)
    rows = [{"สถานะ": level_icon(x.level), **x.as_row()} for x in sorted(checks, key=lambda x: ["error", "warning", "info"].index(x.level))]
    df_show(rows, height=min(420, 40 + 35 * len(rows)))
    if rows:
        df = pd.DataFrame([x.as_row() for x in checks])
        st.download_button("ดาวน์โหลดรายงานความพร้อม (CSV)", ("﻿" + df.to_csv(index=False)).encode("utf-8"), "readiness_report.csv", "text/csv", icon=":material/download:")
    return n_e, n_w


def readiness_page() -> None:
    ctx = require_ctx(ADMIN, DEPT_ADMIN)
    tid = require_term()
    banner("ตรวจสอบความพร้อมของข้อมูล", f"ตรวจข้อมูลก่อนจัดตาราง · {term_label(tid)}")
    dept = _dept_picker(ctx, "rd_dept")
    with tx() as s:
        checks, pb = readiness.run(s, tid, dept)
        status_rows = readiness.data_status(s, tid, checks)
    st.subheader("สถานะข้อมูล")
    df_show(status_rows)
    st.subheader("ผลการตรวจสอบ")
    _checks_view(checks)
    if ctx.role == ADMIN and any("ยังไม่ได้ลงทะเบียน" in c.message for c in checks):
        st.divider()
        st.markdown("##### แก้ไขด่วน: สร้างการลงทะเบียนของกลุ่มเรียนจากข้อมูลการมอบหมายผู้สอน")
        st.caption("ใช้เมื่อไฟล์การมอบหมายผู้สอนคือแผนการเรียนจริงของกลุ่ม — ระบบจะเพิ่มเฉพาะคู่ กลุ่ม/รายวิชา ที่มีการมอบหมายแต่ยังไม่ลงทะเบียน")
        ok = st.checkbox("ยืนยันว่าข้อมูลการมอบหมายผู้สอนถูกต้องตามหลักสูตร", key="fix_reg_ok")
        if st.button("สร้างการลงทะเบียน", disabled=not ok):
            with tx() as s:
                regs = {(g.group_id, g.course_id) for g in s.scalars(select(GroupCourse).where(GroupCourse.term_id == tid)).all()}
                n = 0
                for a in s.scalars(select(Assignment).where(Assignment.term_id == tid)).all():
                    if (a.group_id, a.course_id) not in regs:
                        s.add(GroupCourse(term_id=tid, group_id=a.group_id, course_id=a.course_id))
                        regs.add((a.group_id, a.course_id))
                        n += 1
                audit.log(s, ctx.username, "fix_registration_from_assignment", "group_course", {"created": n})
            flash(f"เพิ่มการลงทะเบียน {n} รายการ")
            st.rerun()


def auto_page() -> None:
    ctx = require_ctx(ADMIN, DEPT_ADMIN)
    tid = require_term()
    banner("จัดตารางอัตโนมัติ", f"ค้นหาตารางที่ผ่านข้อจำกัดบังคับทั้งหมดด้วย OR-Tools CP-SAT · {term_label(tid)}")
    c1, c2, c3 = st.columns([1.2, 1.2, 1])
    with c1:
        dept = _dept_picker(ctx, "auto_dept")
    tts = timetable_options(ctx, tid)
    with c2:
        base_opts = [None] + [t.id for t in tts if t.status not in ("infeasible",)]
        base = st.selectbox("ใช้ตารางฉบับเดิมเป็นฐาน (ลดการเปลี่ยนแปลง)", base_opts,
                            format_func=lambda i: "— ไม่ใช้ —" if i is None else tt_label(next(t for t in tts if t.id == i)), key="auto_base",
                            help="ถ้าจัดเฉพาะแผนก ตารางของแผนกอื่นในฉบับฐานจะถูกถือเป็นเวลาที่ไม่ว่างของครูและห้อง")
    with c3:
        with tx() as s:
            from .. import settings_service as S

            default_tl = int(S.get(s, "solver_time_limit", tid) or 60)
        tl = st.number_input("เวลาประมวลผลสูงสุด (วินาที)", 10, 900, default_tl, step=10, key="auto_tl")
    name = st.text_input("ชื่อฉบับตาราง", value="จัดอัตโนมัติ", key="auto_name")
    if dept is not None and base is None:
        st.warning("การจัดเฉพาะแผนกโดยไม่มีฉบับฐาน จะไม่ทราบเวลาที่ครู/ห้องถูกใช้โดยแผนกอื่น — แนะนำให้เลือกตารางฉบับล่าสุดเป็นฐาน")
    b1, b2 = st.columns(2)
    if b1.button("ตรวจสอบความพร้อมของข้อมูล", icon=":material/fact_check:", width="stretch"):
        with tx() as s:
            st.session_state["auto_checks"] = readiness.run(s, tid, dept)[0]
    checks = st.session_state.get("auto_checks")
    n_e = None
    if checks is not None:
        with st.expander("ผลการตรวจสอบความพร้อม", expanded=True):
            n_e, _ = _checks_view(checks)
    start = b2.button("เริ่มจัดตาราง", icon=":material/play_arrow:", type="primary", width="stretch", disabled=(checks is None or bool(n_e)),
                      help="ต้องตรวจสอบความพร้อมและไม่มีข้อผิดพลาดก่อน")
    if start:
        with st.status("กำลังจัดตาราง…", expanded=True) as stt:
            st.write("สร้างรายการสอนและข้อจำกัดจากฐานข้อมูล")
            st.write(f"ค้นหาคำตอบด้วย CP-SAT (สูงสุด {tl} วินาที) และตรวจสอบผลด้วยระบบตรวจสอบอิสระ")
            new_id = {}

            def go():
                with tx() as s:
                    tt = T.run_auto(s, ctx, tid, dept, int(tl), base, name)
                    new_id["id"] = tt.id
                    new_id["status"] = tt.status

            ok = run_safely(go)
            if ok:
                stt.update(label=f"เสร็จสิ้น: {T.STATUS_LABEL[new_id['status']]}", state="complete" if new_id["status"] in ("success",) else "error")
                st.session_state["auto_result"] = new_id["id"]
                st.session_state.pop("auto_checks", None)
            else:
                stt.update(label="จัดตารางไม่สำเร็จ", state="error")
    rid = st.session_state.get("auto_result")
    if rid:
        _result_panel(ctx, rid)


def _result_panel(ctx, tt_id: int) -> None:
    with tx() as s:
        tt = s.get(Timetable, tt_id)
        if tt is None:
            return
        rep = tt.solver_report or {}
        vrep = tt.validation_report or {}
        info = dict(status=tt.status, validation=tt.validation_status, version=tt.version, n=len(tt.entries), term=tt.term_id)
    st.divider()
    st.subheader(f"ผลการจัดตาราง — ฉบับที่ {info['version']}")
    st.markdown(f"{status_badge(info['status'])} &nbsp; {validation_badge(info['validation'])}", unsafe_allow_html=True)
    unplaced = rep.get("unplaced") or []
    errs = [f for f in (vrep.get("findings") or []) if f["level"] == "error"]
    m = st.columns(4)
    m[0].metric("รายการที่จัดได้ (ในตาราง)", info["n"])
    m[1].metric("ช่วงคาบที่ยังจัดไม่ได้", len(unplaced))
    m[2].metric("ข้อผิดพลาดจากการตรวจสอบ", len(errs))
    m[3].metric("เวลาที่ใช้ (วินาที)", rep.get("seconds_total") or rep.get("seconds_strict") or "-")
    if info["status"] == "success" and info["validation"] in ("pass", "warn"):
        st.success("จัดตารางได้ครบทุกรายการและผ่านการตรวจสอบข้อจำกัดบังคับ — ตรวจดูตารางแล้วส่งตรวจสอบ/อนุมัติที่เมนู “ฉบับตาราง & เผยแพร่”")
        if not rep.get("optimal", True):
            st.caption("หมายเหตุ: คุณภาพตาราง (ช่วงว่าง การกระจายคาบ) เป็นคำตอบที่ดีที่สุดที่พบภายในเวลาที่กำหนด อาจปรับดีขึ้นได้ถ้าเพิ่มเวลาประมวลผล")
    if rep.get("message"):
        st.error(rep["message"])
    if rep.get("note"):
        st.info(rep["note"])
    if rep.get("conflicts"):
        st.markdown("**สาเหตุ/ข้อจำกัดที่ขัดแย้งที่ตรวจพบ**")
        for c in rep["conflicts"]:
            st.markdown(f"- {c}")
        st.markdown("**แนวทางแก้ไข (ให้ผู้มีสิทธิ์ตัดสินใจ):** เพิ่มห้องเรียนประเภทที่ขาด · ลดช่วงเวลาไม่สะดวกของครูที่เกี่ยวข้อง · "
                    "ตรวจสอบจำนวนคาบ/คาบต่อเนื่องของรายวิชา · ปรับการมอบหมายผู้สอน · เพิ่มเวลาประมวลผล")
    if unplaced:
        st.markdown("**รายการที่ยังจัดไม่ได้** (ตารางนี้เป็น “จัดได้บางส่วน” ไม่ใช่ตารางสมบูรณ์)")
        df_show([{"รายการ": u["label"], "ช่วงที่": u["block"], "จำนวนคาบ": u["periods"]} for u in unplaced])
    if rep.get("issues"):
        with st.expander("ข้อสังเกตจากข้อมูล"):
            df_show([{"ระดับ": i["level"], "รายละเอียด": i["message"]} for i in rep["issues"]])
    if errs:
        with st.expander(f"ข้อผิดพลาดจากระบบตรวจสอบ ({len(errs)})", expanded=True):
            df_show([{"ข้อ": f["check"], "รายละเอียด": f["message"]} for f in errs])
    if info["n"]:
        with tx() as s:
            tt = s.get(Timetable, tt_id)
            gopts = options(s, ctx, tt.term_id, "group")
        if gopts:
            g = st.selectbox("ดูตัวอย่างตารางของกลุ่มเรียน", [o[0] for o in gopts], format_func=lambda i: dict(gopts)[i], key="auto_prev_g")
            with tx() as s:
                vd = get_view(s, ctx, s.get(Timetable, tt_id), "group", g)
            st.markdown(view_html(vd), unsafe_allow_html=True)
    c = st.columns(3)
    if c[0].button("ตรวจสอบตาราง (ระบบตรวจสอบอิสระ)", icon=":material/verified:", key="res_val"):
        run_safely(lambda: _validate(ctx, tt_id), "ตรวจสอบเรียบร้อย")
        st.rerun()
    nm = c[1].text_input("ชื่อฉบับร่าง", key="res_name", label_visibility="collapsed", placeholder="ตั้งชื่อฉบับร่าง")
    if c[1].button("บันทึกฉบับร่าง", icon=":material/save:", key="res_save"):
        with tx() as s:
            t = s.get(Timetable, tt_id)
            if nm:
                t.name = nm
            audit.log(s, ctx.username, "save_draft", "timetable", {"id": tt_id})
        st.toast("บันทึกฉบับร่างแล้ว (ตารางทุกฉบับถูกเก็บในฐานข้อมูล)", icon=":material/check_circle:")
    if c[2].button("ส่งตรวจสอบ", icon=":material/send:", key="res_submit", disabled=info["status"] not in ("success",)):
        if run_safely(lambda: _do(T.submit_for_review, ctx, tt_id), "ส่งตรวจสอบแล้ว"):
            st.rerun()


def _validate(ctx, tt_id):
    with tx() as s:
        T.validate(s, ctx, tt_id)


def _do(fn, ctx, tt_id):
    with tx() as s:
        fn(s, ctx, tt_id)


def validation_report_view(rep: dict) -> None:
    summ = rep.get("summary") or {}
    rows = []
    for ck, v in sorted(summ.items(), key=lambda x: int(x[0])):
        icon = "ไม่ผ่าน" if v["errors"] else ("มีคำเตือน" if v["warnings"] else "ผ่าน")
        rows.append({"": icon, "ข้อ": int(ck), "รายการตรวจสอบ": v["name"], "ข้อผิดพลาด": v["errors"], "คำเตือน": v["warnings"]})
    df_show(rows, height=min(760, 40 + 35 * len(rows)))
    finds = rep.get("findings") or []
    if finds:
        df = pd.DataFrame([{"ระดับ": "ข้อผิดพลาด" if f["level"] == "error" else "คำเตือน", "ข้อ": f["check"],
                            "รายการตรวจสอบ": V.CHECK_NAMES.get(f["check"], ""), "รายละเอียด": f["message"]} for f in finds])
        df_show(df, height=300)
        st.download_button("ดาวน์โหลดรายงานผลการตรวจสอบ (CSV)", ("﻿" + df.to_csv(index=False)).encode("utf-8"), "validation_report.csv", "text/csv",
                           key=f"dl_vr_{id(rep)}")


def versions_page() -> None:
    ctx = require_ctx(ADMIN, DEPT_ADMIN)
    tid = require_term()
    banner("ฉบับตาราง & เผยแพร่", "ตรวจสอบ ส่งตรวจ อนุมัติ และเผยแพร่ตาราง — ทุกฉบับถูกเก็บไว้เป็นประวัติ")
    tts = timetable_options(ctx, tid)
    if not tts:
        st.info("ยังไม่มีตารางในภาคเรียนนี้ — เริ่มที่เมนู “จัดตารางอัตโนมัติ” หรือ “จัด/แก้ไขตาราง”")
        return
    rows = [{"ฉบับที่": t.version, "ชื่อ": t.name or "", "ขอบเขต": "เฉพาะแผนก" if t.scope_department_id else "ทั้งวิทยาลัย",
             "สถานะ": T.STATUS_LABEL.get(t.status, t.status),
             "ผลตรวจสอบ": {"pass": "ผ่านทั้งหมด", "warn": "ผ่าน แต่มีคำเตือน", "fail": "ไม่ผ่าน"}.get(t.validation_status or "", "ยังไม่ตรวจ"),
             "สร้างโดย": t.created_by, "สร้างเมื่อ": t.created_at.strftime("%d/%m/%Y %H:%M"), "อ้างอิงฉบับ": next((p.version for p in tts if p.id == t.parent_id), "")}
            for t in tts]
    df_show(rows)
    sel = st.selectbox("เลือกฉบับตาราง", [t.id for t in tts], format_func=lambda i: tt_label(next(t for t in tts if t.id == i)), key="ver_sel")
    with tx() as s:
        tt = s.get(Timetable, sel)
        status, vstat, vrep = tt.status, tt.validation_status, tt.validation_report
    st.markdown(f"{status_badge(status)} &nbsp; {validation_badge(vstat)}", unsafe_allow_html=True)
    if status == "published":
        st.success("ฉบับนี้คือตารางที่มีผลใช้จริงในภาคเรียนนี้")
    c = st.columns(5)
    if c[0].button("ตรวจสอบตาราง", icon=":material/verified:", width="stretch"):
        run_safely(lambda: _validate(ctx, sel), "ตรวจสอบเรียบร้อย")
        st.rerun()
    if c[1].button("ส่งตรวจสอบ", icon=":material/send:", width="stretch", disabled=status not in ("success", "draft", "validation_failed")):
        if run_safely(lambda: _do(T.submit_for_review, ctx, sel), "ส่งตรวจสอบแล้ว"):
            st.rerun()
    if ctx.role == ADMIN:
        if c[2].button("อนุมัติ", icon=":material/check_circle:", width="stretch", disabled=status not in ("pending_review", "success")):
            if run_safely(lambda: _do(T.approve, ctx, sel), "อนุมัติแล้ว"):
                st.rerun()
        pub_ok = st.session_state.get(f"pubok_{sel}", False)
        if c[3].button("เผยแพร่", icon=":material/public:", width="stretch", type="primary", disabled=status != "approved"):
            st.session_state[f"pubconf_{sel}"] = True
        if st.session_state.get(f"pubconf_{sel}"):
            st.warning("ยืนยันการเผยแพร่? ตารางที่เผยแพร่ก่อนหน้า (ถ้ามี) จะถูกเปลี่ยนสถานะเป็น “ถูกแทนที่” แต่ยังเก็บไว้เป็นประวัติ")
            if st.button("ยืนยันเผยแพร่", type="primary", key=f"pubyes_{sel}"):
                st.session_state.pop(f"pubconf_{sel}", None)
                if run_safely(lambda: _do(T.publish, ctx, sel), "เผยแพร่แล้ว"):
                    st.rerun()
        _ = pub_ok
    if c[4].button("สร้างฉบับใหม่เพื่อแก้ไข", icon=":material/file_copy:", width="stretch"):
        out = {}

        def go():
            with tx() as s:
                out["id"] = T.clone(s, ctx, sel).id

        if run_safely(go, "สร้างฉบับใหม่แล้ว — แก้ไขได้ที่เมนู “จัด/แก้ไขตาราง”"):
            st.session_state["manual_tt"] = out["id"]
            st.rerun()
    if ctx.role == ADMIN and status not in ("published", "superseded", "approved"):
        with st.expander("ลบฉบับร่างนี้"):
            if st.checkbox("ยืนยันลบฉบับร่าง (ไม่สามารถกู้คืนได้)", key=f"delok_{sel}") and st.button("ลบฉบับร่าง", key=f"del_{sel}"):
                if run_safely(lambda: _do(T.delete_draft, ctx, sel), "ลบแล้ว"):
                    st.rerun()
    st.subheader("รายงานผลการตรวจสอบ")
    if vrep:
        validation_report_view(vrep)
    else:
        st.info("ยังไม่ได้ตรวจสอบฉบับนี้ — กด “ตรวจสอบตาราง”")
