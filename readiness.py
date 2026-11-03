"""ตรวจสอบความพร้อมของข้อมูลก่อนจัดตาราง (หัวข้อ 4.5 / 4.6)

ผลลัพธ์ 3 ระดับ: error (ต้องแก้ก่อนจัด), warning (ดำเนินการต่อได้เมื่อผู้ใช้ตรวจแล้ว), info (ผ่าน)
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import problem as P
from .importer.mapping import norm
from .models import (
    Assignment,
    ClassGroup,
    Course,
    Enrollment,
    GroupCourse,
    Room,
    Student,
    StudentMembership,
    Teacher,
    TeacherUnavailable,
)
from .settings_service import DAY_NAMES

LEVEL_LABEL = {"error": "ข้อผิดพลาด", "warning": "คำเตือน", "info": "ข้อมูลทั่วไป"}


@dataclass
class Check:
    level: str
    category: str
    message: str
    fix: str = ""

    def as_row(self) -> dict:
        return {"ระดับ": LEVEL_LABEL[self.level], "หมวด": self.category, "รายละเอียด": self.message, "วิธีแก้ไข": self.fix}


def run(s: Session, term_id: int, department_id: int | None = None) -> tuple[list[Check], P.Problem]:
    out: list[Check] = []
    add = lambda lv, cat, msg, fix="": out.append(Check(lv, cat, msg, fix))  # noqa: E731

    courses = s.scalars(select(Course).where(Course.term_id == term_id)).all()
    teachers = s.scalars(select(Teacher)).all()
    rooms = s.scalars(select(Room).where(Room.active.is_(True))).all()
    groups = s.scalars(select(ClassGroup).where(ClassGroup.term_id == term_id)).all()
    tmap = {t.id: t for t in teachers}
    cmap = {c.id: c for c in courses}
    gmap = {g.id: g for g in groups}

    if not courses:
        add("error", "รายวิชา", "ยังไม่มีข้อมูลรายวิชาในภาคเรียนนี้", "นำเข้าไฟล์รายวิชา")
    if not teachers:
        add("error", "ครู", "ยังไม่มีข้อมูลครู", "นำเข้าไฟล์รายชื่อครู")
    if not rooms:
        add("error", "ห้องเรียน", "ยังไม่มีข้อมูลห้องเรียน", "นำเข้าไฟล์ห้องเรียน")
    if not groups:
        add("error", "กลุ่มเรียน", "ยังไม่มีข้อมูลกลุ่มเรียนในภาคเรียนนี้", "นำเข้าไฟล์กลุ่มเรียน")

    # 1-2 รายวิชา
    for c in courses:
        if not (c.code or "").strip():
            add("error", "รายวิชา", f"รายวิชา “{c.name}” ไม่มีรหัสวิชา", "แก้ไขข้อมูลรายวิชา")
    # 3 ครู
    for t in teachers:
        if not (t.code or "").strip():
            add("error", "ครู", f"ครู “{t.name}” ไม่มีรหัสประจำตัว", "แก้ไขข้อมูลครู")
    # 6-7 ห้อง
    no_type = [r.code for r in rooms if r.room_type_id is None]
    no_cap = [r.code for r in rooms if r.capacity is None]
    if no_type:
        add("error", "ห้องเรียน", f"ห้องที่ไม่มีข้อมูลประเภทห้อง: {', '.join(no_type[:20])}" + (" ..." if len(no_type) > 20 else ""), "ระบุประเภทห้องในหน้าจัดการข้อมูล > ห้องเรียน")
    if no_cap:
        add("warning", "ห้องเรียน", f"ห้องที่ไม่มีข้อมูลความจุ ({len(no_cap)} ห้อง) จะไม่ถูกใช้จัดตาราง: {', '.join(no_cap[:20])}", "ระบุความจุห้อง")
    # 8 นักเรียนไม่มีกลุ่ม
    member_ids = set(s.scalars(select(StudentMembership.student_id).where(StudentMembership.term_id == term_id)).all())
    enrolled_ids = set(s.scalars(select(Enrollment.student_id).where(Enrollment.term_id == term_id)).all())
    no_group = [st.code for st in s.scalars(select(Student)).all() if st.id not in member_ids and st.id in enrolled_ids]
    total_students = s.scalar(select(func.count(Student.id))) or 0
    orphan = total_students - len(member_ids)
    if no_group:
        add("error", "นักเรียน", f"นักเรียนที่ลงทะเบียนแต่ไม่สามารถระบุกลุ่มเรียนได้: {', '.join(no_group[:20])}", "นำเข้าข้อมูลนักเรียนพร้อมรหัสกลุ่มเรียน")
    elif orphan > 0:
        add("warning", "นักเรียน", f"มีนักเรียน {orphan} คนที่ไม่มีกลุ่มเรียนในภาคเรียนนี้ (จะไม่ปรากฏในตารางรายบุคคล)", "ตรวจสอบข้อมูลนักเรียน")
    # กลุ่มเรียนไม่มีจำนวนสมาชิก
    sizes = dict(s.execute(select(StudentMembership.group_id, func.count()).where(StudentMembership.term_id == term_id).group_by(StudentMembership.group_id)).all())
    nosize = [g.code for g in groups if g.size is None and not sizes.get(g.id)]
    if nosize:
        add("error", "กลุ่มเรียน", f"กลุ่มเรียนที่ไม่ทราบจำนวนผู้เรียน (ใช้ตรวจความจุห้องไม่ได้): {', '.join(nosize[:20])}", "ระบุจำนวนสมาชิก หรือนำเข้ารายชื่อนักเรียน")
    for g in groups:
        if g.size is not None and sizes.get(g.id) and g.size != sizes[g.id]:
            add("warning", "กลุ่มเรียน", f"กลุ่ม {g.code}: จำนวนสมาชิกที่ระบุ ({g.size}) ไม่ตรงกับรายชื่อนักเรียน ({sizes[g.id]}) — ระบบใช้ค่าที่ระบุ", "ตรวจสอบจำนวนสมาชิก")

    # 5 ครูไม่มีสิทธิ์สอน (เมื่อมีข้อมูลรายวิชาที่สอนได้)
    assigns = s.scalars(select(Assignment).where(Assignment.term_id == term_id)).all()
    pb = P.build(s, term_id, department_id)
    for a in assigns:
        t, c = tmap.get(a.teacher_id), cmap.get(a.course_id)
        if not t or not c:
            continue
        quals = pb.teachers.get(t.id, {}).get("quals") or set()
        if quals and c.code not in quals:
            add("error", "การมอบหมาย", f"ครู {t.name} ได้รับมอบหมายวิชา {c.code} ซึ่งไม่อยู่ในรายวิชาที่สามารถสอนได้", "แก้ไขการมอบหมาย หรือเพิ่มรายวิชาที่ครูสอนได้")
    # การมอบหมายที่ไม่มีการลงทะเบียนรองรับ (เงื่อนไขที่ 3)
    regs = {(gc.group_id, gc.course_id) for gc in s.scalars(select(GroupCourse).where(GroupCourse.term_id == term_id)).all()}
    unreg = [(a.group_id, a.course_id) for a in assigns if (a.group_id, a.course_id) not in regs]
    if unreg:
        sample = ", ".join(f"{gmap[g].code}/{cmap[c].code}" for g, c in sorted(set(unreg))[:10] if g in gmap and c in cmap)
        add("error", "การลงทะเบียน", f"มีการมอบหมายผู้สอน {len(set(unreg))} รายการที่กลุ่มเรียนยังไม่ได้ลงทะเบียนรายวิชานั้น เช่น {sample}",
            "นำเข้าการลงทะเบียนของกลุ่มเรียน หรือกดปุ่ม “สร้างการลงทะเบียนจากข้อมูลการมอบหมาย” (ต้องยืนยัน)")
    # 9 ลงทะเบียนวิชาที่ไม่มีในรายวิชา: ป้องกันโดย foreign key — ตรวจวิชาที่ไม่ใช้งาน
    inactive = {c.id for c in courses if not c.active}
    bad = s.scalar(select(func.count(Enrollment.id)).where(Enrollment.term_id == term_id, Enrollment.course_id.in_(inactive))) if inactive else 0
    if bad:
        add("warning", "การลงทะเบียน", f"มีการลงทะเบียน {bad} รายการในรายวิชาที่ปิดใช้งาน", "ตรวจสอบสถานะรายวิชา")
    # 10 รหัสซ้ำที่อาจหมายถึงคนละบุคคล / ชื่อซ้ำต่างรหัส
    byname = defaultdict(list)
    for t in teachers:
        byname[norm(t.name)].append(t.code)
    for n, codes in byname.items():
        if len(codes) > 1:
            add("warning", "ครู", f"ครูชื่อเดียวกันแต่รหัสต่างกัน: {', '.join(codes)} — อาจเป็นบุคคลเดียวกันที่บันทึกซ้ำ", "ตรวจสอบรหัสครู")
    # 11 จำนวนคาบไม่สอดคล้อง
    rate = float(pb.settings["hour_to_period_rate"] or 1)
    for c in courses:
        if c.total_periods and (c.theory_hours is not None or c.practice_hours is not None):
            calc = int(round(((c.theory_hours or 0) + (c.practice_hours or 0)) * rate))
            if calc != c.total_periods:
                add("error", "รายวิชา", f"วิชา {c.code}: จำนวนคาบรวมที่ระบุ ({c.total_periods}) ไม่ตรงกับชั่วโมงทฤษฎี+ปฏิบัติ × อัตราแปลง ({calc})", "แก้ไขข้อมูลรายวิชาให้สอดคล้องกัน")
    # 13 ข้อมูลเวลาไม่ตรงโครงสร้าง
    ts = pb.ts
    valid = set(ts.teaching_periods) | set(ts.break_periods) | {p["no"] for p in ts.periods}
    for u in s.scalars(select(TeacherUnavailable).where(TeacherUnavailable.term_id == term_id)).all():
        if u.period not in valid or u.day not in ts.days:
            t = tmap.get(u.teacher_id)
            add("error", "เวลาไม่สะดวก", f"ครู {t.name if t else u.teacher_id}: เวลาไม่สะดวก วัน{DAY_NAMES[u.day]} คาบ {u.period} ไม่อยู่ในโครงสร้างเวลาเรียน", "แก้ไขข้อมูลเวลาไม่สะดวก")
    mt, act = pb.settings["meeting"], pb.settings["activity"]
    for nm, cfg in (("ประชุม", mt), ("กิจกรรม", act)):
        if cfg.get("enabled"):
            for p in cfg["periods"]:
                if p not in ts.teaching_periods:
                    add("error", "โครงสร้างเวลา", f"ช่วง{nm}คาบที่ {p} ไม่ใช่คาบเรียนปกติในโครงสร้างเวลา", "แก้ไขที่หน้าตั้งค่า")
            ps = sorted(cfg["periods"])
            if ps and not ts.span_ok(ps[0], len(ps)):
                add("error", "โครงสร้างเวลา", f"ช่วง{nm}ไม่ต่อเนื่องหรือมีคาบพักคั่น", "แก้ไขที่หน้าตั้งค่า")

    # ปัญหาจากการสร้างรายการสอน
    for i in pb.issues:
        add(i.level, "รายการสอน", i.message, i.fix)

    # 14 ความเป็นไปได้ (ตรวจเชิงปริมาณก่อนจัด)
    nslots_day = len(ts.teaching_periods)
    days = len(ts.days)
    gload = Counter()
    for L in pb.lessons:
        for g in L.group_ids:
            gload[g] += L.periods
    for f in pb.fixed:
        for g in f.group_ids:
            gload[g] += len(f.periods)
    cap_g = min(nslots_day, ts.max_per_day) * days
    for g, n in gload.items():
        forb = len(pb.group_forbidden.get(g, set()))
        fixed_n = sum(len(f.periods) for f in pb.fixed if g in f.group_ids)
        avail = cap_g - max(0, forb - fixed_n)
        if n > avail:
            add("error", "ความเป็นไปได้", f"กลุ่ม {pb.groups[g]['code']} ต้องเรียน {n} คาบ/สัปดาห์ แต่มีช่องเวลาที่ใช้ได้เพียง {avail} คาบ", "ลดรายวิชาที่ลงทะเบียน หรือปรับโครงสร้างเวลา/จำนวนคาบสูงสุดต่อวัน")
    tload = Counter()
    for L in pb.lessons:
        if L.kind == "class" or pb.settings["homeroom"].get("count_as_teaching"):
            for t in L.teacher_ids:
                tload[t] += L.periods
    for f in pb.fixed:
        if f.kind == "activity":
            for t in f.teacher_ids:
                tload[t] += len(f.periods)
    busy_all = Counter()
    for L in pb.lessons:
        for t in L.teacher_ids:
            busy_all[t] += L.periods
    for f in pb.fixed:
        for t in f.teacher_ids:
            busy_all[t] += len(f.periods)
    for t, n in tload.items():
        ti = pb.teachers[t]
        if ti["max_week"] is not None and n > ti["max_week"]:
            add("error", "ภาระงานครู", f"ครู {ti['name']} ได้รับมอบหมาย {n} คาบ เกินชั่วโมงสอนสูงสุด {ti['max_week']} คาบ/สัปดาห์", "ปรับการมอบหมายผู้สอน หรือให้ผู้มีสิทธิ์แก้ไขชั่วโมงสอนสูงสุด")
    for t, n in busy_all.items():
        ti = pb.teachers[t]
        free = days * nslots_day - len({x for x in ti["unavail"] if x[1] in ts.teaching_periods and x[0] in ts.days})
        if ti["max_day"]:
            free = min(free, ti["max_day"] * days + sum(len(f.periods) for f in pb.fixed if t in f.teacher_ids and f.kind == "meeting"))
        if n > free:
            add("error", "ความเป็นไปได้", f"ครู {ti['name']} ต้องมีรายการ {n} คาบ แต่มีช่วงเวลาว่างเพียง {free} คาบ", "ลดเวลาไม่สะดวกของครู หรือปรับการมอบหมาย")
    demand = Counter()
    for L in pb.lessons:
        if L.needs_room:
            key = "theory" if all(pb.rooms[r]["kind"] == "theory" for r in L.room_ids) else "practice"
            demand[key] += L.periods
    for key, n in demand.items():
        supply_rooms = [r for r, ri in pb.rooms.items() if ri["kind"] == key and ri["capacity"] is not None]
        supply = len(supply_rooms) * days * nslots_day
        if n > supply:
            add("error", "ความเป็นไปได้", f"ต้องใช้ห้อง{'ทฤษฎี' if key == 'theory' else 'ปฏิบัติ'}รวม {n} คาบ แต่มีความจุห้องรวมเพียง {supply} คาบ/สัปดาห์", "เพิ่มห้องเรียนประเภทนี้")

    if not any(c.level == "error" for c in out):
        add("info", "สรุป", f"ผ่านการตรวจสอบข้อผิดพลาด: รายการสอน {len(pb.lessons)} รายการ ({sum(L.periods for L in pb.lessons)} คาบ), รายการกำหนดเวลาคงที่ {len(pb.fixed)} รายการ")
    if not unreg:
        add("info", "การลงทะเบียน", "การมอบหมายผู้สอนทุกรายการมีการลงทะเบียนรองรับ")
    if pb.student_sets:
        add("info", "นักเรียน", f"พบชุดการลงทะเบียนรายบุคคลที่ต่างจากกลุ่มหลัก {len(pb.student_sets)} รูปแบบ — จะตรวจการชนระดับนักเรียนด้วย")
    return out, pb


def data_status(s: Session, term_id: int, checks: list[Check] | None = None) -> list[dict]:
    """สถานะของข้อมูลแต่ละหมวด (หัวข้อ 4.6)"""
    counts = {
        "รายวิชา": s.scalar(select(func.count(Course.id)).where(Course.term_id == term_id)) or 0,
        "ครู": s.scalar(select(func.count(Teacher.id))) or 0,
        "ห้องเรียน": s.scalar(select(func.count(Room.id))) or 0,
        "กลุ่มเรียน": s.scalar(select(func.count(ClassGroup.id)).where(ClassGroup.term_id == term_id)) or 0,
        "นักเรียน": s.scalar(select(func.count(StudentMembership.id)).where(StudentMembership.term_id == term_id)) or 0,
        "การลงทะเบียน": (s.scalar(select(func.count(GroupCourse.id)).where(GroupCourse.term_id == term_id)) or 0)
        + (s.scalar(select(func.count(Enrollment.id)).where(Enrollment.term_id == term_id)) or 0),
        "การมอบหมาย": s.scalar(select(func.count(Assignment.id)).where(Assignment.term_id == term_id)) or 0,
    }
    cat_map = {"รายวิชา": ["รายวิชา"], "ครู": ["ครู", "ภาระงานครู", "เวลาไม่สะดวก"], "ห้องเรียน": ["ห้องเรียน"], "กลุ่มเรียน": ["กลุ่มเรียน"],
               "นักเรียน": ["นักเรียน"], "การลงทะเบียน": ["การลงทะเบียน"], "การมอบหมาย": ["การมอบหมาย", "รายการสอน"]}
    rows = []
    for k, n in counts.items():
        if n == 0:
            status = "ข้อมูลที่ยังไม่ครบ"
        elif checks is None:
            status = "ข้อมูลที่นำเข้าแล้ว"
        else:
            errs = [c for c in checks if c.level == "error" and c.category in cat_map[k]]
            warns = [c for c in checks if c.level == "warning" and c.category in cat_map[k]]
            status = "ข้อมูลที่ต้องแก้ไข" if errs else ("ข้อมูลที่ตรวจสอบแล้ว (มีคำเตือน)" if warns else "ข้อมูลที่พร้อมจัดตาราง")
        rows.append({"หมวดข้อมูล": k, "จำนวน": n, "สถานะ": status})
    return rows
