"""เงื่อนไขพิเศษข้อ 9–13 แบบเลือกใช้"""
from __future__ import annotations

from sqlalchemy import select

from nvc import db, problem as P, settings_service as S, solver as SV
from nvc.models import JointClass


def _set(tid, **kw):
    with db.session_scope() as s:
        if "room" in kw:
            S.put(s, "room_type_rule", kw["room"], tid)
        if "joint" in kw:
            S.put(s, "general_joint", kw["joint"], tid)
        for k in ("meeting", "homeroom", "activity"):
            if k in kw:
                v = S.get(s, k, tid)
                v["enabled"] = kw[k]
                S.put(s, k, v, tid)


def test_auto_general_joint_on_and_off(loaded):
    with db.session_scope() as s:
        auto = [j for j in s.scalars(select(JointClass)).all() if j.note == P.AUTO_JOINT_NOTE]
        pb = P.build(s, loaded)
        auto = [j for j in s.scalars(select(JointClass)).all() if j.note == P.AUTO_JOINT_NOTE]
    assert auto and all(len(j.group_ids) == 2 for j in auto)
    assert not pb.has_errors, [i.message for i in pb.issues if i.level == "error"]
    _set(loaded, joint=False)
    with db.session_scope() as s:
        P.build(s, loaded)
        left = [j for j in s.scalars(select(JointClass)).all()]
    assert left and all(j.note != P.AUTO_JOINT_NOTE for j in left)  # เหลือเฉพาะที่กำหนดเอง


def test_all_conditions_off(loaded):
    _set(loaded, room=False, joint=False, meeting=False, homeroom=False, activity=False)
    with db.session_scope() as s:
        pb = P.build(s, loaded)
    assert not pb.fixed  # ไม่มีประชุม/กิจกรรม
    assert not any(L.kind == "homeroom" for L in pb.lessons)
    acts = [L for L in pb.lessons if L.course_id and pb.courses[L.course_id]["type"] == "activity"]
    assert acts  # วิชากิจกรรมจัดเป็นรายการปกติ
    res = SV.solve(pb, time_limit=10)
    assert res.status == "success", res.report


def test_conditions_on_produce_fixed_items(loaded):
    with db.session_scope() as s:
        pb = P.build(s, loaded)
    kinds = {f.kind for f in pb.fixed}
    assert kinds == {"meeting", "activity"} and any(L.kind == "homeroom" for L in pb.lessons)
