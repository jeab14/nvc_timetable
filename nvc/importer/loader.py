"""นำข้อมูลที่จับคู่คอลัมน์แล้วเข้าสู่ฐานข้อมูล

- ทำงานภายใน SAVEPOINT: โหมดตัวอย่าง (dry_run) จะคำนวณผลกระทบจริงแล้วย้อนกลับทั้งหมด
  ทำให้ “ผลกระทบก่อนยืนยัน” ตรงกับผลจริง และไฟล์ที่ผิดพลาดไม่ทำให้ข้อมูลเดิมเสียหาย
- ไม่สร้างข้อมูลสำคัญขึ้นเอง: ถ้าอ้างอิงรหัสที่ไม่มีอยู่ แถวนั้นเป็นข้อผิดพลาด
- การจับคู่ด้วยชื่อทำเฉพาะเมื่อผู้ใช้เปิดตัวเลือก และจะรายงานเป็นคำเตือนทุกครั้ง
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import audit, settings_service
from ..models import (
    Assignment,
    ClassGroup,
    Course,
    Department,
    Enrollment,
    GroupCourse,
    ImportBatch,
    JointClass,
    Room,
    RoomType,
    Student,
    StudentMembership,
    Teacher,
    TeacherQualification,
    TeacherUnavailable,
)
from ..permissions import ADMIN, Ctx, require
from . import mapping as M
from .schemas import ENTITIES, alt_required_satisfied

MODES = {
    "add": "เพิ่มเฉพาะข้อมูลใหม่ (รายการที่มีอยู่และข้อมูลต่างกันจะแจ้งเตือน ไม่เขียนทับ)",
    "update": "เพิ่มข้อมูลใหม่และอัปเดตข้อมูลเดิม",
    "skip": "เพิ่มเฉพาะข้อมูลใหม่ และข้ามรายการที่ซ้ำโดยไม่แจ้งเตือน",
}


@dataclass
class Issue:
    level: str  # error / warning / info
    message: str
    row: int | None = None
    column: str | None = None
    file: str = ""
    sheet: str | None = None

    def as_dict(self) -> dict:
        lv = {"error": "ข้อผิดพลาด", "warning": "คำเตือน", "info": "ข้อมูลทั่วไป"}[self.level]
        return {"ระดับ": lv, "ไฟล์": self.file, "แผ่นงาน": self.sheet or "-", "แถว": self.row or "-", "คอลัมน์": self.column or "-", "รายละเอียด": self.message}


@dataclass
class ImportResult:
    entity: str
    counts: dict = field(default_factory=lambda: {"new": 0, "updated": 0, "unchanged": 0, "skipped": 0, "error": 0})
    issues: list[Issue] = field(default_factory=list)
    committed: bool = False

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.level == "error"]

    @property
    def warnings(self) -> list[Issue]:
        return [i for i in self.issues if i.level == "warning"]


class _RowError(Exception):
    def __init__(self, msg: str, column: str | None = None):
        super().__init__(msg)
        self.column = column


class _Ctx:
    """แคชสำหรับค้นหาข้อมูลอ้างอิงระหว่างนำเข้า"""

    def __init__(self, s: Session, term_id: int, allow_name_match: bool):
        self.s = s
        self.term_id = term_id
        self.allow_name_match = allow_name_match
        self.ts = settings_service.time_structure(s, term_id)
        self.room_types = {rt.code: rt for rt in s.scalars(select(RoomType)).all()}
        self.room_type_names = {rt.name: rt.code for rt in self.room_types.values()}

    def dept(self, value: str, res: ImportResult, row: int, col: str, file: str, sheet) -> int | None:
        v = (value or "").strip()
        if not v:
            return None
        d = self.s.scalars(select(Department).where(Department.code == v)).first()
        if d is None:
            d = self.s.scalars(select(Department).where(Department.name == v)).first()
        if d is None:
            d = Department(code=v, name=v)
            self.s.add(d)
            self.s.flush()
            res.issues.append(Issue("warning", f"สร้างแผนก “{v}” ใหม่จากข้อมูลอ้างอิงในไฟล์ (ตรวจสอบรหัส/ชื่อแผนกได้ที่หน้าจัดการข้อมูล)", row, col, file, sheet))
        return d.id

    def teacher(self, value: str, col: str) -> Teacher:
        v = (value or "").strip()
        if not v:
            raise _RowError("ไม่ได้ระบุรหัสครู", col)
        t = self.s.scalars(select(Teacher).where(Teacher.code == v)).first()
        if t:
            return t
        if self.allow_name_match:
            cands = [x for x in self.s.scalars(select(Teacher)).all() if M.norm(x.name) == M.norm(v)]
            if len(cands) == 1:
                raise _NameMatched(cands[0], f"จับคู่ครู “{v}” ด้วยชื่อ → รหัส {cands[0].code} (กรุณาตรวจสอบ)")
            if len(cands) > 1:
                raise _RowError(f"พบครูชื่อ “{v}” มากกว่า 1 คน ไม่สามารถจับคู่ด้วยชื่อได้", col)
        hint = self._similar_teacher(v)
        raise _RowError(f"ไม่พบครูรหัส “{v}” ในฐานข้อมูล" + (f" (ใกล้เคียง: {hint})" if hint else ""), col)

    def _similar_teacher(self, v: str) -> str:
        from difflib import get_close_matches

        all_t = self.s.scalars(select(Teacher)).all()
        names = {t.name: t.code for t in all_t}
        codes = {t.code: t.name for t in all_t}
        m = get_close_matches(v, list(codes), n=1, cutoff=0.8) or get_close_matches(v, list(names), n=1, cutoff=0.8)
        if not m:
            return ""
        k = m[0]
        return f"{k} {codes.get(k, '')}".strip() if k in codes else f"{names[k]} {k}"

    def course(self, value: str, col: str) -> Course:
        v = (value or "").strip()
        c = self.s.scalars(select(Course).where(Course.term_id == self.term_id, Course.code == v)).first()
        if c is None:
            raise _RowError(f"ไม่พบรหัสวิชา “{v}” ในข้อมูลรายวิชาของภาคเรียนนี้", col)
        return c

    def group(self, value: str, col: str) -> ClassGroup:
        v = (value or "").strip()
        g = self.s.scalars(select(ClassGroup).where(ClassGroup.term_id == self.term_id, ClassGroup.code == v)).first()
        if g is None:
            raise _RowError(f"ไม่พบกลุ่มเรียน “{v}” ในภาคเรียนนี้", col)
        return g

    def student(self, value: str, col: str) -> Student:
        v = (value or "").strip()
        st = self.s.scalars(select(Student).where(Student.code == v)).first()
        if st is None:
            raise _RowError(f"ไม่พบนักเรียนรหัส “{v}”", col)
        return st

    def room_type(self, value: str, col: str) -> str | None:
        if not (value or "").strip():
            return None
        code = M.parse_room_type(value, self.room_type_names)
        if code is None or code not in self.room_types:
            raise _RowError(f"ไม่รู้จักประเภทห้อง “{value}” — เพิ่มประเภทห้องที่หน้าจัดการข้อมูลก่อน", col)
        return code


class _DupSkip(Exception):
    def __init__(self, msg: str, column: str | None = None):
        super().__init__(msg)
        self.column = column


class _NameMatched(Exception):
    def __init__(self, teacher: Teacher, msg: str):
        super().__init__(msg)
        self.teacher = teacher


def _get(row: dict, mp: dict[str, str], fld: str) -> str:
    col = mp.get(fld)
    if not col:
        return ""
    return str(row.get(col, "") or "").strip()


def _apply(obj, values: dict, mode: str, is_new: bool, res: ImportResult, label: str, row: int, file: str, sheet) -> None:
    if is_new:
        for k, v in values.items():
            setattr(obj, k, v)
        res.counts["new"] += 1
        return
    diff = {k: v for k, v in values.items() if v is not None and getattr(obj, k) != v}
    if not diff:
        res.counts["unchanged"] += 1
        return
    if mode == "update":
        for k, v in diff.items():
            setattr(obj, k, v)
        res.counts["updated"] += 1
    elif mode == "add":
        res.counts["skipped"] += 1
        res.issues.append(Issue("warning", f"{label} มีอยู่แล้วแต่ข้อมูลต่างกัน ({', '.join(diff)}) — ไม่ได้เขียนทับ เลือกโหมด “อัปเดต” หากต้องการแก้ไข", row, None, file, sheet))
    else:
        res.counts["skipped"] += 1


def _person_name(row, mp) -> str:
    name = _get(row, mp, "name")
    if not name:
        parts = [_get(row, mp, "prefix"), _get(row, mp, "first_name"), _get(row, mp, "last_name")]
        name = " ".join(p for p in parts if p)
    elif _get(row, mp, "prefix") and not name.startswith(_get(row, mp, "prefix")):
        name = f"{_get(row, mp, 'prefix')}{name}"
    return " ".join(name.split())


def run_import(
    s: Session,
    ctx: Ctx,
    term_id: int,
    entity: str,
    df: pd.DataFrame,
    mp: dict[str, str],
    mode: str = "add",
    filename: str = "",
    sheet: str | None = None,
    header_row: int = 0,
    allow_name_match: bool = False,
    skip_error_rows: bool = False,
    dry_run: bool = True,
) -> ImportResult:
    """mp = {ฟิลด์มาตรฐาน: ชื่อคอลัมน์ต้นทาง}"""
    require(ctx, ADMIN)
    if entity not in ENTITIES:
        raise ValueError("ชนิดข้อมูลไม่ถูกต้อง")
    if mode not in MODES:
        raise ValueError("โหมดการนำเข้าไม่ถูกต้อง")
    res = ImportResult(entity)
    missing = alt_required_satisfied(entity, set(mp))
    if missing:
        labels = [ENTITIES[entity]["fields"][m]["label"] for m in missing]
        res.issues.append(Issue("error", f"ยังไม่ได้จับคู่ข้อมูลสำคัญ: {', '.join(labels)} — ไม่สามารถนำเข้าได้", None, None, filename, sheet))
        res.counts["error"] = len(df)
        return res

    nested = s.begin_nested()
    cx = _Ctx(s, term_id, allow_name_match)
    handler = _HANDLERS[entity]
    seen_keys: dict[str, tuple[int, str]] = {}
    try:
        for idx, row in enumerate(df.to_dict("records")):
            rnum = header_row + 2 + idx  # หมายเลขแถวตามไฟล์ต้นทาง
            sp = s.begin_nested()
            try:
                handler(cx, row, mp, mode, res, rnum, filename, sheet, seen_keys)
                sp.commit()
            except _DupSkip as e:
                sp.rollback()
                res.counts["skipped"] += 1
                res.issues.append(Issue("warning", str(e), rnum, e.column, filename, sheet))
            except _RowError as e:
                sp.rollback()
                res.counts["error"] += 1
                res.issues.append(Issue("error", str(e), rnum, e.column, filename, sheet))
            except ValueError as e:
                sp.rollback()
                res.counts["error"] += 1
                res.issues.append(Issue("error", str(e), rnum, None, filename, sheet))
        has_err = res.counts["error"] > 0
        if dry_run or (has_err and not skip_error_rows):
            nested.rollback()
            if not dry_run:
                res.issues.append(Issue("error", "ยกเลิกการนำเข้าทั้งไฟล์เนื่องจากมีข้อผิดพลาด (ข้อมูลเดิมไม่ถูกเปลี่ยนแปลง)", None, None, filename, sheet))
            return res
        nested.commit()
        s.add(
            ImportBatch(
                term_id=term_id,
                filename=filename,
                sheet=sheet,
                entity=entity,
                mapping=mp,
                row_count=len(df),
                result={"counts": res.counts, "issues": [i.as_dict() for i in res.issues[:500]]},
                raw_rows=df.head(5000).to_dict("records"),
                mode=mode,
                created_by=ctx.username,
            )
        )
        audit.log(s, ctx.username, "import", entity, {"file": filename, "sheet": sheet, "counts": res.counts})
        res.committed = True
        return res
    except Exception:
        if nested.is_active:
            nested.rollback()
        raise


def _dup_check(seen: dict, key: str, sig: str, rnum: int, label: str, col: str | None):
    if key in seen:
        prow, psig = seen[key]
        if psig != sig:
            raise _RowError(f"{label} “{key}” ซ้ำกับแถว {prow} แต่ข้อมูลต่างกัน — อาจหมายถึงคนละรายการ กรุณาตรวจสอบ", col)
        raise _DupSkip(f"{label} “{key}” ซ้ำกับแถว {prow} (ข้อมูลเหมือนกัน) — ข้ามแถวนี้", col)
    seen[key] = (rnum, sig)


# ---------------------------------------------------------------- handlers
def h_department(cx: _Ctx, row, mp, mode, res, rnum, file, sheet, seen):
    code, name = _get(row, mp, "code"), _get(row, mp, "name")
    if not code:
        raise _RowError("ไม่มีรหัสแผนก", mp.get("code"))
    if not name:
        raise _RowError("ไม่มีชื่อแผนก", mp.get("name"))
    _dup_check(seen, code, name, rnum, "รหัสแผนก", mp.get("code"))
    d = cx.s.scalars(select(Department).where(Department.code == code)).first()
    new = d is None
    if new:
        d = Department(code=code, name=name)
        cx.s.add(d)
    _apply(d, {"code": code, "name": name}, mode, new, res, f"แผนก {code}", rnum, file, sheet)


def h_teacher(cx: _Ctx, row, mp, mode, res, rnum, file, sheet, seen):
    code = _get(row, mp, "code")
    name = _person_name(row, mp)
    if not code:
        raise _RowError("ครูไม่มีรหัสประจำตัว", mp.get("code"))
    if not name:
        raise _RowError("ไม่มีชื่อครู", mp.get("name") or mp.get("first_name"))
    _dup_check(seen, code, M.norm(name), rnum, "รหัสครู", mp.get("code"))
    vals = {"code": code, "name": name}
    if mp.get("department"):
        vals["department_id"] = cx.dept(_get(row, mp, "department"), res, rnum, mp["department"], file, sheet)
    if mp.get("position"):
        vals["position"] = _get(row, mp, "position") or None
    try:
        if mp.get("max_hours_week"):
            vals["max_hours_week"] = M.parse_int(_get(row, mp, "max_hours_week"))
        if mp.get("max_hours_day"):
            vals["max_hours_day"] = M.parse_int(_get(row, mp, "max_hours_day"))
    except ValueError as e:
        raise _RowError(str(e), mp.get("max_hours_week"))
    if mp.get("meeting_member"):
        try:
            b = M.parse_bool(_get(row, mp, "meeting_member"))
        except ValueError as e:
            raise _RowError(str(e), mp["meeting_member"])
        vals["is_meeting_member"] = bool(b)
    t = cx.s.scalars(select(Teacher).where(Teacher.code == code)).first()
    new = t is None
    if not new and M.norm(t.name) != M.norm(name):
        res.issues.append(Issue("warning", f"รหัสครู {code} มีอยู่แล้วในชื่อ “{t.name}” แต่ไฟล์ระบุ “{name}” — อาจเป็นคนละบุคคล กรุณาตรวจสอบ", rnum, mp.get("name"), file, sheet))
    if new:
        t = Teacher(code=code, name=name)
        cx.s.add(t)
    _apply(t, vals, mode, new, res, f"ครู {code}", rnum, file, sheet)
    cx.s.flush()
    q = _get(row, mp, "qualified_courses")
    if q:
        for cc in [x.strip() for x in q.replace(";", ",").split(",") if x.strip()]:
            if not cx.s.scalars(select(TeacherQualification).where(TeacherQualification.teacher_id == t.id, TeacherQualification.course_code == cc)).first():
                cx.s.add(TeacherQualification(teacher_id=t.id, course_code=cc))


def h_room(cx: _Ctx, row, mp, mode, res, rnum, file, sheet, seen):
    code = _get(row, mp, "code")
    if not code:
        raise _RowError("ไม่มีรหัสห้อง", mp.get("code"))
    _dup_check(seen, code, _get(row, mp, "number") + _get(row, mp, "name"), rnum, "รหัสห้อง", mp.get("code"))
    vals = {"code": code}
    for f in ("name", "number", "equipment"):
        if mp.get(f):
            vals[f] = _get(row, mp, f) or None
    if mp.get("room_type"):
        rt = cx.room_type(_get(row, mp, "room_type"), mp["room_type"])
        vals["room_type_id"] = cx.room_types[rt].id if rt else None
    if mp.get("capacity"):
        try:
            vals["capacity"] = M.parse_int(_get(row, mp, "capacity"))
        except ValueError as e:
            raise _RowError(str(e), mp["capacity"])
    if mp.get("department"):
        vals["department_id"] = cx.dept(_get(row, mp, "department"), res, rnum, mp["department"], file, sheet)
    r = cx.s.scalars(select(Room).where(Room.code == code)).first()
    new = r is None
    if new:
        r = Room(code=code)
        cx.s.add(r)
    _apply(r, vals, mode, new, res, f"ห้อง {code}", rnum, file, sheet)


def h_course(cx: _Ctx, row, mp, mode, res, rnum, file, sheet, seen):
    code, name = _get(row, mp, "code"), _get(row, mp, "name")
    if not code:
        raise _RowError("รายวิชาไม่มีรหัสวิชา", mp.get("code"))
    if not name:
        raise _RowError("รายวิชาไม่มีชื่อวิชา", mp.get("name"))
    _dup_check(seen, code, M.norm(name), rnum, "รหัสวิชา", mp.get("code"))
    vals: dict = {"code": code, "name": name, "term_id": cx.term_id}
    try:
        th = M.parse_float(_get(row, mp, "theory_hours")) if mp.get("theory_hours") else None
        pr = M.parse_float(_get(row, mp, "practice_hours")) if mp.get("practice_hours") else None
        cr = M.parse_float(_get(row, mp, "credits")) if mp.get("credits") else None
        if mp.get("tpn") and _get(row, mp, "tpn"):
            t3 = M.parse_tpn(_get(row, mp, "tpn"))
            if (th is not None and th != t3[0]) or (pr is not None and pr != t3[1]):
                raise _RowError("ชั่วโมงทฤษฎี/ปฏิบัติไม่สอดคล้องกับค่า ท-ป-น ในแถวเดียวกัน", mp.get("tpn"))
            th, pr, cr = t3
        if th is not None:
            vals["theory_hours"] = th
        if pr is not None:
            vals["practice_hours"] = pr
        if cr is not None:
            vals["credits"] = cr
        if mp.get("total_periods"):
            vals["total_periods"] = M.parse_int(_get(row, mp, "total_periods"))
        if mp.get("consecutive"):
            vals["consecutive"] = M.parse_int(_get(row, mp, "consecutive"))
    except ValueError as e:
        raise _RowError(str(e))
    ctype = M.parse_course_type(_get(row, mp, "course_type")) if mp.get("course_type") else None
    if ctype == "unknown":
        res.issues.append(Issue("warning", f"ไม่สามารถระบุประเภทวิชาจาก “{_get(row, mp, 'course_type')}” — ต้องยืนยันประเภทวิชาก่อนจัดตาราง", rnum, mp.get("course_type"), file, sheet))
        vals["course_type"], vals["type_confirmed"] = None, False
    elif ctype:
        vals["course_type"], vals["type_confirmed"] = ctype, True
    else:
        th_ = vals.get("theory_hours")
        pr_ = vals.get("practice_hours")
        if "กิจกรรม" in name:
            vals["course_type"], vals["type_confirmed"] = "activity", False
            res.issues.append(Issue("warning", f"วิชา {code} คาดว่าเป็นรายวิชากิจกรรมจากชื่อวิชา — กรุณายืนยันประเภทวิชา", rnum, mp.get("name"), file, sheet))
        elif th_ is not None or pr_ is not None:
            th_, pr_ = th_ or 0, pr_ or 0
            vals["course_type"] = "mixed" if th_ > 0 and pr_ > 0 else ("practice" if pr_ > 0 else "theory")
            vals["type_confirmed"] = True
        else:
            vals["course_type"], vals["type_confirmed"] = None, False
    if mp.get("practice_room_types") and _get(row, mp, "practice_room_types"):
        codes = []
        for part in _get(row, mp, "practice_room_types").replace(";", ",").split(","):
            if part.strip():
                codes.append(cx.room_type(part, mp["practice_room_types"]))
        vals["practice_room_types"] = ",".join(c for c in codes if c)
    if mp.get("department"):
        vals["department_id"] = cx.dept(_get(row, mp, "department"), res, rnum, mp["department"], file, sheet)
    if mp.get("level"):
        vals["level"] = _get(row, mp, "level") or None
    if mp.get("is_general") and _get(row, mp, "is_general"):
        try:
            vals["is_general"] = M.parse_bool(_get(row, mp, "is_general"))
        except ValueError as e:
            raise _RowError(str(e), mp["is_general"])
    c = cx.s.scalars(select(Course).where(Course.term_id == cx.term_id, Course.code == code)).first()
    new = c is None
    if new:
        c = Course(code=code, name=name, term_id=cx.term_id)
        cx.s.add(c)
    _apply(c, vals, mode, new, res, f"รายวิชา {code}", rnum, file, sheet)


def h_group(cx: _Ctx, row, mp, mode, res, rnum, file, sheet, seen):
    code = _get(row, mp, "code")
    if not code:
        raise _RowError("ไม่มีรหัสกลุ่มเรียน", mp.get("code"))
    _dup_check(seen, code, _get(row, mp, "name"), rnum, "รหัสกลุ่มเรียน", mp.get("code"))
    vals: dict = {"code": code, "term_id": cx.term_id}
    if mp.get("name"):
        vals["name"] = _get(row, mp, "name") or None
    if mp.get("level"):
        vals["level"] = _get(row, mp, "level") or None
    if mp.get("department"):
        vals["department_id"] = cx.dept(_get(row, mp, "department"), res, rnum, mp["department"], file, sheet)
    if mp.get("size"):
        try:
            vals["size"] = M.parse_int(_get(row, mp, "size"))
        except ValueError as e:
            raise _RowError(str(e), mp["size"])
    if mp.get("advisor") and _get(row, mp, "advisor"):
        try:
            vals["advisor_teacher_id"] = cx.teacher(_get(row, mp, "advisor"), mp["advisor"]).id
        except _NameMatched as nm:
            vals["advisor_teacher_id"] = nm.teacher.id
            res.issues.append(Issue("warning", str(nm), rnum, mp["advisor"], file, sheet))
    g = cx.s.scalars(select(ClassGroup).where(ClassGroup.term_id == cx.term_id, ClassGroup.code == code)).first()
    new = g is None
    if new:
        g = ClassGroup(code=code, term_id=cx.term_id)
        cx.s.add(g)
    _apply(g, vals, mode, new, res, f"กลุ่มเรียน {code}", rnum, file, sheet)


def h_student(cx: _Ctx, row, mp, mode, res, rnum, file, sheet, seen):
    code = _get(row, mp, "code")
    name = _person_name(row, mp)
    if not code:
        raise _RowError("นักเรียนไม่มีรหัสประจำตัว", mp.get("code"))
    if not name:
        raise _RowError("ไม่มีชื่อนักเรียน", mp.get("name"))
    _dup_check(seen, code, M.norm(name), rnum, "รหัสนักเรียน", mp.get("code"))
    vals: dict = {"code": code, "name": name}
    if mp.get("department"):
        vals["department_id"] = cx.dept(_get(row, mp, "department"), res, rnum, mp["department"], file, sheet)
    if mp.get("status"):
        vals["status"] = _get(row, mp, "status") or None
    grp = None
    if mp.get("group"):
        gv = _get(row, mp, "group")
        if not gv:
            res.issues.append(Issue("warning", f"นักเรียน {code} ไม่ระบุกลุ่มเรียน", rnum, mp["group"], file, sheet))
        else:
            grp = cx.group(gv, mp["group"])
    st = cx.s.scalars(select(Student).where(Student.code == code)).first()
    new = st is None
    if not new and M.norm(st.name) != M.norm(name):
        res.issues.append(Issue("warning", f"รหัสนักเรียน {code} มีอยู่แล้วในชื่อ “{st.name}” แต่ไฟล์ระบุ “{name}” — อาจเป็นคนละบุคคล", rnum, mp.get("name"), file, sheet))
    if new:
        st = Student(code=code, name=name)
        cx.s.add(st)
    _apply(st, vals, mode, new, res, f"นักเรียน {code}", rnum, file, sheet)
    cx.s.flush()
    if grp is not None:
        m = cx.s.scalars(select(StudentMembership).where(StudentMembership.term_id == cx.term_id, StudentMembership.student_id == st.id)).first()
        if m is None:
            cx.s.add(StudentMembership(term_id=cx.term_id, student_id=st.id, group_id=grp.id))
        elif m.group_id != grp.id:
            if mode == "update":
                m.group_id = grp.id
            else:
                res.issues.append(Issue("warning", f"นักเรียน {code} อยู่กลุ่มอื่นแล้ว — ไม่ได้ย้ายกลุ่ม (ใช้โหมดอัปเดตหากต้องการ)", rnum, mp["group"], file, sheet))


def h_group_course(cx: _Ctx, row, mp, mode, res, rnum, file, sheet, seen):
    g = cx.group(_get(row, mp, "group"), mp.get("group"))
    c = cx.course(_get(row, mp, "course"), mp.get("course"))
    key = f"{g.code}|{c.code}"
    if key in seen:
        raise _RowError(f"การลงทะเบียน {c.code} ของกลุ่ม {g.code} ซ้ำกับแถว {seen[key][0]}")
    seen[key] = (rnum, "")
    if cx.s.scalars(select(GroupCourse).where(GroupCourse.group_id == g.id, GroupCourse.course_id == c.id)).first():
        res.counts["unchanged" if mode != "skip" else "skipped"] += 1
        return
    cx.s.add(GroupCourse(term_id=cx.term_id, group_id=g.id, course_id=c.id))
    res.counts["new"] += 1


def h_enrollment(cx: _Ctx, row, mp, mode, res, rnum, file, sheet, seen):
    st = cx.student(_get(row, mp, "student"), mp.get("student"))
    c = cx.course(_get(row, mp, "course"), mp.get("course"))
    sec = None
    if mp.get("group") and _get(row, mp, "group"):
        sec = cx.group(_get(row, mp, "group"), mp["group"]).id
    key = f"{st.code}|{c.code}"
    if key in seen:
        raise _RowError(f"นักเรียน {st.code} ลงทะเบียนวิชา {c.code} ซ้ำกับแถว {seen[key][0]}")
    seen[key] = (rnum, "")
    e = cx.s.scalars(select(Enrollment).where(Enrollment.term_id == cx.term_id, Enrollment.student_id == st.id, Enrollment.course_id == c.id)).first()
    new = e is None
    if new:
        e = Enrollment(term_id=cx.term_id, student_id=st.id, course_id=c.id)
        cx.s.add(e)
    _apply(e, {"section_group_id": sec}, mode, new, res, f"การลงทะเบียน {st.code}/{c.code}", rnum, file, sheet)


def h_assignment(cx: _Ctx, row, mp, mode, res, rnum, file, sheet, seen):
    try:
        t = cx.teacher(_get(row, mp, "teacher"), mp.get("teacher"))
    except _NameMatched as nm:
        t = nm.teacher
        res.issues.append(Issue("warning", str(nm), rnum, mp.get("teacher"), file, sheet))
    c = cx.course(_get(row, mp, "course"), mp.get("course"))
    g = cx.group(_get(row, mp, "group"), mp.get("group"))
    part = "all"
    if mp.get("part"):
        pv = M.norm(_get(row, mp, "part"))
        part = next((v for k, v in M.PART_WORDS.items() if M.norm(k) == pv), None)
        if part is None:
            raise _RowError(f"ส่วนที่สอน “{_get(row, mp, 'part')}” ไม่ถูกต้อง (ใช้ ทฤษฎี/ปฏิบัติ/ทั้งหมด)", mp["part"])
    try:
        periods = M.parse_int(_get(row, mp, "periods")) if mp.get("periods") else None
    except ValueError as e:
        raise _RowError(str(e), mp.get("periods"))
    key = f"{t.code}|{c.code}|{g.code}|{part}"
    if key in seen:
        raise _RowError(f"การมอบหมาย {t.code}/{c.code}/{g.code} ซ้ำกับแถว {seen[key][0]}")
    seen[key] = (rnum, "")
    a = cx.s.scalars(
        select(Assignment).where(Assignment.term_id == cx.term_id, Assignment.teacher_id == t.id, Assignment.course_id == c.id, Assignment.group_id == g.id, Assignment.part == part)
    ).first()
    new = a is None
    if new:
        a = Assignment(term_id=cx.term_id, teacher_id=t.id, course_id=c.id, group_id=g.id, part=part)
        cx.s.add(a)
    _apply(a, {"periods": periods}, mode, new, res, f"การมอบหมาย {t.code}/{c.code}/{g.code}", rnum, file, sheet)


def h_qualification(cx: _Ctx, row, mp, mode, res, rnum, file, sheet, seen):
    try:
        t = cx.teacher(_get(row, mp, "teacher"), mp.get("teacher"))
    except _NameMatched as nm:
        t = nm.teacher
        res.issues.append(Issue("warning", str(nm), rnum, mp.get("teacher"), file, sheet))
    cc = _get(row, mp, "course")
    if not cc:
        raise _RowError("ไม่มีรหัสวิชา", mp.get("course"))
    if cx.s.scalars(select(TeacherQualification).where(TeacherQualification.teacher_id == t.id, TeacherQualification.course_code == cc)).first():
        res.counts["unchanged"] += 1
        return
    cx.s.add(TeacherQualification(teacher_id=t.id, course_code=cc))
    res.counts["new"] += 1


def h_unavailable(cx: _Ctx, row, mp, mode, res, rnum, file, sheet, seen):
    try:
        t = cx.teacher(_get(row, mp, "teacher"), mp.get("teacher"))
    except _NameMatched as nm:
        t = nm.teacher
        res.issues.append(Issue("warning", str(nm), rnum, mp.get("teacher"), file, sheet))
    d = M.parse_day(_get(row, mp, "day"))
    if d is None or d not in cx.ts.days:
        raise _RowError(f"วัน “{_get(row, mp, 'day')}” ไม่ถูกต้องหรือไม่อยู่ในวันเรียน", mp.get("day"))
    try:
        periods = M.parse_periods(_get(row, mp, "periods"), cx.ts)
    except ValueError as e:
        raise _RowError(str(e), mp.get("periods"))
    if not periods:
        raise _RowError("ไม่ได้ระบุคาบ", mp.get("periods"))
    kind = M.norm(_get(row, mp, "kind")) if mp.get("kind") else ""
    preferred = kind in {M.norm(x) for x in ("ต้องการสอน", "สะดวก", "preferred", "prefer", "ว่าง")}
    for p in periods:
        exists = cx.s.scalars(
            select(TeacherUnavailable).where(
                TeacherUnavailable.term_id == cx.term_id, TeacherUnavailable.teacher_id == t.id, TeacherUnavailable.day == d, TeacherUnavailable.period == p, TeacherUnavailable.preferred == preferred
            )
        ).first()
        if exists:
            res.counts["unchanged"] += 1
        else:
            cx.s.add(TeacherUnavailable(term_id=cx.term_id, teacher_id=t.id, day=d, period=p, preferred=preferred))
            res.counts["new"] += 1


def h_joint(cx: _Ctx, row, mp, mode, res, rnum, file, sheet, seen):
    c = cx.course(_get(row, mp, "course"), mp.get("course"))
    codes = [x.strip() for x in _get(row, mp, "groups").replace(";", ",").replace("+", ",").split(",") if x.strip()]
    if len(codes) < 2:
        raise _RowError("การเรียนร่วมต้องมีอย่างน้อย 2 กลุ่ม", mp.get("groups"))
    gids = [cx.group(gc, mp.get("groups")).id for gc in codes]
    tid = None
    if mp.get("teacher") and _get(row, mp, "teacher"):
        try:
            tid = cx.teacher(_get(row, mp, "teacher"), mp["teacher"]).id
        except _NameMatched as nm:
            tid = nm.teacher.id
            res.issues.append(Issue("warning", str(nm), rnum, mp["teacher"], file, sheet))
    for j in cx.s.scalars(select(JointClass).where(JointClass.term_id == cx.term_id, JointClass.course_id == c.id)).all():
        if set(j.group_ids) & set(gids):
            if set(j.group_ids) == set(gids):
                res.counts["unchanged"] += 1
                return
            raise _RowError(f"กลุ่มเรียนบางกลุ่มอยู่ในการเรียนร่วมวิชา {c.code} ชุดอื่นแล้ว", mp.get("groups"))
    cx.s.add(JointClass(term_id=cx.term_id, course_id=c.id, teacher_id=tid, group_ids=sorted(gids), note=f"นำเข้าจาก {file}"))
    res.counts["new"] += 1


_HANDLERS = {
    "department": h_department,
    "teacher": h_teacher,
    "room": h_room,
    "course": h_course,
    "group": h_group,
    "student": h_student,
    "group_course": h_group_course,
    "enrollment": h_enrollment,
    "assignment": h_assignment,
    "qualification": h_qualification,
    "teacher_unavailable": h_unavailable,
    "joint": h_joint,
}
