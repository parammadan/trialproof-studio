"""Milestone 3 production check for uploads, through the public API only (no model calls): files are parsed in the
sandbox, an unfamiliar compatible layout is verified independently, limits hold, and other sessions cannot see them.

  python scripts/upload_acceptance.py [--api https://trialproof-studio.fly.dev] [--out log.json]
"""
import argparse
import json
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "server"), str(ROOT / "server" / "tests")]
from test_verification import EXCLUDE, LABEL, SYN_R, SYN_SPEC  # noqa: E402  (the hand-written program and spec)

ap = argparse.ArgumentParser()
ap.add_argument("--api", default="https://trialproof-studio.fly.dev")
ap.add_argument("--out", default="upload_acceptance.json")
a = ap.parse_args()
c = httpx.Client(base_url=a.api, timeout=180)
results = []


def check(name, ok, detail=""):
    results.append({"check": name, "pass": bool(ok), "detail": detail})
    print(("PASS " if ok else "FAIL ") + name + (f"  ({detail})" if detail else ""), flush=True)


fx = ROOT / "server" / "tests" / "fixtures" / "synthetic"
wid = c.post("/api/workspaces", json={"name": "acceptance · uploads"}).json()["id"]
r = c.post(f"/api/workspaces/{wid}/datasets", files=[("files", ("dm.csv", (fx / "dm.csv").read_bytes())),
                                                   ("files", ("aev.csv", (fx / "aev.csv").read_bytes()))])
ds = r.json().get("datasets", [])
check("uploads accepted in public mode and parsed in the sandbox", r.status_code == 200 and [d["name"] for d in ds] == ["DM", "AEV"],
      r.text[:200])
r = c.post(f"/api/workspaces/{wid}/specs", json={"spec": SYN_SPEC, "rationale": "no uncoded rule"})
check("a spec over data with blank SOC/PT is refused without an uncoded rule", r.status_code >= 400 or not r.json().get("ok", True),
      r.text[:160])
for handling, code in (("label", LABEL), ("exclude", EXCLUDE)):
    spec = {**SYN_SPEC, "uncoded": {"handling": handling, "label": "Uncoded"} if handling == "label" else {"handling": "exclude"}}
    c.post(f"/api/workspaces/{wid}/specs", json={"spec": spec, "rationale": f"uncoded {handling}"}).raise_for_status()
    c.post(f"/api/workspaces/{wid}/programs", json={"code": SYN_R.replace("CODING", code)}).raise_for_status()
    run = c.post(f"/api/workspaces/{wid}/run", json={}).json()
    t = ((run.get("validation") or {}).get("tables") or [{}])[0]
    check(f"unfamiliar layout (DM/AEV, PID, ARMGRP, BODYSYS/TERM), uncoded = {handling}: verified",
          run["status"] == "succeeded" and t.get("status") == "passed" and run["verification"]["state"] == "verified",
          f"{t.get('passed')} passed, {t.get('mismatch')} mismatch")
c.post(f"/api/workspaces/{wid}/programs", json={"code": SYN_R.replace("CODING", EXCLUDE).replace("n_distinct(PID)", "n()")})
run = c.post(f"/api/workspaces/{wid}/run", json={}).json()
check("an error on uploaded data is caught (events counted as subjects)", run["verification"]["state"] == "failed",
      run["verification"]["reasons"][0] if run["verification"]["reasons"] else "")
big = b"PID,X\n" + b"1,2\n" * (7 * 2**20)  # 28 MB
r = c.post(f"/api/workspaces/{wid}/datasets", files=[("files", ("big.csv", big))]).json()
check("files over the size limit are refused", not r["datasets"] and "MB limit" in r["errors"][0]["error"], r["errors"][0]["error"])
r = c.post(f"/api/workspaces/{wid}/datasets", files=[("files", ("x.sh", b"echo hi"))]).json()
check("other file types are refused", "unsupported file type" in r["errors"][0]["error"])
other = httpx.Client(base_url=a.api, timeout=30)
check("another session cannot read the uploaded data", other.get(f"/api/workspaces/{wid}/datasets/DM/rows").status_code == 404
      and c.get(f"/api/workspaces/{wid}/datasets/DM/rows").status_code == 200)
json.dump({"api": a.api, "workspace": wid, "results": results}, open(a.out, "w"), indent=1)
n = sum(x["pass"] for x in results)
print(f"\n{n}/{len(results)} checks passed")
sys.exit(0 if n == len(results) else 1)
