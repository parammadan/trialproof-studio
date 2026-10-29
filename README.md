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

## Data

The CDISC pilot ADSL and ADAE (CDISCPILOT01) are downloaded on demand from
[phuse-org/phuse-scripts](https://github.com/phuse-org/phuse-scripts/tree/master/data/adam/cdisc) and checked against
pinned SHA-256 hashes. They are not committed.
