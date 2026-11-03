"""หน้าผู้ดูแลระบบ: บัญชีผู้ใช้ ตั้งค่า (ภาคเรียน โครงสร้างเวลา กฎ รหัสผ่านเริ่มต้น) ประวัติการดำเนินการ คำขอเปลี่ยนแปลง และคู่มือ"""
from __future__ import annotations

import pandas as pd
import streamlit as st
from sqlalchemy import select

from .. import audit, auth, config, settings_service as S, timetable_service as T
from ..models import AuditLog, ChangeRequest, Department, Entry, Room, Student, Teacher, Term, Timetable, User
from ..permissions import ADMIN, DEPT_ADMIN, ROLE_LABELS, STUDENT, TEACHER
from .common import banner, df_show, require_ctx, require_term, run_safely, show_conflicts, term_label, tx


# ------------------------------------------------------------------ users
def users_page() -> None:
    ctx = require_ctx(ADMIN)
    banner("จัดการบัญชีผู้ใช้งาน", "ไม่มีการสมัครสมาชิก — บัญชีถูกสร้างโดยผู้ดูแลระบบเท่านั้น · ไม่แสดงรหัสผ่านของผู้ใช้")
    with tx() as s:
        has_default = bool(auth.default_password_hash(s))
        deps = {d.id: d.name for d in s.scalars(select(Department)).all()}
        users = s.scalars(select(User).order_by(User.role, User.username)).all()
        rows = [{"id": u.id, "ชื่อ": u.display_name, "ชื่อผู้ใช้": u.username, "ประเภทสิทธิ์": ROLE_LABELS[u.role],
                 "แผนก(สิทธิ์)": ", ".join(deps.get(d, str(d)) for d in (u.dept_scope or [])),
                 "สถานะบัญชี": "ใช้งาน" if u.active else "ระงับ", "เปลี่ยนรหัสผ่านแล้ว": "ยัง (ใช้รหัสเริ่มต้น)" if u.must_change_password else "แล้ว",
                 "แก้ไขตารางได้": "ใช่" if u.can_edit_timetable else "", "เข้าสู่ระบบล่าสุด": u.last_login.strftime("%d/%m/%Y %H:%M") if u.last_login else "-"} for u in users]
        teachers = s.scalars(select(Teacher).where(Teacher.active.is_(True)).order_by(Teacher.name)).all()
        tl = {t.id: f"{t.code} {t.name}" for t in teachers}
    if not has_default:
        st.warning("ยังไม่ได้กำหนดรหัสผ่านเริ่มต้น — ตั้งได้ที่เมนู “ตั้งค่า” ก่อนสร้างบัญชีหรือรีเซ็ตรหัสผ่าน")
    q = st.text_input("ค้นหาบัญชี (ชื่อ/ชื่อผู้ใช้/สิทธิ์)", key="u_q")
    rf = st.multiselect("กรองประเภทสิทธิ์", list(ROLE_LABELS.values()), key="u_rf")
    view = [r for r in rows if (not q or q.lower() in f"{r['ชื่อ']} {r['ชื่อผู้ใช้']} {r['ประเภทสิทธิ์']}".lower()) and (not rf or r["ประเภทสิทธิ์"] in rf)]
    st.caption(f"แสดง {len(view)} จาก {len(rows)} บัญชี")
    evt = st.dataframe(view, hide_index=True, on_select="rerun", selection_mode="single-row", key="u_tbl", column_config={"id": None})
    if evt and evt.selection and evt.selection.rows:
        uid = view[evt.selection.rows[0]]["id"]
        u = next(r for r in rows if r["id"] == uid)
        st.markdown(f"#### จัดการบัญชี: {u['ชื่อ']} ({u['ชื่อผู้ใช้']})")
        c = st.columns(4)
        if c[0].button("รีเซ็ตรหัสผ่านเป็นค่าเริ่มต้น", icon=":material/lock_reset:", width="stretch", disabled=not has_default):
            st.session_state[f"rst_{uid}"] = True
        if st.session_state.get(f"rst_{uid}"):
            st.warning("ยืนยันรีเซ็ตรหัสผ่าน? ผู้ใช้จะต้องเปลี่ยนรหัสผ่านเมื่อเข้าสู่ระบบครั้งถัดไป")
            if st.button("ยืนยันรีเซ็ต", type="primary", key=f"rsty_{uid}"):
                st.session_state.pop(f"rst_{uid}")
                if run_safely(lambda: _w(auth.reset_password, ctx, uid), "รีเซ็ตรหัสผ่านแล้ว (บันทึกประวัติผู้ดำเนินการแล้ว)"):
                    st.rerun()
        active = u["สถานะบัญชี"] == "ใช้งาน"
        if c[1].button("ระงับบัญชี" if active else "เปิดใช้งาน", icon=":material/block:" if active else ":material/check_circle:", width="stretch"):
            if run_safely(lambda: _w(auth.set_active, ctx, uid, not active), "บันทึกแล้ว"):
                st.rerun()
        if u["ประเภทสิทธิ์"] == ROLE_LABELS[TEACHER]:
            allow = u["แก้ไขตารางได้"] == "ใช่"
            if c[2].button("ยกเลิกสิทธิ์แก้ไขตาราง" if allow else "ให้สิทธิ์แก้ไขตาราง", width="stretch"):
                if run_safely(lambda: _w(auth.set_teacher_edit, ctx, uid, not allow), "บันทึกแล้ว"):
                    st.rerun()
        if u["ประเภทสิทธิ์"] == ROLE_LABELS[DEPT_ADMIN]:
            if c[3].button("ยกเลิกสิทธิ์ฝ่ายวิชาการประจำแผนก", width="stretch"):
                if run_safely(lambda: _w(auth.revoke_dept_admin, ctx, uid), "บันทึกแล้ว"):
                    st.rerun()
    st.divider()
    t1, t2, t3 = st.tabs(["กำหนดสิทธิ์ฝ่ายวิชาการประจำแผนก", "สร้างบัญชีครู/นักเรียนจากข้อมูล", "เพิ่มบัญชีรายบุคคล"])
    with t1:
        st.caption("เลือกจากรายชื่อครู หรือค้นหาด้วยรหัสครู/ชื่อ — ถ้าครูมีบัญชีอยู่แล้วจะเชื่อมสิทธิ์กับบัญชีเดิม ไม่สร้างซ้ำ")
        how = st.radio("วิธีเลือกครู", ["เลือกจากรายชื่อ", "กรอกรหัสครูหรือค้นหาชื่อ"], horizontal=True, key="da_how")
        tsel = None
        if how == "เลือกจากรายชื่อ":
            tsel = st.selectbox("ครู", [None] + list(tl), format_func=lambda i: "— เลือก —" if i is None else tl[i], key="da_t")
        else:
            qq = st.text_input("รหัสครู หรือ ชื่อ-นามสกุล", key="da_q")
            hits = [i for i, lbl in tl.items() if qq and qq.lower() in lbl.lower()]
            if qq and not hits:
                st.error("ไม่พบครู")
            if hits:
                tsel = st.selectbox("ผลการค้นหา", hits, format_func=tl.get, key="da_hit")
        dsel = st.multiselect("แผนกที่รับผิดชอบ", list(deps), format_func=deps.get, key="da_d")
        if st.button("กำหนดสิทธิ์", type="primary", disabled=not (tsel and dsel)):
            if run_safely(lambda: _w(auth.grant_dept_admin, ctx, tsel, dsel), "กำหนดสิทธิ์แล้ว"):
                st.rerun()
    with t2:
        st.caption("ชื่อผู้ใช้ = รหัสครู / รหัสนักเรียน · รหัสผ่าน = รหัสผ่านเริ่มต้น และต้องเปลี่ยนเมื่อเข้าสู่ระบบครั้งแรก")
        c = st.columns(2)
        if c[0].button("สร้างบัญชีให้ครูที่ยังไม่มีบัญชี", disabled=not has_default, width="stretch"):
            out = {}
            if run_safely(lambda: out.update(_w(auth.bulk_create_accounts, ctx, TEACHER))):
                st.success(f"สร้าง {out['created']} บัญชี · ข้าม {out['skipped']} (มีบัญชีแล้ว)")
        if c[1].button("สร้างบัญชีให้นักเรียนที่ยังไม่มีบัญชี", disabled=not has_default, width="stretch"):
            out = {}
            if run_safely(lambda: out.update(_w(auth.bulk_create_accounts, ctx, STUDENT))):
                st.success(f"สร้าง {out['created']} บัญชี · ข้าม {out['skipped']} (มีบัญชีแล้ว)")
    with t3:
        with st.form("new_user"):
            role = st.selectbox("ประเภทสิทธิ์", [ADMIN, TEACHER, STUDENT], format_func=ROLE_LABELS.get)
            uname = st.text_input("ชื่อผู้ใช้")
            dname = st.text_input("ชื่อที่แสดง")
            link = st.text_input("เชื่อมกับรหัสครู/รหัสนักเรียน (ถ้ามี)")
            go = st.form_submit_button("สร้างบัญชี", disabled=not has_default)
        if go:
            def mk():
                with tx() as s:
                    t_id = s_id = None
                    if link:
                        if role == TEACHER:
                            t = s.scalars(select(Teacher).where(Teacher.code == link)).first()
                            if not t:
                                raise ValueError("ไม่พบรหัสครู")
                            t_id = t.id
                        elif role == STUDENT:
                            x = s.scalars(select(Student).where(Student.code == link)).first()
                            if not x:
                                raise ValueError("ไม่พบรหัสนักเรียน")
                            s_id = x.id
                    auth.create_user(s, ctx, uname, role, dname, t_id, s_id)

            if run_safely(mk, "สร้างบัญชีแล้ว"):
                st.rerun()


def _w(fn, ctx, *args):
    with tx() as s:
        return fn(s, ctx, *args)


# ------------------------------------------------------------------ settings
def settings_page() -> None:
    ctx = require_ctx(ADMIN)
    banner("ตั้งค่าระบบ", "ภาคเรียน โครงสร้างเวลาเรียน กฎการจัดตาราง และรหัสผ่านเริ่มต้น — แก้ไขได้โดยไม่ต้องแก้โค้ด")
    t0, t1, t2, t3, t4 = st.tabs(["ปีการศึกษา/ภาคเรียน", "โครงสร้างเวลาเรียน", "กฎการจัดตาราง", "รหัสผ่านเริ่มต้น", "สถานะระบบ"])
    with t0:
        with tx() as s:
            terms = s.scalars(select(Term).order_by(Term.year.desc(), Term.semester.desc())).all()
            rows = [{"ภาคเรียน": t.label, "ปัจจุบัน": "ใช่" if t.is_current else ""} for t in terms]
            tids = [(t.id, t.label) for t in terms]
        df_show(rows)
        c = st.columns(3)
        y = c[0].number_input("ปีการศึกษา (พ.ศ.)", 2500, 2700, 2569)
        sem = c[1].selectbox("ภาคเรียน", [1, 2, 3], format_func=lambda x: {1: "1", 2: "2", 3: "3 (ฤดูร้อน)"}[x])
        cur = c[2].checkbox("ตั้งเป็นภาคเรียนปัจจุบัน", True)
        copy_from = st.selectbox("คัดลอกการตั้งค่าโครงสร้างเวลา/กฎจากภาคเรียน", [None] + [t[0] for t in tids], format_func=lambda i: "— ใช้ค่าเริ่มต้น —" if i is None else dict(tids)[i])
        if st.button("เพิ่มภาคเรียน", type="primary"):
            def go():
                with tx() as s:
                    if s.scalars(select(Term).where(Term.year == y, Term.semester == sem)).first():
                        raise ValueError("มีภาคเรียนนี้อยู่แล้ว")
                    if cur:
                        for t in s.scalars(select(Term)).all():
                            t.is_current = False
                    t = Term(year=int(y), semester=int(sem), is_current=cur)
                    s.add(t)
                    s.flush()
                    if copy_from:
                        for k in S.DEFAULTS:
                            r = S._row(s, k, copy_from)
                            if r is not None:
                                S.put(s, k, r.value, t.id)
                    audit.log(s, ctx.username, "create_term", "term", {"year": y, "semester": sem})
                    st.session_state["term_id"] = t.id

            if run_safely(go, "เพิ่มภาคเรียนแล้ว"):
                st.rerun()
        if tids:
            setc = st.selectbox("เปลี่ยนภาคเรียนปัจจุบัน", [t[0] for t in tids], format_func=lambda i: dict(tids)[i], key="set_cur")
            if st.button("บันทึกภาคเรียนปัจจุบัน"):
                with tx() as s:
                    for t in s.scalars(select(Term)).all():
                        t.is_current = t.id == setc
                    audit.log(s, ctx.username, "set_current_term", "term", {"id": setc})
                st.rerun()
    tid = require_term()
    with t1:
        _time_structure(ctx, tid)
    with t2:
        _rules(ctx, tid)
    with t3:
        st.caption("รหัสผ่านเริ่มต้นใช้กับบัญชีใหม่และการรีเซ็ตรหัสผ่าน · ระบบเก็บเฉพาะค่าแฮช (bcrypt) ไม่เก็บข้อความจริง และจะไม่แสดงซ้ำ")
        with tx() as s:
            has = bool(auth.default_password_hash(s))
        st.markdown("สถานะ: " + (":green[:material/check_circle:] กำหนดแล้ว" if has else ":orange[:material/warning:] ยังไม่ได้กำหนด"))
        with st.form("defpw"):
            p1 = st.text_input("รหัสผ่านเริ่มต้นใหม่", type="password")
            p2 = st.text_input("ยืนยัน", type="password")
            go = st.form_submit_button("บันทึก")
        if go:
            if p1 != p2:
                st.error("รหัสผ่านไม่ตรงกัน")
            elif run_safely(lambda: _w(auth.set_default_password, ctx, p1), "บันทึกรหัสผ่านเริ่มต้นแล้ว"):
                st.rerun()
    with t4:
        from .. import db

        ok, msg = db.check_connection()
        st.markdown(f"ฐานข้อมูล: {':green[:material/check_circle:]' if ok else ':red[:material/error:]'} {msg}")
        if config.is_sqlite():
            st.warning("ระบบกำลังใช้ SQLite (โหมดพัฒนา/ทดสอบ) — บน Streamlit Cloud ข้อมูลจะหายเมื่อแอปรีสตาร์ต ต้องตั้งค่า DATABASE_URL เป็น PostgreSQL สำหรับใช้งานจริง")
        else:
            st.success("ใช้ฐานข้อมูลภายนอก (PostgreSQL) — ข้อมูลถาวร")


def _time_structure(ctx, tid):
    with tx() as s:
        periods = S.get(s, "time_structure", tid)
        maxd = S.get(s, "max_periods_per_day_group", tid)
        allow_sp = S.get(s, "allow_special_periods", tid)
    kinds = {"pre": "ก่อนเริ่มเรียน (จัดไม่ได้)", "teaching": "คาบเรียนปกติ", "break": "คาบพัก (ล็อก)", "special": "คาบพิเศษ (ต้องเปิดใช้)"}
    inv = {v: k for k, v in kinds.items()}
    df = pd.DataFrame([{"คาบ": p["no"], "ชื่อ": p["label"], "เริ่ม": p["start"], "สิ้นสุด": p["end"], "ประเภท": kinds[p["kind"]]} for p in periods])
    st.caption(f"ภาคเรียน {term_label(tid)} · ค่าเริ่มต้นยึดตามตารางสอนของวิทยาลัย · คาบพักถูกล็อกไม่ให้จัดวิชา · เวลาใช้รูปแบบ HH:MM")
    ed = st.data_editor(df, num_rows="dynamic", hide_index=True, key="ts_ed",
                        column_config={"ประเภท": st.column_config.SelectboxColumn(options=list(inv), required=True), "คาบ": st.column_config.NumberColumn(min_value=0, max_value=20, step=1)})
    c = st.columns(2)
    nmax = c[0].number_input("จำนวนคาบเรียนปกติสูงสุดต่อวันของกลุ่มเรียน", 1, 12, int(maxd))
    sp = c[1].checkbox("เปิดใช้คาบพิเศษ (เช่น คาบที่ 11) — กรณีพิเศษตามประกาศวิทยาลัย", bool(allow_sp))
    if sp and not allow_sp:
        st.warning("การเปิดใช้คาบพิเศษจะทำให้ระบบจัดรายวิชาในคาบนั้นได้ แต่ยังคงจำกัดจำนวนคาบต่อวันตามค่าด้านบน")
    if st.button("บันทึกโครงสร้างเวลา", icon=":material/save:", type="primary"):
        new = []
        try:
            for _, r in ed.iterrows():
                if pd.isna(r["คาบ"]):
                    continue
                new.append({"no": int(r["คาบ"]), "label": str(r["ชื่อ"] or f"คาบที่ {int(r['คาบ'])}"), "start": str(r["เริ่ม"]).strip(), "end": str(r["สิ้นสุด"]).strip(), "kind": inv.get(r["ประเภท"], "teaching")})
        except (TypeError, ValueError) as e:
            st.error(f"ข้อมูลไม่ถูกต้อง: {e}")
            return
        errs = S.validate_time_structure(new)
        if errs:
            for e in errs:
                st.error(e)
            return
        with tx() as s:
            S.put(s, "time_structure", new, tid)
            S.put(s, "max_periods_per_day_group", int(nmax), tid)
            S.put(s, "allow_special_periods", bool(sp), tid)
            audit.log(s, ctx.username, "edit_time_structure", "settings", {"term": tid})
        st.success("บันทึกแล้ว")


def _rules(ctx, tid):
    with tx() as s:
        st_ = {k: S.get(s, k, tid) for k in S.DEFAULTS}
        teachers = {t.id: t.name for t in s.scalars(select(Teacher).where(Teacher.is_meeting_member.is_(True))).all()}
        rooms = {r.id: f"{r.label} {r.name or ''}" for r in s.scalars(select(Room)).all()}
        ts = S.time_structure(s, tid)
    days = {d: S.DAY_NAMES[d] for d in ts.days}
    st.markdown("##### การแปลงหน่วยชั่วโมงเป็นคาบ")
    st.caption("จำนวนคาบ = (ชั่วโมงทฤษฎี + ชั่วโมงปฏิบัติ) × อัตราแปลง — ต้องยืนยันก่อนจัดตาราง (ระบบจะไม่ตีความเองโดยไม่ได้รับการยืนยัน)")
    c = st.columns(2)
    rate = c[0].number_input("1 ชั่วโมง/สัปดาห์ = กี่คาบ", 0.5, 4.0, float(st_["hour_to_period_rate"]), 0.5)
    conf = c[1].checkbox("ยืนยันกติกาการแปลงหน่วยนี้", bool(st_["hour_to_period_confirmed"]))
    mt, act, hr = st_["meeting"], st_["activity"], st_["homeroom"]
    with st.container(border=True):
        st.markdown("##### :material/tune: เงื่อนไขพิเศษ (กดเลือกเฉพาะข้อที่ต้องการใช้)")
        st.caption("ข้อที่ไม่เลือก ระบบจะไม่นำไปใช้ทั้งตอนจัดตารางและตอนตรวจสอบตาราง — เปลี่ยนได้ทุกเมื่อ แล้วกดบันทึกและจัดตารางใหม่")
        from .p_schedule import SPECIAL_LABELS

        room_rule = st.checkbox(SPECIAL_LABELS[9], bool(st_["room_type_rule"]), key="sc9")
        m_en = st.checkbox(SPECIAL_LABELS[10], bool(mt.get("enabled")), key="sc10")
        h_en = st.checkbox(SPECIAL_LABELS[11], bool(hr.get("enabled")), key="sc11")
        a_en = st.checkbox(SPECIAL_LABELS[12], bool(act.get("enabled")), key="sc12")
        gj = st.checkbox(SPECIAL_LABELS[13], bool(st_["general_joint"]), key="sc13")
        st.caption("รายละเอียดของแต่ละข้อ (วัน คาบ ห้องประชุม ฯลฯ) จะแสดงด้านล่างเมื่อเลือกใช้ข้อนั้น")
    m_day, m_p, m_room = int(mt["day"]), [p for p in mt["periods"] if p in ts.teaching_periods], mt.get("room_id")
    if m_en:
        st.markdown("##### การประชุมหัวหน้าแผนก/หัวหน้างาน")
        c = st.columns(3)
        m_day = c[0].selectbox("วัน", list(days), index=list(days).index(int(mt["day"])), format_func=days.get, key="m_day")
        m_p = c[1].multiselect("คาบ", ts.teaching_periods, default=m_p, key="m_p")
        m_room = c[2].selectbox("ห้องประชุม (ถ้ามี)", [None] + list(rooms), index=([None] + list(rooms)).index(mt.get("room_id")) if mt.get("room_id") in rooms else 0,
                                format_func=lambda i: "-" if i is None else rooms[i], key="m_room")
        st.caption(f"ผู้เข้าร่วมประชุม ({len(teachers)} คน) กำหนดรายบุคคลที่ จัดการข้อมูล > ครูผู้สอน > “เข้าประชุมหัวหน้า”: {', '.join(teachers.values()) or '-'}")
    a_day, a_p, a_lock = int(act["day"]), [p for p in act["periods"] if p in ts.teaching_periods], act.get("lock_all_groups", True)
    if a_en:
        st.markdown("##### รายวิชากิจกรรม")
        c = st.columns(2)
        a_day = c[0].selectbox("วันกิจกรรม", list(days), index=list(days).index(int(act["day"])), format_func=days.get, key="a_day")
        a_p = c[1].multiselect("คาบกิจกรรม (ต่อเนื่อง)", ts.teaching_periods, default=a_p, key="a_p")
        a_lock = st.checkbox("ล็อกช่วงกิจกรรมสำหรับทุกกลุ่มเรียน (ห้ามจัดวิชาปกติในช่วงนี้)", a_lock)
    h_n, h_room, h_count, slots = int(hr.get("periods_per_week", 1)), bool(hr.get("needs_room")), bool(hr.get("count_as_teaching")), hr.get("allowed_slots")
    if h_en:
        st.markdown("##### โฮมรูม")
        c = st.columns(3)
        h_n = c[0].number_input("คาบต่อสัปดาห์ (อย่างน้อย)", 1, 5, h_n)
        h_room = c[1].checkbox("ต้องใช้ห้องเรียน", h_room)
        h_count = c[2].checkbox("นับเป็นชั่วโมงสอน", h_count)
        allowed = hr.get("allowed_slots")
        h_fix = st.checkbox("กำหนดช่วงเวลาที่อนุญาตให้จัดโฮมรูม", allowed is not None)
        slots = None
        if h_fix:
            all_slots = [(d, p) for d in ts.days for p in ts.teaching_periods]
            cur = [tuple(x) for x in (allowed or [])]
            pick = st.multiselect("ช่วงเวลาที่อนุญาต", all_slots, default=[x for x in cur if x in all_slots], format_func=lambda x: f"{S.DAY_NAMES[x[0]]} คาบ {x[1]}")
            slots = [list(x) for x in pick]
    pref, maxg = ", ".join(st_["general_prefixes"]), int(st_["general_joint_max_groups"])
    if gj:
        st.markdown("##### วิชาสามัญเรียนร่วม")
        c = st.columns(2)
        pref = c[0].text_input("รหัสวิชาขึ้นต้น (คั่นด้วย ,)", pref)
        maxg = c[1].number_input("จำนวนกลุ่มที่เรียนร่วมกัน", 2, 6, maxg)
        st.caption("ระบบจับคู่กลุ่มที่เรียนวิชาเดียวกันกับครูคนเดียวกันให้อัตโนมัติ โดยข้ามคู่ที่ไม่มีห้องจุผู้เรียนรวมได้")
    st.markdown("##### ตัวจัดตาราง")
    c = st.columns(3)
    blk = c[0].number_input("คาบต่อเนื่องสูงสุดเมื่อรายวิชาไม่ระบุ", 1, 6, int(st_["default_block_max"]))
    tlim = c[1].number_input("เวลาประมวลผลเริ่มต้น (วินาที)", 10, 900, int(st_["solver_time_limit"]))
    w = st_["soft_weights"]
    with st.expander("น้ำหนักข้อจำกัดเพื่อคุณภาพตาราง (ยิ่งมาก ยิ่งให้ความสำคัญ)"):
        cc = st.columns(3)
        w_new = {
            "group_gaps": cc[0].number_input("ลดช่วงว่างของนักเรียน", 0, 50, int(w.get("group_gaps", 5))),
            "teacher_gaps": cc[1].number_input("ลดช่วงว่างของครู", 0, 50, int(w.get("teacher_gaps", 2))),
            "late_period": cc[2].number_input("หลีกเลี่ยงคาบท้ายวัน", 0, 50, int(w.get("late_period", 1))),
            "same_day_repeat": cc[0].number_input("กระจายวิชาเดียวกันไม่ให้อยู่วันเดียว", 0, 50, int(w.get("same_day_repeat", 8))),
            "preferred": cc[1].number_input("จัดตามเวลาที่ครูต้องการ", 0, 50, int(w.get("preferred", 3))),
            "stability": cc[2].number_input("ลดการเปลี่ยนแปลงจากตารางเดิม", 0, 50, int(w.get("stability", 4))),
        }
    if st.button("บันทึกกฎการจัดตาราง", icon=":material/save:", type="primary"):
        if a_en and (not a_p or not ts.span_ok(min(a_p), len(a_p)) or sorted(a_p) != list(range(min(a_p), min(a_p) + len(a_p)))):
            st.error("คาบกิจกรรมต้องต่อเนื่องและไม่มีคาบพักคั่น")
            return
        if m_en and not m_p:
            st.error("ต้องเลือกคาบประชุม")
            return
        with tx() as s:
            S.put(s, "hour_to_period_rate", float(rate), tid)
            S.put(s, "hour_to_period_confirmed", bool(conf), tid)
            S.put(s, "meeting", {"enabled": m_en, "day": int(m_day), "periods": sorted(m_p), "room_id": m_room, "title": mt.get("title") or "ประชุมหัวหน้าแผนก/หัวหน้างาน"}, tid)
            S.put(s, "activity", {"enabled": a_en, "day": int(a_day), "periods": sorted(a_p), "lock_all_groups": a_lock}, tid)
            S.put(s, "homeroom", {"enabled": h_en, "periods_per_week": int(h_n), "allowed_slots": slots, "needs_room": h_room, "count_as_teaching": h_count}, tid)
            S.put(s, "general_prefixes", [x.strip() for x in pref.split(",") if x.strip()], tid)
            S.put(s, "room_type_rule", bool(room_rule), tid)
            S.put(s, "general_joint", bool(gj), tid)
            S.put(s, "general_joint_max_groups", int(maxg), tid)
            S.put(s, "default_block_max", int(blk), tid)
            S.put(s, "solver_time_limit", int(tlim), tid)
            S.put(s, "soft_weights", w_new, tid)
            audit.log(s, ctx.username, "edit_rules", "settings", {"term": tid})
        st.success("บันทึกแล้ว")


# ------------------------------------------------------------------ audit
def audit_page() -> None:
    require_ctx(ADMIN)
    banner("ประวัติการดำเนินการ", "บันทึกการดำเนินการสำคัญ (ไม่บันทึกรหัสผ่าน)")
    with tx() as s:
        rows = [{"เวลา": a.created_at.strftime("%d/%m/%Y %H:%M:%S"), "ผู้ใช้": a.username, "การดำเนินการ": a.action, "ข้อมูล": a.entity or "", "รายละเอียด": str(a.detail or "")}
                for a in s.scalars(select(AuditLog).order_by(AuditLog.id.desc()).limit(3000)).all()]
    q = st.text_input("ค้นหา", key="aud_q")
    view = [r for r in rows if not q or q.lower() in " ".join(map(str, r.values())).lower()]
    df_show(view, height=520)
    if view:
        df = pd.DataFrame(view)
        st.download_button("ดาวน์โหลด (CSV)", ("﻿" + df.to_csv(index=False)).encode("utf-8"), "audit_log.csv", "text/csv", icon=":material/download:")


# ------------------------------------------------------------------ change requests
def requests_page() -> None:
    ctx = require_ctx(ADMIN, DEPT_ADMIN, TEACHER)
    tid = require_term()
    banner("คำขอเปลี่ยนแปลงตาราง", "ครูเสนอการย้ายรายการสอน → ฝ่ายวิชาการพิจารณา (ระบบตรวจข้อจำกัดให้ทุกครั้ง)")
    if ctx.role == TEACHER:
        _teacher_request(ctx, tid)
    with tx() as s:
        q = select(ChangeRequest).order_by(ChangeRequest.id.desc())
        reqs = s.scalars(q).all()
        if ctx.role == TEACHER:
            reqs = [r for r in reqs if r.requested_by == ctx.username]
        rows = []
        for r in reqs:
            e = s.get(Entry, r.entry_id)
            tt = s.get(Timetable, r.timetable_id)
            if tt is None or tt.term_id != tid:
                continue
            label = (f"{e.course_code or ''} {e.course_name or ''}".strip() or {"homeroom": "โฮมรูม", "meeting": "ประชุม"}.get(e.entry_type, "")) if e else "(ไม่พบรายการ)"
            if e:
                label += " · กลุ่ม " + ", ".join(g.group_code or "" for g in e.groups)
            rows.append({"id": r.id, "ผู้ขอ": r.requested_by, "รายการ": label,
                         "เดิม": f"{S.DAY_NAMES[e.day]} คาบ {e.start_period}-{e.end_period}" if e else "-",
                         "ขอย้ายเป็น": f"{S.DAY_NAMES[r.new_day]} คาบ {r.new_start}", "เหตุผล": r.reason or "",
                         "สถานะ": {"pending": "รอพิจารณา", "approved": "อนุมัติ", "rejected": "ไม่อนุมัติ"}[r.status], "ผู้พิจารณา": r.reviewed_by or ""})
    st.subheader("รายการคำขอ")
    df_show([{k: v for k, v in r.items() if k != "id"} for r in rows])
    if ctx.role in (ADMIN, DEPT_ADMIN):
        pend = [r for r in rows if r["สถานะ"] == "รอพิจารณา"]
        if pend:
            rid = st.selectbox("พิจารณาคำขอ", [r["id"] for r in pend], format_func=lambda i: next(f"#{r['id']} {r['ผู้ขอ']}: {r['รายการ']} → {r['ขอย้ายเป็น']}" for r in pend if r["id"] == i))
            c = st.columns(2)
            if c[0].button("อนุมัติ (สร้าง/แก้ไขฉบับร่าง)", icon=":material/check_circle:", type="primary"):
                out = {}

                def go():
                    with tx() as s:
                        t = T.review_request(s, ctx, rid, True)
                        out["tt"] = t.version if t else None

                if run_safely(go):
                    st.success(f"อนุมัติแล้ว — นำไปใช้ในตารางฉบับร่างที่ {out['tt']} (ต้องตรวจสอบและเผยแพร่ใหม่)")
            if c[1].button("ไม่อนุมัติ", icon=":material/close:"):
                if run_safely(lambda: _w(T.review_request, ctx, rid, False), "บันทึกแล้ว"):
                    st.rerun()


def _teacher_request(ctx, tid):
    with tx() as s:
        pub = T.latest_published(s, tid)
        if not pub:
            st.info("ยังไม่มีตารางที่เผยแพร่")
            return
        mine = [T.entry_dict(e) for e in T.entries_of(s, pub.id) if ctx.teacher_id in e.teacher_ids and e.entry_type in ("class", "homeroom")]
        ts = S.time_structure(s, tid)
        pub_id = pub.id
        rooms = {r.id: r.label for r in s.scalars(select(Room).where(Room.active.is_(True))).all()}
    if not mine:
        st.info("ไม่มีรายการสอนของท่านในตารางที่เผยแพร่")
        return
    with st.expander("เสนอการเปลี่ยนแปลง", icon=":material/add:", expanded=True):
        eid = st.selectbox("รายการสอน", [e["id"] for e in mine], format_func=lambda i: next(
            f"{e.get('course_code') or 'โฮมรูม'} {e.get('course_name') or ''} · {S.DAY_NAMES[e['day']]} คาบ {e['start_period']}-{e['end_period']} · {', '.join(e['group_codes'])}" for e in mine if e["id"] == i))
        e = next(x for x in mine if x["id"] == eid)
        c = st.columns(3)
        nd = c[0].selectbox("วันใหม่", ts.days, format_func=lambda d: S.DAY_NAMES[d])
        ns = c[1].selectbox("คาบเริ่มใหม่", ts.teaching_periods)
        nr = c[2].selectbox("ห้อง", [e["room_id"]] + [r for r in rooms if r != e["room_id"]], format_func=lambda i: "-" if i is None else rooms.get(i, "-"))
        reason = st.text_input("เหตุผล")
        if st.button("ส่งคำขอ", type="primary"):
            out = {}

            def go():
                with tx() as s:
                    _, conf = T.create_request(s, ctx, pub_id, eid, nd, ns, nr, reason)
                    out["conf"] = conf

            if run_safely(go):
                if out["conf"]:
                    st.warning("ส่งคำขอแล้ว แต่ระบบตรวจพบปัญหาที่ฝ่ายวิชาการต้องพิจารณา:")
                    show_conflicts(out["conf"])
                else:
                    st.success("ส่งคำขอแล้ว — ไม่พบการชนกับข้อจำกัดบังคับ รอฝ่ายวิชาการพิจารณา")


# ------------------------------------------------------------------ help
def help_page() -> None:
    require_ctx()
    banner("คู่มือการใช้งาน", "อธิบายตามหน้าจอและปุ่มที่มีอยู่จริงในระบบ")
    p = config.DOCS_DIR / "คู่มือการใช้งาน.md"
    if p.exists():
        text = p.read_text(encoding="utf-8")
        st.download_button("ดาวน์โหลดคู่มือ (Markdown)", text.encode("utf-8"), "คู่มือการใช้งาน_NVC_Smart_Timetable.md", "text/markdown", icon=":material/download:")
        st.markdown(text)
    else:
        st.error("ไม่พบไฟล์คู่มือ")
