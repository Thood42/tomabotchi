"""The web app with its real lifespan (config, SQLite, fleet) against a temp data dir."""
import time

import pytest
from fastapi.testclient import TestClient

from tomabotchi import store
from tomabotchi.config import AI_ROOT


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("TOMABOTCHI_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("TOMABOTCHI_DOCKER", "0")
    monkeypatch.setenv("TOMABOTCHI_CONFIG", str(AI_ROOT / "platform" / "tomabotchi.toml"))
    from tomabotchi.web import app
    with TestClient(app) as c:
        yield c


def wait_for(pred, timeout=10.0):
    deadline = time.monotonic() + timeout
    while not pred():
        if time.monotonic() > deadline:
            raise AssertionError("condition never became true")
        time.sleep(0.05)


HATCH = {"name": "Biscuit", "shell": "grape", "look_seed": "15", "brain": "fake", "home": "python",
         "model": "local-qwen", "budget": "100k-2h", "first_task_md": "Write a hello note"}


def test_empty_shelf_invites_hatching(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "Nobody lives here yet" in r.text
    assert 'href="/hatch"' in r.text


def test_hatch_page_renders_brains_and_homes(client):
    r = client.get("/hatch")
    assert r.status_code == 200
    assert 'value="hermes" selected' in r.text
    assert "Python (default)" in r.text


def test_hatch_redirects_to_pet_and_call_shows_on_shelf(client):
    r = client.post("/pets", data=HATCH, follow_redirects=False)
    assert r.status_code == 303
    pet_id = r.headers["location"].rsplit("/", 1)[1]
    conn = client.app.state.conn
    wait_for(lambda: store.pending_calls(conn, pet_id))
    shelf = client.get("/").text
    assert "Biscuit is calling you" in shelf
    assert "Wants your OK to write notes/hello.md" in shelf
    page = client.get(f"/pets/{pet_id}").text
    assert 'value="reject_once"' in page


def test_answering_a_call_records_the_wait_and_clears_the_card(client):
    pet_id = client.post("/pets", data=HATCH, follow_redirects=False).headers["location"].rsplit("/", 1)[1]
    conn = client.app.state.conn
    wait_for(lambda: store.pending_calls(conn, pet_id))
    call = store.pending_calls(conn, pet_id)[0]
    r = client.post(f"/calls/{call.id}", data={"option": "allow_once"})
    assert r.status_code == 200
    assert "hidden" in r.text                       # the card swaps out
    assert client.post(f"/calls/{call.id}", data={"option": "allow_once"}).status_code == 409
    assert "No calls yet" not in client.get("/").text


def test_container_brain_without_docker_is_refused_clearly(client):
    r = client.post("/pets", data={**HATCH, "brain": "hermes"}, follow_redirects=False)
    assert r.status_code == 400
    assert "only practice pets can hatch" in r.text


def test_unknown_brain_is_refused(client):
    assert client.post("/pets", data={**HATCH, "brain": "skynet"}, follow_redirects=False).status_code == 400


def test_pet_subnet_is_kept_out_of_the_keepers_api(client):
    from tomabotchi.web import app
    with TestClient(app, client=("172.30.0.5", 5555)) as pet:
        assert pet.post("/calls/1", data={"option": "allow_once"}).status_code == 403
        assert pet.get("/").status_code == 403
        assert pet.get("/llm/not-a-token/v1/models").status_code == 401     # reaches the gateway, which checks the token


def test_bad_call_option_is_rejected(client):
    assert client.post("/calls/1", data={"option": "maybe"}).status_code == 400
