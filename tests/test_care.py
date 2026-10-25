"""Slice 8: care actions (Lights, Chat, Medicine, Tidy) and the out-of-budget sleep, on practice pets."""
from test_web import HATCH, client, wait_for  # noqa: F401 - client is a fixture

from tomabotchi import store, views
from tomabotchi.models import Usage
from tomabotchi.supervisor import CHAT_PREFIX


def hatch(client, first="", **extra):
    data = {**HATCH, "first_task_md": first, **extra}
    pet_id = client.post("/pets", data=data, follow_redirects=False).headers["location"].rsplit("/", 1)[1]
    wait_for(lambda: client.app.state.fleet.is_awake(pet_id))
    return pet_id


def prompts(client, pet_id):
    agent = client.app.state.fleet._fakes[pet_id]
    return [" ".join(b.get("text", "") for b in m["params"]["prompt"]) for m in agent.received
            if m.get("method") == "session/prompt"]


def sent(client, pet_id, method):
    return [m for m in client.app.state.fleet._fakes[pet_id].received if m.get("method") == method]


def test_lights_off_stops_the_task_and_lights_on_picks_it_up(client):
    conn = client.app.state.conn
    pet_id = hatch(client, "Think about tea #slow")
    wait_for(lambda: client.app.state.fleet.get(pet_id).busy)
    first = store.current_task(conn, pet_id)
    assert client.post(f"/pets/{pet_id}/lights", data={"on": 0}).status_code == 204
    assert sent(client, pet_id, "session/cancel")
    assert store.get_task(conn, first.id).state == "cancelled"
    assert store.get_pet(conn, pet_id).state == "asleep"
    page = client.get(f"/pets/{pet_id}").text
    assert 'aria-pressed="true"' in page and ">Asleep<" in page and "Lights off. Sleeping" in client.get("/").text
    assert client.post(f"/pets/{pet_id}/tasks", data={"prompt_md": "hi"}).status_code == 409

    assert client.post(f"/pets/{pet_id}/lights", data={"on": 1}).status_code == 204
    assert store.get_pet(conn, pet_id).state == "awake"
    again = store.current_task(conn, pet_id)
    assert again.id != first.id and again.prompt == "Think about tea #slow" and again.kind == "task"
    wait_for(lambda: any(p.startswith("Continue where you left off. Your task was: Think about tea")
                         for p in prompts(client, pet_id)))       # the same agent, reached over a new connection


def test_lights_off_while_a_call_waits_cancels_the_call(client):
    conn = client.app.state.conn
    pet_id = hatch(client, "Write a hello note")
    wait_for(lambda: store.pending_calls(conn, pet_id))
    call = store.pending_calls(conn, pet_id)[0]
    client.post(f"/pets/{pet_id}/lights", data={"on": 0})
    assert store.get_call(conn, call.id).outcome == "cancelled" and not store.pending_calls(conn, pet_id)
    client.post(f"/pets/{pet_id}/lights", data={"on": 1})
    wait_for(lambda: store.pending_calls(conn, pet_id))           # the resumed task asks again


def test_chat_talks_without_counting_as_a_task(client):
    conn = client.app.state.conn
    pet_id = hatch(client)
    assert client.post(f"/pets/{pet_id}/tasks", data={"prompt_md": "How are you?", "mode": "chat"}).status_code == 200
    wait_for(lambda: store.current_task(conn, pet_id).state == "done")
    task = store.current_task(conn, pet_id)
    assert (task.kind, task.prompt) == ("chat", "How are you?")
    assert prompts(client, pet_id)[-1] == CHAT_PREFIX + "How are you?"
    assert store.get_pet(conn, pet_id).tasks_done == 0
    assert "only a practice pet, but I'm listening." in client.get(f"/pets/{pet_id}").text


def test_medicine_stops_a_stuck_pet_and_nudges_it(client):
    conn = client.app.state.conn
    pet_id = hatch(client, "Fetch papers #slow")
    wait_for(lambda: client.app.state.fleet.get(pet_id).busy)
    assert client.post(f"/pets/{pet_id}/medicine").status_code == 204
    assert sent(client, pet_id, "session/cancel")
    nudge = prompts(client, pet_id)[-1]
    assert nudge.startswith("You seemed stuck.") and "different approach" in nudge
    assert nudge.endswith("Your task was: Fetch papers #slow")
    assert store.current_task(conn, pet_id).kind == "care"


def test_medicine_names_what_looked_wrong(client):
    conn = client.app.state.conn
    pet_id = hatch(client, "Find papers #sick")
    pet = store.get_pet(conn, pet_id)
    wait_for(lambda: views.pet_context(conn, store.get_pet(conn, pet_id))["vitals"].sick_reason)
    reason = views.pet_context(conn, pet)["vitals"].sick_reason
    client.post(f"/pets/{pet_id}/medicine")
    assert prompts(client, pet_id)[-1].startswith(f"You seemed stuck ({reason}).")
    wait_for(lambda: store.current_task(conn, pet_id).state == "done")
    assert views.pet_context(conn, store.get_pet(conn, pet_id))["vitals"].sick_reason is None     # cured


def test_tidy_sends_the_brains_compact_command_and_waits_for_idle(client):
    conn = client.app.state.conn
    pet_id = hatch(client, "Ponder #slow")
    wait_for(lambda: client.app.state.fleet.get(pet_id).busy)
    assert client.post(f"/pets/{pet_id}/tidy").status_code == 409          # not mid-turn
    client.post(f"/pets/{pet_id}/medicine")
    client.app.state.fleet.get(pet_id)._turn.cancel()
    wait_for(lambda: not client.app.state.fleet.get(pet_id).busy)
    assert client.post(f"/pets/{pet_id}/tidy").status_code == 204
    assert prompts(client, pet_id)[-1] == "/compress"
    wait_for(lambda: store.current_task(conn, pet_id).state == "done")
    assert store.get_pet(conn, pet_id).tasks_done == 0


def test_out_of_budget_pet_falls_asleep_and_a_new_task_wakes_it(client):
    conn = client.app.state.conn
    pet_id = hatch(client, "Count the stars #slow", budget="25k-30m")
    wait_for(lambda: client.app.state.fleet.get(pet_id).busy)
    task = store.current_task(conn, pet_id)
    store.add_usage(conn, Usage(pet_id=pet_id, task_id=task.id, ts=store.now(), model="m", prompt_tokens=24_000,
                                completion_tokens=2_000))
    token = (client.app.state.cfg.pet_dir(pet_id) / ".token").read_text()
    r = client.post(f"/llm/{token}/v1/chat/completions", json={"model": "x", "messages": []})
    assert r.status_code == 429 and "25,000-token budget" in r.json()["error"]["message"]
    wait_for(lambda: store.get_task(conn, task.id).state == "cancelled")
    assert store.get_pet(conn, pet_id).state == "asleep"
    assert "Out of budget: Biscuit has used its 25,000-token budget" in client.get("/").text
    assert client.post(f"/pets/{pet_id}/tasks", data={"prompt_md": "A smaller job"}).status_code == 200
    assert store.get_pet(conn, pet_id).state == "awake"


def test_out_of_budget_sleep_survives_a_server_restart(client):
    from fastapi.testclient import TestClient
    from tomabotchi.web import app
    pet_id = hatch(client)
    store.set_pet_state(client.app.state.conn, pet_id, "asleep")
    client.__exit__(None, None, None)                         # stop the server...
    with TestClient(app) as again:                            # ...and start it on the same data
        wait_for(lambda: again.app.state.fleet.is_awake(pet_id))
        assert store.get_pet(again.app.state.conn, pet_id).state == "asleep"
        assert again.post(f"/pets/{pet_id}/tasks", data={"prompt_md": "Wake up"}).status_code == 200
        assert store.get_pet(again.app.state.conn, pet_id).state == "awake"
