"""ระบบตรวจสอบตารางอิสระ (หัวข้อ 11)

ทำงานแยกจากตัวจัดตาราง: อ่านรายการตารางที่บันทึกในฐานข้อมูล และข้อมูลหลักสูตร/การลงทะเบียน/
การมอบหมายโดยตรง แล้วตรวจสอบทุกเงื่อนไขซ้ำ — ป้องกันความผิดพลาดที่อาจเกิดจากตัวจัดตารางเอง
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import settings_service as S
from .models import (
    Assignment,
    ClassGroup,
    Course,
    Enrollment,
    Entry,
    GroupCourse,
    JointClass,
    Room,
    RoomType,
    RoomUnavailable,
    StudentMembership,
    Teacher,
    TeacherUnavailable,
    Timetable,
)

CHECK_NAMES = {
    1: "จำนวนคาบของทุกรายวิชาครบ",
    2: "จำนวนคาบของแต่ละกลุ่มเรียนถูกต้อง",
    3: "ครูไม่มีรายการสอนชนกัน",
    4: "ห้องเรียนไม่ถูกใช้ซ้ำซ้อน",
    5: "นักเรียน/กลุ่มเรียนไม่เรียนชนกัน",
    6: "ใช้ห้องถูกประเภท",
    7: "ไม่มีการจัดเรียนในคาบพัก",
    8: "ไม่มีรายวิชาที่ไม่ได้ลงทะเบียน",
    9: "ทุกรายวิชามีผู้สอน",
    10: "ครูไม่สอนเกินชั่วโมงสูงสุด",
    11: "ครูสอนเฉพาะรายวิชาที่ได้รับมอบหมาย",
    12: "ห้องเรียนมีความจุเพียงพอ",
    13: "จำนวนคาบต่อวันไม่เกินข้อกำหนด",
    14: "โฮมรูมครบทุกกลุ่ม",
    15: "การประชุมถูกจัดตามข้อกำหนด",
    16: "กิจกรรมถูกจัดตามเวลาที่กำหนด",
    17: "การเรียนร่วมมีความสัมพันธ์ถูกต้อง",
    18: "ไม่มีรายการซ้ำโดยไม่ได้ตั้งใจ",
    19: "ทุกรายการเชื่อมโยงครู ห้อง และกลุ่มเรียน",
    20: "ทุกรายการอยู่ในเวลาเรียนที่อนุญาต",
    21: "ไม่จัดในเวลาที่ครูไม่สะดวก/ห้องไม่พร้อมใช้",
    22: "การเรียนต่อเนื่องตามข้อกำหนด",
}


@dataclass
class Finding:
    level: str  # error / warning
    check: int
    message: str
    entry_ids: list[int] = field(default_factory=list)
    incomplete: bool = False  # True = ยังจัดไม่ครบ (ไม่ใช่การละเมิดจากการแก้ไข)


@dataclass
class ValidationResult:
    status: str  # pass / warn / fail
    findings: list[Finding]
    summary: dict

    @property
    def errors(self):
        return [f for f in self.findings if f.level == "error"]

    @property
    def warnings(self):
        return [f for f in self.findings if f.level == "warning"]

    def as_report(self) -> dict:
        return {
            "status": self.status,
            "summary": self.summary,
            "findings": [{"level": f.level, "check": f.check, "message": f.message, "entry_ids": f.entry_ids, "incomplete": f.incomplete} for f in self.findings],
        }


STATUS_LABEL = {"pass": "ผ่านทั้งหมด", "warn": "ผ่าน แต่มีคำเตือน", "fail": "ไม่ผ่าน เนื่องจากพบข้อผิดพลาด"}


def validate_entries(s: Session, term_id: int, entries: list, scope_groups: set[int] | None = None) -> ValidationResult:
    """entries: รายการแบบ dict หรือ Entry — ตรวจสอบได้ทั้งตารางที่บันทึกแล้วและตารางสมมติ (ก่อนยืนยันการแก้ไข)"""
    E = [_as_dict(e) for e in entries]
    find: list[Finding] = []
    add = lambda lv, ck, msg, ids=(), inc=False: find.append(Finding(lv, ck, msg, list(ids), inc))  # noqa: E731

    ts = S.time_structure(s, term_id)
    st = {k: S.get(s, k, term_id) for k in S.DEFAULTS}
    rate = float(st["hour_to_period_rate"] or 1)
    teachers = {t.id: t for t in s.scalars(select(Teacher)).all()}
    rooms = {r.id: r for r in s.scalars(select(Room)).all()}
    rtypes = {rt.id: rt for rt in s.scalars(select(RoomType)).all()}
    groups = {g.id: g for g in s.scalars(select(ClassGroup).where(ClassGroup.term_id == term_id)).all()}
    courses = {c.id: c for c in s.scalars(select(Course).where(Course.term_id == term_id)).all()}
    regs = {(x.group_id, x.course_id) for x in s.scalars(select(GroupCourse).where(GroupCourse.term_id == term_id)).all()}
    assigns = s.scalars(select(Assignment).where(Assignment.term_id == term_id)).all()
    assigned = {(a.teacher_id, a.course_id, a.group_id) for a in assigns}
    joints = s.scalars(select(JointClass).where(JointClass.term_id == term_id)).all()
    memb = defaultdict(list)
    stu_group = {}
    for m in s.scalars(select(StudentMembership).where(StudentMembership.term_id == term_id)).all():
        memb[m.group_id].append(m.student_id)
        stu_group[m.student_id] = m.group_id
    enrolls = defaultdict(list)
    for e in s.scalars(select(Enrollment).where(Enrollment.term_id == term_id)).all():
        enrolls[e.student_id].append(e)
    t_unav = defaultdict(set)
    for u in s.scalars(select(TeacherUnavailable).where(TeacherUnavailable.term_id == term_id, TeacherUnavailable.preferred.is_(False))).all():
        t_unav[u.teacher_id].add((u.day, u.period))
    r_unav = defaultdict(set)
    for u in s.scalars(select(RoomUnavailable).where(RoomUnavailable.term_id == term_id)).all():
        r_unav[u.room_id].add((u.day, u.period))

    def gsize(gid):
        g = groups.get(gid)
        if g is None:
            return 0
        return g.size if g.size is not None else len(memb.get(gid, []))

    teaching = set(ts.teaching_periods)
    breaks = set(ts.break_periods)
    valid_nos = {p["no"] for p in ts.periods}

    # ---------------------------------------------------- 7, 20 เวลา
    for e in E:
        cells = list(range(e["start_period"], e["end_period"] + 1))
        if e["start_period"] > e["end_period"] or any(p not in valid_nos for p in cells) or e["day"] not in ts.days:
            add("error", 20, f"{_lbl(e)} อยู่นอกโครงสร้างเวลาเรียน", [e["id"]])
            continue
        if any(p in breaks for p in cells):
            add("error", 7, f"{_lbl(e)} ถูกจัดในคาบพัก", [e["id"]])
        if any(p not in teaching and p not in breaks for p in cells):
            add("error", 20, f"{_lbl(e)} อยู่ในคาบที่ไม่เปิดให้จัดการเรียนการสอน", [e["id"]])
        if e.get("start_time") and e["start_time"] != ts.start_time(e["start_period"]) or e.get("end_time") and e["end_time"] != ts.end_time(e["end_period"]):
            add("error", 20, f"{_lbl(e)} เวลาเริ่ม/สิ้นสุดไม่ตรงกับโครงสร้างเวลา", [e["id"]])

    # ---------------------------------------------------- occupancy
    occ_t = defaultdict(list)
    occ_r = defaultdict(list)
    occ_g = defaultdict(list)
    for e in E:
        for p in range(e["start_period"], e["end_period"] + 1):
            for t in e["teacher_ids"]:
                occ_t[(t, e["day"], p)].append(e["id"])
            if e["room_id"]:
                occ_r[(e["room_id"], e["day"], p)].append(e["id"])
            for g in e["group_ids"]:
                occ_g[(g, e["day"], p)].append(e["id"])
    byid = {e["id"]: e for e in E}
    seen_pairs = set()
    for (t, d, p), ids in occ_t.items():
        if len(ids) > 1 and (("t", t, tuple(sorted(ids)))) not in seen_pairs:
            seen_pairs.add(("t", t, tuple(sorted(ids))))
            nm = teachers[t].name if t in teachers else t
            add("error", 3, f"ครู {nm} มีรายการชนกัน วัน{S.DAY_NAMES[d]} คาบ {p}: " + " / ".join(_lbl(byid[i]) for i in ids), ids)
    for (r, d, p), ids in occ_r.items():
        if len(ids) > 1 and (("r", r, tuple(sorted(ids)))) not in seen_pairs:
            seen_pairs.add(("r", r, tuple(sorted(ids))))
            add("error", 4, f"ห้อง {rooms[r].label if r in rooms else r} ถูกใช้ซ้ำซ้อน วัน{S.DAY_NAMES[d]} คาบ {p}: " + " / ".join(_lbl(byid[i]) for i in ids), ids)
    for (g, d, p), ids in occ_g.items():
        if len(ids) > 1 and (("g", g, tuple(sorted(ids)))) not in seen_pairs:
            seen_pairs.add(("g", g, tuple(sorted(ids))))
            add("error", 5, f"กลุ่ม {groups[g].code if g in groups else g} มีรายการเรียนชนกัน วัน{S.DAY_NAMES[d]} คาบ {p}", ids)

    # ---------------------------------------------------- 5 นักเรียนรายบุคคล (ตามการลงทะเบียนจริง)
    def student_entries(sid):
        out = []
        g0 = stu_group.get(sid)
        en = enrolls.get(sid)
        for e in E:
            if e["entry_type"] in ("homeroom", "activity"):
                if g0 in e["group_ids"]:
                    out.append(e)
                continue
            if e["entry_type"] != "class":
                continue
            if en:
                for x in en:
                    sec = x.section_group_id or g0
                    if x.course_id == e["course_id"] and sec in e["group_ids"]:
                        out.append(e)
                        break
            elif g0 in e["group_ids"]:
                out.append(e)
        return out

    for sid in enrolls:
        es = student_entries(sid)
        cells = defaultdict(list)
        for e in es:
            for p in range(e["start_period"], e["end_period"] + 1):
                cells[(e["day"], p)].append(e["id"])
        for (d, p), ids in cells.items():
            if len(set(ids)) > 1:
                add("error", 5, f"นักเรียนรหัส {_stu_code(s, sid)} มีรายวิชาชนกัน วัน{S.DAY_NAMES[d]} คาบ {p}", ids)
                break

    # ---------------------------------------------------- 1, 2, 8 จำนวนคาบ / การลงทะเบียน
    sched = Counter()  # (group, course, part) -> periods (นับตามกลุ่ม)
    for e in E:
        if e["entry_type"] != "class" or not e["course_id"]:
            continue
        n = e["end_period"] - e["start_period"] + 1
        for g in e["group_ids"]:
            sched[(g, e["course_id"], e.get("part") or "all")] += n
            if (g, e["course_id"]) not in regs:
                enrolled_any = any(x.course_id == e["course_id"] and (x.section_group_id == g) for xs in enrolls.values() for x in xs)
                if not enrolled_any:
                    add("error", 8, f"{_lbl(e)}: กลุ่ม {groups[g].code if g in groups else g} ไม่ได้ลงทะเบียนรายวิชานี้", [e["id"]])
    group_req = Counter()
    group_got = Counter()
    for (g, c) in regs:
        if scope_groups is not None and g not in scope_groups:
            continue
        co = courses.get(c)
        if co is None or not co.active or co.course_type == "activity":
            continue
        req = {}
        if co.theory_hours is None and co.practice_hours is None:
            if co.total_periods:
                req = {"all": co.total_periods}
        else:
            if (co.theory_hours or 0) > 0:
                req["theory"] = int(round(co.theory_hours * rate))
            if (co.practice_hours or 0) > 0:
                req["practice"] = int(round(co.practice_hours * rate))
        total_req = sum(req.values())
        total_got = sum(v for (gg, cc, _p), v in sched.items() if gg == g and cc == c)
        group_req[g] += total_req
        group_got[g] += total_got
        if total_got != total_req:
            add("error", 1, f"วิชา {co.code} {co.name} กลุ่ม {groups[g].code if g in groups else g}: จัดแล้ว {total_got} คาบ จากที่ต้องได้ {total_req} คาบ", (), total_got < total_req)
        else:
            for part, n in req.items():
                got = sched.get((g, c, part), 0)
                if part != "all" and got != n:
                    add("error", 1, f"วิชา {co.code} กลุ่ม {groups[g].code}: ส่วน{'ทฤษฎี' if part == 'theory' else 'ปฏิบัติ'} จัดแล้ว {got} คาบ จากที่ต้องได้ {n} คาบ", (), got < n)
    for g in group_req:
        if group_req[g] != group_got[g]:
            add("error", 2, f"กลุ่ม {groups[g].code}: คาบรายวิชารวมที่จัด {group_got[g]} คาบ ไม่เท่ากับที่ต้องได้ {group_req[g]} คาบ", (), group_got[g] < group_req[g])

    # ---------------------------------------------------- 6, 12, 19 ห้อง/ความจุ/การเชื่อมโยง
    for e in E:
        if e["entry_type"] == "class":
            if not e["teacher_ids"]:
                add("error", 9, f"{_lbl(e)} ไม่มีครูผู้สอน", [e["id"]])
            if not e["group_ids"]:
                add("error", 19, f"{_lbl(e)} ไม่เชื่อมโยงกับกลุ่มเรียน", [e["id"]])
            if not e["room_id"]:
                add("error", 19, f"{_lbl(e)} ไม่มีห้องเรียน", [e["id"]])
            if not e["course_id"]:
                add("error", 19, f"รายการสอน id {e['id']} ไม่เชื่อมโยงกับรายวิชา", [e["id"]])
        if e["room_id"]:
            r = rooms.get(e["room_id"])
            if r is None:
                add("error", 19, f"{_lbl(e)} อ้างอิงห้องที่ไม่มีอยู่", [e["id"]])
                continue
            kind = rtypes[r.room_type_id].kind if r.room_type_id in rtypes else None
            rtcode = rtypes[r.room_type_id].code if r.room_type_id in rtypes else None
            if e["entry_type"] == "class" and e["course_id"] in courses:
                co = courses[e["course_id"]]
                part = e.get("part") or "all"
                is_theory = part == "theory" or (part == "all" and co.course_type == "theory")
                if is_theory and kind != "theory":
                    add("error", 6, f"{_lbl(e)} เป็นส่วนทฤษฎี แต่จัดในห้อง {r.label} ซึ่งไม่ใช่ห้องทฤษฎี", [e["id"]])
                if not is_theory:
                    if kind == "theory":
                        add("error", 6, f"{_lbl(e)} เป็นส่วนปฏิบัติ แต่จัดในห้องทฤษฎี {r.label}", [e["id"]])
                    req_types = [x for x in (co.practice_room_types or "").split(",") if x]
                    if req_types and rtcode not in req_types:
                        add("error", 6, f"{_lbl(e)} ต้องใช้ห้องประเภท {', '.join(req_types)} แต่จัดในห้อง {r.label}", [e["id"]])
            size = _entry_size(e, enrolls, stu_group, gsize)
            if r.capacity is None:
                add("error", 12, f"ห้อง {r.label} ไม่มีข้อมูลความจุ จึงตรวจสอบความจุของ {_lbl(e)} ไม่ได้", [e["id"]])
            elif size > r.capacity:
                add("error", 12, f"{_lbl(e)} มีผู้เรียน {size} คน เกินความจุห้อง {r.label} ({r.capacity})", [e["id"]])
            for p in range(e["start_period"], e["end_period"] + 1):
                if (e["day"], p) in r_unav.get(r.id, set()):
                    add("error", 21, f"{_lbl(e)} ใช้ห้อง {r.label} ในเวลาที่ห้องไม่พร้อมใช้งาน", [e["id"]])
                    break

    # ---------------------------------------------------- 11, 21 การมอบหมาย / เวลาไม่สะดวก
    for e in E:
        if e["entry_type"] == "class" and e["course_id"]:
            for t in e["teacher_ids"]:
                for g in e["group_ids"]:
                    if (t, e["course_id"], g) not in assigned:
                        add("error", 11, f"ครู {teachers[t].name if t in teachers else t} ไม่ได้รับมอบหมายให้สอน {_lbl(e)} กลุ่ม {groups[g].code if g in groups else g}", [e["id"]])
        if e["entry_type"] in ("class", "homeroom", "activity"):
            for t in e["teacher_ids"]:
                bad = [p for p in range(e["start_period"], e["end_period"] + 1) if (e["day"], p) in t_unav.get(t, set())]
                if bad:
                    add("error", 21, f"{_lbl(e)} ถูกจัดในเวลาที่ครู {teachers[t].name if t in teachers else t} ไม่สะดวก (คาบ {bad})", [e["id"]])

    # ---------------------------------------------------- 10 ชั่วโมงสอนสูงสุด (รายการสอนร่วมนับครั้งเดียว)
    count_hr = bool(st["homeroom"].get("count_as_teaching"))
    week = Counter()
    day = Counter()
    for e in E:
        if e["entry_type"] == "class" or e["entry_type"] == "activity" or (e["entry_type"] == "homeroom" and count_hr):
            n = e["end_period"] - e["start_period"] + 1
            for t in e["teacher_ids"]:
                week[t] += n
                day[(t, e["day"])] += n
    for t, n in week.items():
        tt = teachers.get(t)
        if tt and tt.max_hours_week is not None and n > tt.max_hours_week:
            add("error", 10, f"ครู {tt.name} สอน {n} คาบ/สัปดาห์ เกินชั่วโมงสอนสูงสุด {tt.max_hours_week}")
    for (t, d), n in day.items():
        tt = teachers.get(t)
        if tt and tt.max_hours_day and n > tt.max_hours_day:
            add("error", 10, f"ครู {tt.name} สอน {n} คาบในวัน{S.DAY_NAMES[d]} เกินสูงสุด {tt.max_hours_day} คาบ/วัน")

    # ---------------------------------------------------- 13 คาบต่อวัน
    for g in groups:
        for d in ts.days:
            n = len({p for (gg, dd, p) in occ_g if gg == g and dd == d and p in teaching})
            if n > ts.max_per_day:
                add("error", 13, f"กลุ่ม {groups[g].code} เรียน {n} คาบในวัน{S.DAY_NAMES[d]} เกิน {ts.max_per_day} คาบ")

    # ---------------------------------------------------- 14 โฮมรูม
    hr = st["homeroom"]
    if hr.get("enabled"):
        need = int(hr.get("periods_per_week", 1))
        for g, go in groups.items():
            if scope_groups is not None and g not in scope_groups:
                continue
            hrs = [e for e in E if e["entry_type"] == "homeroom" and g in e["group_ids"]]
            n = sum(e["end_period"] - e["start_period"] + 1 for e in hrs)
            if n < need:
                add("error", 14, f"กลุ่ม {go.code} มีโฮมรูม {n} คาบ น้อยกว่าที่กำหนด {need} คาบ/สัปดาห์", (), True)
            for e in hrs:
                if go.advisor_teacher_id and go.advisor_teacher_id not in e["teacher_ids"]:
                    add("error", 14, f"โฮมรูมกลุ่ม {go.code} ไม่ได้จัดกับครูที่ปรึกษา", [e["id"]])

    # ---------------------------------------------------- 15 ประชุม
    mt = st["meeting"]
    if mt.get("enabled"):
        members = {t for t, tt in teachers.items() if tt.is_meeting_member and tt.active}
        mentries = [e for e in E if e["entry_type"] == "meeting"]
        if members and not mentries:
            add("error", 15, "ไม่มีรายการประชุมหัวหน้าแผนก/หัวหน้างานในตาราง", (), True)
        if len(mentries) > 1:
            add("warning", 15, f"พบรายการประชุม {len(mentries)} รายการ (ควรมีรายการเดียวที่เชื่อมผู้เข้าร่วมทั้งหมด)", [e["id"] for e in mentries])
        covered = set()
        for e in mentries:
            if e["day"] != int(mt["day"]) or set(range(e["start_period"], e["end_period"] + 1)) != set(mt["periods"]):
                add("error", 15, "รายการประชุมไม่อยู่ในวัน/คาบที่กำหนด", [e["id"]])
            covered |= set(e["teacher_ids"])
            extra = set(e["teacher_ids"]) - members
            if extra:
                add("error", 15, f"มีครูที่ไม่ได้เป็นผู้เข้าร่วมประชุมอยู่ในรายการประชุม: {', '.join(teachers[t].name for t in extra if t in teachers)}", [e["id"]])
        missing = members - covered
        if missing and mentries:
            add("error", 15, f"ผู้เข้าร่วมประชุมที่ไม่มีรายการประชุม: {', '.join(teachers[t].name for t in missing)}", (), True)

    # ---------------------------------------------------- 16 กิจกรรม
    act = st["activity"]
    if act.get("enabled"):
        aslot = (int(act["day"]), set(act["periods"]))
        act_courses = {c for c, co in courses.items() if co.course_type == "activity"}
        for e in E:
            if e["entry_type"] == "activity":
                if e["day"] != aslot[0] or set(range(e["start_period"], e["end_period"] + 1)) != aslot[1]:
                    add("error", 16, f"{_lbl(e)} ไม่อยู่ในช่วงเวลากิจกรรมที่กำหนด", [e["id"]])
            elif e["entry_type"] == "class" and e["course_id"] in act_courses:
                add("error", 16, f"{_lbl(e)} เป็นรายวิชากิจกรรมแต่ถูกจัดเป็นรายการสอนปกติ", [e["id"]])
        for (g, c) in regs:
            if c in act_courses and (scope_groups is None or g in scope_groups):
                if not any(e["entry_type"] == "activity" and e["course_id"] == c and g in e["group_ids"] for e in E):
                    add("error", 16, f"กลุ่ม {groups[g].code} ลงทะเบียนรายวิชากิจกรรม {courses[c].code} แต่ไม่มีรายการกิจกรรมในตาราง", (), True)
        if act.get("lock_all_groups"):
            for e in E:
                if e["entry_type"] in ("class", "homeroom") and e["day"] == aslot[0] and set(range(e["start_period"], e["end_period"] + 1)) & aslot[1]:
                    add("error", 16, f"{_lbl(e)} ถูกจัดในช่วงเวลากิจกรรม", [e["id"]])

    # ---------------------------------------------------- 17 การเรียนร่วม
    joint_sets = {(j.course_id, frozenset(j.group_ids)) for j in joints}
    for e in E:
        if e["entry_type"] == "class" and len(e["group_ids"]) > 1:
            if (e["course_id"], frozenset(e["group_ids"])) not in joint_sets:
                add("error", 17, f"{_lbl(e)} จัดหลายกลุ่มเรียนร่วมกันโดยไม่มีข้อมูลการเรียนร่วมรองรับ", [e["id"]])
    for j in joints:
        gs = set(j.group_ids)
        for e in E:
            if e["entry_type"] == "class" and e["course_id"] == j.course_id and gs & set(e["group_ids"]) and set(e["group_ids"]) != gs:
                add("error", 17, f"{_lbl(e)}: วิชานี้กำหนดให้เรียนร่วมกัน แต่ถูกจัดแยกกลุ่ม", [e["id"]])

    # ---------------------------------------------------- 18 รายการซ้ำ
    sig = Counter((e["entry_type"], e["course_id"], e["day"], e["start_period"], tuple(sorted(e["group_ids"])), tuple(sorted(e["teacher_ids"]))) for e in E)
    for k, n in sig.items():
        if n > 1:
            ids = [e["id"] for e in E if (e["entry_type"], e["course_id"], e["day"], e["start_period"], tuple(sorted(e["group_ids"])), tuple(sorted(e["teacher_ids"]))) == k]
            add("error", 18, f"พบรายการซ้ำ {n} รายการ: {_lbl(byid[ids[0]])}", ids)

    # ---------------------------------------------------- 22 ต่อเนื่อง
    for e in E:
        if e["entry_type"] == "class" and e["course_id"] in courses:
            co = courses[e["course_id"]]
            n = e["end_period"] - e["start_period"] + 1
            if co.consecutive and n > co.consecutive:
                add("error", 22, f"{_lbl(e)} ยาว {n} คาบ เกินจำนวนคาบต่อเนื่องที่กำหนด ({co.consecutive})", [e["id"]])
            elif co.consecutive and n < co.consecutive:
                add("warning", 22, f"{_lbl(e)} ยาว {n} คาบ น้อยกว่าจำนวนคาบต่อเนื่องที่กำหนด ({co.consecutive}) — ตรวจสอบว่าเป็นช่วงเศษที่ตั้งใจ", [e["id"]])

    errs = [f for f in find if f.level == "error"]
    warns = [f for f in find if f.level == "warning"]
    status = "fail" if errs else ("warn" if warns else "pass")
    summary = {}
    for ck, name in CHECK_NAMES.items():
        e_n = sum(1 for f in errs if f.check == ck)
        w_n = sum(1 for f in warns if f.check == ck)
        summary[ck] = {"name": name, "errors": e_n, "warnings": w_n}
    return ValidationResult(status, find, summary)


def validate_timetable(s: Session, timetable_id: int) -> ValidationResult:
    tt = s.get(Timetable, timetable_id)
    entries = s.scalars(select(Entry).where(Entry.timetable_id == timetable_id)).all()
    scope = None
    if tt.scope_department_id:
        scope = {g.id for g in s.scalars(select(ClassGroup).where(ClassGroup.term_id == tt.term_id, ClassGroup.department_id == tt.scope_department_id)).all()}
    return validate_entries(s, tt.term_id, entries, scope)


def _as_dict(e) -> dict:
    if isinstance(e, dict):
        d = dict(e)
        d.setdefault("teacher_ids", [])
        d.setdefault("group_ids", [])
        return d
    return {
        "id": e.id, "day": e.day, "start_period": e.start_period, "end_period": e.end_period,
        "start_time": e.start_time, "end_time": e.end_time, "entry_type": e.entry_type, "part": e.part,
        "course_id": e.course_id, "room_id": e.room_id, "teacher_ids": e.teacher_ids, "group_ids": e.group_ids,
        "course_code": e.course_code, "course_name": e.course_name,
    }


def _lbl(e: dict) -> str:
    if e["entry_type"] == "homeroom":
        return f"โฮมรูม ({S.DAY_NAMES[e['day']]} คาบ {e['start_period']})"
    if e["entry_type"] == "meeting":
        return "ประชุม"
    base = f"{e.get('course_code') or ''} {e.get('course_name') or ''}".strip() or f"รายการ {e['id']}"
    return f"{base} ({S.DAY_NAMES[e['day']] if 0 <= e['day'] < 7 else e['day']} คาบ {e['start_period']}-{e['end_period']})"


def _entry_size(e, enrolls, stu_group, gsize) -> int:
    """จำนวนผู้เรียนจริงของรายการ (นับจากการลงทะเบียน ถ้ามีรายบุคคล)"""
    if e["entry_type"] != "class":
        return sum(gsize(g) for g in e["group_ids"])
    total = 0
    gset = set(e["group_ids"])
    individual = 0
    for sid, xs in enrolls.items():
        for x in xs:
            sec = x.section_group_id or stu_group.get(sid)
            if x.course_id == e["course_id"] and sec in gset and stu_group.get(sid) not in gset:
                individual += 1
    for g in gset:
        total += gsize(g)
    return total + individual


_stu_cache: dict = {}


def _stu_code(s, sid):
    from .models import Student

    st = s.get(Student, sid)
    return st.code if st else sid
