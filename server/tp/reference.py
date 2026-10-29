"""Independent reference engine and cell-level comparison.

The reference results are computed here, in plain Python over the CSV copies of the datasets, directly
from the approved analysis specification. None of it shares code with the R program under test, so an
error in the program (or in the model that wrote it) does not repeat itself in the reference.

Agreement only shows that the program computes what the specification says. It does not show that the
specification is the right analysis; that is the reviewer's call.
"""
from __future__ import annotations

import re
from collections import defaultdict

SPEC_TYPES = {"teae_summary"}
OPS = {"eq", "ne", "in", "not_in", "missing", "not_missing", "gt", "ge", "lt", "le"}
TOTAL = "Total"


class SpecError(ValueError):
    pass


def check_spec(spec: dict, columns: dict[str, set[str]]) -> list[str]:
    """Return a list of problems with a spec, given each dataset's column names."""
    probs = []
    if spec.get("type") not in SPEC_TYPES:
        return [f"type must be one of {sorted(SPEC_TYPES)}"]
    ds = spec.get("datasets") or {}
    subj, ev = ds.get("subject"), ds.get("events")
    for role, name in (("subject", subj), ("events", ev)):
        if name not in columns:
            probs.append(f"datasets.{role}: no dataset named {name!r}")
    if probs:
        return probs
    sid = spec.get("subject_id", "USUBJID")
    for name in (subj, ev):
        if sid not in columns[name]:
            probs.append(f"{name} has no subject id column {sid}")
    for key, name in (("treatment_var", subj), ("soc_var", ev), ("pt_var", ev)):
        if spec.get(key) not in columns[name]:
            probs.append(f"{key}: {name} has no column {spec.get(key)!r}")
    for key, name in (("population", subj), ("event_filter", ev)):
        for c in spec.get(key) or []:
            if c.get("var") not in columns[name]:
                probs.append(f"{key}: {name} has no column {c.get('var')!r}")
            if c.get("op") not in OPS:
                probs.append(f"{key}: unknown op {c.get('op')!r} (use {sorted(OPS)})")
    return probs


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _holds(row: dict, cond: dict) -> bool:
    v = (row.get(cond["var"]) or "").strip()
    op, want = cond["op"], cond.get("value")
    if op == "missing":
        return v == ""
    if op == "not_missing":
        return v != ""
    if op == "eq":
        return v == str(want)
    if op == "ne":
        return v != str(want)
    if op == "in":
        return v in {str(w) for w in want}
    if op == "not_in":
        return v not in {str(w) for w in want}
    a, b = _num(v), _num(want)
    if a is None or b is None:
        return False
    return {"gt": a > b, "ge": a >= b, "lt": a < b, "le": a <= b}[op]


def describe(cond: dict) -> str:
    op, v = cond["op"], cond.get("value")
    sym = {"eq": "==", "ne": "!=", "gt": ">", "ge": ">=", "lt": "<", "le": "<="}
    if op in sym:
        return f'{cond["var"]} {sym[op]} "{v}"'
    if op in ("in", "not_in"):
        return f'{cond["var"]} {"in" if op == "in" else "not in"} {list(v)}'
    return f'{cond["var"]} is {"missing" if op == "missing" else "not missing"}'


def teae_reference(spec: dict, subjects: list[dict], events: list[dict]) -> dict:
    """Subject counts with at least one qualifying event: overall, by SOC and by SOC/PT, per group.
    Returns cells keyed by (level, soc, pt, group) holding n, N and the contributing subject ids."""
    sid = spec.get("subject_id", "USUBJID")
    trt, soc_v, pt_v = spec["treatment_var"], spec["soc_var"], spec["pt_var"]
    pop = [r for r in subjects if all(_holds(r, c) for c in spec.get("population") or [])]
    arm = {r[sid]: (r.get(trt) or "").strip() for r in pop}
    groups = sorted({a for a in arm.values() if a})
    with_total = bool(spec.get("include_total"))
    if with_total:
        groups.append(TOTAL)
    denom = defaultdict(set)
    for s, a in arm.items():
        if a:
            denom[a].add(s)
            if with_total:
                denom[TOTAL].add(s)
    hits: dict[tuple, set] = defaultdict(set)
    records: dict[tuple, list] = defaultdict(list)
    used = 0
    for i, e in enumerate(events):
        s = e.get(sid)
        if s not in arm or not arm[s] or not all(_holds(e, c) for c in spec.get("event_filter") or []):
            continue
        used += 1
        soc, pt = (e.get(soc_v) or "").strip(), (e.get(pt_v) or "").strip()
        for g in ([arm[s], TOTAL] if with_total else [arm[s]]):
            for key in (("any", None, None, g), ("soc", soc, None, g), ("pt", soc, pt, g)):
                hits[key].add(s)
                records[key].append(i)
    cats = {(k[0], k[1], k[2]) for k in hits}
    cells = {}
    for lvl, soc, pt in cats | {("any", None, None)}:
        for g in groups:
            key = (lvl, soc, pt, g)
            cells[key] = {"n": len(hits.get(key, ())), "N": len(denom[g]),
                          "subjects": sorted(hits.get(key, ())), "records": records.get(key, [])}
    return {"groups": groups, "cells": cells, "population_n": len(pop), "events_used": used,
            "subjects_without_group": sum(1 for a in arm.values() if not a)}


def _norm(v):
    if v is None:
        return None
    s = str(v).strip()
    return None if s in ("", "NA") else s


def _int(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return int(f) if f.is_integer() else f


def compare(ref: dict, table: dict) -> dict:
    """Cell-level comparison of a program's teae_summary table with the reference."""
    out_cells = []
    seen = set()
    for row in table.get("rows", []):
        lvl = _norm(row.get("level"))
        soc = _norm(row.get("soc")) if lvl in ("soc", "pt") else None
        pt = _norm(row.get("pt")) if lvl == "pt" else None
        key = (lvl, soc, pt, _norm(row.get("group")))
        n, N = _int(row.get("n")), _int(row.get("N"))
        r = ref["cells"].get(key)
        cell = {"level": lvl, "soc": soc, "pt": pt, "group": key[3], "n": n, "N": N}
        if key in seen:
            cell.update(status="mismatch", reason="duplicate row in program output")
        elif key[3] not in ref["groups"]:
            cell.update(status="mismatch", ref_n=None, ref_N=None,
                        reason=f"group {key[3]!r} is not in the reference population")
        elif r is None:
            gN = ref["cells"][("any", None, None, key[3])]["N"]
            ok = (n or 0) == 0 and N == gN
            cell.update(status="passed" if ok else "mismatch", ref_n=0, ref_N=gN,
                        reason=None if ok else "no qualifying events for this category in the reference")
        else:
            ok = n == r["n"] and N == r["N"]
            cell.update(status="passed" if ok else "mismatch", ref_n=r["n"], ref_N=r["N"],
                        reason=None if ok else ", ".join(
                            p for p, bad in (("numerator differs", n != r["n"]), ("denominator differs", N != r["N"])) if bad))
        seen.add(key)
        out_cells.append(cell)
    for key, r in ref["cells"].items():
        if key in seen:
            continue
        lvl, soc, pt, g = key
        if r["n"] == 0:
            continue  # an empty category the program need not print
        out_cells.append({"level": lvl, "soc": soc, "pt": pt, "group": g, "n": None, "N": None, "ref_n": r["n"],
                          "ref_N": r["N"], "status": "mismatch", "reason": "missing from program output"})
    counts = defaultdict(int)
    for c in out_cells:
        counts[c["status"]] += 1
    return {"cells": out_cells, "passed": counts["passed"], "mismatch": counts["mismatch"],
            "not_verified": counts["not_verified"], "groups": ref["groups"], "population_n": ref["population_n"],
            "pattern": pattern(out_cells)}


def pattern(cells: list[dict]) -> list[str]:
    """Plain facts about where and how the mismatches occur (no guesses about the cause)."""
    bad = [c for c in cells if c["status"] == "mismatch"]
    if not bad:
        return []
    facts = []
    by_level = defaultdict(int)
    for c in bad:
        by_level[c["level"]] += 1
    facts.append(f"{len(bad)} mismatched cells: " + ", ".join(f"{n} at level {lv!r}" for lv, n in sorted(by_level.items())))
    both = [c for c in bad if c.get("n") is not None and c.get("ref_n") is not None]
    if both:
        hi = sum(1 for c in both if c["n"] > c["ref_n"])
        lo = sum(1 for c in both if c["n"] < c["ref_n"])
        facts.append(f"numerator: program n > reference n in {hi}, program n < reference n in {lo}, of {len(both)} comparable cells")
        dN = sum(1 for c in both if c.get("N") != c.get("ref_N"))
        facts.append("denominators (N) agree in every mismatched cell" if dN == 0 else f"denominator N differs in {dN} cells")
    missing = sum(1 for c in bad if c.get("n") is None)
    extra = sum(1 for c in bad if c.get("ref_N") is None)
    if missing:
        facts.append(f"{missing} reference cells with events are missing from the program output")
    if extra:
        facts.append(f"{extra} program cells have a group that is not in the reference population")
    return facts


def code_lines(code: str, spec: dict) -> list[dict]:
    """Lines of the program that mention the spec's variables (a static text match, not a trace)."""
    terms = {spec.get("treatment_var"), spec.get("soc_var"), spec.get("pt_var"), spec.get("subject_id", "USUBJID")}
    terms |= {c["var"] for c in (spec.get("population") or []) + (spec.get("event_filter") or [])}
    terms.discard(None)
    pat = re.compile(r"\b(" + "|".join(map(re.escape, sorted(terms))) + r"|n_distinct|distinct|tp_emit_table)\b")
    return [{"line": i + 1, "text": ln, "terms": sorted(set(pat.findall(ln)))}
            for i, ln in enumerate(code.splitlines()) if pat.search(ln)]
