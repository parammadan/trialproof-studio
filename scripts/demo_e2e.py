"""Drive the flagship session end to end against a running TrialProof server and a live model, through the
same HTTP API the UI uses. Prints what happened at each step and writes a JSON log.

  server/.venv/bin/python scripts/demo_e2e.py [--api http://127.0.0.1:8765] [--out demo_run.json]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time

import httpx

p = argparse.ArgumentParser()
p.add_argument("--api", default="http://127.0.0.1:8765")
p.add_argument("--out", default="demo_run.json")
p.add_argument("--timeout", type=int, default=600)
args = p.parse_args()
c = httpx.Client(base_url=args.api, timeout=180)
log: list[dict] = []


def snap(w):
    return c.get(f"/api/workspaces/{w}").json()


def say(w, text, ctx=None):
    print(f"\n>>> {text}")
    t0 = time.time()
    r = c.post(f"/api/workspaces/{w}/messages", json={"text": text, "context": ctx})
    r.raise_for_status()
    time.sleep(1)
    while snap(w)["agent_busy"]:
        if time.time() - t0 > args.timeout:
            c.post(f"/api/workspaces/{w}/cancel")
            print("   timed out; cancelled")
            break
        time.sleep(2)
    s = snap(w)
    step = {"say": text, "seconds": round(time.time() - t0, 1), "program": (s["program"] or {}).get("version"),
            "specs": [(x["version"], x["status"]) for x in s["specs"]],
            "run": (s["run"] or {}).get("status"),
            "validation": [(t["table_id"], t["status"], t["passed"], t["mismatch"]) for t in (s["validation"] or {}).get("tables", [])]}
    log.append(step)
    print("   ", json.dumps({k: v for k, v in step.items() if k != "say"}))
    return s


def approve_pending(w):
    s = snap(w)
    pend = [x for x in s["specs"] if x["status"] == "proposed"]
    if not pend:
        return None
    v = pend[-1]["version"]
    print(f"   [user] approving spec v{v}: {json.dumps(pend[-1]['spec'])[:200]}")
    c.post(f"/api/workspaces/{w}/specs/{v}/decision", json={"decision": "approve"})
    return v


def until_run(w, prompt, tries=3):
    """Keep the conversation going (approving proposals) until a run with validation exists."""
    s = say(w, prompt)
    for _ in range(tries):
        v = approve_pending(w)
        if v:
            s = say(w, f"I approved specification v{v}. Go ahead.")
        elif s["run"] and s["validation"]:
            break
        else:
            s = say(w, "Continue: write the program, run it and check the validation.")
        if s["run"] and s["validation"] and not any(x["status"] == "proposed" for x in s["specs"]):
            break
    return s


w = c.post("/api/workspaces", json={"name": f"Demo run {time.strftime('%H:%M')}"}).json()["id"]
print("workspace", w)
c.post(f"/api/workspaces/{w}/datasets/pilot").raise_for_status()

# 1-7: TEAE summary from scratch
s = until_run(w, "Using these datasets, create a treatment-emergent adverse event summary by treatment group.")

# 8: where did a number come from
if s["run"] and s["run"]["tables"]:
    t = s["run"]["tables"][0]
    anyrow = next((r for r in t["rows"] if r.get("level") == "any"), None)
    if anyrow:
        s = say(w, "Where did this number come from? Show the R code responsible.",
                {"selected_cell": {"table_id": t["id"], "level": "any", "soc": None, "pt": None, "group": anyrow["group"], "value_n": anyrow["n"], "value_N": anyrow["N"]}})

# 9-10: change the population
s = until_run(w, "Use the efficacy population (EFFFL) instead of the safety population.")

# 11-12: deliberately break the program
code = s["program"]["code"] if s["program"] else ""
broken = re.sub(r"n_distinct\(USUBJID\)", "n()", code, count=1)
if broken != code:
    c.post(f"/api/workspaces/{w}/programs", json={"code": broken, "summary": "deliberate error"})
    r = c.post(f"/api/workspaces/{w}/run", json={}).json()
    vt = (r.get("validation") or {}).get("tables", [])
    print(f"\n[user] introduced an error (first n_distinct(USUBJID) -> n()); run {r['status']}; validation {[(x['status'], x['mismatch']) for x in vt]}")
    log.append({"user_error": True, "run": r["status"], "validation": [(x["table_id"], x["status"], x["passed"], x["mismatch"]) for x in vt]})
    # 13-14: ask for the investigation and a fix
    s = say(w, "Validation found mismatches. Investigate the cause and propose a corrected program, then rerun it. Do not change the specification.")
    approve_pending(w)

json.dump({"workspace": w, "steps": log}, open(args.out, "w"), indent=2)
print("\nlog written to", args.out, "· workspace", w)
