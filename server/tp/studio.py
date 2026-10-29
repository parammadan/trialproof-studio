"""Workspace operations shared by the HTTP API (the user) and the agent's tools (the model): program
versions, runs, specifications, validation, lineage, sign-off and the export bundle."""
from __future__ import annotations

import csv
import difflib
import hashlib
import io
import json
import time
import uuid
import zipfile

from . import datasets as dsets
from . import reference as ref
from .bus import Bus
from .executor import Executor
from .store import Store


def _sha(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def unified_diff(a: str, b: str, va, vb) -> str:
    return "".join(difflib.unified_diff(a.splitlines(True), b.splitlines(True), f"program.R v{va}", f"program.R v{vb}"))


class Studio:
    def __init__(self, store: Store, bus: Bus, executor: Executor):
        self.store, self.bus, self.ex = store, bus, executor

    # ---------- programs ----------
    def save_program(self, wid: str, code: str, author: str, summary: str = "") -> dict:
        prev = self.store.latest_program(wid)
        if prev and prev["code"] == code:
            return prev
        version = (prev["version"] + 1) if prev else 1
        self.store.x("insert into programs(ws_id, version, author, code, sha256, summary, created_at) values(?,?,?,?,?,?,?)",
                     (wid, version, author, code, _sha(code), summary, time.time()))
        diff = unified_diff(prev["code"], code, prev["version"], version) if prev else ""
        added = sum(1 for ln in diff.splitlines() if ln.startswith("+") and not ln.startswith("+++"))
        removed = sum(1 for ln in diff.splitlines() if ln.startswith("-") and not ln.startswith("---"))
        self.bus.emit(wid, "program_version", version=version, author=author, summary=summary, sha256=_sha(code),
                      previous=prev["version"] if prev else None, added=added, removed=removed)
        return self.store.program(wid, version)

    # ---------- specs ----------
    def columns(self, wid: str) -> dict[str, set[str]]:
        out = {}
        for d in self.store.q("select name from datasets where ws_id=?", (wid,)):
            out[d["name"]] = {c["name"] for c in dsets.profile(self.store, wid, d["name"])["columns"]}
        return out

    def propose_spec(self, wid: str, spec: dict, rationale: str, by: str) -> dict:
        problems = ref.check_spec(spec, self.columns(wid))
        if problems:
            return {"ok": False, "problems": problems}
        last = self.store.one("select max(version) v from specs where ws_id=?", (wid,))
        version = (last["v"] or 0) + 1
        # Only one open proposal at a time: a newer one supersedes it
        self.store.x("update specs set status='superseded' where ws_id=? and status='proposed'", (wid,))
        status = "approved" if by == "user" else "proposed"
        now = time.time()
        self.store.x("insert into specs(ws_id, version, spec, status, rationale, proposed_by, decided_by, created_at, decided_at) values(?,?,?,?,?,?,?,?,?)",
                     (wid, version, json.dumps(spec), status, rationale, by, "user" if by == "user" else None, now, now if by == "user" else None))
        prev = self.store.approved_spec(wid) if status == "proposed" else None
        self.bus.emit(wid, "spec_proposed" if status == "proposed" else "spec_decided", version=version, spec=spec,
                      rationale=rationale, by=by, status=status, previous=prev["version"] if prev else None)
        return {"ok": True, "version": version, "status": status}

    def decide_spec(self, wid: str, version: int, decision: str, note: str = "") -> dict:
        s = self.store.spec(wid, version)
        if not s or s["status"] != "proposed":
            raise ValueError(f"spec v{version} is not awaiting a decision")
        status = "approved" if decision == "approve" else "rejected"
        self.store.x("update specs set status=?, decided_by='user', decided_at=? where ws_id=? and version=?",
                     (status, time.time(), wid, version))
        self.bus.emit(wid, "spec_decided", version=version, status=status, note=note, spec=s["spec"])
        return {"version": version, "status": status}

    # ---------- runs ----------
    async def run(self, wid: str, by: str, version: int | None = None) -> dict:
        prog = self.store.program(wid, version) if version else self.store.latest_program(wid)
        if not prog:
            raise ValueError("there is no program to run yet")

        def on_event(kind, payload):
            self.bus.emit(wid, kind, program_version=prog["version"], by=by, **payload)

        res = await self.ex.run(self.store.ws_path(wid), prog["code"], on_event)
        self.store.x("insert into runs values(?,?,?,?,?,?,?,?,?,?)",
                     (res.run_id, wid, prog["version"], res.status, time.time() - res.duration, time.time(),
                      res.console, json.dumps(res.result), json.dumps(res.tables), json.dumps(res.provenance)))
        self.bus.emit(wid, "run_end", run_id=res.run_id, program_version=prog["version"], by=by, status=res.status,
                      error=None if res.ok else res.result, duration=round(res.duration, 2),
                      tables=[{"id": t["id"], "title": t.get("title"), "type": t.get("type"), "rows": len(t["rows"])} for t in res.tables],
                      warnings=res.result.get("warnings") or [])
        out = {"run_id": res.run_id, "status": res.status, "program_version": prog["version"], "console": res.console,
               "error": None if res.ok else res.result, "tables": res.tables, "warnings": res.result.get("warnings") or []}
        if res.ok and any(t.get("type") == "teae_summary" for t in res.tables):
            out["validation"] = self.validate(wid, res.run_id)
        return out

    def get_run(self, wid: str, run_id: str | None = None) -> dict | None:
        r = (self.store.one("select * from runs where ws_id=? and id=?", (wid, run_id)) if run_id else
             self.store.one("select * from runs where ws_id=? order by started desc limit 1", (wid,)))
        if not r:
            return None
        for k in ("result", "tables", "provenance"):
            r[k] = json.loads(r[k])
        return r

    # ---------- validation ----------
    def _reference(self, wid: str, spec: dict) -> dict:
        subj = dsets.read_rows(self.store, wid, spec["datasets"]["subject"])
        ev = dsets.read_rows(self.store, wid, spec["datasets"]["events"])
        return ref.teae_reference(spec, subj, ev)

    def validate(self, wid: str, run_id: str) -> dict:
        run = self.get_run(wid, run_id)
        spec_row = self.store.approved_spec(wid)
        vid = uuid.uuid4().hex[:10]
        tables = []
        for t in run["tables"]:
            if t.get("type") != "teae_summary" or not spec_row:
                tables.append({"table_id": t["id"], "status": "not_verified", "passed": 0, "mismatch": 0,
                               "not_verified": len(t["rows"]), "cells": [],
                               "reason": "no approved specification" if t.get("type") == "teae_summary"
                               else f"no independent reference implementation for table type {t.get('type')!r}"})
                continue
            cmp = ref.compare(self._reference(wid, spec_row["spec"]), t)
            tables.append({"table_id": t["id"], "status": "passed" if cmp["mismatch"] == 0 else "mismatch", **cmp})
        summary = {"validation_id": vid, "run_id": run_id, "spec_version": spec_row["version"] if spec_row else None,
                   "program_version": run["program_version"],
                   "tables": [{k: v for k, v in t.items() if k != "cells"} for t in tables]}
        self.store.x("insert into validations values(?,?,?,?,?,?,?)",
                     (vid, wid, run_id, summary["spec_version"], json.dumps(summary), json.dumps(tables), time.time()))
        self.bus.emit(wid, "validation", **summary)
        return summary

    def get_validation(self, wid: str, run_id: str) -> dict | None:
        v = self.store.one("select * from validations where ws_id=? and run_id=? order by created_at desc limit 1", (wid, run_id))
        if not v:
            return None
        return {**json.loads(v["summary"]), "tables": json.loads(v["cells"])}

    # ---------- lineage ----------
    def trace(self, wid: str, run_id: str, table_id: str, level: str, soc: str | None, pt: str | None, group: str) -> dict:
        run = self.get_run(wid, run_id)
        spec_row = self.store.approved_spec(wid)
        table = next((t for t in run["tables"] if t["id"] == table_id), None)
        if not table:
            raise ValueError(f"run {run_id} has no table {table_id}")
        prog = self.store.program(wid, run["program_version"])
        soc = soc if level in ("soc", "pt") else None
        pt = pt if level == "pt" else None
        row = next((r for r in table["rows"] if r.get("level") == level and (r.get("soc") or None) == soc
                    and (r.get("pt") or None) == pt and r.get("group") == group), None)
        out = {"run_id": run_id, "table_id": table_id, "program_version": run["program_version"],
               "cell": {"level": level, "soc": soc, "pt": pt, "group": group},
               "program_value": {"n": row.get("n"), "N": row.get("N")} if row else None}
        if not spec_row or table.get("type") != "teae_summary":
            out["reference"] = None
            out["note"] = "No approved specification for this table, so there is no independent derivation to trace."
            return out
        spec = spec_row["spec"]
        r = self._reference(wid, spec)
        cell = r["cells"].get((level, soc, pt, group))
        events = dsets.read_rows(self.store, wid, spec["datasets"]["events"])
        sid = spec.get("subject_id", "USUBJID")
        sample_cols = [c for c in (sid, "AESEQ", spec["pt_var"], spec["soc_var"], "ASTDT", *[c["var"] for c in spec.get("event_filter") or []]) if c in (events[0] if events else {})]
        recs = [{c: events[i].get(c) for c in dict.fromkeys(sample_cols)} for i in (cell["records"] if cell else [])[:25]]
        what = {"any": "any qualifying event", "soc": f'an event in SOC "{soc}"', "pt": f'the preferred term "{pt}" (SOC "{soc}")'}[level]
        out.update({
            "spec_version": spec_row["version"],
            "source": {"subject": spec["datasets"]["subject"], "events": spec["datasets"]["events"]},
            "population": [ref.describe(c) for c in spec.get("population") or []] or ["all subjects"],
            "group": f'{spec["treatment_var"]} == "{group}"' if group != ref.TOTAL else "all groups",
            "event_filter": [ref.describe(c) for c in spec.get("event_filter") or []],
            "numerator": f"distinct {sid} in {spec['datasets']['events']} with {what}, in the population and group",
            "denominator": f"distinct {sid} in {spec['datasets']['subject']} meeting the population definition, in the group",
            "reference": {"n": cell["n"] if cell else 0, "N": cell["N"] if cell else r["cells"][("any", None, None, group)]["N"],
                          "subjects": (cell or {}).get("subjects", []), "record_count": len((cell or {}).get("records", []))},
            "records_sample": recs,
            "code_lines": ref.code_lines(prog["code"], spec),
        })
        return out

    # ---------- sign-off ----------
    def approve(self, wid: str, run_id: str, reviewer: str, decision: str, note: str) -> dict:
        v = self.store.one("select * from validations where ws_id=? and run_id=? order by created_at desc limit 1", (wid, run_id))
        if not reviewer.strip():
            raise ValueError("a reviewer name is required")
        aid = uuid.uuid4().hex[:10]
        self.store.x("insert into approvals values(?,?,?,?,?,?,?,?)",
                     (aid, wid, run_id, v["id"] if v else None, reviewer.strip(), decision, note, time.time()))
        run = self.get_run(wid, run_id)
        self.bus.emit(wid, "signoff", approval_id=aid, run_id=run_id, reviewer=reviewer.strip(), decision=decision,
                      note=note, program_version=run["program_version"], program_sha256=run["provenance"]["program_sha256"],
                      validation=json.loads(v["summary"]) if v else None)
        return {"approval_id": aid}

    # ---------- export ----------
    def export(self, wid: str, run_id: str | None = None) -> bytes:
        run = self.get_run(wid, run_id)
        if not run:
            raise ValueError("nothing has been run yet")
        prog = self.store.program(wid, run["program_version"])
        spec = self.store.approved_spec(wid)
        val = self.get_validation(wid, run["id"])
        ws = self.store.one("select * from workspaces where id=?", (wid,))
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("program.R", prog["code"])
            for f in ("prelude.R", "runner.R"):
                z.write(dsets.R_DIR / f, f"r/{f}")
            z.writestr("console.txt", run["console"])
            if spec:
                z.writestr("analysis_spec.json", json.dumps(spec, indent=2, default=str))
            for t in run["tables"]:
                z.writestr(f"results/{t['id']}.json", json.dumps(t, indent=2))
                s = io.StringIO()
                w = csv.DictWriter(s, fieldnames=t["columns"])
                w.writeheader()
                w.writerows(t["rows"])
                z.writestr(f"results/{t['id']}.csv", s.getvalue())
            if val:
                z.writestr("validation.json", json.dumps(val, indent=2))
            z.writestr("provenance.json", json.dumps({"workspace": ws, "run_id": run["id"], "status": run["status"],
                                                     "program_version": run["program_version"], **run["provenance"],
                                                     "datasets": self.store.q("select name, filename, sha256, nrow, ncol, source from datasets where ws_id=?", (wid,))}, indent=2))
            z.writestr("audit_log.jsonl", "\n".join(json.dumps(e, default=str) for e in self.store.events(wid)))
            z.writestr("README.txt", (
                "TrialProof Studio export\n\n"
                "Re-run: place each dataset's parsed copy at data/<NAME>/data.rds, set TP_DATA_DIR and TP_OUT_DIR,\n"
                "then `Rscript r/runner.R program.R` (r/ holds the prelude and runner used for this run).\n"
                "provenance.json holds the SHA-256 of the program and of every source dataset.\n\n"
                "This is not validated software. A passing comparison means the program agrees with the\n"
                "independent reference computed from the approved specification; it does not establish that\n"
                "the specification or the analysis is correct, nor fitness for regulatory use.\n"))
        return buf.getvalue()
