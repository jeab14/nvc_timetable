"""ยืนยันตัวตน รหัสผ่าน และบัญชีผู้ใช้งาน

- รหัสผ่านถูกแฮชด้วย bcrypt (cost 12) หลังผ่าน SHA-256 เพื่อรองรับรหัสผ่านยาว/ภาษาไทยเกิน 72 ไบต์
- ไม่มีการบันทึกรหัสผ่านแบบข้อความธรรมดาในฐานข้อมูลหรือประวัติ
- จำกัดความพยายามเข้าสู่ระบบ: ผิดเกิน LOGIN_MAX_ATTEMPTS ครั้ง ล็อกบัญชีชั่วคราว
"""
from __future__ import annotations

import base64
import hashlib
from datetime import datetime, timedelta

import bcrypt
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import audit, config, settings_service
from .models import Student, Teacher, User
from .permissions import ADMIN, DEPT_ADMIN, STUDENT, TEACHER, Ctx, PermissionDenied, require

GENERIC_LOGIN_ERROR = "ชื่อผู้ใช้หรือรหัสผ่านไม่ถูกต้อง"


def _prep(pw: str) -> bytes:
    return base64.b64encode(hashlib.sha256(pw.encode("utf-8")).digest())


def hash_password(pw: str) -> str:
    return bcrypt.hashpw(_prep(pw), bcrypt.gensalt(12)).decode("ascii")


def verify_password(pw: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(_prep(pw), hashed.encode("ascii"))
    except Exception:
        return False


def password_problems(pw: str, username: str = "") -> list[str]:
    errs = []
    if len(pw) < 8:
        errs.append("รหัสผ่านต้องมีอย่างน้อย 8 ตัวอักษร")
    if username and pw.strip().lower() == username.strip().lower():
        errs.append("รหัสผ่านต้องไม่เหมือนชื่อผู้ใช้")
    if pw.isdigit() and len(set(pw)) <= 2:
        errs.append("รหัสผ่านง่ายเกินไป")
    return errs


_DUMMY: list[str] = []


def _dummy_hash() -> str:
    if not _DUMMY:
        _DUMMY.append(hash_password("not-a-real-password"))
    return _DUMMY[0]


def ctx_from_user(u: User) -> Ctx:
    return Ctx(
        user_id=u.id,
        username=u.username,
        role=u.role,
        display_name=u.display_name or u.username,
        teacher_id=u.teacher_id,
        student_id=u.student_id,
        dept_scope=list(u.dept_scope or []),
        can_edit_timetable=bool(u.can_edit_timetable),
    )


def has_any_user(s: Session) -> bool:
    return (s.scalar(select(func.count(User.id))) or 0) > 0


def bootstrap_admin(s: Session, username: str, password: str, display_name: str) -> User:
    """สร้างผู้ดูแลระบบคนแรก — ทำได้เฉพาะเมื่อยังไม่มีบัญชีใดในระบบ (ไม่ใช่การสมัครสมาชิก)"""
    if has_any_user(s):
        raise PermissionDenied("ระบบมีผู้ดูแลแล้ว")
    errs = password_problems(password, username)
    if errs:
        raise ValueError(" / ".join(errs))
    u = User(
        username=username.strip(),
        display_name=display_name.strip() or username.strip(),
        password_hash=hash_password(password),
        role=ADMIN,
        must_change_password=False,
    )
    s.add(u)
    s.flush()
    audit.log(s, u.username, "bootstrap_admin", "user", {"user_id": u.id})
    return u


def authenticate(s: Session, username: str, password: str) -> tuple[Ctx | None, str, bool]:
    """คืน (ctx, ข้อความ, ต้องเปลี่ยนรหัสผ่านหรือไม่)"""
    username = (username or "").strip()
    u = s.scalars(select(User).where(User.username == username)).first()
    nowt = datetime.now()
    if u is None or not u.active:
        # ใช้เวลาใกล้เคียงกันเพื่อไม่เปิดเผยว่าไม่มีบัญชีนี้
        verify_password(password or "", _dummy_hash())
        return None, GENERIC_LOGIN_ERROR, False
    if u.locked_until and u.locked_until > nowt:
        mins = int((u.locked_until - nowt).total_seconds() // 60) + 1
        return None, f"บัญชีถูกล็อกชั่วคราวเนื่องจากเข้าสู่ระบบผิดหลายครั้ง กรุณาลองใหม่ในอีก {mins} นาที", False
    if not verify_password(password or "", u.password_hash):
        u.failed_attempts = (u.failed_attempts or 0) + 1
        if u.failed_attempts >= config.LOGIN_MAX_ATTEMPTS:
            u.locked_until = nowt + timedelta(minutes=config.LOGIN_LOCK_MINUTES)
            u.failed_attempts = 0
            audit.log(s, u.username, "account_locked", "user", {"user_id": u.id})
        return None, GENERIC_LOGIN_ERROR, False
    u.failed_attempts = 0
    u.locked_until = None
    u.last_login = nowt
    audit.log(s, u.username, "login", "user", {"user_id": u.id})
    return ctx_from_user(u), "เข้าสู่ระบบสำเร็จ", bool(u.must_change_password)


def change_password(s: Session, ctx: Ctx, old: str, new: str) -> None:
    u = s.get(User, ctx.user_id)
    if u is None or not verify_password(old, u.password_hash):
        raise ValueError("รหัสผ่านเดิมไม่ถูกต้อง")
    errs = password_problems(new, u.username)
    if old == new:
        errs.append("รหัสผ่านใหม่ต้องไม่ซ้ำกับรหัสผ่านเดิม")
    if errs:
        raise ValueError(" / ".join(errs))
    u.password_hash = hash_password(new)
    u.must_change_password = False
    audit.log(s, ctx.username, "change_password", "user", {"user_id": u.id})


# ------------------------------------------------------------- รหัสผ่านเริ่มต้น
def set_default_password(s: Session, ctx: Ctx, pw: str) -> None:
    require(ctx, ADMIN)
    errs = password_problems(pw)
    if errs:
        raise ValueError(" / ".join(errs))
    settings_service.put(s, "default_password_hash", hash_password(pw))
    audit.log(s, ctx.username, "set_default_password", "settings")


def default_password_hash(s: Session) -> str | None:
    return settings_service.get(s, "default_password_hash")


def reset_password(s: Session, ctx: Ctx, user_id: int) -> None:
    require(ctx, ADMIN)
    h = default_password_hash(s)
    if not h:
        raise ValueError("ยังไม่ได้กำหนดรหัสผ่านเริ่มต้นในหน้าตั้งค่า")
    u = s.get(User, user_id)
    if u is None:
        raise ValueError("ไม่พบบัญชีผู้ใช้")
    u.password_hash = h
    u.must_change_password = True
    u.failed_attempts = 0
    u.locked_until = None
    audit.log(s, ctx.username, "reset_password", "user", {"user_id": u.id, "target": u.username})


def create_user(
    s: Session,
    ctx: Ctx,
    username: str,
    role: str,
    display_name: str = "",
    teacher_id: int | None = None,
    student_id: int | None = None,
    dept_scope: list[int] | None = None,
) -> User:
    require(ctx, ADMIN)
    if role not in (ADMIN, DEPT_ADMIN, TEACHER, STUDENT):
        raise ValueError("ประเภทสิทธิ์ไม่ถูกต้อง")
    username = username.strip()
    if not username:
        raise ValueError("กรุณาระบุชื่อผู้ใช้")
    if s.scalars(select(User).where(User.username == username)).first():
        raise ValueError(f"ชื่อผู้ใช้ {username} มีอยู่แล้ว")
    if teacher_id and s.scalars(select(User).where(User.teacher_id == teacher_id)).first():
        raise ValueError("ครูคนนี้มีบัญชีผู้ใช้อยู่แล้ว — ให้แก้ไขสิทธิ์ของบัญชีเดิมแทน")
    h = default_password_hash(s)
    if not h:
        raise ValueError("ยังไม่ได้กำหนดรหัสผ่านเริ่มต้นในหน้าตั้งค่า")
    u = User(
        username=username,
        display_name=display_name or username,
        password_hash=h,
        role=role,
        teacher_id=teacher_id,
        student_id=student_id,
        dept_scope=dept_scope or [],
        must_change_password=True,
    )
    s.add(u)
    s.flush()
    audit.log(s, ctx.username, "create_user", "user", {"user_id": u.id, "username": username, "role": role})
    return u


def grant_dept_admin(s: Session, ctx: Ctx, teacher_id: int, dept_ids: list[int]) -> User:
    """ให้สิทธิ์ฝ่ายวิชาการประจำแผนกแก่ครูที่มีอยู่ — เชื่อมกับบัญชีเดิม ไม่สร้างซ้ำ"""
    require(ctx, ADMIN)
    if not dept_ids:
        raise ValueError("ต้องกำหนดแผนกที่รับผิดชอบอย่างน้อย 1 แผนก")
    t = s.get(Teacher, teacher_id)
    if t is None:
        raise ValueError("ไม่พบครู")
    u = s.scalars(select(User).where(User.teacher_id == teacher_id)).first()
    if u is None:
        u = create_user(s, ctx, t.code, DEPT_ADMIN, t.name, teacher_id=t.id, dept_scope=dept_ids)
    else:
        if u.role == ADMIN:
            raise ValueError("บัญชีนี้เป็นผู้ดูแลระบบอยู่แล้ว")
        u.role = DEPT_ADMIN
        u.dept_scope = list(dept_ids)
    audit.log(s, ctx.username, "grant_dept_admin", "user", {"user_id": u.id, "depts": dept_ids})
    return u


def revoke_dept_admin(s: Session, ctx: Ctx, user_id: int) -> None:
    require(ctx, ADMIN)
    u = s.get(User, user_id)
    if u and u.role == DEPT_ADMIN:
        u.role = TEACHER if u.teacher_id else u.role
        u.dept_scope = []
        audit.log(s, ctx.username, "revoke_dept_admin", "user", {"user_id": u.id})


def bulk_create_accounts(s: Session, ctx: Ctx, kind: str) -> dict:
    """สร้างบัญชีให้ครู/นักเรียนที่ยังไม่มีบัญชี (ชื่อผู้ใช้ = รหัสครู/รหัสนักเรียน)"""
    require(ctx, ADMIN)
    h = default_password_hash(s)
    if not h:
        raise ValueError("ยังไม่ได้กำหนดรหัสผ่านเริ่มต้นในหน้าตั้งค่า")
    existing = set(s.scalars(select(User.username)).all())
    created = skipped = 0
    if kind == TEACHER:
        linked = set(x for x in s.scalars(select(User.teacher_id)).all() if x)
        for t in s.scalars(select(Teacher).where(Teacher.active.is_(True))).all():
            if t.id in linked or t.code in existing:
                skipped += 1
                continue
            s.add(User(username=t.code, display_name=t.name, password_hash=h, role=TEACHER, teacher_id=t.id, must_change_password=True))
            created += 1
    elif kind == STUDENT:
        linked = set(x for x in s.scalars(select(User.student_id)).all() if x)
        for st in s.scalars(select(Student)).all():
            if st.id in linked or st.code in existing:
                skipped += 1
                continue
            s.add(User(username=st.code, display_name=st.name, password_hash=h, role=STUDENT, student_id=st.id, must_change_password=True))
            created += 1
    else:
        raise ValueError("kind ไม่ถูกต้อง")
    s.flush()
    audit.log(s, ctx.username, "bulk_create_accounts", "user", {"kind": kind, "created": created})
    return {"created": created, "skipped": skipped}


def set_active(s: Session, ctx: Ctx, user_id: int, active: bool) -> None:
    require(ctx, ADMIN)
    u = s.get(User, user_id)
    if u is None:
        raise ValueError("ไม่พบบัญชี")
    if u.id == ctx.user_id and not active:
        raise ValueError("ไม่สามารถระงับบัญชีของตนเองได้")
    u.active = active
    audit.log(s, ctx.username, "set_active", "user", {"user_id": u.id, "active": active})


def set_teacher_edit(s: Session, ctx: Ctx, user_id: int, allow: bool) -> None:
    require(ctx, ADMIN)
    u = s.get(User, user_id)
    if u is None:
        raise ValueError("ไม่พบบัญชี")
    u.can_edit_timetable = allow
    audit.log(s, ctx.username, "set_teacher_edit", "user", {"user_id": u.id, "allow": allow})
