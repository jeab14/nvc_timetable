"""จัดรูปแบบตาราง (วันเป็นแถว คาบเป็นคอลัมน์ รวมช่องคาบต่อเนื่องในแนวนอน) — ใช้ร่วมกันทั้งหน้าเว็บ พิมพ์ PDF และ Excel"""
from __future__ import annotations

import html

from .ui.icons import svg as _svg
import re
from dataclasses import dataclass

from . import config
from .settings_service import DAY_NAMES, TimeStructure
from .views import ViewData

TYPE_LABEL = {"class": "", "homeroom": "โฮมรูม", "meeting": "ประชุม", "activity": "กิจกรรม"}
_PREFIX = re.compile(r"^(นางสาว|นาง|นาย|ว่าที่ร้อยตรี|ว่าที่ ร\.ต\.|ดร\.|ผศ\.|รศ\.|Mr\.|Mrs\.|Ms\.)\s*")


@dataclass
class Seg:
    kind: str  # entry / empty / break / pre
    col: int
    span: int
    entry: dict | None = None


def short_teacher(name: str) -> str:
    n = _PREFIX.sub("", (name or "").strip())
    parts = n.split()
    if not parts:
        return ""
    if len(parts) == 1:
        return f"ครู{parts[0]}"
    return f"ครู{parts[0]} {parts[1][0]}."


def cell_lines(e: dict, fields: list[str], full_names: bool = False) -> list[str]:
    lines = []
    t = e["entry_type"]
    for f in fields:
        if f == "code":
            if t == "class" or t == "activity":
                if e.get("course_code"):
                    lines.append(e["course_code"])
            else:
                lines.append(TYPE_LABEL[t])
        elif f == "name":
            if t in ("class", "activity"):
                if e.get("course_name"):
                    lines.append(e["course_name"])
            elif t == "meeting":
                lines.append(e.get("course_name") or "ประชุม")
            elif t == "homeroom" and "code" not in fields:
                lines.append("โฮมรูม")
        elif f == "room":
            if e.get("room_label"):
                lines.append(f"ห้อง {e['room_label']}")
        elif f == "teacher":
            names = e.get("teacher_names") or []
            if names:
                if t == "meeting" and len(names) > 3:
                    lines.append(f"ผู้เข้าร่วม {len(names)} คน")
                else:
                    lines.append(", ".join(names if full_names else [short_teacher(n) for n in names]))
        elif f == "group":
            codes = e.get("group_codes") or []
            if codes:
                lines.append("กลุ่ม " + ", ".join(codes))
    return [ln for ln in lines if ln]


def layout(ts: TimeStructure, entries: list[dict]) -> tuple[list[dict], list[tuple[int, list[list[Seg]]]]]:
    cols = ts.display_periods
    idx = {p["no"]: i for i, p in enumerate(cols)}
    rows = []
    for d in ts.days:
        des = sorted([e for e in entries if e["day"] == d], key=lambda e: (e["start_period"], -(e["end_period"] - e["start_period"])))
        lanes: list[list[dict]] = []
        for e in des:
            for lane in lanes:
                if all(e["start_period"] > x["end_period"] or e["end_period"] < x["start_period"] for x in lane):
                    lane.append(e)
                    break
            else:
                lanes.append([e])
        if not lanes:
            lanes = [[]]
        lane_segs = []
        for lane in lanes:
            segs: list[Seg] = []
            occupied: dict[int, dict] = {}
            for e in lane:
                if e["start_period"] in idx:
                    occupied[idx[e["start_period"]]] = e
            i = 0
            while i < len(cols):
                p = cols[i]
                if i in occupied:
                    e = occupied[i]
                    span = 1
                    while i + span < len(cols) and cols[i + span]["no"] <= e["end_period"]:
                        span += 1
                    segs.append(Seg("entry", i, span, e))
                    i += span
                    continue
                if p["kind"] == "break":
                    segs.append(Seg("break", i, 1))
                elif p["kind"] == "pre":
                    segs.append(Seg("pre", i, 1))
                else:
                    segs.append(Seg("empty", i, 1))
                i += 1
            lane_segs.append(segs)
        rows.append((d, lane_segs))
    return cols, rows


CSS = """
<style>
.nvc-wrap{font-family:'Sarabun','Noto Sans Thai','Tahoma',sans-serif;color:#0f172a}
.nvc-doc-head{text-align:center;margin-bottom:8px}
.nvc-doc-head h2{margin:0;color:#0b3d91;font-size:20px}
.nvc-doc-head .sub{font-size:14px;color:#334155}
.nvc-meta{display:flex;flex-wrap:wrap;gap:4px 18px;justify-content:center;font-size:13.5px;margin:6px 0 10px}
.nvc-meta b{color:#0b3d91}
.nvc-scroll{overflow-x:auto;-webkit-overflow-scrolling:touch;border:1px solid #d6e2f5;border-radius:12px;background:#fff}
table.nvc{border-collapse:separate;border-spacing:0;width:100%;min-width:900px;table-layout:fixed;background:#fff}
table.nvc th,table.nvc td{border-right:1px solid #dbe5f5;border-bottom:1px solid #dbe5f5;padding:5px 5px;text-align:center;vertical-align:middle;
  font-size:12.5px;line-height:1.3;word-break:break-word}
table.nvc thead th{background:#0b3d91;color:#fff;font-weight:700;position:sticky;top:0;z-index:2;font-size:13px;padding:7px 4px}
table.nvc thead th:first-child{border-top-left-radius:11px}
table.nvc thead th .t{display:block;font-weight:400;font-size:11px;color:#cfe0ff}
table.nvc td.day,table.nvc thead th:first-child{position:sticky;left:0;z-index:3}
table.nvc td.day{background:#eef4ff;color:#0b3d91;font-weight:700;font-size:14px;width:64px;box-shadow:inset -1px 0 0 #c7d7f2}
table.nvc tbody tr:nth-child(even) td:not(.day):not(.brk):not(.ent){background:#fbfdff}
table.nvc td.brk{background:repeating-linear-gradient(135deg,#eef1f6,#eef1f6 6px,#e5e9f0 6px,#e5e9f0 12px);color:#64748b;font-weight:600;font-size:11.5px}
table.nvc td.pre{background:#f6f8fb}
table.nvc th.prehead{width:40px;background:#3b5c9c}
table.nvc th.brkhead{width:40px;background:#64748b}
table.nvc td.ent{padding:3px}
.cell{border-radius:9px;padding:5px 6px;text-align:left;height:100%;min-height:74px;box-sizing:border-box;border-left:4px solid #2563eb;
  background:#eff5ff;display:flex;flex-direction:column;gap:2px}
.cell .code{display:inline-block;white-space:nowrap;align-self:flex-start;font-size:11px;font-weight:700;color:#0b3d91;background:#fff;border:1px solid #c7d7f2;border-radius:6px;padding:0 6px}
.cell .nm{font-weight:700;color:#0f172a;font-size:13px;line-height:1.25}
.cell .ln{font-size:12px;color:#334155}
.cell .tm{font-size:11px;color:#64748b;margin-top:auto}
.cell.c0{background:#eff5ff;border-left-color:#2563eb}.cell.c1{background:#ecfeff;border-left-color:#0891b2}
.cell.c2{background:#eef2ff;border-left-color:#4f46e5}.cell.c3{background:#f0fdfa;border-left-color:#0d9488}
.cell.c4{background:#f0f9ff;border-left-color:#0284c7}.cell.c5{background:#f5f7fb;border-left-color:#475569}
.cell.homeroom{background:#ecfdf5;border-left-color:#16a34a}.cell.activity{background:#fffbeb;border-left-color:#d97706}
.cell.meeting{background:#f5f3ff;border-left-color:#7c3aed}
.cell.conflict{background:#fee2e2;border-left-color:#dc2626;box-shadow:inset 0 0 0 2px #dc2626}
.cell .flag{color:#b91c1c;font-weight:700;font-size:12px}
.nvc-legend{display:flex;flex-wrap:wrap;gap:6px 14px;font-size:12.5px;color:#475569;margin:2px 2px 8px}
.nvc-legend span{display:inline-flex;align-items:center;gap:5px}
.nvc-legend i{width:12px;height:12px;border-radius:3px;display:inline-block}
.nvc-sign{display:flex;justify-content:space-around;margin-top:28px;font-size:13px}
.nvc-sign div{text-align:center}
@media print{
  @page{size:A4 landscape;margin:8mm}
  .noprint{display:none!important}
  table.nvc{min-width:0}
  table.nvc th,table.nvc td{font-size:10px}
  .nvc-scroll{overflow:visible;border:0}
  .cell{min-height:0;padding:3px 4px}
  .cell .nm{font-size:10.5px}
  .nvc-legend{display:none}
  .nvc-page{page-break-after:always}
  .nvc-page:last-child{page-break-after:auto}
  body{-webkit-print-color-adjust:exact;print-color-adjust:exact}
}
</style>
"""


def _tone(e: dict) -> str:
    if e["entry_type"] != "class":
        return e["entry_type"]
    code = e.get("course_code") or ""
    return f"c{sum(ord(ch) for ch in code) % 6}"


def _cell_html(e: dict, fields: list[str], conflict: bool) -> str:
    """ช่องตาราง: รหัสวิชา ชื่อวิชา ห้อง ครู กลุ่ม และช่วงเวลา (อ่านง่ายบนหน้าเว็บ)"""
    esc = html.escape
    t = e["entry_type"]
    parts = []
    if t in ("class", "activity") and "code" in fields and e.get("course_code"):
        parts.append(f'<span class="code">{esc(e["course_code"])}</span>')
    name = e.get("course_name") if t in ("class", "activity") else {"homeroom": "โฮมรูม", "meeting": e.get("course_name") or "ประชุม"}.get(t, "")
    if name and ("name" in fields or t != "class"):
        parts.append(f'<div class="nm">{esc(name)}</div>')
    if "room" in fields and e.get("room_label"):
        parts.append(f'<div class="ln">{_svg("location")} ห้อง {esc(e["room_label"])}</div>')
    if "teacher" in fields and e.get("teacher_names"):
        names = e["teacher_names"]
        txt = f"ผู้เข้าร่วม {len(names)} คน" if t == "meeting" and len(names) > 3 else ", ".join(short_teacher(n) for n in names)
        parts.append(f'<div class="ln">{_svg("person")} {esc(txt)}</div>')
    if "group" in fields and e.get("group_codes"):
        parts.append(f'<div class="ln">{_svg("group")} {esc(", ".join(e["group_codes"]))}</div>')
    span = f'คาบ {e["start_period"]}' + (f'–{e["end_period"]}' if e["end_period"] != e["start_period"] else "")
    parts.append(f'<div class="tm">{_svg("schedule", 12)} {esc(e["start_time"].replace(":", "."))}–{esc(e["end_time"].replace(":", "."))} · {span}</div>')
    if conflict:
        parts.append(f'<div class="flag">{_svg("warning")} ชนกัน</div>')
    return f'<div class="cell {_tone(e)}{" conflict" if conflict else ""}">{"".join(parts)}</div>'


LEGEND = ('<div class="nvc-legend noprint"><span><i style="background:#2563eb"></i>รายวิชา (สีต่างกันตามรหัสวิชา)</span>'
          '<span><i style="background:#16a34a"></i>โฮมรูม</span><span><i style="background:#d97706"></i>กิจกรรม</span>'
          '<span><i style="background:#7c3aed"></i>ประชุม</span><span><i style="background:#cbd5e1"></i>คาบพัก</span>'
          '<span><i style="background:#dc2626"></i>รายการที่ชนกัน</span></div>')


def grid_html(vd: ViewData, cell_fields: list[str] | None = None) -> str:
    fields = cell_fields or vd.fields
    cols, rows = layout(vd.ts, vd.entries)
    out = [LEGEND, '<div class="nvc-scroll"><table class="nvc"><thead><tr><th style="width:64px">วัน / คาบ</th>']
    for p in cols:
        cls = "prehead" if p["kind"] == "pre" else ("brkhead" if p["kind"] == "break" else "")
        label = "ก่อนเรียน" if p["kind"] == "pre" else ("พัก" if p["kind"] == "break" else f"คาบ {p['no']}")
        out.append(f'<th class="{cls}">{html.escape(label)}<span class="t">{p["start"].replace(":", ".")}–{p["end"].replace(":", ".")}</span></th>')
    out.append("</tr></thead><tbody>")
    for d, lanes in rows:
        for li, segs in enumerate(lanes):
            out.append("<tr>")
            if li == 0:
                out.append(f'<td class="day" rowspan="{len(lanes)}">{DAY_NAMES[d]}</td>')
            for sg in segs:
                if sg.kind == "break":
                    out.append('<td class="brk">พัก</td>')
                elif sg.kind == "pre":
                    out.append('<td class="pre"></td>')
                elif sg.kind == "empty":
                    out.append("<td></td>")
                else:
                    e = sg.entry
                    conflict = e["id"] in vd.conflicts
                    tip = html.escape(" | ".join(cell_lines(e, ["code", "name", "room", "teacher", "group"], True)))
                    out.append(f'<td class="ent" colspan="{sg.span}" title="{tip}">{_cell_html(e, fields, conflict)}</td>')
            out.append("</tr>")
    out.append("</tbody></table></div>")
    return "".join(out)


def estimated_height(vd: ViewData) -> int:
    """ความสูงโดยประมาณของตาราง (ใช้กำหนดความสูงกรอบแสดงผล ไม่ให้มีช่องว่างหรือถูกตัด)"""
    def one(v):
        _, rows = layout(v.ts, v.entries)
        lanes = sum(len(l) for _, l in rows)
        return 190 + lanes * 118 + 40

    if vd.sub_views:
        return sum(one(v) + 30 for v in vd.sub_views) + 60
    return one(vd) + (90 if vd.kind in ("group", "student", "teacher", "room") else 0)


def doc_header_html(vd: ViewData) -> str:
    meta = "".join(f"<span><b>{html.escape(k)}:</b> {html.escape(str(v))}</span>" for k, v in vd.header)
    return (f'<div class="nvc-doc-head"><h2>{html.escape(config.COLLEGE_NAME)}</h2>'
            f'<div class="sub">{html.escape(vd.title)} — {html.escape(vd.term_label)} ({html.escape(vd.timetable_label)})</div></div>'
            f'<div class="nvc-meta">{meta}</div>')


def view_html(vd: ViewData, with_header: bool = True) -> str:
    if vd.sub_views:
        parts = []
        for sv in vd.sub_views:
            parts.append(f'<div class="nvc-page">{doc_header_html(sv)}{grid_html(sv, vd.fields if vd.kind != "college" else ["code", "name", "room", "teacher"])}</div>')
        return CSS + '<div class="nvc-wrap">' + "".join(parts) + "</div>"
    head = doc_header_html(vd) if with_header else ""
    return CSS + f'<div class="nvc-wrap"><div class="nvc-page">{head}{grid_html(vd)}</div></div>'


def printable_html(vd: ViewData, signature: bool = True) -> str:
    """หน้า HTML สำหรับ iframe: ปุ่มพิมพ์อยู่มุมขวาบนของตาราง และพิมพ์เฉพาะตาราง"""
    sign = ""
    if signature and vd.kind in ("group", "student", "teacher", "room"):
        sign = ('<div class="nvc-sign"><div>ลงชื่อ ....................................<br>ครูที่ปรึกษา/ผู้สอน</div>'
                '<div>ลงชื่อ ....................................<br>รองผู้อำนวยการฝ่ายวิชาการ</div></div>')
    btn = ('<div class="noprint" style="display:flex;justify-content:flex-end;gap:8px;margin:2px 0 6px">'
           '<button onclick="window.print()" style="background:#0b3d91;color:#fff;border:0;border-radius:8px;padding:7px 16px;'
           'font-size:14px;cursor:pointer;font-family:inherit;display:inline-flex;align-items:center;gap:6px">' + _svg("print", 16) + 'พิมพ์ตารางนี้</button></div>')
    body = view_html(vd)
    if sign and not vd.sub_views:
        body = body + f'<div class="nvc-wrap">{sign}</div>'
    return f'<!DOCTYPE html><html lang="th"><head><meta charset="utf-8"><link rel="preconnect" href="https://fonts.googleapis.com"><link href="https://fonts.googleapis.com/css2?family=Sarabun:wght@400;700&display=swap" rel="stylesheet"></head><body style="margin:0;padding:4px 6px;background:#fff">{btn}{body}</body></html>'


def day_list_html(vd: ViewData) -> str:
    """รูปแบบรายการรายวัน (สำหรับหน้าจอมือถือ)"""
    out = [CSS, '<div class="nvc-wrap">']
    for d in vd.ts.days:
        es = sorted([e for e in vd.entries if e["day"] == d], key=lambda e: e["start_period"])
        out.append(f'<div style="margin:8px 0 4px;font-weight:700;color:#0b3d91">วัน{DAY_NAMES[d]}</div>')
        if not es:
            out.append('<div style="color:#64748b;font-size:13px">ไม่มีรายการ</div>')
        for e in es:
            t = f'{e["start_time"].replace(":", ".")}–{e["end_time"].replace(":", ".")} น. (คาบ {e["start_period"]}{"–" + str(e["end_period"]) if e["end_period"] != e["start_period"] else ""})'
            lines = cell_lines(e, vd.fields, True)
            warn = ' <span style="color:#b91c1c;font-weight:700">' + _svg("warning") + ' ชนกัน</span>' if e["id"] in vd.conflicts else ""
            out.append(f'<div style="border:1px solid #cbd5e1;border-left:4px solid #0b3d91;border-radius:8px;padding:6px 10px;margin:4px 0;background:#fff">'
                       f'<div style="font-size:12.5px;color:#475569">{t}{warn}</div><div style="font-weight:700">{html.escape(lines[0]) if lines else ""}</div>'
                       f'<div style="font-size:13px">{html.escape(" · ".join(lines[1:]))}</div></div>')
    out.append("</div>")
    return "".join(out)
