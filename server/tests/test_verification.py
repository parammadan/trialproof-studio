"""Milestone 2 regression tests: every verification and approval failure from the audit, on the CDISC pilot and on
the synthetic study (which has an excluded participant, a participant without a group, an event for a subject not in
the subject file, duplicate records, and blank SOC/PT codes). Programs run through the real sandbox."""
import asyncio
import csv
import io
import json
import shutil
import subprocess
import zipfile
from pathlib import Path

import httpx
import pytest

from tp import datasets as dsets
from tp import reference as ref
from tp.agent import TOOLS
from tp.bus import Bus
from tp.example import SPEC, TEAE_R
from tp.executor import Executor
from tp.store import Store
from tp.studio import ApprovalError, Studio

FIX = Path(__file__).parent / "fixtures"


def _studio(tmp_path, monkeypatch):
    monkeypatch.setattr("tp.store.WS_DIR", tmp_path / "ws")
    monkeypatch.setattr("tp.store.VAR", tmp_path)
    store = Store(tmp_path / "t.db")
    ex = Executor(tmp_path)
    assert asyncio.run(ex.self_test(store.path))["verified"]
    return store, Studio(store, Bus(store), ex)


@pytest.fixture
def pilot(tmp_path, monkeypatch):
    store, st = _studio(tmp_path, monkeypatch)
    wid = store.create_workspace("pilot")["id"]
    asyncio.run(dsets.load_pilot(store, wid))
    assert st.propose_spec(wid, SPEC, "safety", by="user")["ok"]
    return store, st, wid


def run(st, wid, code, author="user"):
    st.save_program(wid, code, author)
    r = asyncio.run(st.run(wid, by="user"))
    t = (r.get("validation") or {}).get("tables") or [{}]
    full = st.get_validation(wid, r["run_id"])
    return r, t[0], (full["tables"][0]["cells"] if full and full["tables"] else [])


def codes(cells):
    return {c.get("code") for c in cells if c["status"] == "mismatch"}


EMIT = 'tp_emit_table(teae, id = "teae", title = "TEAEs by SOC and PT", type = "teae_summary")'
assert EMIT in TEAE_R


def before_emit(extra):
    return TEAE_R.replace(EMIT, extra + "\n" + EMIT)


# ---------------------------------------------------------------- 17 correct reference still passes (+ reconciliation)
def test_correct_program_passes_and_reconciles_with_independent_r(pilot, tmp_path):
    store, st, wid = pilot
    r, t, cells = run(st, wid, TEAE_R)
    assert r["status"] == "succeeded" and t["status"] == "passed" and t["passed"] == 417 and t["mismatch"] == 0
    assert r["verification"]["state"] == "verified"
    out = tmp_path / "independent.csv"
    subprocess.run(["Rscript", "--vanilla", str(FIX / "independent_teae.R"), str(dsets.PILOT_DIR / "adsl.xpt"),
                    str(dsets.PILOT_DIR / "adae.xpt"), str(out)], check=True, capture_output=True)
    ind = {}
    for row in csv.DictReader(open(out)):
        k = (row["level"], row["soc"] if row["soc"] not in ("", "NA") else None, row["pt"] if row["pt"] not in ("", "NA") else None, row["group"])
        ind[k] = (int(row["n"]), int(row["N"]), float(row["pct"]))
    got = {(c["level"], c["soc"], c["pt"], c["group"]): (c["ref_n"], c["ref_N"], c["ref_pct"]) for c in cells}
    assert len(ind) == len(got) == 417
    for k, (n, N, p) in ind.items():
        assert got[k][:2] == (n, N) and abs(got[k][2] - p) < 1e-9, k
    anyr = {k[3]: v for k, v in ind.items() if k[0] == "any"}
    assert anyr == {"Placebo": (65, 86, pytest.approx(75.5813953)), "Xanomeline High Dose": (76, 84, pytest.approx(90.4761905)),
                    "Xanomeline Low Dose": (77, 84, pytest.approx(91.6666667))}


def test_new_specs_record_their_output_rules(pilot):
    store, st, wid = pilot
    s = store.approved_spec(wid)["spec"]
    assert s["percent"] == {"required": True, "decimals": 1} and s["zero_cells"] == "optional"


# ---------------------------------------------------------------- 1, 2 percentages
def test_1_wrong_percentages_fail(pilot):
    store, st, wid = pilot
    r, t, cells = run(st, wid, TEAE_R.replace("mutate(pct = 100 * n / N)", "mutate(pct = 10 * n / N)"))
    assert t["status"] == "mismatch" and t["mismatch"] == 417 and codes(cells) == {"pct"}
    assert r["verification"]["state"] == "failed"
    assert any("percentage that differs" in f for f in t["pattern"])


def test_1b_rounded_percentages_pass_and_off_by_rounding_fail(pilot):
    store, st, wid = pilot
    _, t, _ = run(st, wid, TEAE_R.replace("mutate(pct = 100 * n / N)", "mutate(pct = round(100 * n / N, 1))"))
    assert t["status"] == "passed" and t["passed"] == 417
    _, t, cells = run(st, wid, TEAE_R.replace("mutate(pct = 100 * n / N)", "mutate(pct = floor(100 * n / N * 10) / 10 - 0.1)"))
    assert t["mismatch"] > 0 and codes(cells) == {"pct"}


def test_2_missing_required_percentage_column_fails(pilot):
    store, st, wid = pilot
    _, t, cells = run(st, wid, TEAE_R.replace(" %>% mutate(pct = 100 * n / N)", ""))
    assert t["status"] == "mismatch" and t["mismatch"] == 417 and codes(cells) == {"pct_missing"}


def test_2b_percentage_optional_when_the_spec_says_so(pilot):
    store, st, wid = pilot
    assert st.propose_spec(wid, {**SPEC, "percent": {"required": False, "decimals": 1}}, "pct optional", by="user")["ok"]
    _, t, _ = run(st, wid, TEAE_R.replace(" %>% mutate(pct = 100 * n / N)", ""))
    assert t["status"] == "passed"
    _, t, cells = run(st, wid, TEAE_R.replace("mutate(pct = 100 * n / N)", "mutate(pct = 10 * n / N)"))
    assert codes(cells) == {"pct"}, "a percentage that is present is always checked"


def test_2c_malformed_and_missing_percentage_values(pilot):
    store, st, wid = pilot
    _, t, cells = run(st, wid, before_emit('teae$pct[1] <- NA; teae$pct[2] <- 150; teae$pct[3] <- -1; teae$pct <- as.character(teae$pct); teae$pct[4] <- "n/a"'))
    bad = {c.get("code") for c in cells if c["status"] == "mismatch"}
    assert t["mismatch"] == 4 and bad == {"pct_missing", "pct_invalid"}


def test_percentage_rules_unit():
    spec = {"percent": {"required": True, "decimals": 1}}
    r = {"groups": ["A"], "population_n": 3, "cells": {("any", None, None, "A"): {"n": 1, "N": 3, "pct": 100 / 3},
                                                       ("soc", "S", None, "A"): {"n": 0, "N": 0, "pct": None}}}

    def one(pct, key=("any", None, None, "A"), n=1, N=3):
        row = {"level": key[0], "soc": key[1], "pt": key[2], "group": key[3], "n": n, "N": N, "pct": pct}
        return ref.compare(r, {"columns": list(row), "rows": [row]}, spec)["cells"][0]

    assert one(33.3333)["status"] == "passed" and one(33.3)["status"] == "passed" and one(33.38)["status"] == "passed"
    assert one(33.39)["code"] == "pct" and one(3.33)["code"] == "pct"
    assert one(None)["code"] == "pct_missing" and one("NA")["code"] == "pct_missing"
    assert one(float("nan"))["code"] == "pct_invalid" and one(101)["code"] == "pct_invalid" and one("x")["code"] == "pct_invalid"
    assert one(0.0, key=("soc", "S", None, "A"), n=0, N=0)["code"] == "pct_invalid", "a percentage for N = 0"
    assert one(None, key=("soc", "S", None, "A"), n=0, N=0)["status"] == "passed"
    assert ref.pct_tolerance(0) == pytest.approx(0.5) and ref.pct_tolerance(2) == pytest.approx(0.005)


# ---------------------------------------------------------------- 3, 4, 5 rows
FAKE_ROW = 'teae <- bind_rows(teae, data.frame(level="pt", soc="CARDIAC DISORDERS", pt="MADE UP TERM", group="Placebo", n=0L, N=86L, pct=0))'


def test_3_invented_zero_count_row_fails(pilot):
    store, st, wid = pilot
    _, t, cells = run(st, wid, before_emit(FAKE_ROW))
    assert t["status"] == "mismatch" and t["mismatch"] == 1 and t["passed"] == 417 and codes(cells) == {"extra_category"}


def test_3b_pt_under_the_wrong_soc_fails(pilot):
    store, st, wid = pilot
    code = before_emit('i <- which(teae$level == "pt" & teae$pt == "APPLICATION SITE PRURITUS")[1]; teae$soc[i] <- "CARDIAC DISORDERS"')
    _, t, cells = run(st, wid, code)
    bad = [c for c in cells if c["status"] == "mismatch"]
    assert {c["code"] for c in bad} == {"extra_category", "missing"}
    assert any("under" in c["reason"] for c in bad if c["code"] == "extra_category")


def test_3c_required_zero_cells_are_kept_not_rejected(pilot):
    store, st, wid = pilot
    assert st.propose_spec(wid, {**SPEC, "zero_cells": "required"}, "all groups in every row", by="user")["ok"]
    _, t, cells = run(st, wid, TEAE_R)  # emits only non-zero cells
    assert t["status"] == "mismatch" and codes(cells) == {"missing"}
    full = TEAE_R.replace('teae <- bind_rows(any_ae, by_soc, by_pt)',
                          'by_soc <- by_soc %>% tidyr::complete(group, soc, fill = list(n = 0L, level = "soc"))\n'
                          'by_pt <- by_pt %>% tidyr::complete(group, tidyr::nesting(soc, pt), fill = list(n = 0L, level = "pt"))\n'
                          'teae <- bind_rows(any_ae, by_soc, by_pt)')
    _, t, cells = run(st, wid, full)
    assert t["status"] == "passed" and any(c["n"] == 0 for c in cells), "genuine zero-count cells must pass"


def test_4_duplicate_rows_fail_with_the_right_reason(pilot):
    store, st, wid = pilot
    _, t, cells = run(st, wid, before_emit("teae <- bind_rows(teae, teae[1, ])"))
    assert t["mismatch"] == 1 and codes(cells) == {"duplicate"} and any("duplicate" in f for f in t["pattern"])


def test_4b_unexpected_group_fails(pilot):
    store, st, wid = pilot
    _, t, cells = run(st, wid, before_emit('teae <- bind_rows(teae, mutate(teae[1, ], group = "Total"))'))
    assert codes(cells) == {"extra_group"}


def test_5_missing_rows_fail(pilot):
    store, st, wid = pilot
    _, t, cells = run(st, wid, before_emit('teae <- teae[-which(teae$level == "pt")[1:5], ]'))
    assert t["mismatch"] == 5 and codes(cells) == {"missing"}


# ---------------------------------------------------------------- 6–9 counting and populations
@pytest.mark.parametrize("name, old, new, expect", [
    ("6 subject counts", "pt = AEDECOD) %>% summarise(n = n_distinct(USUBJID)", "pt = AEDECOD) %>% summarise(n = n()", {"n"}),
    ("7 denominators", 'left_join(denom, by = "group")', 'left_join(ae %>% group_by(group) %>% summarise(N = n_distinct(USUBJID)), by = "group")', {"N"}),
    ("8 population", 'filter(SAFFL == "Y")', 'filter(EFFFL == "Y")', {"n", "N"}),
    ("9 TE filter", 'tp_read("ADAE") %>% filter(TRTEMFL == "Y") %>%', 'tp_read("ADAE") %>%', {"n", "extra_category"}),
])
def test_counting_and_population_errors_fail(pilot, name, old, new, expect):
    store, st, wid = pilot
    assert old in TEAE_R
    _, t, cells = run(st, wid, TEAE_R.replace(old, new))
    assert t["status"] == "mismatch" and codes(cells) <= expect | {"missing"} and codes(cells) & expect, (name, codes(cells))


# ---------------------------------------------------------------- 10 uncoded terms + synthetic population errors
SYN_SPEC = {"type": "teae_summary", "title": "synthetic TEAE", "datasets": {"subject": "DM", "events": "AEV"}, "subject_id": "PID",
            "population": [{"var": "SAFETY", "op": "eq", "value": "Y"}], "treatment_var": "ARMGRP",
            "event_filter": [{"var": "EMERGENT", "op": "eq", "value": "Y"}], "soc_var": "BODYSYS", "pt_var": "TERM"}
SYN_R = '''dm <- tp_read("DM") %>% filter(SAFETY == "Y", !is.na(ARMGRP), ARMGRP != "") %>% select(PID, group = ARMGRP)
denom <- dm %>% count(group, name = "N")
ae <- tp_read("AEV") %>% filter(EMERGENT == "Y") %>% inner_join(dm, by = "PID")
CODING
any_ae <- ae %>% group_by(group) %>% summarise(n = n_distinct(PID), .groups = "drop") %>% mutate(level = "any", soc = NA_character_, pt = NA_character_)
by_soc <- ae %>% group_by(group, soc = BODYSYS) %>% summarise(n = n_distinct(PID), .groups = "drop") %>% mutate(level = "soc", pt = NA_character_)
by_pt <- ae %>% group_by(group, soc = BODYSYS, pt = TERM) %>% summarise(n = n_distinct(PID), .groups = "drop") %>% mutate(level = "pt")
teae <- bind_rows(any_ae, by_soc, by_pt) %>% left_join(denom, by = "group") %>% mutate(pct = 100 * n / N)
tp_emit_table(teae, id = "teae", title = "synthetic", type = "teae_summary")
'''
LABEL = 'ae <- ae %>% mutate(BODYSYS = ifelse(is.na(BODYSYS) | BODYSYS == "", "Uncoded", BODYSYS), TERM = ifelse(is.na(TERM) | TERM == "", "Uncoded", TERM))'
EXCLUDE = 'ae <- ae %>% filter(!is.na(BODYSYS), BODYSYS != "", !is.na(TERM), TERM != "")'


@pytest.fixture
def synthetic(tmp_path, monkeypatch):
    store, st = _studio(tmp_path, monkeypatch)
    wid = store.create_workspace("synthetic")["id"]
    for f in ("dm.csv", "aev.csv"):
        asyncio.run(dsets.ingest(store, wid, FIX / "synthetic" / f, f))
    return store, st, wid


def test_10_uncoded_terms_need_an_explicit_rule(synthetic):
    store, st, wid = synthetic
    r = st.propose_spec(wid, SYN_SPEC, "no rule", by="user")
    assert not r["ok"] and "blank" in r["problems"][0] and "uncoded" in r["problems"][0]
    assert not st.propose_spec(wid, {**SYN_SPEC, "uncoded": {"handling": "drop"}}, "bad", by="user")["ok"]


def test_10b_uncoded_label_passes_with_hand_computed_counts(synthetic):
    store, st, wid = synthetic
    assert st.propose_spec(wid, {**SYN_SPEC, "uncoded": {"handling": "label", "label": "Uncoded"}}, "label", by="user")["ok"]
    _, t, cells = run(st, wid, SYN_R.replace("CODING", LABEL))
    assert t["status"] == "passed", [c for c in cells if c["status"] == "mismatch"]
    got = {(c["level"], c["soc"], c["pt"], c["group"]): (c["n"], c["N"]) for c in cells}
    # EXPECTED.md: N Drug 3, Control 3; any TEAE Drug 3, Control 2; P03's blank TERM and P06's blank BODYSYS are labelled
    assert got[("any", None, None, "Drug")] == (3, 3) and got[("any", None, None, "Control")] == (2, 3)
    assert got[("pt", "Gastrointestinal", "Uncoded", "Drug")] == (1, 3) and got[("soc", "Uncoded", None, "Control")] == (1, 3)
    assert got[("pt", "Nervous system", "Headache", "Drug")] == (1, 3), "P01's two headache records count once; P04 (SAFETY=N) is out"
    _, t, cells = run(st, wid, SYN_R.replace("CODING", EXCLUDE))  # a program that excludes instead
    assert t["status"] == "mismatch"


def test_10c_uncoded_exclude_passes(synthetic):
    store, st, wid = synthetic
    assert st.propose_spec(wid, {**SYN_SPEC, "uncoded": {"handling": "exclude"}}, "exclude", by="user")["ok"]
    _, t, cells = run(st, wid, SYN_R.replace("CODING", EXCLUDE))
    assert t["status"] == "passed", [c for c in cells if c["status"] == "mismatch"]
    got = {(c["level"], c["soc"], c["pt"], c["group"]): c["n"] for c in cells}
    assert got[("any", None, None, "Drug")] == 2, "P03's only TE event is uncoded, so P03 drops out of every level"
    _, t, _ = run(st, wid, SYN_R.replace("CODING", LABEL))
    assert t["status"] == "mismatch"


@pytest.mark.parametrize("name, old, new", [
    ("no safety filter", 'filter(SAFETY == "Y", ', "filter("),
    ("no TE filter", ' %>% filter(EMERGENT == "Y")', ""),
    ("events not subjects", "n_distinct(PID)", "n()"),
])
def test_synthetic_population_and_counting_errors_fail(synthetic, name, old, new):
    store, st, wid = synthetic
    assert st.propose_spec(wid, {**SYN_SPEC, "uncoded": {"handling": "label", "label": "Uncoded"}}, "label", by="user")["ok"]
    _, t, _ = run(st, wid, SYN_R.replace("CODING", LABEL).replace(old, new))
    assert t["status"] == "mismatch", name


# ---------------------------------------------------------------- 11–13 sign-off
def approve(st, wid, run_id, decision="approved", confirmed=True):
    return st.approve(wid, run_id, "Reviewer", decision, "", confirmed=confirmed)


def test_11_failed_run_cannot_be_signed_off(pilot):
    store, st, wid = pilot
    r, _, _ = run(st, wid, "x <- c(1,\n")
    assert r["status"] == "failed"
    with pytest.raises(ApprovalError) as e:
        approve(st, wid, r["run_id"])
    assert e.value.status == 409
    assert approve(st, wid, r["run_id"], "changes_requested")["approval_id"], "requesting changes is always allowed"


def test_11b_mismatched_run_cannot_be_signed_off(pilot):
    store, st, wid = pilot
    r, _, _ = run(st, wid, TEAE_R.replace("n_distinct(USUBJID)", "n()"))
    with pytest.raises(ApprovalError) as e:
        approve(st, wid, r["run_id"])
    assert e.value.status == 409 and "failed" in str(e.value)


def test_12_unverified_run_cannot_be_signed_off(pilot):
    store, st, wid = pilot
    r, _, _ = run(st, wid, TEAE_R.replace('type = "teae_summary"', 'type = "generic"'))
    assert r["verification"]["state"] == "not_verified"
    with pytest.raises(ApprovalError) as e:
        approve(st, wid, r["run_id"])
    assert e.value.status == 409


def test_12b_decision_reviewer_and_confirmation_are_required(pilot):
    store, st, wid = pilot
    r, _, _ = run(st, wid, TEAE_R)
    for kwargs, status in (({"decision": "totally-made-up"}, 400), ({"confirmed": False}, 400)):
        with pytest.raises(ApprovalError) as e:
            approve(st, wid, r["run_id"], **kwargs)
        assert e.value.status == status
    with pytest.raises(ApprovalError) as e:
        st.approve(wid, r["run_id"], "  ", "approved", "", confirmed=True)
    assert e.value.status == 400
    with pytest.raises(ApprovalError) as e:
        approve(st, wid, "no-such-run")
    assert e.value.status == 404
    ok = approve(st, wid, r["run_id"])
    row = store.one("select * from approvals where id=?", (ok["approval_id"],))
    ev = json.loads(row["evidence"])
    assert ev["verification_state"] == "verified" and ev["spec_version"] == 1 and ev["program_version"] == 1 and ev["datasets"]


def test_13_stale_validation_cannot_be_signed_off(pilot):
    store, st, wid = pilot
    r, _, _ = run(st, wid, TEAE_R)
    st.save_program(wid, TEAE_R + "\n# edited\n", "user")
    with pytest.raises(ApprovalError) as e:
        approve(st, wid, r["run_id"])
    assert e.value.status == 409 and "stale" in str(e.value)


def test_agent_has_no_approval_tools():
    names = {t["function"]["name"] for t in TOOLS}
    assert not names & {"signoff", "sign_off", "approve", "approve_spec", "decide_spec"}


# ---------------------------------------------------------------- 14, 15 stale states
def test_14_program_change_makes_verification_stale(pilot):
    store, st, wid = pilot
    r, _, _ = run(st, wid, TEAE_R)
    assert st.verification_state(wid)["state"] == "verified"
    st.save_program(wid, TEAE_R.replace("# ", "#  "), "agent") if "# " in TEAE_R else st.save_program(wid, TEAE_R + "\n", "agent")
    s = st.verification_state(wid)
    assert s["state"] == "stale" and "program changed" in s["reasons"][0]
    assert st.get_validation(wid, r["run_id"])["tables"][0]["status"] == "passed", "the historical result is kept"
    r2, _, _ = run(st, wid, store.latest_program(wid)["code"])
    assert st.verification_state(wid)["state"] == "verified"


def test_15_spec_change_makes_verification_stale(pilot):
    store, st, wid = pilot
    run(st, wid, TEAE_R)
    # an agent proposal alone changes nothing; the approval does
    assert st.propose_spec(wid, {**SPEC, "population": [{"var": "EFFFL", "op": "eq", "value": "Y"}]}, "efficacy", by="agent")["ok"]
    assert st.verification_state(wid)["state"] == "verified"
    st.decide_spec(wid, 2, "approve")
    s = st.verification_state(wid)
    assert s["state"] == "stale" and "specification changed" in s["reasons"][0]


def test_15b_dataset_change_makes_verification_stale(synthetic, tmp_path):
    store, st, wid = synthetic
    assert st.propose_spec(wid, {**SYN_SPEC, "uncoded": {"handling": "label", "label": "Uncoded"}}, "label", by="user")["ok"]
    run(st, wid, SYN_R.replace("CODING", LABEL))
    assert st.verification_state(wid)["state"] == "verified"
    changed = tmp_path / "dm.csv"
    changed.write_text((FIX / "synthetic" / "dm.csv").read_text().replace("P07,Control,Y", "P07,Control,N"))
    asyncio.run(dsets.ingest(store, wid, changed, "dm.csv"))
    s = st.verification_state(wid)
    assert s["state"] == "stale" and "DM" in s["reasons"][0]


def test_in_progress_state(pilot):
    store, st, wid = pilot
    st.save_program(wid, "Sys.sleep(3)\n", "user")

    async def go():
        task = asyncio.create_task(st.run(wid, by="user"))
        await asyncio.sleep(1.2)
        mid = st.verification_state(wid)["state"]
        await task
        return mid
    assert asyncio.run(go()) == "in_progress"


# ---------------------------------------------------------------- 16 export integrity
def test_16_export_of_an_older_run_keeps_its_own_evidence(pilot):
    store, st, wid = pilot
    r1, _, _ = run(st, wid, TEAE_R)
    approve(st, wid, r1["run_id"])
    assert st.propose_spec(wid, {**SPEC, "population": [{"var": "EFFFL", "op": "eq", "value": "Y"}]}, "efficacy", by="user")["ok"]
    r2, t2, _ = run(st, wid, TEAE_R.replace('filter(SAFFL == "Y")', 'filter(EFFFL == "Y")'))
    assert t2["status"] == "passed" and t2["passed"] == 405
    z = zipfile.ZipFile(io.BytesIO(st.export(wid, r1["run_id"])))
    spec, man, val = (json.loads(z.read(n)) for n in ("analysis_spec.json", "manifest.json", "validation.json"))
    assert spec["version"] == 1 and spec["spec"]["population"][0]["var"] == "SAFFL"
    assert man["spec_version"] == 1 and man["program_version"] == 1 and man["program_matches_recorded_hash"]
    assert z.read("program.R").decode() == TEAE_R and val["run_id"] == r1["run_id"]
    assert man["verification_state_at_export"]["state"] == "stale" and man["reviews"][0]["decision"] == "approved"
    assert all(d["sha256_at_run"] and d["unchanged_since_run"] for d in man["datasets"])
    z2 = zipfile.ZipFile(io.BytesIO(st.export(wid, r2["run_id"])))
    assert json.loads(z2.read("analysis_spec.json"))["version"] == 2


# ---------------------------------------------------------------- API: HTTP codes, and the snapshot shows the state
def test_api_signoff_codes_and_snapshot_state(public_server):
    url = public_server()
    c = httpx.Client(base_url=url, timeout=120)
    wid = c.post("/api/workspaces", json={"name": "api"}).json()["id"]
    c.post(f"/api/workspaces/{wid}/example").raise_for_status()
    bad = c.post(f"/api/workspaces/{wid}/programs", json={"code": TEAE_R.replace("n_distinct(USUBJID)", "n()")}).json()
    rb = c.post(f"/api/workspaces/{wid}/run", json={}).json()
    assert rb["verification"]["state"] == "failed"
    sign = lambda run_id, **kw: c.post(f"/api/workspaces/{wid}/signoff", json={"run_id": run_id, "reviewer": "R", "decision": "approved", "confirmed": True, **kw})
    assert sign(rb["run_id"]).status_code == 409
    assert sign(rb["run_id"], decision="whatever").status_code == 400
    c.post(f"/api/workspaces/{wid}/programs", json={"code": TEAE_R}).raise_for_status()
    rg = c.post(f"/api/workspaces/{wid}/run", json={}).json()
    assert rg["verification"]["state"] == "verified"
    assert sign(rg["run_id"], confirmed=False).status_code == 400
    assert sign(rg["run_id"]).status_code == 200
    c.post(f"/api/workspaces/{wid}/programs", json={"code": TEAE_R + "\n"}).raise_for_status()
    snap = c.get(f"/api/workspaces/{wid}").json()
    assert snap["verification"]["state"] == "stale" and sign(rg["run_id"]).status_code == 409
    assert snap["approvals"][0]["evidence"]["verification_state"] == "verified"
    assert bad["version"] == 2
