"""The model gateway: the only way a pet reaches a model.

Pets authenticate with their token. The gateway forwards to Ollama, meters tokens per pet and task
(the Energy hearts), enforces the budget, and keeps real API keys out of pet containers.
Slice 2 carries the OpenAI-compatible route; the Anthropic route arrives with the Claude brain.
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from . import brains, store
from .models import Pet, Usage

log = logging.getLogger(__name__)
router = APIRouter(prefix="/llm")
_aliases_ready: set[str] = set()


@dataclass(frozen=True)
class Metered:
    prompt_tokens: int
    completion_tokens: int
    reasoning: str | None


class BudgetExceeded(Exception):
    pass


def check_budget(pet: Pet, tokens_used: int, elapsed: timedelta) -> None:
    if pet.budget_tokens is not None and tokens_used >= pet.budget_tokens:
        raise BudgetExceeded(f"{pet.name} has used its {pet.budget_tokens:,}-token budget")
    if pet.budget_seconds is not None and elapsed.total_seconds() >= pet.budget_seconds:
        raise BudgetExceeded(f"{pet.name} has used its {pet.budget_seconds // 60}-minute budget")


def meter_openai_stream(chunks: Iterable[bytes]) -> Metered:
    """Read an OpenAI-style SSE stream: usage from the final chunk, reasoning from deltas."""
    prompt = completion = 0
    reasoning: list[str] = []
    buf = b""
    for chunk in chunks:
        buf += chunk
        while b"\n" in buf:
            line, buf = buf.split(b"\n", 1)
            line = line.strip()
            if not line.startswith(b"data:"):
                continue
            data = line[5:].strip()
            if data == b"[DONE]":
                continue
            try:
                obj = json.loads(data)
            except ValueError:
                continue
            usage = obj.get("usage") or {}
            if usage:
                prompt = usage.get("prompt_tokens", prompt)
                completion = usage.get("completion_tokens", completion)
            for choice in obj.get("choices") or []:
                delta = choice.get("delta") or {}
                text = delta.get("reasoning_content") or delta.get("reasoning")
                if text:
                    reasoning.append(text)
    return Metered(prompt, completion, "".join(reasoning) or None)


def _pet_for_token(request: Request, token: str) -> Pet:
    conn = request.app.state.conn
    pet = store.pet_by_token_hash(conn, hashlib.sha256(token.encode()).hexdigest())
    if pet is None:
        raise HTTPException(401, "unknown pet token")
    return pet


async def ensure_ollama_alias(ollama_url: str, model: str) -> str:
    """Ollama's OpenAI endpoint ignores a model's own context length and uses a small default,
    silently truncating long prompts. Pets talk to a 64k-context alias that shares the weights."""
    alias = brains.ollama_alias(model)
    if alias in _aliases_ready:
        return alias
    async with httpx.AsyncClient(timeout=120) as c:
        r = await c.post(f"{ollama_url}/api/create", json={"model": alias, "from": model, "stream": False,
                                                           "parameters": {"num_ctx": brains.OLLAMA_CONTEXT}})
        r.raise_for_status()
    _aliases_ready.add(alias)
    return alias


def _record(request: Request, pet: Pet, task_id: int | None, model: str, m: Metered) -> None:
    conn = request.app.state.conn
    store.add_usage(conn, Usage(pet_id=pet.id, task_id=task_id, ts=store.now(), model=model,
                                prompt_tokens=m.prompt_tokens, completion_tokens=m.completion_tokens))
    on_usage = getattr(request.app.state, "on_usage", None)
    if on_usage:
        on_usage(pet.id, m)


@router.get("/{token}/v1/models")
async def models(request: Request, token: str):
    pet = _pet_for_token(request, token)
    choice = request.app.state.cfg.model(pet.model)
    return {"object": "list", "data": [{"id": choice.model, "object": "model", "owned_by": "tomabotchi",
                                        "context_length": brains.OLLAMA_CONTEXT}]}


@router.post("/{token}/v1/chat/completions")
async def openai_chat(request: Request, token: str):
    pet = _pet_for_token(request, token)
    cfg, conn = request.app.state.cfg, request.app.state.conn
    choice = cfg.model(pet.model)
    if choice.provider != "ollama":
        raise HTTPException(400, f"{choice.label} is served on the Anthropic route")
    task = store.current_task(conn, pet.id)
    task_id = task.id if task and task.state in ("running", "waiting") else None
    used = store.task_tokens(conn, task_id) if task_id else 0
    elapsed = datetime.now(timezone.utc) - task.started_at if task_id else timedelta(0)
    try:
        check_budget(pet, used, elapsed)
    except BudgetExceeded as e:
        on_budget = getattr(request.app.state, "on_budget", None)
        if on_budget:
            on_budget(pet.id, str(e))
        return JSONResponse({"error": {"message": f"{e}. Ask your keeper for more.", "type": "budget_exceeded"}},
                            status_code=429)
    body = await request.json()
    body["model"] = await ensure_ollama_alias(cfg.ollama_url, choice.model)
    url = f"{cfg.ollama_url}/v1/chat/completions"
    client = httpx.AsyncClient(timeout=httpx.Timeout(None, connect=10))

    if not body.get("stream"):
        r = await client.post(url, json=body)
        await client.aclose()
        if r.status_code == 200:
            data = r.json()
            u = data.get("usage") or {}
            msg = ((data.get("choices") or [{}])[0].get("message") or {})
            _record(request, pet, task_id, choice.model, Metered(u.get("prompt_tokens", 0),
                    u.get("completion_tokens", 0), msg.get("reasoning_content") or msg.get("reasoning")))
        return Response(r.content, status_code=r.status_code, media_type="application/json")

    body.setdefault("stream_options", {})["include_usage"] = True
    req = client.build_request("POST", url, json=body)
    upstream = await client.send(req, stream=True)

    async def relay():
        seen: list[bytes] = []
        try:
            async for chunk in upstream.aiter_raw():
                seen.append(chunk)
                yield chunk
        finally:
            await upstream.aclose()
            await client.aclose()
            if upstream.status_code == 200:
                _record(request, pet, task_id, choice.model, meter_openai_stream(seen))

    return StreamingResponse(relay(), status_code=upstream.status_code,
                             media_type=upstream.headers.get("content-type", "text/event-stream"))
