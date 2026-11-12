# TrialProof Studio — security

TrialProof runs R programs written by people and by a language model, and the public demo lets anyone on the internet
do that. This page describes what protects the server, how each protection is checked, and what is still open.

## Threat model

Untrusted: every visitor; every R program (user-written or model-written); dataset contents (they reach the model's
prompt); the model's output. Trusted: the operator, the hosting proxy (Fly), the server process and its configuration.

Assets: the model API key; other visitors' workspaces (data, chat, programs, validations, sign-offs); the SQLite
database and the application source (tampering with either could forge a "passed" validation); the model quota
(shared across visitors); the machine itself (CPU, memory, disk).

## Controls

### 1. R runs in a sandbox, and only after the sandbox has proven itself (`server/tp/executor.py`)

| Backend | Where | Isolation |
|---|---|---|
| `bwrap` (default on Linux, used on Fly) | bubblewrap | New user, mount, PID, network, IPC and UTS namespaces; `--clearenv`; `--cap-drop ALL`; `--die-with-parent`; `--new-session`. The filesystem holds only `/usr`, `/etc` and the `/bin`/`/lib` links (read-only), the workspace's own `datasets/` (read-only, at `/data`), the R helpers (read-only, at `/tp`), the run directory (read-write, at `/work`), a private `/proc`, `/dev` and `/tmp`. The database, the application folder, other workspaces and the server's environment do not exist inside. |
| `sandbox` (default on macOS) | `sandbox-exec` with `server/r/sandbox.sb` | No network; no reads of the home folder, the application folder or the data folder except this run's directory, this workspace's datasets and the R helpers; writes only in the run directory. |
| `docker` (optional) | `docker run --network none` | Container per run. Not used in production; covered by the same self-test if enabled. |

Every backend also gets: CPU time (`timeout + 5` s), address space (`TP_RUN_MEMORY_MB`, Linux), file size
(`TP_RUN_TABLES_MB`), process count (`TP_RUN_NPROC`, Linux), open files (256) and core-dump limits; a wall-clock
timeout (`TP_RUN_TIMEOUT`); a console output cap (`TP_RUN_OUTPUT_KB`, the run is stopped when it is reached); a cap on
emitted table size; and a cap on concurrent runs (`TP_MAX_CONCURRENT_RUNS`).

**Fail closed.** At startup the server runs a probe program through the sandbox (`Executor.self_test`). The probe must
be able to read its own dataset and write its output, and must **not** be able to read the database, read or list the
application source, read another workspace, write into the source or data folders, modify its own datasets, see a
random canary placed in the server's environment (directly or through `/proc`), or open a network connection. Until
every check passes, every run is refused with HTTP 503 ("R execution is disabled…"). An unknown backend, a missing
`bwrap`, or a host that forbids user namespaces all fail the self-test. `GET /api/health` reports the result
(`execution.enabled`, `execution.self_test`, and each check). `TP_ALLOW_UNISOLATED=1` overrides this for local
development only; never set it on a public server.

Uploaded files are parsed by `Rscript ingest.R` **outside** the sandbox. Uploads are therefore disabled in public mode
(only the PhUSE pilot files, checked against pinned SHA-256 hashes, are loaded). Do not enable uploads on a public
server until ingest also runs in the sandbox.

### 2. Sessions and workspace ownership (`server/tp/main.py`, `guard`)

Every API client gets a server-issued session: a random 256-bit token in an `HttpOnly`, `SameSite=Lax` cookie
(`Secure` over HTTPS). Only its SHA-256 is stored. A cookie the server did not issue is treated as no cookie. Each
workspace records the session that created it; every `/api/workspaces/{id}/…` request (reads, writes, the event
stream, export) answers **404** unless the workspace belongs to the caller's session, and the workspace list shows only
your own. Workspaces created before sessions existed (owner unset) stay reachable on a local, non-public server only.
Dataset names are validated (`^[A-Za-z0-9_]{1,64}$`) wherever they are used, so neither a client nor the model can
reach another workspace's files through a name like `../…`.

### 3. Client address and rate limits

The client address is the value of `TP_CLIENT_IP_HEADER` (on Fly: `fly-client-ip`, which Fly's proxy sets and
overwrites) or else the TCP peer. `X-Forwarded-For` and similar headers are never used, and uvicorn is started with
`--no-proxy-headers` so it does not rewrite the peer address from them either. In public mode every write is counted
per client address **and** per session over a sliding hour: workspaces 10, runs 40, agent messages 20, new sessions
20 (per address), any write 300.

### 4. Model budgets (`server/tp/agent.py`)

Per UTC day, counted in the `usage` table: agent turns (counted when a turn starts) and provider-reported tokens
(prompt + completion, added after every model step). Budgets apply per session, per client address and globally; the
global token budget keeps a margin below the provider's own daily limit. A turn that would start over budget is
refused (HTTP 429 and a visible `quota` message); a turn that crosses a token budget stops after the current step. A
provider quota error is shown as a plain message, not raw JSON. Public-mode defaults: session 15 turns / 40,000 tokens,
address 30 / 80,000, global 150,000 tokens. Set them with `TP_BUDGET_{SESSION,IP,GLOBAL}_{TURNS,TOKENS}` (0 =
unlimited; local servers default to unlimited).

### 5. Credentials

The model key reaches the server only as an environment variable (a Fly secret). It is never returned by the API
(`/api/health` shows `has_key` only), never passed to R (the sandbox environment is cleared and the server's `/proc` is
not visible), and never sent to the browser. In public mode the settings endpoints are disabled (403). On a local
server, a key entered in Settings is stored in plaintext in the local SQLite database.

**Rotating the model key (Groq):**

1. Create a new key at console.groq.com → API Keys.
2. Set it without putting it on a command line:
   `printf 'TP_LLM_API_KEY=%s\n' "$(pbpaste)" | fly secrets import -a trialproof-studio`
   (or paste it in the Fly dashboard → Secrets). Fly restarts the machine with the new key.
3. Check `curl -s https://trialproof-studio.fly.dev/api/health` shows `"has_key": true`, then send one agent message.
4. Delete the old key in the Groq console.

Rotate after any exposure (for example, a key pasted into a chat, an issue or a log).

## Verifying

| What | Command | Where |
|---|---|---|
| Unit and integration tests (sandbox, fail-closed, sessions, rate limits, budgets, normal flow) | `cd server && .venv/bin/python -m pytest -q tests/` | locally, and on the Fly machine: `fly ssh console -C "sh -c 'cd /home/user/app/server && runuser -u user -- env HOME=/home/user .venv/bin/python -m pytest -q -p no:cacheprovider tests/'"` |
| Production acceptance (from outside, public API only) | `python scripts/security_acceptance.py --api https://trialproof-studio.fly.dev` | any client; uses ten of that address's hourly workspace creations |
| Startup self-test | `curl -s https://trialproof-studio.fly.dev/api/health` → `execution` | production |

## Residual risks

- **Shared kernel.** The sandbox shares the Linux kernel and the VM with the server. A kernel or bubblewrap escape
  would reach the server. A stronger design runs R in a separate Fly Machine per run (or per session) that holds no
  data except the workspace's datasets, with no secrets and no private network; see the Milestone 1 report.
- **Reads of system files.** Programs can read `/usr` and `/etc` (no secrets live there on this image).
- **Denial of service.** Limits bound one run; many sessions from many addresses can still keep the single machine
  busy. Fly's auto-stop bounds cost, not availability.
- **Prompt injection through data.** Dataset values reach the model. The model's tools cannot approve specifications
  or sign off, and its R runs in the sandbox, so the effect is limited to what the agent says and writes.
- **No user accounts.** A session is a browser cookie; clearing it loses access to its workspaces (by design for an
  anonymous demo). Workspaces are not persistent on the Fly machine (no volume).
- **Unpinned Python dependencies** and a DOMPurify advisory in Monaco (see the audit).
