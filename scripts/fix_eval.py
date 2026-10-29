"""Bug-fix evaluation against a live model: in fresh workspaces, a passing TEAE program gets one injected
bug (PT counts use n() instead of n_distinct(USUBJID)); the agent is asked to fix it. A trial succeeds only
if the latest run's independent validation passes with zero mismatches and the spec is unchanged.

  server/.venv/bin/python scripts/fix_eval.py [--trials 3] [--out fix_eval.json]
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

p = argparse.ArgumentParser()
p.add_argument("--api", default="http://127.0.0.1:8765")
p.add_argument("--trials", type=int, default=3)
p.add_argument("--timeout", type=int, default=420)
p.add_argument("--out", default="fix_eval.json")
a = p.parse_args()
c = httpx.Client(base_url=a.api, timeout=200)
h = c.get("/api/health").json()["llm"]
c.post("/api/settings/ack", json={"host": h["host"]})
BUG = ("by_pt <- ae %>% group_by(group, soc = AEBODSYS, pt = AEDECOD) %>% summarise(n = n_distinct(USUBJID)",
       "by_pt <- ae %>% group_by(group, soc = AEBODSYS, pt = AEDECOD) %>% summarise(n = n()")
results = []
for i in range(a.trials):
    w = c.post("/api/workspaces", json={"name": f"Fix eval {i + 1} · {time.strftime('%H:%M')}"}).json()["id"]
    c.post(f"/api/workspaces/{w}/datasets/pilot").raise_for_status()
    c.post(f"/api/workspaces/{w}/specs", json={"spec": SPEC, "rationale": "safety population"})
    c.post(f"/api/workspaces/{w}/programs", json={"code": TEAE_R})
    ok = c.post(f"/api/workspaces/{w}/run", json={}).json()["validation"]["tables"][0]
    c.post(f"/api/workspaces/{w}/programs", json={"code": TEAE_R.replace(*BUG), "summary": "injected bug"})
    bad = c.post(f"/api/workspaces/{w}/run", json={}).json()["validation"]["tables"][0]
    t0 = time.time()
    c.post(f"/api/workspaces/{w}/messages", json={"text": "Validation found mismatches. Investigate the cause, explain it, fix the program and rerun it. Do not change the specification."})
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
    r = {"workspace": w, "before": [ok["passed"], ok["mismatch"]], "injected": [bad["passed"], bad["mismatch"]],
         "after": [v.get("passed"), v.get("mismatch")], "run": s["run"]["status"], "program_version": s["program"]["version"],
         "program_author": s["program"]["author"], "spec_unchanged": approved == [1], "seconds": round(time.time() - t0), "fixed": fixed}
    results.append(r)
    print(json.dumps(r))
print(f"\nfixed {sum(r['fixed'] for r in results)}/{len(results)} with {h['model']}")
json.dump({"model": h["model"], "results": results}, open(a.out, "w"), indent=2)
