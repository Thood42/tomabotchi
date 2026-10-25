"""Slice 13: the small things: looks that stick, tasks closed on restart,
and a quiet VS Code in every home."""
import json

from fastapi.testclient import TestClient
from test_web import HATCH, client, wait_for  # noqa: F401 - client is a fixture

from tomabotchi import brains, db, store, views


def test_look_sticks_from_hatch_to_desk(client):
    pet_id = client.post("/pets", data={**HATCH, "look_seed": "4242", "shell": "tangerine"},
                         follow_redirects=False).headers["location"].rsplit("/", 1)[1]
    desk = client.get("/").text
    module = desk[desk.index(f'id="module-{pet_id}"'):]
    assert 'data-seed="4242"' in module[:600]
    assert 'data-seed="4242"' in client.get(f"/pets/{pet_id}").text


def test_a_restart_closes_a_practice_pets_dead_turn(client):
    from tomabotchi.web import app
    conn = client.app.state.conn
    pet_id = client.post("/pets", data=HATCH, follow_redirects=False).headers["location"].rsplit("/", 1)[1]
    wait_for(lambda: store.pending_calls(conn, pet_id))
    task, path = store.current_task(conn, pet_id), client.app.state.cfg.db_path
    client.__exit__(None, None, None)
    raw = db.connect(path)
    raw.execute("UPDATE tasks SET state = 'running', ended_at = NULL WHERE id = ?", (task.id,))  # died mid-turn
    raw.close()
    with TestClient(app) as again:
        wait_for(lambda: again.app.state.fleet.is_awake(pet_id))
        assert store.get_task(again.app.state.conn, task.id).state == "cancelled"
        assert "is working" not in again.get("/").text


def test_vscode_settings_are_written_once_and_then_the_keepers(tmp_path):
    path = brains.write_code_server_settings(tmp_path)
    s = json.loads(path.read_text())
    assert s["chat.disableAIFeatures"] is True and s["workbench.startupEditor"] == "none"
    path.write_text('{"editor.fontSize": 18}')
    assert brains.write_code_server_settings(tmp_path) is None
    assert json.loads(path.read_text()) == {"editor.fontSize": 18}


def test_replies_render_markdown_safely_and_counts_read_right(client):
    from tomabotchi.models import Event
    conn = client.app.state.conn
    pet_id = client.post("/pets", data={**HATCH, "first_task_md": ""}, follow_redirects=False).headers["location"].rsplit("/", 1)[1]
    store.append_event(conn, Event(id=0, pet_id=pet_id, task_id=None, ts=store.now(), kind="message",
                                   payload={"text": "Node is **v22** <script>x()</script>"}))
    store.update_pet(conn, pet_id, tasks_done=1)
    page = client.get(f"/pets/{pet_id}").text
    assert "<strong>v22</strong>" in page and "<script>x()" not in page
    assert "1 task done." in page
