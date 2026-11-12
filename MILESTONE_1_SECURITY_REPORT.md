# Milestone 1 — Security & Deployment Hardening: report

Date: 12 November 2026 · Commits `49e8b2d`…`225c0e3` on `main` (after audit baseline `114cac6`) · Production:
`https://trialproof-studio.fly.dev` (Fly machine `82dd32df717038`, region iad) · Logs: `security_logs/`.

## Outcome

All critical blockers from `TRIALPROOF_COMPLETE_AUDIT.md` for the public deployment are resolved **and verified on the
production Fly machine**. Public R execution was switched off at the start of the milestone and has been switched back
on by the new fail-closed gate only, after the sandbox passed its own isolation self-test in production.

| Requirement | Locally verified | Production-verified |
|---|---|---|
| R cannot read or modify the application database | ✅ `test_sandbox_isolates_user_programs`, self-test | ✅ acceptance: `read database`, `write database dir` → FALSE; self-test on Fly |
| R cannot access application source or secrets | ✅ same tests (canary secret in the server's environment) | ✅ acceptance: source read/list/write → FALSE; the real Groq key not visible in R's environment or in `/proc`; R sees 2 processes (its own) |
| R cannot access another workspace | ✅ | ✅ acceptance: another session's dataset and the workspace folder → FALSE |
| R cannot access the network | ✅ | ✅ acceptance: TCP to 1.1.1.1:80 and HTTP to example.com → FALSE |
| R can read its datasets and write permitted outputs | ✅ | ✅ acceptance; worked example 417/417 passed in production |
| Execution rejected if isolation fails | ✅ `test_runs_refused_until_isolation_verified`, `…fails_closed_without_a_sandbox` (plain `Rscript` detected), `…when_sandbox_missing` | ✅ the same tests ran on the Fly machine; the production gate opened only after `sandbox self-test (bwrap): passed` (Fly log, 16:13:02Z) |
| Other sessions cannot read or modify a workspace | ✅ `test_sessions_cannot_reach_each_others_workspaces` (12 endpoints + forged cookie) | ✅ acceptance: 10 endpoints → 404, list excludes it, forged cookie → 404, workspace unchanged |
| Client IP headers cannot bypass rate limits | ✅ `test_spoofed_ip_headers_do_not_bypass_rate_limits` (direct and trusted-header modes) | ✅ acceptance: 12 new clients with spoofed `X-Forwarded-For`, `X-Real-IP`, `Fly-Client-IP`, `True-Client-IP`, `Forwarded` → `200×8, 429×4` (8 because 2 of the hour's 10 were used earlier in the same run) |
| One session cannot exhaust model resources | ✅ `test_budgets_stop_one_session_and_the_whole_demo`, `test_public_session_turn_budget` (real servers, 2-turn session and 3-turn global budgets) | ✅ through the public URL (addendum): a real session was stopped mid-turn at 41,152 of its 40,000-token budget with the quota message, and its next message was refused with 429. The two budget tests also passed on the Fly machine |
| Normal AI generation, R execution and verification still work | ✅ full scripted session on the hardened code (`gpt-oss-20b`): spec → 417/417 → efficacy-population spec v2 → 405/405 (`local_ai_session.txt`) | ◐ R execution and verification ✅ (417/417). AI: real tool calls ran (`list_datasets`, `inspect_dataset`), then Groq's **daily** cap for the key was exhausted (199,606 / 200,000 tokens, used earlier today by the audit and demo runs); the agent stopped with the new plain quota message. A full production AI session is **not yet verified** and should be rerun once the provider quota resets |

Test totals:
- **Local (macOS):** 23/23 (`local_pytest.txt`).
- **On the Fly machine:** 22/23 (`production_pytest.txt`). All 10 security tests passed. The one failure,
  `test_long_single_turn_fits_context`, is a pre-existing agent test that fails whenever `TP_CONTEXT_CHARS=12000` (the
  production value inherited by the SSH session). It fails the same way on the pre-milestone commit `114cac6` and is
  unrelated to security (see Open items).
- **Production acceptance from outside:** 24/24 (`production_acceptance.txt`).

## What changed

**1. Safe public mode first.** At the start of the work the live machine was removed (`fly scale count 0`). The site
stayed offline until the hardened build passed its self-test on Fly.

**2. Linux executor replaced** (`server/tp/executor.py`). The `rlimit` backend is gone. R now runs in **bubblewrap**:
- new user, mount, PID, network, IPC and UTS namespaces; `--clearenv`; `--cap-drop ALL`; `--die-with-parent`;
  `--new-session`
- a filesystem built from an allow-list: `/usr`, `/etc` and the `/bin`/`/lib` links read-only; this workspace's
  `datasets/` read-only at `/data`; the R helpers read-only at `/tp`; the run directory at `/work`
- a private `/proc`, `/dev` and `/tmp`

The database, application folder, other workspaces and the server's environment don't exist inside. Unknown backends
raise instead of running unsandboxed. The macOS profile (`server/r/sandbox.sb`) now also denies reads of the
application and data folders, which closes the audit's "data folder outside `$HOME`" gap.

**3. Fail closed.** `Executor.self_test` runs a probe program through the real sandbox at startup. 14 checks must hold:
- **Allowed:** reading its own dataset and writing its own output.
- **Not allowed:** reading the database, reading or listing the application source, reading another workspace, writing
  into the source or data folders, modifying its own dataset, seeing a random canary placed in the server's environment
  (directly or through any `/proc/*/environ` or `cmdline`), TCP and HTTP network access, and the canary appearing in
  output.

Until every check passes, `Executor.run` raises `IsolationError` and the API returns 503. `/api/health` reports
`execution.enabled`, `self_test` and `checks_passed`. The override `TP_ALLOW_UNISOLATED=1` is for local development
only.

**4. Limits** on every run:
- CPU time (timeout + 5 s)
- address space (1 GB on Fly)
- file size (20 MB), processes (128), open files (256), no core dumps
- wall-clock timeout (60 s on Fly)
- console output cap (1 MB; the run is stopped and reported as "output limit reached")
- emitted table size cap (20 MB)
- concurrent runs (1 on Fly)

Output is now read in chunks, so a single huge line can't break the reader. The Fly VM is 2 GB, so one run at its
limit plus the server fit in memory.

**5. Sessions and ownership** (`server/tp/main.py`, `store.py`).
- **Cookie:** a server-issued, 256-bit, `HttpOnly`, `SameSite=Lax` session cookie (`Secure` over HTTPS); only its
  SHA-256 is stored, and an unknown cookie gets a new session.
- **Ownership:** workspaces record their owner (additive `owner` column; no existing rows changed), and every
  `/api/workspaces/{id}…` route answers 404 to non-owners, including the event stream and export. The list shows only
  your own, and the owner field is never returned.
- **Dataset names are validated** in all file access (`datasets.ds_dir`). This closed an additional hole found during
  the work: a name like `../<other workspace>/…`, from a client or from the model's `inspect_dataset`/`tabulate`
  arguments, could have read another workspace's files.

**6. Trusted client IP and rate limits.**
- **Client IP:** taken from `TP_CLIENT_IP_HEADER` (`fly-client-ip` on Fly, set by Fly's proxy) or the TCP peer, and
  never from `X-Forwarded-For`.
- **Root cause found:** uvicorn trusts `X-Forwarded-For` by default for 127.0.0.1, and the old start command trusted it
  from everyone (`--forwarded-allow-ips='*'`). The server now starts with `--no-proxy-headers` (Dockerfile, README,
  tests).
- **Limits:** counted per address **and** per session over a sliding hour: workspaces 10, runs 40, messages 20, new
  sessions 20 per address, any write 300.

**7. Model budgets** (`server/tp/agent.py`).
- **Budgets:** turns and provider-reported tokens per UTC day, per session, per address and globally. Fly sets 15 turns
  / 40k tokens per session, 30 / 80k per address, and 150k tokens globally (below Groq's 200k/day).
- **Enforcement:** a turn over budget is refused (HTTP 429 plus a visible message), and a turn that crosses a token
  budget stops after the current step.
- **Messages:** provider quota errors are shown in plain language, with the raw provider text kept in `detail`.

**8. Credentials.**
- The key stays a Fly secret.
- R can't see it (verified in production), and the API never returns it.
- Rotation steps are in `docs/SECURITY.md`.

**9. Unchanged.**
- Statistics: `reference.py`, `studio.py` validation logic, and `example.py` are untouched.
- No previous run, validation or sign-off record was altered: the migration only adds a nullable column.
- The audit's evidence database and the film's database were not opened by the new code.

Files: `server/tp/{executor,main,store,agent,datasets}.py`, `server/r/sandbox.sb`, `server/tests/test_security.py`
(new), `server/tests/test_flow.py` (fixtures now run the real self-test), `scripts/security_acceptance.py` (new),
`scripts/ui_check.py` (shares the browser's session), `Dockerfile`, `fly.toml`, `README.md`, `docs/SECURITY.md` (new).
`scripts/ui_check.py` still passes 13/13 locally against the hardened server.

## Evidence

| File | Contents |
|---|---|
| `security_logs/production_acceptance.txt`, `.json` | 24/24 checks from outside, including the raw probe facts |
| `security_logs/production_acceptance_run1.*` | First production run. All isolation facts were already correct (`0` = denied), but my test harness encoded booleans as 0/1 and reported 13 false failures. Kept unedited; the harness was fixed and rerun. |
| `security_logs/production_pytest.txt` | Test suite on the Fly machine, as the app user (`runuser -u user`): 22 passed, 1 pre-existing failure (output is the last 40 lines) |
| `security_logs/local_pytest.txt` | 23/23 on macOS |
| `security_logs/local_ai_session.*` | Full AI session through the hardened local server |
| `security_logs/production_ai_session.*` | Production AI attempt ending at the provider's daily cap |
| Fly log line | `[tp] sandbox self-test (bwrap): passed` at 2026-11-12T16:13:02Z |

## Is this isolation adequate on Fly?

For a public demo on public data: **yes, with stated residual risk.** Bubblewrap namespaces are the mechanism Flatpak
and many CI sandboxes use, and on Fly the unprivileged user namespaces they need are available (proven by the
self-test). The remaining weaknesses are structural:

1. **The sandbox shares the kernel and the VM with the server.** A kernel or bubblewrap exploit would reach the server
   process, including its environment.
2. **One machine serves everyone**, so a burst of heavy runs from many addresses degrades the service, though not other
   users' data.

**Recommended next architecture, before any non-public data or uploads:** run R in a separate Fly Machine per run (or
per session), created through the Machines API from a minimal R image. It would hold no secrets and no database, and
receive only that workspace's datasets and program. It would run bubblewrap inside as a second layer and have no
private network (`fly machine run --no-public-ips` with egress blocked inside), returning only the run folder. That
gives a separate kernel per run at a cost of a few seconds of start-up. Until then, keep uploads disabled in public
mode; ingest still runs outside the sandbox.

## Open items (not done in Milestone 1)

- **Full production AI session:** rerun once Groq's daily cap resets. Use a separate key for testing: the audit and
  these tests shared the demo's key, which is why it ran out.
- **Rotate the Groq key.** It was pasted into a chat transcript earlier. Steps are in `docs/SECURITY.md`.
- **Rate-limit counters are in memory.** A restart resets them (used once here, deliberately, to rerun the acceptance
  test). Budgets are in SQLite but the Fly disk is ephemeral, so a redeploy resets them too. Acceptable for a
  single-machine demo; use a persistent store before scaling out.
- **Pre-existing context-trim limitation** under the production 12k context: `test_long_single_turn_fits_context`.
  This is an agent issue, outside this milestone.
- **Audit items not in scope:**
  - percentage and phantom-row verification
  - uncoded SOC/PT handling
  - server-side sign-off rules and stale validations
  - silent agent turn endings
  - sandboxed ingest
  - CI, pinned dependencies, the Docker executor
  - the DOMPurify advisory

## Addendum: production AI run (same day, 16:22–16:28 UTC)

Groq's `gpt-oss-120b` daily cap was still exhausted (198,649 / 200,000), so the production model was switched for one
session to the same key's `openai/gpt-oss-20b` with `fly deploy --env TP_LLM_MODEL=openai/gpt-oss-20b`, then switched
back. `/api/health` reports `openai/gpt-oss-120b` with the self-test passed. Log: `security_logs/production_ai_session_20b.txt`,
plus the workspace's events read from the production database.

- **AI generation in production:** ✅ The agent inspected the data and proposed the correct spec (SAFFL, TRT01A, TRTEMFL,
  AEBODSYS/AEDECOD). After approval it wrote programs v1–v3, and each ran in the production sandbox. The runs failed
  with real R errors carrying line numbers ("Column `N` doesn't exist", line 19; "Can't use arithmetic operator `*` in
  selection context", lines 19 and 24), which the agent used to revise. Infrastructure works end to end; the small
  model's programs were wrong.
- **Session budget in production:** ✅ The turn was stopped at 41,152 tokens (budget 40,000) with "You have used this
  session's AI budget for today (41,152 of 40,000 tokens). The worked example and your own R runs still work."; the
  next message got HTTP 429. Usage rows confirm the same totals for the session, the client address and the global
  counter.
- **Not yet shown in production:** a model-built table passing verification. This was shown locally with `gpt-oss-20b`
  and on the pre-milestone production build with `gpt-oss-120b`. Rerun `scripts/demo_e2e.py` against production once
  the 120b quota resets.

Milestone 1 stops here, as instructed.
