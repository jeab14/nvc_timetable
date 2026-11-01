"""อ่านไฟล์หลายรูปแบบให้เป็น DataFrame (ค่าทุกช่องเป็นข้อความเพื่อไม่ให้รหัสเสียรูป เช่น เลข 0 นำหน้า)"""
from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from .. import config

UNSUPPORTED_HINT = {
    ".pdf": "ไฟล์ PDF ไม่ใช่ตารางข้อมูลโดยตรง กรุณาแปลงเป็น Excel หรือ CSV ก่อน (เช่น คัดลอกตารางไปวางใน Excel แล้วบันทึกเป็น .xlsx)",
    ".doc": "ไฟล์ Word ไม่รองรับโดยตรง กรุณาคัดลอกตารางไปไว้ใน Excel แล้วบันทึกเป็น .xlsx",
    ".docx": "ไฟล์ Word ไม่รองรับโดยตรง กรุณาคัดลอกตารางไปไว้ใน Excel แล้วบันทึกเป็น .xlsx",
}


@dataclass
class SheetData:
    filename: str
    sheet: str | None
    df: pd.DataFrame
    header_row: int = 0
    notes: list[str] = field(default_factory=list)

    @property
    def key(self) -> str:
        return f"{self.filename}::{self.sheet or '-'}"


class ReadError(Exception):
    pass


def _clean(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [str(c).strip() if c is not None else "" for c in df.columns]
    # ตั้งชื่อคอลัมน์ว่าง/ซ้ำ
    seen: dict[str, int] = {}
    cols = []
    for i, c in enumerate(df.columns):
        if not c or c.lower().startswith("unnamed") or c == "nan":
            c = f"คอลัมน์_{i + 1}"
        if c in seen:
            seen[c] += 1
            c = f"{c}_{seen[c]}"
        else:
            seen[c] = 0
        cols.append(c)
    df.columns = cols
    df = df.map(lambda v: "" if v is None or (isinstance(v, float) and pd.isna(v)) else str(v).strip())
    # ตัดแถว/คอลัมน์ว่างทั้งหมด
    df = df.loc[~(df == "").all(axis=1)]
    df = df.loc[:, ~((df == "").all(axis=0) & df.columns.str.startswith("คอลัมน์_"))]
    # ค่าตัวเลขที่อ่านจาก Excel เป็น 3.0 -> 3
    df = df.map(lambda v: v[:-2] if isinstance(v, str) and v.endswith(".0") and v[:-2].lstrip("-").isdigit() else v)
    return df.reset_index(drop=True)


def _detect_header(raw: pd.DataFrame, max_scan: int = 15) -> int:
    """หาแถวหัวตาราง: แถวที่มีข้อความไม่ว่างมากที่สุดและส่วนใหญ่ไม่ใช่ตัวเลข"""
    best, best_score = 0, -1.0
    n = min(max_scan, len(raw))
    for i in range(n):
        vals = [str(v).strip() for v in raw.iloc[i].tolist() if v is not None and str(v).strip() not in ("", "nan", "None")]
        if not vals:
            continue
        texty = sum(1 for v in vals if not v.replace(".", "").replace("-", "").isdigit())
        score = len(vals) + texty * 0.5
        if score > best_score + 0.01:
            best, best_score = i, score
    return best


def _frame_from_raw(raw: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    h = _detect_header(raw)
    header = [str(v).strip() if v is not None and str(v) != "nan" else "" for v in raw.iloc[h].tolist()]
    body = raw.iloc[h + 1 :].copy()
    body.columns = header
    return _clean(body), h


def _decode(data: bytes) -> str:
    for enc in ("utf-8-sig", "utf-8", "cp874", "tis-620", "utf-16"):
        try:
            return data.decode(enc)
        except Exception:
            continue
    raise ReadError("ไม่สามารถอ่านการเข้ารหัสตัวอักษรของไฟล์ได้ (รองรับ UTF-8, TIS-620/Windows-874)")


def _read_delimited(text: str, sep: str | None) -> pd.DataFrame:
    if sep is None:
        try:
            sep = csv.Sniffer().sniff(text[:5000], delimiters=",\t;|").delimiter
        except Exception:
            sep = ","
    raw = pd.read_csv(io.StringIO(text), sep=sep, header=None, dtype=str, keep_default_na=False, engine="python")
    return raw


def read_file(filename: str, data: bytes) -> list[SheetData]:
    ext = Path(filename).suffix.lower()
    if len(data) > config.MAX_UPLOAD_MB * 1024 * 1024:
        raise ReadError(f"ไฟล์มีขนาดเกิน {config.MAX_UPLOAD_MB} MB")
    if ext in UNSUPPORTED_HINT:
        raise ReadError(UNSUPPORTED_HINT[ext])
    if ext not in config.ALLOWED_UPLOAD_EXT:
        raise ReadError(f"ไม่รองรับไฟล์นามสกุล {ext} (รองรับ: {', '.join(sorted(config.ALLOWED_UPLOAD_EXT))})")
    out: list[SheetData] = []
    try:
        if ext in (".xlsx", ".xls"):
            engine = "openpyxl" if ext == ".xlsx" else "xlrd"
            sheets = pd.read_excel(io.BytesIO(data), sheet_name=None, header=None, dtype=str, engine=engine)
            for name, raw in sheets.items():
                if raw.dropna(how="all").empty:
                    continue
                df, h = _frame_from_raw(raw)
                if df.empty:
                    continue
                out.append(SheetData(filename, str(name), df, h))
        elif ext in (".csv", ".tsv", ".txt"):
            text = _decode(data)
            sep = "\t" if ext == ".tsv" else ("," if ext == ".csv" else None)
            raw = _read_delimited(text, sep)
            df, h = _frame_from_raw(raw)
            out.append(SheetData(filename, None, df, h))
        elif ext == ".json":
            obj = json.loads(_decode(data))
            if isinstance(obj, list):
                out.append(SheetData(filename, None, _clean(pd.DataFrame(obj))))
            elif isinstance(obj, dict):
                if all(isinstance(v, list) for v in obj.values()) and all(
                    (len(v) == 0 or isinstance(v[0], dict)) for v in obj.values()
                ):
                    for k, v in obj.items():
                        if v:
                            out.append(SheetData(filename, str(k), _clean(pd.DataFrame(v))))
                else:
                    out.append(SheetData(filename, None, _clean(pd.DataFrame(obj))))
            else:
                raise ReadError("โครงสร้าง JSON ต้องเป็นรายการของ object หรือ object ของรายการ")
    except ReadError:
        raise
    except Exception as e:
        raise ReadError(f"อ่านไฟล์ไม่สำเร็จ: {e.__class__.__name__}: {str(e)[:200]}")
    if not out:
        raise ReadError("ไม่พบข้อมูลตารางในไฟล์")
    return out
