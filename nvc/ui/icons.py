"""ไอคอนของระบบ — ใช้ Google Material Symbols (Rounded) ชุดเดียวทั้งระบบ

- ปุ่ม/เมนู/ป้ายของ Streamlit: ใช้ mi("name") -> ":material/name:" (ฟอนต์ไอคอนมากับ Streamlit อยู่แล้ว ไม่ต้องติดตั้งเพิ่ม ทำงานได้แม้ไม่มีอินเทอร์เน็ต)
- HTML ในหน้าเว็บ: ใช้ ic("name") -> <span class="mi">name</span> (ฟอนต์เดียวกัน)
- HTML ใน iframe ตาราง/หน้าพิมพ์ (ไม่มีฟอนต์ของ Streamlit): ใช้ svg("name") รูปไอคอน Material แบบฝังในหน้า
"""
from __future__ import annotations

# ชื่อไอคอนตามหน้าที่ (เปลี่ยนที่นี่ที่เดียว มีผลทั้งระบบ)
PAGE = {
    "dashboard": "space_dashboard", "my": "person", "view": "calendar_month", "compare": "group",
    "history": "history", "requests": "edit_note", "password": "key", "manual": "menu_book", "logout": "logout",
    "import": "upload_file", "data": "database", "readiness": "fact_check", "auto": "auto_mode", "edit": "edit_calendar",
    "versions": "inventory_2", "users": "manage_accounts", "settings": "settings", "audit": "receipt_long", "deleted": "delete_history", "subjects": "difference",
    "setup": "settings", "login": "lock",
}


def mi(name: str) -> str:
    """ไอคอนสำหรับพารามิเตอร์ icon= และข้อความ markdown ของ Streamlit"""
    return f":material/{name}:"


def ic(name: str, cls: str = "") -> str:
    """ไอคอนสำหรับ HTML ที่แสดงในหน้า Streamlit"""
    return f'<span class="mi {cls}" aria-hidden="true">{name}</span>'


# path ของ Material Icons (Apache License 2.0) สำหรับหน้าที่ไม่มีฟอนต์ไอคอน
_SVG = {
    "print": "M19 8H5c-1.66 0-3 1.34-3 3v6h4v4h12v-4h4v-6c0-1.66-1.34-3-3-3zm-3 11H8v-5h8v5zm3-7c-.55 0-1-.45-1-1s.45-1 1-1 1 .45 1 1-.45 1-1 1zm-1-9H6v4h12V3z",
    "warning": "M1 21h22L12 2 1 21zm12-3h-2v-2h2v2zm0-4h-2v-4h2v4z",
    "person": "M12 12c2.21 0 4-1.79 4-4s-1.79-4-4-4-4 1.79-4 4 1.79 4 4 4zm0 2c-2.67 0-8 1.34-8 4v2h16v-2c0-2.66-5.33-4-8-4z",
    "location": "M12 2C8.13 2 5 5.13 5 9c0 5.25 7 13 7 13s7-7.75 7-13c0-3.87-3.13-7-7-7zm0 9.5c-1.38 0-2.5-1.12-2.5-2.5s1.12-2.5 2.5-2.5 2.5 1.12 2.5 2.5-1.12 2.5-2.5 2.5z",
    "group": "M16 11c1.66 0 2.99-1.34 2.99-3S17.66 5 16 5c-1.66 0-3 1.34-3 3s1.34 3 3 3zm-8 0c1.66 0 2.99-1.34 2.99-3S9.66 5 8 5C6.34 5 5 6.34 5 8s1.34 3 3 3zm0 2c-2.33 0-7 1.17-7 3.5V19h14v-2.5c0-2.33-4.67-3.5-7-3.5zm8 0c-.29 0-.62.02-.97.05 1.16.84 1.97 1.97 1.97 3.45V19h6v-2.5c0-2.33-4.67-3.5-7-3.5z",
    "schedule": "M11.99 2C6.47 2 2 6.48 2 12s4.47 10 9.99 10C17.52 22 22 17.52 22 12S17.52 2 11.99 2zM12 20c-4.42 0-8-3.58-8-8s3.58-8 8-8 8 3.58 8 8-3.58 8-8 8zm.5-13H11v6l5.25 3.15.75-1.23-4.5-2.67z",
    "event": "M19 4h-1V2h-2v2H8V2H6v2H5c-1.11 0-1.99.9-1.99 2L3 20c0 1.1.89 2 2 2h14c1.1 0 2-.9 2-2V6c0-1.1-.9-2-2-2zm0 16H5V9h14v11zM7 11h5v5H7z",
}


def svg(name: str, size: int = 13, color: str = "currentColor") -> str:
    return (f'<svg width="{size}" height="{size}" viewBox="0 0 24 24" fill="{color}" aria-hidden="true" '
            f'style="vertical-align:-2px;flex:0 0 auto"><path d="{_SVG[name]}"/></svg>')
