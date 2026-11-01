"""การทดสอบการนำเข้าข้อมูล (หัวข้อ 25)"""
from __future__ import annotations

import pandas as pd
import pytest
from sqlalchemy import func, select

from nvc import db
from nvc.importer import loader, mapping, readers
from nvc.models import Course, Room, Teacher

from .conftest import ADMIN, SAMPLE, import_file


def _csv(df: pd.DataFrame, name="x.csv"):
    return readers.read_file(name, df.to_csv(index=False).encode("utf-8-sig"))[0]


def _map(sh, ent):
    return {g.field: g.source for g in mapping.suggest_mapping(sh.df, ent) if g.field}


def test_thai_headers_detected(fresh_db):
    sh = readers.read_file("04_รายวิชา.xlsx", (SAMPLE / "04_รายวิชา.xlsx").read_bytes())[0]
    assert mapping.detect_entity(sh.df, sh.filename, sh.sheet)[0][0] == "course"
    mp = _map(sh, "course")
    assert mp["code"] == "รหัสวิชา" and mp["name"] == "ชื่อวิชา" and mp["tpn"] == "ท-ป-น"


def test_english_headers_and_extra_columns(fresh_db):
    sh = _csv(pd.DataFrame([{"No.": 1, "Teacher ID": "X1", "Full Name": "นายทดสอบ ระบบ", "Department": "IT", "Max Hours": 20, "Phone": "-"}]))
    mp = _map(sh, "teacher")
    assert mp["code"] == "Teacher ID" and mp["name"] == "Full Name" and mp["max_hours_week"] == "Max Hours"
    assert "No." not in mp.values() and "Phone" not in mp.values()


def test_synonyms_th_en():
    for header, field in [("รหัสรายวิชา", "code"), ("Course ID", "code"), ("Subject Code", "code")]:
        sh = _csv(pd.DataFrame([{header: "20000-1101", "ชื่อวิชา": "ภาษาไทย"}]))
        assert _map(sh, "course").get(field) == header
    for header in ["ชื่ออาจารย์", "ชื่อครู", "ผู้สอน", "Teacher Name"]:
        sh = _csv(pd.DataFrame([{"รหัสครู": "T1", header: "นายเอ บี"}]))
        assert _map(sh, "teacher").get("name") == header, header
    for header in ["กลุ่มเรียน", "กลุ่ม", "รหัสกลุ่ม", "Class Group"]:
        sh = _csv(pd.DataFrame([{header: "G1", "รหัสวิชา": "20000-1101"}]))
        assert _map(sh, "group_course").get("group") == header, header


def test_content_used_not_only_header():
    # คอลัมน์ชื่อ "จำนวน" แต่ข้อมูลเป็นข้อความ -> ไม่ควรจับคู่กับจำนวนสมาชิกอย่างมั่นใจ
    sh = _csv(pd.DataFrame([{"รหัสกลุ่ม": "G1", "จำนวน": "ไม่ทราบ"}, {"รหัสกลุ่ม": "G2", "จำนวน": "สามสิบ"}]))
    g = {x.source: x for x in mapping.suggest_mapping(sh.df, "group")}
    assert g["จำนวน"].field is None or g["จำนวน"].needs_confirm


def test_multi_file_import_and_links(loaded):
    with db.session_scope() as s:
        assert s.scalar(select(func.count(Course.id))) == 12
        assert s.scalar(select(func.count(Teacher.id))) == 10
        assert s.scalar(select(func.count(Room.id))) == 9


def test_reordered_columns_and_header_not_first_row(fresh_db):
    r = import_file(fresh_db, "01_แผนกวิชา.csv")
    r = import_file(fresh_db, "03_ห้องเรียน.xlsx")  # หัวตารางอยู่แถวที่ 3 และคอลัมน์สลับลำดับ
    assert r[0].counts["new"] == 9 and not r[0].errors


def test_reference_to_missing_code_is_error(fresh_db):
    r = import_file(fresh_db, "08_teaching_assignments.xlsx", "assignment")
    assert r[0].errors and not r[0].committed
    assert "ไม่พบ" in r[0].errors[0].message and r[0].errors[0].row is not None


def test_partial_missing_and_wrong_type(fresh_db):
    df = pd.DataFrame([{"รหัสห้อง": "A1", "ความจุ": "40"}, {"รหัสห้อง": "A2", "ความจุ": "สี่สิบ"}, {"รหัสห้อง": "", "ความจุ": "30"}])
    sh = _csv(df)
    with db.session_scope() as s:
        res = loader.run_import(s, ADMIN, fresh_db, "room", sh.df, _map(sh, "room"), "add", "x.csv", dry_run=False)
    assert res.counts["error"] == 2 and not res.committed  # ไม่ข้ามแถวผิด -> ไม่บันทึกทั้งไฟล์
    rows = {i.row for i in res.errors if i.row}
    assert rows == {3, 4}
    with db.session_scope() as s:
        assert s.scalar(select(func.count(Room.id))) == 0
        res = loader.run_import(s, ADMIN, fresh_db, "room", sh.df, _map(sh, "room"), "add", "x.csv", skip_error_rows=True, dry_run=False)
    assert res.committed and res.counts["new"] == 1


def test_duplicate_codes(fresh_db):
    df = pd.DataFrame([{"รหัสครู": "T1", "ชื่อครู": "นายเอ"}, {"รหัสครู": "T1", "ชื่อครู": "นายบี"}, {"รหัสครู": "T2", "ชื่อครู": "นายซี"}, {"รหัสครู": "T2", "ชื่อครู": "นายซี"}])
    sh = _csv(df)
    with db.session_scope() as s:
        res = loader.run_import(s, ADMIN, fresh_db, "teacher", sh.df, _map(sh, "teacher"), "add", "t.csv", skip_error_rows=True, dry_run=False)
    msgs = " ".join(i.message for i in res.issues)
    assert "อาจหมายถึงคนละรายการ" in msgs  # รหัสซ้ำแต่ชื่อต่าง
    assert res.counts["skipped"] == 1  # ซ้ำเหมือนกันทุกช่อง -> ข้าม


def test_no_silent_overwrite(fresh_db):
    sh = _csv(pd.DataFrame([{"รหัสครู": "T1", "ชื่อครู": "นายเอ", "ชั่วโมงสอนสูงสุด": 20}]))
    with db.session_scope() as s:
        loader.run_import(s, ADMIN, fresh_db, "teacher", sh.df, _map(sh, "teacher"), "add", "t.csv", dry_run=False)
    sh2 = _csv(pd.DataFrame([{"รหัสครู": "T1", "ชื่อครู": "นายเอ", "ชั่วโมงสอนสูงสุด": 30}]))
    with db.session_scope() as s:
        res = loader.run_import(s, ADMIN, fresh_db, "teacher", sh2.df, _map(sh2, "teacher"), "add", "t.csv", dry_run=False)
    assert res.counts["skipped"] == 1 and res.warnings
    with db.session_scope() as s:
        assert s.scalars(select(Teacher)).one().max_hours_week == 20
        res = loader.run_import(s, ADMIN, fresh_db, "teacher", sh2.df, _map(sh2, "teacher"), "update", "t.csv", dry_run=False)
    with db.session_scope() as s:
        assert s.scalars(select(Teacher)).one().max_hours_week == 30


def test_dry_run_changes_nothing(fresh_db):
    r = import_file(fresh_db, "02_teachers.csv", dry_run=True)
    assert r[0].counts["new"] == 10
    with db.session_scope() as s:
        assert s.scalar(select(func.count(Teacher.id))) == 0


def test_unsupported_and_formats():
    with pytest.raises(readers.ReadError) as e:
        readers.read_file("x.pdf", b"%PDF-1.4")
    assert "แปลง" in str(e.value)
    js = '[{"รหัสวิชา":"20000-1101","ชื่อวิชา":"ไทย"}]'.encode()
    assert len(readers.read_file("a.json", js)[0].df) == 1
    tsv = "รหัสครู\tชื่อครู\nT1\tนายเอ\n".encode("cp874")
    assert readers.read_file("a.tsv", tsv)[0].df.iloc[0]["ชื่อครู"] == "นายเอ"


def test_tpn_and_hours_consistency(fresh_db):
    sh = _csv(pd.DataFrame([{"รหัสวิชา": "1", "ชื่อวิชา": "ก", "ท-ป-น": "2-2-3", "ชั่วโมงทฤษฎี": "1"}]))
    with db.session_scope() as s:
        res = loader.run_import(s, ADMIN, fresh_db, "course", sh.df, _map(sh, "course"), "add", "c.csv", dry_run=False)
    assert res.errors and "ไม่สอดคล้อง" in res.errors[0].message
