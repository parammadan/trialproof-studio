"""Sandboxed R execution. Each run gets a fresh directory holding the program; the process can read its own
workspace's datasets and the R toolchain, write only inside its run directory, and has no network.

Backends:
  sandbox  macOS sandbox-exec with r/sandbox.sb (default on macOS)
  bwrap    Linux bubblewrap (default on Linux): new user, mount, PID, network, IPC and UTS namespaces; the
           filesystem holds only the system toolchain (read-only), the workspace's datasets (read-only), the R
           helpers (read-only) and the run directory; empty environment; no capabilities
  docker   `docker run --network none` with the run and data directories mounted (TP_EXECUTOR=docker)

Every backend also gets CPU-time, memory, file-size, process and open-file limits, a wall-clock timeout, an
output cap and a cap on concurrent runs.

Fail closed: no program runs until `self_test` has shown, inside the sandbox itself, that a program can read its
own datasets and write its output, and cannot read the database, the application source, another workspace,
the server's environment or the network. TP_ALLOW_UNISOLATED=1 overrides this for local development only.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import signal
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable

from .datasets import sha256

R_DIR = Path(__file__).resolve().parents[1] / "r"
APP_DIR = R_DIR.parents[1]
TIMEOUT = int(os.environ.get("TP_RUN_TIMEOUT", "120"))
BACKEND = os.environ.get("TP_EXECUTOR", "sandbox" if sys.platform == "darwin" else "bwrap")
MEM_BYTES = int(os.environ.get("TP_RUN_MEMORY_MB", "2048")) * 2**20
OUTPUT_LIMIT = int(os.environ.get("TP_RUN_OUTPUT_KB", "1024")) * 1024      # console bytes kept per run
TABLE_LIMIT = int(os.environ.get("TP_RUN_TABLES_MB", "20")) * 2**20        # emitted table JSON per run
NPROC = int(os.environ.get("TP_RUN_NPROC", "128"))
MAX_CONCURRENT = int(os.environ.get("TP_MAX_CONCURRENT_RUNS", "2"))
ALLOW_UNISOLATED = os.environ.get("TP_ALLOW_UNISOLATED") == "1"
DOCKER_IMAGE = os.environ.get("TP_DOCKER_IMAGE", "rocker/tidyverse:4.4.1")
LANG = "en_US.UTF-8"


class IsolationError(RuntimeError):
    pass


@dataclass
class RunResult:
    run_id: str
    ok: bool
    status: str  # succeeded | failed | cancelled | timeout
    console: str
    result: dict
    tables: list[dict] = field(default_factory=list)
    duration: float = 0.0
    provenance: dict = field(default_factory=dict)


def _bwrap(run_dir: Path, data_dir: Path, script: list[str]) -> list[str]:
    a = ["bwrap", "--unshare-all", "--die-with-parent", "--new-session", "--clearenv", "--cap-drop", "ALL",
         "--ro-bind", "/usr", "/usr", "--ro-bind", "/etc", "/etc"]
    for d in ("bin", "lib", "lib32", "lib64", "libx32", "sbin"):
        p = Path("/" + d)
        if p.is_symlink():
            a += ["--symlink", os.readlink(p), str(p)]
        elif p.is_dir():
            a += ["--ro-bind", str(p), str(p)]
    a += ["--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp",
          "--ro-bind", str(data_dir), "/data", "--ro-bind", str(R_DIR), "/tp", "--bind", str(run_dir), "/work",
          "--chdir", "/work",
          "--setenv", "PATH", "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
          "--setenv", "HOME", "/work", "--setenv", "TMPDIR", "/work/tmp", "--setenv", "LANG", LANG,
          "--setenv", "TP_DATA_DIR", "/data", "--setenv", "TP_OUT_DIR", "/work/out",
          "Rscript", "--vanilla", "/tp/" + script[0], *script[1:]]
    return a


def _command(run_dir: Path, data_dir: Path, var_dir: Path, script: list[str] | None = None) -> list[str]:
    """The sandboxed command for an R script in server/r (default: runner.R on program.R), run in run_dir."""
    script = script or ["runner.R", "program.R"]
    if BACKEND == "bwrap":
        return _bwrap(run_dir, data_dir, script)
    if BACKEND == "docker":
        return ["docker", "run", "--rm", "--network", "none", "--memory", "2g", "--cpus", "2", "--pids-limit", str(NPROC),
                "-v", f"{run_dir}:/run", "-v", f"{data_dir}:/data:ro", "-v", f"{R_DIR}:/tp:ro",
                "-e", "TP_DATA_DIR=/data", "-e", "TP_OUT_DIR=/run/out", "-e", "TMPDIR=/run/tmp",
                "-w", "/run", DOCKER_IMAGE, "Rscript", "--vanilla", "/tp/" + script[0], *script[1:]]
    if BACKEND == "sandbox":
        return ["sandbox-exec", "-D", f"RUN_DIR={run_dir}", "-D", f"DATA_DIR={data_dir}", "-D", f"R_DIR={R_DIR}",
                "-D", f"HOME_DIR={Path.home().resolve()}", "-D", f"VAR_DIR={var_dir.resolve()}", "-D", f"APP_DIR={APP_DIR}",
                "-f", str(R_DIR / "sandbox.sb"), "Rscript", "--vanilla", str(R_DIR / script[0]), *script[1:]]
    raise IsolationError(f"unknown executor backend {BACKEND!r}; refusing to run R without a sandbox")


def _limits() -> None:  # runs in the child before exec; limits are inherited through the sandbox
    import resource
    want = [(resource.RLIMIT_CPU, TIMEOUT + 5), (resource.RLIMIT_FSIZE, TABLE_LIMIT), (resource.RLIMIT_NOFILE, 256),
            (resource.RLIMIT_CORE, 0)]
    if sys.platform != "darwin":  # macOS ignores RLIMIT_AS, and RLIMIT_NPROC there counts every process of the user
        want += [(resource.RLIMIT_AS, MEM_BYTES), (resource.RLIMIT_NPROC, NPROC)]
    for res, val in want:
        try:
            resource.setrlimit(res, (val, val))
        except (ValueError, OSError):
            pass


def backend_label() -> str:
    return {"sandbox": "macOS sandbox, no network", "bwrap": "Linux namespaces (bubblewrap), no network",
            "docker": "Docker, no network"}.get(BACKEND, BACKEND)


class Executor:
    def __init__(self, var_dir: Path):
        self.var_dir = var_dir
        self.procs: dict[str, tuple[Path, asyncio.subprocess.Process]] = {}
        self.cancelled: set[str] = set()
        self.slots = asyncio.Semaphore(MAX_CONCURRENT)
        self.isolation: dict = {"verified": False, "status": "not tested", "backend": BACKEND, "checks": {}}

    def allowed(self) -> bool:
        return self.isolation["verified"] or ALLOW_UNISOLATED

    async def run(self, ws_path: Path, code: str, on_event: Callable[[str, dict], Awaitable[None] | None],
                  _self_test: bool = False) -> RunResult:
        if not _self_test and not self.allowed():
            raise IsolationError("R execution is disabled: the sandbox has not passed its isolation self-test "
                                 f"({self.isolation['status']}).")
        async with self.slots:
            return await self._run(ws_path, code, on_event)

    async def _run(self, ws_path: Path, code: str, on_event) -> RunResult:
        run_id = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
        run_dir = (ws_path / "runs" / run_id).resolve()
        (run_dir / "out" / "tables").mkdir(parents=True)
        (run_dir / "tmp").mkdir()
        (run_dir / "program.R").write_text(code)
        data_dir = (ws_path / "datasets").resolve()
        data_dir.mkdir(exist_ok=True)
        env = {"PATH": os.environ["PATH"], "HOME": str(run_dir), "TMPDIR": str(run_dir / "tmp"),
               "TP_DATA_DIR": str(data_dir), "TP_OUT_DIR": str(run_dir / "out"), "LANG": LANG}
        t0 = time.time()
        proc = await asyncio.create_subprocess_exec(
            *_command(run_dir, data_dir, self.var_dir), cwd=run_dir, env=env, start_new_session=True,
            preexec_fn=_limits, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        self.procs[run_id] = (ws_path, proc)
        await _maybe(on_event("run_start", {"run_id": run_id, "backend": BACKEND}))
        console: list[str] = []
        kept = [0]
        over = [False]

        async def line(text: str, is_err: bool):
            if is_err and text.startswith("\x1eTP"):
                parts = text[3:].split()
                if parts and parts[0] == "STEP":
                    await _maybe(on_event("run_step", {"run_id": run_id, "step": int(parts[1]),
                                                       "line": int(parts[2]), "end_line": int(parts[3])}))
                elif parts and parts[0] == "BEGIN":
                    await _maybe(on_event("run_step", {"run_id": run_id, "step": 0, "total": int(parts[1])}))
                return
            if text.startswith("OMP: Warning #179"):  # libomp probing /tmp shared memory; harmless
                return
            if over[0]:
                return
            kept[0] += len(text)
            if kept[0] > OUTPUT_LIMIT:
                over[0] = True
                self._kill(proc)
                text = f"\n[output limit of {OUTPUT_LIMIT // 1024} KB reached; the run was stopped]\n"
            console.append(text)
            await _maybe(on_event("run_output", {"run_id": run_id, "text": text, "stream": "stderr" if is_err else "stdout"}))

        async def pump(stream, is_err: bool):
            buf = b""
            while True:
                chunk = await stream.read(65536)
                if not chunk:
                    break
                buf += chunk
                *lines, buf = buf.split(b"\n")
                for ln in lines:
                    await line(ln.decode(errors="replace") + "\n", is_err)
                if len(buf) > 65536:  # one very long line: pass it on in pieces
                    await line(buf.decode(errors="replace"), is_err)
                    buf = b""
            if buf:
                await line(buf.decode(errors="replace"), is_err)

        status = "succeeded"
        try:
            await asyncio.wait_for(asyncio.gather(pump(proc.stdout, False), pump(proc.stderr, True), proc.wait()), TIMEOUT)
        except asyncio.TimeoutError:
            status = "timeout"
            self._kill(proc)
            await proc.wait()
        finally:
            self.procs.pop(run_id, None)
        if run_id in self.cancelled:
            status = "cancelled"
            self.cancelled.discard(run_id)
        sfile = run_dir / "out" / "status.json"
        try:
            result = json.loads(sfile.read_text()) if sfile.exists() else {"ok": False}
        except (ValueError, OSError):
            result = {"ok": False}
        if status == "succeeded" and (proc.returncode != 0 or not result.get("ok")):
            status = "failed"
        if status in ("timeout", "cancelled"):
            result = {"ok": False, "kind": status, "message": f"run {status}" + (f" after {TIMEOUT}s" if status == "timeout" else "")}
        if over[0]:
            status, result = "failed", {"ok": False, "kind": "limit", "message": f"output limit of {OUTPUT_LIMIT // 1024} KB reached"}
        if status == "failed" and not result.get("message") and proc.returncode:
            tail = "".join(console)[-600:].strip()
            result = {"ok": False, "kind": "runtime", "message": f"R exited with status {proc.returncode}" + (f": {tail}" if tail else "")}
        tables = []
        tdir = run_dir / "out" / "tables"
        files = sorted(tdir.glob("*.json")) if tdir.exists() else []
        if sum(p.stat().st_size for p in files) > TABLE_LIMIT:
            status, result = "failed", {"ok": False, "kind": "limit", "message": f"emitted tables exceed {TABLE_LIMIT // 2**20} MB"}
        else:
            for p in files:
                try:
                    tables.append(json.loads(p.read_text()))
                except ValueError:
                    pass
        prov = {
            "program_sha256": sha256(run_dir / "program.R"),
            "datasets": {d.name: sha256(next(d.glob("source.*"))) for d in data_dir.iterdir() if d.is_dir() and any(d.glob("source.*"))},
            "backend": BACKEND, "isolation": backend_label(),
            "r_version": result.get("r_version"),
            "packages": result.get("packages"),
        }
        return RunResult(run_id, status == "succeeded", status, "".join(console), result, tables, time.time() - t0, prov)

    async def ingest(self, stage: Path, source: str, fmt: str) -> tuple[int, str]:
        """Parse an uploaded file with ingest.R inside the same sandbox and limits as programs. `stage` holds only
        the uploaded file (`source`); the parsed output is written to stage/parsed. Returns (exit code, stderr)."""
        if not self.allowed():
            raise IsolationError("dataset parsing is disabled: the sandbox has not passed its isolation self-test "
                                 f"({self.isolation['status']}).")
        stage = stage.resolve()
        empty = stage / "nodata"
        empty.mkdir(exist_ok=True)
        (stage / "tmp").mkdir(exist_ok=True)
        env = {"PATH": os.environ["PATH"], "HOME": str(stage), "TMPDIR": str(stage / "tmp"), "LANG": LANG}
        async with self.slots:
            proc = await asyncio.create_subprocess_exec(
                *_command(stage, empty, self.var_dir, ["ingest.R", source, fmt, "parsed"]), cwd=stage, env=env,
                start_new_session=True, preexec_fn=_limits, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            try:
                _, err = await asyncio.wait_for(proc.communicate(), TIMEOUT)
            except asyncio.TimeoutError:
                self._kill(proc)
                await proc.wait()
                return -1, f"parsing timed out after {TIMEOUT}s"
        return proc.returncode, err.decode(errors="replace")[-2000:]

    async def self_test(self, db_path: Path) -> dict:
        """Run a probe program inside the sandbox and record whether isolation holds. Sets self.isolation."""
        base = self.var_dir / "selftest" / uuid.uuid4().hex[:8]
        mine, other = base / "mine", base / "other"
        token = uuid.uuid4().hex
        os.environ["TP_ISOLATION_CANARY"] = token  # in the server's environment only; must stay invisible to R
        try:
            (mine / "datasets" / "OK").mkdir(parents=True)
            (mine / "runs").mkdir()
            (mine / "datasets" / "OK" / "data.csv").write_text("x\n1\n")
            (other / "datasets" / "SECRET").mkdir(parents=True)
            (other / "datasets" / "SECRET" / "canary.txt").write_text(token)
            p = {k: str(v) for k, v in {"db": db_path.resolve(), "src": APP_DIR / "server" / "tp" / "main.py",
                                        "app": APP_DIR, "other": other / "datasets" / "SECRET" / "canary.txt",
                                        "appw": APP_DIR / "server" / "tp" / "zz_selftest.txt",
                                        "varw": self.var_dir.resolve() / "zz_selftest.txt"}.items()}
            # the program text is echoed to the console, so it carries the token reversed and R reverses it back
            code = SELF_TEST_R.replace("@RTOKEN@", token[::-1])
            for k, v in p.items():
                code = code.replace(f"@{k.upper()}@", json.dumps(v))
            res = await self.run(mine, code, lambda *_: None, _self_test=True)
            out = mine / "runs" / res.run_id / "out" / "selftest.json"
            got = json.loads(out.read_text()) if out.exists() else None
            leaked = token in res.console
            for f in (p["appw"], p["varw"]):  # remove anything a failed sandbox managed to write
                Path(f).unlink(missing_ok=True)
        except Exception as e:  # the sandbox could not even start: fail closed
            got, leaked, res = None, False, None
            err = f"{type(e).__name__}: {e}"
        else:
            err = None
        finally:
            os.environ.pop("TP_ISOLATION_CANARY", None)
            shutil.rmtree(base, ignore_errors=True)
        if got is None:
            tail = (res.console[-800:] if res else "") or err or ""
            self.isolation = {"verified": False, "status": "sandbox did not run", "backend": BACKEND, "checks": {},
                              "detail": tail.strip()}
            return self.isolation
        checks = {}
        for k, want in EXPECT.items():
            checks[k] = {"expected": want, "observed": bool(got.get(k)), "ok": bool(got.get(k)) == want}
        checks["canary_not_in_output"] = {"expected": True, "observed": not leaked, "ok": not leaked}
        ok = all(c["ok"] for c in checks.values())
        failed = [k for k, c in checks.items() if not c["ok"]]
        self.isolation = {"verified": ok, "status": "passed" if ok else "FAILED: " + ", ".join(failed),
                          "backend": BACKEND, "checks": checks, "tested_at": time.time()}
        return self.isolation

    def _kill(self, proc):
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

    def cancel(self, ws_path: Path) -> int:
        n = 0
        for rid, (path, proc) in list(self.procs.items()):
            if path != ws_path:
                continue
            self.cancelled.add(rid)
            self._kill(proc)
            n += 1
        return n


# What a sandboxed program must (True) and must not (False) be able to do
EXPECT = {
    "read_own_dataset": True, "write_own_output": True,
    "read_database": False, "read_app_source": False, "list_app_dir": False, "read_other_workspace": False,
    "write_app_source_dir": False, "write_data_root": False, "write_own_dataset": False,
    "env_canary": False, "proc_canary": False, "network_tcp": False, "network_http": False,
}

SELF_TEST_R = r'''
tok <- paste(rev(strsplit("@RTOKEN@", "")[[1]]), collapse = "")
ok <- function(expr) tryCatch({ force(expr); TRUE }, error = function(e) FALSE, warning = function(w) FALSE)
r <- list()
r$read_own_dataset <- ok(stopifnot(length(readLines(file.path(Sys.getenv("TP_DATA_DIR"), "OK", "data.csv"))) == 2))
r$write_own_output <- ok(writeLines("ok", file.path(Sys.getenv("TP_OUT_DIR"), "probe.txt")))
r$read_database <- ok(stopifnot(length(readBin(@DB@, "raw", 16)) > 0))
r$read_app_source <- ok(stopifnot(length(readLines(@SRC@, n = 1)) > 0))
r$list_app_dir <- ok(stopifnot(length(list.files(@APP@)) > 0))
r$read_other_workspace <- ok(stopifnot(length(readLines(@OTHER@, warn = FALSE)) > 0))
r$write_app_source_dir <- ok(writeLines("x", @APPW@))
r$write_data_root <- ok(writeLines("x", @VARW@))
r$write_own_dataset <- ok(writeLines("x", file.path(Sys.getenv("TP_DATA_DIR"), "OK", "data.csv")))
r$env_canary <- any(grepl(tok, Sys.getenv(), fixed = TRUE))
scan <- function(f) tryCatch({ b <- readBin(f, "raw", 1e6); b[b == as.raw(0)] <- as.raw(10); grepl(tok, rawToChar(b), fixed = TRUE) }, error = function(e) FALSE)
r$proc_canary <- any(vapply(c(Sys.glob("/proc/[0-9]*/environ"), Sys.glob("/proc/[0-9]*/cmdline")), scan, logical(1)))
r$network_tcp <- ok({ s <- socketConnection("1.1.1.1", 80, blocking = TRUE, open = "r+", timeout = 3); close(s) })
r$network_http <- ok(readLines(url("http://example.com"), n = 1))
r$proc_pids_visible <- length(Sys.glob("/proc/[0-9]*"))
jsonlite::write_json(r, file.path(Sys.getenv("TP_OUT_DIR"), "selftest.json"), auto_unbox = TRUE)
'''


async def _maybe(x):
    if asyncio.iscoroutine(x):
        await x


def docker_available() -> bool:
    return shutil.which("docker") is not None
