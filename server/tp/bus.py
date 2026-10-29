"""Per-workspace event bus. Every event is persisted first (audit trail), then pushed to live listeners."""
from __future__ import annotations

import asyncio
from collections import defaultdict

from .store import Store

# Kinds that are high-frequency stream fragments: pushed live but not persisted.
EPHEMERAL = {"assistant_delta", "code_delta", "run_output", "run_step"}


class Bus:
    def __init__(self, store: Store):
        self.store = store
        self.listeners: dict[str, set[asyncio.Queue]] = defaultdict(set)

    def emit(self, wid: str, kind: str, **payload) -> dict:
        if kind in EPHEMERAL:
            ev = {"id": None, "kind": kind, "payload": payload}
        else:
            ev = self.store.add_event(wid, kind, payload)
        for q in list(self.listeners[wid]):
            q.put_nowait(ev)
        return ev

    def subscribe(self, wid: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self.listeners[wid].add(q)
        return q

    def unsubscribe(self, wid: str, q: asyncio.Queue):
        self.listeners[wid].discard(q)
