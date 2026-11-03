"""บันทึกประวัติการดำเนินการสำคัญ (ไม่บันทึกรหัสผ่าน)"""
from __future__ import annotations

from sqlalchemy.orm import Session

from .models import AuditLog


def log(s: Session, username: str | None, action: str, entity: str | None = None, detail=None) -> None:
    if isinstance(detail, dict):
        detail = {k: v for k, v in detail.items() if "password" not in k.lower()}
    s.add(AuditLog(username=username, action=action, entity=entity, detail=detail))
