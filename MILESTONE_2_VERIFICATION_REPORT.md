# Milestone 2 — Statistical verification, scientific integrity and approval correctness

Date: 2026-11-12 · Production: https://trialproof-studio.fly.dev (Fly release v10) · Repository: `parammadan/trialproof-studio`

Everything below is either **observed** (a command was run and its log is in `verification_logs/`) or explicitly
labelled as an assumption or limitation. Nothing here is a regulatory validation claim: the verifier is an
independent recomputation for one table type (`teae_summary`) on the data in the workspace.

## 1. Executive summary

The audit found that the TEAE verifier accepted tables with wrong percentages, tables with no percentage column, and
invented zero-count rows; that sign-off was enforced only in the browser; that a result stayed "verified" after the
program, the specification or the data changed; and that an export of an older run could carry the current
specification instead of the one its validation used. All four are fixed and covered by tests:

- Percentages are now verified per cell against the independent reference (tolerance half a unit in the last reported
  decimal). A required percentage that is missing, malformed or wrong fails the cell.
- Rows that the reference does not contain (invented terms, a PT under the wrong SOC, an unexpected group) fail as
  `extra_category` / `extra_group`, including zero-count rows. Duplicates and missing rows fail as before.
- Blank SOC/PT records are no longer silently handled: the specification must say `label` or `exclude`, otherwise the
  spec is refused (and a legacy spec's table is flagged).
- Sign-off is enforced on the server: only a run whose verification is **current and verified** can be approved
  (HTTP 409 otherwise); the decision must be `approved` or `changes_requested`, a reviewer is required, and the reviewer
  must confirm the review (HTTP 400). The model has no tool that approves or signs off.
- Each validation is tied to the program version, the approved specification version and the SHA-256 of each dataset.
  Any change makes it **stale**; a run in progress is **in progress**.
- Exports carry the specification the validation used, a manifest with hashes, the verification state at export, and
  the reviews.

Results: local pytest **59/59**; local acceptance **35/35**; production acceptance **35/35**; Milestone 1 security
acceptance re-run on production **24/24**; pytest on the Fly machine **58/59** (the one failure is pre-existing, §11). The reference reconciles **417/417** cells with
a separately written R recomputation. **The production AI acceptance test is PENDING** (§12): the free Groq daily token
quota for `openai/gpt-oss-120b` was exhausted when it ran, so no successful AI run is claimed.

## 2. Findings addressed

| Audit finding | Status | Where |
|---|---|---|
| Wrong percentages pass validation | Fixed | `reference.compare` checks `pct` per cell |
| Percentage column can be dropped | Fixed | `percent.required` (default true for new specs) → `pct_missing` |
| Invented zero-count rows pass | Fixed | rows absent from the reference → `extra_category` |
| PT under the wrong SOC / unexpected group not reported clearly | Fixed | `extra_category` with a "wrong SOC" hint; `extra_group` |
| Blank SOC/PT records handled implicitly | Fixed | spec field `uncoded {handling: label\|exclude, label}`; required when blanks exist |
| Sign-off enforced only in the UI | Fixed | `studio.approve` → 400/404/409 |
| Approval allowed on failed / unverified / mismatched runs | Fixed | 409 unless the state is `verified` |
| No confirmation that a reviewer reviewed | Fixed | `confirmed: true` required |
| Verification not invalidated by later changes | Fixed | `studio.verification_state` → `stale` with reasons |
| Export could carry the wrong specification | Fixed | export uses the validation's `spec_version`; `manifest.json` |
| Removing the safety-population filter passes on the pilot | **Not detectable on this data** — see §11 | synthetic fixture detects it |

## 3. Exact source changes

Commits on `main` (after the Milestone 1 report, `a138790`):

| Commit | Change |
|---|---|
| `31c64cb` | verify pct, reject invented rows, uncoded rule (`server/tp/reference.py`) |
| `8b1d56a` | verification state, server-side sign-off, faithful export (`server/tp/studio.py`, `main.py`, `store.py`) |
| `d067013` | agent: spec rules, verification state (`server/tp/agent.py`) |
| `717eb3f` | UI: emitted pct, verification states (`web/src/…`) |
| `d00a011` | verification regression tests, fixtures, acceptance script |

`git diff --stat a138790 d00a011`: 17 files, +1053 / −130.

- `server/tp/reference.py` — `normalize_spec` (new specs default to `percent {required: true, decimals: 1}`,
  `zero_cells: "optional"`); `percent_rule` (legacy specs: `required: false`); `pct_tolerance`; `check_spec` validates
  `percent`, `zero_cells`, `uncoded`; `uncoded_records`; `teae_reference` applies the uncoded rule and returns `pct` per
  cell; `_int` rejects non-finite values; `_pct` returns (value, problem); `compare(ref, table, spec)` adds the codes
  `pct`, `pct_missing`, `pct_invalid`, `extra_category`, `extra_group` (existing: `n`, `N`, `duplicate`, `missing`) and
  enforces `zero_cells: "required"`.
- `server/tp/studio.py` — `ApprovalError`; `DECISIONS`; `propose_spec` normalizes and requires an uncoded rule when
  blank records exist; runs record `spec_version` and `program_version`; validations store `spec_sha256`,
  `program_sha256` and dataset hashes; `verification_state(wid, run_id)`; `approve(…, confirmed, session)`; `export`
  writes `manifest.json`.
- `server/tp/main.py` — snapshot and `GET /runs/{id}` include `verification`; sign-off takes `confirmed` and maps
  `ApprovalError` to its HTTP status. `server/tp/store.py` — additive `approvals.evidence` column.
- `server/tp/agent.py` — system prompt and `propose_spec` schema document the new fields and forbid padded rows; the
  agent sees the verification state; it still has no approval tool.
- Web — emitted percentages shown (and "–" when missing); verification-state badge and banners; sign-off button only
  for the current verified run; review confirmation checkbox.

Milestone 1 controls (`executor.py`, sandbox, sessions, rate limits, budgets) were not changed.

## 4. Statistical methodology

For a `teae_summary` spec, the reference (Python, independent of the R program) computes:

- **Population:** subjects in the subject dataset passing every `population` condition, with a non-blank treatment
  group. **N** = distinct subjects per group.
- **Events:** event records passing every `event_filter` condition (e.g. `TRTEMFL == "Y"`), joined to the population
  by subject id; events of subjects outside the population are dropped.
- **Counts:** **n** = distinct subjects with ≥ 1 qualifying event, at three levels: any TEAE, per SOC, per (SOC, PT).
  A subject with several events in one term counts once.
- **Percentages:** `pct = 100 · n / N`. A program value is accepted when `|pct_program − pct_ref| ≤ 0.5·10^−d + 1e−9`,
  with `d = percent.decimals` (default 1), so a value correctly rounded to `d` decimals passes and one off by a
  rounding unit fails. Percentages must be finite and in [0, 100].
- **Uncoded terms:** with `label`, blank SOC/PT values are replaced by the label (default "Uncoded") and counted; with
  `exclude`, those records are dropped. A spec over data that contains blanks must choose one.
- **Rows:** each (level, SOC, PT, group) in the reference must appear exactly once. Rows not in the reference are
  extra (even when n = 0); with `zero_cells: "required"` the reference includes zero cells for every SOC/PT × group
  and the program must emit them.

Legacy specifications (created before this milestone, without a `percent` field) keep their approved methodology:
percentages are not required, but are checked when present. This avoids silently changing an approved method
(non-negotiable rule) at the cost of weaker checking for those specs (§11).

## 5. Before/after regression results

Audit fault-injection matrix (same R mutations, the CDISC pilot ADSL/ADAE, the 417-cell reference). "Before" =
the audit build `114cac6`; "after" = Milestone 2. Source: `verification_logs/before_after.md`,
`verification_logs/after/verify_matrix.*`.

| Case | Before | After |
|---|---|---|
| A denominator from AE subjects | mismatch 0/417 | mismatch 0/417 |
| A2 denominator not by group | mismatch 0/417 | mismatch 0/417 |
| B events counted instead of subjects (PT) | mismatch 259/158 | mismatch 259/158 |
| B2 events instead of subjects (all levels) | mismatch 212/205 | mismatch 212/205 |
| C efficacy instead of safety population | mismatch 0/417 | mismatch 0/417 |
| C2 population filter removed | passed 417/0 | passed 417/0 (see §11) |
| D treatment-emergent filter dropped | mismatch 381/56 | mismatch 381/56 |
| E PT from AELLT | mismatch 192/491 | mismatch 192/491 |
| E2 SOC from AEHLGT | mismatch 3/1122 | mismatch 3/1122 |
| F 5 PT rows omitted | mismatch 412/5 | mismatch 412/5 |
| F2 "any" level omitted | mismatch 414/3 | mismatch 414/3 |
| F3 one group omitted | mismatch 281/136 | mismatch 281/136 |
| **G wrong percentages** | **passed 417/0** | **mismatch 0/417** |
| **G2 percentage column dropped** | **passed 417/0** | **mismatch 0/417** |
| **X1 invented zero-count row** | **passed 418/0** | **mismatch 417/1** |
| X2 invented row, n > 0 | mismatch 417/1 | mismatch 417/1 |
| X3 duplicate row | mismatch 417/1 | mismatch 417/1 |
| X4 one n off by one | mismatch 416/1 | mismatch 416/1 |
| X5 table not typed `teae_summary` | no validation | no validation (state `not_verified`, cannot be signed off) |
| X6 n emitted as text ("12") | passed 417/0 | passed 417/0 (exact integer text is accepted) |

(passed/mismatched cells.) The three bold rows are the false passes the audit reported; all now fail. No case that
failed before passes now.

Regression tests (`server/tests/test_verification.py`, numbered as in the brief): 1 wrong %, 1b rounding, 2 missing %
column, 2b % optional by spec, 2c malformed/missing % values, 3 invented zero row, 3b PT under wrong SOC, 3c required
zero cells, 4 duplicates, 4b unexpected group, 5 missing rows, 6–9 subject counts / denominators / population / TE
filter, 10 uncoded without a rule / with `label` (hand-computed) / with `exclude`, synthetic population and counting
errors, 11 failed run, 11b mismatched run, 12 unverified run, 12b decision/reviewer/confirmation, 13 stale result,
14 program change → stale, 15 spec change → stale, 15b dataset change → stale, 16 export of an older run, 17 correct
program passes and reconciles; plus in-progress state, no agent approval tools, and API sign-off codes. 36 tests.

## 6. Independent numerical reconciliation

`server/tests/fixtures/independent_teae.R` is a separate R implementation (written without the reference code) of the
same counts. On the pilot data it produces 417 rows; the test
`test_correct_program_passes_and_reconciles_with_independent_r` (case 17) asserts that all 417 (level, SOC, PT, group) cells
agree with the Python reference on n, N and pct, and that the shipped example program passes 417/417. Output:
`verification_logs/after/independent_teae.csv`.

The synthetic fixture (`fixtures/synthetic/`, 8 subjects, 12 events) has counts computed by hand in
`EXPECTED.md` (N = 3/3; any TEAE 3/3 and 2/3; headache counted once for a subject with two records; a non-TE event
excluded; a subject outside DM and one without a group excluded; one blank TERM and one blank BODYSYS). The `label`
and `exclude` tests assert those hand-computed values.

## 7. Server-side approval tests

Observed locally (pytest) and on production (acceptance script, public API):

| Request | Expected | Local | Production |
|---|---|---|---|
| approve a mismatched run (each of 9 fault cases) | 409 | ✓ | ✓ (9/9) |
| approve a failed run | 409 | ✓ | ✓ |
| request changes on a failed run | 200 | ✓ | ✓ |
| approve an unverified table type | 409 | ✓ | ✓ |
| decision outside `approved`/`changes_requested` | 400 | ✓ | ✓ |
| `confirmed: false` | 400 | ✓ | ✓ |
| unknown run | 404 | ✓ | — |
| approve the current verified run | 200, state `verified` recorded | ✓ | ✓ |
| approve a stale run | 409 | ✓ | ✓ |

The agent's tool list contains no approval or sign-off tool (`test_agent_has_no_approval_tools`). Approvals store an
evidence record (run, spec and program versions, hashes, verification state, session). Existing approval rows were not
modified; the new column is additive.

## 8. Stale-state tests

| Change after a verified run | State | Reason text | Local | Production |
|---|---|---|---|---|
| program edited | `stale` | "program changed …" | ✓ | ✓ |
| new specification approved | `stale` | "specification changed …" | ✓ | ✓ |
| dataset replaced | `stale` | dataset hash changed | ✓ | — (uploads are disabled in public mode) |
| run executing | `in_progress` | | ✓ | — |

The UI shows a stale banner on Results and Verification and hides the sign-off button (screenshots
`verification_logs/screens/1_verified.png`, `2_failed_percentages.png`, `3_verified_again.png`,
`4_stale_after_edit.png`).

## 9. Export-integrity tests

On production, a run was approved under spec v1, then the program was edited and spec v2 (efficacy population)
approved; the bundle for the older run was exported. Observed:

- `analysis_spec.json` is spec **v1** (SAFFL), not the current v2; `manifest.spec_version = 1`.
- `program.R` is byte-identical to the executed program; `manifest.program_matches_recorded_hash = true`.
- `validation.json` belongs to the exported run.
- `manifest.reviews` contains the approval; `manifest.verification_state_at_export.state = "stale"`.
- each dataset has `sha256_at_run` and `unchanged_since_run = true`.

The same is asserted locally by `test_16_export_of_an_older_run_keeps_its_own_evidence`.

## 10. Local and Fly acceptance results

| Suite | Where | Result | Log |
|---|---|---|---|
| pytest (59 tests) | laptop (macOS sandbox) | **59 passed** | `verification_logs/local_pytest.txt` |
| verification acceptance (35 checks) | local server | **35/35** | `verification_logs/local_acceptance.*` |
| verification acceptance (35 checks) | production, public API | **35/35** | `verification_logs/production_acceptance.*` |
| Milestone 1 security acceptance (24 checks) | production | **24/24** | `verification_logs/production_security_acceptance.*` |
| pytest (59 tests) | Fly machine, as the app user, bwrap sandbox | **58 passed, 1 failed** (45 min): the failure is the pre-existing `test_long_single_turn_fits_context` (§11) | `verification_logs/production_pytest.txt` |
| startup isolation self-test | production `/api/health` | passed, 14/14 checks | |

The production acceptance used the shipped example program and fault-injected copies of it, through one new workspace
of its own session. No model calls were made.

## 11. Remaining limitations

- **C2 (safety filter removed) is not detectable on the pilot data**: every ADSL subject has `SAFFL = "Y"`, so the
  wrong program produces the right numbers. Verification checks numbers, not code. The synthetic fixture (which has a
  `SAFETY = "N"` subject) detects it.
- **Legacy specs** (no `percent` field) do not require percentages; they are checked when present.
- **One table type.** Only `teae_summary` is independently verified; any other table is `not_verified` and cannot be
  signed off as verified.
- **Numeric text** such as `"12"` is accepted as the integer 12 (X6). Non-integer and non-finite values fail.
- **Tolerance** accepts any value within half a rounding unit, so a program that truncates instead of rounding can
  pass when the difference is below that bound.
- **Pre-existing test failure in production config:** `test_long_single_turn_fits_context` fails when
  `TP_CONTEXT_CHARS=12000` (the production setting for Groq's 8K tokens/minute). It fails identically on the audit
  build `114cac6`; it is not caused by this milestone.
- The verifier is not a regulatory validation. It does not replace double programming by a second programmer.

## 12. Outstanding production AI acceptance status

**PENDING — no successful production AI run is claimed.**

`scripts/demo_e2e.py` was run against production (model `openai/gpt-oss-120b`, workspace `943a8531db`,
`verification_logs/production_ai_session.*`). Every turn ended after about 1.2 s with no specification, program or
run, because Groq returned its daily-token 429 ("Used 199700" of 200,000 tokens/day for this model). The app showed
the plain-language quota message, as designed in Milestone 1.

Cause: a direct quota probe made earlier the same day (about 7K tokens) consumed most of the remaining daily quota.
That probe was a mistake in test sequencing and is recorded here.

To complete: after the quota resets (00:00 UTC), run
`server/.venv/bin/python scripts/demo_e2e.py --api https://trialproof-studio.fly.dev` once and check that the agent
proposes a spec with `percent`, writes a program, and the run reaches `verified` (or reports a specific mismatch).

## 13. Git commit and deployment information

- Source commits: `31c64cb`, `8b1d56a`, `d067013`, `717eb3f`, `d00a011` (branch `main`).
- Report and logs: the commit that adds this file and `verification_logs/`.
- Deployment: Fly app `trialproof-studio`, release **v10**, machine `82dd32df717038` (iad, shared-cpu-1x, 2 GB),
  image `deployment-01M78BDPVGS3Z9S1ZWJ3RF63SW`; `/api/health` reports `executor: bwrap`, self-test passed,
  `public: true`, model `openai/gpt-oss-120b`.
- Milestone 3 has not been started.
