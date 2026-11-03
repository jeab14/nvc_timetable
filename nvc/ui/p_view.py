"""หน้าดูตาราง (ทุกประเภท) ส่งออก/พิมพ์ ตารางของฉัน เปรียบเทียบตารางครู เปรียบเทียบรายวิชา และประวัติตาราง"""
from __future__ import annotations

import html
import io
import zipfile

import streamlit as st
import streamlit.components.v1 as components
from sqlalchemy import select

from .. import settings_service as S, timetable_service as T
from ..export import exporters as X
from ..models import Assignment, Course, Teacher, Timetable
from ..permissions import ADMIN, DEPT_ADMIN, STUDENT, TEACHER, PermissionDenied
from ..render import day_list_html, estimated_height, printable_html
from ..views import SUBJECT_CATS, VIEW_KINDS, allowed_kinds, get_view, options, subject_catalog
from .common import banner, df_show, empty_state, require_ctx, require_term, term_label, term_options, timetable_options, tt_label, tx


def _safe_name(s: str) -> str:
    import re

    out = re.sub(r"[^\w\u0E00-\u0E7F.\-]+", "_", s, flags=re.UNICODE).strip("_")
    return out[:80] or "timetable"


def render_view(ctx, tt_id: int, kind: str, key, show_export: bool = True, key_prefix: str = "v") -> None:
    try:
        with tx() as s:
            vd = get_view(s, ctx, s.get(Timetable, tt_id), kind, key)
    except PermissionDenied as e:
        st.error(str(e), icon=":material/block:")
        return
    # แถบเครื่องมือ: ชื่อตาราง (ซ้าย) · รูปแบบการแสดงผล และปุ่มส่งออก (ขวา ติดกับตาราง)
    with st.container(border=True):
        top = st.columns([2.2, 1.6], vertical_alignment="center")
        n_teach = sum(e["end_period"] - e["start_period"] + 1 for e in vd.entries if e["entry_type"] in ("class", "activity"))
        top[0].markdown(f'<div style="font-size:17px;font-weight:700;color:#0b3d91">{html.escape(vd.title)}</div>'
                        f'<div style="font-size:13px;color:#64748b">{html.escape(vd.term_label)} · {html.escape(vd.timetable_label)}'
                        f' · {len(vd.entries)} รายการ · {n_teach} คาบเรียน/สัปดาห์</div>', unsafe_allow_html=True)
        with top[1]:
            mode = st.segmented_control("รูปแบบการแสดงผล", ["grid", "list"], format_func={"grid": ":material/grid_view: ตาราง", "list": ":material/view_agenda: รายการรายวัน"}.get,
                                        default="grid", key=f"{key_prefix}_mode_{kind}", label_visibility="collapsed") or "grid"
        if show_export:
            c = st.columns(3)
            fname = _safe_name(f"{vd.title}_{vd.term_label}")
            try:
                c[0].download_button("PDF", X.to_pdf(vd), f"{fname}.pdf", "application/pdf", key=f"{key_prefix}_pdf_{kind}_{key}", width="stretch", icon=":material/picture_as_pdf:")
            except Exception as e:  # pragma: no cover
                c[0].error(f"สร้าง PDF ไม่สำเร็จ: {e}")
            c[1].download_button("Excel", X.to_excel(vd), f"{fname}.xlsx",
                                 "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", key=f"{key_prefix}_xlsx_{kind}_{key}", width="stretch", icon=":material/table_view:")
            c[2].download_button("CSV", X.to_csv(vd), f"{fname}.csv", "text/csv", key=f"{key_prefix}_csv_{kind}_{key}", width="stretch", icon=":material/csv:")
    if vd.conflicts:
        st.error(f"พบรายการที่ชนกัน {len(vd.conflicts)} รายการในตารางนี้ (กรอบสีแดง) — ตารางนี้ยังไม่ผ่านการตรวจสอบ")
    if not vd.entries and not vd.sub_views:
        empty_state("ยังไม่มีรายการในตารางนี้", "calendar_month")
    elif mode == "list" and not vd.sub_views:
        st.markdown(day_list_html(vd), unsafe_allow_html=True)
    else:
        components.html(printable_html(vd), height=min(estimated_height(vd), 6000), scrolling=True)
    if vd.entries and not vd.sub_views:
        with st.expander("รายละเอียดรายการ (คลิกดูข้อมูลเพิ่มเติมเมื่อช่องตารางแสดงไม่พอ)"):
            df_show([{"วัน": S.DAY_NAMES[e["day"]], "เวลา": f"{e['start_time']}–{e['end_time']}", "คาบ": f"{e['start_period']}-{e['end_period']}",
                      "รหัสวิชา": e.get("course_code") or "", "ชื่อวิชา": e.get("course_name") or {"homeroom": "โฮมรูม", "meeting": "ประชุม"}.get(e["entry_type"], ""),
                      "ห้อง": e.get("room_label") or "-", "ครู": ", ".join(e.get("teacher_names") or []), "กลุ่มเรียน": ", ".join(e.get("group_codes") or []),
                      "สถานะ": "ชนกัน" if e["id"] in vd.conflicts else ""} for e in sorted(vd.entries, key=lambda x: (x["day"], x["start_period"]))])


def _pick_timetable(ctx, term_id: int, key: str, published_only: bool = False):
    tts = timetable_options(ctx, term_id)
    if published_only:
        tts = [t for t in tts if t.status in ("published", "superseded")]
    if not tts:
        return None
    ids = [t.id for t in tts]
    pub = next((t.id for t in tts if t.status == "published"), ids[0])
    return st.selectbox("ตารางฉบับ", ids, index=ids.index(pub), format_func=lambda i: tt_label(next(t for t in tts if t.id == i)), key=key)


def view_page() -> None:
    ctx = require_ctx()
    tid = require_term()
    banner("ตารางเรียน / พิมพ์ตาราง", f"{term_label(tid)} · เลือกรูปแบบตารางและผู้สอน/กลุ่มเรียน แล้วพิมพ์หรือดาวน์โหลดได้ทันที")
    tts = timetable_options(ctx, tid)
    if ctx.role in (TEACHER, STUDENT):
        tts = [t for t in tts if t.status in ("published", "superseded")]
    if not tts:
        st.info("ภาคเรียนนี้ยังไม่มีตาราง" + ("ที่เผยแพร่" if ctx.role in (TEACHER, STUDENT) else ""))
        return
    kinds = allowed_kinds(ctx)
    with st.container(border=True):
        c1, c2 = st.columns(2)
        kind = c1.selectbox("เลือกรูปแบบแสดงตาราง", kinds, format_func=VIEW_KINDS.get, key="view_kind_sel")
        key = None
        if kind in ("group", "teacher", "student", "room", "course", "department"):
            with tx() as s:
                opts = options(s, ctx, tid, kind)
            if not opts:
                c2.info("ไม่มีข้อมูลที่ท่านมีสิทธิ์ดูในประเภทนี้")
                return
            ids = [o[0] for o in opts]
            default = ids.index(ctx.teacher_id) if kind == "teacher" and ctx.teacher_id in ids else 0
            key = c2.selectbox({"teacher": "เลือกผู้สอน", "group": "เลือกกลุ่มเรียน", "student": "เลือกนักเรียน", "room": "เลือกห้องเรียน",
                                "course": "เลือกรายวิชา", "department": "เลือกแผนกวิชา"}[kind], ids, index=default,
                               format_func=lambda i: dict(opts)[i], key=f"view_key_{kind}")
        else:
            c2.selectbox("ขอบเขต", ["ทั้งหมดที่ท่านมีสิทธิ์ดู"], disabled=True, key=f"view_all_{kind}")
        if len(tts) > 1 or ctx.role in (ADMIN, DEPT_ADMIN):
            ids_t = [t.id for t in tts]
            pub = next((t.id for t in tts if t.status == "published"), ids_t[0])
            tt_id = st.selectbox("ตารางฉบับ", ids_t, index=ids_t.index(pub), format_func=lambda i: tt_label(next(t for t in tts if t.id == i)), key="view_tt")
        else:
            tt_id = tts[0].id
    render_view(ctx, tt_id, kind, key)
    if ctx.role in (ADMIN, DEPT_ADMIN):
        with st.expander("ส่งออกหลายรายการพร้อมกัน", icon=":material/folder_zip:"):
            bk = st.radio("ส่งออก", ["teacher", "group", "room"], format_func={"teacher": "ตารางครูทุกคน", "group": "ตารางทุกกลุ่มเรียน", "room": "ตารางทุกห้อง"}.get,
                          horizontal=True, key="bulk_kind")
            if st.button("สร้างไฟล์", key="bulk_go"):
                with st.spinner("กำลังสร้างไฟล์…"):
                    with tx() as s:
                        tt = s.get(Timetable, tt_id)
                        vds = [get_view(s, ctx, tt, bk, k) for k, _ in options(s, ctx, tid, bk)]
                    xlsx = X.to_excel(vds)
                    zbio = io.BytesIO()
                    with zipfile.ZipFile(zbio, "w", zipfile.ZIP_DEFLATED) as z:
                        for v in vds:
                            z.writestr(_safe_name(v.title) + ".pdf", X.to_pdf(v))
                    st.session_state["bulk_files"] = (xlsx, zbio.getvalue(), bk)
            if "bulk_files" in st.session_state:
                xlsx, zbytes, bk_ = st.session_state["bulk_files"]
                c = st.columns(2)
                c[0].download_button("Excel (แยกแผ่นงาน)", xlsx, f"timetables_{bk_}.xlsx", key="bulk_x", icon=":material/table_view:")
                c[1].download_button("PDF ทั้งหมด (ZIP)", zbytes, f"timetables_{bk_}_pdf.zip", "application/zip", key="bulk_z", icon=":material/folder_zip:")


def my_page() -> None:
    ctx = require_ctx()
    tid = require_term()
    with tx() as s:
        pub = T.latest_published(s, tid)
        pub_id = pub.id if pub else None
    if ctx.role == STUDENT:
        banner("ตารางเรียนของฉัน", term_label(tid))
        if not ctx.student_id:
            st.error("บัญชีนี้ไม่ได้เชื่อมกับข้อมูลนักเรียน")
            return
        if not pub_id:
            st.info("ภาคเรียนนี้ยังไม่มีตารางที่เผยแพร่ — เลือกภาคเรียนก่อนหน้าได้ที่แถบด้านซ้าย")
            return
        render_view(ctx, pub_id, "student", ctx.student_id, key_prefix="me")
    elif ctx.teacher_id:
        banner("ตารางสอนของฉัน", term_label(tid))
        tts = timetable_options(ctx, tid)
        tt_id = pub_id
        if ctx.role in (ADMIN, DEPT_ADMIN) and tts:
            tt_id = _pick_timetable(ctx, tid, "my_tt")
        if not tt_id:
            st.info("ภาคเรียนนี้ยังไม่มีตารางที่เผยแพร่")
            return
        render_view(ctx, tt_id, "teacher", ctx.teacher_id, key_prefix="me")
        with tx() as s:
            asg = s.scalars(select(Assignment).where(Assignment.term_id == tid, Assignment.teacher_id == ctx.teacher_id)).all()
            rows = []
            for a in asg:
                c = s.get(Course, a.course_id)
                from ..models import ClassGroup

                g = s.get(ClassGroup, a.group_id)
                rows.append({"รหัสวิชา": c.code, "ชื่อวิชา": c.name, "กลุ่มเรียน": g.code, "ส่วนที่สอน": {"all": "ทั้งหมด", "theory": "ทฤษฎี", "practice": "ปฏิบัติ"}[a.part]})
        st.subheader("รายวิชาที่ได้รับมอบหมาย")
        q = st.text_input("ค้นหารายวิชา", key="my_q")
        df_show([r for r in rows if not q or q in r["รหัสวิชา"] or q in r["ชื่อวิชา"]])
    else:
        st.info("บัญชีนี้ไม่ได้เชื่อมกับข้อมูลครูหรือนักเรียน — ใช้เมนู “ดูตาราง” แทน")


def compare_page() -> None:
    ctx = require_ctx(DEPT_ADMIN)
    tid = require_term()
    banner("ค้นหาและเปรียบเทียบตารางครู", "เลือกครูหลายคนเพื่อดูรายวิชา กลุ่มเรียน ห้อง และเวลาว่างที่ตรงกัน")
    tt_id = _pick_timetable(ctx, tid, "cmp_tt", published_only=ctx.role == TEACHER)
    if tt_id is None:
        st.info("ยังไม่มีตาราง")
        return
    with tx() as s:
        opts = options(s, ctx, tid, "teacher")
        teachers = {t.id: t for t in s.scalars(select(Teacher)).all()}
        from ..models import Department

        deps = {d.id: d.name for d in s.scalars(select(Department)).all()}
        subj = {}
        for a in s.scalars(select(Assignment).where(Assignment.term_id == tid)).all():
            c = s.get(Course, a.course_id)
            subj.setdefault(a.teacher_id, set()).add(f"{c.code} {c.name}")
    q = st.text_input("ค้นหาด้วยชื่อครู รหัสครู แผนก หรือรายวิชา", key="cmp_q")
    dep_f = st.selectbox("กรองแผนก", [None] + list(deps), format_func=lambda i: "ทุกแผนก" if i is None else deps[i], key="cmp_dep")

    def match(tid_):
        t = teachers[tid_]
        hay = f"{t.code} {t.name} {deps.get(t.department_id, '')} {' '.join(subj.get(tid_, []))}"
        return (not q or q.lower() in hay.lower()) and (dep_f is None or t.department_id == dep_f)

    cands = [o for o in opts if match(o[0])]
    c1, c2 = st.columns([4, 1])
    if c2.button("เลือกทุกคนในแผนกที่กรอง", disabled=dep_f is None):
        st.session_state["cmp_sel"] = [o[0] for o in cands]
    sel = c1.multiselect("ครูที่ต้องการเปรียบเทียบ", [o[0] for o in cands], format_func=lambda i: dict(opts)[i], key="cmp_sel")
    if not sel:
        return
    with tx() as s:
        tt = s.get(Timetable, tt_id)
        vds = {t: get_view(s, ctx, tt, "teacher", t) for t in sel}
        ts = S.time_structure(s, tid)
        from .. import validator as V

        vr = V.validate_timetable(s, tt_id)
    st.subheader("สรุปการสอน")
    df_show([{"ครู": teachers[t].name, "รายวิชา": ", ".join(sorted({e.get('course_code') or '' for e in v.entries if e['entry_type'] == 'class'})),
              "กลุ่มเรียน": ", ".join(sorted({g for e in v.entries for g in (e.get('group_codes') or [])})),
              "ห้อง": ", ".join(sorted({e.get('room_label') or '' for e in v.entries if e.get('room_label')})),
              "คาบสอน/สัปดาห์": sum(e["end_period"] - e["start_period"] + 1 for e in v.entries if e["entry_type"] in ("class", "activity"))} for t, v in vds.items()])
    st.subheader("ช่วงเวลาว่างตรงกัน")
    busy = {t: {(e["day"], p) for e in v.entries for p in range(e["start_period"], e["end_period"] + 1)} for t, v in vds.items()}
    grid = []
    for d in ts.days:
        row = {"วัน": S.DAY_NAMES[d]}
        for p in ts.teaching_periods:
            free = [t for t in sel if (d, p) not in busy[t]]
            row[f"คาบ {p}"] = "ว่างทุกคน" if len(free) == len(sel) else (f"ว่าง {len(free)}/{len(sel)}" if free else "—")
        grid.append(row)
    df_show(grid)
    st.caption("การที่ครูหลายคนสอนเวลาเดียวกันไม่ถือว่าชนกัน (สอนคนละห้อง/คนละกลุ่มได้) — ส่วนด้านล่างแสดงเฉพาะความขัดแย้งตามข้อจำกัดจริง")
    real = [f for f in vr.errors if not f.incomplete and f.check in (3, 4, 5, 10, 21) and any(teachers[t].name in f.message for t in sel)]
    if real:
        st.error("พบความขัดแย้งตามข้อจำกัดจริงที่เกี่ยวข้องกับครูที่เลือก")
        for f in real:
            st.markdown(f"- {f.message}")
    else:
        st.success("ไม่พบรายการที่ชนกันตามข้อจำกัดจริงของครูที่เลือก")
    tabs = st.tabs([teachers[t].name for t in sel])
    for tab, t in zip(tabs, sel):
        with tab:
            render_view(ctx, tt_id, "teacher", t, key_prefix=f"cmp{t}")


def history_page() -> None:
    ctx = require_ctx()
    banner("ประวัติตาราง", "เรียกดูตารางย้อนหลังตามปีการศึกษาและภาคเรียน")
    terms = term_options()
    if not terms:
        st.info("ยังไม่มีข้อมูล")
        return
    tsel = st.selectbox("ปีการศึกษา / ภาคเรียน", [t[0] for t in terms], format_func=lambda i: dict((t[0], t[1]) for t in terms)[i], key="hist_term")
    tts = timetable_options(ctx, tsel)
    if ctx.role in (TEACHER, STUDENT):
        tts = [t for t in tts if t.status in ("published", "superseded")]
    if not tts:
        st.info("ไม่มีตารางในภาคเรียนนี้")
        return
    show_drafts = ctx.role in (ADMIN, DEPT_ADMIN) and st.toggle("แสดงฉบับร่างด้วย", value=False, key="hist_drafts")
    if not show_drafts:
        tts = [t for t in tts if t.status in ("published", "superseded", "approved")]
    if not tts:
        st.info("ไม่มีตารางที่เผยแพร่ในภาคเรียนนี้")
        return
    rows = [{"ฉบับที่": t.version, "สถานะ": T.STATUS_LABEL[t.status], "มีผลใช้จริง": "ใช่" if t.status == "published" else "",
             "เผยแพร่เมื่อ": t.published_at.strftime("%d/%m/%Y %H:%M") if t.published_at else "-", "อนุมัติโดย": t.approved_by or "-"} for t in tts]
    df_show(rows)
    ids = [t.id for t in tts]
    tt_id = st.selectbox("เลือกฉบับ", ids, format_func=lambda i: tt_label(next(t for t in tts if t.id == i)), key="hist_tt")
    kinds = allowed_kinds(ctx)
    kind = st.segmented_control("ประเภทตาราง", kinds, format_func=VIEW_KINDS.get, default=kinds[0], key="hist_kind") or kinds[0]
    key = None
    if kind in ("group", "teacher", "student", "room", "course", "department"):
        with tx() as s:
            opts = options(s, ctx, tsel, kind)
        if not opts:
            st.info("ไม่มีข้อมูลที่ท่านมีสิทธิ์ดู")
            return
        ids2 = [o[0] for o in opts]
        default = ids2.index(ctx.teacher_id) if kind == "teacher" and ctx.teacher_id in ids2 else (ids2.index(ctx.student_id) if kind == "student" and ctx.student_id in ids2 else 0)
        key = st.selectbox("เลือก", ids2, index=default, format_func=lambda i: dict(opts)[i], key=f"hist_key_{kind}")
    render_view(ctx, tt_id, kind, key, key_prefix="hist")


def _subject_rows(items: list[dict], base_key: str | None) -> list[dict]:
    rows = []
    for d in items:
        slots = sorted(d["slots"], key=lambda x: (x["day"], x["start"]))
        rows.append({
            "": "วิชาหลัก" if d["key"] == base_key else "",
            "หมวด": SUBJECT_CATS[d["cat"]], "รหัสวิชา": d["code"], "ชื่อวิชา": d["name"], "ลักษณะ": d["ctype"],
            "หน่วยกิต": "-" if d["credits"] is None else f"{d['credits']:g}", "ชม. ท-ป": d["hours"], "คาบ/สัปดาห์": d["periods"],
            "ครูผู้สอน": ", ".join(sorted(d["teachers"].values())) or "-", "กลุ่มเรียน": ", ".join(sorted(d["groups"])) or "-",
            "ห้อง": ", ".join(sorted(d["rooms"])) or "-",
            "วัน-เวลา": "; ".join(f"{S.DAY_NAMES[x['day']]} คาบ {x['start']}" + (f"-{x['end']}" if x["end"] != x["start"] else "") + f" ({x['time']})" for x in slots),
        })
    return rows


def _subject_grid_html(items: list[dict], base_key: str | None, ts) -> str:
    esc = html.escape
    cell: dict = {}
    for d in items:
        for x in d["slots"]:
            for p in range(x["start"], x["end"] + 1):
                cell.setdefault((x["day"], p), []).append(d)
    head = "".join(f"<th>คาบ {p}<br><small>{esc(ts.start_time(p))}</small></th>" for p in ts.teaching_periods)
    body = []
    for day in ts.days:
        tds = []
        for p in ts.teaching_periods:
            ds = cell.get((day, p), [])
            if not ds:
                tds.append("<td></td>")
                continue
            cls = "ov" if len({d["key"] for d in ds}) > 1 else ("base" if ds[0]["key"] == base_key else "has")
            inner = "".join(f'<div class="sj {d["cat"]}{" b" if d["key"] == base_key else ""}">{esc(d["code"] or d["name"][:14])}</div>' for d in ds)
            tds.append(f'<td class="{cls}">{inner}</td>')
        body.append(f"<tr><th class='d'>{esc(S.DAY_NAMES[day])}</th>{''.join(tds)}</tr>")
    css = """<style>
.nvc-sg{width:100%;border-collapse:separate;border-spacing:0;font-size:13px;border:1px solid #d6e2f5;border-radius:12px;overflow:hidden;background:#fff}
.nvc-sg th{background:#13307a;color:#fff;font-weight:600;padding:6px 4px;text-align:center}
.nvc-sg th small{color:#bcd0ff;font-weight:400}
.nvc-sg th.d{background:#0b1f4d;width:84px}
.nvc-sg td{border-top:1px solid #e6eefb;border-left:1px solid #e6eefb;padding:3px;vertical-align:top;height:40px;min-width:62px}
.nvc-sg td.ov{background:#fff4e5;box-shadow:inset 0 0 0 2px #f59e0b}
.nvc-sg td.base{background:#eaf2ff}
.nvc-sg .sj{border-radius:6px;padding:2px 5px;margin:1px 0;font-weight:600;font-size:12px;color:#0b1f4d;background:#e2ebff;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.nvc-sg .sj.general{background:#dcfce7;color:#14532d}
.nvc-sg .sj.activity{background:#f3e8ff;color:#581c87}
.nvc-sg .sj.b{outline:2px solid #1d4ed8}
.nvc-lg{display:flex;gap:14px;flex-wrap:wrap;font-size:12.5px;color:#475569;margin:6px 0 2px}
.nvc-lg span{display:inline-flex;align-items:center;gap:6px}
.nvc-lg i{width:14px;height:14px;border-radius:4px;display:inline-block}
</style>"""
    legend = ('<div class="nvc-lg"><span><i style="background:#dcfce7"></i>วิชาสามัญ</span><span><i style="background:#e2ebff"></i>วิชาเอก/วิชาชีพ</span>'
              '<span><i style="background:#f3e8ff"></i>กิจกรรม</span><span><i style="outline:2px solid #1d4ed8;background:#fff"></i>วิชาหลัก</span>'
              '<span><i style="background:#fff4e5;box-shadow:inset 0 0 0 2px #f59e0b"></i>อยู่ในเวลาเดียวกัน</span></div>')
    return f'{css}<div style="overflow-x:auto"><table class="nvc-sg"><tr><th class="d">วัน / คาบ</th>{head}</tr>{"".join(body)}</table></div>{legend}'


def _overlaps(items: list[dict], base_key: str | None) -> list[str]:
    """รายการวิชาที่อยู่ในเวลาเดียวกัน (เทียบกับวิชาหลัก หรือระหว่างวิชาที่เลือกถ้าไม่ได้เลือกวิชาหลัก)"""
    out = []
    pairs = []
    if base_key:
        b = next((d for d in items if d["key"] == base_key), None)
        pairs = [(b, d) for d in items if b and d["key"] != base_key]
    else:
        pairs = [(a, c) for i, a in enumerate(items) for c in items[i + 1:]]
    for a, c in pairs:
        for x in a["slots"]:
            for y in c["slots"]:
                if x["day"] == y["day"] and x["start"] <= y["end"] and y["start"] <= x["end"]:
                    lo, hi = max(x["start"], y["start"]), min(x["end"], y["end"])
                    shared = []
                    if set(x["teacher_ids"]) & set(y["teacher_ids"]):
                        shared.append("ครูคนเดียวกัน")
                    if set(x["group_codes"]) & set(y["group_codes"]):
                        shared.append("กลุ่มเรียนเดียวกัน")
                    if x["room"] and x["room"] == y["room"]:
                        shared.append("ห้องเดียวกัน")
                    note = (" — ใช้" + " / ".join(shared) + " (เรียนร่วมหรือต้องตรวจสอบ)") if shared else " — คนละครู คนละกลุ่ม คนละห้อง (ไม่ชนกัน)"
                    out.append(f"{a['code']} {a['name']} กับ {c['code']} {c['name']}: วัน{S.DAY_NAMES[x['day']]} คาบ {lo}" + (f"-{hi}" if hi != lo else "") + note)
    return out


def subjects_page() -> None:
    """เปรียบเทียบรายวิชา (บัญชีครู): เทียบวิชาของตนกับวิชาสามัญ วิชาเอก กิจกรรม หรือวิชาในตารางของอาจารย์ท่านอื่น"""
    ctx = require_ctx(TEACHER, ADMIN, DEPT_ADMIN)
    tid = require_term()
    banner("เปรียบเทียบรายวิชา", "เทียบวิชาของฉันกับวิชาสามัญ วิชาเอก กิจกรรม หรือวิชาในตารางของอาจารย์แต่ละท่าน")
    tt_id = _pick_timetable(ctx, tid, "sj_tt", published_only=ctx.role == TEACHER)
    if tt_id is None:
        st.info("ภาคเรียนนี้ยังไม่มีตารางที่เผยแพร่")
        return
    try:
        with tx() as s:
            cat = subject_catalog(s, ctx, s.get(Timetable, tt_id))
            ts = S.time_structure(s, tid)
            topts = options(s, ctx, tid, "teacher")
    except PermissionDenied as e:
        st.error(str(e), icon=":material/block:")
        return
    if not cat:
        empty_state("ยังไม่มีรายวิชาในตารางฉบับนี้", "inbox")
        return

    def lab(k: str) -> str:
        d = cat[k]
        return f"{d['code']} {d['name']}".strip() + f" · {SUBJECT_CATS[d['cat']]}"

    sort_k = lambda k: (list(SUBJECT_CATS).index(cat[k]["cat"]), cat[k]["code"], cat[k]["name"])  # noqa: E731
    mine = sorted([k for k, d in cat.items() if ctx.teacher_id and ctx.teacher_id in d["teachers"]], key=sort_k)
    with st.container(border=True):
        st.markdown("**1. วิชาหลักที่ต้องการเทียบ**")
        base_opts = [None] + (mine if mine else sorted(cat, key=sort_k))
        base = st.selectbox("วิชาของฉัน" if mine else "วิชาหลัก", base_opts, format_func=lambda k: "ไม่เลือก (เทียบเฉพาะวิชาที่เลือกในข้อ 2)" if k is None else lab(k),
                            index=1 if mine else 0, key="sj_base")
        st.markdown("**2. เทียบกับ**")
        mode = st.segmented_control("เทียบกับ", ["general", "major", "activity", "teacher"],
                                    format_func={"general": ":material/menu_book: วิชาสามัญ", "major": ":material/engineering: วิชาเอก/วิชาชีพ",
                                                 "activity": ":material/groups: กิจกรรม", "teacher": ":material/person_search: วิชาในตารางของอาจารย์"}.get,
                                    default="general", key="sj_mode", label_visibility="collapsed") or "general"
        if mode == "teacher":
            others = [o for o in topts if o[0] != ctx.teacher_id] or topts
            tsel = st.multiselect("เลือกอาจารย์ (เลือกได้หลายท่าน)", [o[0] for o in others], format_func=lambda i: dict(topts)[i], key="sj_teachers",
                                  placeholder="พิมพ์ชื่อหรือรหัสอาจารย์")
            pool = sorted([k for k, d in cat.items() if k != base and set(tsel) & set(d["teachers"])], key=sort_k)
            if tsel and not pool:
                st.info("อาจารย์ที่เลือกยังไม่มีรายวิชาในตารางฉบับนี้")
            fkey = "sj_pick_t_" + "_".join(map(str, sorted(tsel)))
            picked = st.multiselect("รายวิชาในตารางของอาจารย์ที่เลือก", pool, default=pool, format_func=lab, key=fkey) if pool else []
        else:
            pool = sorted([k for k, d in cat.items() if d["cat"] == mode and k != base], key=sort_k)
            if not pool:
                st.info(f"ไม่มี{SUBJECT_CATS[mode]}ในตารางฉบับนี้")
            picked = st.multiselect(f"เลือก{SUBJECT_CATS[mode]}ที่ต้องการเทียบ", pool, format_func=lab, key=f"sj_pick_{mode}",
                                    placeholder="พิมพ์รหัสหรือชื่อวิชา") if pool else []
    keys = ([base] if base else []) + [k for k in picked if k != base]
    if len(keys) < 2 and not (base is None and len(keys) == 1):
        st.caption("เลือกรายวิชาในข้อ 2 อย่างน้อย 1 วิชา เพื่อแสดงผลการเปรียบเทียบ")
        if not keys:
            return
    items = [cat[k] for k in keys]
    st.markdown("#### ข้อมูลรายวิชาเทียบกัน")
    df_show(_subject_rows(items, base))
    tot = {c: sum(d["periods"] for d in items if d["cat"] == c) for c in SUBJECT_CATS}
    m = st.columns(4)
    m[0].metric("จำนวนวิชาที่เทียบ", len(items))
    for i, c in enumerate(SUBJECT_CATS, start=1):
        m[i].metric(f"คาบ/สัปดาห์ · {SUBJECT_CATS[c]}", tot[c])
    st.markdown("#### ตำแหน่งในตารางสัปดาห์")
    st.markdown(_subject_grid_html(items, base, ts), unsafe_allow_html=True)
    ov = _overlaps(items, base)
    if ov:
        with st.expander(f"วิชาที่อยู่ในเวลาเดียวกัน ({len(ov)} รายการ)", expanded=True, icon=":material/schedule:"):
            for line in ov:
                st.markdown(f"- {html.escape(line)}")
    elif len(items) > 1:
        st.success("วิชาที่เลือกไม่มีช่วงเวลาซ้อนกัน", icon=":material/check_circle:")
