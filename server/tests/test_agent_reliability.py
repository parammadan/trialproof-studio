"""Milestone 3: the agent loop never ends a turn silently. Empty replies, stopping with unfinished work, failed
runs, malformed tool calls and exhausted quotas are each handled visibly. The model is a scripted stand-in here
(these test the host's loop logic over the real store, sandbox and verifier); scripts/agent_acceptance.py runs
the same flow with a real model."""
import asyncio

import pytest

from tp import agent as agent_mod
from tp.agent import Agent
from tp.bus import Bus
from tp.example import SPEC, TEAE_R
from tp.llm import LLMError
from tp.store import Store
from tp.studio import Studio
from tp import datasets as dsets
from test_flow import script, verified_executor


@pytest.fixture
def ws(tmp_path, monkeypatch):
    monkeypatch.setattr("tp.store.WS_DIR", tmp_path / "ws")
    monkeypatch.setattr("tp.store.VAR", tmp_path)
    store = Store(tmp_path / "t.db")
    store.set_setting("llm", {"base_url": "http://127.0.0.1:9/v1", "model": "primary"})
    store.set_setting("acked_hosts", ["127.0.0.1:9"])
    st = Studio(store, Bus(store), verified_executor(tmp_path, store))
    wid = store.create_workspace("t")["id"]
    asyncio.run(dsets.load_pilot(store, wid, ex=st.ex))
    st.propose_spec(wid, SPEC, "approved by the test user", by="user")
    return store, st, Agent(st), wid


def turn(ag, wid, text, steps, monkeypatch):
    fake = script(*steps)
    monkeypatch.setattr(agent_mod, "stream_chat", fake)

    async def go():
        ag.start(wid, text)
        await ag.tasks[wid]

    asyncio.run(go())
    ev = ag.store.events(wid)
    since = max(i for i, e in enumerate(ev) if e["kind"] == "agent_start")
    return ev[since:], fake


def of(ev, kind):
    return [e["payload"] for e in ev if e["kind"] == kind]


BROKEN = TEAE_R.replace("filter(SAFFL == \"Y\")", "filter(SAFFL == \"Y\") %>% undefined_step()")


def test_empty_reply_is_retried_not_a_silent_end(ws, monkeypatch):
    store, st, ag, wid = ws
    ev, _ = turn(ag, wid, "I approved the spec. Go ahead.", [
        [("write_program", {"summary": "TEAE", "code": TEAE_R})],
        "",                                   # the model goes quiet after writing the program
        [("run_program", {})],
        "Verified: 417 cells match.",
    ], monkeypatch)
    nudges = of(ev, "agent_nudge")
    assert [n["reason"] for n in nudges] == ["empty"] and "run_program" in nudges[0]["text"]
    assert of(ev, "agent_end")[0]["outcome"] == "verified"
    assert not any(m.get("role") == "assistant" and not m.get("content") and not m.get("tool_calls") for m in store.chat(wid))


def test_stopping_before_running_gets_the_next_step(ws, monkeypatch):
    store, st, ag, wid = ws
    ev, _ = turn(ag, wid, "Go ahead and write the program.", [
        [("write_program", {"summary": "TEAE", "code": TEAE_R})],
        "I wrote the program. I will run it next.",   # says it will, but stops
        [("run_program", {})],
        "Done.",
    ], monkeypatch)
    assert [n["reason"] for n in of(ev, "agent_nudge")] == ["not_run"]
    assert of(ev, "agent_end")[0]["outcome"] == "verified"


def test_failed_run_is_investigated_and_fixed(ws, monkeypatch):
    store, st, ag, wid = ws
    ev, _ = turn(ag, wid, "Write and run the program.", [
        [("write_program", {"summary": "TEAE", "code": BROKEN})],
        [("run_program", {})],
        "The run failed.",                    # stops on the failure
        [("edit_program", {"summary": "drop the bad call", "old_text": ' %>% undefined_step()', "new_text": ""})],
        [("run_program", {})],
        "Fixed; verified.",
    ], monkeypatch)
    n = of(ev, "agent_nudge")
    assert [x["reason"] for x in n] == ["run_failed"] and "undefined_step" in n[0]["text"] and "lines" in n[0]["text"]
    assert of(ev, "agent_end")[0]["outcome"] == "verified"
    assert st.verification_state(wid)["state"] == "verified"


def test_mismatch_after_run_is_followed_up(ws, monkeypatch):
    store, st, ag, wid = ws
    wrong = TEAE_R.replace("mutate(pct = 100 * n / N)", "mutate(pct = 10 * n / N)")
    ev, _ = turn(ag, wid, "Write and run the program.", [
        [("write_program", {"summary": "TEAE", "code": wrong})],
        [("run_program", {})],
        "Here is the table.",                 # claims success over a failed verification
        [("edit_program", {"summary": "pct scale", "old_text": "pct = 10 * n / N", "new_text": "pct = 100 * n / N"})],
        [("run_program", {})],
        "Verified.",
    ], monkeypatch)
    n = of(ev, "agent_nudge")
    assert [x["reason"] for x in n] == ["mismatch"] and n[0]["mismatches"] == 417
    assert of(ev, "agent_end")[0]["outcome"] == "verified"


def test_unfinished_work_is_reported_when_nudges_run_out(ws, monkeypatch):
    store, st, ag, wid = ws
    ev, _ = turn(ag, wid, "Write and run the program.", [
        [("write_program", {"summary": "TEAE", "code": TEAE_R})],
        "ok", "ok", "ok", "ok",               # never runs it
    ], monkeypatch)
    assert len(of(ev, "agent_nudge")) == agent_mod.MAX_NUDGES
    err = of(ev, "agent_error")
    assert err and err[-1]["code"] == "incomplete" and "run_program" in err[-1]["message"]
    assert of(ev, "agent_end")[0]["outcome"] == "incomplete"


def test_repeated_empty_replies_end_visibly(ws, monkeypatch):
    store, st, ag, wid = ws
    ev, _ = turn(ag, wid, "Write the program.", ["", "", ""], monkeypatch)
    err = of(ev, "agent_error")
    assert err and err[-1]["code"] == "empty" and "no program yet" in err[-1]["message"]
    assert "write_program" in err[-1]["message"]  # says what is left
    assert of(ev, "agent_end")[0]["outcome"] == "incomplete"


def test_question_turn_is_not_pushed_into_work(ws, monkeypatch):
    store, st, ag, wid = ws
    ev, _ = turn(ag, wid, "Which population does the approved spec use?", ["The safety population (SAFFL = Y)."], monkeypatch)
    assert not of(ev, "agent_nudge") and not of(ev, "agent_error")
    assert of(ev, "agent_end")[0]["outcome"] == "answered"


def test_malformed_tool_call_is_retried(ws, monkeypatch):
    store, st, ag, wid = ws
    bad = LLMError('model endpoint returned HTTP 400: {"error":{"message":"Failed to call a function.","code":"tool_use_failed"}}')
    ev, _ = turn(ag, wid, "Write and run the program.", [
        bad,
        [("write_program", {"summary": "TEAE", "code": TEAE_R})],
        [("run_program", {})],
        "Verified.",
    ], monkeypatch)
    assert [n["reason"] for n in of(ev, "agent_nudge")] == ["malformed_call"]
    assert of(ev, "agent_end")[0]["outcome"] == "verified"


QUOTA = LLMError('model endpoint returned HTTP 429: {"error":{"message":"Rate limit reached for model on tokens per day (TPD): Limit 200000, Used 199700"}}')


def test_daily_quota_switches_to_the_fallback_model_visibly(ws, monkeypatch):
    store, st, ag, wid = ws
    monkeypatch.setattr(agent_mod, "FALLBACK_MODEL", "fallback")
    ev, fake = turn(ag, wid, "Write and run the program.", [
        QUOTA,
        [("write_program", {"summary": "TEAE", "code": TEAE_R})],
        [("run_program", {})],
        "Verified.",
    ], monkeypatch)
    assert fake.models == ["primary", "fallback", "fallback", "fallback"]
    assert [n["reason"] for n in of(ev, "agent_nudge")] == ["fallback"]
    end = of(ev, "agent_end")[0]
    assert end["outcome"] == "verified" and end["model"] == "fallback"


def test_daily_quota_without_fallback_is_a_plain_message(ws, monkeypatch):
    store, st, ag, wid = ws
    monkeypatch.setattr(agent_mod, "FALLBACK_MODEL", "")
    ev, _ = turn(ag, wid, "Write and run the program.", [QUOTA], monkeypatch)
    err = of(ev, "agent_error")[-1]
    assert err["code"] == "quota" and "{" not in err["message"]
    assert of(ev, "agent_end")[0]["outcome"] == "quota"


def test_follow_up_spec_change_reruns_against_the_new_spec(ws, monkeypatch):
    store, st, ag, wid = ws
    turn(ag, wid, "Write and run the program.", [
        [("write_program", {"summary": "TEAE", "code": TEAE_R})], [("run_program", {})], "Verified."], monkeypatch)
    eff = {**SPEC, "population": [{"var": "EFFFL", "op": "eq", "value": "Y"}]}
    st.propose_spec(wid, eff, "user asked for the efficacy population", by="user")   # approved by the user
    ev, _ = turn(ag, wid, "I approved the efficacy spec. Go ahead.", [
        "The program already exists.",        # stops without updating: the result is stale
        [("edit_program", {"summary": "efficacy population", "old_text": 'filter(SAFFL == "Y")', "new_text": 'filter(EFFFL == "Y")'})],
        [("run_program", {})],
        "Verified against v2.",
    ], monkeypatch)
    assert [n["reason"] for n in of(ev, "agent_nudge")] == ["stale"]
    vs = st.verification_state(wid)
    assert vs["state"] == "verified" and vs["spec_version"] == 2


def test_rejected_call_inside_a_200_stream_is_recovered(ws, monkeypatch):
    """Groq reports a schema-rejected tool call inside an HTTP 200 stream; it used to look like an empty reply."""
    import json as _json
    from tp import llm as llm_mod
    from tp.llm import Delta
    gen = _json.dumps({"name": "inspect_dataset", "arguments": {"name": "ADSL", "columns": None}})
    steps = iter([
        [Delta("rejected_call", text=gen), Delta("done", finish_reason="tool_calls")],
        "ADSL has 254 subjects.",
    ])

    def wrap():
        async def f(cfg, messages, tools):
            step = next(steps)
            if isinstance(step, str):
                yield Delta("text", text=step)
                yield Delta("done", finish_reason="stop")
            else:
                for d in step:
                    yield d
        return f
    monkeypatch.setattr(agent_mod, "stream_chat", wrap())
    store, st, ag, wid = ws

    async def go():
        ag.start(wid, "What is in ADSL?")
        await ag.tasks[wid]
    asyncio.run(go())
    ev = store.events(wid)
    calls = [e["payload"] for e in ev if e["kind"] == "tool_call"]
    res = [e["payload"] for e in ev if e["kind"] == "tool_result"]
    assert calls and calls[0]["name"] == "inspect_dataset" and calls[0]["args"] == {"name": "ADSL"} and res[0]["ok"]
    assert [e["payload"]["reason"] for e in ev if e["kind"] == "agent_nudge"] == ["recovered_call"]
    assert [e["payload"] for e in ev if e["kind"] == "agent_end"][0]["outcome"] == "answered"


def test_stream_error_chunk_is_not_dropped():
    """The client turns an in-stream error chunk into a rejected call (recoverable) or an error, never silence."""
    import json as _json
    from tp.llm import rejected_generation
    err = {"error": {"message": "Tool call validation failed", "code": "tool_use_failed",
                     "failed_generation": '{"name": "tabulate", "arguments": {"dataset": "DM", "column": "SAFETY", "by": null}}'}}
    assert _json.loads(rejected_generation(_json.dumps(err)))["name"] == "tabulate"
    assert rejected_generation(_json.dumps({"error": {"code": "rate_limit_exceeded", "message": "x"}})) is None
    assert agent_mod._rejected_calls(rejected_generation(_json.dumps(err))) == [("tabulate", {"dataset": "DM", "column": "SAFETY"})]
    schema = {t["function"]["name"]: t["function"]["parameters"] for t in agent_mod.TOOLS}
    assert schema["inspect_dataset"]["properties"]["columns"]["type"] == ["array", "null"]
    assert schema["inspect_dataset"]["properties"]["name"]["type"] == "string"  # required stays strict


def test_a_looping_model_is_told_nothing_changed_and_what_is_next(ws, monkeypatch):
    store, st, ag, wid = ws
    w = ("write_program", {"summary": "TEAE", "code": TEAE_R})
    t = ("tabulate", {"dataset": "ADAE", "column": "TRTEMFL"})
    ev, _ = turn(ag, wid, "Write and run the program.", [[w], [w], [t], [t], [t], [("run_program", {})], "Verified."], monkeypatch)
    res = [e["payload"] for e in ev if e["kind"] == "tool_result"]
    assert res[1]["summary"] == "unchanged: still program v1"
    tool_msgs = [m["content"] for m in store.chat(wid) if m["role"] == "tool"]
    assert "nothing changed" in tool_msgs[1] and "run_program" in tool_msgs[1]
    assert res[4]["ok"] is False and "not run again" in res[4]["summary"]  # the third identical tabulate
    assert of(ev, "agent_end")[0]["outcome"] == "verified"
