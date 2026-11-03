"""แปลงข้อมูลในฐานข้อมูลให้เป็น “รายการสอนที่ต้องจัดจริง” (lessons) สำหรับตัวจัดตาราง

กติกาสำคัญ (ข้อสมมติฐานที่ปรับได้ในหน้าตั้งค่า):
- จำนวนคาบ = (ชั่วโมงทฤษฎี + ชั่วโมงปฏิบัติ) × อัตราแปลงชั่วโมง→คาบ (ค่าเริ่มต้น 1 ชั่วโมง = 1 คาบ 60 นาที)
  ต้องให้ผู้ดูแลยืนยันอัตราแปลงก่อนจัดตาราง หากรายวิชาระบุ “จำนวนคาบรวม” โดยตรง จะใช้ค่านั้น
- รายวิชาที่มีทั้งทฤษฎีและปฏิบัติ แยกเป็น 2 ส่วน ส่วนทฤษฎีจัดในห้องทฤษฎี ส่วนปฏิบัติจัดในห้องปฏิบัติ
- จำนวนคาบต่อเนื่อง: ใช้ค่าจากรายวิชา ถ้าไม่ระบุ จะจัดแต่ละส่วนเป็นช่วงเดียว (ถ้าไม่เกิน default_block_max)
  มิฉะนั้นแบ่งเป็นช่วงใกล้เคียงกันไม่เกิน default_block_max คาบ
- การเรียนร่วมเกิดจากข้อมูล JointClass เท่านั้น (ไม่สรุปเองจากรหัสวิชาเดียวกัน)
"""
from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import settings_service as S
from .models import (
    Assignment,
    ClassGroup,
    Course,
    Enrollment,
    GroupCourse,
    JointClass,
    Room,
    RoomType,
    StudentMembership,
    Teacher,
    TeacherQualification,
    TeacherUnavailable,
    RoomUnavailable,
)

PART_LABEL = {"theory": "ทฤษฎี", "practice": "ปฏิบัติ", "all": "ทั้งหมด"}


@dataclass
class Lesson:
    id: int
    kind: str  # class / homeroom
    course_id: int | None
    part: str
    teacher_ids: list[int]
    group_ids: list[int]
    blocks: list[int]  # ความยาวของแต่ละช่วงคาบต่อเนื่อง
    room_ids: list[int]  # ห้องที่ใช้ได้ (ว่าง = ไม่ต้องใช้ห้อง)
    needs_room: bool
    size: int
    label: str
    joint_id: int | None = None
    preferred_dept_rooms: list[int] = field(default_factory=list)

    @property
    def periods(self) -> int:
        return sum(self.blocks)


@dataclass
class FixedItem:
    kind: str  # meeting / activity
    day: int
    periods: list[int]
    teacher_ids: list[int]
    group_ids: list[int]
    course_id: int | None
    room_id: int | None
    label: str


@dataclass
class Issue:
    level: str  # error / warning / info
    code: str
    message: str
    fix: str = ""


@dataclass
class Problem:
    term_id: int
    ts: S.TimeStructure
    settings: dict
    lessons: list[Lesson]
    fixed: list[FixedItem]
    teachers: dict[int, dict]
    rooms: dict[int, dict]
    groups: dict[int, dict]
    courses: dict[int, dict]
    student_sets: list[tuple[frozenset[int], int]]  # (ชุดของ lesson ids, จำนวนนักเรียน)
    group_forbidden: dict[int, set[tuple[int, int]]]
    issues: list[Issue]
    student_sections: dict[int, set[tuple[int, int]]]  # student_id -> {(course_id, section_key)}

    @property
    def has_errors(self) -> bool:
        return any(i.level == "error" for i in self.issues)


def course_periods(c: Course, rate: float) -> dict[str, int]:
    """คืนจำนวนคาบแยกส่วน {'theory': n, 'practice': m} หรือ {'all': k}"""
    th, pr = c.theory_hours, c.practice_hours
    if th is None and pr is None:
        if c.total_periods:
            return {"all": int(c.total_periods)}
        return {}
    th, pr = th or 0, pr or 0
    out = {}
    if th > 0:
        out["theory"] = int(round(th * rate))
    if pr > 0:
        out["practice"] = int(round(pr * rate))
    return out


def split_blocks(n: int, consecutive: int | None, default_max: int) -> list[int]:
    if n <= 0:
        return []
    if consecutive and consecutive > 0:
        k, r = divmod(n, consecutive)
        return [consecutive] * k + ([r] if r else [])
    if n <= default_max:
        return [n]
    k = math.ceil(n / default_max)
    base, extra = divmod(n, k)
    return [base + 1] * extra + [base] * (k - extra)


AUTO_JOINT_NOTE = "เงื่อนไขข้อ 13: วิชาสามัญเรียนร่วม (สร้างอัตโนมัติ)"


def sync_general_joints(s: Session, term_id: int) -> int:
    """เงื่อนไขพิเศษข้อ 13 (เลือกใช้): จับคู่วิชาสามัญเรียนร่วม 2 กลุ่มให้อัตโนมัติ

    - จับคู่เฉพาะกลุ่มที่ลงทะเบียนวิชาเดียวกัน ครูคนเดียวกัน และข้อมูลการมอบหมายเหมือนกัน
    - ข้ามคู่ที่ไม่มีห้องจุผู้เรียนรวมได้ และไม่แตะการเรียนร่วมที่ผู้ดูแลกำหนดเอง
    - ปิดเงื่อนไข = ลบเฉพาะการเรียนร่วมที่ระบบสร้างอัตโนมัติ
    คืนจำนวนคู่ที่ใช้อยู่
    """
    on = bool(S.get(s, "general_joint", term_id))
    existing = s.scalars(select(JointClass).where(JointClass.term_id == term_id)).all()
    auto = [j for j in existing if j.note == AUTO_JOINT_NOTE]
    manual_pairs = {(j.course_id, g) for j in existing if j.note != AUTO_JOINT_NOTE for g in j.group_ids}
    desired: set[tuple[int, tuple]] = set()
    if on:
        prefixes = S.get(s, "general_prefixes", term_id) or []
        maxg = int(S.get(s, "general_joint_max_groups", term_id) or 2)
        room_rule = bool(S.get(s, "room_type_rule", term_id))
        rts = {rt.id: rt for rt in s.scalars(select(RoomType)).all()}
        room_list = [(rts[r.room_type_id].kind if r.room_type_id in rts else None, rts[r.room_type_id].code if r.room_type_id in rts else None, r.capacity or 0)
                     for r in s.scalars(select(Room).where(Room.active.is_(True))).all()]
        rate = float(S.get(s, "hour_to_period_rate", term_id) or 1)

        def max_cap(c: Course) -> int:
            """ความจุสูงสุดของห้องที่รายวิชานี้ใช้ได้ (ทุกส่วนต้องมีห้องจุได้) — ใช้กติกาเดียวกับการสร้างรายการสอน"""
            types = [x for x in (c.practice_room_types or "").split(",") if x]
            best = None
            for part in course_periods(c, rate) or {"all": 0}:
                if not room_rule:
                    ok = [cap for k, code, cap in room_list if (code in types if types and part != "theory" else k != "other")]
                elif part == "theory" or (part == "all" and c.course_type == "theory"):
                    ok = [cap for k, code, cap in room_list if k == "theory"]
                else:
                    ok = [cap for k, code, cap in room_list if (code in types if types else k == "practice")]
                m = max(ok) if ok else 0
                best = m if best is None else min(best, m)
            return best or 0
        members: dict[int, int] = {}
        for m in s.scalars(select(StudentMembership).where(StudentMembership.term_id == term_id)).all():
            members[m.group_id] = members.get(m.group_id, 0) + 1
        gsize = {g.id: (g.size if g.size is not None else members.get(g.id, 0))
                 for g in s.scalars(select(ClassGroup).where(ClassGroup.term_id == term_id)).all()}
        gcode = {g.id: g.code for g in s.scalars(select(ClassGroup).where(ClassGroup.term_id == term_id)).all()}
        regs = {(x.group_id, x.course_id) for x in s.scalars(select(GroupCourse).where(GroupCourse.term_id == term_id)).all()}
        courses = {c.id: c for c in s.scalars(select(Course).where(Course.term_id == term_id, Course.active.is_(True))).all()}
        sig: dict[tuple[int, int], list] = {}
        for a in s.scalars(select(Assignment).where(Assignment.term_id == term_id)).all():
            sig.setdefault((a.course_id, a.group_id), []).append((a.teacher_id, a.part, a.periods))
        bucket: dict[tuple, list[int]] = {}
        for (cid, gid), lst in sig.items():
            c = courses.get(cid)
            if c is None or c.course_type == "activity" or (gid, cid) not in regs or (cid, gid) in manual_pairs:
                continue
            if not S.is_general_course(c.code, prefixes, c.is_general):
                continue
            bucket.setdefault((cid, tuple(sorted(lst))), []).append(gid)
        for (cid, _sig), gids in bucket.items():
            gids = sorted(gids, key=lambda g: gcode.get(g, ""))
            i = 0
            while i + 1 < len(gids):
                chunk = gids[i:i + maxg]
                cap = max_cap(courses[cid])
                while len(chunk) >= 2 and sum(gsize.get(g, 0) for g in chunk) > cap:
                    chunk = chunk[:-1]
                if len(chunk) >= 2:
                    desired.add((cid, tuple(chunk)))
                    i += len(chunk)
                else:
                    i += 1
    have = {(j.course_id, tuple(sorted(j.group_ids))): j for j in auto}
    for k, j in have.items():
        if k not in desired:
            s.delete(j)
    for cid, gids in desired:
        if (cid, tuple(sorted(gids))) not in have:
            teacher = sig[(cid, gids[0])][0][0] if sig.get((cid, gids[0])) else None
            s.add(JointClass(term_id=term_id, course_id=cid, teacher_id=teacher, group_ids=sorted(gids), note=AUTO_JOINT_NOTE))
    s.flush()
    return len(desired)


def build(s: Session, term_id: int, department_id: int | None = None) -> Problem:
    ts = S.time_structure(s, term_id)
    st = {k: S.get(s, k, term_id) for k in S.DEFAULTS}
    rate = float(st["hour_to_period_rate"] or 1)
    issues: list[Issue] = []

    if not st["hour_to_period_confirmed"]:
        issues.append(Issue("error", "rate_unconfirmed", f"ยังไม่ได้ยืนยันกติกาการแปลงชั่วโมงเป็นคาบ (ปัจจุบัน 1 ชั่วโมง = {rate:g} คาบ)", "ไปที่หน้า “ตั้งค่า” แล้วยืนยันกติกาการแปลงหน่วย"))

    rtypes = {rt.id: rt for rt in s.scalars(select(RoomType)).all()}

    teachers = {}
    for t in s.scalars(select(Teacher).where(Teacher.active.is_(True))).all():
        teachers[t.id] = {
            "code": t.code, "name": t.name, "max_week": t.max_hours_week, "max_day": t.max_hours_day,
            "meeting": bool(t.is_meeting_member), "dept": t.department_id,
            "unavail": set(), "preferred": set(), "quals": set(),
        }
    for u in s.scalars(select(TeacherUnavailable).where(TeacherUnavailable.term_id == term_id)).all():
        if u.teacher_id in teachers:
            teachers[u.teacher_id]["preferred" if u.preferred else "unavail"].add((u.day, u.period))
    for q in s.scalars(select(TeacherQualification)).all():
        if q.teacher_id in teachers:
            teachers[q.teacher_id]["quals"].add(q.course_code)

    rooms = {}
    for r in s.scalars(select(Room).where(Room.active.is_(True))).all():
        rt = rtypes.get(r.room_type_id)
        rooms[r.id] = {"code": r.code, "label": r.label, "type": rt.code if rt else None, "kind": rt.kind if rt else None,
                       "capacity": r.capacity, "dept": r.department_id, "unavail": set()}
    for u in s.scalars(select(RoomUnavailable).where(RoomUnavailable.term_id == term_id)).all():
        if u.room_id in rooms:
            rooms[u.room_id]["unavail"].add((u.day, u.period))

    gq = select(ClassGroup).where(ClassGroup.term_id == term_id)
    groups = {}
    members: dict[int, list[int]] = defaultdict(list)
    for m in s.scalars(select(StudentMembership).where(StudentMembership.term_id == term_id)).all():
        members[m.group_id].append(m.student_id)
    for g in s.scalars(gq).all():
        groups[g.id] = {"code": g.code, "name": g.label, "dept": g.department_id, "advisor": g.advisor_teacher_id,
                        "size": g.size if g.size is not None else len(members.get(g.id, [])), "students": members.get(g.id, [])}

    courses = {}
    course_objs = {}
    for c in s.scalars(select(Course).where(Course.term_id == term_id, Course.active.is_(True))).all():
        course_objs[c.id] = c
        courses[c.id] = {"code": c.code, "name": c.name, "type": c.course_type, "confirmed": c.type_confirmed,
                         "parts": course_periods(c, rate), "consecutive": c.consecutive,
                         "room_types": [x for x in (c.practice_room_types or "").split(",") if x],
                         "dept": c.department_id,
                         "general": S.is_general_course(c.code, st["general_prefixes"], c.is_general)}

    reg = defaultdict(set)  # group -> course ids
    for gc in s.scalars(select(GroupCourse).where(GroupCourse.term_id == term_id)).all():
        if gc.group_id in groups and gc.course_id in courses:
            reg[gc.group_id].add(gc.course_id)

    assigns = defaultdict(list)  # (course, group) -> [Assignment]
    for a in s.scalars(select(Assignment).where(Assignment.term_id == term_id)).all():
        assigns[(a.course_id, a.group_id)].append(a)

    sync_general_joints(s, term_id)
    joints = s.scalars(select(JointClass).where(JointClass.term_id == term_id)).all()

    # ---------------------------------------------------------- sections
    # section key = (course_id, tuple(sorted(group_ids)))
    sections: dict[tuple[int, tuple], dict] = {}
    in_joint: set[tuple[int, int]] = set()
    for j in joints:
        gids = tuple(sorted(g for g in j.group_ids if g in groups))
        if j.course_id not in courses or len(gids) < 2:
            continue
        sections[(j.course_id, gids)] = {"joint_id": j.id, "teacher": j.teacher_id}
        for g in gids:
            in_joint.add((j.course_id, g))
    for g, cids in reg.items():
        for c in cids:
            if (c, g) not in in_joint:
                sections[(c, (g,))] = {"joint_id": None, "teacher": None}

    # ---------------------------------------------------------- นักเรียน -> sections
    section_of: dict[tuple[int, int], tuple] = {}  # (course, group) -> section key
    for key in sections:
        for g in key[1]:
            section_of[(key[0], g)] = key
    enrolls = defaultdict(list)
    for e in s.scalars(select(Enrollment).where(Enrollment.term_id == term_id)).all():
        enrolls[e.student_id].append(e)
    stu_group = {}
    for g, sids in members.items():
        for sid in sids:
            stu_group[sid] = g
    student_sections: dict[int, set] = {}
    section_students: dict[tuple, set] = defaultdict(set)
    bad_enroll = 0
    for sid in set(stu_group) | set(enrolls):
        secs = set()
        if enrolls.get(sid):
            for e in enrolls[sid]:
                if e.course_id not in courses:
                    continue
                g = e.section_group_id or stu_group.get(sid)
                key = section_of.get((e.course_id, g)) if g else None
                if key is None:
                    bad_enroll += 1
                    continue
                secs.add(key)
        else:
            g = stu_group.get(sid)
            for c in reg.get(g, set()):
                key = section_of.get((c, g))
                if key:
                    secs.add(key)
        student_sections[sid] = secs
        for k in secs:
            section_students[k].add(sid)
    if bad_enroll:
        issues.append(Issue("error", "enroll_no_section", f"มีการลงทะเบียนรายบุคคล {bad_enroll} รายการที่ไม่สามารถระบุกลุ่มเรียน (section) ที่เปิดสอนรายวิชานั้นได้",
                            "ระบุกลุ่มที่เข้าเรียนในไฟล์ลงทะเบียน หรือเพิ่มการลงทะเบียนรายวิชาให้กลุ่มเรียนนั้น"))

    # ---------------------------------------------------------- lessons
    lessons: list[Lesson] = []
    lid = 0
    default_max = int(st["default_block_max"] or 4)
    maxblk = ts.max_block()
    activity_sections = []
    room_rule = bool(st.get("room_type_rule"))
    act_on = bool((st.get("activity") or {}).get("enabled"))
    for key, info in sections.items():
        cid, gids = key
        c = courses[cid]
        if department_id is not None and not any(groups[g]["dept"] == department_id for g in gids):
            continue
        cname = f"{c['code']} {c['name']}"
        gcodes = ",".join(groups[g]["code"] for g in gids)
        if c["type"] == "activity" and act_on:
            activity_sections.append((key, info))
            continue
        if not c["confirmed"] or c["type"] is None:
            issues.append(Issue("error", "type_unconfirmed", f"รายวิชา {cname} ยังไม่ได้ยืนยันประเภทวิชา", "ยืนยันประเภทวิชาที่หน้าจัดการข้อมูล > รายวิชา"))
            continue
        parts = c["parts"]
        if not parts:
            issues.append(Issue("error", "no_periods", f"รายวิชา {cname} ไม่มีจำนวนชั่วโมง/จำนวนคาบ", "เพิ่มชั่วโมงทฤษฎี/ปฏิบัติ หรือจำนวนคาบรวมให้รายวิชา"))
            continue
        # รวมการมอบหมายของทุกกลุ่มใน section
        alist = []
        for g in gids:
            alist.extend(assigns.get((cid, g), []))
        if not alist:
            issues.append(Issue("error", "no_teacher", f"รายวิชา {cname} กลุ่ม {gcodes} ไม่มีครูผู้รับผิดชอบ", "เพิ่มข้อมูลการมอบหมายครูผู้สอน"))
            continue
        if len(gids) > 1:
            # การเรียนร่วม: ทุกกลุ่มต้องมีผู้สอนชุดเดียวกัน
            per_group = {g: sorted((a.teacher_id, a.part, a.periods) for a in assigns.get((cid, g), [])) for g in gids}
            vals = list(per_group.values())
            if any(v != vals[0] for v in vals):
                issues.append(Issue("error", "joint_teacher_mismatch", f"การเรียนร่วมวิชา {cname} กลุ่ม {gcodes} มีข้อมูลผู้สอนไม่ตรงกันระหว่างกลุ่ม", "แก้ไขการมอบหมายให้ทุกกลุ่มที่เรียนร่วมมีครูและจำนวนคาบเดียวกัน"))
                continue
            if info["teacher"] and info["teacher"] not in [a.teacher_id for a in alist]:
                issues.append(Issue("error", "joint_teacher_mismatch", f"การเรียนร่วมวิชา {cname} ระบุครูที่ไม่ได้รับมอบหมาย", "ตรวจสอบข้อมูลการเรียนร่วม"))
                continue
            alist = list(assigns.get((cid, gids[0]), []))
        # จำนวนผู้เรียน = สมาชิกของกลุ่มทั้งหมด + นักเรียนจากกลุ่มอื่นที่ลงทะเบียนเข้าเรียน section นี้
        outsiders = sum(1 for sid in section_students.get(key, set()) if stu_group.get(sid) not in gids)
        size = sum(groups[g]["size"] or 0 for g in gids) + outsiders
        # แจกจ่ายจำนวนคาบตามส่วน
        part_loads: dict[str, list[tuple[int, int]]] = defaultdict(list)  # part -> [(teacher, periods)]
        bad = False
        for a in alist:
            if a.teacher_id not in teachers:
                issues.append(Issue("error", "teacher_missing", f"การมอบหมายวิชา {cname} อ้างอิงครูที่ไม่มีอยู่/ไม่ใช้งาน", "ตรวจสอบข้อมูลครู"))
                bad = True
                continue
            if a.part == "all":
                if a.periods is not None and len(parts) > 1:
                    issues.append(Issue("error", "part_ambiguous", f"การมอบหมายวิชา {cname} (ครู {teachers[a.teacher_id]['name']}) ระบุจำนวนคาบแต่ไม่ระบุส่วน ทฤษฎี/ปฏิบัติ", "ระบุส่วนที่สอนในข้อมูลการมอบหมาย"))
                    bad = True
                    continue
                for p, n in parts.items():
                    part_loads[p].append((a.teacher_id, a.periods if a.periods is not None else n))
            else:
                target = a.part if a.part in parts else ("all" if "all" in parts else None)
                if target is None:
                    issues.append(Issue("error", "part_missing", f"การมอบหมายวิชา {cname} ระบุส่วน{PART_LABEL[a.part]} แต่รายวิชาไม่มีชั่วโมงส่วนนี้", "ตรวจสอบชั่วโมงทฤษฎี/ปฏิบัติของรายวิชา"))
                    bad = True
                    continue
                part_loads[target].append((a.teacher_id, a.periods if a.periods is not None else parts[target]))
        if bad:
            continue
        for p, n in parts.items():
            got = sum(x[1] for x in part_loads.get(p, []))
            if got != n:
                issues.append(Issue("error", "period_mismatch", f"วิชา {cname} กลุ่ม {gcodes} ส่วน{PART_LABEL[p]}: จำนวนคาบที่มอบหมาย ({got}) ไม่เท่ากับจำนวนคาบตามหลักสูตร ({n})", "แก้ไขจำนวนคาบในข้อมูลการมอบหมายผู้สอน"))
                bad = True
        if bad:
            continue
        for p, loads in part_loads.items():
            types = c["room_types"]
            if not room_rule:
                # ไม่ได้เลือกใช้เงื่อนไข “แยกห้องทฤษฎี/ปฏิบัติ”: ใช้ห้องใดก็ได้ (ยกเว้นห้องประชุม)
                # ถ้ารายวิชาระบุประเภทห้องที่ต้องใช้ในส่วนปฏิบัติไว้ ยังคงใช้ตามข้อมูลรายวิชา
                if types and p != "theory":
                    cand = [r for r, ri in rooms.items() if ri["type"] in types]
                else:
                    cand = [r for r, ri in rooms.items() if ri["kind"] != "other"]
                room_need = "any"
            elif p == "theory" or (p == "all" and c["type"] == "theory"):
                cand = [r for r, ri in rooms.items() if ri["kind"] == "theory"]
                room_need = "theory"
            else:
                if types:
                    cand = [r for r, ri in rooms.items() if ri["type"] in types]
                else:
                    cand = [r for r, ri in rooms.items() if ri["kind"] == "practice"]
                room_need = "practice"
            cap_ok = [r for r in cand if rooms[r]["capacity"] is not None and rooms[r]["capacity"] >= size]
            if not cand:
                issues.append(Issue("error", "no_room_type", f"วิชา {cname} ส่วน{PART_LABEL[p]} ต้องใช้ห้อง{ {'theory': 'ทฤษฎี', 'practice': 'ปฏิบัติ'}.get(room_need, 'เรียน')} แต่ไม่มีห้องประเภทที่ตรงกัน", "เพิ่มห้องเรียนประเภทที่ต้องใช้ หรือแก้ไขประเภทห้องที่รายวิชาต้องการ"))
                continue
            if not cap_ok:
                issues.append(Issue("error", "no_room_capacity", f"วิชา {cname} กลุ่ม {gcodes} มีผู้เรียน {size} คน แต่ไม่มีห้องที่เหมาะสมซึ่งมีความจุเพียงพอ", "เพิ่มห้องที่มีความจุมากขึ้น หรือแก้ไขข้อมูลความจุ/การเรียนร่วม"))
                continue
            for tid, n in loads:
                blocks = split_blocks(n, c["consecutive"], default_max)
                if any(b > maxblk for b in blocks):
                    issues.append(Issue("error", "block_too_long", f"วิชา {cname} ต้องเรียนต่อเนื่อง {max(blocks)} คาบ แต่โครงสร้างเวลามีช่วงต่อเนื่องยาวสุด {maxblk} คาบ", "แก้ไขจำนวนคาบต่อเนื่องของรายวิชา"))
                    continue
                if c["consecutive"] and n % c["consecutive"]:
                    issues.append(Issue("warning", "block_remainder", f"วิชา {cname}: จำนวนคาบ {n} หารด้วยคาบต่อเนื่อง {c['consecutive']} ไม่ลงตัว จะจัดช่วงสุดท้าย {n % c['consecutive']} คาบ"))
                lid += 1
                dept_rooms = [r for r in cap_ok if rooms[r]["dept"] is not None and rooms[r]["dept"] == c["dept"]]
                lessons.append(Lesson(lid, "class", cid, p, [tid], list(gids), blocks, cap_ok, True, size,
                                      f"{cname} [{PART_LABEL[p]}] {gcodes}", info["joint_id"], dept_rooms))

    # ---------------------------------------------------------- homeroom
    hr = st["homeroom"]
    if hr.get("enabled"):
        for g, gi in groups.items():
            if department_id is not None and gi["dept"] != department_id:
                continue
            if not gi["advisor"] or gi["advisor"] not in teachers:
                issues.append(Issue("error", "no_advisor", f"กลุ่มเรียน {gi['code']} ไม่มีครูที่ปรึกษา จึงจัดโฮมรูมไม่ได้", "ระบุครูที่ปรึกษาในข้อมูลกลุ่มเรียน"))
                continue
            need_room = bool(hr.get("needs_room"))
            cand = []
            if need_room:
                cand = [r for r, ri in rooms.items() if ri["kind"] == "theory" and (ri["capacity"] or 0) >= (gi["size"] or 0)]
                if not cand:
                    issues.append(Issue("error", "no_room_capacity", f"ไม่มีห้องสำหรับโฮมรูมกลุ่ม {gi['code']}"))
                    continue
            lid += 1
            lessons.append(Lesson(lid, "homeroom", None, "all", [gi["advisor"]], [g], [1] * int(hr.get("periods_per_week", 1)),
                                  cand, need_room, gi["size"] or 0, f"โฮมรูม {gi['code']}"))

    # ---------------------------------------------------------- fixed: meeting / activity
    fixed: list[FixedItem] = []
    mt = st["meeting"]
    if mt.get("enabled"):
        members_ = [t for t, ti in teachers.items() if ti["meeting"]]
        if members_:
            fixed.append(FixedItem("meeting", int(mt["day"]), list(mt["periods"]), members_, [], None, mt.get("room_id"), mt.get("title") or "ประชุม"))
        else:
            issues.append(Issue("info", "no_meeting_member", "ยังไม่มีครูที่กำหนดให้เข้าประชุมหัวหน้าแผนก/หัวหน้างาน"))
    act = st["activity"]
    group_forbidden: dict[int, set] = defaultdict(set)
    if act.get("enabled"):
        aslots = {(int(act["day"]), p) for p in act["periods"]}
        if act.get("lock_all_groups"):
            for g in groups:
                group_forbidden[g] |= aslots
        bucket: dict[tuple, list[int]] = defaultdict(list)
        for key, info in activity_sections:
            cid, gids = key
            tids = set()
            for g in gids:
                tids |= {a.teacher_id for a in assigns.get((cid, g), []) if a.teacher_id in teachers}
            bucket[(cid, tuple(sorted(tids)))].extend(gids)
            n = sum(courses[cid]["parts"].values()) if courses[cid]["parts"] else None
            if n is not None and n != len(act["periods"]):
                issues.append(Issue("warning", "activity_periods", f"รายวิชากิจกรรม {courses[cid]['code']} มี {n} คาบ แต่ช่วงกิจกรรมที่กำหนดมี {len(act['periods'])} คาบ (ระบบจะจัดตามช่วงกิจกรรม)"))
        for (cid, tids), gids in bucket.items():
            if not tids:
                issues.append(Issue("warning", "activity_no_teacher", f"รายวิชากิจกรรม {courses[cid]['code']} ไม่มีครูผู้รับผิดชอบ (จะแสดงในตารางโดยไม่มีชื่อครู)"))
            fixed.append(FixedItem("activity", int(act["day"]), list(act["periods"]), list(tids), sorted(set(gids)), cid, None,
                                   f"{courses[cid]['code']} {courses[cid]['name']}"))
    # ชุดนักเรียนสำหรับตรวจการชน (ลดจำนวนข้อจำกัดด้วยการรวมนักเรียนที่เรียนเหมือนกัน)
    sec_lessons: dict[tuple, list[int]] = defaultdict(list)
    for L in lessons:
        if L.kind == "class":
            sec_lessons[(L.course_id, tuple(sorted(L.group_ids)))].append(L.id)
    group_lessonset: dict[int, set[int]] = defaultdict(set)
    for L in lessons:
        for g in L.group_ids:
            group_lessonset[g].add(L.id)
    sig_count: dict[frozenset, int] = defaultdict(int)
    for sid, secs in student_sections.items():
        ids = set()
        for k in secs:
            ids.update(sec_lessons.get(k, []))
        g = stu_group.get(sid)
        for L in lessons:
            if L.kind == "homeroom" and g in L.group_ids:
                ids.add(L.id)
        if ids:
            sig_count[frozenset(ids)] += 1
    student_sets = []
    for sig, cnt in sig_count.items():
        if any(sig <= gs for gs in group_lessonset.values()):
            continue  # ถูกบังคับแล้วโดยข้อจำกัดระดับกลุ่มเรียน
        student_sets.append((sig, cnt))

    return Problem(term_id, ts, st, lessons, fixed, teachers, rooms, groups, courses, student_sets, group_forbidden,
                   issues, {k: v for k, v in student_sections.items()})
