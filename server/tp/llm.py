"""Streaming client for any OpenAI-compatible chat-completions endpoint (vLLM, llama.cpp server,
Groq, OpenRouter, ...). Yields text and tool-call fragments as they arrive."""
from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass, field
from typing import AsyncIterator
from urllib.parse import urlparse

import httpx


@dataclass
class LLMConfig:
    base_url: str = ""
    model: str = ""
    api_key: str = ""
    temperature: float = 0.2
    max_tokens: int = 3072

    @classmethod
    def from_env(cls, saved: dict | None = None) -> "LLMConfig":
        saved = saved or {}
        return cls(
            base_url=(saved.get("base_url") or os.environ.get("TP_LLM_BASE_URL", "")).rstrip("/"),
            model=saved.get("model") or os.environ.get("TP_LLM_MODEL", ""),
            api_key=saved.get("api_key") or os.environ.get("TP_LLM_API_KEY", ""),
            temperature=float(saved.get("temperature", os.environ.get("TP_LLM_TEMPERATURE", 0.2))),
        )

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.model)

    @property
    def host(self) -> str:
        return urlparse(self.base_url).netloc

    def public(self) -> dict:
        return {"base_url": self.base_url, "model": self.model, "has_key": bool(self.api_key),
                "configured": self.configured, "host": self.host}


class LLMError(Exception):
    pass


@dataclass
class ToolCall:
    id: str = ""
    name: str = ""
    arguments: str = ""


@dataclass
class Delta:
    kind: str  # text | tool_start | tool_args | done
    text: str = ""
    index: int = 0
    call: ToolCall | None = None
    finish_reason: str | None = None
    usage: dict = field(default_factory=dict)


async def stream_chat(cfg: LLMConfig, messages: list[dict], tools: list[dict], retries: int = 2) -> AsyncIterator[Delta]:
    if not cfg.configured:
        raise LLMError("no model endpoint configured (set it in Settings, or TP_LLM_BASE_URL and TP_LLM_MODEL)")
    body = {"model": cfg.model, "messages": messages, "tools": tools, "tool_choice": "auto", "stream": True,
            "temperature": cfg.temperature, "max_tokens": cfg.max_tokens,
            "stream_options": {"include_usage": True}}
    headers = {"Content-Type": "application/json"}
    if cfg.api_key:
        headers["Authorization"] = f"Bearer {cfg.api_key}"
    attempt = 0
    while True:
        started = False
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(300, connect=15), trust_env=False) as client:
                async with client.stream("POST", f"{cfg.base_url}/chat/completions", json=body, headers=headers) as r:
                    if r.status_code >= 400:
                        text = (await r.aread()).decode(errors="replace")[:500]
                        if r.status_code >= 500 and attempt < retries:
                            raise httpx.TransportError(f"HTTP {r.status_code}: {text}")
                        raise LLMError(f"model endpoint returned HTTP {r.status_code}: {text}")
                    calls: dict[int, ToolCall] = {}
                    finish, usage = None, {}
                    async for line in r.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if data == "[DONE]":
                            break
                        chunk = json.loads(data)
                        if chunk.get("usage"):
                            usage = chunk["usage"]
                        for ch in chunk.get("choices") or []:
                            d = ch.get("delta") or {}
                            if d.get("content"):
                                started = True
                                yield Delta("text", text=d["content"])
                            for tc in d.get("tool_calls") or []:
                                started = True
                                i = tc.get("index", 0)
                                fn = tc.get("function") or {}
                                if i not in calls:
                                    calls[i] = ToolCall(id=tc.get("id") or f"call_{i}", name=fn.get("name") or "")
                                    yield Delta("tool_start", index=i, call=calls[i])
                                elif fn.get("name"):
                                    calls[i].name += fn["name"]
                                if fn.get("arguments"):
                                    calls[i].arguments += fn["arguments"]
                                    yield Delta("tool_args", index=i, text=fn["arguments"], call=calls[i])
                            if ch.get("finish_reason"):
                                finish = ch["finish_reason"]
                    yield Delta("done", finish_reason=finish, usage=usage)
                    return
        except (httpx.TransportError, httpx.TimeoutException) as e:
            if started or attempt >= retries:
                raise LLMError(f"model endpoint unreachable at {cfg.base_url}: {e}") from e
            attempt += 1
            await asyncio.sleep(1.5 * attempt)


async def list_models(cfg: LLMConfig) -> list[str]:
    headers = {"Authorization": f"Bearer {cfg.api_key}"} if cfg.api_key else {}
    async with httpx.AsyncClient(timeout=10, trust_env=False) as c:
        r = await c.get(f"{cfg.base_url}/models", headers=headers)
        r.raise_for_status()
        return [m["id"] for m in r.json().get("data", [])]


class PartialString:
    """Incrementally decode one JSON string field (e.g. "code") out of streamed tool-call arguments,
    so code can be shown as the model writes it."""

    def __init__(self, field_name: str):
        self.key = f'"{field_name}"'
        self.buf = ""
        self.pos = -1  # index in buf where the string body starts
        self.done = False
        self.emitted = 0
        self.out = ""

    def feed(self, chunk: str) -> str:
        self.buf += chunk
        if self.done:
            return ""
        if self.pos < 0:
            k = self.buf.find(self.key)
            if k < 0:
                return ""
            j = k + len(self.key)
            while j < len(self.buf) and self.buf[j] in " \t\r\n:":
                j += 1
            if j >= len(self.buf) or self.buf[j] != '"':
                return ""
            self.pos = j + 1
            self.i = self.pos
        out = []
        b = self.buf
        while self.i < len(b):
            c = b[self.i]
            if c == '"':
                self.done = True
                break
            if c == "\\":
                if self.i + 1 >= len(b):
                    break
                e = b[self.i + 1]
                if e == "u":
                    if self.i + 6 > len(b):
                        break
                    out.append(chr(int(b[self.i + 2:self.i + 6], 16)))
                    self.i += 6
                    continue
                out.append({"n": "\n", "t": "\t", "r": "\r", "b": "\b", "f": "\f"}.get(e, e))
                self.i += 2
                continue
            out.append(c)
            self.i += 1
        s = "".join(out)
        self.out += s
        return s
