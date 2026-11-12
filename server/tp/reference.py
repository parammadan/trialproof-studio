"""Independent reference engine and cell-level comparison.

The reference results are computed here, in plain Python over the CSV copies of the datasets, directly
from the approved analysis specification. None of it shares code with the R program under test, so an
error in the program (or in the model that wrote it) does not repeat itself in the reference.

Agreement only shows that the program computes what the specification says. It does not show that the
specification is the right analysis; that is the reviewer's call.
"""
from __future__ import annotations

import math
import re
from collections import defaultdict

SPEC_TYPES = {"teae_summary"}
OPS = {"eq", "ne", "in", "not_in", "missing", "not_missing", "gt", "ge", "lt", "le"}
TOTAL = "Total"
UNCODED_HANDLING = {"label", "exclude"}
ZERO_CELLS = {"optional", "required"}
# Specs approved before these options existed (stored without them) keep the behaviour they were approved with:
# a percentage column is checked when present but not required, and zero-count cells are optional.
LEGACY_PERCENT = {"required": False, "decimals": 1}


def normalize_spec(spec: dict) -> dict:
    """Defaults written into a NEW proposal, so the approved spec records every output rule it is checked against."""
    out = dict(spec)
    out.setdefault("percent", {"required": True, "decimals": 1})
    out.setdefault("zero_cells", "optional")
    return out


def percent_rule(spec: dict) -> dict:
    return {**LEGACY_PERCENT, **(spec.get("percent") or {})} if "percent" in spec else dict(LEGACY_PERCENT)


def pct_tolerance(decimals: int) -> float:
    """Half a unit in the last reported decimal (a value rounded to `decimals` places is accepted), plus float slack."""
    return 0.5 * 10 ** -decimals + 1e-9


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
    pr = spec.get("percent")
    if pr is not None:
        if not isinstance(pr, dict) or not isinstance(pr.get("required", True), bool):
            probs.append('percent must be an object like {"required": true, "decimals": 1}')
        elif not (isinstance(pr.get("decimals", 1), int) and not isinstance(pr.get("decimals", 1), bool) and 0 <= pr.get("decimals", 1) <= 4):
            probs.append("percent.decimals must be an integer from 0 to 4")
    if spec.get("zero_cells", "optional") not in ZERO_CELLS:
        probs.append(f"zero_cells must be one of {sorted(ZERO_CELLS)}")
    unc = spec.get("uncoded")
    if unc is not None:
        if not isinstance(unc, dict) or unc.get("handling") not in UNCODED_HANDLING:
            probs.append('uncoded must be {"handling": "label", "label": "..."} or {"handling": "exclude"}')
        elif unc["handling"] == "label" and not str(unc.get("label") or "").strip():
            probs.append("uncoded.label must be a non-empty text when handling is 'label'")
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


def _blank(v) -> bool:
    return v is None or str(v).strip() in ("", "NA")


def uncoded_records(spec: dict, subjects: list[dict], events: list[dict]) -> int:
    """Qualifying event records (in the population, with a group, passing the event filter) whose SOC or PT is blank."""
    sid = spec.get("subject_id", "USUBJID")
    pop = {r[sid] for r in subjects if all(_holds(r, c) for c in spec.get("population") or [])
           and (r.get(spec["treatment_var"]) or "").strip()}
    return sum(1 for e in events if e.get(sid) in pop and all(_holds(e, c) for c in spec.get("event_filter") or [])
               and (_blank(e.get(spec["soc_var"])) or _blank(e.get(spec["pt_var"]))))


def teae_reference(spec: dict, subjects: list[dict], events: list[dict]) -> dict:
    """Subject counts with at least one qualifying event: overall, by SOC and by SOC/PT, per group.
    Returns cells keyed by (level, soc, pt, group) holding n, N, the reference percentage and the contributing
    subject ids. Blank SOC/PT values follow the spec's `uncoded` rule: "label" counts them under that label,
    "exclude" drops those records from every level; with no rule they stay blank (and cannot be matched)."""
    sid = spec.get("subject_id", "USUBJID")
    trt, soc_v, pt_v = spec["treatment_var"], spec["soc_var"], spec["pt_var"]
    unc = spec.get("uncoded") or {}
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
    used = uncoded = 0
    for i, e in enumerate(events):
        s = e.get(sid)
        if s not in arm or not arm[s] or not all(_holds(e, c) for c in spec.get("event_filter") or []):
            continue
        soc, pt = (e.get(soc_v) or "").strip(), (e.get(pt_v) or "").strip()
        soc, pt = ("" if soc == "NA" else soc), ("" if pt == "NA" else pt)
        if not soc or not pt:
            uncoded += 1
            if unc.get("handling") == "exclude":
                continue
            if unc.get("handling") == "label":
                soc, pt = soc or unc["label"].strip(), pt or unc["label"].strip()
        used += 1
        for g in ([arm[s], TOTAL] if with_total else [arm[s]]):
            for key in (("any", None, None, g), ("soc", soc, None, g), ("pt", soc, pt, g)):
                hits[key].add(s)
                records[key].append(i)
    cats = {(k[0], k[1], k[2]) for k in hits}
    cells = {}
    for lvl, soc, pt in cats | {("any", None, None)}:
        for g in groups:
            key = (lvl, soc, pt, g)
            n, N = len(hits.get(key, ())), len(denom[g])
            cells[key] = {"n": n, "N": N, "pct": 100 * n / N if N else None,
                          "subjects": sorted(hits.get(key, ())), "records": records.get(key, [])}
    return {"groups": groups, "cells": cells, "population_n": len(pop), "events_used": used,
            "uncoded_records": uncoded, "uncoded_rule": unc.get("handling"),
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
    if not math.isfinite(f):
        return None
    return int(f) if f.is_integer() else f


def _pct(v):
    """(value, problem): a finite number in [0, 100], None when missing, or a problem description."""
    if v is None or (isinstance(v, str) and v.strip() in ("", "NA")):
        return None, None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None, f"invalid percentage {v!r}"
    if not math.isfinite(f) or f < 0 or f > 100:
        return None, f"invalid percentage {v!r}"
    return f, None


def compare(ref: dict, table: dict, spec: dict | None = None) -> dict:
    """Cell-level comparison of a program's teae_summary table with the reference computed from `spec`.

    A cell passes only if its row is a category the reference has (a SOC/PT seen among qualifying events in some
    group, under that SOC), for a reference group, emitted once, with the reference n and N, and, when the table
    has a percentage, a valid one within the spec's tolerance of the reference 100·n/N. Each mismatch carries a
    `code`: n, N, pct, pct_missing, pct_invalid, duplicate, extra_category, extra_group, missing."""
    spec = spec or {}
    pr = percent_rule(spec)
    tol = pct_tolerance(int(pr.get("decimals", 1)))
    has_pct = "pct" in (table.get("columns") or []) or any("pct" in r for r in table.get("rows", []))
    zero_required = spec.get("zero_cells") == "required"
    pts_by_soc = defaultdict(set)
    for (lvl, soc, pt, _g) in ref["cells"]:
        if lvl == "pt":
            pts_by_soc[pt].add(soc)
    out_cells = []
    seen = set()

    def bad(cell, code, reason, **extra):
        cell.update(status="mismatch", code=code, reason=reason, **extra)

    for row in table.get("rows", []):
        lvl = _norm(row.get("level"))
        soc = _norm(row.get("soc")) if lvl in ("soc", "pt") else None
        pt = _norm(row.get("pt")) if lvl == "pt" else None
        key = (lvl, soc, pt, _norm(row.get("group")))
        n, N = _int(row.get("n")), _int(row.get("N"))
        pv, pproblem = _pct(row.get("pct")) if has_pct else (None, None)
        r = ref["cells"].get(key)
        cell = {"level": lvl, "soc": soc, "pt": pt, "group": key[3], "n": n, "N": N, "pct": row.get("pct") if has_pct else None,
                "ref_n": r["n"] if r else None, "ref_N": r["N"] if r else None, "ref_pct": r["pct"] if r else None}
        if key in seen:
            bad(cell, "duplicate", "duplicate row for this category and group")
        elif key[3] not in ref["groups"]:
            bad(cell, "extra_group", f"group {key[3]!r} is not in the reference population")
        elif r is None:
            where = (f"; the reference has {pt!r} under {sorted(pts_by_soc[pt])[0]!r}" if lvl == "pt" and pts_by_soc.get(pt) else "")
            bad(cell, "extra_category", "category not in the reference: no qualifying events in any group" + where)
        elif n != r["n"] or N != r["N"]:
            bad(cell, "n" if n != r["n"] else "N", ", ".join(
                p for p, b in (("numerator differs", n != r["n"]), ("denominator differs", N != r["N"])) if b))
        elif has_pct and pproblem:
            bad(cell, "pct_invalid", pproblem)
        elif r["N"] == 0 and pv is not None:
            bad(cell, "pct_invalid", "percentage given for a zero denominator")
        elif has_pct and pv is None and pr.get("required") and r["N"]:
            bad(cell, "pct_missing", "percentage missing (required by the specification)")
        elif not has_pct and pr.get("required"):
            bad(cell, "pct_missing", "the table has no pct column (required by the specification)")
        elif pv is not None and r["pct"] is not None and abs(pv - r["pct"]) > tol:
            bad(cell, "pct", f"percentage differs: program {pv:g}, reference {r['pct']:.{max(2, int(pr.get('decimals', 1)) + 1)}f} (tolerance ±{tol:.3g})")
        else:
            cell.update(status="passed", code=None, reason=None)
        seen.add(key)
        out_cells.append(cell)
    for key, r in ref["cells"].items():
        if key in seen:
            continue
        lvl, soc, pt, g = key
        if r["n"] == 0 and not zero_required:
            continue  # an empty cell the approved output does not require
        out_cells.append({"level": lvl, "soc": soc, "pt": pt, "group": g, "n": None, "N": None, "pct": None,
                          "ref_n": r["n"], "ref_N": r["N"], "ref_pct": r["pct"], "status": "mismatch", "code": "missing",
                          "reason": "missing from program output" + (" (zero-count cells are required by the specification)" if r["n"] == 0 else "")})
    counts = defaultdict(int)
    for c in out_cells:
        counts[c["status"]] += 1
    return {"cells": out_cells, "passed": counts["passed"], "mismatch": counts["mismatch"],
            "not_verified": counts["not_verified"], "groups": ref["groups"], "population_n": ref["population_n"],
            "percent_rule": {**pr, "tolerance": tol}, "pct_present": has_pct,
            "uncoded_records": ref.get("uncoded_records", 0), "uncoded_rule": ref.get("uncoded_rule"),
            "pattern": pattern(out_cells, ref)}


def pattern(cells: list[dict], ref: dict | None = None) -> list[str]:
    """Plain facts about where and how the mismatches occur (no guesses about the cause)."""
    bad = [c for c in cells if c["status"] == "mismatch"]
    if not bad:
        return []
    facts = []
    by_level = defaultdict(int)
    for c in bad:
        by_level[c["level"]] += 1
    facts.append(f"{len(bad)} mismatched cells: " + ", ".join(f"{n} at level {lv!r}" for lv, n in sorted(by_level.items(), key=lambda x: str(x[0]))))
    codes = defaultdict(int)
    for c in bad:
        codes[c.get("code")] += 1
    both = [c for c in bad if c.get("code") in ("n", "N")]
    if both:
        hi = sum(1 for c in both if (c["n"] or 0) > (c["ref_n"] or 0))
        lo = sum(1 for c in both if (c["n"] or 0) < (c["ref_n"] or 0))
        facts.append(f"numerator: program n > reference n in {hi}, program n < reference n in {lo}, of {len(both)} comparable cells")
        dN = sum(1 for c in both if c.get("N") != c.get("ref_N"))
        facts.append("denominators (N) agree in every mismatched cell" if dN == 0 else f"denominator N differs in {dN} cells")
    words = {"pct": "cells have a percentage that differs from 100·n/N of the reference",
             "pct_missing": "cells are missing a percentage the specification requires",
             "pct_invalid": "cells have an invalid percentage (not a number, outside 0–100, or given for N = 0)",
             "missing": "reference cells are missing from the program output",
             "extra_category": "program rows are categories the reference does not have (invented, or a PT under the wrong SOC)",
             "extra_group": "program cells have a group that is not in the reference population",
             "duplicate": "program rows are duplicates of an earlier row"}
    for code, text in words.items():
        if codes.get(code):
            facts.append(f"{codes[code]} {text}")
    if ref and ref.get("uncoded_records") and not ref.get("uncoded_rule"):
        facts.append(f"{ref['uncoded_records']} qualifying event records have a blank SOC or PT and the specification has no "
                     "uncoded rule, so those categories cannot be matched; a new spec version must say how to treat them")
    return facts


def spec_coverage(code: str, spec: dict, columns: dict[str, set[str]]) -> list[str]:
    """Static text match of the program against the spec: spec variables the code never names, and columns of
    the spec's datasets the code names that the spec does not use. A fact about the text, not a diagnosis."""
    ds = spec.get("datasets") or {}
    roles = [("subject id", spec.get("subject_id", "USUBJID")), ("treatment variable", spec.get("treatment_var")),
             ("SOC variable", spec.get("soc_var")), ("PT variable", spec.get("pt_var"))]
    roles += [("population condition " + describe(c), c["var"]) for c in spec.get("population") or []]
    roles += [("event filter " + describe(c), c["var"]) for c in spec.get("event_filter") or []]
    words = set(re.findall(r"[A-Za-z_][A-Za-z0-9_.]*", re.sub(r"#.*", "", code)))
    facts = [f"the spec's {role} uses {var}, which the program never mentions" for role, var in roles if var and var not in words]
    used = {v for _, v in roles if v}
    for name in (ds.get("subject"), ds.get("events")):
        extra = sorted((columns.get(name, set()) & words) - used)
        if extra:
            facts.append(f"the program mentions {name} column(s) {', '.join(extra)}, which the spec does not use")
    return facts


def code_lines(code: str, spec: dict) -> list[dict]:
    """Lines of the program that mention the spec's variables (a static text match, not a trace)."""
    terms = {spec.get("treatment_var"), spec.get("soc_var"), spec.get("pt_var"), spec.get("subject_id", "USUBJID")}
    terms |= {c["var"] for c in (spec.get("population") or []) + (spec.get("event_filter") or [])}
    terms.discard(None)
    pat = re.compile(r"\b(" + "|".join(map(re.escape, sorted(terms))) + r"|n_distinct|distinct|tp_emit_table)\b")
    return [{"line": i + 1, "text": ln, "terms": sorted(set(pat.findall(ln)))}
            for i, ln in enumerate(code.splitlines()) if pat.search(ln)]
