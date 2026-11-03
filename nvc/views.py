"""ดึงข้อมูลตารางตามมุมมอง (ครู/ห้อง/กลุ่ม/นักเรียน/รายวิชา/แผนก/วิทยาลัย/กิจกรรม/ประชุม/โฮมรูม)
พร้อมตรวจสิทธิ์และขอบเขตแผนกในชั้นข้อมูล
"""
from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import settings_service as S
from .models import ClassGroup, Course, Department, Enrollment, Room, Student, StudentMembership, Teacher, Term, Timetable
from .permissions import ADMIN, DEPT_ADMIN, STUDENT, TEACHER, Ctx, PermissionDenied
from .timetable_service import entries_of, entry_dict

VIEW_KINDS = {
    "group": "ตารางเรียนแยกตามกลุ่มเรียน",
    "teacher": "ตารางสอนแยกตามครู",
    "student": "ตารางเรียนรายบุคคล",
    "room": "ตารางใช้ห้องเรียน",
    "course": "ตารางรายวิชา",
    "department": "ตารางสอนแยกตามแผนก",
    "college": "ตารางสอนรวมของวิทยาลัย",
    "activity": "ตารางกิจกรรม",
    "meeting": "ตารางประชุม",
    "homeroom": "ตารางโฮมรูม",
}

# ข้อมูลที่แสดงในช่องตารางตามมุมมอง
CELL_FIELDS = {
    "group": ["code", "name", "room", "teacher"],
    "student": ["code", "name", "room", "teacher"],
    "teacher": ["code", "name", "room", "group"],
    "room": ["code", "name", "teacher", "group"],
    "course": ["code", "group", "room", "teacher"],
    "department": ["code", "name", "room", "teacher", "group"],
    "college": ["code", "name", "room", "teacher", "group"],
    "activity": ["code", "name", "teacher", "group"],
    "meeting": ["name", "teacher"],
    "homeroom": ["name", "teacher", "group"],
}


@dataclass
class ViewData:
    kind: str
    title: str
    header: list[tuple[str, str]]  # (ป้าย, ค่า) สำหรับส่วนหัวเอกสาร
    entries: list[dict]
    term_label: str
    timetable_label: str
    fields: list[str]
    ts: S.TimeStructure
    sub_views: list["ViewData"] = field(default_factory=list)  # ใช้กับตารางรวม (แยกตามกลุ่ม)
    conflicts: set[int] = field(default_factory=set)


def visible_departments(s: Session, ctx: Ctx) -> list[Department]:
    q = select(Department).order_by(Department.name)
    rows = list(s.scalars(q).all())
    if ctx.role == DEPT_ADMIN:
        return [d for d in rows if d.id in (ctx.dept_scope or [])]
    return rows


def options(s: Session, ctx: Ctx, term_id: int, kind: str) -> list[tuple[int, str]]:
    """รายการตัวเลือกสำหรับมุมมองที่ผู้ใช้มีสิทธิ์"""
    scope = ctx.dept_scope if ctx.role == DEPT_ADMIN else None
    if kind == "teacher":
        rows = s.scalars(select(Teacher).where(Teacher.active.is_(True)).order_by(Teacher.name)).all()
        if scope is not None:
            rows = [t for t in rows if t.department_id in scope]
        if ctx.role == STUDENT:
            return []
        return [(t.id, f"{t.name} ({t.code})") for t in rows]
    if kind == "group":
        rows = s.scalars(select(ClassGroup).where(ClassGroup.term_id == term_id).order_by(ClassGroup.code)).all()
        if scope is not None:
            rows = [g for g in rows if g.department_id in scope]
        if ctx.role == STUDENT:
            m = s.scalars(select(StudentMembership).where(StudentMembership.term_id == term_id, StudentMembership.student_id == ctx.student_id)).first()
            rows = [g for g in rows if m and g.id == m.group_id]
        return [(g.id, f"{g.code} {g.name or ''}".strip()) for g in rows]
    if kind == "room":
        if ctx.role == STUDENT:
            return []
        rows = s.scalars(select(Room).where(Room.active.is_(True)).order_by(Room.code)).all()
        if scope is not None:
            rows = [r for r in rows if r.department_id in scope or r.department_id is None]
        return [(r.id, f"{r.label} {r.name or ''}".strip()) for r in rows]
    if kind == "course":
        rows = s.scalars(select(Course).where(Course.term_id == term_id).order_by(Course.code)).all()
        if scope is not None:
            rows = [c for c in rows if c.department_id in scope]
        if ctx.role == STUDENT:
            return []
        return [(c.id, f"{c.code} {c.name}") for c in rows]
    if kind == "student":
        if ctx.role == STUDENT:
            st = s.get(Student, ctx.student_id)
            return [(st.id, f"{st.code} {st.name}")] if st else []
        if ctx.role == TEACHER:
            return []
        q = select(Student, StudentMembership).join(StudentMembership, StudentMembership.student_id == Student.id).where(StudentMembership.term_id == term_id)
        out = []
        for st, m in s.execute(q).all():
            if scope is not None:
                g = s.get(ClassGroup, m.group_id)
                if not g or g.department_id not in scope:
                    continue
            out.append((st.id, f"{st.code} {st.name}"))
        return sorted(out, key=lambda x: x[1])
    if kind == "department":
        if ctx.role == STUDENT:
            return []
        return [(d.id, d.name) for d in visible_departments(s, ctx)]
    return []


def allowed_kinds(ctx: Ctx) -> list[str]:
    if ctx.role == STUDENT:
        return ["student", "group"]
    if ctx.role == TEACHER:
        return ["teacher", "group", "room", "course", "activity", "meeting", "homeroom"]
    if ctx.role == DEPT_ADMIN:
        return ["department", "teacher", "group", "student", "room", "course", "activity", "homeroom", "meeting"]
    return list(VIEW_KINDS)


def _scope_filter(s: Session, ctx: Ctx, term_id: int, entries: list[dict]) -> list[dict]:
    """ฝ่ายวิชาการประจำแผนก: เห็นเฉพาะรายการที่เกี่ยวกับกลุ่มเรียน/ครูในแผนกตน"""
    if ctx.role != DEPT_ADMIN:
        return entries
    scope = set(ctx.dept_scope or [])
    gdept = {g.id: g.department_id for g in s.scalars(select(ClassGroup).where(ClassGroup.term_id == term_id)).all()}
    tdept = {t.id: t.department_id for t in s.scalars(select(Teacher)).all()}
    out = []
    for e in entries:
        if any(gdept.get(g) in scope for g in e["group_ids"]) or any(tdept.get(t) in scope for t in e["teacher_ids"]):
            out.append(e)
    return out


def student_entries(s: Session, term_id: int, student_id: int, entries: list[dict]) -> list[dict]:
    """ตารางรายบุคคล: อ้างอิงการลงทะเบียนจริง (รวมรายวิชานอกกลุ่มหลัก)"""
    m = s.scalars(select(StudentMembership).where(StudentMembership.term_id == term_id, StudentMembership.student_id == student_id)).first()
    g0 = m.group_id if m else None
    en = s.scalars(select(Enrollment).where(Enrollment.term_id == term_id, Enrollment.student_id == student_id)).all()
    out = []
    for e in entries:
        if e["entry_type"] in ("homeroom", "activity"):
            if g0 in e["group_ids"]:
                out.append(e)
            continue
        if e["entry_type"] != "class":
            continue
        if en:
            if any(x.course_id == e["course_id"] and (x.section_group_id or g0) in e["group_ids"] for x in en):
                out.append(e)
        elif g0 in e["group_ids"]:
            out.append(e)
    return out


def _enrich(s: Session, entries: list[dict]) -> list[dict]:
    """เพิ่มรหัสครู รหัสห้อง ชื่อกลุ่มเรียน สำหรับการส่งออก CSV"""
    tcode = {t.id: t.code for t in s.scalars(select(Teacher)).all()}
    rcode = {r.id: r.code for r in s.scalars(select(Room)).all()}
    gname: dict[int, str] = {}
    for e in entries:
        for g in e["group_ids"]:
            if g not in gname:
                go = s.get(ClassGroup, g)
                gname[g] = (go.name or go.code) if go else str(g)
        e["teacher_codes"] = [tcode.get(t, "") for t in e["teacher_ids"]]
        e["room_code"] = rcode.get(e["room_id"], "") if e["room_id"] else ""
        e["group_names"] = [gname[g] for g in e["group_ids"]]
    return entries


def detect_conflicts(entries: list[dict], key: str) -> set[int]:
    """หารายการที่ทับเวลากันภายในมุมมองเดียว (ใช้เน้นสีแดง)"""
    bad = set()
    for i, a in enumerate(entries):
        for b in entries[i + 1:]:
            if a["day"] == b["day"] and a["start_period"] <= b["end_period"] and b["start_period"] <= a["end_period"]:
                bad.add(a["id"])
                bad.add(b["id"])
    return bad


def get_view(s: Session, ctx: Ctx, tt: Timetable, kind: str, key: int | None = None) -> ViewData:
    if kind not in allowed_kinds(ctx):
        raise PermissionDenied("ไม่มีสิทธิ์ดูตารางประเภทนี้")
    if ctx.role in (TEACHER, STUDENT) and tt.status not in ("published", "superseded"):
        raise PermissionDenied("ตารางฉบับนี้ยังไม่เผยแพร่")
    if tt.status == "deleted" and ctx.role != ADMIN:
        raise PermissionDenied("ตารางฉบับนี้ถูกลบแล้ว")
    term = s.get(Term, tt.term_id)
    ts = S.time_structure(s, tt.term_id)
    allent = _enrich(s, [entry_dict(e) for e in entries_of(s, tt.id)])
    allent = _scope_filter(s, ctx, tt.term_id, allent)
    term_label = term.label if term else ""
    tlabel = f"ฉบับที่ {tt.version}" + (f" — {tt.name}" if tt.name else "")
    hdr: list[tuple[str, str]] = []
    if kind in ("group", "teacher", "student", "room", "course", "department"):
        valid = {o[0] for o in options(s, ctx, tt.term_id, kind)}
        if key not in valid:
            raise PermissionDenied("ไม่มีสิทธิ์ดูตารางนี้ หรือไม่พบข้อมูล")
    if kind == "group":
        g = s.get(ClassGroup, key)
        ents = [e for e in allent if key in e["group_ids"]]
        adv = s.get(Teacher, g.advisor_teacher_id) if g.advisor_teacher_id else None
        dept = s.get(Department, g.department_id) if g.department_id else None
        hdr = [("แผนกวิชา", dept.name if dept else "-"), ("ระดับชั้น", g.level or "-"), ("รหัสกลุ่มเรียน", g.code),
               ("ชื่อกลุ่มเรียน", g.name or "-"), ("ครูที่ปรึกษา", adv.name if adv else "-")]
        title = f"ตารางเรียน กลุ่ม {g.code}"
    elif kind == "teacher":
        t = s.get(Teacher, key)
        ents = [e for e in allent if key in e["teacher_ids"]]
        dept = s.get(Department, t.department_id) if t.department_id else None
        teach = [e for e in ents if e["entry_type"] in ("class", "activity")]
        hours = sum(e["end_period"] - e["start_period"] + 1 for e in teach)
        subj = sorted({f"{e['course_code']}" for e in teach if e.get("course_code")})
        hdr = [("ชื่อ-นามสกุล", t.name), ("รหัสครู", t.code), ("แผนกวิชา", dept.name if dept else "-"),
               ("รายวิชาที่รับผิดชอบ", ", ".join(subj) or "-"), ("จำนวนชั่วโมงสอนรวม", f"{hours} คาบ/สัปดาห์")]
        title = f"ตารางสอน {t.name}"
    elif kind == "student":
        st = s.get(Student, key)
        if ctx.role == STUDENT and key != ctx.student_id:
            raise PermissionDenied("ดูได้เฉพาะตารางของตนเอง")
        ents = student_entries(s, tt.term_id, key, allent)
        m = s.scalars(select(StudentMembership).where(StudentMembership.term_id == tt.term_id, StudentMembership.student_id == key)).first()
        g = s.get(ClassGroup, m.group_id) if m else None
        adv = s.get(Teacher, g.advisor_teacher_id) if g and g.advisor_teacher_id else None
        dept = s.get(Department, (g.department_id if g else None) or st.department_id) if ((g and g.department_id) or st.department_id) else None
        hdr = [("ชื่อ-นามสกุล", st.name), ("รหัสนักเรียน", st.code), ("แผนกวิชา", dept.name if dept else "-"),
               ("ระดับชั้น", (g.level if g else None) or "-"), ("รหัสกลุ่มเรียน", g.code if g else "-"),
               ("ชื่อกลุ่มเรียน", (g.name if g else None) or "-"), ("ครูที่ปรึกษา", adv.name if adv else "-")]
        title = f"ตารางเรียนรายบุคคล {st.name}"
    elif kind == "room":
        r = s.get(Room, key)
        ents = [e for e in allent if e["room_id"] == key]
        hdr = [("หมายเลขห้อง", r.label), ("ชื่อห้อง", r.name or "-"), ("ความจุ", str(r.capacity or "-"))]
        title = f"ตารางใช้ห้อง {r.label}"
    elif kind == "course":
        c = s.get(Course, key)
        ents = [e for e in allent if e["course_id"] == key]
        hdr = [("รหัสวิชา", c.code), ("ชื่อวิชา", c.name)]
        title = f"ตารางรายวิชา {c.code}"
    elif kind in ("department", "college"):
        groups = s.scalars(select(ClassGroup).where(ClassGroup.term_id == tt.term_id).order_by(ClassGroup.code)).all()
        if kind == "department":
            d = s.get(Department, key)
            groups = [g for g in groups if g.department_id == key]
            title = f"ตารางสอนแผนก{d.name}"
            hdr = [("แผนกวิชา", d.name), ("จำนวนกลุ่มเรียน", str(len(groups)))]
        else:
            if ctx.role == DEPT_ADMIN:
                groups = [g for g in groups if g.department_id in (ctx.dept_scope or [])]
            title = "ตารางสอนรวมของวิทยาลัย"
            hdr = [("จำนวนกลุ่มเรียน", str(len(groups)))]
        subs = []
        gids = {g.id for g in groups}
        for g in groups:
            subs.append(get_view(s, ctx, tt, "group", g.id) if g.id in {o[0] for o in options(s, ctx, tt.term_id, "group")} else None)
        subs = [x for x in subs if x]
        ents = [e for e in allent if set(e["group_ids"]) & gids or (kind == "college" and e["entry_type"] == "meeting")]
        vd = ViewData(kind, title, hdr, ents, term_label, tlabel, CELL_FIELDS[kind], ts, subs)
        return vd
    elif kind in ("activity", "meeting", "homeroom"):
        ents = [e for e in allent if e["entry_type"] == kind]
        if ctx.role == TEACHER and kind == "meeting":
            ents = [e for e in ents if ctx.teacher_id in e["teacher_ids"]] or ents
        title = VIEW_KINDS[kind]
        hdr = [("จำนวนรายการ", str(len(ents)))]
    else:
        raise ValueError("ประเภทตารางไม่ถูกต้อง")
    conf = detect_conflicts(ents, kind) if kind in ("group", "teacher", "student", "room") else set()
    return ViewData(kind, title, hdr, ents, term_label, tlabel, CELL_FIELDS[kind], ts, [], conf)


# ------------------------------------------------------------------ เปรียบเทียบรายวิชา (สำหรับครู)
SUBJECT_CATS = {"general": "วิชาสามัญ", "major": "วิชาเอก/วิชาชีพ", "activity": "กิจกรรม"}
_PART_TH = {"theory": "ทฤษฎี", "practice": "ปฏิบัติ", "all": "", None: ""}


def subject_catalog(s: Session, ctx: Ctx, tt: Timetable) -> dict:
    """รวบรวมรายวิชาทั้งหมดที่อยู่ในตารางฉบับหนึ่ง จัดหมวด วิชาสามัญ / วิชาเอก / กิจกรรม
    คืนค่า dict: key วิชา -> ข้อมูลวิชา (ครูผู้สอน กลุ่ม ห้อง คาบต่อสัปดาห์ และช่วงเวลาในตาราง)"""
    if ctx.role in (TEACHER, STUDENT) and tt.status not in ("published", "superseded"):
        raise PermissionDenied("ตารางฉบับนี้ยังไม่เผยแพร่")
    if tt.status == "deleted" and ctx.role != ADMIN:
        raise PermissionDenied("ตารางฉบับนี้ถูกลบแล้ว")
    prefixes = S.get(s, "general_prefixes", tt.term_id) or []
    courses = {c.id: c for c in s.scalars(select(Course).where(Course.term_id == tt.term_id)).all()}
    ents = _scope_filter(s, ctx, tt.term_id, [entry_dict(e) for e in entries_of(s, tt.id)])
    out: dict = {}
    for e in ents:
        et = e["entry_type"]
        if et == "meeting":
            continue
        c = courses.get(e["course_id"]) if e["course_id"] else None
        if et in ("activity", "homeroom") or (c is not None and c.course_type == "activity"):
            cat = "activity"
        elif c is not None and S.is_general_course(c.code, prefixes, c.is_general):
            cat = "general"
        elif c is None and et == "class" and S.is_general_course(e.get("course_code") or "", prefixes):
            cat = "general"
        else:
            cat = "major"
        if c is not None:
            k = f"c{c.id}"
            code, name = c.code, c.name
        else:
            name = e.get("course_name") or {"homeroom": "โฮมรูม (พบครูที่ปรึกษา)", "activity": "กิจกรรม"}.get(et, "ไม่ระบุวิชา")
            code = e.get("course_code") or ""
            k = f"x{et}:{code}:{name}"
        d = out.setdefault(k, {"key": k, "code": code, "name": name, "cat": cat,
                               "ctype": {"theory": "ทฤษฎี", "practice": "ปฏิบัติ", "mixed": "ทฤษฎี+ปฏิบัติ", "activity": "กิจกรรม"}.get(c.course_type, "-") if c else "-",
                               "credits": c.credits if c else None,
                               "hours": (f"{c.theory_hours or 0:g}-{c.practice_hours or 0:g}" if c and (c.theory_hours or c.practice_hours) else "-"),
                               "teachers": {}, "groups": set(), "rooms": set(), "periods": 0, "slots": []})
        for tid_, nm in zip(e["teacher_ids"], e.get("teacher_names") or []):
            d["teachers"][tid_] = nm
        d["groups"].update(e.get("group_codes") or [])
        if e.get("room_label"):
            d["rooms"].add(e["room_label"])
        d["periods"] += e["end_period"] - e["start_period"] + 1
        d["slots"].append({"day": e["day"], "start": e["start_period"], "end": e["end_period"], "time": f"{e['start_time']}–{e['end_time']}",
                           "teacher_ids": list(e["teacher_ids"]), "group_codes": list(e.get("group_codes") or []), "room": e.get("room_label") or "",
                           "part": _PART_TH.get(e.get("part"), "")})
    return out
