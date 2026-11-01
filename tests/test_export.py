"""การทดสอบการส่งออกและการแสดงผลตาราง (หัวข้อ 25)"""
from __future__ import annotations

import io
import subprocess

from openpyxl import load_workbook
from sqlalchemy import select

from nvc import db, render, views
from nvc.export import exporters as X
from nvc.models import ClassGroup, Student, StudentMembership, Teacher, Timetable

from .conftest import ADMIN


def _vd(sched, kind, key=None):
    with db.session_scope() as s:
        return views.get_view(s, ADMIN, s.get(Timetable, sched["tt"]), kind, key)


def _group(sched, code="DBT1-1"):
    with db.session_scope() as s:
        return s.scalars(select(ClassGroup).where(ClassGroup.code == code)).one().id


def test_pdf_thai(sched, tmp_path):
    vd = _vd(sched, "group", _group(sched))
    pdf = X.to_pdf(vd)
    assert pdf[:4] == b"%PDF" and b"Sarabun" in pdf
    f = tmp_path / "g.pdf"
    f.write_bytes(pdf)
    try:
        txt = subprocess.run(["pdftotext", str(f), "-"], capture_output=True, text=True, timeout=30).stdout
    except FileNotFoundError:  # pragma: no cover
        return
    assert "วิทยาลัยอาชีวศึกษานครราชสีมา" in txt and "DBT1-1" in txt


def test_excel_merged_cells(sched):
    vd = _vd(sched, "group", _group(sched))
    wb = load_workbook(io.BytesIO(X.to_excel(vd)))
    ws = wb.worksheets[0]
    multi = [e for e in vd.entries if e["end_period"] > e["start_period"]]
    horiz = [m for m in ws.merged_cells.ranges if m.min_row == m.max_row and m.min_row > 5]
    assert len(horiz) >= len(multi) > 0
    assert "รายการทั้งหมด" in wb.sheetnames


def test_csv_columns(sched):
    vd = _vd(sched, "group", _group(sched))
    raw = X.to_csv(vd)
    assert raw[:3] == b"\xef\xbb\xbf"
    head = raw.decode("utf-8-sig").splitlines()[0].split(",")
    for col in ["ปีการศึกษา", "ภาคเรียน", "วันเรียน", "คาบเริ่มต้น", "คาบสิ้นสุด", "เวลาเริ่มต้น", "เวลาสิ้นสุด", "รหัสวิชา", "ชื่อวิชา",
                "รหัสครู", "ชื่อครู", "รหัสห้อง", "หมายเลขห้อง", "รหัสกลุ่มเรียน", "ชื่อกลุ่มเรียน"]:
        assert col in head


def test_layout_merges_consecutive_not_break(sched):
    vd = _vd(sched, "group", _group(sched))
    cols, rows = render.layout(vd.ts, vd.entries)
    brk = [i for i, p in enumerate(cols) if p["kind"] == "break"][0]
    for _, lanes in rows:
        for segs in lanes:
            assert sum(sg.span for sg in segs) == len(cols)
            for sg in segs:
                if sg.kind == "entry":
                    e = sg.entry
                    assert sg.span == e["end_period"] - e["start_period"] + 1
                    assert not (sg.col <= brk < sg.col + sg.span)
    synth = [dict(vd.entries[0], id=1, day=0, start_period=1, end_period=4), dict(vd.entries[0], id=2, day=0, start_period=6, end_period=8)]
    _, rows2 = render.layout(vd.ts, synth)
    spans = [sg.span for sg in rows2[0][1][0] if sg.kind == "entry"]
    assert spans == [4, 3]


def test_print_html_only_table(sched):
    vd = _vd(sched, "group", _group(sched))
    html = render.printable_html(vd)
    assert "window.print()" in html and "@media print" in html and ".noprint{display:none" in html
    assert "landscape" in html


def test_student_and_teacher_headers(sched):
    with db.session_scope() as s:
        m = s.scalars(select(StudentMembership)).first()
        st = s.get(Student, m.student_id)
        t = s.scalars(select(Teacher).where(Teacher.code == "T005")).one()
        sid, scode, tid_ = st.id, st.code, t.id
    v = _vd(sched, "student", sid)
    h = dict(v.header)
    assert h["รหัสนักเรียน"] == scode and h["ครูที่ปรึกษา"] != "-"
    vt = _vd(sched, "teacher", tid_)
    ht = dict(vt.header)
    assert ht["รหัสครู"] == "T005" and "คาบ/สัปดาห์" in ht["จำนวนชั่วโมงสอนรวม"]


def test_college_and_special_views(sched):
    vc = _vd(sched, "college")
    assert len(vc.sub_views) == 4
    assert X.to_pdf(vc)[:4] == b"%PDF"
    for k in ("activity", "meeting", "homeroom"):
        v = _vd(sched, k)
        assert v.entries
