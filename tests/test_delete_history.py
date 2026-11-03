"""ลบตาราง -> เก็บในประวัติการลบ -> เรียกดู/กู้คืน และหมวดรายวิชาสำหรับหน้าเปรียบเทียบรายวิชา"""
import pytest
from sqlalchemy import select

from nvc import db, timetable_service as T
from nvc.models import Teacher, Timetable
from nvc.permissions import TEACHER, Ctx, PermissionDenied
from nvc.views import get_view, subject_catalog

from .conftest import ADMIN


def _teacher_ctx(s) -> Ctx:
    t = s.scalars(select(Teacher).order_by(Teacher.id)).first()
    return Ctx(99, t.code, TEACHER, t.name, teacher_id=t.id)


def test_subject_catalog_and_delete_restore(sched):
    tid, tt_id = sched["term"], sched["tt"]
    with db.session_scope() as s:
        T.approve(s, ADMIN, tt_id)
        T.publish(s, ADMIN, tt_id)
    # หมวดรายวิชา (ครูดูได้เมื่อเผยแพร่แล้ว)
    with db.session_scope() as s:
        teacher = _teacher_ctx(s)
        cat = subject_catalog(s, teacher, s.get(Timetable, tt_id))
        assert cat and {d["cat"] for d in cat.values()} <= {"general", "major", "activity"}
        assert "general" in {d["cat"] for d in cat.values()} and "major" in {d["cat"] for d in cat.values()}
        assert all(d["periods"] == sum(x["end"] - x["start"] + 1 for x in d["slots"]) for d in cat.values())
        with pytest.raises(PermissionDenied):
            T.delete_timetable(s, teacher, tt_id)
    # ลบ: ย้ายไปประวัติการลบ ไม่แสดงในรายการปกติ และไม่มีตารางเผยแพร่
    with db.session_scope() as s:
        rec_id = T.delete_timetable(s, ADMIN, tt_id, "ทดสอบ").id
    with db.session_scope() as s:
        tt = s.get(Timetable, tt_id)
        assert tt.status == "deleted" and tt.entries
        assert tt_id not in [t.id for t in T.list_timetables(s, ADMIN, tid)]
        assert T.latest_published(s, tid) is None
        rows = T.list_deleted(s, ADMIN, tid)
        assert rows[0][0].prev_status == "published" and rows[0][0].reason == "ทดสอบ"
        assert get_view(s, ADMIN, tt, "college").entries or get_view(s, ADMIN, tt, "college").sub_views  # ผู้ดูแลระบบเรียกดูได้
        teacher = _teacher_ctx(s)
        with pytest.raises(PermissionDenied):
            get_view(s, teacher, tt, "teacher", teacher.teacher_id)
        with pytest.raises(PermissionDenied):
            T.list_deleted(s, teacher, tid)
        with pytest.raises(PermissionDenied):
            T.delete_entry(s, ADMIN, tt_id, tt.entries[0].id)  # แก้ไขตารางที่ถูกลบไม่ได้
        with pytest.raises(ValueError):
            T.submit_for_review(s, ADMIN, tt_id)
    # กู้คืน: กลับเป็นเผยแพร่
    with db.session_scope() as s:
        T.restore_timetable(s, ADMIN, rec_id)
    with db.session_scope() as s:
        assert s.get(Timetable, tt_id).status == "published"
        with pytest.raises(ValueError):
            T.restore_timetable(s, ADMIN, rec_id)
    # ลบอีกครั้ง แล้วเผยแพร่ฉบับใหม่ -> กู้คืนฉบับเก่าเป็น “ถูกแทนที่”
    with db.session_scope() as s:
        rec2 = T.delete_timetable(s, ADMIN, tt_id).id
        new_id = T.clone(s, ADMIN, tt_id).id
    with db.session_scope() as s:
        T.validate(s, ADMIN, new_id)
        T.approve(s, ADMIN, new_id)
        T.publish(s, ADMIN, new_id)
    with db.session_scope() as s:
        T.restore_timetable(s, ADMIN, rec2)
    with db.session_scope() as s:
        assert s.get(Timetable, tt_id).status == "superseded"
        assert s.get(Timetable, new_id).status == "published"
        assert len(T.list_deleted(s, ADMIN)) == 2
