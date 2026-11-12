"""Audit harness: (1) the reference engine vs the auditor's independent base-R recomputation, cell by cell;
(2) deliberately wrong R programs against the validator. Runs against a local audit server; writes JSON evidence."""
import csv
import json
import sys
from pathlib import Path

import httpx

API = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8770"
E = Path(__file__).parent
c = httpx.Client(base_url=API, timeout=300)

w = c.post("/api/workspaces", json={"name": "audit · verification matrix"}).json()["id"]
c.post(f"/api/workspaces/{w}/example").raise_for_status()
base = c.get(f"/api/workspaces/{w}").json()["program"]["code"]


def run(code, label):
    c.post(f"/api/workspaces/{w}/programs", json={"code": code, "summary": label}).raise_for_status()
    r = c.post(f"/api/workspaces/{w}/run", json={}).json()
    tabs = (r.get("validation") or {}).get("tables") or []
    return r, tabs


# (1) reference vs independent recomputation
r, tabs = run(base, "example (baseline)")
full = c.get(f"/api/workspaces/{w}").json()["validation"]["tables"][0]["cells"]
ref = {(x["level"], x["soc"] or None, x["pt"] or None, x["group"]): (x["ref_n"], x["ref_N"]) for x in full}
ind = {}
for row in csv.DictReader(open(E / "independent_teae.csv")):
    k = (row["level"], row["soc"] if row["soc"] not in ("", "NA") else None, row["pt"] if row["pt"] not in ("", "NA") else None, row["group"])
    ind[k] = (int(row["n"]), int(row["N"]))
diff = [k for k in set(ref) | set(ind) if ref.get(k) != ind.get(k)]
prog_rows = {(x["level"], x["soc"] or None, x["pt"] or None, x["group"]): (x["n"], x["N"]) for x in full}
pct_rows = r["tables"][0]["rows"]
pct_bad = [x for x in pct_rows if abs(float(x["pct"]) - 100 * x["n"] / x["N"]) > 1e-9]
recon = {"baseline_validation": [(t["status"], t["passed"], t["mismatch"]) for t in tabs],
         "reference_cells": len(ref), "independent_cells": len(ind), "cells_differing": len(diff), "examples": diff[:5],
         "program_equals_independent": sum(1 for k in ind if prog_rows.get(k) == ind[k]),
         "example_program_pct_inconsistent_rows": len(pct_bad)}
print(json.dumps(recon, indent=1))

# (2) deliberately wrong programs. Each replaces text in the hand-written example program.
B = base
cases = {
    "A_denominator_from_AE_subjects": B.replace('left_join(denom, by = "group")',
        'left_join(ae %>% group_by(group) %>% summarise(N = n_distinct(USUBJID)), by = "group")'),
    "A2_denominator_whole_study_not_by_group": B.replace('denom <- adsl %>% count(group, name = "N")',
        'denom <- adsl %>% count(group, name = "N") %>% mutate(N = sum(N))'),
    "B_events_not_subjects_PT": B.replace('pt = AEDECOD) %>% summarise(n = n_distinct(USUBJID)', 'pt = AEDECOD) %>% summarise(n = n()'),
    "B2_events_not_subjects_all_levels": B.replace("n_distinct(USUBJID)", "n()"),
    "C_wrong_population_EFFFL": B.replace('filter(SAFFL == "Y")', 'filter(EFFFL == "Y")'),
    "C2_population_filter_removed": B.replace(' %>% filter(SAFFL == "Y")', ''),
    "D_no_TE_filter": B.replace('tp_read("ADAE") %>% filter(TRTEMFL == "Y") %>%', 'tp_read("ADAE") %>%'),
    "E_PT_from_AELLT": B.replace('pt = AEDECOD)', 'pt = AELLT)'),
    "E2_SOC_from_AEHLGT": B.replace('soc = AEBODSYS)', 'soc = AEHLGT)').replace('soc = AEBODSYS, pt', 'soc = AEHLGT, pt'),
    "F_omit_5_PT_rows": B.replace('tp_emit_table(teae,', 'teae <- teae[-which(teae$level == "pt")[1:5], ]\ntp_emit_table(teae,'),
    "F2_omit_any_level": B.replace('tp_emit_table(teae,', 'teae <- teae[teae$level != "any", ]\ntp_emit_table(teae,'),
    "F3_omit_one_group": B.replace('tp_emit_table(teae,', 'teae <- teae[teae$group != "Placebo", ]\ntp_emit_table(teae,'),
    "G_wrong_percentages": B.replace('mutate(pct = 100 * n / N)', 'mutate(pct = 10 * n / N)'),
    "G2_pct_column_dropped": B.replace(' %>% mutate(pct = 100 * n / N)', ''),
    "X1_extra_fabricated_row": B.replace('tp_emit_table(teae,', 'teae <- bind_rows(teae, data.frame(level="pt", soc="CARDIAC DISORDERS", pt="MADE UP TERM", group="Placebo", n=0L, N=86L, pct=0))\ntp_emit_table(teae,'),
    "X2_fabricated_row_nonzero": B.replace('tp_emit_table(teae,', 'teae <- bind_rows(teae, data.frame(level="pt", soc="CARDIAC DISORDERS", pt="MADE UP TERM", group="Placebo", n=3L, N=86L, pct=3.5))\ntp_emit_table(teae,'),
    "X3_duplicate_row": B.replace('tp_emit_table(teae,', 'teae <- bind_rows(teae, teae[1, ])\ntp_emit_table(teae,'),
    "X4_n_off_by_one_single_cell": B.replace('tp_emit_table(teae,', 'teae$n[teae$level == "pt"][1] <- teae$n[teae$level == "pt"][1] + 1L\ntp_emit_table(teae,'),
    "X5_not_teae_type": B.replace('type = "teae_summary"', 'type = "generic"'),
    "X6_n_as_text": B.replace('tp_emit_table(teae,', 'teae$n <- as.character(teae$n)\ntp_emit_table(teae,'),
}
results = {}
for name, code in cases.items():
    assert code != B, f"{name}: replacement did not apply"
    r, tabs = run(code, f"audit: {name}")
    t = tabs[0] if tabs else {}
    results[name] = {"run": r["status"], "error": (r.get("error") or {}).get("message") if isinstance(r.get("error"), dict) else r.get("error"),
                     "validation": t.get("status"), "passed": t.get("passed"), "mismatch": t.get("mismatch"),
                     "not_verified": t.get("not_verified"), "reason": t.get("reason"),
                     "pattern": (t.get("pattern") or [])[:3]}
    print(name, json.dumps(results[name])[:300])
json.dump({"workspace": w, "reconciliation": recon, "cases": results}, open(E / "verify_matrix.json", "w"), indent=1)
