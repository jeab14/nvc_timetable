"""ค่าตั้งค่าของระบบ: โครงสร้างเวลาเรียน กฎการจัดตาราง ฯลฯ

ค่าเริ่มต้นยึดตามตัวอย่างตารางสอนของวิทยาลัยอาชีวศึกษานครราชสีมา และแก้ไขได้จากหน้า "ตั้งค่า"
โดยไม่ต้องแก้ไขโค้ด
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Setting

DAY_NAMES = ["จันทร์", "อังคาร", "พุธ", "พฤหัสบดี", "ศุกร์", "เสาร์", "อาทิตย์"]

DEFAULT_TIME_STRUCTURE = [
    {"no": 0, "label": "ก่อนคาบที่ 1", "start": "07:45", "end": "08:00", "kind": "pre"},
    {"no": 1, "label": "คาบที่ 1", "start": "08:00", "end": "09:00", "kind": "teaching"},
    {"no": 2, "label": "คาบที่ 2", "start": "09:00", "end": "10:00", "kind": "teaching"},
    {"no": 3, "label": "คาบที่ 3", "start": "10:00", "end": "11:00", "kind": "teaching"},
    {"no": 4, "label": "คาบที่ 4", "start": "11:00", "end": "12:00", "kind": "teaching"},
    {"no": 5, "label": "คาบที่ 5 (พัก)", "start": "12:00", "end": "13:00", "kind": "break"},
    {"no": 6, "label": "คาบที่ 6", "start": "13:00", "end": "14:00", "kind": "teaching"},
    {"no": 7, "label": "คาบที่ 7", "start": "14:00", "end": "15:00", "kind": "teaching"},
    {"no": 8, "label": "คาบที่ 8", "start": "15:00", "end": "16:00", "kind": "teaching"},
    {"no": 9, "label": "คาบที่ 9", "start": "16:00", "end": "17:00", "kind": "teaching"},
    {"no": 10, "label": "คาบที่ 10", "start": "17:00", "end": "18:00", "kind": "teaching"},
    {"no": 11, "label": "คาบที่ 11", "start": "18:00", "end": "19:00", "kind": "special"},
]

DEFAULTS: dict = {
    "time_structure": DEFAULT_TIME_STRUCTURE,
    "days": [0, 1, 2, 3, 4],
    "max_periods_per_day_group": 10,
    "allow_special_periods": False,  # เปิดใช้คาบประเภท special (เช่น คาบที่ 11) — ต้องเป็นผู้ดูแลระบบ
    "meeting": {"enabled": True, "day": 1, "periods": [8], "room_id": None, "title": "ประชุมหัวหน้าแผนก/หัวหน้างาน"},
    "activity": {"enabled": True, "day": 2, "periods": [8, 9], "lock_all_groups": True},
    "homeroom": {
        "enabled": True,
        "periods_per_week": 1,
        "allowed_slots": None,  # None = ทุกคาบเรียนปกติ, หรือ [[day, period], ...]
        "needs_room": False,
        "count_as_teaching": False,
    },
    "general_prefixes": ["20000", "30000"],
    "general_joint_max_groups": 2,
    "hour_to_period_rate": 1.0,
    "hour_to_period_confirmed": False,
    "default_block_max": 4,
    "solver_time_limit": 60,
    "soft_weights": {"group_gaps": 5, "teacher_gaps": 2, "late_period": 1, "same_day_repeat": 8, "preferred": 3, "stability": 4},
    "teacher_can_edit_default": False,
}


def _row(s: Session, key: str, term_id: int | None) -> Setting | None:
    return s.scalars(select(Setting).where(Setting.key == key, Setting.term_id.is_(None) if term_id is None else Setting.term_id == term_id)).first()


def get(s: Session, key: str, term_id: int | None = None):
    """ค่าของภาคเรียน -> ค่าส่วนกลาง -> ค่าเริ่มต้น"""
    if term_id is not None:
        r = _row(s, key, term_id)
        if r is not None:
            return copy.deepcopy(r.value)
    r = _row(s, key, None)
    if r is not None:
        return copy.deepcopy(r.value)
    return copy.deepcopy(DEFAULTS.get(key))


def put(s: Session, key: str, value, term_id: int | None = None) -> None:
    r = _row(s, key, term_id)
    if r is None:
        s.add(Setting(key=key, value=value, term_id=term_id))
    else:
        r.value = value
    s.flush()


@dataclass
class TimeStructure:
    periods: list[dict]
    days: list[int]
    max_per_day: int
    allow_special: bool = False
    by_no: dict = field(default_factory=dict)

    def __post_init__(self):
        self.periods = sorted(self.periods, key=lambda p: p["no"])
        self.by_no = {p["no"]: p for p in self.periods}

    @property
    def display_periods(self) -> list[dict]:
        """คาบที่แสดงเป็นคอลัมน์บนตาราง (ซ่อน special ถ้าไม่เปิดใช้)"""
        return [p for p in self.periods if p["kind"] != "special" or self.allow_special]

    @property
    def teaching_periods(self) -> list[int]:
        out = []
        for p in self.periods:
            if p["kind"] == "teaching" or (p["kind"] == "special" and self.allow_special):
                out.append(p["no"])
        return out

    @property
    def break_periods(self) -> list[int]:
        return [p["no"] for p in self.periods if p["kind"] == "break"]

    def is_teaching(self, no: int) -> bool:
        return no in self.teaching_periods

    def span_ok(self, start: int, length: int) -> bool:
        """ช่วงคาบต่อเนื่องต้องเป็นคาบเรียนทั้งหมด ไม่มีคาบพักคั่น และหมายเลขคาบติดกัน"""
        nos = list(range(start, start + length))
        tp = set(self.teaching_periods)
        return all(n in tp for n in nos)

    def valid_starts(self, length: int) -> list[int]:
        return [p for p in self.teaching_periods if self.span_ok(p, length)]

    def start_time(self, no: int) -> str:
        return self.by_no[no]["start"]

    def end_time(self, no: int) -> str:
        return self.by_no[no]["end"]

    def max_block(self) -> int:
        best = cur = 0
        prev = None
        for n in self.teaching_periods:
            cur = cur + 1 if prev is not None and n == prev + 1 else 1
            best = max(best, cur)
            prev = n
        return best

    def slots(self) -> list[tuple[int, int]]:
        return [(d, p) for d in self.days for p in self.teaching_periods]


def time_structure(s: Session, term_id: int | None) -> TimeStructure:
    return TimeStructure(
        periods=get(s, "time_structure", term_id),
        days=get(s, "days", term_id),
        max_per_day=int(get(s, "max_periods_per_day_group", term_id)),
        allow_special=bool(get(s, "allow_special_periods", term_id)),
    )


def validate_time_structure(periods: list[dict]) -> list[str]:
    """ตรวจรูปแบบโครงสร้างเวลา คืนรายการข้อผิดพลาด"""
    import re

    errs = []
    seen = set()
    pat = re.compile(r"^\d{2}:\d{2}$")
    prev_end = None
    for p in sorted(periods, key=lambda x: x["no"]):
        if p["no"] in seen:
            errs.append(f"หมายเลขคาบ {p['no']} ซ้ำ")
        seen.add(p["no"])
        if not pat.match(str(p.get("start", ""))) or not pat.match(str(p.get("end", ""))):
            errs.append(f"คาบ {p['no']}: รูปแบบเวลาต้องเป็น HH:MM")
            continue
        if p["start"] >= p["end"]:
            errs.append(f"คาบ {p['no']}: เวลาเริ่มต้องน้อยกว่าเวลาสิ้นสุด")
        if prev_end and p["start"] < prev_end:
            errs.append(f"คาบ {p['no']}: เวลาทับซ้อนกับคาบก่อนหน้า")
        prev_end = p["end"]
        if p.get("kind") not in ("pre", "teaching", "break", "special"):
            errs.append(f"คาบ {p['no']}: ประเภทคาบไม่ถูกต้อง")
    return errs


def is_general_course(code: str, prefixes: list[str], override: bool | None = None) -> bool:
    if override is not None:
        return bool(override)
    c = (code or "").strip()
    return any(c.startswith(p) for p in prefixes)
