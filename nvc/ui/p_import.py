"""หน้านำเข้าข้อมูลหลายไฟล์ พร้อมจับคู่คอลัมน์ ตรวจสอบผลกระทบ และยืนยันก่อนบันทึก"""
from __future__ import annotations

import hashlib
import io
import zipfile

import pandas as pd
import streamlit as st

from .. import config, db
from ..importer import loader, mapping, readers
from ..importer.schemas import ENTITIES, ENTITY_ORDER, NONE_FIELD, alt_required_satisfied
from ..permissions import ADMIN
from .common import banner, df_show, require_ctx, require_term, term_label

K = "imp_sheets"


def _sample_zip() -> bytes:
    bio = io.BytesIO()
    folder = config.BASE_DIR / "sample_data"
    with zipfile.ZipFile(bio, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(folder.iterdir()):
            if p.is_file():
                z.write(p, p.name)
    return bio.getvalue()


def page() -> None:
    ctx = require_ctx(ADMIN)
    tid = require_term()
    banner("นำเข้าข้อมูล", f"นำเข้าได้หลายไฟล์พร้อมกัน (Excel, CSV, TSV, JSON, TXT) · บันทึกเข้าภาคเรียน: {term_label(tid)}")
    with st.expander("ℹ️ วิธีใช้งานและไฟล์ตัวอย่าง", expanded=False):
        st.markdown(
            "1. เลือกไฟล์ทั้งหมดพร้อมกัน (ไม่ต้องเรียงลำดับ) ระบบจะอ่านทุกแผ่นงานและคาดการณ์ว่าเป็นข้อมูลประเภทใด\n"
            "2. ตรวจสอบ/แก้ไขการจับคู่คอลัมน์ — รายการที่ระบบไม่มั่นใจจะแสดง “ต้องยืนยัน” ในช่องความมั่นใจ และต้องติ๊ก “ยืนยัน” ก่อน\n"
            "3. กด **ตรวจสอบผลกระทบ** เพื่อดูว่าจะเพิ่ม/อัปเดต/ข้ามกี่รายการ และมีข้อผิดพลาดแถวใด (ยังไม่บันทึก)\n"
            "4. กด **ยืนยันการนำเข้า** — ระบบนำเข้าตามลำดับความสัมพันธ์ของข้อมูลให้อัตโนมัติ\n\n"
            "ไฟล์ PDF/Word ไม่ใช่ตารางข้อมูลโดยตรง ต้องแปลงเป็น Excel หรือ CSV ก่อน"
        )
        if (config.BASE_DIR / "sample_data").exists():
            st.download_button("ดาวน์โหลดชุดไฟล์ตัวอย่าง (ข้อมูลสมมติ)", _sample_zip(), "nvc_sample_data.zip", "application/zip", icon=":material/download:")

    files = st.file_uploader("เลือกไฟล์ข้อมูล (เลือกได้หลายไฟล์)", accept_multiple_files=True,
                             type=[e.strip(".") for e in sorted(config.ALLOWED_UPLOAD_EXT)] + ["pdf", "docx"],
                             key=f"imp_files_{st.session_state.get('imp_nonce', 0)}")
    state: dict = st.session_state.setdefault(K, {})
    current_keys = set()
    read_errors = []
    for f in files or []:
        data = f.getvalue()
        h = hashlib.sha1(data).hexdigest()[:10]
        try:
            sheets = readers.read_file(f.name, data)
        except readers.ReadError as e:
            read_errors.append({"ไฟล์": f.name, "ปัญหา": str(e)})
            continue
        for sh in sheets:
            key = f"{h}::{sh.key}"
            current_keys.add(key)
            if key not in state:
                st.session_state.pop("imp_result", None)
                rank = mapping.detect_entity(sh.df, sh.filename, sh.sheet)
                ent = rank[0][0]
                state[key] = {"sheet": sh, "entity": ent, "rank": rank, "include": True, "mapping": _initial_map(sh.df, ent)}
    for k in list(state):
        if k not in current_keys:
            del state[k]
    if read_errors:
        st.error("ไฟล์ต่อไปนี้อ่านไม่ได้")
        df_show(read_errors)
    if not state:
        if "imp_result" in st.session_state:
            results, committed = st.session_state["imp_result"]
            _show_results(results, committed)
        else:
            st.info("ยังไม่ได้เลือกไฟล์")
        return

    st.subheader("ไฟล์ที่เลือก")
    summary = []
    for k, v in state.items():
        sh = v["sheet"]
        mp = _effective_map(v)
        miss = alt_required_satisfied(v["entity"], set(mp))
        need_conf = [r for r in v["mapping"] if r["needs_confirm"] and not r["confirmed"] and r["field"] != NONE_FIELD]
        status = "ขาดข้อมูลสำคัญ" if miss else ("รอยืนยันการจับคู่" if need_conf else "พร้อมตรวจสอบ")
        summary.append({"ไฟล์": sh.filename, "แผ่นงาน": sh.sheet or "-", "ประเภทไฟล์": sh.filename.rsplit(".", 1)[-1].upper(),
                        "จำนวนแถวที่อ่านได้": len(sh.df), "ชนิดข้อมูล": ENTITIES[v["entity"]]["label"],
                        "นำเข้า": "ใช่" if v["include"] else "ไม่", "สถานะ": status})
    df_show(summary)

    for k, v in state.items():
        sh = v["sheet"]
        with st.expander(f":material/description: {sh.filename}{' / ' + sh.sheet if sh.sheet else ''} — {ENTITIES[v['entity']]['label']} ({len(sh.df)} แถว)"):
            c1, c2 = st.columns([2, 1])
            ents = [e for e, _ in v["rank"]]
            new_ent = c1.selectbox("ชนิดข้อมูลของแผ่นงานนี้", ents, index=ents.index(v["entity"]),
                                   format_func=lambda e: ENTITIES[e]["label"], key=f"ent_{k}")
            if new_ent != v["entity"]:
                v["entity"] = new_ent
                v["mapping"] = _initial_map(sh.df, new_ent)
                st.rerun()
            v["include"] = c2.checkbox("นำเข้าแผ่นงานนี้", value=v["include"], key=f"inc_{k}")
            if sh.header_row:
                st.caption(f"ระบบตรวจพบหัวตารางที่แถว {sh.header_row + 1} ของไฟล์")
            st.markdown("**ตัวอย่างข้อมูล**")
            st.dataframe(sh.df.head(8), hide_index=True)
            st.markdown("**การจับคู่คอลัมน์** — แก้ไขช่อง “ใช้เป็น” ได้, รายการที่ระบุ “ต้องยืนยัน” ต้องติ๊กช่องยืนยันก่อน")
            fields = ENTITIES[v["entity"]]["fields"]
            label_of = {f: f"{spec['label']}{' *' if spec.get('required') else ''}" for f, spec in fields.items()}
            choices = [NONE_FIELD] + [label_of[f] for f in fields]
            rows = []
            for r in v["mapping"]:
                rows.append({"คอลัมน์ต้นทาง": r["source"], "ตัวอย่างข้อมูล": ", ".join(str(x) for x in sh.df[r["source"]].head(3).tolist()),
                             "ใช้เป็น": label_of.get(r["field"], NONE_FIELD) if r["field"] != NONE_FIELD else NONE_FIELD,
                             "ความมั่นใจ": ("ต้องยืนยัน · " if r["needs_confirm"] else "") + f"{int(r['confidence'] * 100)}%",
                             "เหตุผล": r["reason"], "ยืนยัน": r["confirmed"]})
            edited = st.data_editor(
                pd.DataFrame(rows), hide_index=True, key=f"map_{k}_{v['entity']}",
                column_config={"ใช้เป็น": st.column_config.SelectboxColumn(options=choices, required=True),
                               "ยืนยัน": st.column_config.CheckboxColumn(help="ติ๊กเพื่อยืนยันการจับคู่ที่ระบบไม่มั่นใจ")},
                disabled=["คอลัมน์ต้นทาง", "ตัวอย่างข้อมูล", "ความมั่นใจ", "เหตุผล"],
            )
            inv = {v_: f for f, v_ in label_of.items()}
            for r, (_, er) in zip(v["mapping"], edited.iterrows()):
                nf = inv.get(er["ใช้เป็น"], NONE_FIELD)
                if nf != r["field"]:
                    r["field"] = nf
                    r["confirmed"] = True  # ผู้ใช้เลือกเอง ถือว่ายืนยันแล้ว
                    r["needs_confirm"] = False
                else:
                    r["confirmed"] = bool(er["ยืนยัน"])
            mp = _effective_map(v)
            dup = _dups(v)
            if dup:
                st.error(f"มีหลายคอลัมน์ถูกจับคู่กับข้อมูลเดียวกัน: {', '.join(dup)}")
            miss = alt_required_satisfied(v["entity"], set(mp))
            if miss:
                st.error("ยังไม่ได้จับคู่ข้อมูลสำคัญ: " + ", ".join(fields[m]["label"] for m in miss) + " — ไม่สามารถนำเข้าได้จนกว่าจะระบุ")

    st.divider()
    st.subheader("ตัวเลือกการนำเข้า")
    mode = st.radio("เมื่อพบข้อมูลที่มีอยู่แล้ว", list(loader.MODES), format_func=lambda m: loader.MODES[m], key="imp_mode")
    c1, c2 = st.columns(2)
    allow_name = c1.checkbox("อนุญาตให้จับคู่ครูด้วยชื่อ เมื่อไม่พบรหัส (ระบบจะแจ้งเตือนทุกรายการ)", value=False)
    skip_err = c2.checkbox("ข้ามแถวที่มีข้อผิดพลาด แล้วนำเข้าแถวที่ถูกต้อง", value=False,
                           help="ถ้าไม่เลือก ไฟล์ที่มีข้อผิดพลาดจะไม่ถูกนำเข้าเลย (ข้อมูลเดิมไม่เปลี่ยนแปลง)")
    ready = [k for k, v in state.items() if v["include"]]
    sig = str([(k, state[k]["entity"], sorted(_effective_map(state[k]).items())) for k in ready]) + str((mode, allow_name, skip_err))
    if st.session_state.get("imp_sig") != sig:
        st.session_state["imp_sig"] = sig
        st.session_state.pop("imp_previewed", None)
    blockers = []
    for k in ready:
        v = state[k]
        if alt_required_satisfied(v["entity"], set(_effective_map(v))) or _dups(v):
            blockers.append(f"{v['sheet'].filename} {v['sheet'].sheet or ''}: การจับคู่คอลัมน์ยังไม่สมบูรณ์")
        if any(r["needs_confirm"] and not r["confirmed"] and r["field"] != NONE_FIELD for r in v["mapping"]):
            blockers.append(f"{v['sheet'].filename} {v['sheet'].sheet or ''}: มีการจับคู่ที่ต้องยืนยัน")
    for b in blockers:
        st.warning(b)
    b1, b2, b3 = st.columns([1, 1, 1])
    preview = b1.button("ตรวจสอบผลกระทบ (ยังไม่บันทึก)", icon=":material/search:", disabled=bool(blockers) or not ready, width="stretch")
    commit = b2.button("ยืนยันการนำเข้า", icon=":material/check_circle:", type="primary", disabled=bool(blockers) or not ready or not st.session_state.get("imp_previewed"), width="stretch",
                       help="ต้องกดตรวจสอบผลกระทบก่อน")
    if b3.button("ยกเลิกการนำเข้า", icon=":material/close:", width="stretch"):
        st.session_state.pop(K, None)
        st.session_state.pop("imp_previewed", None)
        st.session_state.pop("imp_result", None)
        st.session_state["imp_nonce"] = st.session_state.get("imp_nonce", 0) + 1
        st.rerun()
    if preview or commit:
        results = _run(ctx, tid, state, ready, mode, allow_name, skip_err, commit=commit)
        st.session_state["imp_result"] = (results, commit)
        st.session_state["imp_previewed"] = not commit
        if commit:
            st.session_state.pop(K, None)
            st.session_state.pop("imp_previewed", None)
            st.session_state["imp_nonce"] = st.session_state.get("imp_nonce", 0) + 1  # ล้างรายการไฟล์ที่เลือก
        st.rerun()
    if "imp_result" in st.session_state:
        results, committed = st.session_state["imp_result"]
        _show_results(results, committed)


def _initial_map(df: pd.DataFrame, ent: str) -> list[dict]:
    out = []
    for g in mapping.suggest_mapping(df, ent):
        out.append({"source": g.source, "field": g.field or NONE_FIELD, "confidence": g.confidence, "reason": g.reason,
                    "needs_confirm": g.needs_confirm, "confirmed": not g.needs_confirm})
    return out


def _effective_map(v: dict) -> dict[str, str]:
    mp = {}
    for r in v["mapping"]:
        if r["field"] != NONE_FIELD and (not r["needs_confirm"] or r["confirmed"]):
            mp.setdefault(r["field"], r["source"])
    return mp


def _dups(v: dict) -> list[str]:
    seen, d = set(), []
    for r in v["mapping"]:
        if r["field"] == NONE_FIELD:
            continue
        if r["field"] in seen:
            d.append(ENTITIES[v["entity"]]["fields"][r["field"]]["label"])
        seen.add(r["field"])
    return d


def _run(ctx, tid, state, keys, mode, allow_name, skip_err, commit: bool):
    order = sorted(keys, key=lambda k: ENTITY_ORDER.index(state[k]["entity"]))
    results = []
    s = db.session()
    try:
        for k in order:
            v = state[k]
            sh = v["sheet"]
            res = loader.run_import(s, ctx, tid, v["entity"], sh.df, _effective_map(v), mode, sh.filename, sh.sheet, sh.header_row,
                                    allow_name, skip_err, dry_run=False)
            results.append((sh.filename, sh.sheet, v["entity"], res))
        if commit:
            s.commit()
        else:
            s.rollback()
    except Exception as e:
        s.rollback()
        st.error(f"เกิดข้อผิดพลาดระหว่างนำเข้า ข้อมูลเดิมไม่ถูกเปลี่ยนแปลง: {e}")
        results = []
    finally:
        s.close()
    return results


def _show_results(results, committed: bool) -> None:
    st.subheader("ผลการนำเข้า" if committed else "ผลกระทบที่จะเกิดขึ้น (ยังไม่บันทึก)")
    rows, issues = [], []
    for fn, sh, ent, r in results:
        aborted = any("ยกเลิกการนำเข้าทั้งไฟล์" in i.message for i in r.issues)
        state = ("บันทึกแล้ว" if r.committed else ("ไม่ถูกบันทึก" if aborted else "-")) if committed else (
            "จะไม่ถูกนำเข้า (มีข้อผิดพลาด)" if r.errors and not r.committed else "พร้อมนำเข้า")
        rows.append({"ไฟล์": fn, "แผ่นงาน": sh or "-", "ชนิดข้อมูล": ENTITIES[ent]["label"], "เพิ่มใหม่": r.counts["new"],
                     "อัปเดต": r.counts["updated"], "ไม่เปลี่ยนแปลง": r.counts["unchanged"], "ข้าม": r.counts["skipped"],
                     "แถวที่ผิดพลาด": r.counts["error"], "สถานะ": state})
        issues.extend(i.as_dict() for i in r.issues)
    df_show(rows)
    if committed and any(r.committed for *_, r in results):
        st.success("นำเข้าข้อมูลเรียบร้อย — ไปที่เมนู “ตรวจสอบความพร้อม” เพื่อตรวจข้อมูลก่อนจัดตาราง")
    if issues:
        st.markdown("**รายงานปัญหา** (ระบุไฟล์ แผ่นงาน แถว และคอลัมน์)")
        lv = st.multiselect("แสดงระดับ", ["ข้อผิดพลาด", "คำเตือน", "ข้อมูลทั่วไป"], default=["ข้อผิดพลาด", "คำเตือน"], key="imp_lv")
        df = pd.DataFrame(issues)
        df_show(df[df["ระดับ"].isin(lv)], height=320)
        st.download_button("ดาวน์โหลดรายงานปัญหา (CSV)", ("﻿" + df.to_csv(index=False)).encode("utf-8"), "import_issues.csv", "text/csv", icon=":material/download:")
    elif results:
        st.success("ไม่พบข้อผิดพลาดหรือคำเตือน")
