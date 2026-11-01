"""การควบคุมสิทธิ์ — ตรวจสอบในชั้นบริการ/การเข้าถึงข้อมูล ไม่ใช่เพียงซ่อนปุ่ม"""
from __future__ import annotations

from dataclasses import dataclass, field

ADMIN = "admin"
DEPT_ADMIN = "dept_admin"
TEACHER = "teacher"
STUDENT = "student"

ROLE_LABELS = {
    ADMIN: "ฝ่ายวิชาการ/ผู้ดูแลระบบ",
    DEPT_ADMIN: "ฝ่ายวิชาการประจำแผนก",
    TEACHER: "ครู",
    STUDENT: "นักเรียน",
}


class PermissionDenied(Exception):
    def __init__(self, msg: str = "คุณไม่มีสิทธิ์ดำเนินการนี้"):
        super().__init__(msg)


@dataclass
class Ctx:
    """ข้อมูลผู้ใช้งานปัจจุบัน ส่งเข้าทุกฟังก์ชันบริการเพื่อตรวจสิทธิ์"""

    user_id: int
    username: str
    role: str
    display_name: str = ""
    teacher_id: int | None = None
    student_id: int | None = None
    dept_scope: list[int] = field(default_factory=list)
    can_edit_timetable: bool = False

    @property
    def is_admin(self) -> bool:
        return self.role == ADMIN

    @property
    def is_staff(self) -> bool:
        return self.role in (ADMIN, DEPT_ADMIN)


def require(ctx: Ctx | None, *roles: str) -> Ctx:
    if ctx is None:
        raise PermissionDenied("กรุณาเข้าสู่ระบบ")
    if roles and ctx.role not in roles:
        raise PermissionDenied()
    return ctx


def dept_filter(ctx: Ctx) -> list[int] | None:
    """None = เห็นทุกแผนก; list = เห็นเฉพาะแผนกในรายการ"""
    if ctx.role == DEPT_ADMIN:
        return list(ctx.dept_scope or [])
    return None


def can_manage_data(ctx: Ctx) -> bool:
    return ctx.role == ADMIN


def check_dept(ctx: Ctx, department_id: int | None) -> None:
    """ฝ่ายวิชาการประจำแผนกเข้าถึงได้เฉพาะแผนกของตน"""
    f = dept_filter(ctx)
    if f is None:
        return
    if department_id is None or department_id not in f:
        raise PermissionDenied("ไม่สามารถเข้าถึงข้อมูลของแผนกอื่นได้")


def can_edit_timetable(ctx: Ctx) -> bool:
    if ctx.role in (ADMIN, DEPT_ADMIN):
        return True
    if ctx.role == TEACHER and ctx.can_edit_timetable:
        return True
    return False


def can_publish(ctx: Ctx) -> bool:
    return ctx.role == ADMIN
