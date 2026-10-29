"""Sandboxed R execution. Each run gets a fresh directory holding the program; the process can read the
workspace datasets and the R toolchain, write only inside its run directory, and has no network.

Backends:
  sandbox  macOS sandbox-exec with r/sandbox.sb (default on macOS)
  docker   `docker run --network none` with the run and data directories mounted (TP_EXECUTOR=docker)
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
TIMEOUT = int(os.environ.get("TP_RUN_TIMEOUT", "120"))
BACKEND = os.environ.get("TP_EXECUTOR", "sandbox" if sys.platform == "darwin" else "docker")
DOCKER_IMAGE = os.environ.get("TP_DOCKER_IMAGE", "rocker/tidyverse:4.4.1")


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


def _command(run_dir: Path, data_dir: Path) -> list[str]:
    if BACKEND == "docker":
        return ["docker", "run", "--rm", "--network", "none", "--memory", "2g", "--cpus", "2",
                "-v", f"{run_dir}:/run", "-v", f"{data_dir}:/data:ro", "-v", f"{R_DIR}:/tp:ro",
                "-e", "TP_DATA_DIR=/data", "-e", "TP_OUT_DIR=/run/out", "-e", "TMPDIR=/run/tmp",
                "-w", "/run", DOCKER_IMAGE, "Rscript", "--vanilla", "/tp/runner.R", "program.R"]
    return ["sandbox-exec", "-D", f"RUN_DIR={run_dir}", "-D", f"DATA_DIR={data_dir}", "-D", f"R_DIR={R_DIR}",
            "-D", f"HOME_DIR={Path.home().resolve()}", "-f", str(R_DIR / "sandbox.sb"),
            "Rscript", "--vanilla", str(R_DIR / "runner.R"), "program.R"]


class Executor:
    def __init__(self):
        self.procs: dict[str, tuple[Path, asyncio.subprocess.Process]] = {}
        self.cancelled: set[str] = set()

    async def run(self, ws_path: Path, code: str, on_event: Callable[[str, dict], Awaitable[None] | None]) -> RunResult:
        run_id = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
        run_dir = (ws_path / "runs" / run_id).resolve()
        (run_dir / "out").mkdir(parents=True)
        (run_dir / "tmp").mkdir()
        (run_dir / "program.R").write_text(code)
        data_dir = (ws_path / "datasets").resolve()
        env = {"PATH": os.environ["PATH"], "HOME": str(run_dir), "TMPDIR": str(run_dir / "tmp"),
               "TP_DATA_DIR": str(data_dir), "TP_OUT_DIR": str(run_dir / "out"), "LANG": "en_US.UTF-8"}
        t0 = time.time()
        proc = await asyncio.create_subprocess_exec(
            *_command(run_dir, data_dir), cwd=run_dir, env=env, start_new_session=True,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        self.procs[run_id] = (ws_path, proc)
        await _maybe(on_event("run_start", {"run_id": run_id, "backend": BACKEND}))
        console: list[str] = []

        async def pump(stream, is_err: bool):
            while True:
                line = await stream.readline()
                if not line:
                    break
                text = line.decode(errors="replace")
                if is_err and text.startswith("\x1eTP"):
                    parts = text[3:].split()
                    if parts and parts[0] == "STEP":
                        await _maybe(on_event("run_step", {"run_id": run_id, "step": int(parts[1]),
                                                           "line": int(parts[2]), "end_line": int(parts[3])}))
                    elif parts and parts[0] == "BEGIN":
                        await _maybe(on_event("run_step", {"run_id": run_id, "step": 0, "total": int(parts[1])}))
                    continue
                console.append(text)
                await _maybe(on_event("run_output", {"run_id": run_id, "text": text, "stream": "stderr" if is_err else "stdout"}))

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
        result = json.loads(sfile.read_text()) if sfile.exists() else {"ok": False}
        if status == "succeeded" and (proc.returncode != 0 or not result.get("ok")):
            status = "failed"
        if status in ("timeout", "cancelled"):
            result = {"ok": False, "kind": status, "message": f"run {status}" + (f" after {TIMEOUT}s" if status == "timeout" else "")}
        tables = []
        tdir = run_dir / "out" / "tables"
        if tdir.exists():
            for p in sorted(tdir.glob("*.json")):
                tables.append(json.loads(p.read_text()))
        prov = {
            "program_sha256": sha256(run_dir / "program.R"),
            "datasets": {d.name: sha256(next(d.glob("source.*"))) for d in data_dir.iterdir() if d.is_dir() and any(d.glob("source.*"))},
            "backend": BACKEND,
            "r_version": result.get("r_version"),
            "packages": result.get("packages"),
        }
        return RunResult(run_id, status == "succeeded", status, "".join(console), result, tables, time.time() - t0, prov)

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


async def _maybe(x):
    if asyncio.iscoroutine(x):
        await x


def docker_available() -> bool:
    return shutil.which("docker") is not None
