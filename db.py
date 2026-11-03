"""การเชื่อมต่อฐานข้อมูลและธุรกรรม"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine, event, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from . import config
from .models import Base, RoomType, Term

_engine: Engine | None = None
_SessionLocal: sessionmaker | None = None

DEFAULT_ROOM_TYPES = [
    ("theory", "ห้องทฤษฎี", "theory"),
    ("lab", "ห้องปฏิบัติการ", "practice"),
    ("computer", "ห้องคอมพิวเตอร์", "practice"),
    ("special", "ห้องเฉพาะทาง", "practice"),
    ("activity", "ห้องกิจกรรม", "activity"),
    ("meeting", "ห้องประชุม", "other"),
]


def _make_engine(url: str) -> Engine:
    if url.startswith("sqlite"):
        eng = create_engine(url, connect_args={"check_same_thread": False}, future=True)

        @event.listens_for(eng, "connect")
        def _fk_on(dbapi_conn, _):  # pragma: no cover - sqlite only
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()

        return eng
    return create_engine(url, pool_pre_ping=True, pool_recycle=1800, future=True)


def configure(url: str | None = None) -> Engine:
    """สร้าง engine ใหม่ (ใช้ในชุดทดสอบเพื่อชี้ไปฐานข้อมูลทดสอบ)"""
    global _engine, _SessionLocal
    if _engine is not None:
        _engine.dispose()
    _engine = _make_engine(url or config.database_url())
    _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False, future=True)
    return _engine


def engine() -> Engine:
    if _engine is None:
        configure()
    return _engine  # type: ignore[return-value]


def session() -> Session:
    if _SessionLocal is None:
        configure()
    return _SessionLocal()  # type: ignore[misc]


@contextmanager
def session_scope() -> Iterator[Session]:
    """ธุรกรรม: commit เมื่อสำเร็จทั้งหมด, rollback เมื่อมีข้อผิดพลาด"""
    s = session()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def init_db() -> None:
    """สร้างตาราง (ถ้ายังไม่มี) และข้อมูลตั้งต้นที่จำเป็น — ไม่สร้างข้อมูลจำลอง"""
    Base.metadata.create_all(engine())
    with session_scope() as s:
        if not s.scalars(select(RoomType)).first():
            for code, name, kind in DEFAULT_ROOM_TYPES:
                s.add(RoomType(code=code, name=name, kind=kind))


def check_connection() -> tuple[bool, str]:
    try:
        with engine().connect() as c:
            c.exec_driver_sql("SELECT 1")
        return True, "เชื่อมต่อฐานข้อมูลสำเร็จ"
    except Exception as e:  # pragma: no cover
        return False, f"ไม่สามารถเชื่อมต่อฐานข้อมูลได้: {e.__class__.__name__}"


def current_term(s: Session) -> Term | None:
    t = s.scalars(select(Term).where(Term.is_current.is_(True))).first()
    if t is None:
        t = s.scalars(select(Term).order_by(Term.year.desc(), Term.semester.desc())).first()
    return t
