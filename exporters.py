"""ส่งออกตารางเป็น PDF (A4 แนวนอน ฟอนต์ Sarabun พร้อม text shaping ภาษาไทย), Excel และ CSV"""
from __future__ import annotations

import csv
import io
import re
import unicodedata

from fpdf import FPDF
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from .. import config
from ..render import cell_lines, layout
from ..settings_service import DAY_NAMES
from ..views import ViewData

FILL = {"class": (240, 247, 255), "homeroom": (236, 253, 245), "activity": (254, 252, 232), "meeting": (245, 243, 255), "conflict": (254, 226, 226)}
NAVY = (11, 61, 145)

# ------------------------------------------------------------------ การตัดบรรทัดภาษาไทย
_LEAD = set("เแโใไ")


def _clusters(text: str) -> list[str]:
    out: list[str] = []
    for ch in text:
        if out and (unicodedata.combining(ch) or ch in "ัำิีึืฺุู็่้๊๋์ํ๎" or ch == "ๅ"):
            out[-1] += ch
        elif out and out[-1] in _LEAD:
            out[-1] += ch
        else:
            out.append(ch)
    return out


def wrap_text(pdf: FPDF, text: str, width: float) -> list[str]:
    lines: list[str] = []
    for para in str(text).split("\n"):
        cur = ""
        for word in re.split(r"(\s+)", para):
            if not word:
                continue
            trial = cur + word
            if pdf.get_string_width(trial) <= width:
                cur = trial
                continue
            if cur.strip():
                lines.append(cur.rstrip())
                cur = ""
            word = word.lstrip()
            if pdf.get_string_width(word) <= width:
                cur = word
                continue
            for cl in _clusters(word):
                if pdf.get_string_width(cur + cl) <= width:
                    cur += cl
                else:
                    if cur:
                        lines.append(cur)
                    cur = cl
        if cur.strip():
            lines.append(cur.rstrip())
    return lines or [""]


class _PDF(FPDF):
    def footer(self):
        self.set_y(-8)
        self.set_font("Sarabun", "", 8)
        self.set_text_color(100, 116, 139)
        self.cell(0, 4, f"{config.APP_NAME_TH} — หน้า {self.page_no()}", align="R")


def _new_pdf() -> _PDF:
    pdf = _PDF(orientation="L", unit="mm", format="A4")
    pdf.set_auto_page_break(False)
    pdf.add_font("Sarabun", "", str(config.FONT_REGULAR))
    pdf.add_font("Sarabun", "B", str(config.FONT_BOLD))
    try:
        pdf.set_text_shaping(True)  # จัดตำแหน่งสระ/วรรณยุกต์ภาษาไทยถูกต้อง (ต้องมี uharfbuzz)
    except Exception:
        pass
    pdf.set_margins(8, 8, 8)
    return pdf


def _pdf_page(pdf: _PDF, vd: ViewData, fields: list[str]) -> None:
    pdf.add_page()
    W = pdf.w - pdf.l_margin - pdf.r_margin
    pdf.set_text_color(*NAVY)
    pdf.set_font("Sarabun", "B", 15)
    pdf.cell(W, 7, config.COLLEGE_NAME, align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Sarabun", "B", 12)
    pdf.set_text_color(15, 23, 42)
    pdf.cell(W, 6, f"{vd.title}   {vd.term_label}", align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Sarabun", "", 10)
    meta = "    ".join(f"{k}: {v}" for k, v in vd.header)
    for ln in wrap_text(pdf, meta, W):
        pdf.cell(W, 5, ln, align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)

    cols, rows = layout(vd.ts, vd.entries)
    day_w, narrow = 17.0, 9.0
    n_narrow = sum(1 for p in cols if p["kind"] in ("pre", "break"))
    n_wide = len(cols) - n_narrow
    wide = (W - day_w - narrow * n_narrow) / max(1, n_wide)
    widths = [narrow if p["kind"] in ("pre", "break") else wide for p in cols]
    x0 = pdf.l_margin
    # หัวตาราง
    hh = 10.0
    y = pdf.get_y()
    pdf.set_fill_color(*NAVY)
    pdf.set_draw_color(100, 116, 139)
    pdf.set_text_color(255, 255, 255)
    pdf.set_font("Sarabun", "B", 9)
    pdf.rect(x0, y, day_w, hh, style="DF")
    pdf.set_xy(x0, y + 2.5)
    pdf.cell(day_w, 5, "วัน/เวลา", align="C")
    x = x0 + day_w
    for p, w in zip(cols, widths):
        pdf.rect(x, y, w, hh, style="DF")
        label = "ก่อน" if p["kind"] == "pre" else ("พัก" if p["kind"] == "break" else f"คาบ {p['no']}")
        pdf.set_font("Sarabun", "B", 8.5 if w > 10 else 7)
        pdf.set_xy(x, y + 0.8)
        pdf.cell(w, 4.2, label, align="C")
        pdf.set_font("Sarabun", "", 7 if w > 10 else 5.5)
        pdf.set_xy(x, y + 5)
        pdf.cell(w, 4, f"{p['start'].replace(':', '.')}-{p['end'].replace(':', '.')}", align="C")
        x += w
    y += hh
    pdf.set_text_color(15, 23, 42)
    line_h = 3.8
    usable_bottom = pdf.h - 12
    total_lanes = sum(len(l) for _, l in rows)
    avail_h = usable_bottom - y
    base_row_h = max(11.0, min(22.0, avail_h / max(1, total_lanes)))
    for d, lanes in rows:
        lane_heights = []
        lane_texts = []
        for segs in lanes:
            hmax = base_row_h
            texts = {}
            for sg in segs:
                if sg.kind == "entry":
                    w = sum(widths[sg.col:sg.col + sg.span]) - 1.6
                    pdf.set_font("Sarabun", "", 8)
                    lines = []
                    for i, ln in enumerate(cell_lines(sg.entry, fields)):
                        lines.extend(wrap_text(pdf, ln, w))
                    texts[sg.col] = lines
                    hmax = max(hmax, len(lines) * line_h + 2)
            lane_heights.append(hmax)
            lane_texts.append(texts)
        day_h = sum(lane_heights)
        if y + day_h > usable_bottom:
            pdf.add_page()
            y = pdf.get_y()
        pdf.set_fill_color(224, 237, 255)
        pdf.rect(x0, y, day_w, day_h, style="DF")
        pdf.set_font("Sarabun", "B", 10)
        pdf.set_text_color(*NAVY)
        pdf.set_xy(x0, y + day_h / 2 - 2.5)
        pdf.cell(day_w, 5, DAY_NAMES[d], align="C")
        pdf.set_text_color(15, 23, 42)
        ly = y
        for segs, lh, texts in zip(lanes, lane_heights, lane_texts):
            for sg in segs:
                x = x0 + day_w + sum(widths[: sg.col])
                w = sum(widths[sg.col:sg.col + sg.span])
                if sg.kind == "break":
                    pdf.set_fill_color(229, 231, 235)
                    pdf.rect(x, ly, w, lh, style="DF")
                    pdf.set_font("Sarabun", "B", 7.5)
                    pdf.set_text_color(71, 85, 105)
                    pdf.set_xy(x, ly + lh / 2 - 2)
                    pdf.cell(w, 4, "พัก", align="C")
                    pdf.set_text_color(15, 23, 42)
                elif sg.kind == "pre":
                    pdf.set_fill_color(241, 245, 249)
                    pdf.rect(x, ly, w, lh, style="DF")
                elif sg.kind == "empty":
                    pdf.rect(x, ly, w, lh, style="D")
                else:
                    e = sg.entry
                    key = "conflict" if e["id"] in vd.conflicts else e["entry_type"]
                    pdf.set_fill_color(*FILL.get(key, (255, 255, 255)))
                    pdf.rect(x, ly, w, lh, style="DF")
                    lines = texts.get(sg.col, [])
                    ty = ly + (lh - len(lines) * line_h) / 2
                    for i, ln in enumerate(lines):
                        pdf.set_font("Sarabun", "B" if i == 0 else "", 8)
                        pdf.set_xy(x + 0.8, ty + i * line_h)
                        pdf.cell(w - 1.6, line_h, ln, align="C")
            ly += lh
        y += day_h
    if vd.kind in ("group", "student", "teacher", "room") and y + 22 < usable_bottom:
        pdf.set_font("Sarabun", "", 10)
        pdf.set_xy(x0, y + 10)
        half = W / 2
        pdf.cell(half, 5, "ลงชื่อ ........................................", align="C")
        pdf.cell(half, 5, "ลงชื่อ ........................................", align="C", new_x="LMARGIN", new_y="NEXT")
        pdf.set_x(x0)
        pdf.cell(half, 5, "ครูที่ปรึกษา/ผู้สอน", align="C")
        pdf.cell(half, 5, "รองผู้อำนวยการฝ่ายวิชาการ", align="C")


def to_pdf(vd: ViewData) -> bytes:
    pdf = _new_pdf()
    if vd.sub_views:
        for sv in vd.sub_views:
            _pdf_page(pdf, sv, ["code", "name", "room", "teacher"])
    else:
        _pdf_page(pdf, vd, vd.fields)
    return bytes(pdf.output())


# ------------------------------------------------------------------ Excel
_THIN = Side(style="thin", color="94A3B8")
_BORDER = Border(left=_THIN, right=_THIN, top=_THIN, bottom=_THIN)
_CENTER = Alignment(horizontal="center", vertical="center", wrap_text=True)
_HEX = {k: "%02X%02X%02X" % v for k, v in FILL.items()}


def _sheet_name(name: str, used: set[str]) -> str:
    n = re.sub(r"[\[\]:*?/\\]", "-", name)[:31] or "Sheet"
    base, i = n, 2
    while n in used:
        suffix = f" ({i})"
        n = base[: 31 - len(suffix)] + suffix
        i += 1
    used.add(n)
    return n


def _excel_sheet(wb: Workbook, vd: ViewData, fields: list[str], used: set[str]) -> None:
    ws = wb.create_sheet(_sheet_name(vd.title.replace("ตารางเรียน ", "").replace("ตารางสอน ", ""), used))
    cols, rows = layout(vd.ts, vd.entries)
    ncol = len(cols) + 1
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=ncol)
    ws.cell(1, 1, config.COLLEGE_NAME).font = Font(name="TH Sarabun New", bold=True, size=18, color="0B3D91")
    ws.cell(1, 1).alignment = _CENTER
    ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=ncol)
    ws.cell(2, 1, f"{vd.title}  {vd.term_label}  ({vd.timetable_label})").font = Font(name="TH Sarabun New", bold=True, size=15)
    ws.cell(2, 1).alignment = _CENTER
    ws.merge_cells(start_row=3, start_column=1, end_row=3, end_column=ncol)
    ws.cell(3, 1, "    ".join(f"{k}: {v}" for k, v in vd.header)).font = Font(name="TH Sarabun New", size=13)
    ws.cell(3, 1).alignment = _CENTER
    r0 = 5
    hfill = PatternFill("solid", fgColor="0B3D91")
    c = ws.cell(r0, 1, "วัน / เวลา")
    c.font, c.fill, c.alignment, c.border = Font(name="TH Sarabun New", bold=True, color="FFFFFF", size=13), hfill, _CENTER, _BORDER
    for i, p in enumerate(cols):
        label = "ก่อนเรียน" if p["kind"] == "pre" else ("พัก" if p["kind"] == "break" else f"คาบ {p['no']}")
        c = ws.cell(r0, i + 2, f"{label}\n{p['start'].replace(':', '.')}-{p['end'].replace(':', '.')} น.")
        c.font, c.fill, c.alignment, c.border = Font(name="TH Sarabun New", bold=True, color="FFFFFF", size=12), hfill, _CENTER, _BORDER
        ws.column_dimensions[get_column_letter(i + 2)].width = 7 if p["kind"] in ("pre", "break") else 17
    ws.column_dimensions["A"].width = 11
    ws.row_dimensions[r0].height = 34
    r = r0 + 1
    for d, lanes in rows:
        start = r
        for segs in lanes:
            maxlines = 2
            for sg in segs:
                col = sg.col + 2
                cell = ws.cell(r, col)
                cell.border = _BORDER
                cell.alignment = _CENTER
                if sg.kind == "break":
                    cell.value = "พัก"
                    cell.fill = PatternFill("solid", fgColor="E5E7EB")
                    cell.font = Font(name="TH Sarabun New", bold=True, color="475569", size=12)
                elif sg.kind == "pre":
                    cell.fill = PatternFill("solid", fgColor="F1F5F9")
                elif sg.kind == "entry":
                    e = sg.entry
                    lines = cell_lines(e, fields)
                    if e["id"] in vd.conflicts:
                        lines.append("[ชนกัน]")
                    maxlines = max(maxlines, len(lines) + sum(len(x) // 22 for x in lines))
                    cell.value = "\n".join(lines)
                    key = "conflict" if e["id"] in vd.conflicts else e["entry_type"]
                    cell.fill = PatternFill("solid", fgColor=_HEX.get(key, "FFFFFF"))
                    cell.font = Font(name="TH Sarabun New", size=12)
                    if sg.span > 1:
                        ws.merge_cells(start_row=r, start_column=col, end_row=r, end_column=col + sg.span - 1)
                        for k in range(1, sg.span):
                            ws.cell(r, col + k).border = _BORDER
            ws.row_dimensions[r].height = max(48, maxlines * 17)
            r += 1
        c = ws.cell(start, 1, DAY_NAMES[d])
        c.font = Font(name="TH Sarabun New", bold=True, size=14, color="0B3D91")
        c.fill = PatternFill("solid", fgColor="E0EDFF")
        c.alignment, c.border = _CENTER, _BORDER
        if r - 1 > start:
            ws.merge_cells(start_row=start, start_column=1, end_row=r - 1, end_column=1)
    ws.page_setup.orientation = "landscape"
    ws.page_setup.paperSize = ws.PAPERSIZE_A4
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.print_area = f"A1:{get_column_letter(ncol)}{r - 1}"


def to_excel(vd: ViewData | list[ViewData]) -> bytes:
    wb = Workbook()
    wb.remove(wb.active)
    used: set[str] = set()
    vds = vd if isinstance(vd, list) else [vd]
    all_rows = []
    for v in vds:
        if v.sub_views:
            for sv in v.sub_views:
                _excel_sheet(wb, sv, ["code", "name", "room", "teacher"], used)
        else:
            _excel_sheet(wb, v, v.fields, used)
        all_rows.extend(csv_rows(v))
    ws = wb.create_sheet(_sheet_name("รายการทั้งหมด", used))
    if all_rows:
        heads = list(all_rows[0].keys())
        ws.append(heads)
        for c in ws[1]:
            c.font = Font(name="TH Sarabun New", bold=True, color="FFFFFF", size=13)
            c.fill = PatternFill("solid", fgColor="0B3D91")
        for row in all_rows:
            ws.append([row[h] for h in heads])
        for i, h in enumerate(heads, 1):
            ws.column_dimensions[get_column_letter(i)].width = max(10, min(40, len(h) + 6))
        ws.freeze_panes = "A2"
    bio = io.BytesIO()
    wb.save(bio)
    return bio.getvalue()


# ------------------------------------------------------------------ CSV
TYPE_TH = {"class": "รายวิชา", "homeroom": "โฮมรูม", "meeting": "ประชุม", "activity": "กิจกรรม"}


def csv_rows(vd: ViewData) -> list[dict]:
    m = re.match(r"ภาคเรียนที่ (\d+)/(\d+)", vd.term_label or "")
    sem, year = (m.group(1), m.group(2)) if m else ("", "")
    out = []
    seen = set()
    for e in sorted(vd.entries, key=lambda x: (x["day"], x["start_period"])):
        if e["id"] in seen:
            continue
        seen.add(e["id"])
        out.append({
            "ปีการศึกษา": year, "ภาคเรียน": sem, "วันเรียน": DAY_NAMES[e["day"]],
            "คาบเริ่มต้น": e["start_period"], "คาบสิ้นสุด": e["end_period"],
            "เวลาเริ่มต้น": e["start_time"], "เวลาสิ้นสุด": e["end_time"],
            "ประเภทรายการ": TYPE_TH.get(e["entry_type"], e["entry_type"]),
            "รหัสวิชา": e.get("course_code") or "", "ชื่อวิชา": e.get("course_name") or TYPE_TH.get(e["entry_type"], ""),
            "รหัสครู": ",".join(str(x) for x in e.get("teacher_codes", [])) if e.get("teacher_codes") else "",
            "ชื่อครู": ", ".join(e.get("teacher_names") or []),
            "รหัสห้อง": e.get("room_code") or "", "หมายเลขห้อง": e.get("room_label") or "",
            "รหัสกลุ่มเรียน": ", ".join(e.get("group_codes") or []), "ชื่อกลุ่มเรียน": ", ".join(e.get("group_names") or []),
        })
    return out


def to_csv(vd: ViewData) -> bytes:
    rows = csv_rows(vd)
    bio = io.StringIO()
    heads = list(rows[0].keys()) if rows else ["ปีการศึกษา", "ภาคเรียน", "วันเรียน", "คาบเริ่มต้น", "คาบสิ้นสุด", "เวลาเริ่มต้น", "เวลาสิ้นสุด",
                                              "ประเภทรายการ", "รหัสวิชา", "ชื่อวิชา", "รหัสครู", "ชื่อครู", "รหัสห้อง", "หมายเลขห้อง", "รหัสกลุ่มเรียน", "ชื่อกลุ่มเรียน"]
    w = csv.DictWriter(bio, fieldnames=heads)
    w.writeheader()
    for r in rows:
        w.writerow(r)
    return ("﻿" + bio.getvalue()).encode("utf-8")  # UTF-8 BOM ให้ Excel เปิดภาษาไทยได้ถูกต้อง
