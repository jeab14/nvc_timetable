"""การทดสอบสิทธิ์และรหัสผ่าน (หัวข้อ 25)"""
from __future__ import annotations

import pytest
from sqlalchemy import select

from nvc import auth, db, timetable_service as T, views
from nvc.importer import loader
from nvc.models import AuditLog, ClassGroup, Department, Student, Teacher, Timetable, User
from nvc.permissions import PermissionDenied

from .conftest import ADMIN


@pytest.fixture()
def accounts(sched):
    with db.session_scope() as s:
        if not auth.default_password_hash(s):
            auth.set_default_password(s, ADMIN, "Default@123")
        if not s.scalars(select(User).where(User.username == "T005")).first():
            auth.bulk_create_accounts(s, ADMIN, "teacher")
            auth.bulk_create_accounts(s, ADMIN, "student")
        dbt = s.scalars(select(Department).where(Department.code == "DBT")).one()
        t4 = s.scalars(select(Teacher).where(Teacher.code == "T004")).one()
        auth.grant_dept_admin(s, ADMIN, t4.id, [dbt.id])
    return sched


def _ctx(username):
    with db.session_scope() as s:
        return auth.ctx_from_user(s.scalars(select(User).where(User.username == username)).one())


def _published(sched):
    with db.session_scope() as s:
        tt = s.get(Timetable, sched["tt"])
        if tt.status != "published":
            if tt.status not in ("pending_review", "approved"):
                T.submit_for_review(s, ADMIN, tt.id)
            if tt.status != "approved":
                T.approve(s, ADMIN, tt.id)
            T.publish(s, ADMIN, tt.id)
    return sched["tt"]


def test_password_is_hashed_and_login(accounts):
    with db.session_scope() as s:
        u = s.scalars(select(User).where(User.username == "T005")).one()
        assert "Default@123" not in u.password_hash and u.password_hash.startswith("$2")
        ctx, msg, must = auth.authenticate(s, "T005", "Default@123")
    assert ctx and must is True
    with db.session_scope() as s:
        ctx2, msg2, _ = auth.authenticate(s, "T005", "wrong")
        ctx3, msg3, _ = auth.authenticate(s, "nobody", "wrong")
    assert ctx2 is None and msg2 == msg3  # ไม่เปิดเผยว่าส่วนใดผิด


def test_lockout(accounts):
    with db.session_scope() as s:
        for _ in range(5):
            auth.authenticate(s, "T006", "bad-password")
        ctx, msg, _ = auth.authenticate(s, "T006", "Default@123")
    assert ctx is None and "ล็อก" in msg


def test_change_and_reset_password(accounts):
    ctx = _ctx("T001")
    with db.session_scope() as s:
        auth.change_password(s, ctx, "Default@123", "NewPass@999")
    with db.session_scope() as s:
        assert auth.authenticate(s, "T001", "NewPass@999")[0] is not None
        auth.reset_password(s, ADMIN, ctx.user_id)
    with db.session_scope() as s:
        c, _, must = auth.authenticate(s, "T001", "Default@123")
        assert c is not None and must
        log = s.scalars(select(AuditLog).where(AuditLog.action == "reset_password")).all()
        assert log and all("Default" not in str(x.detail) for x in log)


def test_no_duplicate_account_for_dept_admin(accounts):
    with db.session_scope() as s:
        n = len(s.scalars(select(User).where(User.username == "T004")).all())
        u = s.scalars(select(User).where(User.username == "T004")).one()
        assert n == 1 and u.role == "dept_admin"


def test_student_scope(accounts):
    tt = _published(accounts)
    with db.session_scope() as s:
        st1, st2 = s.scalars(select(Student).order_by(Student.code)).all()[:2]
        u = s.scalars(select(User).where(User.student_id == st1.id)).one()
        ctx = auth.ctx_from_user(u)
        ttobj = s.get(Timetable, tt)
        views.get_view(s, ctx, ttobj, "student", st1.id)
        with pytest.raises(PermissionDenied):
            views.get_view(s, ctx, ttobj, "student", st2.id)
        other = s.scalars(select(ClassGroup).where(ClassGroup.code == "ACC1-2")).one()
        with pytest.raises(PermissionDenied):
            views.get_view(s, ctx, ttobj, "group", other.id)
        with pytest.raises(PermissionDenied):
            views.get_view(s, ctx, ttobj, "teacher", 1)
        with pytest.raises(PermissionDenied):
            T.run_auto(s, ctx, accounts["term"])
        with pytest.raises(PermissionDenied):
            loader.run_import(s, ctx, accounts["term"], "teacher", None, {}, "add")


def test_teacher_cannot_edit_or_manage(accounts):
    tt = _published(accounts)
    ctx = _ctx("T005")
    with db.session_scope() as s:
        new = T.clone(s, ADMIN, tt)
        nid = new.id
        e = next(x for x in T.entries_of(s, nid) if ctx.teacher_id in x.teacher_ids)
        eid = e.id
    with db.session_scope() as s:
        with pytest.raises(PermissionDenied):
            T.delete_entry(s, ctx, nid, eid)
        with pytest.raises(PermissionDenied):
            auth.create_user(s, ctx, "x", "admin")
        with pytest.raises(PermissionDenied):
            T.publish(s, ctx, nid)
        # ครูไม่เห็นตารางฉบับร่าง
        with pytest.raises(PermissionDenied):
            views.get_view(s, ctx, s.get(Timetable, nid), "teacher", ctx.teacher_id)


def test_dept_admin_cannot_cross_department(accounts):
    tt = _published(accounts)
    ctx = _ctx("T004")
    with db.session_scope() as s:
        acc = s.scalars(select(Department).where(Department.code == "ACC")).one()
        accg = s.scalars(select(ClassGroup).where(ClassGroup.code == "ACC1-1")).one()
        with pytest.raises(PermissionDenied):
            views.get_view(s, ctx, s.get(Timetable, tt), "group", accg.id)
        with pytest.raises(PermissionDenied):
            T.create_empty(s, ctx, accounts["term"], "x", acc.id)
        with pytest.raises(PermissionDenied):
            T.publish(s, ctx, tt)
        opts = views.options(s, ctx, accounts["term"], "group")
        assert opts and all(o[1].startswith("DBT") for o in opts)
        dbt_dep = s.scalars(select(Department).where(Department.code == "DBT")).one()
        vd = views.get_view(s, ctx, s.get(Timetable, tt), "department", dbt_dep.id)
        assert vd.sub_views and all(sv.title.startswith("ตารางเรียน กลุ่ม DBT") for sv in vd.sub_views)
        with pytest.raises(PermissionDenied):
            views.get_view(s, ctx, s.get(Timetable, tt), "department", acc.id)
        with pytest.raises(PermissionDenied):
            views.get_view(s, ctx, s.get(Timetable, tt), "college")


def test_dept_admin_edit_scope(accounts):
    tt = _published(accounts)
    ctx = _ctx("T004")
    with db.session_scope() as s:
        new = T.clone(s, ADMIN, tt)
        nid = new.id
        accg = s.scalars(select(ClassGroup).where(ClassGroup.code == "ACC1-1")).one()
        e = next(x for x in T.entries_of(s, nid) if accg.id in x.group_ids and x.entry_type == "class")
        eid = e.id
    with db.session_scope() as s:
        with pytest.raises(PermissionDenied):
            T.delete_entry(s, ctx, nid, eid)
