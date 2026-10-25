"""Slice 12: surviving a server restart (rejoin the session, take back the open call, let the turn
finish), an agent that forgot its session, and clear trouble when the model or Docker is down."""
import asyncio
import hashlib

from test_call_flow import until
from test_web import client, wait_for  # noqa: F401 - client is a fixture

from tomabotchi import store, views
from tomabotchi.fake_agent import FakeAgent
from tomabotchi.models import Pet
from tomabotchi.supervisor import PetSupervisor


def pet_row(conn, **extra) -> Pet:
    pet = Pet(id="p1", name="Biscuit", shell="grape", look_seed=15, home="python", brain="fake",
              model="local-qwen", state="hatching", born_at=store.now(), **extra)
    store.insert_pet(conn, pet)
    return pet


def method_count(agent, method):
    return sum(m.get("method") == method for m in agent.received)


async def test_restart_mid_call_rejoins_the_session_and_the_call_is_still_answerable(cfg, conn, bus):
    agent = FakeAgent(step_delay=0, token="tok")
    url = await agent.start()
    pet = pet_row(conn)
    old = PetSupervisor(cfg, conn, bus, pet, url, "tok")
    await old.start()
    task_id = await old.feed("Write a hello note")
    await until(lambda: store.pending_calls(conn, pet.id))
    [call] = store.pending_calls(conn, pet.id)

    # The old server process dies: its socket drops, and nothing it would have written afterwards lands.
    old._turn.cancel()
    await old.acp.close()
    conn.execute("UPDATE tasks SET state = 'running', ended_at = NULL WHERE id = ?", (task_id,))

    new = PetSupervisor(cfg, conn, bus, store.get_pet(conn, pet.id), url, "tok")
    await new.start(reattach=True)
    assert method_count(agent, "initialize") == 1 and method_count(agent, "session/new") == 1    # no new session
    assert new.busy and call.id in new._waiting
    assert "is calling you" in views.render("_call.html", **views.pet_context(conn, store.get_pet(conn, pet.id)))

    await new.answer(call.id, "allow_once", None)
    await until(lambda: store.get_task(conn, task_id).state == "done")    # the old prompt's answer closed it
    assert not new.busy and store.get_pet(conn, pet.id).tasks_done == 1
    assert len(store.pending_calls(conn, pet.id)) == 0
    assert conn.execute("SELECT COUNT(*) FROM calls WHERE pet_id = ?", (pet.id,)).fetchone()[0] == 1   # no duplicate
    await new.stop()
    await agent.stop()


async def test_a_call_replayed_by_petd_after_reconnect_is_not_doubled(cfg, conn, bus):
    agent = FakeAgent(step_delay=0, token="tok")
    url = await agent.start()
    pet = pet_row(conn)
    sup = PetSupervisor(cfg, conn, bus, pet, url, "tok")
    await sup.start()
    await sup.feed("Write a hello note")
    await until(lambda: store.pending_calls(conn, pet.id))
    ask = next(iter(agent._waiting))                                      # the agent's request id
    await agent.send({"jsonrpc": "2.0", "id": ask, "method": "session/request_permission",
                      "params": {"sessionId": sup.session_id, "toolCall": {"title": "Write notes/hello.md"},
                                 "options": []}})                          # the same request, delivered again
    await asyncio.sleep(0.2)
    assert conn.execute("SELECT COUNT(*) FROM calls WHERE pet_id = ?", (pet.id,)).fetchone()[0] == 1
    await sup.stop()
    await agent.stop()


async def test_an_agent_that_forgot_the_session_gets_a_new_one(cfg, conn, bus):
    agent = FakeAgent(step_delay=0, token="tok")
    url = await agent.start()
    pet = pet_row(conn, acp_session_id="fake-session-from-before")
    sup = PetSupervisor(cfg, conn, bus, pet, url, "tok")
    await sup.start(reattach=True)
    task_id = await sup.feed("Ponder")
    await until(lambda: store.pending_calls(conn, pet.id))
    assert sup.session_id in agent.sessions and store.get_pet(conn, pet.id).acp_session_id == sup.session_id
    await sup.answer(store.pending_calls(conn, pet.id)[0].id, "allow_once", None)
    await until(lambda: store.get_task(conn, task_id).state == "done")
    await sup.stop()
    await agent.stop()


def token_pet(client, brain="hermes"):
    conn = client.app.state.conn
    pet = Pet(id="h1", name="Biscuit", shell="grape", look_seed=1, home="python", brain=brain, model="local-qwen",
              state="awake", born_at=store.now())
    store.insert_pet(conn, pet, token_hash=hashlib.sha256(b"tok").hexdigest())
    return pet


def test_model_down_is_a_clear_502_and_a_notice_once_a_minute(tmp_path, monkeypatch):
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://127.0.0.1:9")
    from fastapi.testclient import TestClient
    from tomabotchi import gateway
    gateway._aliases_ready.clear()
    monkeypatch.setenv("TOMABOTCHI_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("TOMABOTCHI_DOCKER", "0")
    from tomabotchi.web import app
    with TestClient(app) as c:
        pet = token_pet(c)
        for _ in range(2):
            r = c.post("/llm/tok/v1/chat/completions", json={"model": "x", "messages": []})
            assert r.status_code == 502 and "isn't answering" in r.json()["error"]["message"]
        notes = [e for e in store.recent_events(c.app.state.conn, pet.id, {"notice"}, 10)
                 if e.payload["type"] == "model_down"]
        assert len(notes) == 1 and "127.0.0.1:9" in notes[0].payload["text"]
        assert "Something's wrong" in c.get(f"/pets/{pet.id}").text


def test_trouble_clears_once_the_model_answers(client):
    from tomabotchi.models import Event, Usage
    conn = client.app.state.conn
    pet = token_pet(client)
    store.append_event(conn, Event(id=0, pet_id=pet.id, task_id=None, ts=store.now(), kind="notice",
                                   payload={"type": "model_down", "text": "Its model isn't answering"}))
    assert views.pet_context(conn, pet)["trouble"] == "Its model isn't answering"
    store.add_usage(conn, Usage(pet_id=pet.id, task_id=None, ts=store.now(), model="m", prompt_tokens=1,
                                completion_tokens=1))
    assert views.pet_context(conn, pet)["trouble"] is None


def test_docker_down_on_boot_says_so(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from tomabotchi import db
    monkeypatch.setenv("TOMABOTCHI_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("TOMABOTCHI_DOCKER", "0")
    conn = db.connect(tmp_path / "tomabotchi.db")
    pet_row(conn)
    conn.execute("UPDATE pets SET brain = 'hermes', state = 'awake'")
    conn.close()
    from tomabotchi.web import app
    with TestClient(app) as c:
        pet = store.get_pet(c.app.state.conn, "p1")
        assert pet.state == "asleep"
        assert "can&#39;t reach Docker" in c.get("/").text and "Something's wrong" in c.get("/pets/p1").text
