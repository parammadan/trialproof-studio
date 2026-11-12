# Milestone 3 — AI agent reliability

Date: 2026-11-12 (UTC) · Production: https://trialproof-studio.fly.dev (Fly release v11) · Repository:
`parammadan/trialproof-studio`

Observed results are marked with their logs; anything not observed is labelled. No AI run is reported as successful
unless the server's own verification state said `verified`.

## 1. Summary

The audit and the earlier sessions showed agent turns that "ended silently": the model's turn stopped with no
reply, no error and no result (for example after writing a program, without running it). The cause was found in the
transport, not the model's reasoning: **Groq reports a tool call that does not match its schema as an error inside an
HTTP 200 stream** (`data: {"error": {"code": "tool_use_failed", "failed_generation": …}}`). The client skipped every
chunk without `choices`, so a rejected call looked exactly like an empty reply. The trigger was ordinary: the model
sent `{"columns": null}` for an optional argument.

Fixed in layers: optional tool arguments accept `null`; the client surfaces in-stream errors; a rejected call is
recovered from `failed_generation` (null arguments dropped; the tool still validates its arguments) and the recovery
is shown in the chat; other in-stream errors become visible errors. Independently of the cause, the host no longer
ends a turn on an empty reply, and when the model stops with the work it was asked to do unfinished, the host tells
it the concrete next step (from the workspace's verification state) and, if it still does not finish, ends the turn
with a visible "stopped before finishing — still to do: …". Each turn now ends with an explicit outcome.

Uploads, which were disabled in public mode because files were parsed outside the sandbox, are now parsed **inside
the same sandbox** and enabled with size, type, count and rate limits, so a reviewer can bring a compatible dataset.

## 2. Changes

| Commit | Change |
|---|---|
| `78e45e7` | `Executor.ingest`: `ingest.R` runs in the sandbox (same backend, rlimits, timeout, concurrency cap; refused until the self-test passes); uploads streamed to disk and capped (25 MB/file, 12 datasets/workspace, .csv/.xpt/.rds); public mode allows uploads with an hourly limit (30); the pilot load uses the same path |
| `de3fd4c` | Agent loop: in-stream provider errors; recovery of rejected calls; nullable optional arguments; null = not given; empty-reply retry (2) with a visible host note; unfinished-work continuation from the verification state (no program, not run, stale, run failed with its lines and message, mismatches, no verified table), at most 3 per turn, then a visible `incomplete` error; question turns ("which/what/where/why/how…") are not pushed into work; malformed-call retry (2); daily-quota fallback to `TP_LLM_FALLBACK_MODEL`, shown in the chat; `agent_end` carries `outcome` (`verified`, `done`, `answered`, `waiting_for_user`, `incomplete`, `quota`, `error`, `cancelled`) and the model used |
| `de3fd4c` (cont.) | Loop guard (added after run 3 below): an identical `write_program` says "unchanged … nothing changed; next: …"; a third identical call in one turn is refused with the next step instead of being run again |
| `36c8451` | UI: host notes shown as "Host: …"; "Continue" on incomplete/empty endings; turn outcome shown ("finished · verified", "stopped before finishing", …); public-mode upload card with a do-not-upload-real-data warning (the old card wrongly said other visitors could open workspaces) |
| `970e1a2` | `fly.toml`: budgets sized for one complete workflow per session (measured 60–200K tokens): session 25 turns / 100K tokens, address 40 / 150K, global 330K; fallback model `openai/gpt-oss-20b` (separate daily quota) |
| `00f76db`, `a7e56d4` | `scripts/agent_acceptance.py` (real model, full flow), `scripts/upload_acceptance.py`; `GET /api/workspaces/{id}/log` (the event log as JSON, owner-only) |

Milestone 1 isolation, sessions, ownership, rate limits and budgets are unchanged except the budget values above.
Milestone 2 verification is unchanged.

## 3. Tests

| Suite | Where | Result |
|---|---|---|
| `test_agent_reliability.py` (14: empty reply, stop before running, failed run fixed, mismatch followed up, nudges exhausted, repeated empties, question turn, malformed call, quota fallback, quota without fallback, spec change rerun, rejected call in a 200 stream, stream error parsing, loop guard) | laptop | 14 passed (scripted model over the real store, sandbox and verifier) |
| Upload security (ingest refused without a verified sandbox; ingest command is sandboxed; public uploads parsed, capped, owner-only) | laptop | 3 passed |
| Full suite (76 tests) | laptop | 75 passed + 1 errored on "No space left on device" (the laptop's disk was 99 % full); that test passed on rerun |
| Full suite (76 tests) | Fly machine, bwrap | **75 passed, 1 failed** (45 min); the failure is the pre-existing `test_long_single_turn_fits_context` (`agent_logs/production_pytest_m3.txt`) |
| Verification acceptance (Milestone 2, 35 checks) | production v11 | **35/35** (`verification_logs/production_acceptance_m3.json`) |
| Security acceptance (Milestone 1, 24 checks) | production v11 | **24/24** (`agent_logs/production_security_acceptance_m3.*`) |
| Upload acceptance (8 checks) | production v11 | **8/8** (`agent_logs/production_upload_acceptance.*`): uploads parsed in the sandbox; unfamiliar DM/AEV layout verified with uncoded = label (14 cells) and exclude (11 cells); a counting error caught; 28 MB file and a `.sh` refused; another session gets 404 |

## 4. Real-model runs (no mocks)

`scripts/agent_acceptance.py`: upload → inspect → propose spec → the script, acting as reviewer, approves only if the
spec matches the expected analysis written before the run → write R → run → verify → reviewer injects an error →
verify catches it → agent fixes → rerun → sign-off → export.

| Run | Model | Data | Result | Log |
|---|---|---|---|---|
| 1 (before the transport fix) | gpt-oss-20b, local | synthetic DM/AEV upload | after `list_datasets`, three "empty" replies → turn ended visibly (`empty`). Replaying the request showed the cause: `tool_use_failed`, `columns: null`, inside an HTTP 200 stream | `agent_logs/local_synthetic_20b.*` |
| 2 | gpt-oss-20b, local | synthetic | inspected (5 tools, no host help) and proposed the **correct spec** for the unfamiliar layout, including an uncoded rule; approved. Wrote, ran, edited and reran on its own, but its programs failed in R (a `select()` misuse; later edits deleted a definition and left a broken pipe); then Groq's daily quota for 20b ran out mid-fix → `quota` | `agent_logs/local_synthetic_20b_r2.*` |
| 3 | qwen3.8-27b, local | synthetic | **correct spec**; then wrote the identical program 12 times without running it (the store made no new version; the tool did not say so) → the loop guard above; daily quota ran out | `agent_logs/local_synthetic_qwen.*` |
| 4 | gpt-oss-120b, **production** | synthetic upload | PENDING: scheduled for 00:03 UTC 2026-11-13, after the daily quota resets | `agent_logs/production_agent_synthetic_120b.*` |

Observed: with all three models the inspection and specification steps worked on an unfamiliar layout; the failures
were in R programming (20b), in looping (qwen) and in provider quotas. The host now handles each of those visibly.
Not observed yet: a smaller model reliably writing correct R for an unfamiliar layout.

## 5. Remaining issues

- **Model capability and quota.** On the free tier, a full workflow uses 60–200K tokens and each model has 200K per
  day, so the public demo supports a few complete sessions per day. The smaller fallback model is weaker at R.
- **Prompt size.** Most tokens are prompt tokens (each step resends the system prompt, tool schemas and recent
  history). Reducing them (shorter tool schemas, summarising older steps) is the next efficiency task.
- **Laptop limits.** Local runs were constrained by a 99 % full disk and heavy swap; long suites run on the Fly machine.
- `test_long_single_turn_fits_context` still fails under the production context setting (pre-existing, Milestone 2 §11).

## 6. Deployment

Fly app `trialproof-studio`, release **v11**, machine `82dd32df717038`; `/api/health`: sandbox self-test passed,
`public: true`, model `openai/gpt-oss-120b`, fallback `openai/gpt-oss-20b`. Auto-stop was switched off on the machine
during the production test run and restored afterwards (restored with `fly machine update --autostop=stop`).
