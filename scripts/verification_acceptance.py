"""Milestone 2 acceptance tests against a running deployment (default: production), through the public API only,
on the public CDISC pilot data in one new workspace of this client's own session. No model calls.

  python scripts/verification_acceptance.py [--api https://trialproof-studio.fly.dev] [--out log.json]
"""
import argparse
import io
import json
import sys
import time
import zipfile
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))
from tp.example import TEAE_R  # noqa: E402  (the hand-written example program, the same one the server ships)

ap = argparse.ArgumentParser()
ap.add_argument("--api", default="https://trialproof-studio.fly.dev")
ap.add_argument("--out", default="verification_acceptance.json")
a = ap.parse_args()
c = httpx.Client(base_url=a.api, timeout=180)
results, log = [], {"api": a.api, "started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "runs": {}}
EMIT = 'tp_emit_table(teae, id = "teae", title = "TEAEs by SOC and PT", type = "teae_summary")'


def check(name, cond, detail=""):
    results.append({"check": name, "pass": bool(cond), "detail": detail})
    print(("PASS " if cond else "FAIL ") + name + (f"  ({detail})" if detail else ""), flush=True)


h = c.get("/api/health").json()
check("execution enabled after a passing sandbox self-test", h["execution"]["enabled"] and h["execution"]["verified"], h["execution"]["self_test"])
wid = c.post("/api/workspaces", json={"name": "acceptance · verification"}).json()["id"]
c.post(f"/api/workspaces/{wid}/example").raise_for_status()
spec = c.get(f"/api/workspaces/{wid}").json()["specs"][-1]
check("new specs record the percentage and zero-cell rules", spec["spec"].get("percent") == {"required": True, "decimals": 1}
      and spec["spec"].get("zero_cells") == "optional", json.dumps({k: spec["spec"].get(k) for k in ("percent", "zero_cells")}))


def run(label, code):
    c.post(f"/api/workspaces/{wid}/programs", json={"code": code}).raise_for_status()
    r = c.post(f"/api/workspaces/{wid}/run", json={}).json()
    t = ((r.get("validation") or {}).get("tables") or [{}])[0]
    cells = c.get(f"/api/workspaces/{wid}/runs/{r['run_id']}").json()["validation"]
    codes = sorted({x.get("code") for x in (cells or {}).get("tables", [{}])[0].get("cells", []) if x["status"] == "mismatch"}) if cells else []
    log["runs"][label] = {"run_id": r["run_id"], "status": r["status"], "validation": t.get("status"), "passed": t.get("passed"),
                          "mismatch": t.get("mismatch"), "codes": codes, "verification": r.get("verification")}
    return r, t, codes


def sign(run_id, **kw):
    return c.post(f"/api/workspaces/{wid}/signoff", json={"run_id": run_id, "reviewer": "Acceptance test", "decision": "approved",
                                                           "note": "automated acceptance", "confirmed": True, **kw})


r0, t, _ = run("correct", TEAE_R)
check("the correct program passes (417/417) and is verified", t.get("passed") == 417 and t.get("mismatch") == 0
      and r0["verification"]["state"] == "verified", f"{t.get('passed')}/{t.get('mismatch')}, {r0['verification']['state']}")
cases = [
    ("percentages ×0.1", TEAE_R.replace("mutate(pct = 100 * n / N)", "mutate(pct = 10 * n / N)"), {"pct"}, 417),
    ("percentage column missing", TEAE_R.replace(" %>% mutate(pct = 100 * n / N)", ""), {"pct_missing"}, 417),
    ("invented zero-count PT row", TEAE_R.replace(EMIT, 'teae <- bind_rows(teae, data.frame(level="pt", soc="CARDIAC DISORDERS", pt="MADE UP TERM", group="Placebo", n=0L, N=86L, pct=0))\n' + EMIT), {"extra_category"}, 1),
    ("duplicate row", TEAE_R.replace(EMIT, "teae <- bind_rows(teae, teae[1, ])\n" + EMIT), {"duplicate"}, 1),
    ("5 PT rows missing", TEAE_R.replace(EMIT, 'teae <- teae[-which(teae$level == "pt")[1:5], ]\n' + EMIT), {"missing"}, 5),
    ("records counted instead of subjects (PT)", TEAE_R.replace("pt = AEDECOD) %>% summarise(n = n_distinct(USUBJID)", "pt = AEDECOD) %>% summarise(n = n()"), {"n"}, 158),
    ("denominator from AE subjects", TEAE_R.replace('left_join(denom, by = "group")', 'left_join(ae %>% group_by(group) %>% summarise(N = n_distinct(USUBJID)), by = "group")'), {"N"}, 417),
    ("efficacy population instead of safety", TEAE_R.replace('filter(SAFFL == "Y")', 'filter(EFFFL == "Y")'), None, 417),
    ("treatment-emergent filter dropped", TEAE_R.replace('tp_read("ADAE") %>% filter(TRTEMFL == "Y") %>%', 'tp_read("ADAE") %>%'), None, None),
]
for label, code, want, n in cases:
    r, t, codes = run(label, code)
    ok = t.get("status") == "mismatch" and (want is None or set(codes) == want) and (n is None or t.get("mismatch") == n)
    check(f"detected: {label}", ok, f"{t.get('mismatch')} mismatched, codes {codes}")
    check(f"cannot sign off: {label}", sign(r["run_id"]).status_code == 409 and r["verification"]["state"] == "failed", r["verification"]["state"])

rf, _, _ = run("failed run", "x <- c(1,\n")
check("cannot sign off a failed run", rf["status"] == "failed" and sign(rf["run_id"]).status_code == 409)
check("changes can be requested on a failed run", sign(rf["run_id"], decision="changes_requested").status_code == 200)
ru, _, _ = run("unverified table type", TEAE_R.replace('type = "teae_summary"', 'type = "generic"'))
check("cannot sign off an unverified run", ru["verification"]["state"] == "not_verified" and sign(ru["run_id"]).status_code == 409, ru["verification"]["state"])

r1, t, _ = run("correct again", TEAE_R)
check("decision must be allowed", sign(r1["run_id"], decision="totally-made-up").status_code == 400)
check("the reviewer must confirm their review", sign(r1["run_id"], confirmed=False).status_code == 400)
ok = sign(r1["run_id"])
check("a current verified run can be signed off", ok.status_code == 200 and ok.json().get("verification_state") == "verified", ok.text[:120])

c.post(f"/api/workspaces/{wid}/programs", json={"code": TEAE_R + "\n# edited after validation\n"}).raise_for_status()
v = c.get(f"/api/workspaces/{wid}").json()["verification"]
check("program change after validation → stale", v["state"] == "stale" and "program changed" in " ".join(v["reasons"]), "; ".join(v["reasons"]))
check("cannot sign off a stale result", sign(r1["run_id"]).status_code == 409)

r2, t, _ = run("correct, current", TEAE_R)
efficacy = {**spec["spec"], "population": [{"var": "EFFFL", "op": "eq", "value": "Y"}]}
c.post(f"/api/workspaces/{wid}/specs", json={"spec": efficacy, "rationale": "acceptance: later spec"}).raise_for_status()
v = c.get(f"/api/workspaces/{wid}").json()["verification"]
check("specification change after validation → stale", v["state"] == "stale" and "specification changed" in " ".join(v["reasons"]), "; ".join(v["reasons"]))
check("cannot sign off after the spec changed", sign(r2["run_id"]).status_code == 409)

z = zipfile.ZipFile(io.BytesIO(c.get(f"/api/workspaces/{wid}/export", params={"run_id": r1["run_id"]}).content))
man, sp, val = (json.loads(z.read(n)) for n in ("manifest.json", "analysis_spec.json", "validation.json"))
log["export_manifest"] = man
check("export of an older run carries the spec its validation used", sp["version"] == 1 and man["spec_version"] == 1
      and sp["spec"]["population"][0]["var"] == "SAFFL", f"bundle spec v{sp['version']}, current approved spec is v2")
check("export carries the executed code and its validation", z.read("program.R").decode() == TEAE_R and val["run_id"] == r1["run_id"]
      and man["program_matches_recorded_hash"])
check("export records the review and the verification state now", man["reviews"] and man["reviews"][-1]["decision"] == "approved"
      and man["verification_state_at_export"]["state"] == "stale")
check("export records dataset hashes at run time", all(d["sha256_at_run"] and d["unchanged_since_run"] for d in man["datasets"]),
      ", ".join(f"{d['name']} {d['sha256_at_run'][:12]}" for d in man["datasets"]))

log["workspace"], log["results"] = wid, results
log["passed"], log["failed"] = sum(r["pass"] for r in results), sum(not r["pass"] for r in results)
json.dump(log, open(a.out, "w"), indent=1, default=str)
print(f"\n{log['passed']}/{len(results)} checks passed")
sys.exit(1 if log["failed"] else 0)
