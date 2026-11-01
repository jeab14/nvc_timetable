"""บริการจัดการตาราง: ฉบับตาราง การจัดอัตโนมัติ การแก้ไขด้วยตนเอง การย้อนกลับ และขั้นตอนอนุมัติ/เผยแพร่"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import audit, problem as P, settings_service as S, solver as SV, validator as V
from .models import (
    ChangeRequest,
    ClassGroup,
    Course,
    Entry,
    EntryGroup,
    EntryTeacher,
    Room,
    Teacher,
    Timetable,
    TimetableChange,
)
from .permissions import ADMIN, DEPT_ADMIN, TEACHER, Ctx, PermissionDenied, check_dept, require

STATUS_LABEL = {
    "not_scheduled": "ยังไม่ได้จัดตาราง",
    "running": "กำลังจัดตาราง",
    "success": "จัดตารางสำเร็จ",
    "partial": "จัดตารางได้บางส่วน",
    "infeasible": "ไม่สามารถจัดตารางได้",
    "validation_failed": "พบข้อผิดพลาดจากการตรวจสอบ",
    "draft": "ฉบับร่าง (แก้ไขแล้ว รอตรวจสอบใหม่)",
    "pending_review": "รอการตรวจสอบจากฝ่ายวิชาการ",
    "approved": "อนุมัติแล้ว",
    "published": "เผยแพร่แล้ว",
    "superseded": "ถูกแทนที่ด้วยตารางฉบับใหม่",
}
LOCKED = {"pending_review", "approved", "published", "superseded", "running"}

FRIENDLY = {
    3: "ไม่สามารถจัดวิชานี้ได้ เนื่องจากครูผู้สอนมีการสอนอีกรายการในช่วงเวลาเดียวกัน",
    4: "ไม่สามารถใช้ห้องนี้ได้ เนื่องจากห้องถูกใช้โดยรายการอื่นในช่วงเวลาเดียวกัน",
    5: "ไม่สามารถจัดวิชานี้ได้ เนื่องจากกลุ่มเรียน/นักเรียนบางคนมีรายการเรียนอื่นในช่วงเวลาเดียวกัน",
    6: "ไม่สามารถใช้ห้องนี้ได้ เนื่องจากประเภทห้องไม่ตรงกับลักษณะการเรียนของรายวิชา",
    7: "ไม่สามารถจัดคาบนี้ได้ เนื่องจากเป็นคาบพัก",
    8: "ไม่สามารถจัดวิชานี้ได้ เนื่องจากกลุ่มเรียนไม่ได้ลงทะเบียนรายวิชานี้",
    9: "ไม่สามารถจัดวิชานี้ได้ เนื่องจากไม่มีครูผู้สอน",
    10: "ไม่สามารถจัดวิชานี้ได้ เนื่องจากครูจะสอนเกินชั่วโมงสอนสูงสุด",
    11: "ไม่สามารถจัดวิชานี้ได้ เนื่องจากครูไม่ได้รับมอบหมายให้สอนรายวิชา/กลุ่มนี้",
    12: "ไม่สามารถจัดวิชานี้ได้ เนื่องจากความจุของห้องไม่เพียงพอ",
    13: "ไม่สามารถจัดวิชานี้ได้ เนื่องจากกลุ่มเรียนจะเรียนเกินจำนวนคาบสูงสุดต่อวัน",
    16: "ไม่สามารถจัดช่วงเวลานี้ได้ เนื่องจากเป็นช่วงเวลากิจกรรมที่กำหนด",
    17: "ไม่สามารถจัดได้ เนื่องจากไม่ตรงกับข้อมูลการเรียนร่วม",
    18: "ไม่สามารถจัดได้ เนื่องจากเป็นรายการซ้ำ",
    19: "ข้อมูลรายการไม่ครบ (ครู/ห้อง/กลุ่มเรียน)",
    20: "ไม่สามารถจัดได้ เนื่องจากอยู่นอกช่วงเวลาที่เปิดให้จัดการเรียนการสอน",
    21: "ไม่สามารถจัดได้ เนื่องจากเป็นเวลาที่ครูไม่สะดวกหรือห้องไม่พร้อมใช้งาน",
    22: "ไม่สามารถจัดได้ เนื่องจากไม่ตรงกับจำนวนคาบต่อเนื่องที่กำหนด",
    1: "ไม่สามารถจัดได้ เนื่องจากจำนวนคาบของรายวิชาจะเกินหลักสูตร",
    2: "ไม่สามารถจัดได้ เนื่องจากจำนวนคาบของกลุ่มเรียนจะเกินหลักสูตร",
    14: "โฮมรูมไม่ถูกต้อง",
    15: "การประชุมไม่ถูกต้อง",
}


class ConflictError(Exception):
    def __init__(self, conflicts: list[dict]):
        super().__init__("; ".join(c["reason"] for c in conflicts))
        self.conflicts = conflicts


class StaleError(Exception):
    pass


# ------------------------------------------------------------------ helpers
def entry_dict(e: Entry) -> dict:
    return {
        "id": e.id, "day": e.day, "start_period": e.start_period, "end_period": e.end_period,
        "start_time": e.start_time, "end_time": e.end_time, "entry_type": e.entry_type, "part": e.part,
        "course_id": e.course_id, "room_id": e.room_id, "teacher_ids": e.teacher_ids, "group_ids": e.group_ids,
        "course_code": e.course_code, "course_name": e.course_name, "room_label": e.room_label,
        "schedule_mode": e.schedule_mode,
        "teacher_names": [t.teacher_name for t in e.teachers], "group_codes": [g.group_code for g in e.groups],
    }


def _snap(s: Session, d: dict) -> dict:
    """เติมข้อมูล snapshot ชื่อวิชา/ห้อง/ครู/กลุ่ม เพื่อให้ดูย้อนหลังได้แม้ข้อมูลหลักเปลี่ยน"""
    out = dict(d)
    if d.get("course_id"):
        c = s.get(Course, d["course_id"])
        out["course_code"], out["course_name"] = (c.code, c.name) if c else (None, None)
    if d.get("room_id"):
        r = s.get(Room, d["room_id"])
        out["room_label"] = r.label if r else None
    else:
        out["room_label"] = None
    out["teacher_names"] = [(s.get(Teacher, t).name if s.get(Teacher, t) else str(t)) for t in d.get("teacher_ids", [])]
    out["group_codes"] = [(s.get(ClassGroup, g).code if s.get(ClassGroup, g) else str(g)) for g in d.get("group_ids", [])]
    return out


def _create_entry(s: Session, tt: Timetable, d: dict, user: str, ts: S.TimeStructure) -> Entry:
    d = _snap(s, d)
    e = Entry(
        timetable_id=tt.id, day=d["day"], start_period=d["start_period"], end_period=d["end_period"],
        start_time=ts.start_time(d["start_period"]), end_time=ts.end_time(d["end_period"]),
        entry_type=d.get("entry_type", "class"), part=d.get("part"), course_id=d.get("course_id"),
        room_id=d.get("room_id"), schedule_mode=d.get("schedule_mode", "manual"),
        course_code=d.get("course_code"), course_name=d.get("course_name"),
        room_label=d.get("room_label"), created_by=user, updated_by=user,
    )
    s.add(e)
    s.flush()
    for t, nm in zip(d.get("teacher_ids", []), d.get("teacher_names", [])):
        s.add(EntryTeacher(entry_id=e.id, teacher_id=t, teacher_name=nm))
    for g, code in zip(d.get("group_ids", []), d.get("group_codes", [])):
        s.add(EntryGroup(entry_id=e.id, group_id=g, group_code=code))
    s.flush()
    s.refresh(e)
    return e


def next_version(s: Session, term_id: int) -> int:
    return (s.scalar(select(func.max(Timetable.version)).where(Timetable.term_id == term_id)) or 0) + 1


def entries_of(s: Session, timetable_id: int) -> list[Entry]:
    return list(s.scalars(select(Entry).where(Entry.timetable_id == timetable_id).order_by(Entry.day, Entry.start_period)).all())


def latest_published(s: Session, term_id: int) -> Timetable | None:
    return s.scalars(select(Timetable).where(Timetable.term_id == term_id, Timetable.status == "published").order_by(Timetable.version.desc())).first()


def list_timetables(s: Session, ctx: Ctx, term_id: int) -> list[Timetable]:
    q = select(Timetable).where(Timetable.term_id == term_id).order_by(Timetable.version.desc())
    rows = list(s.scalars(q).all())
    if ctx.role in (TEACHER, "student"):
        return [t for t in rows if t.status in ("published", "superseded")]
    if ctx.role == DEPT_ADMIN:
        return [t for t in rows if t.status in ("published", "superseded") or t.scope_department_id in (ctx.dept_scope or []) or t.scope_department_id is None]
    return rows


def can_view_timetable(ctx: Ctx, tt: Timetable) -> bool:
    if ctx.role == ADMIN:
        return True
    if ctx.role == DEPT_ADMIN:
        return True  # การกรองข้อมูลรายแผนกทำที่ชั้นมุมมองตาราง (views)
    return tt.status in ("published", "superseded")


# ------------------------------------------------------------------ edit permission
def _entry_dept_ok(s: Session, ctx: Ctx, d: dict) -> bool:
    if ctx.role == ADMIN:
        return True
    if ctx.role == DEPT_ADMIN:
        depts = set(ctx.dept_scope or [])
        gd = {s.get(ClassGroup, g).department_id for g in d.get("group_ids", []) if s.get(ClassGroup, g)}
        if d.get("entry_type") == "meeting":
            return False
        return bool(gd) and gd <= depts
    if ctx.role == TEACHER and ctx.can_edit_timetable:
        return ctx.teacher_id in d.get("teacher_ids", [])
    return False


def _require_editable(s: Session, ctx: Ctx, tt: Timetable) -> None:
    if tt.status in LOCKED:
        raise PermissionDenied(f"ตารางฉบับนี้อยู่ในสถานะ “{STATUS_LABEL[tt.status]}” แก้ไขไม่ได้ — สร้างฉบับใหม่เพื่อแก้ไข")
    if ctx.role == DEPT_ADMIN and tt.scope_department_id not in (ctx.dept_scope or []) and tt.scope_department_id is not None:
        raise PermissionDenied("ไม่สามารถแก้ไขตารางของแผนกอื่นได้")
    if ctx.role not in (ADMIN, DEPT_ADMIN) and not (ctx.role == TEACHER and ctx.can_edit_timetable):
        raise PermissionDenied("คุณไม่มีสิทธิ์แก้ไขตาราง — ใช้เมนู “เสนอการเปลี่ยนแปลง” แทน")


# ------------------------------------------------------------------ create / auto
def create_empty(s: Session, ctx: Ctx, term_id: int, name: str = "", scope_department_id: int | None = None) -> Timetable:
    require(ctx, ADMIN, DEPT_ADMIN)
    if ctx.role == DEPT_ADMIN:
        if scope_department_id is None:
            raise PermissionDenied("ฝ่ายวิชาการประจำแผนกต้องเลือกแผนกของตน")
        check_dept(ctx, scope_department_id)
    tt = Timetable(term_id=term_id, version=next_version(s, term_id), name=name or "จัดด้วยตนเอง", status="draft",
                   created_by=ctx.username, scope_department_id=scope_department_id)
    s.add(tt)
    s.flush()
    _add_fixed_items(s, tt, ctx, scope_department_id)
    audit.log(s, ctx.username, "create_timetable", "timetable", {"id": tt.id, "version": tt.version})
    return tt


def _add_fixed_items(s: Session, tt: Timetable, ctx: Ctx, dept: int | None) -> None:
    pb = P.build(s, tt.term_id, dept)
    ts = pb.ts
    for f in pb.fixed:
        if dept is not None and f.kind == "meeting":
            continue
        _create_entry(s, tt, {"day": f.day, "start_period": min(f.periods), "end_period": max(f.periods), "entry_type": f.kind,
                              "course_id": f.course_id, "room_id": f.room_id, "teacher_ids": f.teacher_ids, "group_ids": f.group_ids,
                              "schedule_mode": "fixed", "course_name": f.label if f.kind == "meeting" else None}, ctx.username, ts)


def run_auto(s: Session, ctx: Ctx, term_id: int, department_id: int | None = None, time_limit: int | None = None,
             base_timetable_id: int | None = None, name: str = "") -> Timetable:
    """จัดตารางอัตโนมัติ สร้างตารางฉบับใหม่เสมอ (ไม่ลบฉบับเดิม)"""
    require(ctx, ADMIN, DEPT_ADMIN)
    if ctx.role == DEPT_ADMIN:
        if department_id is None:
            raise PermissionDenied("ฝ่ายวิชาการประจำแผนกต้องเลือกแผนกของตน")
        check_dept(ctx, department_id)
    ts = S.time_structure(s, term_id)
    pb = P.build(s, term_id, department_id)
    base_entries: list[Entry] = []
    hint = None
    base = s.get(Timetable, base_timetable_id) if base_timetable_id else None
    if base is not None and base.term_id == term_id:
        all_base = entries_of(s, base.id)
        if department_id is not None:
            # ตารางของแผนกอื่นในฉบับฐานถูกถือเป็นเวลาที่ไม่ว่าง (ครู/ห้อง/กลุ่ม)
            scope_groups = {g for g, gi in pb.groups.items() if gi["dept"] == department_id}
            for e in all_base:
                if set(e.group_ids) & scope_groups and e.entry_type not in ("activity", "meeting"):
                    continue  # รายการของแผนกที่จะจัดใหม่ (กิจกรรม/ประชุมเป็นเวลาคงที่ คัดลอกจากฉบับฐาน)
                base_entries.append(e)
                for g in e.group_ids:
                    if g not in scope_groups:
                        pb.group_forbidden.setdefault(g, set()).update((e.day, p) for p in range(e.start_period, e.end_period + 1))
                if e.entry_type == "meeting":
                    continue  # รายการประชุมคัดลอกจากฉบับฐาน (เวลาประชุมถูกล็อกไว้อยู่แล้ว)
                cells = {(e.day, p) for p in range(e.start_period, e.end_period + 1)}
                for t in e.teacher_ids:
                    if t in pb.teachers:
                        pb.teachers[t]["unavail"] |= cells
                        if e.entry_type in ("class", "activity"):
                            # ภาระสอนในแผนกอื่นนับรวมในชั่วโมงสอนสูงสุด
                            n = e.end_period - e.start_period + 1
                            pb.teachers[t]["preload_week"] = pb.teachers[t].get("preload_week", 0) + n
                            pd_ = pb.teachers[t].setdefault("preload_day", {})
                            pd_[e.day] = pd_.get(e.day, 0) + n
                if e.room_id in pb.rooms:
                    pb.rooms[e.room_id]["unavail"] |= cells
        hint = _hint_from(pb, all_base)
    tl = int(time_limit or S.get(s, "solver_time_limit", term_id) or 60)
    res = SV.solve(pb, time_limit=tl, hint=hint)
    # จัดเฉพาะแผนกโดยมีฉบับฐาน = ได้ตารางทั้งวิทยาลัย (แผนกอื่นคงเดิมตามฉบับฐาน) จึงไม่จำกัดขอบเขต
    merged = department_id is not None and bool(base_entries)
    tt = Timetable(term_id=term_id, version=next_version(s, term_id),
                   name=(name or "จัดอัตโนมัติ") + (" (ปรับเฉพาะแผนก รวมกับฉบับฐาน)" if merged else ""), status="running",
                   created_by=ctx.username, scope_department_id=None if merged else department_id, parent_id=base.id if base else None)
    s.add(tt)
    s.flush()
    lessons = {L.id: L for L in pb.lessons}
    for p in res.placements:
        L = lessons[p.lesson_id]
        _create_entry(s, tt, {"day": p.day, "start_period": p.start, "end_period": p.start + p.length - 1,
                              "entry_type": L.kind, "part": L.part if L.kind == "class" else None, "course_id": L.course_id,
                              "room_id": p.room_id, "teacher_ids": L.teacher_ids, "group_ids": L.group_ids, "schedule_mode": "auto"},
                      ctx.username, ts)
    if res.status in ("success", "partial"):
        for f in pb.fixed:
            if department_id is not None and (f.kind == "meeting" or (merged and f.kind == "activity")):
                continue
            _create_entry(s, tt, {"day": f.day, "start_period": min(f.periods), "end_period": max(f.periods), "entry_type": f.kind,
                                  "course_id": f.course_id, "room_id": f.room_id, "teacher_ids": f.teacher_ids, "group_ids": f.group_ids,
                                  "schedule_mode": "fixed", "course_name": f.label if f.kind == "meeting" else None}, ctx.username, ts)
        for e in base_entries:
            d = entry_dict(e)
            d["schedule_mode"] = e.schedule_mode
            _create_entry(s, tt, d, ctx.username, ts)
    report = dict(res.report)
    report["unplaced"] = res.unplaced
    report["issues"] = [{"level": i.level, "message": i.message, "fix": i.fix} for i in pb.issues]
    tt.solver_report = report
    if res.status in ("success", "partial"):
        vr = V.validate_timetable(s, tt.id)
        tt.validation_status = vr.status
        tt.validation_report = vr.as_report()
        if vr.status == "fail":
            tt.status = "validation_failed"
        else:
            tt.status = res.status
    else:
        tt.status = "infeasible"
    audit.log(s, ctx.username, "auto_schedule", "timetable", {"id": tt.id, "status": tt.status, "dept": department_id})
    return tt


def _hint_from(pb: P.Problem, entries: list[Entry]) -> dict:
    """ใช้ตารางเดิมเป็นจุดเริ่มต้น เพื่อลดการเปลี่ยนแปลงเมื่อปรับปรุงตาราง"""
    hint = {}
    pool: dict[tuple, list[Entry]] = {}
    for e in entries:
        if e.entry_type in ("class", "homeroom"):
            key = (e.entry_type, e.course_id, e.part if e.entry_type == "class" else "all", tuple(sorted(e.group_ids)), tuple(sorted(e.teacher_ids)))
            pool.setdefault(key, []).append(e)
    for L in pb.lessons:
        key = (L.kind, L.course_id, L.part, tuple(sorted(L.group_ids)), tuple(sorted(L.teacher_ids)))
        cands = sorted(pool.get(key, []), key=lambda x: (x.day, x.start_period))
        for k, n in enumerate(L.blocks):
            for e in cands:
                if e.end_period - e.start_period + 1 == n:
                    hint[(L.id, k)] = (e.day, e.start_period, e.room_id)
                    cands.remove(e)
                    break
    return hint


# ------------------------------------------------------------------ manual editing
def check_candidate(s: Session, tt: Timetable, candidate: dict, replace_id: int | None = None, delete: bool = False) -> list[dict]:
    """ตรวจข้อจำกัดของการแก้ไขก่อนบันทึก คืนรายการปัญหาที่ “เกิดใหม่” จากการแก้ไขนี้"""
    current = [entry_dict(e) for e in entries_of(s, tt.id)]
    base = V.validate_entries(s, tt.term_id, current, _scope_groups(s, tt))
    trial = [e for e in current if e["id"] != replace_id]
    if not delete:
        cand = dict(candidate)
        cand["id"] = cand.get("id") or -1
        ts = S.time_structure(s, tt.term_id)
        try:
            cand["start_time"] = ts.start_time(cand["start_period"])
            cand["end_time"] = ts.end_time(cand["end_period"])
        except KeyError:
            return [{"check": 20, "reason": FRIENDLY[20], "detail": "คาบที่เลือกไม่มีในโครงสร้างเวลา", "entry_ids": []}]
        cand = _snap(s, cand)
        trial.append(cand)
    after = V.validate_entries(s, tt.term_id, trial, _scope_groups(s, tt))
    base_msgs = {(f.check, f.message) for f in base.errors}
    out = []
    for f in after.errors:
        if f.incomplete:
            continue
        if (f.check, f.message) in base_msgs and not ((candidate.get("id") or -1) in f.entry_ids):
            continue
        out.append({"check": f.check, "reason": FRIENDLY.get(f.check, V.CHECK_NAMES.get(f.check, "")), "detail": f.message,
                    "entry_ids": [i for i in f.entry_ids if i and i > 0 and i != replace_id]})
    return out


def _scope_groups(s: Session, tt: Timetable):
    if tt.scope_department_id is None:
        return None
    return {g.id for g in s.scalars(select(ClassGroup).where(ClassGroup.term_id == tt.term_id, ClassGroup.department_id == tt.scope_department_id)).all()}


def _bump(tt: Timetable, expected: int | None) -> None:
    if expected is not None and tt.lock_version != expected:
        raise StaleError("ตารางถูกแก้ไขโดยผู้ใช้อื่นระหว่างนี้ กรุณาโหลดข้อมูลใหม่แล้วลองอีกครั้ง")
    tt.lock_version += 1
    if tt.status not in ("draft",):
        tt.status = "draft"
    tt.validation_status = None


def add_entry(s: Session, ctx: Ctx, tt_id: int, d: dict, reason: str = "", expected_version: int | None = None) -> Entry:
    tt = s.get(Timetable, tt_id)
    _require_editable(s, ctx, tt)
    if not _entry_dept_ok(s, ctx, d):
        raise PermissionDenied("ไม่มีสิทธิ์จัดรายการนี้ (นอกขอบเขตแผนก/รายการของตนเอง)")
    conf = check_candidate(s, tt, d)
    if conf:
        raise ConflictError(conf)
    _bump(tt, expected_version)
    d = dict(d)
    d.setdefault("schedule_mode", "manual")
    e = _create_entry(s, tt, d, ctx.username, S.time_structure(s, tt.term_id))
    s.add(TimetableChange(timetable_id=tt.id, action="add", entry_id=e.id, before=None, after=entry_dict(e), reason=reason, user=ctx.username))
    audit.log(s, ctx.username, "entry_add", "timetable", {"timetable": tt.id, "entry": e.id})
    return e


def update_entry(s: Session, ctx: Ctx, tt_id: int, entry_id: int, changes: dict, reason: str = "", expected_version: int | None = None) -> Entry:
    tt = s.get(Timetable, tt_id)
    _require_editable(s, ctx, tt)
    e = s.get(Entry, entry_id)
    if e is None or e.timetable_id != tt.id:
        raise ValueError("ไม่พบรายการ")
    before = entry_dict(e)
    if not _entry_dept_ok(s, ctx, before):
        raise PermissionDenied("ไม่มีสิทธิ์แก้ไขรายการนี้")
    if e.schedule_mode == "fixed" and ctx.role != ADMIN:
        raise PermissionDenied("รายการกำหนดเวลาคงที่ (ประชุม/กิจกรรม) แก้ไขได้เฉพาะผู้ดูแลระบบ")
    cand = {**before, **changes}
    if "start_period" in changes and "end_period" not in changes:
        cand["end_period"] = changes["start_period"] + (before["end_period"] - before["start_period"])
    if not _entry_dept_ok(s, ctx, cand):
        raise PermissionDenied("ไม่มีสิทธิ์ย้ายรายการไปนอกขอบเขตของตน")
    cand["id"] = entry_id
    conf = check_candidate(s, tt, cand, replace_id=entry_id)
    if conf:
        raise ConflictError(conf)
    _bump(tt, expected_version)
    ts = S.time_structure(s, tt.term_id)
    cand = _snap(s, cand)
    e.day, e.start_period, e.end_period = cand["day"], cand["start_period"], cand["end_period"]
    e.start_time, e.end_time = ts.start_time(e.start_period), ts.end_time(e.end_period)
    e.room_id, e.room_label = cand.get("room_id"), cand.get("room_label")
    e.updated_by = ctx.username
    if e.schedule_mode == "auto":
        e.schedule_mode = "manual"
    if set(cand["teacher_ids"]) != set(before["teacher_ids"]):
        for t in list(e.teachers):
            s.delete(t)
        s.flush()
        for t, nm in zip(cand["teacher_ids"], cand["teacher_names"]):
            s.add(EntryTeacher(entry_id=e.id, teacher_id=t, teacher_name=nm))
    s.flush()
    s.refresh(e)
    s.add(TimetableChange(timetable_id=tt.id, action="update", entry_id=e.id, before=before, after=entry_dict(e), reason=reason, user=ctx.username))
    audit.log(s, ctx.username, "entry_update", "timetable", {"timetable": tt.id, "entry": e.id})
    return e


def delete_entry(s: Session, ctx: Ctx, tt_id: int, entry_id: int, reason: str = "", expected_version: int | None = None) -> None:
    tt = s.get(Timetable, tt_id)
    _require_editable(s, ctx, tt)
    e = s.get(Entry, entry_id)
    if e is None or e.timetable_id != tt.id:
        raise ValueError("ไม่พบรายการ")
    before = entry_dict(e)
    if not _entry_dept_ok(s, ctx, before):
        raise PermissionDenied("ไม่มีสิทธิ์ลบรายการนี้")
    if e.schedule_mode == "fixed" and ctx.role != ADMIN:
        raise PermissionDenied("รายการกำหนดเวลาคงที่ลบได้เฉพาะผู้ดูแลระบบ")
    _bump(tt, expected_version)
    s.delete(e)
    s.add(TimetableChange(timetable_id=tt.id, action="delete", entry_id=entry_id, before=before, after=None, reason=reason, user=ctx.username))
    audit.log(s, ctx.username, "entry_delete", "timetable", {"timetable": tt.id, "entry": entry_id})


def undo_last(s: Session, ctx: Ctx, tt_id: int) -> str:
    """ย้อนกลับการแก้ไขล่าสุดที่ยังไม่ถูกย้อน"""
    tt = s.get(Timetable, tt_id)
    _require_editable(s, ctx, tt)
    ch = s.scalars(select(TimetableChange).where(TimetableChange.timetable_id == tt.id, TimetableChange.undone.is_(False)).order_by(TimetableChange.id.desc())).first()
    if ch is None:
        raise ValueError("ไม่มีการแก้ไขที่ย้อนกลับได้")
    if ctx.role != ADMIN and ch.user != ctx.username:
        raise PermissionDenied("ย้อนกลับได้เฉพาะการแก้ไขของตนเอง")
    ts = S.time_structure(s, tt.term_id)
    if ch.action == "add":
        e = s.get(Entry, ch.entry_id)
        if e:
            s.delete(e)
    elif ch.action == "delete":
        b = dict(ch.before)
        e = _create_entry(s, tt, b, ctx.username, ts)
        # ปรับการอ้างอิง entry_id ในประวัติให้ชี้รายการใหม่
        for later in s.scalars(select(TimetableChange).where(TimetableChange.timetable_id == tt.id, TimetableChange.entry_id == ch.entry_id)).all():
            later.entry_id = e.id
    elif ch.action == "update":
        e = s.get(Entry, ch.entry_id)
        b = ch.before
        if e:
            e.day, e.start_period, e.end_period = b["day"], b["start_period"], b["end_period"]
            e.start_time, e.end_time = ts.start_time(e.start_period), ts.end_time(e.end_period)
            e.room_id, e.room_label = b.get("room_id"), b.get("room_label")
            if set(b["teacher_ids"]) != set(e.teacher_ids):
                for t in list(e.teachers):
                    s.delete(t)
                s.flush()
                for t, nm in zip(b["teacher_ids"], b.get("teacher_names") or [""] * len(b["teacher_ids"])):
                    s.add(EntryTeacher(entry_id=e.id, teacher_id=t, teacher_name=nm))
    ch.undone = True
    tt.lock_version += 1
    tt.status = "draft"
    tt.validation_status = None
    audit.log(s, ctx.username, "undo", "timetable", {"timetable": tt.id, "change": ch.id})
    return {"add": "ยกเลิกการเพิ่มรายการ", "delete": "กู้คืนรายการที่ลบ", "update": "ย้อนการแก้ไขรายการ"}[ch.action]


# ------------------------------------------------------------------ workflow
def validate(s: Session, ctx: Ctx, tt_id: int) -> V.ValidationResult:
    require(ctx, ADMIN, DEPT_ADMIN, TEACHER)
    tt = s.get(Timetable, tt_id)
    vr = V.validate_timetable(s, tt.id)
    if tt.status in LOCKED:
        return vr
    tt.validation_status = vr.status
    tt.validation_report = vr.as_report()
    unplaced = (tt.solver_report or {}).get("unplaced") if tt.status == "partial" else None
    if vr.status == "fail":
        tt.status = "validation_failed"
    elif tt.status in ("draft", "validation_failed", "success", "not_scheduled"):
        tt.status = "success"
    if unplaced and vr.status != "fail":
        tt.status = "success" if not any(f.incomplete for f in vr.findings) else "partial"
    audit.log(s, ctx.username, "validate", "timetable", {"id": tt.id, "status": vr.status})
    return vr


def submit_for_review(s: Session, ctx: Ctx, tt_id: int) -> None:
    require(ctx, ADMIN, DEPT_ADMIN)
    tt = s.get(Timetable, tt_id)
    vr = validate(s, ctx, tt_id)
    if vr.status == "fail":
        raise ValueError("ตารางยังไม่ผ่านการตรวจสอบข้อจำกัดบังคับ จึงส่งตรวจสอบไม่ได้")
    tt.status = "pending_review"
    audit.log(s, ctx.username, "submit_review", "timetable", {"id": tt.id})


def approve(s: Session, ctx: Ctx, tt_id: int) -> None:
    require(ctx, ADMIN)
    tt = s.get(Timetable, tt_id)
    if tt.status not in ("pending_review", "success"):
        raise ValueError("อนุมัติได้เฉพาะตารางที่จัดสำเร็จหรือรอการตรวจสอบ")
    vr = V.validate_timetable(s, tt.id)
    tt.validation_status, tt.validation_report = vr.status, vr.as_report()
    if vr.status == "fail":
        tt.status = "validation_failed"
        raise ValueError("ตารางไม่ผ่านการตรวจสอบ ไม่สามารถอนุมัติได้")
    tt.status = "approved"
    tt.approved_by = ctx.username
    tt.approved_at = datetime.now()
    audit.log(s, ctx.username, "approve", "timetable", {"id": tt.id})


def publish(s: Session, ctx: Ctx, tt_id: int) -> None:
    require(ctx, ADMIN)
    tt = s.get(Timetable, tt_id)
    if tt.status != "approved":
        raise ValueError("ต้องอนุมัติตารางก่อนเผยแพร่")
    vr = V.validate_timetable(s, tt.id)
    if vr.status == "fail":
        raise ValueError("ตารางไม่ผ่านการตรวจสอบ ไม่สามารถเผยแพร่ได้")
    if tt.scope_department_id is not None:
        raise ValueError("ตารางเฉพาะแผนกต้องรวมเป็นตารางทั้งวิทยาลัยก่อนเผยแพร่ (จัดอัตโนมัติทั้งวิทยาลัยโดยใช้ฉบับนี้เป็นฐาน)")
    for old in s.scalars(select(Timetable).where(Timetable.term_id == tt.term_id, Timetable.status == "published", Timetable.id != tt.id)).all():
        old.status = "superseded"
    tt.status = "published"
    tt.published_at = datetime.now()
    audit.log(s, ctx.username, "publish", "timetable", {"id": tt.id})


def clone(s: Session, ctx: Ctx, tt_id: int, name: str = "") -> Timetable:
    """สร้างฉบับใหม่จากฉบับเดิมเพื่อแก้ไข (ไม่ทำลายประวัติ)"""
    require(ctx, ADMIN, DEPT_ADMIN)
    src = s.get(Timetable, tt_id)
    if ctx.role == DEPT_ADMIN and src.scope_department_id not in (ctx.dept_scope or []):
        if src.scope_department_id is not None:
            raise PermissionDenied("ไม่สามารถคัดลอกตารางของแผนกอื่น")
    tt = Timetable(term_id=src.term_id, version=next_version(s, src.term_id), name=name or f"ปรับปรุงจากฉบับที่ {src.version}",
                   status="draft", parent_id=src.id, created_by=ctx.username,
                   scope_department_id=src.scope_department_id if ctx.role == ADMIN else (ctx.dept_scope[0] if ctx.dept_scope else None),
                   solver_report=src.solver_report)
    s.add(tt)
    s.flush()
    ts = S.time_structure(s, src.term_id)
    for e in entries_of(s, src.id):
        d = entry_dict(e)
        _create_entry(s, tt, d, ctx.username, ts)
    audit.log(s, ctx.username, "clone_timetable", "timetable", {"from": src.id, "to": tt.id})
    return tt


def delete_draft(s: Session, ctx: Ctx, tt_id: int) -> None:
    require(ctx, ADMIN)
    tt = s.get(Timetable, tt_id)
    if tt.status in ("published", "superseded", "approved"):
        raise ValueError("ไม่สามารถลบตารางที่อนุมัติ/เผยแพร่แล้ว (เก็บไว้เป็นประวัติ)")
    for ch in s.scalars(select(TimetableChange).where(TimetableChange.timetable_id == tt.id)).all():
        s.delete(ch)
    for cr in s.scalars(select(ChangeRequest).where(ChangeRequest.timetable_id == tt.id)).all():
        s.delete(cr)
    for child in s.scalars(select(Timetable).where(Timetable.parent_id == tt.id)).all():
        child.parent_id = tt.parent_id
    s.delete(tt)
    audit.log(s, ctx.username, "delete_timetable", "timetable", {"id": tt_id})


# ------------------------------------------------------------------ change requests (ครู)
def create_request(s: Session, ctx: Ctx, tt_id: int, entry_id: int, new_day: int, new_start: int, new_room_id: int | None, reason: str) -> tuple[ChangeRequest, list[dict]]:
    require(ctx, TEACHER, DEPT_ADMIN, ADMIN)
    tt = s.get(Timetable, tt_id)
    e = s.get(Entry, entry_id)
    if e is None or e.timetable_id != tt_id:
        raise ValueError("ไม่พบรายการ")
    if ctx.role == TEACHER and ctx.teacher_id not in e.teacher_ids:
        raise PermissionDenied("เสนอการเปลี่ยนแปลงได้เฉพาะรายการสอนของตนเอง")
    d = entry_dict(e)
    length = e.end_period - e.start_period
    cand = {**d, "day": new_day, "start_period": new_start, "end_period": new_start + length, "room_id": new_room_id if new_room_id else d["room_id"]}
    conf = check_candidate(s, tt, cand, replace_id=e.id)
    cr = ChangeRequest(timetable_id=tt_id, entry_id=entry_id, requested_by=ctx.username, new_day=new_day, new_start=new_start,
                       new_room_id=cand["room_id"], reason=reason)
    s.add(cr)
    s.flush()
    audit.log(s, ctx.username, "change_request", "timetable", {"id": cr.id, "conflicts": len(conf)})
    return cr, conf


def review_request(s: Session, ctx: Ctx, req_id: int, approve_it: bool) -> Timetable | None:
    """อนุมัติคำขอ: นำไปใช้กับตารางฉบับร่างใหม่ที่คัดลอกจากฉบับเดิม (ฉบับที่เผยแพร่ไม่ถูกแก้ไขโดยตรง)"""
    require(ctx, ADMIN, DEPT_ADMIN)
    cr = s.get(ChangeRequest, req_id)
    if cr is None or cr.status != "pending":
        raise ValueError("ไม่พบคำขอที่รอพิจารณา")
    cr.reviewed_by = ctx.username
    if not approve_it:
        cr.status = "rejected"
        audit.log(s, ctx.username, "reject_request", "timetable", {"id": cr.id})
        return None
    src = s.get(Timetable, cr.timetable_id)
    e = s.get(Entry, cr.entry_id)
    if e is None:
        raise ValueError("รายการต้นทางไม่มีอยู่แล้ว")
    sig = (e.entry_type, e.course_id, e.day, e.start_period, tuple(sorted(e.group_ids)), tuple(sorted(e.teacher_ids)))
    target = src if src.status not in LOCKED else clone(s, ctx, src.id, name=f"ปรับปรุงตามคำขอของ {cr.requested_by}")
    tgt_entry = next((x for x in entries_of(s, target.id) if (x.entry_type, x.course_id, x.day, x.start_period, tuple(sorted(x.group_ids)), tuple(sorted(x.teacher_ids))) == sig), None)
    if tgt_entry is None:
        raise ValueError("ไม่พบรายการที่ตรงกันในตารางฉบับร่าง")
    update_entry(s, ctx, target.id, tgt_entry.id, {"day": cr.new_day, "start_period": cr.new_start, "room_id": cr.new_room_id},
                 reason=f"คำขอ #{cr.id}: {cr.reason or ''}")
    cr.status = "approved"
    audit.log(s, ctx.username, "approve_request", "timetable", {"id": cr.id, "timetable": target.id})
    return target
