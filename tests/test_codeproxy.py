"""Slice 7: the Workspace tab's VS Code proxy, against a stub code-server."""
import socket
import threading
import time

import pytest
import uvicorn
from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import HTMLResponse
from fastapi.testclient import TestClient

from tomabotchi import codeproxy, store
from tomabotchi.config import AI_ROOT
from tomabotchi.models import Pet


@pytest.fixture(scope="module")
def code_server():
    seen = []
    app = FastAPI()

    @app.get("/")
    async def workbench(request: Request):
        seen.append({"path": "/", "host": request.headers.get("host"), "query": str(request.query_params)})
        return HTMLResponse('<html><script src="./static/out/main.js"></script>workbench</html>',
                            headers={"set-cookie": "key=abc; Path=/"})

    @app.post("/vscode-remote-resource")
    async def upload(request: Request):
        return {"got": (await request.body()).decode()}

    @app.websocket("/stable-abc")
    async def ws(socket_: WebSocket):
        seen.append({"path": "ws", "host": socket_.headers.get("host"), "origin": socket_.headers.get("origin"),
                     "query": socket_.url.query})
        await socket_.accept()
        while True:
            msg = await socket_.receive()
            if msg["type"] == "websocket.disconnect":
                return
            if msg.get("bytes") is not None:
                await socket_.send_bytes(b"echo:" + msg["bytes"])
            else:
                await socket_.send_text("echo:" + msg["text"])

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    while not server.started:
        time.sleep(0.02)
    yield {"addr": f"127.0.0.1:{port}", "seen": seen}
    server.should_exit = True
    t.join(5)


@pytest.fixture
def client(tmp_path, monkeypatch, code_server):
    monkeypatch.setenv("TOMABOTCHI_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("TOMABOTCHI_DOCKER", "0")
    monkeypatch.setenv("TOMABOTCHI_CONFIG", str(AI_ROOT / "platform" / "tomabotchi.toml"))
    monkeypatch.setattr(codeproxy, "upstream", lambda pet_id: code_server["addr"])
    monkeypatch.setattr(codeproxy, "_client", None)
    from tomabotchi.web import app
    with TestClient(app) as c:
        for pet_id, brain in (("h1", "hermes"), ("f1", "fake")):
            store.insert_pet(c.app.state.conn, Pet(id=pet_id, name="Biscuit", shell="grape", look_seed=1, home="python",
                                                   brain=brain, model="local-qwen", state="awake", born_at=store.now()))
        yield c


def test_codeproxy_serves_workbench_and_ws(client, code_server):
    code_server["seen"].clear()
    r = client.get("/pets/h1/code/?folder=/workspace")
    assert r.status_code == 200 and "workbench" in r.text and './static/out/main.js' in r.text
    assert r.cookies.get("key") == "abc"                                  # code-server's own cookie comes back
    assert code_server["seen"][0]["query"] == "folder=%2Fworkspace"
    with client.websocket_connect("/pets/h1/code/stable-abc?reconnectionToken=t1",
                                  headers={"origin": "http://testserver"}) as ws:
        ws.send_text("hello")
        assert ws.receive_text() == "echo:hello"
        ws.send_bytes(b"\x01\x02")
        assert ws.receive_bytes() == b"echo:\x01\x02"
        ws.close()
        time.sleep(0.3)        # TestClient cancels the app right after its own close; let the proxy hang up first
    ws_seen = next(s for s in code_server["seen"] if s["path"] == "ws")
    assert ws_seen["host"] == "testserver" and ws_seen["origin"] == "http://testserver"   # origin check passes
    assert ws_seen["query"] == "reconnectionToken=t1"


def test_codeproxy_posts_bodies_and_redirects_to_the_slash(client):
    assert client.post("/pets/h1/code/vscode-remote-resource", content=b"abc").json() == {"got": "abc"}
    r = client.get("/pets/h1/code", follow_redirects=False)
    assert r.status_code == 307 and r.headers["location"] == "/pets/h1/code/"


def test_codeproxy_refuses_practice_pets_and_strangers(client):
    assert client.get("/pets/f1/code/").status_code == 404
    assert client.get("/pets/nobody/code/").status_code == 404


def test_codeproxy_says_so_when_code_server_is_down(client, monkeypatch):
    monkeypatch.setattr(codeproxy, "upstream", lambda pet_id: "127.0.0.1:9")
    r = client.get("/pets/h1/code/")
    assert r.status_code == 502 and "isn't answering" in r.text


def test_workspace_page_embeds_vscode_and_practice_pets_have_none(client):
    page = client.get("/pets/h1/workspace").text
    assert '<iframe class="vscode" src="/pets/h1/code/?folder=/workspace"' in page
    assert 'href="/pets/h1/code/?folder=/workspace" target="_blank"' in page          # the new-tab fallback
    assert 'aria-current="page">Workspace' in page
    r = client.get("/pets/f1/workspace", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/pets/f1"
    assert client.get("/pets/h1/pause").status_code == 405


def test_pause_while_editing_needs_an_awake_pet(client):
    assert client.post("/pets/h1/pause").status_code == 409      # no container attached in this test
    assert client.post("/pets/h1/resume").status_code == 409
