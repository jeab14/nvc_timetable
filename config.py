"""การตั้งค่าระดับแอปพลิเคชัน (อ่านจาก st.secrets หรือ environment variables)

ห้ามใส่รหัสผ่านหรือค่าลับลงในไฟล์นี้ ค่าลับทั้งหมดต้องมาจาก
- Streamlit Cloud: App settings > Secrets
- เครื่องพัฒนา: .streamlit/secrets.toml (ไม่ commit) หรือ environment variable
"""
from __future__ import annotations

import os
from pathlib import Path

APP_NAME_TH = "ระบบจัดตารางสอนอัตโนมัติ วิทยาลัยอาชีวศึกษานครราชสีมา"
APP_NAME_EN = "NVC Smart Timetable"
COLLEGE_NAME = "วิทยาลัยอาชีวศึกษานครราชสีมา"

BASE_DIR = Path(__file__).resolve().parent.parent
ASSETS_DIR = BASE_DIR / "assets"
FONT_REGULAR = ASSETS_DIR / "fonts" / "Sarabun-Regular.ttf"
FONT_BOLD = ASSETS_DIR / "fonts" / "Sarabun-Bold.ttf"
LOGO_PATH = ASSETS_DIR / "logo.png"  # วางไฟล์โลโก้ที่ได้รับอนุญาตไว้ที่นี่ (ถ้ามี)
DOCS_DIR = BASE_DIR / "docs"

MAX_UPLOAD_MB = 20
ALLOWED_UPLOAD_EXT = {".xlsx", ".xls", ".csv", ".tsv", ".json", ".txt"}
LOGIN_MAX_ATTEMPTS = 5
LOGIN_LOCK_MINUTES = 5


def _secret(name: str, default=None):
    try:
        import streamlit as st

        if name in st.secrets:
            return st.secrets[name]
        if "database" in st.secrets and name == "DATABASE_URL" and "url" in st.secrets["database"]:
            return st.secrets["database"]["url"]
    except Exception:
        pass
    return os.environ.get(name, default)


def database_url() -> str:
    """คืนค่า URL ฐานข้อมูล

    - ระบบใช้งานจริง: ต้องตั้ง DATABASE_URL เป็น PostgreSQL เช่น
      postgresql+psycopg2://user:pass@host:5432/dbname?sslmode=require
    - หากไม่ได้ตั้งค่า จะใช้ SQLite ไฟล์ในเครื่องสำหรับพัฒนา/ทดสอบเท่านั้น
    """
    url = _secret("DATABASE_URL")
    if url:
        url = str(url)
        if url.startswith("postgres://"):
            url = "postgresql+psycopg2://" + url[len("postgres://"):]
        elif url.startswith("postgresql://"):
            url = "postgresql+psycopg2://" + url[len("postgresql://"):]
        return url
    return f"sqlite:///{BASE_DIR / 'local_dev.sqlite3'}"


def is_sqlite(url: str | None = None) -> bool:
    return (url or database_url()).startswith("sqlite")
