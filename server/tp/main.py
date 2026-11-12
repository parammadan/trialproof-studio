"""TrialProof Studio API (FastAPI). Run: uvicorn tp.main:app --port 8765"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import secrets
import subprocess
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
import tempfile
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import example as ex
from . import datasets as dsets
from .agent import Agent, QuotaError
from .bus import Bus
from .executor import BACKEND, Executor, IsolationError, backend_label
from .llm import LLMConfig, list_models
from .store import ROOT, VAR, Store
from .studio import ApprovalError, Studio

store = Store()
bus = Bus(store)
studio = Studio(store, bus, Executor(VAR))
agent = Agent(studio)

# Public demo mode (TP_PUBLIC=1): model settings fixed by the server, per-visitor rate limits; uploads are parsed in the sandbox
PUBLIC = os.environ.get("TP_PUBLIC") == "1"
# The only client address the server trusts: a header set by the operator's proxy (e.g. Fly's fly-client-ip,
# which the proxy overwrites), else the TCP peer. X-Forwarded-For is client-controlled and never used.
IP_HEADER = os.environ.get("TP_CLIENT_IP_HEADER", "").strip().lower()
COOKIE = "tp_session"
LIMITS = {"run": (40, 3600), "messages": (20, 3600), "workspaces": (10, 3600), "session": (20, 3600),
          "datasets": (30, 3600), "write": (300, 3600)}  # (requests, per seconds), per client IP and per session
_hits: dict[tuple, deque] = defaultdict(deque)
WS_PATH = re.compile(r"^/api/workspaces/([^/]+)")
R_VERSION = (lambda r: (r.stdout or r.stderr).strip())(subprocess.run(["Rscript", "--version"], capture_output=True, text=True))
if PUBLIC and agent.config().configured:
    store.set_setting("acked_hosts", [agent.config().host])  # the operator chose this endpoint for public data


@asynccontextmanager
async def lifespan(_app):
    iso = await studio.ex.self_test(store.path)
    print(f"[tp] sandbox self-test ({iso['backend']}): {iso['status']}", flush=True)
    yield


app = FastAPI(title="TrialProof Studio", lifespan=lifespan)


def client_ip(request: Request) -> str:
    if IP_HEADER and request.headers.get(IP_HEADER):
        return request.headers[IP_HEADER].strip()
    return request.client.host if request.client else "?"


def _hit(key: tuple, kind: str) -> bool:
    """Count one request against a sliding hourly window; False when the limit is reached."""
    n, per = LIMITS[kind]
    q, now = _hits[(*key, kind)], time.time()
    while q and q[0] < now - per:
        q.popleft()
    if len(q) >= n:
        return False
    q.append(now)
    return True


def _sid(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def _json(status: int, detail: str) -> Response:
    return Response(json.dumps({"detail": detail}), status, media_type="application/json")


def owns(ws: dict, sid: str | None) -> bool:
    # workspaces created before sessions existed (owner NULL) stay reachable on a local, single-user server only
    return (ws.get("owner") is not None and ws["owner"] == sid) or (ws.get("owner") is None and not PUBLIC)


@app.middleware("http")
async def guard(request: Request, call_next):
    path, ip = request.url.path, client_ip(request)
    raw = request.cookies.get(COOKIE)
    sid = _sid(raw) if raw else None
    new = None
    if path.startswith("/api/") and not (sid and store.one("select 1 from sessions where id=?", (sid,))):
        # only server-issued sessions count; a made-up cookie gets a fresh session like no cookie at all
        if PUBLIC and not _hit(("ip", ip), "session"):
            return _json(429, f"rate limit: {LIMITS['session'][0]} new sessions per hour in the public demo")
        new = secrets.token_urlsafe(32)
        sid = _sid(new)
        store.x("insert into sessions values(?,?,?)", (sid, ip, time.time()))
    request.state.session, request.state.ip = sid, ip
    if PUBLIC and request.method != "GET":
        if path.startswith("/api/settings"):
            return _json(403, "not available in the public demo")
        kind = next((k for k in ("run", "messages", "workspaces", "datasets") if path.rstrip("/").endswith(k)), None)
        for k in ([kind] if kind else []) + ["write"]:
            for key in (("ip", ip), ("session", sid)):
                if not _hit(key, k):
                    return _json(429, f"rate limit: {LIMITS[k][0]} {k} requests per hour in the public demo")
    m = WS_PATH.match(path)
    if m:
        ws = store.one("select owner from workspaces where id=?", (m.group(1),))
        if not ws or not owns(ws, sid):
            return _json(404, "workspace not found")
    resp = await call_next(request)
    if new:
        https = request.url.scheme == "https" or request.headers.get("x-forwarded-proto", "").lower() == "https"
        resp.set_cookie(COOKIE, new, max_age=30 * 86400, httponly=True, samesite="lax", secure=https, path="/")
    return resp


def ws_or_404(wid: str) -> dict:
    ws = store.one("select * from workspaces where id=?", (wid,))
    if not ws:
        raise HTTPException(404, "workspace not found")
    return ws


def bad(e: Exception):
    raise HTTPException(400, str(e))


# ---------- health & settings ----------
@app.get("/api/health")
def health():
    iso = studio.ex.isolation
    return {"r": R_VERSION, "executor": BACKEND, "isolation": backend_label(), "public": PUBLIC,
            "execution": {"enabled": studio.ex.allowed(), "self_test": iso["status"], "verified": iso["verified"],
                          "checks_passed": {k: c["ok"] for k, c in iso.get("checks", {}).items()}},
            "budgets": agent.budgets(),
            "llm": agent.config().public(),
            "acked_hosts": store.get_setting("acked_hosts", [])}


class LLMSettings(BaseModel):
    base_url: str
    model: str
    api_key: str | None = None
    temperature: float = 0.2


@app.put("/api/settings/llm")
def set_llm(s: LLMSettings):
    cur = store.get_setting("llm", {})
    new = {"base_url": s.base_url.strip().rstrip("/"), "model": s.model.strip(), "temperature": s.temperature,
           "api_key": cur.get("api_key", "") if s.api_key is None else s.api_key}
    store.set_setting("llm", new)
    return agent.config().public()


@app.post("/api/settings/llm/test")
async def test_llm():
    cfg = agent.config()
    if not cfg.base_url:
        return {"ok": False, "error": "no endpoint set"}
    try:
        models = await list_models(cfg)
        return {"ok": True, "models": models, "model_available": cfg.model in models}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


class Ack(BaseModel):
    host: str


@app.post("/api/settings/ack")
def ack(a: Ack):
    hosts = store.get_setting("acked_hosts", [])
    if a.host not in hosts:
        hosts.append(a.host)
    store.set_setting("acked_hosts", hosts)
    bus.emit("_global", "ack", host=a.host)
    return {"acked_hosts": hosts}


# ---------- workspaces ----------
class NewWorkspace(BaseModel):
    name: str


@app.get("/api/workspaces")
def workspaces(request: Request):
    rows = store.q("""select w.*, (select count(*) from datasets d where d.ws_id=w.id) datasets,
                      (select max(ts) from events e where e.ws_id=w.id) last_active
                      from workspaces w order by coalesce(last_active, created_at) desc""")
    return [{k: v for k, v in w.items() if k != "owner"} for w in rows if owns(w, request.state.session)]


@app.post("/api/workspaces")
def create_workspace(b: NewWorkspace, request: Request):
    ws = store.create_workspace(b.name.strip()[:120] or "Untitled analysis", owner=request.state.session)
    return {k: v for k, v in ws.items() if k != "owner"}


@app.get("/api/workspaces/{wid}")
def workspace(wid: str):
    ws = {k: v for k, v in ws_or_404(wid).items() if k != "owner"}
    run = studio.get_run(wid)
    specs = store.q("select * from specs where ws_id=? order by version", (wid,))
    for s in specs:
        s["spec"] = json.loads(s["spec"])
    return {
        "workspace": ws,
        "datasets": store.q("select * from datasets where ws_id=? order by created_at", (wid,)),
        "program": store.latest_program(wid),
        "programs": store.q("select version, author, summary, sha256, created_at from programs where ws_id=? order by version", (wid,)),
        "specs": specs,
        "run": run,
        "validation": studio.get_validation(wid, run["id"]) if run else None,
        "approvals": [{**a, "evidence": json.loads(a["evidence"]) if a.get("evidence") else None}
                      for a in store.q("select * from approvals where ws_id=? order by created_at", (wid,))],
        "verification": studio.verification_state(wid),
        "agent_busy": agent.busy(wid),
        "last_event": (store.one("select max(id) m from events where ws_id=?", (wid,)) or {}).get("m") or 0,
    }


# ---------- datasets ----------
@app.post("/api/workspaces/{wid}/datasets")
async def upload(wid: str, files: list[UploadFile] = File(...)):
    ws_or_404(wid)
    done, errors = [], []
    for f in files[:dsets.MAX_DATASETS]:
        too_big = False
        with tempfile.NamedTemporaryFile(delete=False, suffix=Path(f.filename or "").suffix) as tmp:
            size = 0
            while chunk := await f.read(1 << 20):  # stream to disk; stop at the size limit
                size += len(chunk)
                if size > dsets.MAX_UPLOAD_BYTES:
                    too_big = True
                    break
                tmp.write(chunk)
        try:
            if too_big:
                raise dsets.IngestError(f"{f.filename}: larger than the {dsets.MAX_UPLOAD_BYTES // 2**20} MB limit")
            d = await dsets.ingest(store, wid, Path(tmp.name), f.filename or "data.csv", ex=studio.ex)
            done.append(d)
            bus.emit(wid, "dataset_added", **d)
        except dsets.IngestError as e:
            errors.append({"file": f.filename, "error": str(e)})
        except asyncio.TimeoutError:
            errors.append({"file": f.filename, "error": "parsing timed out"})
        except IsolationError as e:
            raise HTTPException(503, str(e))
        finally:
            Path(tmp.name).unlink(missing_ok=True)
    return {"datasets": done, "errors": errors}


@app.post("/api/workspaces/{wid}/datasets/pilot")
async def pilot(wid: str):
    ws_or_404(wid)
    try:
        out = await dsets.load_pilot(store, wid, ex=studio.ex)
    except IsolationError as e:
        raise HTTPException(503, str(e))
    except Exception as e:
        raise HTTPException(502, f"could not load the CDISC pilot data: {e}")
    for d in out:
        bus.emit(wid, "dataset_added", **d)
    return {"datasets": out, "errors": []}


@app.get("/api/workspaces/{wid}/datasets/{name}/profile")
def profile(wid: str, name: str):
    try:
        return dsets.profile(store, wid, name)
    except FileNotFoundError:
        raise HTTPException(404, "no such dataset")


@app.get("/api/workspaces/{wid}/datasets/{name}/rows")
def rows(wid: str, name: str, offset: int = 0, limit: int = 100):
    try:
        return dsets.preview(store, wid, name, offset, min(limit, 500))
    except FileNotFoundError:
        raise HTTPException(404, "no such dataset")


@app.get("/api/workspaces/{wid}/relationships")
def relationships(wid: str):
    return dsets.relationships(store, wid)


# ---------- programs & runs ----------
class Code(BaseModel):
    code: str
    summary: str = ""


@app.get("/api/workspaces/{wid}/programs/{version}")
def program(wid: str, version: int):
    p = store.program(wid, version)
    if not p:
        raise HTTPException(404, "no such version")
    return p


@app.post("/api/workspaces/{wid}/programs")
def save_program(wid: str, b: Code):
    ws_or_404(wid)
    return studio.save_program(wid, b.code, "user", b.summary or "edited by the user")


class RunReq(BaseModel):
    version: int | None = None


@app.post("/api/workspaces/{wid}/run")
async def run(wid: str, b: RunReq):
    ws_or_404(wid)
    try:
        r = await studio.run(wid, by="user", version=b.version)
    except IsolationError as e:
        raise HTTPException(503, str(e))
    except ValueError as e:
        bad(e)
    return {k: v for k, v in r.items() if k != "console"}


@app.post("/api/workspaces/{wid}/cancel")
async def cancel(wid: str):
    a = await agent.cancel(wid)
    r = studio.ex.cancel(store.ws_path(wid))
    return {"agent_cancelled": a, "runs_cancelled": r}


@app.get("/api/workspaces/{wid}/runs")
def runs(wid: str):
    return store.q("select id, program_version, status, started, ended from runs where ws_id=? order by started desc", (wid,))


@app.get("/api/workspaces/{wid}/runs/{run_id}")
def get_run(wid: str, run_id: str):
    r = studio.get_run(wid, run_id)
    if not r:
        raise HTTPException(404, "no such run")
    return {**r, "validation": studio.get_validation(wid, run_id), "verification": studio.verification_state(wid, run_id)}


class TraceReq(BaseModel):
    run_id: str
    table_id: str
    level: str
    soc: str | None = None
    pt: str | None = None
    group: str


@app.post("/api/workspaces/{wid}/trace")
def trace(wid: str, b: TraceReq):
    try:
        return studio.trace(wid, b.run_id, b.table_id, b.level, b.soc, b.pt, b.group)
    except ValueError as e:
        bad(e)


# ---------- specs ----------
class SpecReq(BaseModel):
    spec: dict
    rationale: str = ""


@app.post("/api/workspaces/{wid}/specs")
def user_spec(wid: str, b: SpecReq):
    r = studio.propose_spec(wid, b.spec, b.rationale or "edited by the user", by="user")
    if not r["ok"]:
        raise HTTPException(400, "; ".join(r["problems"]))
    return r


class Decision(BaseModel):
    decision: str  # approve | reject
    note: str = ""


@app.post("/api/workspaces/{wid}/specs/{version}/decision")
def decide(wid: str, version: int, b: Decision):
    try:
        return studio.decide_spec(wid, version, b.decision, b.note)
    except ValueError as e:
        bad(e)


@app.post("/api/workspaces/{wid}/example")
async def example(wid: str):
    """Load the pilot data (if needed), then the built-in example spec (approved) and program, marked as the example."""
    ws_or_404(wid)
    if not {"ADSL", "ADAE"} <= {d["name"] for d in store.q("select name from datasets where ws_id=?", (wid,))}:
        await pilot(wid)
    r = studio.propose_spec(wid, ex.SPEC, "Built-in example specification (written by hand, not by the agent)", by="user")
    if not r["ok"]:
        raise HTTPException(400, "; ".join(r["problems"]))
    return {"spec": r, "program": studio.save_program(wid, ex.TEAE_R, "example", "built-in example program (written by hand, not by the agent)")}


# ---------- agent ----------
class Message(BaseModel):
    text: str
    context: dict | None = None


@app.post("/api/workspaces/{wid}/messages")
async def message(wid: str, b: Message, request: Request):
    ws_or_404(wid)
    try:
        agent.start(wid, b.text[:8000], b.context, who={"session": request.state.session, "ip": request.state.ip})
    except QuotaError as e:
        raise HTTPException(429, str(e))
    except RuntimeError as e:
        raise HTTPException(409, str(e))
    return {"started": True}


# ---------- sign-off & export ----------
class SignOff(BaseModel):
    run_id: str
    reviewer: str
    decision: str  # approved | changes_requested
    note: str = ""
    confirmed: bool = False  # the reviewer confirms they reviewed spec, program and results themselves


@app.post("/api/workspaces/{wid}/signoff")
def signoff(wid: str, b: SignOff, request: Request):
    try:
        return studio.approve(wid, b.run_id, b.reviewer, b.decision, b.note, b.confirmed, request.state.session)
    except ApprovalError as e:
        raise HTTPException(e.status, str(e))


@app.get("/api/workspaces/{wid}/export")
def export(wid: str, run_id: str | None = None):
    try:
        data = studio.export(wid, run_id)
    except ValueError as e:
        bad(e)
    return Response(data, media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="trialproof-{wid}.zip"'})


@app.get("/api/workspaces/{wid}/log")
def event_log(wid: str, after: int = 0, limit: int = 2000):
    """The workspace's event log after an event id, as JSON (the same events the live stream sends)."""
    ws_or_404(wid)
    return store.events(wid, after)[: max(1, min(limit, 5000))]


@app.get("/api/workspaces/{wid}/events")
async def events(wid: str, request: Request, after: int = 0):
    async def gen():
        q = bus.subscribe(wid)
        try:
            for ev in store.events(wid, after):
                yield f"data: {json.dumps(ev, default=str)}\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=15)
                    yield f"data: {json.dumps(ev, default=str)}\n\n"
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
        finally:
            bus.unsubscribe(wid, q)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


DIST = ROOT / "web" / "dist"
if DIST.exists():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/{path:path}")
    def spa(path: str):
        f = DIST / path
        return FileResponse(f if path and f.is_file() else DIST / "index.html")
