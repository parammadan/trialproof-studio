"""Security regression tests (Milestone 1). The sandbox tests run user programs through the same path as the app
(Studio.run → Executor → the platform's sandbox), so on Linux they exercise bubblewrap and on macOS sandbox-exec.
The API tests start real servers in public mode, each with its own data folder."""
import asyncio
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

from tp import datasets as dsets
from tp import executor as ex_mod
from tp.agent import Agent, QuotaError
from tp.bus import Bus
from tp.example import SPEC, TEAE_R
from tp.executor import APP_DIR, Executor, IsolationError
from tp.store import Store
from tp.studio import Studio

SERVER = Path(__file__).resolve().parents[1]
PROBE = r'''
ok <- function(expr) tryCatch({ force(expr); TRUE }, error = function(e) FALSE, warning = function(w) FALSE)
r <- list()
r$own_dataset <- ok(stopifnot(nrow(tp_read("MINE")) == 2))
r$read_db <- ok(stopifnot(length(readBin(@DB@, "raw", 16)) > 0))
r$write_db_dir <- ok(writeLines("x", file.path(dirname(@DB@), "zz_probe.txt")))
r$read_other_ws <- ok(stopifnot(length(readLines(@OTHER@, warn = FALSE)) > 0))
r$list_other_ws <- ok(stopifnot(length(list.files(@OTHERDIR@)) > 0))
r$read_source <- ok(stopifnot(length(readLines(@SRC@, n = 1)) > 0))
r$write_source_dir <- ok(writeLines("x", @SRCW@))
secret <- paste0("SECRET_", "FOR_TEST")  # built at run time: the program text itself is echoed to the console
r$env_secret <- nzchar(Sys.getenv("TP_LLM_API_KEY")) || any(grepl(secret, Sys.getenv(), fixed = TRUE))
scan <- function(f) tryCatch({ b <- readBin(f, "raw", 1e6); b[b == as.raw(0)] <- as.raw(10); grepl(secret, rawToChar(b), fixed = TRUE) }, error = function(e) FALSE)
r$proc_secret <- any(vapply(Sys.glob("/proc/[0-9]*/environ"), scan, logical(1)))
r$network <- ok({ s <- socketConnection("1.1.1.1", 80, blocking = TRUE, open = "r+", timeout = 3); close(s) }) || ok(readLines(url("http://example.com"), n = 1))
writeLines(jsonlite::toJSON(r, auto_unbox = TRUE), file.path(Sys.getenv("TP_OUT_DIR"), "probe.json"))
tp_emit_table(data.frame(check = names(r), value = unlist(r)), id = "probe", type = "generic")
'''


@pytest.fixture
def two_workspaces(tmp_path, monkeypatch):
    monkeypatch.setattr("tp.store.WS_DIR", tmp_path / "ws")
    monkeypatch.setattr("tp.store.VAR", tmp_path)
    monkeypatch.setenv("TP_LLM_API_KEY", "SECRET_FOR_TEST")  # in the server's environment only
    store = Store(tmp_path / "t.db")
    ex = Executor(tmp_path)
    iso = asyncio.run(ex.self_test(store.path))
    assert iso["verified"], iso
    st = Studio(store, Bus(store), ex)
    a, b = store.create_workspace("mine")["id"], store.create_workspace("other")["id"]
    for wid, name in ((a, "mine.csv"), (b, "other.csv")):
        src = tmp_path / name
        src.write_text("id,value\n1,SECRET_ROW\n2,y\n")
        asyncio.run(dsets.ingest(store, wid, src, name))
    return store, st, a, b


def test_sandbox_isolates_user_programs(two_workspaces):
    store, st, a, b = two_workspaces
    paths = {"DB": store.path, "OTHER": store.ws_path(b) / "datasets" / "OTHER" / "data.csv",
             "OTHERDIR": store.ws_path(b), "SRC": APP_DIR / "server" / "tp" / "main.py",
             "SRCW": APP_DIR / "server" / "tp" / "zz_probe.txt"}
    code = PROBE
    for k, v in paths.items():
        code = code.replace(f"@{k}@", json.dumps(str(Path(v).resolve())))
    st.save_program(a, code, "user")
    r = asyncio.run(st.run(a, by="user"))
    assert r["status"] == "succeeded", r["console"]
    got = json.loads((store.ws_path(a) / "runs" / r["run_id"] / "out" / "probe.json").read_text())
    assert got["own_dataset"] is True, "the program must still read its own datasets"
    assert r["tables"] and r["tables"][0]["id"] == "probe", "the program must still write its output"
    denied = {k: v for k, v in got.items() if k != "own_dataset" and v}
    assert not denied, f"sandbox allowed: {denied}"
    assert "SECRET_FOR_TEST" not in r["console"] and "SECRET_ROW" not in json.dumps(r["tables"][0]["rows"])
    assert not (store.path.parent / "zz_probe.txt").exists() and not Path(paths["SRCW"]).exists()


def test_runs_refused_until_isolation_verified(tmp_path, monkeypatch):
    monkeypatch.setattr("tp.store.WS_DIR", tmp_path / "ws")
    store = Store(tmp_path / "t.db")
    st = Studio(store, Bus(store), Executor(tmp_path))  # self-test never run
    wid = store.create_workspace("w")["id"]
    st.save_program(wid, "x <- 1\n", "user")
    with pytest.raises(IsolationError):
        asyncio.run(st.run(wid, by="user"))


def test_self_test_fails_closed_without_a_sandbox(tmp_path, monkeypatch):
    """An executor that runs R with no sandbox at all must fail its self-test (and so refuse to run programs)."""
    store = Store(tmp_path / "t.db")
    ex = Executor(tmp_path)
    monkeypatch.setattr(ex_mod, "_command", lambda run_dir, data_dir, var: ["Rscript", "--vanilla", str(ex_mod.R_DIR / "runner.R"), "program.R"])
    iso = asyncio.run(ex.self_test(store.path))
    assert iso["verified"] is False and "read_database" in iso["status"], iso
    assert not ex.allowed()


def test_self_test_fails_closed_when_sandbox_missing(tmp_path, monkeypatch):
    store = Store(tmp_path / "t.db")
    monkeypatch.setattr(ex_mod, "BACKEND", "no-such-backend")
    ex = Executor(tmp_path)
    iso = asyncio.run(ex.self_test(store.path))
    assert iso["verified"] is False and not ex.allowed(), iso


def test_output_limit_stops_runaway_printing(two_workspaces, monkeypatch):
    store, st, a, _ = two_workspaces
    monkeypatch.setattr(ex_mod, "OUTPUT_LIMIT", 64 * 1024)
    st.save_program(a, 'for (i in 1:1e6) cat(strrep("x", 200), "\\n")\n', "user")
    r = asyncio.run(st.run(a, by="user"))
    assert r["status"] == "failed" and "output limit" in r["error"]["message"]
    assert len(r["console"]) < 80 * 1024


def test_budgets_stop_one_session_and_the_whole_demo(tmp_path, monkeypatch):
    store = Store(tmp_path / "t.db")
    monkeypatch.setattr("tp.store.WS_DIR", tmp_path / "ws")
    ag = Agent(Studio(store, Bus(store), Executor(tmp_path)))
    import tp.agent as A
    monkeypatch.setattr(A, "BUDGETS", {"session": {"turns": 2, "tokens": 1000}, "ip": {"turns": 0, "tokens": 0},
                                       "global": {"turns": 0, "tokens": 2500}})
    wid = store.create_workspace("w")["id"]
    s1, s2 = {"session": "s1", "ip": "1.1.1.1"}, {"session": "s2", "ip": "2.2.2.2"}
    assert ag.over_budget(s1) is None
    ag._charge(s1, turns=2)
    assert "session" in ag.over_budget(s1) and ag.over_budget(s2) is None
    with pytest.raises(QuotaError):
        ag.start(wid, "hi", who=s1)
    ag._charge(s2, tokens=900)
    assert ag.over_budget(s2) is None
    ag._charge(s2, tokens=200)  # past the session token budget
    assert "session" in ag.over_budget(s2)
    ag._charge({"session": "s3", "ip": "3.3.3.3"}, tokens=1500)  # total 1,100 + 1,500 tokens ≥ the global 2,500
    assert "shared" in ag.over_budget({"session": "s4", "ip": "4.4.4.4"})
    assert any(e["kind"] == "agent_error" and e["payload"]["code"] == "quota" for e in store.events(wid))


# ---------- API, public mode, real servers ----------
def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


@pytest.fixture
def public_server(tmp_path):
    procs = []

    def start(**env):
        port = _free_port()
        e = {**os.environ, "TP_PUBLIC": "1", "TP_VAR": str(tmp_path / f"var{port}"), "TP_LLM_BASE_URL": "http://127.0.0.1:9/v1",
             "TP_LLM_MODEL": "none", "TP_LLM_API_KEY": "SECRET_FOR_TEST", **{k: str(v) for k, v in env.items()}}
        p = subprocess.Popen([sys.executable, "-m", "uvicorn", "tp.main:app", "--port", str(port), "--no-proxy-headers"], cwd=SERVER, env=e,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        procs.append(p)
        url = f"http://127.0.0.1:{port}"
        for _ in range(120):
            try:
                if httpx.get(url + "/api/health", timeout=2).status_code == 200:
                    return url
            except httpx.HTTPError:
                time.sleep(0.25)
        raise RuntimeError("server did not start")

    yield start
    for p in procs:
        p.terminate()
        p.wait(10)


def test_sessions_cannot_reach_each_others_workspaces(public_server):
    url = public_server()
    alice, bob = httpx.Client(base_url=url, timeout=60), httpx.Client(base_url=url, timeout=60)
    wid = alice.post("/api/workspaces", json={"name": "alice"}).json()["id"]
    assert alice.post(f"/api/workspaces/{wid}/example").status_code == 200
    assert alice.get(f"/api/workspaces/{wid}").status_code == 200
    assert wid not in [w["id"] for w in bob.get("/api/workspaces").json()]
    probes = [("GET", ""), ("GET", "/events"), ("GET", "/export"), ("GET", "/runs"), ("GET", "/datasets/ADSL/rows"),
              ("POST", "/programs", {"code": "x <- 1"}), ("POST", "/run", {}), ("POST", "/messages", {"text": "hi"}),
              ("POST", "/specs", {"spec": SPEC}), ("POST", "/example", None), ("POST", "/cancel", None),
              ("POST", "/signoff", {"run_id": "x", "reviewer": "bob", "decision": "approved"})]
    for method, sub, *body in probes:
        r = bob.request(method, f"/api/workspaces/{wid}{sub}", json=body[0] if body else None)
        assert r.status_code == 404, (method, sub, r.status_code)
    forged = httpx.Client(base_url=url, timeout=60, cookies={"tp_session": "made-up-value"})
    assert forged.get(f"/api/workspaces/{wid}").status_code == 404
    # Alice's workspace is unchanged by all of that
    s = alice.get(f"/api/workspaces/{wid}").json()
    assert s["program"]["author"] == "example" and len(s["programs"]) == 1 and not s["approvals"]
    assert "owner" not in s["workspace"]


def test_spoofed_ip_headers_do_not_bypass_rate_limits(public_server):
    for env in ({}, {"TP_CLIENT_IP_HEADER": "fly-client-ip"}):
        url = public_server(**env)
        codes = []
        for i in range(12):  # a new client (new session) each time, with a different spoofed address
            c = httpx.Client(base_url=url, timeout=30)
            h = {"X-Forwarded-For": f"10.0.0.{i}", "X-Real-IP": f"10.1.0.{i}"}
            if env:
                h["fly-client-ip"] = "203.0.113.7"  # the trusted proxy's header (here: one fixed client)
            codes.append(c.post("/api/workspaces", json={"name": "x"}, headers=h).status_code)
        assert codes[:10] == [200] * 10 and codes[10:] == [429, 429], (env, codes)


def test_public_session_turn_budget(public_server):
    url = public_server(TP_BUDGET_SESSION_TURNS=2, TP_BUDGET_GLOBAL_TURNS=3)
    a = httpx.Client(base_url=url, timeout=60)
    wid = a.post("/api/workspaces", json={"name": "a"}).json()["id"]
    def idle(c, w):  # the stand-in model is unreachable, so each turn ends after the client's retries
        for _ in range(120):
            if not c.get(f"/api/workspaces/{w}").json()["agent_busy"]:
                return
            time.sleep(0.5)

    codes = []
    for _ in range(3):
        codes.append(a.post(f"/api/workspaces/{wid}/messages", json={"text": "hi"}).status_code)
        idle(a, wid)
    assert codes == [200, 200, 429], codes
    b = httpx.Client(base_url=url, timeout=60)
    wb = b.post("/api/workspaces", json={"name": "b"}).json()["id"]
    first = b.post(f"/api/workspaces/{wb}/messages", json={"text": "hi"})
    idle(b, wb)
    second = b.post(f"/api/workspaces/{wb}/messages", json={"text": "hi"})
    assert first.status_code == 200 and second.status_code == 429 and "shared" in second.json()["detail"]


def test_public_mode_still_runs_and_verifies(public_server):
    url = public_server()
    c = httpx.Client(base_url=url, timeout=120)
    h = c.get("/api/health").json()
    assert h["execution"]["enabled"] and h["execution"]["verified"], h["execution"]
    assert "SECRET_FOR_TEST" not in json.dumps(h)
    wid = c.post("/api/workspaces", json={"name": "ok"}).json()["id"]
    c.post(f"/api/workspaces/{wid}/example").raise_for_status()
    r = c.post(f"/api/workspaces/{wid}/run", json={}).json()
    t = r["validation"]["tables"][0]
    assert r["status"] == "succeeded" and t["status"] == "passed" and t["passed"] == 417 and t["mismatch"] == 0
