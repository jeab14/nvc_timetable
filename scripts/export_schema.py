"""สร้างไฟล์โครงสร้างฐานข้อมูล (DDL) สำหรับ PostgreSQL จาก models.py
รัน: python scripts/export_schema.py > docs/schema_postgresql.sql
(ระบบสร้างตารางให้อัตโนมัติเมื่อเริ่มทำงาน ไฟล์นี้มีไว้เพื่อตรวจสอบ/สร้างเองด้วยมือ)
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from sqlalchemy.dialects import postgresql  # noqa: E402
from sqlalchemy.schema import CreateIndex, CreateTable  # noqa: E402

from nvc.models import Base  # noqa: E402

print("-- NVC Smart Timetable: PostgreSQL schema (generated from nvc/models.py)\n")
for t in Base.metadata.sorted_tables:
    print(str(CreateTable(t).compile(dialect=postgresql.dialect())).strip() + ";\n")
    for ix in t.indexes:
        print(str(CreateIndex(ix).compile(dialect=postgresql.dialect())).strip() + ";\n")
