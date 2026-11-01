"""หน้าจัดการข้อมูล: ค้นหา กรอง ดู แก้ไข เพิ่ม ลบ (ผู้ดูแลระบบ) — ทุกการบันทึกเป็นธุรกรรมเดียวและบันทึกประวัติ"""
from __future__ import annotations

import pandas as pd
import streamlit as st
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from .. import audit, settings_service as S
from ..models import (
    Assignment,
    ClassGroup,
    Course,
    Department,
    Enrollment,
    GroupCourse,
    JointClass,
    Room,
    RoomType,
    RoomUnavailable,
    Student,
    StudentMembership,
    Teacher,
    TeacherQualification,
    TeacherUnavailable,
)
from ..permissions import ADMIN
from .common import banner, count_caption, df_show, flash, require_ctx, require_term, term_label, tx

TYPE_TH = {"theory": "ทฤษฎี", "practice": "ปฏิบัติ", "mixed": "ทฤษฎีและปฏิบัติ", "activity": "กิจกรรม", None: "(ยังไม่ระบุ)"}
TYPE_EN = {v: k for k, v in TYPE_TH.items()}
PART_TH = {"all": "ทั้งหมด", "theory": "ทฤษฎี", "practice": "ปฏิบัติ"}
PART_EN = {v: k for k, v in PART_TH.items()}


def _nan(v):
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    return v


def _int(v):
    v = _nan(v)
    return None if v is None or v == "" else int(float(v))


def _float(v):
    v = _nan(v)
    return None if v is None or v == "" else float(v)


def _save_table(key: str, before: pd.DataFrame, after: pd.DataFrame, model, to_obj, ctx, entity: str, extra_new=None) -> None:
    """เปรียบเทียบตารางก่อน/หลังแก้ไข แล้วบันทึกเป็นธุรกรรมเดียว"""
    ids_before = set(before["id"].dropna().astype(int)) if not before.empty else set()
    ids_after = set(after["id"].dropna().astype(int)) if "id" in after and not after.empty else set()
    deleted = ids_before - ids_after
    new_rows = after[after["id"].isna()] if "id" in after else after
    changed = []
    b_idx = before.set_index("id") if not before.empty else pd.DataFrame()
    for _, r in after.dropna(subset=["id"]).iterrows():
        rid = int(r["id"])
        if rid in b_idx.index:
            br = b_idx.loc[rid]
            if any(str(_nan(br.get(c))) != str(_nan(r.get(c))) for c in after.columns if c != "id"):
                changed.append(r)
    n_new = sum(1 for _, r in new_rows.iterrows() if not all(_nan(r.get(c)) in (None, "", False) for c in after.columns if c != "id"))
    with st.container(border=True):
        if not (changed or n_new or deleted):
            st.markdown('<div class="nvc-hint" style="margin:0"><span class="mi">edit</span> แก้ไขในตารางด้านบนได้โดยตรง (ดับเบิลคลิกที่ช่อง) · เพิ่มแถวที่ปุ่ม + ท้ายตาราง · '
                        'ลบโดยเลือกแถวแล้วกดไอคอนถังขยะ — ระบบจะแสดงปุ่มบันทึกเมื่อมีการเปลี่ยนแปลง</div>', unsafe_allow_html=True)
            return
        c = st.columns([2.2, 1, 1], vertical_alignment="center")
        c[0].markdown(f'<b>มีการเปลี่ยนแปลงที่ยังไม่บันทึก</b> &nbsp; <span class="badge b-info">แก้ไข {len(changed)}</span> '
                      f'<span class="badge b-ok">เพิ่ม {n_new}</span> <span class="badge b-err">ลบ {len(deleted)}</span>', unsafe_allow_html=True)
        conf = c[0].checkbox("ยืนยันการบันทึกการเปลี่ยนแปลงข้างต้น", key=f"conf_{key}")
        save = c[1].button("บันทึกการเปลี่ยนแปลง", icon=":material/save:", type="primary", key=f"save_{key}", disabled=not conf, width="stretch")
        if c[2].button("ยกเลิกการแก้ไข", icon=":material/undo:", key=f"cancel_{key}", width="stretch"):
            st.session_state.pop(f"ed_{key}", None)
            st.session_state.pop(f"conf_{key}", None)
            st.rerun()
        if not save:
            return

        def _label(r):
            for col in after.columns:
                if col != "id" and _nan(r.get(col)) not in (None, "", False):
                    return str(r.get(col))
            return "แถวใหม่"

        try:
            with st.spinner("กำลังบันทึก…"), tx() as s:
                for rid in deleted:
                    obj = s.get(model, rid)
                    if obj is not None:
                        s.delete(obj)
                s.flush()
                for r in changed:
                    obj = s.get(model, int(r["id"]))
                    try:
                        to_obj(s, obj, r)
                    except (ValueError, KeyError) as e:
                        raise ValueError(f"รายการ “{_label(r)}”: {e}")
                for _, r in new_rows.iterrows():
                    if all(_nan(r.get(c)) in (None, "", False) for c in after.columns if c != "id"):
                        continue
                    obj = model() if extra_new is None else extra_new()
                    try:
                        to_obj(s, obj, r)
                    except (ValueError, KeyError) as e:
                        raise ValueError(f"แถวที่เพิ่มใหม่ “{_label(r)}”: {e}")
                    s.add(obj)
                s.flush()
                audit.log(s, ctx.username, "data_edit", entity, {"changed": len(changed), "added": n_new, "deleted": len(deleted)})
            st.session_state.pop(f"ed_{key}", None)
            st.session_state.pop(f"conf_{key}", None)
            flash(f"บันทึกเรียบร้อย: แก้ไข {len(changed)} · เพิ่ม {n_new} · ลบ {len(deleted)} รายการ")
            st.rerun()
        except IntegrityError:
            st.error("บันทึกไม่สำเร็จ: ข้อมูลซ้ำ หรือรายการที่จะลบยังถูกอ้างอิงโดยข้อมูลอื่น (เช่น การมอบหมาย/ตาราง) — ไม่มีข้อมูลใดถูกเปลี่ยนแปลง")
        except ValueError as e:
            st.error(f"ข้อมูลไม่ถูกต้อง — {e} · ไม่มีข้อมูลใดถูกเปลี่ยนแปลง")


NUMERIC_COLS = {"ชม.ทฤษฎี", "ชม.ปฏิบัติ", "หน่วยกิต", "คาบรวม(ระบุตรง)", "คาบต่อเนื่อง", "ชั่วโมงสอนสูงสุด/สัปดาห์", "สูงสุด/วัน",
                "ความจุ", "จำนวนสมาชิก(ระบุ)", "จำนวนนักเรียนในระบบ", "จำนวนคาบ (ว่าง=เต็มตามส่วน)"}


def _numeric(df: pd.DataFrame) -> pd.DataFrame:
    """ช่องตัวเลขที่ว่างแสดงเป็นช่องว่าง (ไม่ใช่คำว่า None)"""
    for c in df.columns:
        if c in NUMERIC_COLS:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def _toolbar(df: pd.DataFrame, key: str, filters: list[str] | None = None, unit: str = "รายการ") -> pd.DataFrame:
    """แถบค้นหาและตัวกรอง พร้อมจำนวนผลลัพธ์"""
    df = _numeric(df)
    filters = [f for f in (filters or []) if f in df.columns]
    cols = st.columns([2.2] + [1] * len(filters)) if filters else [st.container()]
    q = cols[0].text_input(":material/search: ค้นหา", key=f"q_{key}", placeholder="พิมพ์คำค้น เช่น รหัส ชื่อ แผนก")
    view = df
    for i, f in enumerate(filters, start=1):
        opts = sorted({str(v) for v in df[f].dropna() if str(v) != ""})
        sel = cols[i].selectbox(f"กรอง: {f}", ["ทั้งหมด"] + opts, key=f"f_{key}_{i}")
        if sel != "ทั้งหมด":
            view = view[view[f].astype(str) == sel]
    if q and not view.empty:
        mask = view.astype(str).apply(lambda col: col.str.contains(q, case=False, regex=False)).any(axis=1)
        view = view[mask]
    count_caption(len(view), len(df), unit)
    if df.empty:
        st.info("ยังไม่มีข้อมูลในหมวดนี้ — นำเข้าที่เมนู “นำเข้าข้อมูล” หรือเพิ่มแถวในตารางด้านล่าง")
    elif view.empty:
        st.warning("ไม่พบรายการที่ตรงกับคำค้น/ตัวกรอง")
    return view


def page() -> None:
    ctx = require_ctx(ADMIN)
    tid = require_term()
    banner("จัดการข้อมูล", f"ค้นหา กรอง แก้ไข เพิ่ม และลบข้อมูลหลัก · {term_label(tid)}")
    tabs = st.tabs(["รายวิชา", "ครูผู้สอน", "ห้องเรียน", "กลุ่มเรียน", "นักเรียน", "แผนกวิชา", "การลงทะเบียน",
                    "การมอบหมายครู", "การเรียนร่วม", "ข้อจำกัด/เวลาไม่สะดวก"])
    with tabs[0]:
        _courses(ctx, tid)
    with tabs[1]:
        _teachers(ctx)
    with tabs[2]:
        _rooms(ctx)
    with tabs[3]:
        _groups(ctx, tid)
    with tabs[4]:
        _students(ctx, tid)
    with tabs[5]:
        _departments(ctx)
    with tabs[6]:
        _registrations(ctx, tid)
    with tabs[7]:
        _assignments(ctx, tid)
    with tabs[8]:
        _joints(ctx, tid)
    with tabs[9]:
        _constraints(ctx, tid)


def _dept_maps(s):
    deps = s.scalars(select(Department).order_by(Department.name)).all()
    return {d.id: d.name for d in deps}, {d.name: d.id for d in deps}


def _courses(ctx, tid):
    with tx() as s:
        d_by_id, d_by_name = _dept_maps(s)
        rts = {rt.code: rt.name for rt in s.scalars(select(RoomType)).all()}
        rows = [{"id": c.id, "รหัสวิชา": c.code, "ชื่อวิชา": c.name, "ประเภทวิชา": TYPE_TH.get(c.course_type, "(ยังไม่ระบุ)"),
                 "ยืนยันประเภท": bool(c.type_confirmed), "ชม.ทฤษฎี": c.theory_hours, "ชม.ปฏิบัติ": c.practice_hours,
                 "หน่วยกิต": c.credits, "คาบรวม(ระบุตรง)": c.total_periods, "คาบต่อเนื่อง": c.consecutive,
                 "ประเภทห้องปฏิบัติ": c.practice_room_types or "", "แผนก": d_by_id.get(c.department_id, ""), "ระดับชั้น": c.level or "",
                 "วิชาสามัญ(กำหนดเอง)": "" if c.is_general is None else ("ใช่" if c.is_general else "ไม่ใช่"), "ใช้งาน": bool(c.active)}
                for c in s.scalars(select(Course).where(Course.term_id == tid).order_by(Course.code)).all()]
    before = pd.DataFrame(rows, columns=["id", "รหัสวิชา", "ชื่อวิชา", "ประเภทวิชา", "ยืนยันประเภท", "ชม.ทฤษฎี", "ชม.ปฏิบัติ", "หน่วยกิต", "คาบรวม(ระบุตรง)",
                                         "คาบต่อเนื่อง", "ประเภทห้องปฏิบัติ", "แผนก", "ระดับชั้น", "วิชาสามัญ(กำหนดเอง)", "ใช้งาน"])
    st.caption(f"ประเภทห้องปฏิบัติ: ใส่รหัสประเภทห้อง คั่นด้วย , (รหัสที่มี: {', '.join(f'{k}={v}' for k, v in rts.items())})")
    unconf = before[~before["ยืนยันประเภท"].astype(bool)] if not before.empty else before
    if not unconf.empty:
        st.warning(f"มีรายวิชาที่ต้องยืนยันประเภทวิชา {len(unconf)} รายการ — เลือกประเภทให้ถูกต้องแล้วติ๊ก “ยืนยันประเภท”")
    view = _toolbar(before, "course", ["ประเภทวิชา", "แผนก"])
    after = st.data_editor(view, num_rows="dynamic", hide_index=True, key="ed_course",
                           column_config={"id": None, "ประเภทวิชา": st.column_config.SelectboxColumn(options=list(TYPE_TH.values())),
                                          "แผนก": st.column_config.SelectboxColumn(options=[""] + list(d_by_name)),
                                          "วิชาสามัญ(กำหนดเอง)": st.column_config.SelectboxColumn(options=["", "ใช่", "ไม่ใช่"],
                                                                                              help="เว้นว่าง = ใช้กฎรหัสวิชาขึ้นต้น 20000/30000")})

    def to_obj(s, c: Course, r):
        code = str(_nan(r["รหัสวิชา"]) or "").strip()
        name = str(_nan(r["ชื่อวิชา"]) or "").strip()
        if not code or not name:
            raise ValueError("รายวิชาต้องมีรหัสวิชาและชื่อวิชา")
        rtc = [x.strip() for x in str(_nan(r["ประเภทห้องปฏิบัติ"]) or "").split(",") if x.strip()]
        bad = [x for x in rtc if x not in rts]
        if bad:
            raise ValueError(f"ไม่รู้จักรหัสประเภทห้อง {bad}")
        c.term_id = tid
        c.code, c.name = code, name
        c.course_type = TYPE_EN.get(r["ประเภทวิชา"])
        c.type_confirmed = bool(r["ยืนยันประเภท"]) and c.course_type is not None
        c.theory_hours, c.practice_hours, c.credits = _float(r["ชม.ทฤษฎี"]), _float(r["ชม.ปฏิบัติ"]), _float(r["หน่วยกิต"])
        c.total_periods, c.consecutive = _int(r["คาบรวม(ระบุตรง)"]), _int(r["คาบต่อเนื่อง"])
        c.practice_room_types = ",".join(rtc) or None
        c.department_id = d_by_name.get(_nan(r["แผนก"]) or "")
        c.level = _nan(r["ระดับชั้น"]) or None
        g = _nan(r["วิชาสามัญ(กำหนดเอง)"])
        c.is_general = None if not g else g == "ใช่"
        c.active = bool(r["ใช้งาน"]) if _nan(r["ใช้งาน"]) is not None else True

    _save_table("course", view, after, Course, to_obj, ctx, "course")


def _teachers(ctx):
    with tx() as s:
        d_by_id, d_by_name = _dept_maps(s)
        quals = {}
        for q in s.scalars(select(TeacherQualification)).all():
            quals.setdefault(q.teacher_id, []).append(q.course_code)
        rows = [{"id": t.id, "รหัสครู": t.code, "ชื่อ-นามสกุล": t.name, "แผนก": d_by_id.get(t.department_id, ""), "ตำแหน่ง/หน้าที่": t.position or "",
                 "ชั่วโมงสอนสูงสุด/สัปดาห์": t.max_hours_week, "สูงสุด/วัน": t.max_hours_day, "เข้าประชุมหัวหน้า": bool(t.is_meeting_member),
                 "รายวิชาที่สอนได้": ", ".join(sorted(quals.get(t.id, []))), "ใช้งาน": bool(t.active)}
                for t in s.scalars(select(Teacher).order_by(Teacher.code)).all()]
    before = pd.DataFrame(rows, columns=["id", "รหัสครู", "ชื่อ-นามสกุล", "แผนก", "ตำแหน่ง/หน้าที่", "ชั่วโมงสอนสูงสุด/สัปดาห์", "สูงสุด/วัน",
                                         "เข้าประชุมหัวหน้า", "รายวิชาที่สอนได้", "ใช้งาน"])
    st.caption("ชั่วโมงสอนสูงสุดแยกจากจำนวนชั่วโมงสอนจริง (ดูชั่วโมงสอนจริงได้ที่หัวตารางสอนของครู) · การแก้ไขชั่วโมงสอนสูงสุดถูกบันทึกในประวัติ")
    view = _toolbar(before, "teacher", ["แผนก"], "คน")
    after = st.data_editor(view, num_rows="dynamic", hide_index=True, key="ed_teacher",
                           column_config={"id": None, "แผนก": st.column_config.SelectboxColumn(options=[""] + list(d_by_name))})

    def to_obj(s, t: Teacher, r):
        code = str(_nan(r["รหัสครู"]) or "").strip()
        name = str(_nan(r["ชื่อ-นามสกุล"]) or "").strip()
        if not code or not name:
            raise ValueError("ครูต้องมีรหัสและชื่อ")
        t.code, t.name = code, name
        t.department_id = d_by_name.get(_nan(r["แผนก"]) or "")
        t.position = _nan(r["ตำแหน่ง/หน้าที่"]) or None
        t.max_hours_week, t.max_hours_day = _int(r["ชั่วโมงสอนสูงสุด/สัปดาห์"]), _int(r["สูงสุด/วัน"])
        t.is_meeting_member = bool(_nan(r["เข้าประชุมหัวหน้า"]))
        t.active = bool(r["ใช้งาน"]) if _nan(r["ใช้งาน"]) is not None else True
        s.add(t)
        s.flush()
        want = {x.strip() for x in str(_nan(r["รายวิชาที่สอนได้"]) or "").split(",") if x.strip()}
        have = {q.course_code: q for q in s.scalars(select(TeacherQualification).where(TeacherQualification.teacher_id == t.id)).all()}
        for cc, q in have.items():
            if cc not in want:
                s.delete(q)
        for cc in want - set(have):
            s.add(TeacherQualification(teacher_id=t.id, course_code=cc))

    _save_table("teacher", view, after, Teacher, to_obj, ctx, "teacher")


def _rooms(ctx):
    with tx() as s:
        d_by_id, d_by_name = _dept_maps(s)
        rts = s.scalars(select(RoomType).order_by(RoomType.id)).all()
        rt_by_id = {rt.id: rt.name for rt in rts}
        rt_by_name = {rt.name: rt.id for rt in rts}
        rows = [{"id": r.id, "รหัสห้อง": r.code, "หมายเลขห้อง": r.number or "", "ชื่อห้อง": r.name or "", "ประเภทห้อง": rt_by_id.get(r.room_type_id, ""),
                 "ความจุ": r.capacity, "แผนกที่ดูแล": d_by_id.get(r.department_id, ""), "อุปกรณ์/ทรัพยากรพิเศษ": r.equipment or "", "ใช้งาน": bool(r.active)}
                for r in s.scalars(select(Room).order_by(Room.code)).all()]
        rt_rows = [{"id": rt.id, "รหัสประเภท": rt.code, "ชื่อประเภทห้อง": rt.name, "ลักษณะ": {"theory": "ทฤษฎี", "practice": "ปฏิบัติ", "activity": "กิจกรรม", "other": "อื่น ๆ"}[rt.kind]} for rt in rts]
    before = pd.DataFrame(rows, columns=["id", "รหัสห้อง", "หมายเลขห้อง", "ชื่อห้อง", "ประเภทห้อง", "ความจุ", "แผนกที่ดูแล", "อุปกรณ์/ทรัพยากรพิเศษ", "ใช้งาน"])
    view = _toolbar(before, "room", ["ประเภทห้อง", "แผนกที่ดูแล"], "ห้อง")
    after = st.data_editor(view, num_rows="dynamic", hide_index=True, key="ed_room",
                           column_config={"id": None, "ประเภทห้อง": st.column_config.SelectboxColumn(options=list(rt_by_name)),
                                          "แผนกที่ดูแล": st.column_config.SelectboxColumn(options=[""] + list(d_by_name))})

    def to_obj(s, r: Room, x):
        code = str(_nan(x["รหัสห้อง"]) or "").strip()
        if not code:
            raise ValueError("ห้องต้องมีรหัสห้อง")
        r.code = code
        r.number, r.name = _nan(x["หมายเลขห้อง"]) or None, _nan(x["ชื่อห้อง"]) or None
        r.room_type_id = rt_by_name.get(_nan(x["ประเภทห้อง"]) or "")
        r.capacity = _int(x["ความจุ"])
        r.department_id = d_by_name.get(_nan(x["แผนกที่ดูแล"]) or "")
        r.equipment = _nan(x["อุปกรณ์/ทรัพยากรพิเศษ"]) or None
        r.active = bool(x["ใช้งาน"]) if _nan(x["ใช้งาน"]) is not None else True

    _save_table("room", view, after, Room, to_obj, ctx, "room")
    st.markdown("##### ประเภทห้อง (เพิ่มประเภทใหม่ได้)")
    kinds = {"ทฤษฎี": "theory", "ปฏิบัติ": "practice", "กิจกรรม": "activity", "อื่น ๆ": "other"}
    rbefore = pd.DataFrame(rt_rows, columns=["id", "รหัสประเภท", "ชื่อประเภทห้อง", "ลักษณะ"])
    rafter = st.data_editor(rbefore, num_rows="dynamic", hide_index=True, key="ed_rt",
                            column_config={"id": None, "ลักษณะ": st.column_config.SelectboxColumn(options=list(kinds), required=True,
                                                                                                help="ทฤษฎี = ใช้กับวิชาทฤษฎี, ปฏิบัติ = ใช้กับวิชาปฏิบัติ")})

    def rt_obj(s, rt: RoomType, x):
        if not _nan(x["รหัสประเภท"]) or not _nan(x["ชื่อประเภทห้อง"]):
            raise ValueError("ต้องระบุรหัสและชื่อประเภทห้อง")
        rt.code, rt.name, rt.kind = str(x["รหัสประเภท"]).strip(), str(x["ชื่อประเภทห้อง"]).strip(), kinds.get(x["ลักษณะ"], "other")

    _save_table("rt", rbefore, rafter, RoomType, rt_obj, ctx, "room_type")


def _groups(ctx, tid):
    with tx() as s:
        d_by_id, d_by_name = _dept_maps(s)
        teachers = s.scalars(select(Teacher).order_by(Teacher.name)).all()
        t_by_id = {t.id: f"{t.code} {t.name}" for t in teachers}
        t_by_lbl = {v: k for k, v in t_by_id.items()}
        cnt = {}
        for m in s.scalars(select(StudentMembership).where(StudentMembership.term_id == tid)).all():
            cnt[m.group_id] = cnt.get(m.group_id, 0) + 1
        rows = [{"id": g.id, "รหัสกลุ่มเรียน": g.code, "ชื่อกลุ่มเรียน": g.name or "", "แผนก": d_by_id.get(g.department_id, ""), "ระดับชั้น": g.level or "",
                 "ครูที่ปรึกษา": t_by_id.get(g.advisor_teacher_id, ""), "จำนวนสมาชิก(ระบุ)": g.size, "จำนวนนักเรียนในระบบ": cnt.get(g.id, 0)}
                for g in s.scalars(select(ClassGroup).where(ClassGroup.term_id == tid).order_by(ClassGroup.code)).all()]
    before = pd.DataFrame(rows, columns=["id", "รหัสกลุ่มเรียน", "ชื่อกลุ่มเรียน", "แผนก", "ระดับชั้น", "ครูที่ปรึกษา", "จำนวนสมาชิก(ระบุ)", "จำนวนนักเรียนในระบบ"])
    view = _toolbar(before, "group", ["แผนก", "ระดับชั้น"], "กลุ่ม")
    after = st.data_editor(view, num_rows="dynamic", hide_index=True, key="ed_group", disabled=["จำนวนนักเรียนในระบบ"],
                           column_config={"id": None, "แผนก": st.column_config.SelectboxColumn(options=[""] + list(d_by_name)),
                                          "ครูที่ปรึกษา": st.column_config.SelectboxColumn(options=[""] + list(t_by_lbl))})

    def to_obj(s, g: ClassGroup, x):
        code = str(_nan(x["รหัสกลุ่มเรียน"]) or "").strip()
        if not code:
            raise ValueError("ต้องระบุรหัสกลุ่มเรียน")
        g.term_id, g.code = tid, code
        g.name = _nan(x["ชื่อกลุ่มเรียน"]) or None
        g.department_id = d_by_name.get(_nan(x["แผนก"]) or "")
        g.level = _nan(x["ระดับชั้น"]) or None
        g.advisor_teacher_id = t_by_lbl.get(_nan(x["ครูที่ปรึกษา"]) or "")
        g.size = _int(x["จำนวนสมาชิก(ระบุ)"])

    _save_table("group", view, after, ClassGroup, to_obj, ctx, "group")


def _students(ctx, tid):
    with tx() as s:
        groups = s.scalars(select(ClassGroup).where(ClassGroup.term_id == tid).order_by(ClassGroup.code)).all()
        g_by_id = {g.id: g.code for g in groups}
        g_by_code = {g.code: g.id for g in groups}
        mem = {m.student_id: m for m in s.scalars(select(StudentMembership).where(StudentMembership.term_id == tid)).all()}
        rows = [{"id": st_.id, "รหัสนักเรียน": st_.code, "ชื่อ-นามสกุล": st_.name, "กลุ่มเรียน (ภาคเรียนนี้)": g_by_id.get(mem[st_.id].group_id, "") if st_.id in mem else "",
                 "สถานะการศึกษา": st_.status or ""} for st_ in s.scalars(select(Student).order_by(Student.code)).all()]
    before = pd.DataFrame(rows, columns=["id", "รหัสนักเรียน", "ชื่อ-นามสกุล", "กลุ่มเรียน (ภาคเรียนนี้)", "สถานะการศึกษา"])
    view = _toolbar(before, "student", ["กลุ่มเรียน (ภาคเรียนนี้)", "สถานะการศึกษา"], "คน")
    after = st.data_editor(view, num_rows="dynamic", hide_index=True, key="ed_student",
                           column_config={"id": None, "กลุ่มเรียน (ภาคเรียนนี้)": st.column_config.SelectboxColumn(options=[""] + list(g_by_code))})

    def to_obj(s, x: Student, r):
        code = str(_nan(r["รหัสนักเรียน"]) or "").strip()
        name = str(_nan(r["ชื่อ-นามสกุล"]) or "").strip()
        if not code or not name:
            raise ValueError("นักเรียนต้องมีรหัสและชื่อ")
        x.code, x.name, x.status = code, name, _nan(r["สถานะการศึกษา"]) or None
        s.add(x)
        s.flush()
        gid = g_by_code.get(_nan(r["กลุ่มเรียน (ภาคเรียนนี้)"]) or "")
        m = s.scalars(select(StudentMembership).where(StudentMembership.term_id == tid, StudentMembership.student_id == x.id)).first()
        if gid and m is None:
            s.add(StudentMembership(term_id=tid, student_id=x.id, group_id=gid))
        elif gid and m:
            m.group_id = gid
        elif not gid and m:
            s.delete(m)

    _save_table("student", view, after, Student, to_obj, ctx, "student")
    st.caption("การลบนักเรียนที่มีการลงทะเบียนรายบุคคลอยู่ ต้องลบการลงทะเบียนก่อน")


def _departments(ctx):
    with tx() as s:
        rows = [{"id": d.id, "รหัสแผนก": d.code, "ชื่อแผนก": d.name} for d in s.scalars(select(Department).order_by(Department.code)).all()]
    before = pd.DataFrame(rows, columns=["id", "รหัสแผนก", "ชื่อแผนก"])
    after = st.data_editor(before, num_rows="dynamic", hide_index=True, key="ed_dept", column_config={"id": None})

    def to_obj(s, d: Department, r):
        if not _nan(r["รหัสแผนก"]) or not _nan(r["ชื่อแผนก"]):
            raise ValueError("ต้องระบุรหัสและชื่อแผนก")
        d.code, d.name = str(r["รหัสแผนก"]).strip(), str(r["ชื่อแผนก"]).strip()

    _save_table("dept", before, after, Department, to_obj, ctx, "department")


def _registrations(ctx, tid):
    with tx() as s:
        groups = s.scalars(select(ClassGroup).where(ClassGroup.term_id == tid).order_by(ClassGroup.code)).all()
        courses = s.scalars(select(Course).where(Course.term_id == tid).order_by(Course.code)).all()
        c_lbl = {c.id: f"{c.code} {c.name}" for c in courses}
        if not groups:
            st.info("ยังไม่มีกลุ่มเรียน")
            return
        gsel = st.selectbox("กลุ่มเรียน", [g.id for g in groups], format_func=lambda i: next(g.code for g in groups if g.id == i), key="reg_g")
        have = [gc.course_id for gc in s.scalars(select(GroupCourse).where(GroupCourse.group_id == gsel)).all()]
        en_rows = []
        for e, stu in s.execute(select(Enrollment, Student).join(Student, Student.id == Enrollment.student_id).where(Enrollment.term_id == tid)).all():
            en_rows.append({"รหัสนักเรียน": stu.code, "ชื่อ": stu.name, "รายวิชา": c_lbl.get(e.course_id, ""), "กลุ่มที่เข้าเรียน": next((g.code for g in groups if g.id == e.section_group_id), "(กลุ่มของตน)")})
    new = st.multiselect("รายวิชาที่กลุ่มนี้ลงทะเบียน", list(c_lbl), default=have, format_func=lambda i: c_lbl[i], key=f"reg_ms_{gsel}")
    if set(new) != set(have):
        st.caption(f"เพิ่ม {len(set(new) - set(have))} · นำออก {len(set(have) - set(new))} รายวิชา")
        if st.button("บันทึกการลงทะเบียน", icon=":material/save:", type="primary", key="reg_save"):
            with tx() as s:
                for cid in set(have) - set(new):
                    x = s.scalars(select(GroupCourse).where(GroupCourse.group_id == gsel, GroupCourse.course_id == cid)).first()
                    if x:
                        s.delete(x)
                for cid in set(new) - set(have):
                    s.add(GroupCourse(term_id=tid, group_id=gsel, course_id=cid))
                audit.log(s, ctx.username, "edit_registration", "group_course", {"group": gsel})
            flash("บันทึกแล้ว")
            st.rerun()
    st.markdown("##### การลงทะเบียนรายบุคคล (นำเข้าจากไฟล์)")
    df_show(en_rows, height=240)


def _assignments(ctx, tid):
    with tx() as s:
        groups = {g.id: g.code for g in s.scalars(select(ClassGroup).where(ClassGroup.term_id == tid)).all()}
        courses = {c.id: f"{c.code} {c.name}" for c in s.scalars(select(Course).where(Course.term_id == tid)).all()}
        teachers = {t.id: f"{t.code} {t.name}" for t in s.scalars(select(Teacher).order_by(Teacher.code)).all()}
        rows = [{"id": a.id, "ครู": teachers.get(a.teacher_id, ""), "รายวิชา": courses.get(a.course_id, ""), "กลุ่มเรียน": groups.get(a.group_id, ""),
                 "ส่วนที่สอน": PART_TH.get(a.part, "ทั้งหมด"), "จำนวนคาบ (ว่าง=เต็มตามส่วน)": a.periods}
                for a in s.scalars(select(Assignment).where(Assignment.term_id == tid)).all()]
    inv_g, inv_c, inv_t = ({v: k for k, v in d.items()} for d in (groups, courses, teachers))
    before = pd.DataFrame(rows, columns=["id", "ครู", "รายวิชา", "กลุ่มเรียน", "ส่วนที่สอน", "จำนวนคาบ (ว่าง=เต็มตามส่วน)"]).sort_values(["กลุ่มเรียน", "รายวิชา"]) if rows else \
        pd.DataFrame(columns=["id", "ครู", "รายวิชา", "กลุ่มเรียน", "ส่วนที่สอน", "จำนวนคาบ (ว่าง=เต็มตามส่วน)"])
    st.caption("ระบบจะไม่สมมติว่าครูสอนได้ทุกวิชา — ครูจะถูกจัดเฉพาะรายวิชาที่มอบหมายที่นี่")
    view = _toolbar(before, "asg", ["กลุ่มเรียน", "ส่วนที่สอน"])
    after = st.data_editor(view, num_rows="dynamic", hide_index=True, key="ed_asg",
                           column_config={"id": None, "ครู": st.column_config.SelectboxColumn(options=list(inv_t), required=True),
                                          "รายวิชา": st.column_config.SelectboxColumn(options=list(inv_c), required=True),
                                          "กลุ่มเรียน": st.column_config.SelectboxColumn(options=list(inv_g), required=True),
                                          "ส่วนที่สอน": st.column_config.SelectboxColumn(options=list(PART_EN), required=True)})

    def to_obj(s, a: Assignment, r):
        if r["ครู"] not in inv_t or r["รายวิชา"] not in inv_c or r["กลุ่มเรียน"] not in inv_g:
            raise ValueError("ต้องเลือกครู รายวิชา และกลุ่มเรียน")
        a.term_id = tid
        a.teacher_id, a.course_id, a.group_id = inv_t[r["ครู"]], inv_c[r["รายวิชา"]], inv_g[r["กลุ่มเรียน"]]
        a.part = PART_EN.get(r["ส่วนที่สอน"], "all")
        a.periods = _int(r["จำนวนคาบ (ว่าง=เต็มตามส่วน)"])

    _save_table("asg", view, after, Assignment, to_obj, ctx, "assignment")


def _joints(ctx, tid):
    with tx() as s:
        groups = {g.id: g.code for g in s.scalars(select(ClassGroup).where(ClassGroup.term_id == tid).order_by(ClassGroup.code)).all()}
        courses = {c.id: c for c in s.scalars(select(Course).where(Course.term_id == tid).order_by(Course.code)).all()}
        teachers = {t.id: t.name for t in s.scalars(select(Teacher)).all()}
        joints = s.scalars(select(JointClass).where(JointClass.term_id == tid)).all()
        rows = [{"รหัส": j.id, "รายวิชา": f"{courses[j.course_id].code} {courses[j.course_id].name}" if j.course_id in courses else j.course_id,
                 "กลุ่มที่เรียนร่วม": ", ".join(groups.get(g, str(g)) for g in j.group_ids), "ครู": teachers.get(j.teacher_id, "-"), "หมายเหตุ": j.note or ""} for j in joints]
        prefixes = S.get(s, "general_prefixes", tid)
        maxg = int(S.get(s, "general_joint_max_groups", tid) or 2)
        # ข้อเสนอการเรียนร่วมวิชาสามัญ: รหัสวิชาเดียวกัน ครูคนเดียวกัน (ต้องยืนยันก่อนใช้)
        joined = {(j.course_id, g) for j in joints for g in j.group_ids}
        by = {}
        for a in s.scalars(select(Assignment).where(Assignment.term_id == tid)).all():
            c = courses.get(a.course_id)
            if c and S.is_general_course(c.code, prefixes, c.is_general) and (a.course_id, a.group_id) not in joined:
                by.setdefault((a.course_id, a.teacher_id), []).append(a.group_id)
        sugg = []
        for (cid, t), gs in by.items():
            gs = sorted(set(gs), key=lambda g: groups.get(g, ""))
            for i in range(0, len(gs) - 1, maxg):
                chunk = gs[i:i + maxg]
                if len(chunk) >= 2:
                    sugg.append((cid, t, chunk))
    st.caption("การเรียนร่วมจะเกิดขึ้นเฉพาะรายการที่กำหนดไว้ที่นี่เท่านั้น — ระบบไม่สรุปเองจากรหัสวิชาที่เหมือนกัน")
    df_show(rows)
    if rows:
        did = st.selectbox("ลบการเรียนร่วม", [None] + [r["รหัส"] for r in rows], format_func=lambda x: "— เลือก —" if x is None else f"#{x}", key="j_del")
        if did and st.button("ลบรายการนี้", key="j_del_btn"):
            with tx() as s:
                s.delete(s.get(JointClass, did))
                audit.log(s, ctx.username, "delete_joint", "joint", {"id": did})
            st.rerun()
    if sugg:
        st.markdown(f"##### ข้อเสนอการเรียนร่วมวิชาสามัญ (รหัสขึ้นต้น {', '.join(prefixes)} · ครูคนเดียวกัน · ไม่เกิน {maxg} กลุ่ม)")
        st.caption("ตรวจสอบความจุห้องและข้อกำหนดรายวิชาก่อนยืนยัน — ติ๊กเฉพาะรายการที่ต้องการ")
        picks = []
        for i, (cid, t, gs) in enumerate(sugg):
            if st.checkbox(f"{courses[cid].code} {courses[cid].name} · {teachers.get(t, '')} · กลุ่ม {' + '.join(groups[g] for g in gs)}", key=f"sg_{i}"):
                picks.append((cid, t, gs))
        if picks and st.button(f"ยืนยันสร้างการเรียนร่วม {len(picks)} รายการ", icon=":material/check_circle:", type="primary"):
            with tx() as s:
                for cid, t, gs in picks:
                    s.add(JointClass(term_id=tid, course_id=cid, teacher_id=t, group_ids=gs, note="ยืนยันจากข้อเสนอวิชาสามัญ"))
                audit.log(s, ctx.username, "create_joint", "joint", {"count": len(picks)})
            st.rerun()
    with st.expander("เพิ่มการเรียนร่วมเอง", icon=":material/add:"):
        cid = st.selectbox("รายวิชา", list(courses), format_func=lambda i: f"{courses[i].code} {courses[i].name}", key="j_c")
        gs = st.multiselect("กลุ่มที่เรียนร่วม (อย่างน้อย 2)", list(groups), format_func=lambda i: groups[i], key="j_g")
        t = st.selectbox("ครูผู้สอน (ถ้ามี)", [None] + list(teachers), format_func=lambda i: "-" if i is None else teachers[i], key="j_t")
        if st.button("บันทึกการเรียนร่วม", key="j_add") and cid:
            if len(gs) < 2:
                st.error("ต้องเลือกอย่างน้อย 2 กลุ่ม")
            else:
                with tx() as s:
                    s.add(JointClass(term_id=tid, course_id=cid, teacher_id=t, group_ids=sorted(gs), note="เพิ่มเอง"))
                    audit.log(s, ctx.username, "create_joint", "joint", {"course": cid})
                st.rerun()


def _constraints(ctx, tid):
    from ..settings_service import DAY_NAMES, time_structure

    with tx() as s:
        ts = time_structure(s, tid)
        teachers = s.scalars(select(Teacher).where(Teacher.active.is_(True)).order_by(Teacher.name)).all()
        rooms = s.scalars(select(Room).where(Room.active.is_(True)).order_by(Room.code)).all()
    kind = st.radio("กำหนดข้อจำกัดของ", ["ครู", "ห้องเรียน"], horizontal=True, key="cons_kind")
    if kind == "ครู":
        if not teachers:
            st.info("ยังไม่มีข้อมูลครู")
            return
        tsel = st.selectbox("ครู", [t.id for t in teachers], format_func=lambda i: next(f"{t.code} {t.name}" for t in teachers if t.id == i), key="cons_t")
        mode = st.radio("ประเภท", ["ไม่สะดวก (ข้อจำกัดบังคับ)", "ต้องการสอน (ข้อจำกัดเพื่อคุณภาพ)"], horizontal=True, key="cons_mode")
        pref = mode.startswith("ต้องการ")
        with tx() as s:
            have = {(u.day, u.period) for u in s.scalars(select(TeacherUnavailable).where(TeacherUnavailable.term_id == tid, TeacherUnavailable.teacher_id == tsel, TeacherUnavailable.preferred.is_(pref))).all()}
    else:
        if not rooms:
            st.info("ยังไม่มีข้อมูลห้อง")
            return
        tsel = st.selectbox("ห้องเรียน", [r.id for r in rooms], format_func=lambda i: next(f"{r.label} {r.name or ''}" for r in rooms if r.id == i), key="cons_r")
        pref = False
        with tx() as s:
            have = {(u.day, u.period) for u in s.scalars(select(RoomUnavailable).where(RoomUnavailable.term_id == tid, RoomUnavailable.room_id == tsel)).all()}
    cols = ts.teaching_periods
    df = pd.DataFrame([[((d, p) in have) for p in cols] for d in ts.days], columns=[f"คาบ {p}" for p in cols], index=[DAY_NAMES[d] for d in ts.days])
    st.caption("ติ๊กช่องเวลาที่ " + ("ครูไม่สะดวก" if kind == "ครู" and not pref else ("ครูต้องการสอน" if kind == "ครู" else "ห้องไม่พร้อมใช้งาน")) + " (คาบพักไม่แสดงเพราะจัดไม่ได้อยู่แล้ว)")
    ed = st.data_editor(df, key=f"cons_ed_{kind}_{tsel}_{pref}")
    new = {(ts.days[i], cols[j]) for i in range(len(ts.days)) for j in range(len(cols)) if bool(ed.iat[i, j])}
    if new != have and st.button("บันทึกข้อจำกัด", icon=":material/save:", type="primary", key="cons_save"):
        with tx() as s:
            if kind == "ครู":
                for u in s.scalars(select(TeacherUnavailable).where(TeacherUnavailable.term_id == tid, TeacherUnavailable.teacher_id == tsel, TeacherUnavailable.preferred.is_(pref))).all():
                    s.delete(u)
                s.flush()
                for d, p in new:
                    s.add(TeacherUnavailable(term_id=tid, teacher_id=tsel, day=d, period=p, preferred=pref))
            else:
                for u in s.scalars(select(RoomUnavailable).where(RoomUnavailable.term_id == tid, RoomUnavailable.room_id == tsel)).all():
                    s.delete(u)
                s.flush()
                for d, p in new:
                    s.add(RoomUnavailable(term_id=tid, room_id=tsel, day=d, period=p))
            audit.log(s, ctx.username, "edit_constraints", kind, {"id": tsel, "count": len(new)})
        flash("บันทึกข้อจำกัดแล้ว")
        st.rerun()
