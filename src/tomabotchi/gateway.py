"""The model gateway: the only way a pet reaches a model.

Pets authenticate with their token. The gateway forwards to Ollama, meters tokens per pet and task
(the budget readout), enforces the budget, and keeps real API keys out of pet containers.
Two routes: OpenAI-compatible (Hermes on Ollama) and Anthropic Messages (Claude Code on either
model, and Hermes on Claude), which goes to the Claude API or to Ollama's own /v1/messages.
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


def _sse_events(chunks: Iterable[bytes]):
    buf = b""
    for chunk in chunks:
        buf += chunk
        while b"\n" in buf:
            line, buf = buf.split(b"\n", 1)
            line = line.strip()
            if line.startswith(b"data:"):
                try:
                    yield json.loads(line[5:].strip())
                except ValueError:
                    continue


def _anthropic_input(usage: dict) -> int:
    return (usage.get("input_tokens") or 0) + (usage.get("cache_creation_input_tokens") or 0) + \
        (usage.get("cache_read_input_tokens") or 0)


def meter_anthropic_stream(chunks: Iterable[bytes]) -> Metered:
    """Read an Anthropic Messages SSE stream: input tokens from message_start, output tokens from
    the last message_delta, and thinking text from thinking deltas."""
    prompt = completion = 0
    thinking: list[str] = []
    for ev in _sse_events(chunks):
        t = ev.get("type")
        if t == "message_start":
            usage = (ev.get("message") or {}).get("usage") or {}
            prompt, completion = _anthropic_input(usage), usage.get("output_tokens") or completion
        elif t == "message_delta":
            usage = ev.get("usage") or {}
            completion = usage.get("output_tokens", completion)
            if usage.get("input_tokens"):
                prompt = _anthropic_input(usage)
        elif t == "content_block_delta" and (ev.get("delta") or {}).get("type") == "thinking_delta":
            thinking.append(ev["delta"].get("thinking", ""))
    return Metered(prompt, completion, "".join(thinking) or None)


def meter_anthropic_json(data: dict) -> Metered:
    usage = data.get("usage") or {}
    thinking = "".join(b.get("thinking", "") for b in data.get("content") or [] if b.get("type") == "thinking")
    return Metered(_anthropic_input(usage), usage.get("output_tokens") or 0, thinking or None)


def meter_openai_json(data: dict) -> Metered:
    u = data.get("usage") or {}
    msg = ((data.get("choices") or [{}])[0].get("message") or {})
    return Metered(u.get("prompt_tokens", 0), u.get("completion_tokens", 0),
                   msg.get("reasoning_content") or msg.get("reasoning"))


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


def _gate(request: Request, pet: Pet) -> tuple[int | None, JSONResponse | None]:
    """The task a model call counts against, or a 429 when the pet is out of budget."""
    conn = request.app.state.conn
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
        return task_id, JSONResponse({"type": "error", "error": {"message": f"{e}. Ask your keeper for more.",
                                                                 "type": "budget_exceeded"}}, status_code=429)
    return task_id, None


def _unreachable(request: Request, pet: Pet, where: str, e: Exception) -> JSONResponse:
    """The model isn't answering: tell the brain in its API's error shape, and tell the keeper."""
    text = f"Its model isn't answering ({where}: {e.__class__.__name__}). Check that it's running."
    on_down = getattr(request.app.state, "on_model_down", None)
    if on_down:
        on_down(pet.id, text)
    return JSONResponse({"type": "error", "error": {"type": "api_error", "message": f"tomabotchi: {text}"}},
                        status_code=502)


async def _relay(request: Request, pet: Pet, task_id: int | None, model: str, url: str, body: dict,
                 headers: dict[str, str], meter_stream, meter_json) -> Response:
    """Forward one model call, stream the answer back, and meter it once it's done."""
    client = httpx.AsyncClient(timeout=httpx.Timeout(None, connect=10))
    try:
        if not body.get("stream"):
            r = await client.post(url, json=body, headers=headers)
            await client.aclose()
            if r.status_code == 200:
                _record(request, pet, task_id, model, meter_json(r.json()))
            return Response(r.content, status_code=r.status_code, media_type="application/json")
        upstream = await client.send(client.build_request("POST", url, json=body, headers=headers), stream=True)
    except httpx.HTTPError as e:
        await client.aclose()
        return _unreachable(request, pet, url.split("/v1/")[0], e)

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
                _record(request, pet, task_id, model, meter_stream(seen))

    return StreamingResponse(relay(), status_code=upstream.status_code,
                             media_type=upstream.headers.get("content-type", "text/event-stream"))


@router.post("/{token}/v1/chat/completions")
async def openai_chat(request: Request, token: str):
    pet = _pet_for_token(request, token)
    cfg = request.app.state.cfg
    choice = cfg.model(pet.model)
    if choice.provider != "ollama":
        raise HTTPException(400, f"{choice.label} is served on the Anthropic route")
    task_id, refused = _gate(request, pet)
    if refused:
        return refused
    body = await request.json()
    try:
        body["model"] = await ensure_ollama_alias(cfg.ollama_url, choice.model)
    except httpx.HTTPError as e:
        return _unreachable(request, pet, cfg.ollama_url, e)
    if body.get("stream"):
        body.setdefault("stream_options", {})["include_usage"] = True
    return await _relay(request, pet, task_id, choice.model, f"{cfg.ollama_url}/v1/chat/completions", body, {},
                        meter_openai_stream, meter_openai_json)


_PASS_HEADERS = ("anthropic-version", "anthropic-beta")


@router.post("/{token}/anthropic/v1/messages")
async def anthropic_messages(request: Request, token: str):
    """Claude's API shape. To the Claude API with the real key, or to Ollama's /v1/messages. Either
    way every model a brain names is the pet's own: Claude Code asks for "opus" by default and for
    small "haiku" calls, but the keeper chose the model (and its price) at hatch."""
    pet = _pet_for_token(request, token)
    cfg = request.app.state.cfg
    choice = cfg.model(pet.model)
    task_id, refused = _gate(request, pet)
    if refused:
        return refused
    body = await request.json()
    if choice.provider == "ollama":
        try:
            body["model"] = await ensure_ollama_alias(cfg.ollama_url, choice.model)
        except httpx.HTTPError as e:
            return _unreachable(request, pet, cfg.ollama_url, e)
        url, headers = f"{cfg.ollama_url}/v1/messages", {}
    else:
        if not cfg.anthropic_key:
            return JSONResponse({"type": "error", "error": {"type": "authentication_error", "message":
                                 "tomabotchi has no ANTHROPIC_API_KEY, so Claude models are off"}}, status_code=503)
        body["model"] = choice.model
        url = f"{cfg.anthropic_url}/v1/messages"
        headers = {"x-api-key": cfg.anthropic_key, "anthropic-version": "2023-06-01",
                   **{h: request.headers[h] for h in _PASS_HEADERS if h in request.headers}}
    return await _relay(request, pet, task_id, body.get("model") or choice.model, url, body, headers,
                        meter_anthropic_stream, meter_anthropic_json)


@router.post("/{token}/anthropic/v1/messages/count_tokens")
async def anthropic_count_tokens(request: Request, token: str):
    """Ollama has no token counter, so local pets get an estimate (about 4 characters a token)."""
    pet = _pet_for_token(request, token)
    cfg = request.app.state.cfg
    choice = cfg.model(pet.model)
    body = await request.json()
    if choice.provider == "ollama" or not cfg.anthropic_key:
        return {"input_tokens": len(json.dumps(body)) // 4}
    headers = {"x-api-key": cfg.anthropic_key, "anthropic-version": "2023-06-01",
               **{h: request.headers[h] for h in _PASS_HEADERS if h in request.headers}}
    async with httpx.AsyncClient(timeout=60) as c:
        r = await c.post(f"{cfg.anthropic_url}/v1/messages/count_tokens", json=body, headers=headers)
    return Response(r.content, status_code=r.status_code, media_type="application/json")
