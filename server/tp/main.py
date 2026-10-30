"""TrialProof Studio API (FastAPI). Run: uvicorn tp.main:app --port 8765"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import time
from collections import defaultdict, deque
import tempfile
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import datasets as dsets
from .agent import Agent
from .bus import Bus
from .executor import BACKEND, Executor, backend_label
from .llm import LLMConfig, list_models
from .store import ROOT, Store
from .studio import Studio

store = Store()
bus = Bus(store)
studio = Studio(store, bus, Executor())
agent = Agent(studio)
app = FastAPI(title="TrialProof Studio")

# Public demo mode (TP_PUBLIC=1): pilot data only, model settings fixed by the server, per-visitor rate limits
PUBLIC = os.environ.get("TP_PUBLIC") == "1"
LIMITS = {"run": (40, 3600), "messages": (20, 3600), "workspaces": (10, 3600)}  # (requests, per seconds)
_hits: dict[tuple[str, str], deque] = defaultdict(deque)
if PUBLIC and agent.config().configured:
    store.set_setting("acked_hosts", [agent.config().host])  # the operator chose this endpoint for public data


@app.middleware("http")
async def public_guard(request: Request, call_next):
    if PUBLIC and request.method != "GET":
        path = request.url.path
        if path.startswith("/api/settings") or (path.endswith("/datasets") and request.method == "POST"):
            return Response(json.dumps({"detail": "not available in the public demo"}), 403, media_type="application/json")
        kind = next((k for k in LIMITS if path.rstrip("/").endswith(k)), None)
        if kind:
            ip = (request.headers.get("x-forwarded-for") or (request.client.host if request.client else "?")).split(",")[0].strip()
            n, per = LIMITS[kind]
            q, now = _hits[(ip, kind)], time.time()
            while q and q[0] < now - per:
                q.popleft()
            if len(q) >= n:
                return Response(json.dumps({"detail": f"rate limit: {n} {kind} requests per hour in the public demo"}), 429,
                                media_type="application/json")
            q.append(now)
    return await call_next(request)


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
    r = subprocess.run(["Rscript", "--version"], capture_output=True, text=True)
    return {"r": (r.stdout or r.stderr).strip(), "executor": BACKEND, "isolation": backend_label(), "public": PUBLIC,
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
def workspaces():
    return store.q("""select w.*, (select count(*) from datasets d where d.ws_id=w.id) datasets,
                      (select max(ts) from events e where e.ws_id=w.id) last_active
                      from workspaces w order by coalesce(last_active, created_at) desc""")


@app.post("/api/workspaces")
def create_workspace(b: NewWorkspace):
    return store.create_workspace(b.name.strip() or "Untitled analysis")


@app.get("/api/workspaces/{wid}")
def workspace(wid: str):
    ws = ws_or_404(wid)
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
        "approvals": store.q("select * from approvals where ws_id=? order by created_at", (wid,)),
        "agent_busy": agent.busy(wid),
        "last_event": (store.one("select max(id) m from events where ws_id=?", (wid,)) or {}).get("m") or 0,
    }


# ---------- datasets ----------
@app.post("/api/workspaces/{wid}/datasets")
async def upload(wid: str, files: list[UploadFile] = File(...)):
    ws_or_404(wid)
    done, errors = [], []
    for f in files:
        with tempfile.NamedTemporaryFile(delete=False, suffix=Path(f.filename or "").suffix) as tmp:
            tmp.write(await f.read())
        try:
            d = await dsets.ingest(store, wid, Path(tmp.name), f.filename or "data.csv")
            done.append(d)
            bus.emit(wid, "dataset_added", **d)
        except dsets.IngestError as e:
            errors.append({"file": f.filename, "error": str(e)})
        except asyncio.TimeoutError:
            errors.append({"file": f.filename, "error": "parsing timed out"})
        finally:
            Path(tmp.name).unlink(missing_ok=True)
    return {"datasets": done, "errors": errors}


@app.post("/api/workspaces/{wid}/datasets/pilot")
async def pilot(wid: str):
    ws_or_404(wid)
    try:
        out = await dsets.load_pilot(store, wid)
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
    return dsets.preview(store, wid, name, offset, min(limit, 500))


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
    return {**r, "validation": studio.get_validation(wid, run_id)}


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


# ---------- agent ----------
class Message(BaseModel):
    text: str
    context: dict | None = None


@app.post("/api/workspaces/{wid}/messages")
async def message(wid: str, b: Message):
    ws_or_404(wid)
    try:
        agent.start(wid, b.text, b.context)
    except RuntimeError as e:
        raise HTTPException(409, str(e))
    return {"started": True}


# ---------- sign-off & export ----------
class SignOff(BaseModel):
    run_id: str
    reviewer: str
    decision: str  # approved | changes_requested
    note: str = ""


@app.post("/api/workspaces/{wid}/signoff")
def signoff(wid: str, b: SignOff):
    try:
        return studio.approve(wid, b.run_id, b.reviewer, b.decision, b.note)
    except ValueError as e:
        bad(e)


@app.get("/api/workspaces/{wid}/export")
def export(wid: str, run_id: str | None = None):
    try:
        data = studio.export(wid, run_id)
    except ValueError as e:
        bad(e)
    return Response(data, media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="trialproof-{wid}.zip"'})


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
