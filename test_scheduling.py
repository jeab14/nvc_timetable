"""การทดสอบการจัดตารางและระบบตรวจสอบอิสระ (หัวข้อ 25)"""
from __future__ import annotations

from collections import Counter, defaultdict

from sqlalchemy import select

from nvc import db, problem as P, readiness, settings_service as S, solver as SV, timetable_service as T, validator as V
from nvc.models import ClassGroup, Course, GroupCourse, Room, Teacher, TeacherUnavailable, Timetable

from .conftest import ADMIN


def _entries(sched):
    with db.session_scope() as s:
        return [T.entry_dict(e) for e in T.entries_of(s, sched["tt"])]


def _cells(e):
    return [(e["day"], p) for p in range(e["start_period"], e["end_period"] + 1)]


def test_schedule_success_and_independent_validation(sched):
    with db.session_scope() as s:
        tt = s.get(Timetable, sched["tt"])
        assert tt.status == "success", tt.solver_report
        vr = V.validate_timetable(s, tt.id)
    assert vr.status in ("pass", "warn"), [f.message for f in vr.errors]


def test_period_counts_match_curriculum(sched):
    es = _entries(sched)
    got = Counter()
    for e in es:
        if e["entry_type"] == "class":
            for g in e["group_ids"]:
                got[(g, e["course_id"])] += e["end_period"] - e["start_period"] + 1
    with db.session_scope() as s:
        for gc in s.scalars(select(GroupCourse)).all():
            c = s.get(Course, gc.course_id)
            if c.course_type == "activity":
                continue
            need = int((c.theory_hours or 0) + (c.practice_hours or 0))
            assert got[(gc.group_id, c.id)] == need, (c.code, gc.group_id)


def test_no_teacher_room_group_overlap(sched):
    es = _entries(sched)
    for key in ("teacher_ids", "group_ids"):
        occ = Counter((x, c) for e in es for x in e[key] for c in _cells(e))
        assert max(occ.values()) == 1, key
    occ = Counter((e["room_id"], c) for e in es if e["room_id"] for c in _cells(e))
    assert max(occ.values()) == 1


def test_break_and_bounds(sched):
    for e in _entries(sched):
        assert 5 not in range(e["start_period"], e["end_period"] + 1)
        assert 1 <= e["start_period"] <= e["end_period"] <= 10


def test_room_types(sched):
    with db.session_scope() as s:
        for e in T.entries_of(s, sched["tt"]):
            if e.entry_type != "class":
                continue
            r = s.get(Room, e.room_id)
            kind = r.room_type.kind
            if e.part == "theory":
                assert kind == "theory", (e.course_code, r.code)
            else:
                assert kind != "theory", (e.course_code, r.code)


def test_meeting_homeroom_activity(sched):
    es = _entries(sched)
    with db.session_scope() as s:
        members = {t.id for t in s.scalars(select(Teacher).where(Teacher.is_meeting_member.is_(True))).all()}
        groups = {g.id: g for g in s.scalars(select(ClassGroup)).all()}
    meet = [e for e in es if e["entry_type"] == "meeting"]
    assert len(meet) == 1 and set(meet[0]["teacher_ids"]) == members
    assert meet[0]["day"] == 1 and meet[0]["start_period"] == meet[0]["end_period"] == 8
    for e in es:
        if e["entry_type"] == "class" and e["day"] == 1 and 8 in range(e["start_period"], e["end_period"] + 1):
            assert not set(e["teacher_ids"]) & members
    for g, go in groups.items():
        hr = [e for e in es if e["entry_type"] == "homeroom" and g in e["group_ids"]]
        assert len(hr) == 1 and hr[0]["teacher_ids"] == [go.advisor_teacher_id]
    acts = [e for e in es if e["entry_type"] == "activity"]
    assert acts and all(e["day"] == 2 and (e["start_period"], e["end_period"]) == (8, 9) for e in acts)
    for e in es:
        if e["entry_type"] == "class" and e["day"] == 2:
            assert not {8, 9} & set(range(e["start_period"], e["end_period"] + 1))


def test_general_joint_class_counted_once(sched):
    es = _entries(sched)
    math = [e for e in es if e["course_code"] == "20000-1401"]
    assert len(math) == 2 and all(len(e["group_ids"]) == 2 for e in math)
    with db.session_scope() as s:
        t3 = s.scalars(select(Teacher).where(Teacher.code == "T003")).one()
        vd = __import__("nvc.views", fromlist=["x"]).get_view(s, ADMIN, s.get(Timetable, sched["tt"]), "teacher", t3.id)
    hours = dict(vd.header)["จำนวนชั่วโมงสอนรวม"]
    # T003 สอนคณิต 2 ชุดเรียนร่วม x 2 คาบ = 4 คาบ (ไม่ใช่ 8)
    assert hours.startswith("4 ")


def test_max_per_day(sched):
    es = _entries(sched)
    per = defaultdict(set)
    for e in es:
        for g in e["group_ids"]:
            for c in _cells(e):
                per[(g, c[0])].add(c[1])
    assert max(len(v) for v in per.values()) <= 10


def test_cross_enrolled_student_no_conflict(sched):
    """นักเรียนบัญชีที่ลงทะเบียนวิชาธุรกิจดิจิทัลกับกลุ่ม DBT1-1 ต้องไม่ชนกับวิชาของกลุ่มตนเอง"""
    from nvc.models import Enrollment
    from nvc.views import student_entries

    with db.session_scope() as s:
        sid = s.scalars(select(Enrollment.student_id)).first()
        es = student_entries(s, sched["term"], sid, _entries(sched))
    codes = {e["course_code"] for e in es}
    assert "21910-1001" in codes and "20201-2001" in codes
    occ = Counter(c for e in es for c in _cells(e))
    assert max(occ.values()) == 1


def test_validator_detects_injected_conflicts(sched):
    """ตัวตรวจสอบอิสระต้องจับข้อผิดพลาดที่ใส่เข้าไปเองได้"""
    es = _entries(sched)
    a = next(e for e in es if e["entry_type"] == "class" and e["room_id"])
    b = next(e for e in es if e["entry_type"] == "class" and e["id"] != a["id"] and not set(e["group_ids"]) & set(a["group_ids"]) and not set(e["teacher_ids"]) & set(a["teacher_ids"]))
    bad = dict(b, id=-5, day=a["day"], start_period=a["start_period"], end_period=a["start_period"] + (b["end_period"] - b["start_period"]),
               room_id=a["room_id"], teacher_ids=a["teacher_ids"])
    brk = dict(a, id=-6, start_period=5, end_period=5)
    with db.session_scope() as s:
        trial = [e for e in es if e["id"] != b["id"]] + [bad]
        vr = V.validate_entries(s, sched["term"], trial)
        checks = {f.check for f in vr.errors}
        assert {3, 4} <= checks and vr.status == "fail"
        vr2 = V.validate_entries(s, sched["term"], es + [brk])
        assert 7 in {f.check for f in vr2.errors}


def test_manual_check_messages(sched):
    es = _entries(sched)
    a = next(e for e in es if e["entry_type"] == "class")
    with db.session_scope() as s:
        tt = s.get(Timetable, sched["tt"])
        conf = T.check_candidate(s, tt, dict(a, start_period=5, end_period=5 + a["end_period"] - a["start_period"]), replace_id=a["id"])
    assert any(c["reason"] == "ไม่สามารถจัดคาบนี้ได้ เนื่องจากเป็นคาบพัก" for c in conf)


def test_published_is_locked_and_clone(sched):
    with db.session_scope() as s:
        T.submit_for_review(s, ADMIN, sched["tt"])
        T.approve(s, ADMIN, sched["tt"])
        T.publish(s, ADMIN, sched["tt"])
    with db.session_scope() as s:
        e = T.entries_of(s, sched["tt"])[0]
        try:
            T.delete_entry(s, ADMIN, sched["tt"], e.id)
            raise AssertionError("should be locked")
        except Exception as ex:
            assert "แก้ไขไม่ได้" in str(ex)
        new = T.clone(s, ADMIN, sched["tt"])
        new_id = new.id
    with db.session_scope() as s:
        assert len(T.entries_of(s, new_id)) == len(T.entries_of(s, sched["tt"]))
        e = next(x for x in T.entries_of(s, new_id) if x.entry_type == "class")
        T.delete_entry(s, ADMIN, new_id, e.id)
    with db.session_scope() as s:
        T.undo_last(s, ADMIN, new_id)
    with db.session_scope() as s:
        assert len(T.entries_of(s, new_id)) == len(T.entries_of(s, sched["tt"]))
        assert s.get(Timetable, sched["tt"]).status == "published"


def test_teacher_max_hours_enforced(loaded):
    with db.session_scope() as s:
        t = s.scalars(select(Teacher).where(Teacher.code == "T005")).one()
        t.max_hours_week = 5
    with db.session_scope() as s:
        checks, pb = readiness.run(s, loaded)
    assert any(c.level == "error" and "เกินชั่วโมงสอนสูงสุด" in c.message for c in checks)


def test_consecutive_respected(loaded):
    with db.session_scope() as s:
        c = s.scalars(select(Course).where(Course.code == "21910-2003")).one()
        c.consecutive = 2  # ปฏิบัติ 3 คาบ -> ช่วง 2 + 1
        pb = P.build(s, loaded)
    blocks = [L.blocks for L in pb.lessons if L.course_id == c.id and L.part == "practice"]
    assert blocks and all(b == [2, 1] for b in blocks)


def test_room_capacity_shortage_detected(loaded):
    with db.session_scope() as s:
        r = s.scalars(select(Room).where(Room.code == "R136")).one()
        r.capacity = 30  # ห้องเดียวที่จุการเรียนร่วม 2 กลุ่ม
    with db.session_scope() as s:
        checks, _ = readiness.run(s, loaded)
    assert any(c.level == "error" and "ความจุ" in c.message for c in checks)


def test_infeasible_reports_cause(loaded):
    with db.session_scope() as s:
        t = s.scalars(select(Teacher).where(Teacher.code == "T007")).one()
        ts = S.time_structure(s, loaded)
        for d in ts.days:
            for p in ts.teaching_periods:
                s.add(TeacherUnavailable(term_id=loaded, teacher_id=t.id, day=d, period=p))
    with db.session_scope() as s:
        checks, pb = readiness.run(s, loaded)
        assert any("ช่วงเวลาว่างเพียง" in c.message for c in checks)
        res = SV.solve(pb, time_limit=10)
    assert res.status in ("partial", "infeasible")
    assert res.report.get("conflicts")
    assert all(u["label"] for u in res.unplaced)


def test_hours_rule_must_be_confirmed(loaded):
    with db.session_scope() as s:
        S.put(s, "hour_to_period_confirmed", False, loaded)
        _, pb = readiness.run(s, loaded)
    assert any(i.code == "rate_unconfirmed" for i in pb.issues)
    res = SV.solve(pb, time_limit=5)
    assert res.status == "error"
