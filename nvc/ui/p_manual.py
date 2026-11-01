"""หน้าจัดตารางด้วยตนเอง / แก้ไขตาราง — ตรวจข้อจำกัดทุกครั้งก่อนบันทึก มีปุ่มย้อนกลับ"""
from __future__ import annotations

import streamlit as st
from sqlalchemy import or_, select

from .. import problem as P, settings_service as S, timetable_service as T, validator as V
from ..models import Assignment, ClassGroup, Course, GroupCourse, JointClass, Room, RoomType, Teacher, Timetable
from ..permissions import ADMIN, DEPT_ADMIN, TEACHER
from ..render import view_html
from ..views import get_view, options, visible_departments
from .common import banner, flash, require_ctx, require_term, run_safely, show_conflicts, status_badge, timetable_options, tt_label, tx

EDITABLE = {"draft", "success", "partial", "validation_failed", "not_scheduled"}
PART_TH = {"theory": "ทฤษฎี", "practice": "ปฏิบัติ", "all": "ทั้งหมด"}


def page() -> None:
    ctx = require_ctx(ADMIN, DEPT_ADMIN, TEACHER)
    tid = require_term()
    if ctx.role == TEACHER and not ctx.can_edit_timetable:
        st.error("บัญชีของท่านยังไม่ได้รับสิทธิ์แก้ไขตารางโดยตรง — ใช้เมนู “เสนอการเปลี่ยนแปลง” แทน")
        st.stop()
    banner("จัดตาราง / แก้ไขตารางด้วยตนเอง", "ระบบตรวจสอบข้อจำกัดบังคับทุกครั้งก่อนบันทึก · รายการที่ชนกันจะแสดงเป็นสีแดง")
    tts = [t for t in timetable_options(ctx, tid) if t.status in EDITABLE]
    c1, c2 = st.columns([3, 1])
    if ctx.role in (ADMIN, DEPT_ADMIN):
        with c2.popover("สร้างตารางเปล่า", icon=":material/add:", width="stretch"):
            with tx() as s:
                deps = [(d.id, d.name) for d in visible_departments(s, ctx)]
            if ctx.role == ADMIN:
                dsel = st.selectbox("ขอบเขต", [None] + [d[0] for d in deps], format_func=lambda i: "ทั้งวิทยาลัย" if i is None else dict(deps)[i])
            else:
                dsel = st.selectbox("แผนก", [d[0] for d in deps], format_func=lambda i: dict(deps)[i])
            nm = st.text_input("ชื่อฉบับ", "จัดด้วยตนเอง")
            if st.button("สร้าง", type="primary"):
                out = {}

                def go():
                    with tx() as s:
                        out["id"] = T.create_empty(s, ctx, tid, nm, dsel).id

                if run_safely(go, "สร้างตารางแล้ว (มีรายการประชุม/กิจกรรมตามข้อกำหนด)"):
                    st.session_state["manual_tt"] = out["id"]
                    st.rerun()
    if not tts:
        st.info("ไม่มีตารางที่แก้ไขได้ — สร้างตารางเปล่า หรือสร้างฉบับใหม่จากตารางเดิมที่เมนู “ฉบับตาราง & เผยแพร่”")
        return
    ids = [t.id for t in tts]
    cur = st.session_state.get("manual_tt")
    sel = c1.selectbox("ตารางที่กำลังแก้ไข", ids, index=ids.index(cur) if cur in ids else 0,
                       format_func=lambda i: tt_label(next(t for t in tts if t.id == i)), key="man_sel")
    st.session_state["manual_tt"] = sel
    with tx() as s:
        tt = s.get(Timetable, sel)
        lockv, status = tt.lock_version, tt.status
        current = [T.entry_dict(e) for e in T.entries_of(s, sel)]
        vr = V.validate_entries(s, tid, current, T._scope_groups(s, tt))
    bad_ids = {i for f in vr.errors if not f.incomplete for i in f.entry_ids}
    st.markdown(f"{status_badge(status)} &nbsp; ผลตรวจสอบปัจจุบัน: "
                f"{':red[:material/error:] พบข้อผิดพลาด ' + str(sum(1 for f in vr.errors if not f.incomplete)) + ' รายการ' if bad_ids or any(not f.incomplete for f in vr.errors) else ':green[:material/check_circle:] ไม่มีรายการชนกัน'}"
                f" · ยังจัดไม่ครบ {sum(1 for f in vr.errors if f.incomplete)} รายการ", unsafe_allow_html=True)
    u1, u2 = st.columns([1, 3])
    if u1.button("ย้อนกลับการแก้ไขล่าสุด", icon=":material/undo:", width="stretch"):
        out = {}

        def go():
            with tx() as s:
                out["m"] = T.undo_last(s, ctx, sel)

        if run_safely(go):
            flash(out["m"], "info")
            st.rerun()

    left, right = st.columns([1.55, 1])
    with left:
        vk = st.segmented_control("มุมมอง", ["group", "teacher", "room"], format_func={"group": "กลุ่มเรียน", "teacher": "ครู", "room": "ห้อง"}.get,
                                  default="group", key="man_vk") or "group"
        with tx() as s:
            opts = options(s, ctx, tid, vk)
        if not opts:
            st.info("ไม่มีข้อมูลสำหรับมุมมองนี้")
            return
        key = st.selectbox("เลือก", [o[0] for o in opts], format_func=lambda i: dict(opts)[i], key=f"man_key_{vk}")
        with tx() as s:
            vd = get_view(s, ctx, s.get(Timetable, sel), vk, key)
        vd.conflicts |= {e["id"] for e in vd.entries if e["id"] in bad_ids}
        st.markdown(view_html(vd, with_header=False), unsafe_allow_html=True)
        rows = [{"id": e["id"], "วัน": S.DAY_NAMES[e["day"]], "คาบ": f"{e['start_period']}-{e['end_period']}", "รายการ": f"{e.get('course_code') or ''} {e.get('course_name') or {'homeroom': 'โฮมรูม', 'meeting': 'ประชุม'}.get(e['entry_type'], '')}",
                 "ห้อง": e.get("room_label") or "-", "ครู": ", ".join(e.get("teacher_names") or []), "กลุ่ม": ", ".join(e.get("group_codes") or []),
                 "สถานะ": "มีปัญหา" if e["id"] in vd.conflicts else "ปกติ"} for e in sorted(vd.entries, key=lambda x: (x["day"], x["start_period"]))]
        st.caption("คลิกเลือกแถวเพื่อดูรายละเอียด/ย้าย/ลบรายการ")
        evt = st.dataframe(rows, hide_index=True, on_select="rerun", selection_mode="single-row", key=f"man_tbl_{sel}_{vk}_{key}",
                           column_config={"id": None})
        picked = None
        if evt and evt.selection and evt.selection.rows:
            picked = rows[evt.selection.rows[0]]["id"]
        if picked:
            _edit_panel(ctx, tid, sel, lockv, picked, vr)
        incomplete = [f.message for f in vr.errors if f.incomplete]
        if incomplete:
            with st.expander(f"รายการที่ยังจัดไม่ครบ ({len(incomplete)})"):
                for m in incomplete:
                    st.markdown(f"- {m}")
    with right:
        _add_panel(ctx, tid, sel, lockv)


def _course_info(s, tid, cid):
    c = s.get(Course, cid)
    rate = float(S.get(s, "hour_to_period_rate", tid) or 1)
    return c, P.course_periods(c, rate)


def _add_panel(ctx, tid, tt_id, lockv):
    st.subheader(":material/add_circle: เพิ่มรายการในตาราง")
    ts = None
    with tx() as s:
        ts = S.time_structure(s, tid)
        gopts = options(s, ctx, tid, "group")
    kind = st.radio("ประเภทรายการ", ["class", "homeroom"], format_func={"class": "รายวิชา", "homeroom": "โฮมรูม"}.get, horizontal=True, key="add_kind")
    if not gopts:
        st.info("ไม่มีกลุ่มเรียน")
        return
    gid = st.selectbox("กลุ่มเรียน", [o[0] for o in gopts], format_func=lambda i: dict(gopts)[i], key="add_g")
    cand = {"entry_type": kind, "group_ids": [gid]}
    with tx() as s:
        g = s.get(ClassGroup, gid)
        if kind == "class":
            cids = [x.course_id for x in s.scalars(select(GroupCourse).where(GroupCourse.group_id == gid)).all()]
            courses = {c.id: f"{c.code} {c.name}" for c in s.scalars(select(Course).where(Course.id.in_(cids), or_(Course.course_type.is_(None), Course.course_type != "activity"))).all()} if cids else {}
        advisor = s.get(Teacher, g.advisor_teacher_id) if g.advisor_teacher_id else None
    if kind == "class":
        if not courses:
            st.warning("กลุ่มนี้ยังไม่มีรายวิชาที่ลงทะเบียน")
            return
        cid = st.selectbox("รายวิชา (เฉพาะที่ลงทะเบียน)", list(courses), format_func=courses.get, key="add_c")
        with tx() as s:
            c, parts = _course_info(s, tid, cid)
            joint = next((j for j in s.scalars(select(JointClass).where(JointClass.term_id == tid, JointClass.course_id == cid)).all() if gid in j.group_ids), None)
            groups = sorted(joint.group_ids) if joint else [gid]
            asg = s.scalars(select(Assignment).where(Assignment.term_id == tid, Assignment.course_id == cid, Assignment.group_id == gid)).all()
            tnames = {a.teacher_id: s.get(Teacher, a.teacher_id).name for a in asg}
            gcodes = [s.get(ClassGroup, x).code for x in groups]
            consec = c.consecutive
        if joint:
            st.info(f"วิชานี้กำหนดให้เรียนร่วม: กลุ่ม {', '.join(gcodes)} (สอนครั้งเดียว)")
        part_opts = list(parts) or ["all"]
        part = st.selectbox("ส่วนของรายวิชา", part_opts, format_func=lambda p: f"{PART_TH[p]} ({parts.get(p, '?')} คาบ/สัปดาห์)", key="add_p")
        if not tnames:
            st.error("ไม่สามารถจัดวิชานี้ได้ เนื่องจากยังไม่มีครูได้รับมอบหมาย — แก้ไขการมอบหมายผู้สอนก่อน")
            return
        tid_ = st.selectbox("ครูผู้สอน (เฉพาะที่ได้รับมอบหมาย)", list(tnames), format_func=tnames.get, key="add_t")
        cand.update({"course_id": cid, "part": part, "teacher_ids": [tid_], "group_ids": groups})
        default_len = consec or min(parts.get(part, 1), 4)
    else:
        if not advisor:
            st.error("กลุ่มนี้ไม่มีครูที่ปรึกษา — กำหนดครูที่ปรึกษาก่อน")
            return
        st.caption(f"ครูที่ปรึกษา: {advisor.name}")
        cand.update({"course_id": None, "part": None, "teacher_ids": [advisor.id]})
        default_len = 1
    d1, d2, d3 = st.columns(3)
    day = d1.selectbox("วัน", ts.days, format_func=lambda d: S.DAY_NAMES[d], key="add_d")
    start = d2.selectbox("คาบเริ่ม", ts.teaching_periods + ts.break_periods, key="add_s",
                         format_func=lambda p: f"คาบ {p} ({ts.start_time(p)})" + (" — พัก" if p in ts.break_periods else ""))
    length = d3.number_input("จำนวนคาบต่อเนื่อง", 1, 6, int(default_len), key="add_len")
    with tx() as s:
        rts = {rt.id: rt for rt in s.scalars(select(RoomType)).all()}
        rooms = s.scalars(select(Room).where(Room.active.is_(True)).order_by(Room.code)).all()
        rlabels = {r.id: f"{r.label} · {rts[r.room_type_id].name if r.room_type_id in rts else '?'} · จุ {r.capacity or '?'}" for r in rooms}
    room = st.selectbox("ห้องเรียน", [None] + list(rlabels), format_func=lambda i: "— ไม่ใช้ห้อง —" if i is None else rlabels[i], key="add_r")
    cand.update({"day": day, "start_period": start, "end_period": start + int(length) - 1, "room_id": room})
    reason = st.text_input("เหตุผล/หมายเหตุ (ถ้ามี)", key="add_reason")
    b1, b2 = st.columns(2)
    if b1.button("ตรวจสอบก่อนบันทึก", width="stretch", key="add_chk"):
        with tx() as s:
            conf = T.check_candidate(s, s.get(Timetable, tt_id), cand)
        if conf:
            show_conflicts(conf)
        else:
            st.success("ผ่านข้อจำกัดบังคับ บันทึกได้", icon=":material/check_circle:")
    if b2.button("บันทึกรายการ", icon=":material/save:", type="primary", width="stretch", key="add_save"):
        def go():
            with tx() as s:
                T.add_entry(s, ctx, tt_id, cand, reason, expected_version=lockv)

        if run_safely(go, "บันทึกรายการแล้ว"):
            st.rerun()


def _edit_panel(ctx, tid, tt_id, lockv, entry_id, vr):
    with tx() as s:
        e = next((x for x in T.entries_of(s, tt_id) if x.id == entry_id), None)
        if e is None:
            return
        d = T.entry_dict(e)
        ts = S.time_structure(s, tid)
        rooms = s.scalars(select(Room).where(Room.active.is_(True)).order_by(Room.code)).all()
        rlabels = {r.id: f"{r.label} (จุ {r.capacity or '?'})" for r in rooms}
    st.markdown(f"#### รายการที่เลือก: {d.get('course_code') or ''} {d.get('course_name') or d['entry_type']}")
    st.caption(f"วัน{S.DAY_NAMES[d['day']]} คาบ {d['start_period']}-{d['end_period']} ({d['start_time']}–{d['end_time']}) · ห้อง {d.get('room_label') or '-'} · "
               f"ครู {', '.join(d['teacher_names'])} · กลุ่ม {', '.join(d['group_codes'])} · วิธีจัด: {d['schedule_mode']}")
    probs = [f for f in vr.errors if entry_id in f.entry_ids]
    for f in probs:
        st.markdown(f":red[:material/error:] **{T.FRIENDLY.get(f.check, '')}**  \n{f.message}")
        others = [i for i in f.entry_ids if i != entry_id]
        if others:
            st.caption(f"รายการที่ชนกัน: {', '.join('#' + str(i) for i in others)}")
    c = st.columns(4)
    nd = c[0].selectbox("ย้ายไปวัน", ts.days, index=ts.days.index(d["day"]), format_func=lambda x: S.DAY_NAMES[x], key=f"ed_d_{entry_id}")
    starts = ts.teaching_periods
    ns = c[1].selectbox("คาบเริ่ม", starts, index=starts.index(d["start_period"]) if d["start_period"] in starts else 0, key=f"ed_s_{entry_id}")
    rids = [None] + list(rlabels)
    nr = c[2].selectbox("ห้อง", rids, index=rids.index(d["room_id"]) if d["room_id"] in rids else 0,
                        format_func=lambda i: "— ไม่ใช้ห้อง —" if i is None else rlabels[i], key=f"ed_r_{entry_id}")
    reason = c[3].text_input("เหตุผล", key=f"ed_reason_{entry_id}")
    b = st.columns(3)
    if b[0].button("ตรวจสอบการย้าย", key=f"ed_chk_{entry_id}", width="stretch"):
        cand = {**d, "day": nd, "start_period": ns, "end_period": ns + d["end_period"] - d["start_period"], "room_id": nr}
        with tx() as s:
            conf = T.check_candidate(s, s.get(Timetable, tt_id), cand, replace_id=entry_id)
        show_conflicts(conf) if conf else st.success("ย้ายได้ ไม่ละเมิดข้อจำกัดบังคับ", icon=":material/check_circle:")
    if b[1].button("บันทึกการย้าย", icon=":material/save:", type="primary", key=f"ed_save_{entry_id}", width="stretch"):
        def go():
            with tx() as s:
                T.update_entry(s, ctx, tt_id, entry_id, {"day": nd, "start_period": ns, "room_id": nr}, reason, expected_version=lockv)

        if run_safely(go, "บันทึกการแก้ไขแล้ว"):
            st.rerun()
    if b[2].button("ลบรายการ", icon=":material/delete:", key=f"ed_del_{entry_id}", width="stretch"):
        st.session_state[f"delconf_{entry_id}"] = True
    if st.session_state.get(f"delconf_{entry_id}"):
        st.warning("ยืนยันลบรายการนี้? (ย้อนกลับได้ด้วยปุ่ม “ย้อนกลับการแก้ไขล่าสุด”)")
        if st.button("ยืนยันลบ", key=f"ed_delyes_{entry_id}"):
            st.session_state.pop(f"delconf_{entry_id}", None)

            def go():
                with tx() as s:
                    T.delete_entry(s, ctx, tt_id, entry_id, reason, expected_version=lockv)

            if run_safely(go, "ลบรายการแล้ว"):
                st.rerun()
