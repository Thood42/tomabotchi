"""History: every task and chat a pet has had, its transcript, and keeping a copy."""
from test_reskin_pages import hatch
from test_web import client, wait_for  # noqa: F401 - client is a fixture

from tomabotchi import store


def finished_pet(client):
    pet_id = hatch(client, "Pip")
    conn = client.app.state.conn
    wait_for(lambda: store.pending_calls(conn, pet_id))
    call = store.pending_calls(conn, pet_id)[0]
    client.post(f"/calls/{call.id}", data={"option": "reject_once", "reason": "check notes first"})
    task = store.current_task(conn, pet_id)
    wait_for(lambda: store.get_task(conn, task.id).state != "running" and store.get_task(conn, task.id).state != "waiting")
    return pet_id, task


def test_history_lists_tasks_and_shows_the_transcript_with_calls(client):
    pet_id, task = finished_pet(client)
    client.post(f"/pets/{pet_id}/tasks", data={"prompt_md": "how are you?", "mode": "chat"})
    wait_for(lambda: len(store.tasks(client.app.state.conn, pet_id)) == 2)
    page = client.get(f"/pets/{pet_id}/history").text
    assert "how are you?" in page and "Write a hello note" in page and 'aria-current="page">History' in page
    page = client.get(f"/pets/{pet_id}/history?task={task.id}").text
    assert "Asked to write notes/hello.md" in page and "denied" in page and "check notes first" in page
    assert "List notes/" in page


def test_transcript_downloads_as_markdown_and_saves_to_the_workspace(client):
    pet_id, task = finished_pet(client)
    r = client.get(f"/pets/{pet_id}/history/{task.id}.md")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/markdown")
    assert r.text.startswith("# Pip: Write a hello note") and "denied" in r.text and "attachment" in r.headers["content-disposition"]
    r = client.post(f"/pets/{pet_id}/history/{task.id}/save")
    assert r.status_code == 204 and "tomabotchi-logs/" in r.headers["hx-trigger"]
    saved = list((client.app.state.cfg.pet_dir(pet_id) / "workspace" / "tomabotchi-logs").glob("*.md"))
    assert len(saved) == 1 and "Write a hello note" in saved[0].read_text()


def test_history_of_an_unknown_task_is_a_404(client):
    pet_id, _ = finished_pet(client)
    assert client.get(f"/pets/{pet_id}/history/99999.md").status_code == 404
    assert client.get("/pets/nope/history").status_code == 404


def test_a_retired_pet_keeps_its_history(client):
    pet_id, task = finished_pet(client)
    client.post(f"/pets/{pet_id}/retire")
    page = client.get(f"/pets/{pet_id}/history").text
    assert "Write a hello note" in page and "Save to workspace" not in page
    assert client.post(f"/pets/{pet_id}/history/{task.id}/save").status_code == 409
