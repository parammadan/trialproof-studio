"""Milestone 3 acceptance: the full workflow with a REAL model, through the public HTTP API (the same calls the UI makes).

  upload data -> AI inspects -> AI proposes a spec -> reviewer checks it against the expected spec and approves
  -> AI writes R -> run -> independent verification -> the reviewer injects an error -> verification catches it
  -> AI investigates and fixes -> rerun -> verified -> sign-off -> export -> evidence checked

Nothing is mocked and nothing is assumed: every step records what the server reported. A step that does not reach
its goal is a FAIL in the log, with the agent's own events (host nudges, errors, outcome) kept for diagnosis.

  python scripts/agent_acceptance.py --api http://127.0.0.1:8765 --data synthetic --out log.json
  --data pilot        the public CDISC pilot ADSL + ADAE (loaded server-side, SHA-256 checked)
  --data renamed      the same pilot data uploaded as CSV with every analysis variable renamed (an unfamiliar layout)
  --data synthetic    the small hand-checked DM/AEV fixture (different names, blank SOC and PT values)
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import re
import sys
import tempfile
import time
import zipfile
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
ap = argparse.ArgumentParser()
ap.add_argument("--api", default="http://127.0.0.1:8765")
ap.add_argument("--data", choices=["pilot", "renamed", "synthetic"], default="synthetic")
ap.add_argument("--out", default="agent_acceptance.json")
ap.add_argument("--timeout", type=int, default=900, help="seconds per agent turn")
a = ap.parse_args()
c = httpx.Client(base_url=a.api, timeout=300)
log: dict = {"api": a.api, "data": a.data, "started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "turns": [], "checks": []}

# What a correct specification must say for each data set (the reviewer's own expectation, written before the run)
RENAME = {"ADSL": {"USUBJID": "SUBJ", "TRT01A": "ACTUAL_ARM", "SAFFL": "SAFETY_POP", "EFFFL": "EFFICACY_POP"},
          "ADAE": {"USUBJID": "SUBJ", "AEBODSYS": "BODY_SYSTEM", "AEDECOD": "PREFERRED_TERM", "TRTEMFL": "TREAT_EMERGENT"}}
EXPECT = {
    "pilot": {"subject_id": "USUBJID", "population": ("SAFFL", "Y"), "treatment_var": {"TRT01A", "TRT01P", "ARM"},
              "event_filter": ("TRTEMFL", "Y"), "soc_var": "AEBODSYS", "pt_var": "AEDECOD"},
    "renamed": {"subject_id": "SUBJ", "population": ("SAFETY_POP", "Y"), "treatment_var": {"ACTUAL_ARM", "TRT01P", "ARM"},
                "event_filter": ("TREAT_EMERGENT", "Y"), "soc_var": "BODY_SYSTEM", "pt_var": "PREFERRED_TERM"},
    "synthetic": {"subject_id": "PID", "population": ("SAFETY", "Y"), "treatment_var": {"ARMGRP"},
                  "event_filter": ("EMERGENT", "Y"), "soc_var": "BODYSYS", "pt_var": "TERM", "uncoded": True},
}[a.data]


def check(name, ok, detail=""):
    log["checks"].append({"check": name, "pass": bool(ok), "detail": detail})
    print(("PASS " if ok else "FAIL ") + name + (f"  ({detail})" if detail else ""), flush=True)
    return bool(ok)


def snap():
    return c.get(f"/api/workspaces/{wid}").json()


def say(text):
    """Send one message and wait for the turn to end; return the turn's own events from the snapshot's event log."""
    print(f"\n>>> {text}", flush=True)
    before = snap()["last_event"]
    t0 = time.time()
    r = c.post(f"/api/workspaces/{wid}/messages", json={"text": text})
    if r.status_code != 200:
        turn = {"say": text, "http": r.status_code, "error": r.text[:300]}
        log["turns"].append(turn)
        print("   ", turn)
        return turn
    time.sleep(1)
    while snap()["agent_busy"]:
        if time.time() - t0 > a.timeout:
            c.post(f"/api/workspaces/{wid}/cancel")
            break
        time.sleep(3)
    s = snap()
    ev = c.get(f"/api/workspaces/{wid}/log", params={"after": before}).json()
    end = next((e["payload"] for e in reversed(ev) if e["kind"] == "agent_end"), {})
    turn = {"say": text, "seconds": round(time.time() - t0, 1), "outcome": end.get("outcome"), "model": end.get("model"),
            "tokens": end.get("usage"),
            "tools": [e["payload"]["name"] for e in ev if e["kind"] == "tool_call"],
            "nudges": [e["payload"].get("label") or e["payload"].get("reason") for e in ev if e["kind"] == "agent_nudge"],
            "errors": [f"{e['payload'].get('code')}: {e['payload'].get('message', '')[:300]}" for e in ev if e["kind"] == "agent_error"],
            "replies": [e["payload"]["text"][:400] for e in ev if e["kind"] == "assistant_message"],
            "program": (s["program"] or {}).get("version"), "specs": [(x["version"], x["status"]) for x in s["specs"]],
            "verification": s["verification"]}
    log["turns"].append(turn)
    print("   ", json.dumps({k: v for k, v in turn.items() if k not in ("say", "replies")}, default=str)[:900], flush=True)
    for rep in turn["replies"][-1:]:
        print("    agent:", rep.replace("\n", " ")[:300], flush=True)
    return turn


def spec_problems(spec: dict) -> list[str]:
    e, p = EXPECT, []
    if spec.get("type") != "teae_summary":
        p.append(f"type {spec.get('type')}")
    if spec.get("subject_id") != e["subject_id"]:
        p.append(f"subject_id {spec.get('subject_id')} (expected {e['subject_id']})")
    pop = [(x.get("var"), x.get("value")) for x in spec.get("population") or [] if x.get("op") in ("eq", None)]
    if e["population"] not in pop:
        p.append(f"population {spec.get('population')} (expected {e['population'][0]} == {e['population'][1]})")
    if spec.get("treatment_var") not in e["treatment_var"]:
        p.append(f"treatment_var {spec.get('treatment_var')} (expected one of {sorted(e['treatment_var'])})")
    evf = [(x.get("var"), x.get("value")) for x in spec.get("event_filter") or []]
    if e["event_filter"] not in evf:
        p.append(f"event_filter {spec.get('event_filter')} (expected {e['event_filter'][0]} == {e['event_filter'][1]})")
    for k in ("soc_var", "pt_var"):
        if spec.get(k) != e[k]:
            p.append(f"{k} {spec.get(k)} (expected {e[k]})")
    if e.get("uncoded") and not (spec.get("uncoded") or {}).get("handling"):
        p.append("no rule for uncoded SOC/PT terms")
    return p


def review_pending(tries=2):
    """Act as the reviewer: approve a proposal that matches the expected analysis, otherwise reject it with the reason."""
    for _ in range(tries):
        pend = [x for x in snap()["specs"] if x["status"] == "proposed"]
        if not pend:
            return None
        v, spec = pend[-1]["version"], pend[-1]["spec"]
        probs = spec_problems(spec)
        print(f"   [reviewer] spec v{v}: {json.dumps(spec)[:300]}", flush=True)
        if not probs:
            c.post(f"/api/workspaces/{wid}/specs/{v}/decision", json={"decision": "approve"}).raise_for_status()
            log.setdefault("spec_reviews", []).append({"version": v, "decision": "approve", "spec": spec})
            return v
        note = "; ".join(probs)
        c.post(f"/api/workspaces/{wid}/specs/{v}/decision", json={"decision": "reject", "note": note}).raise_for_status()
        log.setdefault("spec_reviews", []).append({"version": v, "decision": "reject", "note": note, "spec": spec})
        say(f"I rejected specification v{v}: {note}. Inspect the data again and propose a corrected specification.")
    return None


# ---------------------------------------------------------------- 0. server, workspace and data
h = c.get("/api/health").json()
log["model"], log["execution"] = h["llm"]["model"], h["execution"]["self_test"]
print("model", h["llm"]["model"], "at", h["llm"]["host"], "· sandbox", h["execution"]["self_test"], flush=True)
wid = c.post("/api/workspaces", json={"name": f"agent acceptance · {a.data}"}).json()["id"]
log["workspace"] = wid
if a.data == "pilot":
    r = c.post(f"/api/workspaces/{wid}/datasets/pilot")
    ds = r.json().get("datasets", [])
else:
    files = []
    if a.data == "synthetic":
        fx = ROOT / "server" / "tests" / "fixtures" / "synthetic"
        files = [("files", ("dm.csv", (fx / "dm.csv").read_bytes())), ("files", ("aev.csv", (fx / "aev.csv").read_bytes()))]
    else:  # the public pilot, exported once to CSV with renamed analysis variables (values untouched)
        tmp = httpx.Client(base_url=a.api, timeout=300)
        w2 = tmp.post("/api/workspaces", json={"name": "pilot source for renamed copy"}).json()["id"]
        tmp.post(f"/api/workspaces/{w2}/datasets/pilot").raise_for_status()
        for name, out in (("ADSL", "subjects.csv"), ("ADAE", "events.csv")):
            rows, off = [], 0
            while True:
                page = tmp.get(f"/api/workspaces/{w2}/datasets/{name}/rows", params={"offset": off, "limit": 500}).json()
                rows += page["rows"]
                off += 500
                if off >= page["total"]:
                    break
            buf = io.StringIO()
            cols = [RENAME[name].get(k, k) for k in rows[0]]
            wr = csv.writer(buf)
            wr.writerow(cols)
            wr.writerows([list(r.values()) for r in rows])
            files.append(("files", (out, buf.getvalue().encode())))
    r = c.post(f"/api/workspaces/{wid}/datasets", files=files)
    ds = r.json().get("datasets", [])
    log["upload_errors"] = r.json().get("errors")
check("datasets uploaded and parsed in the sandbox", r.status_code == 200 and len(ds) == 2,
      ", ".join(f"{d['name']} {d['nrow']}x{d['ncol']}" for d in ds) or r.text[:200])

# ---------------------------------------------------------------- 1-2. inspect and specify
t = say("Using these datasets, create a treatment-emergent adverse event summary by treatment group.")
check("the agent inspected the data before specifying", {"inspect_dataset", "tabulate", "list_datasets"} & set(t.get("tools", [])),
      ", ".join(t.get("tools", [])))
v = review_pending()
check("the proposed specification matches the expected analysis (approved by the reviewer)", v is not None,
      json.dumps(log.get("spec_reviews", [])[-1:], default=str)[:400])

# ---------------------------------------------------------------- 3-5. write, run, verify
if v:
    t = say(f"I approved specification v{v}. Go ahead.")
    for _ in range(2):  # a real user would say "continue" if the agent stopped; count how often that is needed
        if snap()["verification"]["state"] == "verified":
            break
        t = say("Continue.")
    s = snap()
    first_verified = check("the agent's program runs and passes independent verification", s["verification"]["state"] == "verified",
                           f"{s['verification']['state']}: {'; '.join(s['verification']['reasons'])}")
    log["continues_needed_first"] = sum(1 for x in log["turns"] if x["say"] == "Continue.")
    approved_v = (next((x for x in s["specs"] if x["status"] == "approved"), None) or {}).get("version")

    # ---------------------------------------------------------------- 6-7. reviewer injects an error; it is caught
    if first_verified:
        code = s["program"]["code"]
        mutations = [("subjects counted as records", lambda x: x.replace("n_distinct(", "length(")),
                     ("percentages scaled by 10", lambda x: re.sub(r"100\s*\*", "10 *", x, count=1))]
        injected = None
        for label, f in mutations:
            bad = f(code)
            if bad == code:
                continue
            c.post(f"/api/workspaces/{wid}/programs", json={"code": bad, "summary": f"reviewer test: {label}"}).raise_for_status()
            r = c.post(f"/api/workspaces/{wid}/run", json={}).json()
            mm = sum(x.get("mismatch", 0) for x in (r.get("validation") or {}).get("tables", []))
            if mm:
                injected = {"label": label, "mismatches": mm, "run_id": r["run_id"]}
                break
        log["injected_error"] = injected
        check("an injected error is caught by independent verification", injected,
              f"{injected['label']}: {injected['mismatches']} cells" if injected else "no mutation applied")
        if injected:
            say("Validation found mismatches after my edit. Investigate the cause, fix the program and rerun it. "
                "Do not change the specification.")
            for _ in range(2):
                if snap()["verification"]["state"] == "verified":
                    break
                say("Continue the fix.")
            s = snap()
            approved_now = (next((x for x in s["specs"] if x["status"] == "approved"), None) or {}).get("version")
            check("the agent found and fixed the error; rerun verified", s["verification"]["state"] == "verified",
                  f"{s['verification']['state']}: {'; '.join(s['verification']['reasons'])}")
            check("the specification was not changed by the fix", approved_now == approved_v, f"v{approved_v} -> v{approved_now}")
            log["continues_needed_fix"] = sum(1 for x in log["turns"] if x["say"] == "Continue the fix.")

    # ---------------------------------------------------------------- 8. sign-off and export evidence
    s = snap()
    if s["verification"]["state"] == "verified":
        rid = s["verification"]["run_id"]
        r = c.post(f"/api/workspaces/{wid}/signoff", json={"run_id": rid, "reviewer": "Acceptance reviewer", "decision": "approved",
                                                           "note": "agent acceptance", "confirmed": True})
        check("the verified run can be signed off", r.status_code == 200, r.text[:160])
        z = zipfile.ZipFile(io.BytesIO(c.get(f"/api/workspaces/{wid}/export", params={"run_id": rid}).content))
        man = json.loads(z.read("manifest.json"))
        log["export_manifest"] = man
        check("export evidence: current verified result, matching program hash, review recorded",
              man["verification_state_at_export"]["state"] == "verified" and man["program_matches_recorded_hash"]
              and man["reviews"] and man["reviews"][-1]["decision"] == "approved",
              f"spec v{man['spec_version']}, {len(man['datasets'])} datasets")

log["passed"] = sum(x["pass"] for x in log["checks"])
log["failed"] = sum(not x["pass"] for x in log["checks"])
log["tokens"] = sum(sum((x.get("tokens") or {}).values()) for x in log["turns"])
json.dump(log, open(a.out, "w"), indent=1, default=str)
print(f"\n{log['passed']}/{len(log['checks'])} checks passed · {len(log['turns'])} agent turns · {log['tokens']:,} tokens · log {a.out}")
sys.exit(1 if log["failed"] else 0)
