"""Dataset ingest (parsed by R so CSV, XPT and RDS all land in one canonical form), previews,
relationships between datasets, and small tabulations for the agent's inspection tools."""
from __future__ import annotations

import asyncio
import csv
import hashlib
import json
import re
import shutil
import time
import uuid
from collections import Counter
from pathlib import Path

from .store import Store

R_DIR = Path(__file__).resolve().parents[1] / "r"
FORMATS = {".csv": "csv", ".xpt": "xpt", ".rds": "rds"}
PILOT_DIR = Path(__file__).resolve().parents[2] / "data" / "cdisc-pilot"
# SHA-256 of the files published by PhUSE (github.com/phuse-org/phuse-scripts, data/adam/cdisc)
PILOT_SHA = {
    "adsl.xpt": "c5139f873a93ef6add77bc4297beb6460a398bbdd036874382a9f39dfc92091d",
    "adae.xpt": "b8678e70946473a753bb01d002917f478bf51b59bdd0dc19587b97128059b6a0",
}
PILOT_URL = "https://github.com/phuse-org/phuse-scripts/raw/master/data/adam/cdisc/{}"


class IngestError(Exception):
    pass


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def dataset_name(filename: str) -> str:
    stem = Path(filename).stem
    name = re.sub(r"[^A-Za-z0-9_]", "_", stem).upper().strip("_")
    return name or "DATA"


async def ingest(store: Store, wid: str, src: Path, filename: str, source: str = "upload") -> dict:
    ext = Path(filename).suffix.lower()
    if ext not in FORMATS:
        raise IngestError(f"{filename}: unsupported file type (use .csv, .xpt or .rds)")
    name = dataset_name(filename)
    out = store.ws_path(wid) / "datasets" / name
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    raw = out / f"source{ext}"
    shutil.copyfile(src, raw)
    proc = await asyncio.create_subprocess_exec(
        "Rscript", "--vanilla", str(R_DIR / "ingest.R"), str(raw), FORMATS[ext], str(out),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    _, err = await asyncio.wait_for(proc.communicate(), timeout=180)
    profile_path = out / "profile.json"
    profile = json.loads(profile_path.read_text()) if profile_path.exists() else {"ok": False}
    if proc.returncode != 0 or not profile.get("ok"):
        shutil.rmtree(out, ignore_errors=True)
        msg = profile.get("error") or err.decode(errors="replace").strip().splitlines()[-1:] or ["parse failed"]
        raise IngestError(f"{filename}: {msg if isinstance(msg, str) else msg[0]}")
    digest = sha256(raw)
    store.x("delete from datasets where ws_id=? and name=?", (wid, name))
    did = uuid.uuid4().hex[:10]
    store.x(
        "insert into datasets values(?,?,?,?,?,?,?,?,?,?)",
        (did, wid, name, filename, FORMATS[ext], digest, profile["nrow"], profile["ncol"], source, time.time()),
    )
    return {"id": did, "name": name, "filename": filename, "format": FORMATS[ext], "sha256": digest,
            "nrow": profile["nrow"], "ncol": profile["ncol"], "source": source}


async def load_pilot(store: Store, wid: str) -> list[dict]:
    """Load the public CDISC pilot ADSL + ADAE, checking each file against the PhUSE hash."""
    import httpx

    PILOT_DIR.mkdir(parents=True, exist_ok=True)
    out = []
    for fname, want in PILOT_SHA.items():
        path = PILOT_DIR / fname
        if not path.exists() or sha256(path) != want:
            async with httpx.AsyncClient(follow_redirects=True, timeout=60) as c:
                r = await c.get(PILOT_URL.format(fname))
                r.raise_for_status()
                path.write_bytes(r.content)
        if sha256(path) != want:
            raise IngestError(f"{fname}: downloaded file does not match the published SHA-256")
        out.append(await ingest(store, wid, path, fname, source="cdisc-pilot (PhUSE, sha256 verified)"))
    return out


def profile(store: Store, wid: str, name: str) -> dict:
    return json.loads((store.ws_path(wid) / "datasets" / name / "profile.json").read_text())


def read_rows(store: Store, wid: str, name: str) -> list[dict]:
    with open(store.ws_path(wid) / "datasets" / name / "data.csv", newline="") as f:
        return list(csv.DictReader(f))


def preview(store: Store, wid: str, name: str, offset: int = 0, limit: int = 100) -> dict:
    rows = read_rows(store, wid, name)
    return {"total": len(rows), "offset": offset, "rows": rows[offset:offset + limit]}


def relationships(store: Store, wid: str) -> list[dict]:
    """Shared columns between datasets, and for each candidate key (unique in the parent) how many of
    the child's values are found in the parent."""
    names = [d["name"] for d in store.q("select name from datasets where ws_id=? order by created_at", (wid,))]
    data = {n: read_rows(store, wid, n) for n in names}
    out = []
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            if not data[a] or not data[b]:
                continue
            shared = sorted(set(data[a][0]) & set(data[b][0]))
            if not shared:
                continue
            keys = []
            for col in shared:
                va = [r[col] for r in data[a] if r[col] != ""]
                vb = [r[col] for r in data[b] if r[col] != ""]
                for parent, child, pv, cv in ((a, b, va, vb), (b, a, vb, va)):
                    if pv and len(set(pv)) == len(pv) and len(set(pv)) > 1:
                        ps, cs = set(pv), set(cv)
                        keys.append({"column": col, "parent": parent, "child": child,
                                     "child_distinct": len(cs), "matched": len(cs & ps),
                                     "parent_without_child": len(ps - cs)})
            out.append({"a": a, "b": b, "shared_columns": shared, "keys": keys})
    return out


def tabulate(store: Store, wid: str, name: str, column: str, by: str | None = None, limit: int = 40) -> dict:
    rows = read_rows(store, wid, name)
    if rows and column not in rows[0]:
        raise KeyError(f"{name} has no column {column}")
    if by and rows and by not in rows[0]:
        raise KeyError(f"{name} has no column {by}")
    if by:
        c = Counter((r[by] or "<missing>", r[column] or "<missing>") for r in rows)
        items = [{by: k[0], column: k[1], "n": n} for k, n in c.most_common(limit)]
    else:
        c = Counter(r[column] or "<missing>" for r in rows)
        items = [{column: k, "n": n} for k, n in c.most_common(limit)]
    return {"dataset": name, "rows": len(rows), "distinct": len(c), "counts": items, "truncated": len(c) > limit}
