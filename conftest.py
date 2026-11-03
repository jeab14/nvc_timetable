"""ชุดทดสอบ: ใช้ฐานข้อมูล SQLite ชั่วคราวแยกต่อการทดสอบ และข้อมูลตัวอย่างสมมติใน sample_data/"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from nvc import db, settings_service as S  # noqa: E402
from nvc.importer import loader, mapping, readers  # noqa: E402
from nvc.importer.schemas import ENTITY_ORDER  # noqa: E402
from nvc.models import Term  # noqa: E402
from nvc.permissions import Ctx  # noqa: E402

SAMPLE = ROOT / "sample_data"
ADMIN = Ctx(1, "admin", "admin", "ผู้ดูแลทดสอบ")


def _ensure_sample():
    if not SAMPLE.exists() or len(list(SAMPLE.iterdir())) < 11:
        subprocess.run([sys.executable, str(ROOT / "scripts" / "make_sample_data.py")], check=True)


PG_URL = os.environ.get("NVC_TEST_DATABASE_URL")  # ตั้งค่าเพื่อรันชุดทดสอบกับ PostgreSQL จริง


def enable_all_special(s, tid):
    """เปิดเงื่อนไขพิเศษข้อ 9–13 ทั้งหมด (ชุดทดสอบตรวจพฤติกรรมเมื่อเลือกใช้)"""
    S.put(s, "room_type_rule", True, tid)
    S.put(s, "general_joint", True, tid)
    for k in ("meeting", "homeroom", "activity"):
        v = S.get(s, k, tid)
        v["enabled"] = True
        S.put(s, k, v, tid)


def _reset(url: str):
    from nvc.models import Base

    eng = db.configure(url)
    if PG_URL:
        Base.metadata.drop_all(eng)
    db.init_db()


@pytest.fixture()
def fresh_db(tmp_path):
    _reset(PG_URL or f"sqlite:///{tmp_path / 'test.db'}")
    with db.session_scope() as s:
        t = Term(year=2569, semester=1, is_current=True)
        s.add(t)
        s.flush()
        S.put(s, "hour_to_period_confirmed", True, t.id)
        S.put(s, "solver_time_limit", 15, t.id)
        enable_all_special(s, t.id)
        tid = t.id
    return tid


def import_file(tid: int, name: str, entity: str | None = None, mode: str = "update", skip_errors: bool = False, dry_run: bool = False):
    p = SAMPLE / name
    results = []
    for sh in readers.read_file(p.name, p.read_bytes()):
        ent = entity or mapping.detect_entity(sh.df, sh.filename, sh.sheet)[0][0]
        mp = {g.field: g.source for g in mapping.suggest_mapping(sh.df, ent) if g.field}
        with db.session_scope() as s:
            results.append(loader.run_import(s, ADMIN, tid, ent, sh.df, mp, mode, sh.filename, sh.sheet, sh.header_row,
                                             skip_error_rows=skip_errors, dry_run=dry_run))
    return results


def import_all(tid: int):
    _ensure_sample()
    files = sorted(SAMPLE.iterdir())
    plan = []
    for p in files:
        for sh in readers.read_file(p.name, p.read_bytes()):
            ent = mapping.detect_entity(sh.df, sh.filename, sh.sheet)[0][0]
            plan.append((ENTITY_ORDER.index(ent), p.name, ent))
    for _, name, ent in sorted(plan):
        res = import_file(tid, name, ent)
        for r in res:
            assert not r.errors, (name, [i.message for i in r.errors])


@pytest.fixture()
def loaded(fresh_db):
    import_all(fresh_db)
    return fresh_db


@pytest.fixture(scope="module")
def scheduled(tmp_path_factory):
    """จัดตารางครั้งเดียวต่อโมดูลทดสอบ (ใช้เวลาหลายวินาที)"""
    from nvc import timetable_service as T

    d = tmp_path_factory.mktemp("sched")
    url = PG_URL or f"sqlite:///{d / 'sched.db'}"
    _reset(url)
    with db.session_scope() as s:
        t = Term(year=2569, semester=1, is_current=True)
        s.add(t)
        s.flush()
        S.put(s, "hour_to_period_confirmed", True, t.id)
        enable_all_special(s, t.id)
        tid = t.id
    import_all(tid)
    with db.session_scope() as s:
        tt = T.run_auto(s, ADMIN, tid, time_limit=15)
        ttid = tt.id
    return {"term": tid, "tt": ttid, "url": url}


@pytest.fixture()
def sched(scheduled):
    db.configure(scheduled["url"])
    return scheduled
