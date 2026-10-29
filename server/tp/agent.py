"""The AI statistical programmer: an LLM with function calling, driving real tools over the workspace.

Every event the UI shows comes from this loop as it happens: the model's text as it streams, each tool
call with its arguments, the code as it is written (decoded from the streamed tool arguments), and each
tool's actual result. Nothing is replayed or scripted.
"""
from __future__ import annotations

import asyncio
import json
import re
import time
import traceback

from . import datasets as dsets
from .llm import LLMConfig, LLMError, PartialString, ToolCall, stream_chat
from .studio import Studio, unified_diff

MAX_STEPS = 14
MAX_CALLS = 4  # tool calls run per model step
CONTEXT_CHARS = 60000  # ~17k tokens of history, leaving room for the system prompt and output

SYSTEM = """You are the AI statistical programmer inside TrialProof Studio, working with a human statistical programmer on clinical-trial analysis datasets (CDISC ADaM and others) in R.

How you work:
1. Inspect before you write. Use list_datasets, inspect_dataset, tabulate and relationships to find the variables you need. Never guess a variable name, a flag value or a treatment label; check it.
2. For a TEAE (treatment-emergent adverse event) summary, define the analysis as a specification with propose_spec, then STOP: the user must approve it in the Spec tab. Do not write the program for that analysis until a specification is approved. If the population, treatment variable, TEAE flag or coding variables are ambiguous, say what you found and ask (ask_user), or propose the most standard choice and explain the alternatives in the rationale.
3. Never change an approved specification on your own. If the user asks for a different population or filter, propose a new specification version and wait for approval.
4. Write the complete program with write_program (it replaces the file; the user sees a diff). Then run_program. If it fails, read the error and its line numbers, fix the code and rerun. If the run's validation shows mismatches, investigate before claiming anything: get_validation (read its pattern), compare_with_last_passing (if an earlier version passed, the cause is usually in that diff), then trace_cell and read_program. Fix with the smallest change that explains the pattern, using edit_program (an exact text replacement), not a full rewrite; do not restructure a program that was passing. The reference is computed by an independent engine directly from the approved specification, so a mismatch means the program does not compute what the spec says; never conclude that the reference is wrong (if you believe the spec itself is wrong, say so and ask_user). Pattern facts that name a spec variable the program never mentions point at the line to fix. Investigate with tabulate and trace_cell rather than replacing the analysis program with diagnostic code.
5. Every number you state must come from a tool result in this conversation. Do not invent, estimate or round numbers that no tool returned.
6. When explaining results, separate description (counts and percentages observed) from inference. A difference in percentages is descriptive; do not call it significant or causal unless an appropriate test was actually run, and note its assumptions.
7. Call tools only through function calling, one step at a time, and wait for each result. Never describe what a tool returned before you have its result.
8. Be brief. The user sees your tool calls live; do not narrate them at length. No hidden reasoning is shown, so give short, checkable explanations.

R program rules:
- Read data only with tp_read("NAME") (e.g. adsl <- tp_read("ADSL")). There is no network access and no other file access.
- dplyr and tidyr are loaded. Use tidyverse style. Base R is fine.
- Publish results with tp_emit_table(df, id = "...", title = "...", type = "..."). For a TEAE summary use type = "teae_summary" with a long data frame having exactly these columns:
    level  "any" (subjects with at least one TEAE), "soc" (by system organ class) or "pt" (by preferred term within SOC)
    soc    the SOC text (NA when level is "any")
    pt     the preferred term (NA unless level is "pt")
    group  the treatment group label exactly as in the data
    n      number of distinct subjects with the event in that group
    N      number of subjects in the group's population denominator (from the subject-level dataset, not from the AE dataset)
    pct    100 * n / N (optional)
  Count subjects, not events (n_distinct of the subject id). Take the denominator from the population in ADSL so that groups with no events still have N. Apply the population to the AE records too (join to the population subjects).
- Other outputs use type = "generic"; they are shown but not independently verified.
- Top-level expressions are executed one at a time and errors are reported with their line range.

Specification format (propose_spec):
{"type": "teae_summary", "title": "...",
 "datasets": {"subject": "ADSL", "events": "ADAE"}, "subject_id": "USUBJID",
 "population": [{"var": "SAFFL", "op": "eq", "value": "Y"}],
 "treatment_var": "TRT01A",
 "event_filter": [{"var": "TRTEMFL", "op": "eq", "value": "Y"}],
 "soc_var": "AEBODSYS", "pt_var": "AEDECOD", "include_total": false}
ops: eq, ne, in, not_in (value is a list), missing, not_missing, gt, ge, lt, le. Population conditions apply to the subject dataset; event_filter conditions apply to the events dataset. If include_total is true, also emit group = "Total".
"""


def _fn(name, desc, props=None, required=None):
    return {"type": "function", "function": {"name": name, "description": desc, "parameters": {
        "type": "object", "properties": props or {}, "required": required or []}}}


COND = {"type": "object", "properties": {"var": {"type": "string"}, "op": {"type": "string"}, "value": {}}, "required": ["var", "op"]}
TOOLS = [
    _fn("list_datasets", "List the workspace datasets with row and column counts."),
    _fn("inspect_dataset", "Schema of one dataset: each column's label, type, missing count, distinct count and most common values. Pass columns to limit the output.",
        {"name": {"type": "string"}, "columns": {"type": "array", "items": {"type": "string"}}}, ["name"]),
    _fn("tabulate", "Frequency counts of one column (optionally crossed with a second column, `by`).",
        {"dataset": {"type": "string"}, "column": {"type": "string"}, "by": {"type": "string"}}, ["dataset", "column"]),
    _fn("relationships", "Shared key columns between datasets and how many child keys match the parent."),
    _fn("get_spec", "The approved analysis specification and any proposal awaiting the user's decision."),
    _fn("propose_spec", "Propose an analysis specification (or a new version of it). The user must approve it; stop after calling this.",
        {"spec": {"type": "object", "properties": {
            "type": {"type": "string"}, "title": {"type": "string"},
            "datasets": {"type": "object", "properties": {"subject": {"type": "string"}, "events": {"type": "string"}}},
            "subject_id": {"type": "string"}, "population": {"type": "array", "items": COND},
            "treatment_var": {"type": "string"}, "event_filter": {"type": "array", "items": COND},
            "soc_var": {"type": "string"}, "pt_var": {"type": "string"}, "include_total": {"type": "boolean"}}},
         "rationale": {"type": "string", "description": "Why these choices, citing what you found in the data, and what alternatives exist."}},
        ["spec", "rationale"]),
    _fn("ask_user", "Ask the user a clarifying question when a choice is genuinely theirs. Stop after calling this.",
        {"question": {"type": "string"}, "options": {"type": "array", "items": {"type": "string"}}}, ["question"]),
    _fn("read_program", "The current program with line numbers (the user may have edited it)."),
    _fn("write_program", "Replace program.R with complete new code. The user sees it stream into the editor and a diff against the previous version.",
        {"summary": {"type": "string", "description": "One line: what this version does or changes."},
         "code": {"type": "string", "description": "The complete R program."}}, ["summary", "code"]),
    _fn("edit_program", "Change part of program.R by exact text replacement: old_text must occur exactly once in the current program. Prefer this over write_program for fixes; the user sees the diff.",
        {"summary": {"type": "string", "description": "One line: what this change fixes."},
         "old_text": {"type": "string", "description": "Exact text from the current program (copy it from read_program without the line numbers)."},
         "new_text": {"type": "string", "description": "The replacement text."}}, ["summary", "old_text", "new_text"]),
    _fn("run_program", "Run the current program in the sandbox. Returns console output, errors with line numbers, emitted tables, and the independent validation of teae_summary tables."),
    _fn("get_validation", "The latest validation result for the latest run, listing mismatched cells first."),
    _fn("compare_with_last_passing", "Diff between the current program and the newest version that passed validation with no mismatches. Use it first when a previously passing analysis starts failing."),
    _fn("trace_cell", "Lineage of one result cell from the independent reference: population, denominator, numerator definition, contributing subjects and records, and program lines that mention the variables.",
        {"table_id": {"type": "string"}, "level": {"type": "string", "enum": ["any", "soc", "pt"]},
         "soc": {"type": "string"}, "pt": {"type": "string"}, "group": {"type": "string"}}, ["table_id", "level", "group"]),
    _fn("highlight_code", "Highlight lines of program.R in the user's editor with a short note (to show which code is responsible for something).",
        {"start_line": {"type": "integer"}, "end_line": {"type": "integer"}, "note": {"type": "string"}}, ["start_line", "end_line", "note"]),
]
PAUSE = {"propose_spec", "ask_user"}


def _text_calls(text: str) -> list[tuple[dict, str]]:
    """JSON objects in the text that name a known tool, e.g. {"name": "inspect_dataset", "arguments": {...}}."""
    names = {t["function"]["name"] for t in TOOLS}
    dec = json.JSONDecoder()
    out, i = [], 0
    while (i := text.find("{", i)) >= 0:
        try:
            obj, end = dec.raw_decode(text, i)
        except json.JSONDecodeError:
            i += 1
            continue
        if isinstance(obj, dict) and obj.get("name") in names and isinstance(obj.get("arguments", {}), dict):
            out.append((obj, text[i:end]))
        i = end
    return out


def _clip(s: str, n: int) -> str:
    return s if len(s) <= n else s[: n // 2] + f"\n... [{len(s) - n} chars clipped] ...\n" + s[-n // 2:]


def _drop_code(args: str) -> str:
    """Replace the code of an old write_program call (kept as valid JSON) to save context."""
    try:
        a = json.loads(args or "{}")
    except json.JSONDecodeError:
        return "{}"
    if isinstance(a, dict) and len(a.get("code") or "") > 300:
        a["code"] = "[older program text omitted; call read_program for the current version]"
    return json.dumps(a)


TOOL_NAMES = {t["function"]["name"] for t in TOOLS} - {"tabulate"}  # tabulate() is also base R
SPECIAL = re.compile(r"<\|(?:im_start|im_end|endoftext)\|>")
FIX_ASK = re.compile(r"\b(fix|correct|repair|resolve|debug)\w*", re.I)
MAX_NUDGES = 2


class Agent:
    def __init__(self, studio: Studio):
        self.studio = studio
        self.store = studio.store
        self.bus = studio.bus
        self.tasks: dict[str, asyncio.Task] = {}

    def config(self) -> LLMConfig:
        return LLMConfig.from_env(self.store.get_setting("llm", {}))

    def busy(self, wid: str) -> bool:
        t = self.tasks.get(wid)
        return bool(t and not t.done())

    def start(self, wid: str, text: str, context: dict | None = None) -> None:
        if self.busy(wid):
            raise RuntimeError("the agent is already working in this workspace; cancel it or wait")
        self.tasks[wid] = asyncio.create_task(self._turn(wid, text, context))

    async def cancel(self, wid: str) -> bool:
        t = self.tasks.get(wid)
        if not t or t.done():
            return False
        t.cancel()
        self.studio.ex.cancel(self.store.ws_path(wid))
        try:
            await t
        except (asyncio.CancelledError, Exception):
            pass
        return True

    # ---------- the loop ----------
    def _messages(self, wid: str) -> list[dict]:
        hist = self.store.chat(wid)
        # Keep the context small: old tool results are clipped (the model can call the tool again), and the
        # oldest turns are dropped once the history passes the budget, always starting at a user message
        out = []
        for i, m in enumerate(hist):
            if m["role"] == "tool" and i < len(hist) - 12:
                m = {**m, "content": _clip(m["content"], 400)}
            out.append(m)
        size = lambda ms: sum(len(json.dumps(m)) for m in ms)
        while size(out) > CONTEXT_CHARS and len(out) > 2:
            nxt = next((j for j in range(1, len(out)) if out[j]["role"] == "user"), None)
            if nxt is None:
                break
            out = out[nxt:]
        # A single long turn has only one user message to cut at: clip its older tool results and program
        # bodies, keeping the newest few intact
        for keep in (6, 3, 1):
            if size(out) <= CONTEXT_CHARS:
                break
            for i, m in enumerate(out[:-keep]):
                if m["role"] == "tool":
                    out[i] = {**m, "content": _clip(m["content"], 300)}
                elif m.get("tool_calls"):
                    out[i] = {**m, "content": _clip(m.get("content") or "", 600),
                              "tool_calls": [{**tc, "function": {**tc["function"], "arguments": _drop_code(tc["function"]["arguments"])}}
                                             for tc in m["tool_calls"]]}
        ds =self.store.q("select name, nrow, ncol from datasets where ws_id=?", (wid,))
        spec = self.store.approved_spec(wid)
        prog = self.store.latest_program(wid)
        state = (f"Workspace state now: datasets {[f'{d['name']} ({d['nrow']}x{d['ncol']})' for d in ds]}; "
                 f"approved spec: {'v' + str(spec['version']) if spec else 'none'}; "
                 f"program: {'v' + str(prog['version']) + ' by ' + prog['author'] if prog else 'none'}.")
        return [{"role": "system", "content": SYSTEM + "\n" + state}] + out

    async def _turn(self, wid: str, text: str, context: dict | None):
        cfg = self.config()
        acked = self.store.get_setting("acked_hosts", [])
        user_msg = text if not context else f"{text}\n\n[Context from the UI: {json.dumps(context)}]"
        self.bus.emit(wid, "user_message", text=text, context=context)
        if not cfg.configured:
            self.bus.emit(wid, "agent_error", code="not_configured", message="No model endpoint is configured. Open Settings to connect one.")
            return
        if cfg.host not in acked:
            self.bus.emit(wid, "agent_error", code="needs_ack", host=cfg.host,
                          message=f"Sending workspace metadata to {cfg.host} has not been authorized yet.")
            return
        self.store.add_chat(wid, {"role": "user", "content": user_msg})
        self.bus.emit(wid, "agent_start", model=cfg.model, host=cfg.host)
        t0 = time.time()
        usage_total = {"prompt_tokens": 0, "completion_tokens": 0}
        pending_calls: list[dict] = []
        nudges = 0
        try:
            for step in range(MAX_STEPS):
                text_buf, calls, code_streams = [], {}, {}
                finish = None
                async for d in stream_chat(cfg, self._messages(wid), TOOLS):
                    if d.kind == "text":
                        piece = SPECIAL.sub("", d.text)
                        text_buf.append(piece)
                        if piece:
                            self.bus.emit(wid, "assistant_delta", text=piece)
                    elif d.kind == "tool_start":
                        calls[d.index] = d.call
                        if d.call.name:
                            self.bus.emit(wid, "tool_call_start", call_id=d.call.id, name=d.call.name)
                    elif d.kind == "tool_args":
                        c = d.call
                        if c.name == "write_program":
                            ps = code_streams.setdefault(d.index, PartialString("code"))
                            piece = ps.feed(d.text)
                            if piece:
                                self.bus.emit(wid, "code_delta", call_id=c.id, text=piece)
                    elif d.kind == "done":
                        finish = d.finish_reason
                        for k in usage_total:
                            usage_total[k] += (d.usage or {}).get(k, 0) or 0
                content = "".join(text_buf)
                if not calls:
                    # Some open models write their calls as text (Hermes <tool_call> tags, or bare JSON objects)
                    # that the server's parser missed; run them as the calls the model made
                    for k, (obj, span) in enumerate(_text_calls(content)[:MAX_CALLS]):
                        calls[k] = ToolCall(id=f"text_{step}_{k}", name=obj["name"], arguments=json.dumps(obj.get("arguments", {})))
                        content = content.replace(span, "")
                    content = re.sub(r"</?tool_call>|```(?:json)?", "", content).strip() if calls else content
                # Open models sometimes repeat one call many times in a message: run each distinct call once
                seen_calls: set = set()
                for k in sorted(calls):
                    sig = (calls[k].name, calls[k].arguments)
                    if sig in seen_calls or len(seen_calls) >= MAX_CALLS:
                        del calls[k]
                    else:
                        seen_calls.add(sig)
                tool_calls = [{"id": c.id, "type": "function", "function": {"name": c.name, "arguments": c.arguments or "{}"}}
                              for _, c in sorted(calls.items())]
                msg = {"role": "assistant", "content": content}
                if tool_calls:
                    msg["tool_calls"] = tool_calls
                self.store.add_chat(wid, msg)
                if content.strip():
                    self.bus.emit(wid, "assistant_message", text=content)
                if not tool_calls:
                    if finish == "length" and not (self._mismatches(wid) and nudges < MAX_NUDGES and FIX_ASK.search(text)):
                        self.bus.emit(wid, "agent_error", code="length", message="The model hit its output limit.")
                        break
                    # The user asked for a fix and the model stopped while validation still fails: say so (visibly)
                    # and let it continue, at most MAX_NUDGES times per turn
                    left = self._mismatches(wid)
                    if left and nudges < MAX_NUDGES and FIX_ASK.search(text):
                        nudges += 1
                        note = ("[TrialProof host] Your last reply hit the output limit. Be brief. " if finish == "length" else "[TrialProof host] ") + (
                                f"The latest run (program v{left[0]}) still has {left[1]} mismatched cells. "
                                "Continue the fix: read_program, make the smallest change that explains the pattern facts, then run_program.")
                        self.store.add_chat(wid, {"role": "user", "content": note})
                        self.bus.emit(wid, "agent_nudge", text=note, program_version=left[0], mismatches=left[1])
                        continue
                    break
                pending_calls = list(tool_calls)
                paused = False
                for tc in tool_calls:
                    name = tc["function"]["name"]
                    try:
                        args = json.loads(tc["function"]["arguments"] or "{}")
                        if not isinstance(args, dict):
                            raise ValueError("arguments must be a JSON object")
                    except (json.JSONDecodeError, ValueError) as e:
                        result = {"ok": False, "error": f"could not parse arguments as JSON: {e}"}
                        args = {}
                    else:
                        shown = {k: v for k, v in args.items() if k != "code"}
                        self.bus.emit(wid, "tool_call", call_id=tc["id"], name=name, args=shown)
                        try:
                            result = await self._tool(wid, name, args, tc["id"])
                        except Exception as e:  # a tool failure goes back to the model, not up the stack
                            result = {"ok": False, "error": f"{type(e).__name__}: {e}"}
                    self.bus.emit(wid, "tool_result", call_id=tc["id"], name=name, ok=result.get("ok", True),
                                  summary=self._summary(name, result))
                    self.store.add_chat(wid, {"role": "tool", "tool_call_id": tc["id"], "content": _clip(json.dumps(result, default=str), 6000)})
                    pending_calls.remove(tc)
                    paused = paused or (name in PAUSE and result.get("ok", True))
                if paused:
                    break
            else:
                self.bus.emit(wid, "agent_error", code="steps", message=f"Stopped after {MAX_STEPS} steps; ask me to continue.")
        except asyncio.CancelledError:
            for tc in pending_calls:
                self.store.add_chat(wid, {"role": "tool", "tool_call_id": tc["id"], "content": '{"ok": false, "error": "cancelled by the user"}'})
            self.bus.emit(wid, "agent_cancelled")
            raise
        except LLMError as e:
            for tc in pending_calls:
                self.store.add_chat(wid, {"role": "tool", "tool_call_id": tc["id"], "content": '{"ok": false, "error": "model failure"}'})
            self.bus.emit(wid, "agent_error", code="model", message=str(e))
        except Exception as e:
            traceback.print_exc()
            self.bus.emit(wid, "agent_error", code="internal", message=f"{type(e).__name__}: {e}")
        finally:
            self.bus.emit(wid, "agent_end", seconds=round(time.time() - t0, 1), usage=usage_total)

    def _mismatches(self, wid: str) -> tuple[int, int] | None:
        run = self.studio.get_run(wid)
        v = self.studio.get_validation(wid, run["id"]) if run else None
        n = sum(t.get("mismatch", 0) for t in (v or {}).get("tables", []))
        return (run["program_version"], n) if n else None

    # ---------- tools ----------
    async def _tool(self, wid: str, name: str, a: dict, call_id: str) -> dict:
        st, store = self.studio, self.store
        if name == "list_datasets":
            return {"ok": True, "datasets": store.q("select name, filename, nrow, ncol from datasets where ws_id=?", (wid,))}
        if name == "inspect_dataset":
            p = dsets.profile(store, wid, a["name"].upper())
            cols = p["columns"]
            if a.get("columns"):
                want = {c.upper() for c in a["columns"]}
                cols = [c for c in cols if c["name"].upper() in want]
            return {"ok": True, "name": a["name"].upper(), "nrow": p["nrow"], "columns": [
                {"name": c["name"], "label": c["label"], "type": c["type"], "missing": c["n_missing"], "distinct": c["n_distinct"],
                 "top": [f'{t["value"]} ({t["n"]})' for t in c["top"][: (6 if a.get("columns") else 3)]]} for c in cols]}
        if name == "tabulate":
            return {"ok": True, **dsets.tabulate(store, wid, a["dataset"].upper(), a["column"], a.get("by"))}
        if name == "relationships":
            return {"ok": True, "relationships": dsets.relationships(store, wid)}
        if name == "get_spec":
            ap = store.approved_spec(wid)
            pend = store.one("select version, spec, rationale from specs where ws_id=? and status='proposed' order by version desc limit 1", (wid,))
            return {"ok": True, "approved": {"version": ap["version"], "spec": ap["spec"]} if ap else None,
                    "awaiting_decision": {"version": pend["version"], "spec": json.loads(pend["spec"])} if pend else None}
        if name == "propose_spec":
            r = st.propose_spec(wid, a["spec"], a.get("rationale", ""), by="agent")
            if not r["ok"]:
                return {"ok": False, "error": "the specification has problems", "problems": r["problems"]}
            return {"ok": True, "version": r["version"], "status": "awaiting the user's approval in the Spec tab. Stop now and wait."}
        if name == "ask_user":
            self.bus.emit(wid, "clarification", call_id=call_id, question=a["question"], options=a.get("options") or [])
            return {"ok": True, "status": "question shown to the user. Stop now and wait for the answer."}
        if name == "read_program":
            p = store.latest_program(wid)
            if not p:
                return {"ok": True, "version": None, "code": ""}
            numbered = "\n".join(f"{i + 1:>3}| {ln}" for i, ln in enumerate(p["code"].splitlines()))
            return {"ok": True, "version": p["version"], "author": p["author"], "code": numbered}
        if name == "edit_program":
            prev = store.latest_program(wid)
            if not prev:
                return {"ok": False, "error": "there is no program yet; use write_program"}
            old = a["old_text"]
            if old and prev["code"].count(old) == 0:  # copied with read_program's "  5| " prefixes
                old = re.sub(r"(?m)^\s*\d+\| ?", "", old)
            n = prev["code"].count(old) if old else 0
            if n != 1:
                return {"ok": False, "error": f"old_text occurs {n} times in program v{prev['version']} (it must occur exactly once); "
                        "call read_program and copy the exact text, without line numbers. Nothing was saved."}
            return await self._tool(wid, "write_program", {"summary": a.get("summary", ""), "code": prev["code"].replace(old, a["new_text"])}, call_id)
        if name == "write_program":
            prev = store.latest_program(wid)
            called = sorted(t for t in TOOL_NAMES if re.search(rf"\b{t}\s*\(", a["code"]))
            if called:
                return {"ok": False, "error": f"the R program calls {', '.join(called)}, which are your tools, not R functions; "
                        "call them as tools and keep the program plain R. Nothing was saved."}
            emits = lambda code: bool(re.search(r"tp_emit_table\([^#\n]*teae_summary", code or ""))
            if store.approved_spec(wid) and prev and emits(prev["code"]) and not emits(a["code"]):
                return {"ok": False, "error": "this program no longer emits the teae_summary table, so it could not be validated. "
                        "Keep the analysis program whole and investigate with tabulate, trace_cell and compare_with_last_passing. Nothing was saved."}
            p = st.save_program(wid, a["code"], "agent", a.get("summary", ""))
            diff = unified_diff(prev["code"], p["code"], prev["version"], p["version"]) if prev and prev["version"] != p["version"] else ""
            return {"ok": True, "version": p["version"], "lines": len(p["code"].splitlines()), "diff": _clip(diff, 2500) if diff else None}
        if name == "run_program":
            r = await st.run(wid, by="agent")
            tables = [{"id": t["id"], "type": t.get("type"), "rows": len(t["rows"]), "columns": t["columns"],
                       "head": t["rows"][:12]} for t in r["tables"]]
            out = {"ok": r["status"] == "succeeded", "run_id": r["run_id"], "status": r["status"], "program_version": r["program_version"],
                   "error": r["error"], "warnings": r["warnings"][:5], "console_tail": r["console"][-2500:], "tables": tables}
            if r.get("validation"):
                out["validation"] = r["validation"]["tables"]
            return out
        if name == "get_validation":
            run = st.get_run(wid)
            v = st.get_validation(wid, run["id"]) if run else None
            if not v:
                return {"ok": True, "validation": None, "note": "no validation yet (needs a run with a teae_summary table and an approved spec)"}
            tables = []
            for t in v["tables"]:
                bad = [c for c in t.get("cells", []) if c["status"] == "mismatch"]
                tables.append({k: t[k] for k in ("table_id", "status", "passed", "mismatch", "not_verified", "pattern") if k in t}
                              | {"mismatches": bad[:12], "reason": t.get("reason")})
            out = {"ok": True, "run_id": run["id"], "spec_version": v["spec_version"], "tables": tables}
            good, cur = st.last_passing_program(wid), store.latest_program(wid)
            if any(t["mismatch"] for t in tables) and good and cur and good["version"] != cur["version"]:
                out["since_last_passing"] = {"last_passing_version": good["version"], "current_version": cur["version"],
                                             "diff": _clip(unified_diff(good["code"], cur["code"], good["version"], cur["version"]), 3000),
                                             "note": "the mismatches appeared after this change; undoing the part of it that explains the pattern is the smallest fix"}
            return out
        if name == "compare_with_last_passing":
            good, cur = st.last_passing_program(wid), store.latest_program(wid)
            if not good:
                return {"ok": True, "note": "no version has passed validation yet"}
            if good["version"] == cur["version"]:
                return {"ok": True, "note": f"the current program v{cur['version']} is the last passing version"}
            return {"ok": True, "last_passing_version": good["version"], "last_passing_author": good["author"],
                    "current_version": cur["version"], "current_author": cur["author"],
                    "diff": _clip(unified_diff(good["code"], cur["code"], good["version"], cur["version"]), 4000)}
        if name == "trace_cell":
            run = st.get_run(wid)
            t = st.trace(wid, run["id"], a["table_id"], a["level"], a.get("soc"), a.get("pt"), a["group"])
            self.bus.emit(wid, "trace", **t)
            if t.get("reference"):
                t["reference"]["subjects"] = t["reference"]["subjects"][:15]
            t["records_sample"] = t.get("records_sample", [])[:8]
            return {"ok": True, **t}
        if name == "highlight_code":
            self.bus.emit(wid, "highlight", start_line=int(a["start_line"]), end_line=int(a["end_line"]), note=a.get("note", ""))
            return {"ok": True}
        return {"ok": False, "error": f"unknown tool {name}"}

    @staticmethod
    def _summary(name: str, r: dict) -> str:
        if not r.get("ok", True):
            e = r.get("error")
            if isinstance(e, dict):
                at = f" (lines {e.get('line')}-{e.get('end_line')})" if e.get("line") else ""
                return f"run {r.get('status', 'failed')}: {e.get('message', '')}{at}"
            return str(e or r.get("status") or "failed")
        if name == "list_datasets":
            return ", ".join(f"{d['name']} ({d['nrow']} rows)" for d in r["datasets"]) or "no datasets"
        if name == "inspect_dataset":
            return f"{r['name']}: {len(r['columns'])} columns, {r['nrow']} rows"
        if name == "tabulate":
            return f"{r['distinct']} distinct values over {r['rows']} rows"
        if name == "relationships":
            return f"{len(r['relationships'])} dataset pair(s) share columns"
        if name == "propose_spec":
            return f"spec v{r['version']} awaiting approval"
        if name in ("write_program", "edit_program"):
            return f"program v{r['version']}, {r['lines']} lines"
        if name == "run_program":
            v = r.get("validation") or []
            vs = "; ".join(f"{t['table_id']}: {t['passed']} passed, {t['mismatch']} mismatch" for t in v)
            return f"run {r['status']}" + (f"; {vs}" if vs else "")
        if name == "compare_with_last_passing":
            return f"diff v{r['last_passing_version']} → v{r['current_version']}" if r.get("diff") else r.get("note", "")
        if name == "get_validation":
            return "; ".join(f"{t['table_id']}: {t['status']}" for t in r["tables"]) if r.get("tables") else "no validation yet"
        if name == "trace_cell":
            ref = r.get("reference")
            return f"reference n={ref['n']}, N={ref['N']}" if ref else "no reference"
        if name == "read_program":
            return f"v{r['version']}" if r.get("version") else "empty"
        return "done"
