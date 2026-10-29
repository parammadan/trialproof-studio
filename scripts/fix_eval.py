"""Bug-fix evaluation against a live model. In fresh workspaces a known-good TEAE program gets one injected
bug from BUGS and the agent is asked to fix it. A trial succeeds only if the latest run's independent
validation passes with zero mismatches and the approved spec is still v1.

  warm: the good program ran first, so a passing version exists to compare against
  cold: the buggy program is v1; there is no passing version to fall back on

  server/.venv/bin/python scripts/fix_eval.py [--bugs all|pt_events,...] [--modes warm,cold] [--trials 1]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import httpx

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / "server"), str(Path(__file__).resolve().parents[1] / "server" / "tests")]
from test_flow import SPEC, TEAE_R  # noqa: E402

# Each bug changes at least one validated cell on the PhUSE pilot data (checked by --dry).
BUGS = {
    "pt_events": ('pt = AEDECOD) %>% summarise(n = n_distinct(USUBJID)', 'pt = AEDECOD) %>% summarise(n = n()'),
    "soc_events": ('soc = AEBODSYS) %>% summarise(n = n_distinct(USUBJID)', 'soc = AEBODSYS) %>% summarise(n = n()'),
    "no_trtemfl": ('tp_read("ADAE") %>% filter(TRTEMFL == "Y") %>%', 'tp_read("ADAE") %>%'),
    "denom_events": ('left_join(denom, by = "group")',
                     'left_join(ae %>% group_by(group) %>% summarise(N = n_distinct(USUBJID)), by = "group")'),
    "efficacy_pop": ('filter(SAFFL == "Y")', 'filter(EFFFL == "Y")'),
    "pt_lowest_term": ('pt = AEDECOD)', 'pt = AELLT)'),
}
ASK = "Validation found mismatches. Investigate the cause, explain it, fix the program and rerun it. Do not change the specification."

p = argparse.ArgumentParser()
p.add_argument("--api", default="http://127.0.0.1:8765")
p.add_argument("--bugs", default="all")
p.add_argument("--modes", default="warm,cold")
p.add_argument("--trials", type=int, default=1)
p.add_argument("--timeout", type=int, default=420)
p.add_argument("--dry", action="store_true", help="only check that each bug is caught; no model")
p.add_argument("--out", default="fix_eval.json")
a = p.parse_args()
bugs = list(BUGS) if a.bugs == "all" else a.bugs.split(",")
c = httpx.Client(base_url=a.api, timeout=200)
h = c.get("/api/health").json()["llm"]
c.post("/api/settings/ack", json={"host": h["host"]})


def table(r):
    return (r.get("validation") or {}).get("tables", [{}])[0]


def trial(bug, mode, i):
    w = c.post("/api/workspaces", json={"name": f"Fix eval {bug} {mode} {i + 1} · {time.strftime('%H:%M')}"}).json()["id"]
    c.post(f"/api/workspaces/{w}/datasets/pilot").raise_for_status()
    c.post(f"/api/workspaces/{w}/specs", json={"spec": SPEC, "rationale": "safety population"})
    if mode == "warm":
        c.post(f"/api/workspaces/{w}/programs", json={"code": TEAE_R})
        assert table(c.post(f"/api/workspaces/{w}/run", json={}).json()).get("mismatch") == 0
    c.post(f"/api/workspaces/{w}/programs", json={"code": TEAE_R.replace(*BUGS[bug])})
    bad = table(c.post(f"/api/workspaces/{w}/run", json={}).json())
    r = {"bug": bug, "mode": mode, "workspace": w, "injected": [bad.get("passed"), bad.get("mismatch")]}
    if a.dry:
        return r | {"caught": bool(bad.get("mismatch"))}
    t0 = time.time()
    c.post(f"/api/workspaces/{w}/messages", json={"text": ASK})
    time.sleep(2)
    while c.get(f"/api/workspaces/{w}").json()["agent_busy"]:
        if time.time() - t0 > a.timeout:
            c.post(f"/api/workspaces/{w}/cancel")
            break
        time.sleep(3)
    s = c.get(f"/api/workspaces/{w}").json()
    v = (s["validation"] or {}).get("tables", [{}])[0]
    approved = [x["version"] for x in s["specs"] if x["status"] == "approved"]
    fixed = s["run"]["status"] == "succeeded" and v.get("status") == "passed" and approved == [1]
    return r | {"after": [v.get("passed"), v.get("mismatch")], "run": s["run"]["status"],
                "program_version": s["program"]["version"], "program_author": s["program"]["author"],
                "spec_unchanged": approved == [1], "seconds": round(time.time() - t0), "fixed": fixed}


results = []
for bug in bugs:
    for mode in a.modes.split(","):
        for i in range(a.trials):
            results.append(trial(bug, mode, i))
            print(json.dumps(results[-1]), flush=True)
key = "caught" if a.dry else "fixed"
print()
for mode in a.modes.split(","):
    rs = [r for r in results if r["mode"] == mode]
    print(f"{mode}: {key} {sum(r[key] for r in rs)}/{len(rs)}")
print(f"total: {key} {sum(r[key] for r in results)}/{len(results)} with {'no model (dry)' if a.dry else h['model']}")
json.dump({"model": None if a.dry else h["model"], "results": results}, open(a.out, "w"), indent=2)
