"""Milestone 1 acceptance tests against a running deployment (default: production), from outside, through the
public API only. The probe program prints TRUE/FALSE facts and never the contents of anything it reaches.

  python scripts/security_acceptance.py [--api https://trialproof-studio.fly.dev] [--app-dir /home/user/app] [--out log.json]

Uses at most 3 of this client address's hourly workspace creations before the rate-limit test, which then uses the rest.
"""
import argparse
import json
import sys
import time

import httpx

ap = argparse.ArgumentParser()
ap.add_argument("--api", default="https://trialproof-studio.fly.dev")
ap.add_argument("--app-dir", default="/home/user/app", help="application folder on the server (for probe paths)")
ap.add_argument("--var-dir", default=None, help="data folder on the server (default <app-dir>/var)")
ap.add_argument("--out", default="security_acceptance.json")
a = ap.parse_args()
APP, VAR = a.app_dir, a.var_dir or f"{a.app_dir}/var"
results, log = [], {"api": a.api, "started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}


def check(name, cond, detail=""):
    results.append({"check": name, "pass": bool(cond), "detail": detail})
    print(("PASS " if cond else "FAIL ") + name + (f"  ({detail})" if detail else ""), flush=True)


def client():
    return httpx.Client(base_url=a.api, timeout=180)


h = client().get("/api/health").json()
log["health"] = h
check("execution enabled only after a passing self-test", h["execution"]["enabled"] and h["execution"]["verified"], h["execution"]["self_test"])
check("isolation backend", h["executor"] in ("bwrap", "sandbox", "docker"), h["isolation"])

alice, bob = client(), client()
wa = alice.post("/api/workspaces", json={"name": "acceptance · alice"}).json()["id"]
wb = bob.post("/api/workspaces", json={"name": "acceptance · bob"}).json()["id"]
alice.post(f"/api/workspaces/{wa}/example").raise_for_status()
bob.post(f"/api/workspaces/{wb}/datasets/pilot").raise_for_status()

# normal flow still works
r = alice.post(f"/api/workspaces/{wa}/run", json={}).json()
t = (r.get("validation") or {}).get("tables", [{}])[0]
check("worked example runs and verifies (417 passed, 0 mismatches)", r.get("status") == "succeeded" and t.get("passed") == 417 and t.get("mismatch") == 0,
      f"{r.get('status')}, {t.get('passed')} passed, {t.get('mismatch')} mismatch")

# the sandbox, through the normal user-program path
PROBE = r'''
ok <- function(expr) tryCatch({ force(expr); TRUE }, error = function(e) FALSE, warning = function(w) FALSE)
r <- list()
r$read_own_dataset <- ok(stopifnot(nrow(tp_read("ADSL")) == 254))
r$read_database <- ok(stopifnot(length(readBin(@DB@, "raw", 16)) > 0))
r$write_database_dir <- ok(writeLines("x", @DBW@))
r$read_app_source <- ok(stopifnot(length(readLines(@SRC@, n = 1)) > 0))
r$list_app_dir <- ok(stopifnot(length(list.files(@APP@)) > 0))
r$write_app_source_dir <- ok(writeLines("x", @SRCW@))
r$read_other_workspace <- ok(stopifnot(length(readLines(@OTHER@, n = 1)) > 0))
r$list_workspaces <- ok(stopifnot(length(list.files(@WSDIR@)) > 0))
key_prefix <- paste0("gs", "k_")  # Groq keys start with this; built at run time because the program text is echoed
r$env_has_secret <- any(grepl(key_prefix, Sys.getenv(), fixed = TRUE)) || nzchar(Sys.getenv("TP_LLM_API_KEY"))
scan <- function(f) tryCatch({ b <- readBin(f, "raw", 1e6); b[b == as.raw(0)] <- as.raw(10); grepl(key_prefix, rawToChar(b), fixed = TRUE) }, error = function(e) FALSE)
r$proc_has_secret <- any(vapply(c(Sys.glob("/proc/[0-9]*/environ"), Sys.glob("/proc/[0-9]*/cmdline")), scan, logical(1)))
r$proc_pids_visible <- length(Sys.glob("/proc/[0-9]*"))
r$network_tcp <- ok({ s <- socketConnection("1.1.1.1", 80, blocking = TRUE, open = "r+", timeout = 3); close(s) })
r$network_http <- ok(readLines(url("http://example.com"), n = 1))
r$write_own_dataset <- ok(writeLines("x", file.path(Sys.getenv("TP_DATA_DIR"), "ADSL", "data.csv")))
tp_emit_table(data.frame(check = names(r), value = vapply(r, function(v) as.character(v), "")), id = "probe", type = "generic")
'''
paths = {"DB": f"{VAR}/tp.db", "DBW": f"{VAR}/zz_probe.txt", "SRC": f"{APP}/server/tp/main.py", "APP": APP,
         "SRCW": f"{APP}/server/tp/zz_probe.txt", "OTHER": f"{VAR}/workspaces/{wb}/datasets/ADSL/data.csv",
         "WSDIR": f"{VAR}/workspaces"}
code = PROBE
for k, v in paths.items():
    code = code.replace(f"@{k}@", json.dumps(v))
alice.post(f"/api/workspaces/{wa}/programs", json={"code": code}).raise_for_status()
pr = alice.post(f"/api/workspaces/{wa}/run", json={}).json()
got = {row["check"]: row["value"] for row in (pr.get("tables") or [{}])[0].get("rows", [])}
log["probe"] = {"status": pr.get("status"), "error": pr.get("error"), "facts": got}
check("probe program ran", pr.get("status") == "succeeded", pr.get("status"))
check("R can read its own dataset", got.get("read_own_dataset") == "TRUE")
check("R can write its output (probe table emitted)", bool(got))
for k in ("read_database", "write_database_dir", "read_app_source", "list_app_dir", "write_app_source_dir",
          "read_other_workspace", "list_workspaces", "env_has_secret", "proc_has_secret", "network_tcp", "network_http",
          "write_own_dataset"):
    check(f"R cannot: {k.replace('_', ' ')}", got.get(k) == "FALSE", got.get(k))
check("R sees only its own processes", got.get("proc_pids_visible", "99").isdigit() and int(got["proc_pids_visible"]) <= 5,
      f"{got.get('proc_pids_visible')} pids visible")

# sessions
probes = [("GET", ""), ("GET", "/events"), ("GET", "/export"), ("GET", "/runs"), ("GET", "/datasets/ADSL/rows"),
          ("POST", "/programs", {"code": "x <- 1"}), ("POST", "/run", {}), ("POST", "/messages", {"text": "hi"}),
          ("POST", "/example", None), ("POST", "/signoff", {"run_id": "x", "reviewer": "bob", "decision": "approved"})]
codes = {f"{m} {s}": bob.request(m, f"/api/workspaces/{wa}{s}", json=b[0] if b else None).status_code for m, s, *b in probes}
check("another session gets 404 on every workspace endpoint", set(codes.values()) == {404}, json.dumps(codes))
check("another session's list does not include the workspace", wa not in [w["id"] for w in bob.get("/api/workspaces").json()])
forged = httpx.Client(base_url=a.api, timeout=60, cookies={"tp_session": "forged-session-value"})
check("a forged session cookie gets 404", forged.get(f"/api/workspaces/{wa}").status_code == 404)
after = alice.get(f"/api/workspaces/{wa}").json()
check("the workspace was not changed by the other session", not after["approvals"] and after["program"]["author"] == "user")

# rate limits: new client and spoofed headers every time; the trusted address is still one client
codes = []
for i in range(12):
    c = client()
    hdr = {"X-Forwarded-For": f"10.0.0.{i}", "X-Real-IP": f"10.1.0.{i}", "Fly-Client-IP": f"10.2.0.{i}",
           "True-Client-IP": f"10.3.0.{i}", "Forwarded": f"for=10.4.0.{i}"}
    codes.append(c.post("/api/workspaces", json={"name": "acceptance · rate"}, headers=hdr).status_code)
log["rate_limit_codes"] = codes
first429 = codes.index(429) if 429 in codes else None
check("spoofed client-IP headers do not bypass the workspace limit (10/hour)",
      first429 is not None and first429 <= 10 and all(x == 429 for x in codes[first429:]), str(codes))

log["results"] = results
log["passed"] = sum(r["pass"] for r in results)
log["failed"] = sum(not r["pass"] for r in results)
json.dump(log, open(a.out, "w"), indent=1, default=str)
print(f"\n{log['passed']}/{len(results)} checks passed")
sys.exit(1 if log["failed"] else 0)
