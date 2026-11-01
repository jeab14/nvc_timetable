"""ตัวจัดตารางอัตโนมัติด้วย OR-Tools CP-SAT

ข้อจำกัดบังคับ (hard) ทั้งหมดถูกสร้างเป็นข้อจำกัดของโมเดล — ไม่มีการผ่อนปรนเองเพื่อรายงานว่าสำเร็จ
ข้อจำกัดเพื่อคุณภาพ (soft) อยู่ในฟังก์ชันเป้าหมาย

ขั้นตอน:
1) โหมดเข้มงวด: ทุกช่วงคาบต้องถูกจัด (ใช้ assumption literal เพื่อหาสาเหตุเมื่อไม่มีคำตอบ)
2) ถ้าไม่มีคำตอบ: รายงานกลุ่มข้อจำกัดที่ขัดแย้ง แล้วจัด “บางส่วน” ให้ได้มากที่สุดโดยไม่ละเมิดข้อจำกัดบังคับ
"""
from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass, field

from ortools.sat.python import cp_model

from .problem import Lesson, Problem
from .settings_service import DAY_NAMES

MAX_COMBOS = 600_000
ROOM_TOPK = 8


@dataclass
class Placement:
    lesson_id: int
    block: int
    day: int
    start: int
    length: int
    room_id: int | None


@dataclass
class SolveResult:
    status: str  # success / partial / infeasible / timeout / error
    placements: list[Placement]
    unplaced: list[dict]
    report: dict = field(default_factory=dict)


def _blocked_for_lesson(pb: Problem, L: Lesson) -> set[tuple[int, int]]:
    """ช่องเวลาที่รายการสอนนี้ใช้ไม่ได้ (ครูไม่สะดวก ประชุม กิจกรรม ช่องที่กลุ่มถูกล็อก)"""
    bad: set[tuple[int, int]] = set()
    for t in L.teacher_ids:
        bad |= pb.teachers[t]["unavail"]
    for f in pb.fixed:
        cells = {(f.day, p) for p in f.periods}
        if set(L.teacher_ids) & set(f.teacher_ids) or set(L.group_ids) & set(f.group_ids):
            bad |= cells
    for g in L.group_ids:
        bad |= pb.group_forbidden.get(g, set())
    if L.kind == "homeroom":
        allowed = pb.settings["homeroom"].get("allowed_slots")
        if allowed:
            allowed_set = {(int(a), int(b)) for a, b in allowed}
            bad |= {sl for sl in pb.ts.slots() if sl not in allowed_set}
    return bad


def _room_order(pb: Problem, L: Lesson) -> list[int]:
    pref = set(L.preferred_dept_rooms)
    return sorted(L.room_ids, key=lambda r: (r not in pref, pb.rooms[r]["capacity"] or 0))


class _Model:
    def __init__(self, pb: Problem, partial: bool, hint: dict | None, limit_rooms: bool):
        self.pb = pb
        self._assume: list = []
        self.m = cp_model.CpModel()
        self.partial = partial
        self.z: dict[tuple, cp_model.IntVar] = {}  # (lid, k, d, s, r) -> var
        self.blocks: dict[tuple[int, int], list[tuple]] = defaultdict(list)  # (lid,k) -> keys
        self.placed: dict[tuple[int, int], cp_model.IntVar] = {}
        self.assumptions: dict[int, str] = {}  # literal index -> description
        self.empty_blocks: list[tuple[Lesson, int, str]] = []
        self.limited_rooms = False
        ts = pb.ts
        lessons = {L.id: L for L in pb.lessons}
        self.lessons = lessons
        cover_t = defaultdict(list)
        cover_g = defaultdict(list)
        cover_r = defaultdict(list)
        cover_l = defaultdict(list)  # (lid, d, p)
        day_load_g = defaultdict(list)  # (g, d) -> [(var, len)]
        day_load_t = defaultdict(list)
        week_load_t = defaultdict(list)
        count_teach_hr = bool(pb.settings["homeroom"].get("count_as_teaching"))

        est = sum(len(L.blocks) * len(ts.days) * len(ts.teaching_periods) * max(1, len(L.room_ids)) for L in pb.lessons)
        topk = None
        if limit_rooms and est > MAX_COMBOS:
            topk = ROOM_TOPK
            self.limited_rooms = True

        for L in pb.lessons:
            bad = _blocked_for_lesson(pb, L)
            rooms = _room_order(pb, L) if L.needs_room else [None]
            if topk and L.needs_room:
                rooms = rooms[:topk]
            for k, length in enumerate(L.blocks):
                keys = []
                for d in ts.days:
                    for s in ts.valid_starts(length):
                        cells = [(d, p) for p in range(s, s + length)]
                        if any(c in bad for c in cells):
                            continue
                        for r in rooms:
                            if r is not None and any(c in self.pb.rooms[r]["unavail"] for c in cells):
                                continue
                            v = self.m.NewBoolVar(f"z_{L.id}_{k}_{d}_{s}_{r}")
                            key = (L.id, k, d, s, r)
                            self.z[key] = v
                            keys.append(key)
                            for (dd, p) in cells:
                                for t in L.teacher_ids:
                                    cover_t[(t, dd, p)].append(v)
                                for g in L.group_ids:
                                    cover_g[(g, dd, p)].append(v)
                                if r is not None:
                                    cover_r[(r, dd, p)].append(v)
                                cover_l[(L.id, dd, p)].append(v)
                            for g in L.group_ids:
                                day_load_g[(g, d)].append((v, length))
                            if L.kind == "class" or count_teach_hr:
                                for t in L.teacher_ids:
                                    day_load_t[(t, d)].append((v, length))
                                    week_load_t[t].append((v, length))
                if not keys:
                    self.empty_blocks.append((L, k, self._why_empty(L, length, bad)))
                self.blocks[(L.id, k)] = keys
                lit = self.m.NewBoolVar(f"placed_{L.id}_{k}")
                self.placed[(L.id, k)] = lit
                if keys:
                    self.m.Add(sum(self.z[x] for x in keys) == 1).OnlyEnforceIf(lit)
                    self.m.Add(sum(self.z[x] for x in keys) == 0).OnlyEnforceIf(lit.Not())
                else:
                    self.m.Add(lit == 0)
                if not partial:
                    self.assumptions[lit.Index()] = f"ช่วงที่ {k + 1} ของ {L.label}"
            # สมมาตร: ช่วงที่ยาวเท่ากันของรายการเดียวกัน เรียงตามเวลา
            for k in range(len(L.blocks) - 1):
                if L.blocks[k] == L.blocks[k + 1]:
                    pos_a = sum(self.z[x] * (x[2] * 100 + x[3]) for x in self.blocks[(L.id, k)])
                    pos_b = sum(self.z[x] * (x[2] * 100 + x[3]) for x in self.blocks[(L.id, k + 1)])
                    if self.blocks[(L.id, k)] and self.blocks[(L.id, k + 1)]:
                        self.m.Add(pos_a < pos_b).OnlyEnforceIf([self.placed[(L.id, k)], self.placed[(L.id, k + 1)]])

        # ---------------------------------------------------- ข้อจำกัดบังคับ
        for _, vs in cover_t.items():
            if len(vs) > 1:
                self.m.AddAtMostOne(vs)  # ครูไม่สอนชนกัน
        for _, vs in cover_g.items():
            if len(vs) > 1:
                self.m.AddAtMostOne(vs)  # กลุ่มเรียนไม่ชนกัน
        for _, vs in cover_r.items():
            if len(vs) > 1:
                self.m.AddAtMostOne(vs)  # ห้องไม่ซ้ำซ้อน
        for _, vs in cover_l.items():
            if len(vs) > 1:
                self.m.AddAtMostOne(vs)  # ช่วงของรายการเดียวกันไม่ทับกัน
        # นักเรียนที่ลงทะเบียนข้ามกลุ่ม
        for sig, _cnt in pb.student_sets:
            for d in ts.days:
                for p in ts.teaching_periods:
                    vs = [v for lid in sig for v in cover_l.get((lid, d, p), [])]
                    if len(vs) > 1:
                        self.m.AddAtMostOne(vs)
            for d in ts.days:
                terms = []
                for lid in sig:
                    for k, _len in enumerate(lessons[lid].blocks):
                        for x in self.blocks[(lid, k)]:
                            if x[2] == d:
                                terms.append(self.z[x] * lessons[lid].blocks[k])
                fixed_n = sum(len(f.periods) for f in pb.fixed if f.day == d and set(f.group_ids) & set(g for lid in sig for g in lessons[lid].group_ids))
                if terms:
                    self.m.Add(sum(terms) + fixed_n <= ts.max_per_day)
        # จำนวนคาบต่อวันของกลุ่มเรียน
        for g in pb.groups:
            for d in ts.days:
                terms = day_load_g.get((g, d), [])
                fixed_n = sum(len(f.periods) for f in pb.fixed if f.day == d and g in f.group_ids)
                if terms:
                    lit = self.m.NewBoolVar(f"gday_{g}_{d}")
                    self.m.Add(sum(v * n for v, n in terms) + fixed_n <= ts.max_per_day).OnlyEnforceIf(lit)
                    if partial:
                        self.m.Add(lit == 1)
                    else:
                        self.assumptions[lit.Index()] = f"จำนวนคาบสูงสุดต่อวัน ({ts.max_per_day}) ของกลุ่ม {pb.groups[g]['code']} วัน{DAY_NAMES[d]}"
                        self._assume.append(lit)
        # ชั่วโมงสอนสูงสุดของครู
        for t, ti in pb.teachers.items():
            fixed_teach = sum(len(f.periods) for f in pb.fixed if f.kind == "activity" and t in f.teacher_ids) + int(ti.get("preload_week", 0))
            if ti["max_week"] is not None and week_load_t.get(t):
                lit = self.m.NewBoolVar(f"tweek_{t}")
                self.m.Add(sum(v * n for v, n in week_load_t[t]) + fixed_teach <= ti["max_week"]).OnlyEnforceIf(lit)
                if partial:
                    self.m.Add(lit == 1)
                else:
                    self.assumptions[lit.Index()] = f"ชั่วโมงสอนสูงสุด {ti['max_week']} คาบ/สัปดาห์ ของครู {ti['name']}"
                    self._assume.append(lit)
            if ti["max_day"]:
                for d in ts.days:
                    if day_load_t.get((t, d)):
                        fx = sum(len(f.periods) for f in pb.fixed if f.kind == "activity" and t in f.teacher_ids and f.day == d) + int((ti.get("preload_day") or {}).get(d, 0))
                        lit = self.m.NewBoolVar(f"tday_{t}_{d}")
                        self.m.Add(sum(v * n for v, n in day_load_t[(t, d)]) + fx <= ti["max_day"]).OnlyEnforceIf(lit)
                        if partial:
                            self.m.Add(lit == 1)
                        else:
                            self.assumptions[lit.Index()] = f"ชั่วโมงสอนสูงสุด {ti['max_day']} คาบ/วัน ของครู {ti['name']} วัน{DAY_NAMES[d]}"
                            self._assume.append(lit)

        # ---------------------------------------------------- เป้าหมายคุณภาพ (soft)
        w = pb.settings.get("soft_weights") or {}
        obj = []
        last = max(ts.teaching_periods) if ts.teaching_periods else 0
        for key, v in self.z.items():
            lid, k, d, s, r = key
            L = lessons[lid]
            end = s + L.blocks[k] - 1
            late = sum(1 for p in range(s, end + 1) if p >= last - 1)
            if late:
                obj.append(v * late * int(w.get("late_period", 1)))
            if r is not None and L.preferred_dept_rooms and r not in L.preferred_dept_rooms:
                obj.append(v * 1)
            for t in L.teacher_ids:
                pref = pb.teachers[t]["preferred"]
                if pref:
                    miss = sum(1 for p in range(s, end + 1) if (d, p) not in pref)
                    if miss:
                        obj.append(v * miss * int(w.get("preferred", 3)))
        # กระจายรายวิชาเดียวกันไม่ให้อยู่วันเดียวกันหลายช่วง (ต่อกลุ่ม)
        same = defaultdict(list)
        for key, v in self.z.items():
            lid, k, d, s, r = key
            L = lessons[lid]
            if L.kind == "class":
                for g in L.group_ids:
                    same[(g, L.course_id, d)].append(v)
        wr = int(w.get("same_day_repeat", 8))
        for (_g, _c, _d), vs in same.items():
            if len(vs) > 1:
                ex = self.m.NewIntVar(0, len(vs), "")
                self.m.Add(ex >= sum(vs) - 1)
                obj.append(ex * wr)
        # ลดช่วงว่างระหว่างคาบ (นับจำนวนช่วงเรียนต่อวัน)
        tp = ts.teaching_periods
        for ent, cover, weight in (("g", cover_g, int(w.get("group_gaps", 5))), ("t", cover_t, int(w.get("teacher_gaps", 2)))):
            if weight <= 0:
                continue
            ids = pb.groups if ent == "g" else pb.teachers
            for e in ids:
                for d in ts.days:
                    occ = []
                    for p in tp:
                        vs = cover.get((e, d, p), [])
                        const = 0
                        for f in pb.fixed:
                            if f.day == d and p in f.periods and ((ent == "g" and e in f.group_ids) or (ent == "t" and e in f.teacher_ids)):
                                const = 1
                        occ.append((sum(vs) if vs else 0) + const if (vs or const) else None)
                    if sum(1 for o in occ if o is not None) < 2:
                        continue
                    prev = 0
                    for o in occ:
                        if o is None:
                            prev = 0
                            continue
                        st = self.m.NewBoolVar("")
                        self.m.Add(st >= o - prev)
                        obj.append(st * weight)
                        prev = o
        # ความต่อเนื่องจากตารางเดิม
        if hint:
            ws = int(w.get("stability", 4))
            for key, v in self.z.items():
                lid, k, d, s, r = key
                h = hint.get((lid, k))
                if h:
                    same_slot = h[0] == d and h[1] == s
                    self.m.AddHint(v, 1 if (same_slot and h[2] == r) else 0)
                    if not same_slot:
                        obj.append(v * ws)
        if partial:
            big = 1000
            obj_placed = [self.placed[(L.id, k)] * (L.blocks[k] * big) for L in pb.lessons for k in range(len(L.blocks))]
            self.m.Maximize(sum(obj_placed) - sum(obj))
        else:
            if obj:
                self.m.Minimize(sum(obj))

    def _why_empty(self, L: Lesson, length: int, bad: set) -> str:
        ts = self.pb.ts
        starts = ts.valid_starts(length)
        if not starts:
            return f"ไม่มีช่วงเวลาต่อเนื่อง {length} คาบในโครงสร้างเวลา"
        free_time = [(d, s) for d in ts.days for s in starts if not any((d, p) in bad for p in range(s, s + length))]
        if not free_time:
            return "ครูผู้สอน/กลุ่มเรียนไม่มีช่วงเวลาว่างที่ต่อเนื่องพอ (เวลาไม่สะดวก ประชุม กิจกรรม)"
        return "ห้องที่เหมาะสมไม่ว่างในช่วงเวลาที่เป็นไปได้"


def _extract(model: _Model, solver: cp_model.CpSolver) -> list[Placement]:
    out = []
    for key, v in model.z.items():
        if solver.Value(v):
            lid, k, d, s, r = key
            out.append(Placement(lid, k, d, s, model.lessons[lid].blocks[k], r))
    return out


def solve(pb: Problem, time_limit: int = 60, hint: dict | None = None, allow_partial: bool = True, workers: int = 8) -> SolveResult:
    t0 = time.time()
    report: dict = {"lessons": len(pb.lessons), "blocks": sum(len(L.blocks) for L in pb.lessons),
                    "periods": sum(L.periods for L in pb.lessons), "fixed": len(pb.fixed)}
    if pb.has_errors:
        return SolveResult("error", [], [], {**report, "message": "ข้อมูลยังมีข้อผิดพลาด ต้องแก้ไขก่อนจัดตาราง",
                                            "issues": [i.message for i in pb.issues if i.level == "error"]})
    model = _Model(pb, partial=False, hint=hint, limit_rooms=True)
    report["variables"] = len(model.z)
    if model.limited_rooms:
        report["note"] = f"ข้อมูลมีขนาดใหญ่ ระบบพิจารณาห้องที่เหมาะสมที่สุด {ROOM_TOPK} ห้องต่อรายการ (อาจทำให้หาคำตอบไม่ได้ในบางกรณี)"
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = float(time_limit)
    solver.parameters.num_search_workers = workers
    assume = [model.placed[k] for k in model.placed] + list(model._assume)
    model.m.AddAssumptions(assume)
    st = solver.Solve(model.m)
    report["strict_status"] = solver.StatusName(st)
    report["seconds_strict"] = round(time.time() - t0, 2)
    if st in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        report["objective"] = solver.ObjectiveValue()
        report["optimal"] = st == cp_model.OPTIMAL
        return SolveResult("success", _extract(model, solver), [], report)

    conflicts: list[str] = []
    for L, k, why in model.empty_blocks:
        conflicts.append(f"{L.label} ช่วงที่ {k + 1} ({L.blocks[k]} คาบ): {why}")
    if st == cp_model.INFEASIBLE:
        try:
            core = solver.SufficientAssumptionsForInfeasibility()
            for idx in core[:40]:
                desc = model.assumptions.get(idx)
                if desc:
                    conflicts.append(f"ข้อจำกัดที่เกี่ยวข้อง: {desc}")
        except Exception:
            pass
    report["conflicts"] = conflicts
    if st == cp_model.UNKNOWN:
        report["message"] = f"ไม่พบคำตอบภายในเวลาที่กำหนด ({time_limit} วินาที) — ลองเพิ่มเวลาประมวลผลในหน้าตั้งค่า"
    else:
        report["message"] = "ไม่สามารถจัดตารางให้ครบทุกรายการภายใต้ข้อจำกัดบังคับได้"
    if not allow_partial:
        return SolveResult("timeout" if st == cp_model.UNKNOWN else "infeasible", [], [], report)

    # ------------------------------------------------ จัดบางส่วน (ไม่ละเมิดข้อจำกัดบังคับ)
    pm = _Model(pb, partial=True, hint=hint, limit_rooms=True)
    s2 = cp_model.CpSolver()
    s2.parameters.max_time_in_seconds = float(time_limit)
    s2.parameters.num_search_workers = workers
    st2 = s2.Solve(pm.m)
    report["partial_status"] = s2.StatusName(st2)
    report["seconds_total"] = round(time.time() - t0, 2)
    if st2 not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return SolveResult("timeout" if st == cp_model.UNKNOWN else "infeasible", [], [], report)
    placements = _extract(pm, s2)
    placed_keys = {(p.lesson_id, p.block) for p in placements}
    unplaced = []
    for L in pb.lessons:
        for k, n in enumerate(L.blocks):
            if (L.id, k) not in placed_keys:
                unplaced.append({"lesson_id": L.id, "label": L.label, "block": k + 1, "periods": n})
    return SolveResult("partial", placements, unplaced, report)
