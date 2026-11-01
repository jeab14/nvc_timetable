"""โครงสร้างฐานข้อมูลหลัก (SQLAlchemy ORM) — ใช้ได้ทั้ง PostgreSQL และ SQLite"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Index,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def now() -> datetime:
    return datetime.now()


class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------- ภาคเรียน
class Term(Base):
    __tablename__ = "terms"
    id: Mapped[int] = mapped_column(primary_key=True)
    year: Mapped[int] = mapped_column(Integer)  # ปีการศึกษา พ.ศ.
    semester: Mapped[int] = mapped_column(Integer)  # 1, 2, 3(ฤดูร้อน)
    is_current: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    __table_args__ = (UniqueConstraint("year", "semester", name="uq_term"),)

    @property
    def label(self) -> str:
        return f"ภาคเรียนที่ {self.semester}/{self.year}"


class Setting(Base):
    """ค่าตั้งค่า key/value — term_id = None คือค่าส่วนกลาง"""

    __tablename__ = "settings"
    id: Mapped[int] = mapped_column(primary_key=True)
    term_id: Mapped[int | None] = mapped_column(ForeignKey("terms.id"), nullable=True)
    key: Mapped[str] = mapped_column(String(100))
    value: Mapped[dict | list | str | int | float | bool | None] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now)
    __table_args__ = (UniqueConstraint("term_id", "key", name="uq_setting"),)


# ---------------------------------------------------------------- ข้อมูลหลัก
class Department(Base):
    __tablename__ = "departments"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(50), unique=True)
    name: Mapped[str] = mapped_column(String(200))


class RoomType(Base):
    __tablename__ = "room_types"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(50), unique=True)
    name: Mapped[str] = mapped_column(String(100))
    # kind: theory / practice / activity / other
    kind: Mapped[str] = mapped_column(String(20), default="other")


class Teacher(Base):
    __tablename__ = "teachers"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(50), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    department_id: Mapped[int | None] = mapped_column(ForeignKey("departments.id"), nullable=True)
    position: Mapped[str | None] = mapped_column(String(200), nullable=True)
    max_hours_week: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_hours_day: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_meeting_member: Mapped[bool] = mapped_column(Boolean, default=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    department = relationship("Department")


class Room(Base):
    __tablename__ = "rooms"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(50), unique=True)
    name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    number: Mapped[str | None] = mapped_column(String(50), nullable=True)
    room_type_id: Mapped[int | None] = mapped_column(ForeignKey("room_types.id"), nullable=True)
    capacity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    department_id: Mapped[int | None] = mapped_column(ForeignKey("departments.id"), nullable=True)
    equipment: Mapped[str | None] = mapped_column(Text, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    room_type = relationship("RoomType")
    department = relationship("Department")

    @property
    def label(self) -> str:
        return self.number or self.code


class Course(Base):
    __tablename__ = "courses"
    id: Mapped[int] = mapped_column(primary_key=True)
    term_id: Mapped[int] = mapped_column(ForeignKey("terms.id"))
    code: Mapped[str] = mapped_column(String(50))
    name: Mapped[str] = mapped_column(String(300))
    # theory / practice / mixed / activity
    course_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    type_confirmed: Mapped[bool] = mapped_column(Boolean, default=True)
    theory_hours: Mapped[float | None] = mapped_column(Float, nullable=True)
    practice_hours: Mapped[float | None] = mapped_column(Float, nullable=True)
    credits: Mapped[float | None] = mapped_column(Float, nullable=True)
    total_periods: Mapped[int | None] = mapped_column(Integer, nullable=True)  # ถ้าต้นทางระบุจำนวนคาบโดยตรง
    consecutive: Mapped[int | None] = mapped_column(Integer, nullable=True)  # จำนวนคาบต่อเนื่องที่ต้องการ
    practice_room_types: Mapped[str | None] = mapped_column(String(300), nullable=True)  # รหัสประเภทห้อง คั่นด้วย ,
    department_id: Mapped[int | None] = mapped_column(ForeignKey("departments.id"), nullable=True)
    level: Mapped[str | None] = mapped_column(String(50), nullable=True)
    is_general: Mapped[bool | None] = mapped_column(Boolean, nullable=True)  # None = ใช้กฎรหัสวิชา
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    department = relationship("Department")
    __table_args__ = (UniqueConstraint("term_id", "code", name="uq_course_term_code"),)


class ClassGroup(Base):
    __tablename__ = "class_groups"
    id: Mapped[int] = mapped_column(primary_key=True)
    term_id: Mapped[int] = mapped_column(ForeignKey("terms.id"))
    code: Mapped[str] = mapped_column(String(50))
    name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    department_id: Mapped[int | None] = mapped_column(ForeignKey("departments.id"), nullable=True)
    level: Mapped[str | None] = mapped_column(String(50), nullable=True)
    advisor_teacher_id: Mapped[int | None] = mapped_column(ForeignKey("teachers.id"), nullable=True)
    size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    department = relationship("Department")
    advisor = relationship("Teacher")
    __table_args__ = (UniqueConstraint("term_id", "code", name="uq_group_term_code"),)

    @property
    def label(self) -> str:
        return self.name or self.code


class Student(Base):
    __tablename__ = "students"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(50), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    department_id: Mapped[int | None] = mapped_column(ForeignKey("departments.id"), nullable=True)
    status: Mapped[str | None] = mapped_column(String(50), nullable=True, default="กำลังศึกษา")
    department = relationship("Department")


class StudentMembership(Base):
    """นักเรียนอยู่กลุ่มเรียนใดในภาคเรียนใด"""

    __tablename__ = "student_memberships"
    id: Mapped[int] = mapped_column(primary_key=True)
    term_id: Mapped[int] = mapped_column(ForeignKey("terms.id"))
    student_id: Mapped[int] = mapped_column(ForeignKey("students.id"))
    group_id: Mapped[int] = mapped_column(ForeignKey("class_groups.id"))
    __table_args__ = (UniqueConstraint("term_id", "student_id", name="uq_membership"),)


class GroupCourse(Base):
    """การลงทะเบียนรายวิชาของกลุ่มเรียน"""

    __tablename__ = "group_courses"
    id: Mapped[int] = mapped_column(primary_key=True)
    term_id: Mapped[int] = mapped_column(ForeignKey("terms.id"))
    group_id: Mapped[int] = mapped_column(ForeignKey("class_groups.id"))
    course_id: Mapped[int] = mapped_column(ForeignKey("courses.id"))
    __table_args__ = (UniqueConstraint("group_id", "course_id", name="uq_group_course"),)


class Enrollment(Base):
    """การลงทะเบียนรายบุคคล — section_group_id = กลุ่มเรียน (section) ที่นักเรียนเข้าเรียนรายวิชานั้น"""

    __tablename__ = "enrollments"
    id: Mapped[int] = mapped_column(primary_key=True)
    term_id: Mapped[int] = mapped_column(ForeignKey("terms.id"))
    student_id: Mapped[int] = mapped_column(ForeignKey("students.id"))
    course_id: Mapped[int] = mapped_column(ForeignKey("courses.id"))
    section_group_id: Mapped[int | None] = mapped_column(ForeignKey("class_groups.id"), nullable=True)
    __table_args__ = (UniqueConstraint("student_id", "course_id", "term_id", name="uq_enrollment"),)


class TeacherQualification(Base):
    """รายวิชาที่ครูสามารถสอนได้ (ถ้ามีข้อมูล จะใช้ตรวจสอบการมอบหมาย)"""

    __tablename__ = "teacher_qualifications"
    id: Mapped[int] = mapped_column(primary_key=True)
    teacher_id: Mapped[int] = mapped_column(ForeignKey("teachers.id"))
    course_code: Mapped[str] = mapped_column(String(50))
    __table_args__ = (UniqueConstraint("teacher_id", "course_code", name="uq_qual"),)


class Assignment(Base):
    """การมอบหมายครูผู้สอน: ครู - รายวิชา - กลุ่มเรียน - ภาคเรียน - จำนวนคาบ - ส่วนของวิชา"""

    __tablename__ = "assignments"
    id: Mapped[int] = mapped_column(primary_key=True)
    term_id: Mapped[int] = mapped_column(ForeignKey("terms.id"))
    teacher_id: Mapped[int] = mapped_column(ForeignKey("teachers.id"))
    course_id: Mapped[int] = mapped_column(ForeignKey("courses.id"))
    group_id: Mapped[int] = mapped_column(ForeignKey("class_groups.id"))
    part: Mapped[str] = mapped_column(String(20), default="all")  # all / theory / practice
    periods: Mapped[int | None] = mapped_column(Integer, nullable=True)  # None = เต็มตามส่วนที่รับผิดชอบ
    __table_args__ = (
        UniqueConstraint("term_id", "teacher_id", "course_id", "group_id", "part", name="uq_assignment"),
    )


class JointClass(Base):
    """การเรียนร่วม: หลายกลุ่มเรียนเรียนรายวิชาเดียวกันพร้อมกัน (สอนครั้งเดียว)"""

    __tablename__ = "joint_classes"
    id: Mapped[int] = mapped_column(primary_key=True)
    term_id: Mapped[int] = mapped_column(ForeignKey("terms.id"))
    course_id: Mapped[int] = mapped_column(ForeignKey("courses.id"))
    teacher_id: Mapped[int | None] = mapped_column(ForeignKey("teachers.id"), nullable=True)
    group_ids: Mapped[list] = mapped_column(JSON)  # [group_id, ...]
    note: Mapped[str | None] = mapped_column(String(300), nullable=True)


class TeacherUnavailable(Base):
    __tablename__ = "teacher_unavailable"
    id: Mapped[int] = mapped_column(primary_key=True)
    term_id: Mapped[int] = mapped_column(ForeignKey("terms.id"))
    teacher_id: Mapped[int] = mapped_column(ForeignKey("teachers.id"))
    day: Mapped[int] = mapped_column(Integer)  # 0=จันทร์ ... 4=ศุกร์
    period: Mapped[int] = mapped_column(Integer)
    preferred: Mapped[bool] = mapped_column(Boolean, default=False)  # True = เวลาที่ต้องการสอน (ข้อจำกัดผ่อนปรน)
    __table_args__ = (UniqueConstraint("term_id", "teacher_id", "day", "period", "preferred", name="uq_tunavail"),)


class RoomUnavailable(Base):
    __tablename__ = "room_unavailable"
    id: Mapped[int] = mapped_column(primary_key=True)
    term_id: Mapped[int] = mapped_column(ForeignKey("terms.id"))
    room_id: Mapped[int] = mapped_column(ForeignKey("rooms.id"))
    day: Mapped[int] = mapped_column(Integer)
    period: Mapped[int] = mapped_column(Integer)
    __table_args__ = (UniqueConstraint("term_id", "room_id", "day", "period", name="uq_runavail"),)


# ---------------------------------------------------------------- ตารางสอน
class Timetable(Base):
    """ตารางฉบับหนึ่ง (version) ของภาคเรียน"""

    __tablename__ = "timetables"
    id: Mapped[int] = mapped_column(primary_key=True)
    term_id: Mapped[int] = mapped_column(ForeignKey("terms.id"))
    version: Mapped[int] = mapped_column(Integer)
    name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # not_scheduled / running / success / partial / infeasible / validation_failed /
    # pending_review / approved / published / superseded
    status: Mapped[str] = mapped_column(String(30), default="not_scheduled")
    validation_status: Mapped[str | None] = mapped_column(String(30), nullable=True)  # pass / warn / fail
    validation_report: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    solver_report: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("timetables.id"), nullable=True)
    scope_department_id: Mapped[int | None] = mapped_column(ForeignKey("departments.id"), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    approved_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    lock_version: Mapped[int] = mapped_column(Integer, default=0)  # ป้องกันแก้ไขพร้อมกัน
    entries = relationship("Entry", back_populates="timetable", cascade="all, delete-orphan")


class Entry(Base):
    __tablename__ = "entries"
    id: Mapped[int] = mapped_column(primary_key=True)
    timetable_id: Mapped[int] = mapped_column(ForeignKey("timetables.id"))
    day: Mapped[int] = mapped_column(Integer)
    start_period: Mapped[int] = mapped_column(Integer)
    end_period: Mapped[int] = mapped_column(Integer)
    start_time: Mapped[str] = mapped_column(String(5))
    end_time: Mapped[str] = mapped_column(String(5))
    # class / homeroom / meeting / activity
    entry_type: Mapped[str] = mapped_column(String(20), default="class")
    part: Mapped[str | None] = mapped_column(String(20), nullable=True)  # theory / practice / all
    course_id: Mapped[int | None] = mapped_column(ForeignKey("courses.id"), nullable=True)
    room_id: Mapped[int | None] = mapped_column(ForeignKey("rooms.id"), nullable=True)
    schedule_mode: Mapped[str] = mapped_column(String(20), default="auto")  # auto / manual / fixed
    # snapshot สำหรับดูย้อนหลัง
    course_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    course_name: Mapped[str | None] = mapped_column(String(300), nullable=True)
    room_label: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    updated_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now)
    timetable = relationship("Timetable", back_populates="entries")
    teachers = relationship("EntryTeacher", cascade="all, delete-orphan", lazy="selectin")
    groups = relationship("EntryGroup", cascade="all, delete-orphan", lazy="selectin")
    __table_args__ = (Index("ix_entry_tt_day", "timetable_id", "day"),)

    @property
    def teacher_ids(self) -> list[int]:
        return [t.teacher_id for t in self.teachers]

    @property
    def group_ids(self) -> list[int]:
        return [g.group_id for g in self.groups]


class EntryTeacher(Base):
    __tablename__ = "entry_teachers"
    id: Mapped[int] = mapped_column(primary_key=True)
    entry_id: Mapped[int] = mapped_column(ForeignKey("entries.id"))
    teacher_id: Mapped[int] = mapped_column(ForeignKey("teachers.id"))
    teacher_name: Mapped[str | None] = mapped_column(String(200), nullable=True)


class EntryGroup(Base):
    __tablename__ = "entry_groups"
    id: Mapped[int] = mapped_column(primary_key=True)
    entry_id: Mapped[int] = mapped_column(ForeignKey("entries.id"))
    group_id: Mapped[int] = mapped_column(ForeignKey("class_groups.id"))
    group_code: Mapped[str | None] = mapped_column(String(50), nullable=True)


class TimetableChange(Base):
    """บันทึกการแก้ไขตาราง (ใช้สำหรับย้อนกลับ/undo และตรวจสอบย้อนหลัง)"""

    __tablename__ = "timetable_changes"
    id: Mapped[int] = mapped_column(primary_key=True)
    timetable_id: Mapped[int] = mapped_column(ForeignKey("timetables.id"))
    action: Mapped[str] = mapped_column(String(30))  # add / update / delete
    entry_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    before: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    after: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    user: Mapped[str | None] = mapped_column(String(100), nullable=True)
    undone: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class ChangeRequest(Base):
    """คำขอเปลี่ยนแปลงตารางจากครู (รอฝ่ายวิชาการพิจารณา)"""

    __tablename__ = "change_requests"
    id: Mapped[int] = mapped_column(primary_key=True)
    timetable_id: Mapped[int] = mapped_column(ForeignKey("timetables.id"))
    entry_id: Mapped[int] = mapped_column(Integer)
    requested_by: Mapped[str] = mapped_column(String(100))
    new_day: Mapped[int] = mapped_column(Integer)
    new_start: Mapped[int] = mapped_column(Integer)
    new_room_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending/approved/rejected
    reviewed_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


# ---------------------------------------------------------------- ผู้ใช้งาน
class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(100), unique=True)
    display_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    password_hash: Mapped[str] = mapped_column(String(200))
    # admin / dept_admin / teacher / student
    role: Mapped[str] = mapped_column(String(20))
    teacher_id: Mapped[int | None] = mapped_column(ForeignKey("teachers.id"), nullable=True)
    student_id: Mapped[int | None] = mapped_column(ForeignKey("students.id"), nullable=True)
    dept_scope: Mapped[list | None] = mapped_column(JSON, nullable=True)  # [department_id] สำหรับ dept_admin
    can_edit_timetable: Mapped[bool] = mapped_column(Boolean, default=False)  # ครูแก้ไขตารางได้
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    failed_attempts: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_login: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str | None] = mapped_column(String(100), nullable=True)
    action: Mapped[str] = mapped_column(String(100))
    entity: Mapped[str | None] = mapped_column(String(100), nullable=True)
    detail: Mapped[dict | str | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class ImportBatch(Base):
    """ข้อมูลต้นทางที่นำเข้า (เก็บไว้เพื่อตรวจสอบย้อนหลัง)"""

    __tablename__ = "import_batches"
    id: Mapped[int] = mapped_column(primary_key=True)
    term_id: Mapped[int] = mapped_column(ForeignKey("terms.id"))
    filename: Mapped[str] = mapped_column(String(300))
    sheet: Mapped[str | None] = mapped_column(String(200), nullable=True)
    entity: Mapped[str] = mapped_column(String(50))
    mapping: Mapped[dict] = mapped_column(JSON)
    row_count: Mapped[int] = mapped_column(Integer)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    raw_rows: Mapped[list | None] = mapped_column(JSON, nullable=True)
    mode: Mapped[str] = mapped_column(String(20))
    created_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
