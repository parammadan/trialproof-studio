"""Shared fixtures: real servers in public mode, each with its own data folder."""
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

SERVER = Path(__file__).resolve().parents[1]

def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


@pytest.fixture
def public_server(tmp_path):
    procs = []

    def start(**env):
        port = _free_port()
        e = {**os.environ, "TP_PUBLIC": "1", "TP_VAR": str(tmp_path / f"var{port}"), "TP_LLM_BASE_URL": "http://127.0.0.1:9/v1",
             "TP_LLM_MODEL": "none", "TP_LLM_API_KEY": "SECRET_FOR_TEST", **{k: str(v) for k, v in env.items()}}
        p = subprocess.Popen([sys.executable, "-m", "uvicorn", "tp.main:app", "--port", str(port), "--no-proxy-headers"], cwd=SERVER, env=e,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        procs.append(p)
        url = f"http://127.0.0.1:{port}"
        for _ in range(120):
            try:
                if httpx.get(url + "/api/health", timeout=2).status_code == 200:
                    return url
            except httpx.HTTPError:
                time.sleep(0.25)
        raise RuntimeError("server did not start")

    yield start
    for p in procs:
        p.terminate()
        p.wait(10)


