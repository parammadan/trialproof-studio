"""End-to-end flow over the real store, real R sandbox and real reference engine. Only the model is
replaced, by a scripted stand-in that emits the same streamed chunks an OpenAI-compatible server does."""
import asyncio
import json

import pytest

from tp import llm as llm_mod
from tp import agent as agent_mod
from tp.agent import Agent
from tp.bus import Bus
from tp.executor import Executor
from tp.llm import Delta, PartialString, ToolCall
from tp.store import Store
from tp.studio import Studio
from tp import datasets as dsets

TEAE_R = '''adsl <- tp_read("ADSL") %>% filter(SAFFL == "Y") %>% select(USUBJID, group = TRT01A)
denom <- adsl %>% count(group, name = "N")
ae <- tp_read("ADAE") %>% filter(TRTEMFL == "Y") %>% inner_join(adsl, by = "USUBJID")
any_ae <- ae %>% group_by(group) %>% summarise(n = n_distinct(USUBJID), .groups = "drop") %>%
  mutate(level = "any", soc = NA_character_, pt = NA_character_)
by_soc <- ae %>% group_by(group, soc = AEBODSYS) %>% summarise(n = n_distinct(USUBJID), .groups = "drop") %>%
  mutate(level = "soc", pt = NA_character_)
by_pt <- ae %>% group_by(group, soc = AEBODSYS, pt = AEDECOD) %>% summarise(n = n_distinct(USUBJID), .groups = "drop") %>%
  mutate(level = "pt")
teae <- bind_rows(any_ae, by_soc, by_pt) %>% left_join(denom, by = "group") %>% mutate(pct = 100 * n / N)
tp_emit_table(teae, id = "teae", title = "TEAEs by SOC and PT", type = "teae_summary")
'''

SPEC = {"type": "teae_summary", "title": "TEAE summary", "datasets": {"subject": "ADSL", "events": "ADAE"},
        "subject_id": "USUBJID", "population": [{"var": "SAFFL", "op": "eq", "value": "Y"}],
        "treatment_var": "TRT01A", "event_filter": [{"var": "TRTEMFL", "op": "eq", "value": "Y"}],
        "soc_var": "AEBODSYS", "pt_var": "AEDECOD"}


def script(*steps):
    """Each step is a list of (tool name, args) or a text string; args JSON is streamed in small chunks."""
    it = iter(steps)

    async def fake(cfg, messages, tools):
        step = next(it)
        if isinstance(step, str):
            for w in step.split(" "):
                yield Delta("text", text=w + " ")
        else:
            for i, (name, args) in enumerate(step):
                c = ToolCall(id=f"c{i}{name}", name=name)
                yield Delta("tool_start", index=i, call=c)
                s = json.dumps(args)
                for k in range(0, len(s), 37):
                    c.arguments += s[k:k + 37]
                    yield Delta("tool_args", index=i, text=s[k:k + 37], call=c)
        yield Delta("done", finish_reason="stop")

    return fake


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr("tp.store.WS_DIR", tmp_path / "ws")
    monkeypatch.setattr("tp.store.VAR", tmp_path)
    store = Store(tmp_path / "t.db")
    store.set_setting("llm", {"base_url": "http://127.0.0.1:9/v1", "model": "stand-in"})
    store.set_setting("acked_hosts", ["127.0.0.1:9"])
    bus = Bus(store)
    st = Studio(store, bus, Executor())
    ws = store.create_workspace("test")
    asyncio.run(dsets.load_pilot(store, ws["id"]))
    return store, st, Agent(st), ws["id"]


def kinds(store, wid):
    return [e["kind"] for e in store.events(wid)]


def run_turn(ag, wid, text, steps, monkeypatch):
    monkeypatch.setattr(agent_mod, "stream_chat", script(*steps))

    async def go():
        ag.start(wid, text)
        await ag.tasks[wid]

    asyncio.run(go())


def test_full_flow(env, monkeypatch):
    store, st, ag, wid = env
    # 1. inspect, then propose a spec and stop
    run_turn(ag, wid, "TEAE summary by arm", [
        [("inspect_dataset", {"name": "ADSL", "columns": ["SAFFL", "TRT01A"]})],
        [("propose_spec", {"spec": SPEC, "rationale": "safety population"})],
    ], monkeypatch)
    k = kinds(store, wid)
    assert "spec_proposed" in k and "program_version" not in k
    assert store.approved_spec(wid) is None
    st.decide_spec(wid, 1, "approve")
    # 2. write the program (streamed), run it, validation passes
    run_turn(ag, wid, "approved", [
        [("write_program", {"summary": "TEAE table", "code": TEAE_R})],
        [("run_program", {})],
        "All cells match the reference.",
    ], monkeypatch)
    run = st.get_run(wid)
    assert run["status"] == "succeeded", run["console"]
    v = st.get_validation(wid, run["id"])
    t = v["tables"][0]
    assert t["status"] == "passed" and t["mismatch"] == 0 and t["passed"] > 100
    anyc = {c["group"]: (c["n"], c["N"]) for c in t["cells"] if c["level"] == "any"}
    assert anyc == {"Placebo": (65, 86), "Xanomeline High Dose": (76, 84), "Xanomeline Low Dose": (77, 84)}
    # 3. a deliberate bug (count events instead of subjects) is caught cell by cell
    st.save_program(wid, TEAE_R.replace("by_pt <- ae %>% group_by(group, soc = AEBODSYS, pt = AEDECOD) %>% summarise(n = n_distinct(USUBJID)",
                                        "by_pt <- ae %>% group_by(group, soc = AEBODSYS, pt = AEDECOD) %>% summarise(n = n()"), "user")
    r = asyncio.run(st.run(wid, by="user"))
    t = r["validation"]["tables"][0]
    assert t["status"] == "mismatch" and t["mismatch"] > 0
    bad = [c for c in st.get_validation(wid, r["run_id"])["tables"][0]["cells"] if c["status"] == "mismatch"]
    assert all(c["level"] == "pt" and c["n"] > c["ref_n"] for c in bad)
    # 4. lineage of one cell
    tr = st.trace(wid, r["run_id"], "teae", "any", None, None, "Placebo")
    assert tr["reference"]["n"] == 65 and tr["reference"]["N"] == 86 and len(tr["reference"]["subjects"]) == 65
    assert any("TRTEMFL" in ln["terms"] for ln in tr["code_lines"])
    # 5. sign-off and export
    st.approve(wid, r["run_id"], "Reviewer", "changes_requested", "mismatch in PT rows")
    z = st.export(wid)
    assert z[:2] == b"PK"


def test_spec_change_needs_approval(env, monkeypatch):
    store, st, ag, wid = env
    st.propose_spec(wid, SPEC, "user", by="user")
    eff = {**SPEC, "population": [{"var": "EFFFL", "op": "eq", "value": "Y"}]}
    run_turn(ag, wid, "use the efficacy population", [[("propose_spec", {"spec": eff, "rationale": "asked"})]], monkeypatch)
    assert store.approved_spec(wid)["version"] == 1  # unchanged until the user decides


def test_bad_spec_rejected(env, monkeypatch):
    store, st, ag, wid = env
    r = st.propose_spec(wid, {**SPEC, "treatment_var": "ARMX"}, "", by="agent")
    assert not r["ok"] and "ARMX" in r["problems"][0]


def test_runtime_error_reports_lines(env):
    store, st, ag, wid = env
    st.save_program(wid, 'x <- 1\ny <- x +\n  undefined_thing\n', "user")
    r = asyncio.run(st.run(wid, by="user"))
    assert r["status"] == "failed" and r["error"]["line"] == 2 and r["error"]["end_line"] == 3


def test_sandbox_blocks_home_and_network(env):
    store, st, ag, wid = env
    import pathlib
    st.save_program(wid, f'readLines("{pathlib.Path.home()}/.zshrc")\n', "user")
    assert asyncio.run(st.run(wid, by="user"))["status"] == "failed"
    st.save_program(wid, 'readLines(url("https://example.com"))\n', "user")
    assert asyncio.run(st.run(wid, by="user"))["status"] == "failed"


def test_not_configured(env, monkeypatch):
    store, st, ag, wid = env
    store.set_setting("llm", {})
    monkeypatch.delenv("TP_LLM_BASE_URL", raising=False)
    asyncio.run(ag._turn(wid, "hi", None))
    assert store.events(wid)[-1]["payload"]["code"] == "not_configured"


def test_partial_string_decodes_streamed_json():
    code = 'x <- "a\\"b"\nprint(x)\t# é ✓'
    s = json.dumps({"summary": "s", "code": code})
    ps = PartialString("code")
    got = "".join(ps.feed(s[i:i + 3]) for i in range(0, len(s), 3))
    assert got == code


def test_runs_with_workspace_under_home(tmp_path):
    """The real workspaces live under the home folder, which the sandbox denies reading."""
    import pathlib, shutil, tempfile
    base = pathlib.Path(tempfile.mkdtemp(dir=pathlib.Path(__file__).resolve().parents[2] / "var"))
    try:
        store = Store(base / "t.db")
        import tp.store as S
        old = S.WS_DIR
        S.WS_DIR = base / "ws"
        try:
            st = Studio(store, Bus(store), Executor())
            wid = store.create_workspace("home")["id"]
            asyncio.run(dsets.load_pilot(store, wid))
            st.propose_spec(wid, SPEC, "", by="user")
            st.save_program(wid, TEAE_R, "user")
            r = asyncio.run(st.run(wid, by="user"))
            assert r["status"] == "succeeded", r["console"]
            assert r["validation"]["tables"][0]["mismatch"] == 0
        finally:
            S.WS_DIR = old
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_text_tool_calls_are_parsed():
    from tp.agent import _text_calls
    t = 'First:\n{"name": "inspect_dataset", "arguments": {"name": "ADSL"}}\n<tool_call>{"name": "tabulate", "arguments": {"dataset": "ADAE", "column": "TRTEMFL"}}</tool_call> and {"x": 1}'
    got = [o["name"] for o, _ in _text_calls(t)]
    assert got == ["inspect_dataset", "tabulate"]
