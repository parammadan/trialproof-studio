# TrialProof Studio

An AI-native workspace for statistical programming. You import analysis datasets. An LLM agent with real tools
inspects them and proposes an analysis specification for you to approve. It then writes R into a live Monaco editor
and runs it in a sandbox. A separate deterministic engine recomputes every reported number from the approved
specification and compares them cell by cell. A human signs off.

> Not validated software. Not for regulatory submission or clinical decision-making.

## Run it

```sh
# backend (Python 3.12+, R 4.x with dplyr, tidyr, haven, readr, jsonlite)
cd server && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn tp.main:app --port 8765

# frontend
cd web && npm install && npm run dev          # http://localhost:5173
# or: npm run build, and the backend serves web/dist at http://localhost:8765
```

Connect a model in **Settings**. Any OpenAI-compatible chat-completions endpoint with tool calling works. To serve an
open-weight model on Northeastern's Explorer cluster (A100, vLLM, Qwen2.5-Coder-32B-Instruct-AWQ):

```sh
sh hpc/connect.sh        # submits hpc/serve.sbatch, tunnels it to localhost:8100, points the app at it
sh hpc/connect.sh stop   # or Ctrl-C; the job also stops itself after 8 idle minutes
```

## How it works

```
web/ (React, TypeScript, Tailwind, Radix/shadcn-style UI, Monaco, motion)
  │  REST + Server-Sent Events (every event is persisted, then pushed live)
server/tp/ (FastAPI)
  agent.py      LLM tool-calling loop: inspect → spec → write R → run → read errors → revise → validate
  llm.py        streaming OpenAI-compatible client; code is decoded out of the streamed tool arguments
  studio.py     program versions, specs, runs, validation, lineage, sign-off, export bundle
  executor.py   R in a sandbox: macOS sandbox-exec (no network, no home-folder reads, writes only to the
                run dir) or `docker run --network none` (TP_EXECUTOR=docker, untested here)
  reference.py  independent reference engine in plain Python over CSV copies of the data
  datasets.py   ingest via R (CSV / XPT / RDS), profiles, relationships, tabulations
server/r/       ingest.R, prelude.R (tp_read, tp_emit_table), runner.R (expression-by-expression
                execution with line-range progress and errors), sandbox.sb
```

**What is real.** Agent activity comes straight from the loop: streamed text, tool calls and their actual results,
and the code as the model writes it. Nothing is replayed. Every number in the results panel comes from an R run. The
reference engine shares no code with the R program, and it uses a different language.

**What a "passed" cell means.** The program's count equals the reference recomputed from the approved specification.
It does not mean the specification is the right analysis. The UI says so, and sign-off requires an explicit review
confirmation.

**Guardrails.**
- The agent cannot approve a specification. A population or filter change becomes a new proposal that you decide on.
- The first request to a new model host asks you to authorize what is sent: column metadata, value counts, code,
  console output and result previews. Whole datasets are never sent.
- Runs can be cancelled, they time out at 120 s, and their errors carry line ranges.

**Verification scope.** The independent engine covers TEAE incidence summaries (`type = "teae_summary"`): subjects
with ≥1 event overall, by SOC and by SOC/PT, per treatment group, with population denominators from the subject-level
dataset. Other tables are shown as *not verified*.

## Tests

```sh
cd server && .venv/bin/python -m pytest -q
```

The flow test uses the real store, the real R sandbox and the real reference engine, with a scripted stand-in for the
model. It covers: the spec gate, streamed code, the run, validation (CDISC pilot, safety population: 65/86, 76/84 and
77/84 subjects with ≥1 TEAE), a deliberate bug caught cell by cell, lineage, sign-off, export, the sandbox blocking
home-folder reads and network access, and error line ranges.

`scripts/demo_e2e.py` drives the flagship session against a live model through the HTTP API.

`scripts/ui_check.py` clicks through the UI in headless Chrome with no model: open the pilot workspace, run, see
417 validated cells, trace a cell, sign-off blocked until the review is confirmed, sign off, export, the agent's
error when the model is offline, theme, and phone width without horizontal scroll (13 checks).

## Bug-fix evaluation

`scripts/fix_eval.py` measures whether the agent can repair a broken program. A known-good TEAE program gets one
seeded bug; the agent is told only that validation found mismatches. A trial counts as fixed only if the latest run's
independent validation passes with zero mismatches and the approved spec is unchanged. `--dry` checks, without a
model, that the validator catches every seeded bug (12/12).

| Seeded bug | Mismatched cells |
|---|---|
| PT counts records (`n()`) instead of subjects | 158 |
| SOC counts records instead of subjects | 44 |
| Treatment-emergent filter dropped | 56 |
| Denominator from subjects with events | 417 |
| Efficacy population in place of safety | 417 |
| Lowest-level term (AELLT) in place of preferred term | 491 |

*Warm*: a passing version exists to compare against. *Cold*: the buggy program is the first version.
Qwen2.5-Coder-32B-Instruct-AWQ on one A100 (vLLM), 2 trials per bug and mode, 2026-10-29:

| Agent version | Warm | Cold | Total |
|---|---|---|---|
| validation pattern + compare tool | 10/12 | 8/12 | 18/24 |
| + spec-vs-code facts, fix nudge, in-turn context trim | 8/12 | 12/12 | 20/24 |
| + write guards, diff in validation, output-limit nudge | 8/12 | 9/11 | 17/23 |
| + `edit_program` (exact text replacement) | 11/12 | 11/12 | 22/24 |

With 2 trials per cell, runs differ by 2–3 fixes on their own, so read the trend, not single rows. In no trial did
validation pass a wrong table, and the approved spec never changed. Trials where the model could not be reached
are reported as invalid and excluded.

## Data

The CDISC pilot ADSL and ADAE (CDISCPILOT01) are downloaded on demand from
[phuse-org/phuse-scripts](https://github.com/phuse-org/phuse-scripts/tree/master/data/adam/cdisc) and checked against
pinned SHA-256 hashes. They are not committed.
