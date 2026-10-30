"""SQLite persistence: workspaces, datasets, program versions, specs, runs, validations, approvals and
the event log (every action in a workspace, replayed to the UI and exported as the audit trail)."""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VAR = Path(os.environ["TP_VAR"]).resolve() if os.environ.get("TP_VAR") else ROOT / "var"
WS_DIR = VAR / "workspaces"

SCHEMA = """
create table if not exists workspaces(id text primary key, name text, created_at real);
create table if not exists datasets(id text primary key, ws_id text, name text, filename text, format text,
  sha256 text, nrow int, ncol int, source text, created_at real);
create table if not exists programs(id integer primary key, ws_id text, version int, author text, code text,
  sha256 text, summary text, created_at real);
create table if not exists specs(id integer primary key, ws_id text, version int, spec text, status text,
  rationale text, proposed_by text, decided_by text, created_at real, decided_at real);
create table if not exists runs(id text primary key, ws_id text, program_version int, status text, started real,
  ended real, console text, result text, tables text, provenance text);
create table if not exists validations(id text primary key, ws_id text, run_id text, spec_version int,
  summary text, cells text, created_at real);
create table if not exists approvals(id text primary key, ws_id text, run_id text, validation_id text,
  reviewer text, decision text, note text, created_at real);
create table if not exists chat(id integer primary key, ws_id text, message text);
create table if not exists events(id integer primary key, ws_id text, ts real, kind text, payload text);
create table if not exists settings(key text primary key, value text);
"""


class Store:
    def __init__(self, path: Path | None = None):
        VAR.mkdir(exist_ok=True)
        WS_DIR.mkdir(exist_ok=True)
        self.path = path or VAR / "tp.db"
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self.lock = threading.Lock()

    def q(self, sql: str, args=()) -> list[dict]:
        with self.lock:
            return [dict(r) for r in self.db.execute(sql, args).fetchall()]

    def one(self, sql: str, args=()) -> dict | None:
        rows = self.q(sql, args)
        return rows[0] if rows else None

    def x(self, sql: str, args=()) -> int:
        with self.lock:
            cur = self.db.execute(sql, args)
            self.db.commit()
            return cur.lastrowid

    # workspaces
    def create_workspace(self, name: str) -> dict:
        wid = uuid.uuid4().hex[:10]
        self.x("insert into workspaces values(?,?,?)", (wid, name, time.time()))
        (WS_DIR / wid / "datasets").mkdir(parents=True, exist_ok=True)
        (WS_DIR / wid / "runs").mkdir(parents=True, exist_ok=True)
        return self.one("select * from workspaces where id=?", (wid,))

    def ws_path(self, wid: str) -> Path:
        return WS_DIR / wid

    # programs
    def latest_program(self, wid: str) -> dict | None:
        return self.one("select * from programs where ws_id=? order by version desc limit 1", (wid,))

    def program(self, wid: str, version: int) -> dict | None:
        return self.one("select * from programs where ws_id=? and version=?", (wid, version))

    # specs
    def approved_spec(self, wid: str) -> dict | None:
        r = self.one("select * from specs where ws_id=? and status='approved' order by version desc limit 1", (wid,))
        if r:
            r["spec"] = json.loads(r["spec"])
        return r

    def spec(self, wid: str, version: int) -> dict | None:
        r = self.one("select * from specs where ws_id=? and version=?", (wid, version))
        if r:
            r["spec"] = json.loads(r["spec"])
        return r

    # chat history sent to the model
    def chat(self, wid: str) -> list[dict]:
        return [json.loads(r["message"]) for r in self.q("select message from chat where ws_id=? order by id", (wid,))]

    def add_chat(self, wid: str, msg: dict):
        self.x("insert into chat(ws_id, message) values(?,?)", (wid, json.dumps(msg)))

    # settings
    def get_setting(self, key: str, default=None):
        r = self.one("select value from settings where key=?", (key,))
        return json.loads(r["value"]) if r else default

    def set_setting(self, key: str, value):
        self.x("insert into settings values(?,?) on conflict(key) do update set value=excluded.value", (key, json.dumps(value)))

    # events
    def add_event(self, wid: str, kind: str, payload: dict) -> dict:
        ts = time.time()
        eid = self.x("insert into events(ws_id, ts, kind, payload) values(?,?,?,?)", (wid, ts, kind, json.dumps(payload, default=str)))
        return {"id": eid, "ts": ts, "kind": kind, "payload": payload}

    def events(self, wid: str, after: int = 0) -> list[dict]:
        rows = self.q("select * from events where ws_id=? and id>? order by id", (wid, after))
        return [{"id": r["id"], "ts": r["ts"], "kind": r["kind"], "payload": json.loads(r["payload"])} for r in rows]
