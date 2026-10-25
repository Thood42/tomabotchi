"""Slice 5: the gateway's Anthropic route, against a stub upstream standing in for the Claude API and Ollama."""
import hashlib
import json
import socket
import threading
import time

import pytest
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient

from tomabotchi import gateway, store
from tomabotchi.config import ROOT
from tomabotchi.models import Pet

SSE = [
    {"type": "message_start", "message": {"usage": {"input_tokens": 1200, "cache_read_input_tokens": 300,
                                                    "output_tokens": 1}}},
    {"type": "content_block_delta", "delta": {"type": "thinking_delta", "thinking": "Which file first?"}},
    {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Hi"}},
    {"type": "message_delta", "usage": {"output_tokens": 42}},
    {"type": "message_stop"},
]


def sse_bytes() -> bytes:
    return b"".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n".encode() for e in SSE)


@pytest.fixture(scope="module")
def upstream():
    seen: list[dict] = []
    app = FastAPI()

    @app.post("/v1/messages")
    async def messages(request: Request):
        body = await request.json()
        seen.append({"headers": dict(request.headers), "body": body})
        if body.get("stream"):
            return StreamingResponse(iter([sse_bytes()]), media_type="text/event-stream")
        return {"content": [{"type": "text", "text": "Hi"}], "usage": {"input_tokens": 10, "output_tokens": 5}}

    @app.post("/api/create")
    async def create(request: Request):
        return {"status": "success"}

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    while not server.started:
        time.sleep(0.02)
    yield {"url": f"http://127.0.0.1:{port}", "seen": seen}
    server.should_exit = True
    t.join(5)


def make_client(tmp_path, monkeypatch, upstream, key="real-key"):
    monkeypatch.setenv("TOMABOTCHI_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("TOMABOTCHI_DOCKER", "0")
    monkeypatch.setenv("TOMABOTCHI_CONFIG", str(ROOT / "tomabotchi.toml"))
    monkeypatch.setenv("ANTHROPIC_UPSTREAM_URL", upstream["url"])
    monkeypatch.setenv("OLLAMA_BASE_URL", upstream["url"])
    if key:
        monkeypatch.setenv("ANTHROPIC_API_KEY", key)
    else:
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    gateway._aliases_ready.clear()
    from tomabotchi.web import app
    return TestClient(app)


def add_pet(client, model: str, token: str = "pet-token") -> Pet:
    pet = Pet(id=model[:4], name="Pixel", shell="lagoon", look_seed=3, home="python", brain="claude", model=model,
              state="awake", born_at=store.now())
    store.insert_pet(client.app.state.conn, pet, token_hash=hashlib.sha256(token.encode()).hexdigest())
    return pet


def usage_rows(client, pet_id):
    return client.app.state.conn.execute("SELECT model, prompt_tokens, completion_tokens FROM usage WHERE pet_id = ?",
                                         (pet_id,)).fetchall()


def test_claude_api_gets_the_real_key_and_the_stream_is_metered(tmp_path, monkeypatch, upstream):
    with make_client(tmp_path, monkeypatch, upstream) as client:
        pet = add_pet(client, "claude-sonnet")
        upstream["seen"].clear()
        r = client.post("/llm/pet-token/anthropic/v1/messages",
                        headers={"x-api-key": "pet-token", "anthropic-beta": "fine-grained-tool-streaming"},
                        json={"model": "opus", "stream": True, "max_tokens": 50,
                              "messages": [{"role": "user", "content": "hi"}]})
        assert r.status_code == 200 and b"message_stop" in r.content
        [req] = upstream["seen"]
        assert req["headers"]["x-api-key"] == "real-key"
        assert req["headers"]["anthropic-beta"] == "fine-grained-tool-streaming"
        assert req["body"]["model"] == "claude-sonnet-5-5"           # pinned to the keeper's choice, not "opus"
        assert [tuple(u) for u in usage_rows(client, pet.id)] == [("claude-sonnet-5-5", 1500, 42)]


def test_local_model_goes_to_ollama_messages_with_the_wide_alias(tmp_path, monkeypatch, upstream):
    with make_client(tmp_path, monkeypatch, upstream) as client:
        pet = add_pet(client, "local-qwen")
        upstream["seen"].clear()
        r = client.post("/llm/pet-token/anthropic/v1/messages",
                        json={"model": "claude-haiku-4-5", "max_tokens": 50, "messages": []})
        assert r.status_code == 200
        [req] = upstream["seen"]
        assert req["body"]["model"] == "tomabotchi-qwen3.6-35b-ctx64k"     # every role is the pet's own model
        assert "real-key" not in json.dumps(req["headers"])
        assert [tuple(u) for u in usage_rows(client, pet.id)] == [("tomabotchi-qwen3.6-35b-ctx64k", 10, 5)]


def test_claude_model_without_a_key_is_refused_clearly(tmp_path, monkeypatch, upstream):
    with make_client(tmp_path, monkeypatch, upstream, key=None) as client:
        add_pet(client, "claude-sonnet")
        r = client.post("/llm/pet-token/anthropic/v1/messages", json={"model": "x", "messages": []})
        assert r.status_code == 503 and "ANTHROPIC_API_KEY" in r.json()["error"]["message"]


def test_count_tokens_is_estimated_for_local_models(tmp_path, monkeypatch, upstream):
    with make_client(tmp_path, monkeypatch, upstream) as client:
        add_pet(client, "local-qwen")
        r = client.post("/llm/pet-token/anthropic/v1/messages/count_tokens", json={"messages": [{"content": "x" * 400}]})
        assert r.status_code == 200 and r.json()["input_tokens"] > 90


def test_meter_anthropic_stream_reads_usage_and_thinking():
    m = gateway.meter_anthropic_stream([sse_bytes()[:50], sse_bytes()[50:]])
    assert (m.prompt_tokens, m.completion_tokens, m.reasoning) == (1500, 42, "Which file first?")
