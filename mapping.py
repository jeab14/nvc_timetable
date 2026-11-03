"""วิเคราะห์หัวคอลัมน์และเนื้อหา เพื่อเสนอการจับคู่คอลัมน์กับฟิลด์มาตรฐาน

ใช้กฎ (rule-based) ทั้งหมด — ทำงานได้โดยไม่ต้องพึ่งบริการภายนอก/ไม่มีค่าใช้จ่าย
คะแนน = ความใกล้เคียงของชื่อคอลัมน์ (คำพ้องไทย/อังกฤษ) + ความสอดคล้องของข้อมูลตัวอย่างในคอลัมน์
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher

import pandas as pd

from .schemas import ENTITIES, alt_required_satisfied

AUTO_THRESHOLD = 0.80  # มั่นใจ จับคู่อัตโนมัติ (ยังแก้ไขได้)
SUGGEST_THRESHOLD = 0.50  # เสนอ แต่ต้องให้ผู้ใช้ยืนยัน

_PUNCT = re.compile(r"[\s_\-\.\(\)\[\]/:]+")

DAY_WORDS = {
    "จันทร์": 0, "จ": 0, "mon": 0, "monday": 0,
    "อังคาร": 1, "อ": 1, "tue": 1, "tuesday": 1,
    "พุธ": 2, "พ": 2, "wed": 2, "wednesday": 2,
    "พฤหัสบดี": 3, "พฤหัส": 3, "พฤ": 3, "thu": 3, "thursday": 3,
    "ศุกร์": 4, "ศ": 4, "fri": 4, "friday": 4,
    "เสาร์": 5, "ส": 5, "sat": 5, "อาทิตย์": 6, "อา": 6, "sun": 6,
}
TRUE_WORDS = {"ใช่", "y", "yes", "true", "1", "x", "✓", "/", "เป็น", "มี", "หัวหน้า"}
FALSE_WORDS = {"ไม่ใช่", "n", "no", "false", "0", "ไม่", "-", "ไม่มี", ""}


def norm(s: str) -> str:
    s = str(s or "").strip().lower()
    s = s.replace("ชม.", "ชั่วโมง")
    return _PUNCT.sub("", s)


def _kind_ok(kind: str, v: str) -> bool:
    v = str(v).strip()
    if not v:
        return False
    if kind == "course_code":
        return bool(re.match(r"^\d{4,5}-\d{4}$", v) or re.match(r"^[A-Za-z0-9][A-Za-z0-9\-]{3,15}$", v))
    if kind == "code":
        return len(v) <= 30 and " " not in v.strip()
    if kind == "int":
        return bool(re.match(r"^\d+$", v))
    if kind == "num":
        return bool(re.match(r"^\d+(\.\d+)?$", v))
    if kind == "tpn":
        return bool(re.match(r"^\d+(\.\d+)?\s*-\s*\d+(\.\d+)?\s*-\s*\d+(\.\d+)?$", v))
    if kind == "person":
        return bool(re.search(r"[A-Za-z฀-๿]", v)) and not re.match(r"^\d", v)
    if kind == "text":
        return not re.match(r"^\d+(\.\d+)?$", v)
    if kind == "bool":
        return norm(v) in {norm(x) for x in TRUE_WORDS | FALSE_WORDS}
    if kind == "day":
        return parse_day(v) is not None
    if kind == "periods":
        return bool(re.match(r"^\d{1,2}(\s*[-,]\s*\d{1,2})*$", v) or re.match(r"^\d{1,2}[:.]\d{2}\s*-\s*\d{1,2}[:.]\d{2}$", v))
    return True


def content_score(kind: str, values: list[str]) -> float:
    vals = [v for v in values if str(v).strip()]
    if not vals:
        return 0.0
    return sum(1 for v in vals if _kind_ok(kind, v)) / len(vals)


def name_score(header: str, synonyms: list[str]) -> tuple[float, str]:
    h = norm(header)
    if not h:
        return 0.0, ""
    best, why = 0.0, ""
    for syn in synonyms:
        n = norm(syn)
        if not n:
            continue
        if h == n:
            return 1.0, f"ชื่อคอลัมน์ตรงกับ “{syn}”"
        if len(n) >= 3 and len(h) >= 3 and (n in h or h in n):
            sc = 0.82 if n in h else 0.72
            if sc > best:
                best, why = sc, f"ชื่อคอลัมน์มีคำว่า “{syn}”"
        elif len(n) >= 3:
            r = SequenceMatcher(None, h, n).ratio() * 0.75
            if r > best:
                best, why = r, f"ชื่อคอลัมน์คล้าย “{syn}”"
    return best, why


@dataclass
class ColumnGuess:
    source: str
    field: str | None
    confidence: float
    reason: str
    needs_confirm: bool


def suggest_mapping(df: pd.DataFrame, entity: str, sample: int = 30) -> list[ColumnGuess]:
    fields = ENTITIES[entity]["fields"]
    cand: list[tuple[float, str, str, str]] = []  # (score, column, field, reason)
    for col in df.columns:
        values = df[col].head(sample).tolist()
        for fname, spec in fields.items():
            ns, why = name_score(col, spec["syn"] + [spec["label"]])
            if ns <= 0.3:
                continue
            cs = content_score(spec["kind"], values)
            score = ns
            if cs >= 0.8:
                score += 0.12
                why += " และข้อมูลในคอลัมน์มีรูปแบบสอดคล้อง"
            elif cs < 0.3 and any(str(v).strip() for v in values):
                score -= 0.35
                why += " แต่ข้อมูลในคอลัมน์ไม่ตรงรูปแบบที่คาด"
            cand.append((min(score, 1.0), col, fname, why))
    cand.sort(key=lambda x: -x[0])
    used_cols: set[str] = set()
    used_fields: set[str] = set()
    result: dict[str, ColumnGuess] = {}
    for score, col, fname, why in cand:
        if col in used_cols or fname in used_fields or score < SUGGEST_THRESHOLD:
            continue
        used_cols.add(col)
        used_fields.add(fname)
        result[col] = ColumnGuess(col, fname, round(score, 2), why, needs_confirm=score < AUTO_THRESHOLD)
    out = []
    for col in df.columns:
        out.append(result.get(col) or ColumnGuess(col, None, 0.0, "ไม่สามารถระบุความหมายได้อย่างมั่นใจ", needs_confirm=False))
    return out


def detect_entity(df: pd.DataFrame, filename: str = "", sheet: str | None = None) -> list[tuple[str, float]]:
    """จัดอันดับชนิดข้อมูลที่เป็นไปได้ของแผ่นงาน"""
    ranking = []
    title = norm(f"{filename} {sheet or ''}")
    for ent, spec in ENTITIES.items():
        guesses = suggest_mapping(df, ent)
        mapped = {g.field for g in guesses if g.field}
        missing = alt_required_satisfied(ent, mapped)
        req_total = max(1, len([f for f in spec["fields"].values() if f.get("required")]))
        req_hit = req_total - len([m for m in missing if spec["fields"].get(m, {}).get("required")])
        conf = sum(g.confidence for g in guesses if g.field)
        score = req_hit / req_total * 2 + conf / max(3, len(spec["fields"])) - len(missing) * 0.6
        score += sum(0.8 for h in spec["hints"] if norm(h) and norm(h) in title)
        # ไฟล์ที่มีคอลัมน์หลายประเภท: ชนิดที่ใช้คอลัมน์ได้มากกว่าได้คะแนนมากกว่า
        score += len(mapped) / max(1, len(df.columns)) * 0.5
        ranking.append((ent, round(score, 3)))
    ranking.sort(key=lambda x: -x[1])
    return ranking


# ------------------------------------------------------------------ ตัวแปลงค่า
def parse_int(v) -> int | None:
    v = str(v or "").strip().replace(",", "")
    if not v:
        return None
    try:
        f = float(v)
    except ValueError:
        raise ValueError(f"“{v}” ไม่ใช่ตัวเลขจำนวนเต็ม")
    if f != int(f) or f < 0:
        raise ValueError(f"“{v}” ต้องเป็นจำนวนเต็มไม่ติดลบ")
    return int(f)


def parse_float(v) -> float | None:
    v = str(v or "").strip().replace(",", "")
    if not v:
        return None
    try:
        f = float(v)
    except ValueError:
        raise ValueError(f"“{v}” ไม่ใช่ตัวเลข")
    if f < 0:
        raise ValueError(f"“{v}” ต้องไม่ติดลบ")
    return f


def parse_bool(v) -> bool | None:
    n = norm(v)
    if n == "":
        return None
    if n in {norm(x) for x in TRUE_WORDS}:
        return True
    if n in {norm(x) for x in FALSE_WORDS}:
        return False
    raise ValueError(f"“{v}” ไม่ใช่ค่า ใช่/ไม่ใช่")


def parse_day(v) -> int | None:
    s = str(v or "").strip().lower().replace("วัน", "").replace(".", "").strip()
    if not s:
        return None
    if s.isdigit():
        d = int(s)
        return d - 1 if 1 <= d <= 7 else None
    return DAY_WORDS.get(s)


def parse_tpn(v) -> tuple[float, float, float] | None:
    s = str(v or "").strip()
    if not s:
        return None
    m = re.match(r"^(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)$", s)
    if not m:
        raise ValueError(f"“{s}” ไม่ใช่รูปแบบ ท-ป-น (เช่น 2-2-3)")
    return float(m.group(1)), float(m.group(2)), float(m.group(3))


def parse_periods(v, time_struct) -> list[int]:
    """ “6-8” -> [6,7,8], “1,2” -> [1,2], “13:00-15:00” -> คาบที่อยู่ในช่วงเวลา"""
    s = str(v or "").strip()
    if not s:
        return []
    m = re.match(r"^(\d{1,2})[:.](\d{2})\s*-\s*(\d{1,2})[:.](\d{2})$", s)
    if m:
        a = f"{int(m.group(1)):02d}:{m.group(2)}"
        b = f"{int(m.group(3)):02d}:{m.group(4)}"
        res = [p["no"] for p in time_struct.periods if p["start"] >= a and p["end"] <= b and p["kind"] != "pre"]
        if not res:
            raise ValueError(f"ช่วงเวลา “{s}” ไม่ตรงกับโครงสร้างเวลาเรียน")
        return res
    out: list[int] = []
    for part in re.split(r"\s*,\s*", s):
        if re.match(r"^\d{1,2}\s*-\s*\d{1,2}$", part):
            a, b = [int(x) for x in re.split(r"\s*-\s*", part)]
            if a > b:
                raise ValueError(f"ช่วงคาบ “{part}” ไม่ถูกต้อง")
            out.extend(range(a, b + 1))
        elif part.isdigit():
            out.append(int(part))
        else:
            raise ValueError(f"“{part}” ไม่ใช่หมายเลขคาบ")
    valid = {p["no"] for p in time_struct.periods}
    bad = [p for p in out if p not in valid]
    if bad:
        raise ValueError(f"คาบ {bad} ไม่มีในโครงสร้างเวลาเรียน")
    return out


COURSE_TYPE_WORDS = {
    "ทฤษฎี": "theory", "theory": "theory", "lecture": "theory", "ท": "theory",
    "ปฏิบัติ": "practice", "practice": "practice", "lab": "practice", "practical": "practice", "ป": "practice",
    "ทฤษฎีและปฏิบัติ": "mixed", "ทฤษฎีปฏิบัติ": "mixed", "ผสม": "mixed", "mixed": "mixed", "ทป": "mixed",
    "กิจกรรม": "activity", "activity": "activity", "กิจกรรมพัฒนาผู้เรียน": "activity", "กิจกรรมเสริมหลักสูตร": "activity",
}
ROOM_TYPE_WORDS = {
    "ทฤษฎี": "theory", "ห้องทฤษฎี": "theory", "theory": "theory", "lecture": "theory", "ห้องเรียน": "theory", "classroom": "theory",
    "ปฏิบัติ": "lab", "ปฏิบัติการ": "lab", "ห้องปฏิบัติการ": "lab", "lab": "lab", "laboratory": "lab", "แล็บ": "lab", "workshop": "lab", "โรงฝึกงาน": "lab",
    "คอมพิวเตอร์": "computer", "ห้องคอมพิวเตอร์": "computer", "computer": "computer", "คอม": "computer", "computerlab": "computer",
    "เฉพาะทาง": "special", "ห้องเฉพาะทาง": "special", "special": "special", "specialized": "special",
    "กิจกรรม": "activity", "ห้องกิจกรรม": "activity", "activity": "activity", "หอประชุม": "activity", "ลานกิจกรรม": "activity",
    "ประชุม": "meeting", "ห้องประชุม": "meeting", "meeting": "meeting",
}
PART_WORDS = {"ทฤษฎี": "theory", "theory": "theory", "ปฏิบัติ": "practice", "practice": "practice", "ทั้งหมด": "all", "all": "all", "": "all"}


def parse_course_type(v) -> str | None:
    n = norm(v)
    if not n:
        return None
    for k, val in COURSE_TYPE_WORDS.items():
        if norm(k) == n:
            return val
    return "unknown"


def parse_room_type(v, extra: dict[str, str] | None = None) -> str | None:
    n = norm(v)
    if not n:
        return None
    if extra:
        for name, code in extra.items():
            if norm(name) == n or norm(code) == n:
                return code
    for k, val in ROOM_TYPE_WORDS.items():
        if norm(k) == n:
            return val
    return None
