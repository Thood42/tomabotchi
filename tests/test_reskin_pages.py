"""The desk and its endpoints, on practice pets (the same fixtures as test_web)."""
from test_web import HATCH, client, wait_for  # noqa: F401 - client is a fixture

from tomabotchi import store


def hatch(client, name, **extra):
    r = client.post("/pets", data={**HATCH, "name": name, **extra}, follow_redirects=False)
    return r.headers["location"].rsplit("/", 1)[1]


def test_desk_lists_every_pet_with_a_module_and_a_track(client):
    ids = [hatch(client, "Pip"), hatch(client, "Fig")]
    page = client.get("/").text
    for pet_id in ids:
        assert f'id="module-{pet_id}"' in page and f'id="track-{pet_id}"' in page


def test_desk_focuses_the_oldest_call_by_default(client):
    first, second = hatch(client, "Pip"), hatch(client, "Fig")
    conn = client.app.state.conn
    wait_for(lambda: len(store.pending_calls(conn)) == 2)
    assert f'<section id="deck" class="deck" aria-label="Pip" data-pet="{first}"' in client.get("/").text
    assert 'aria-label="Fig"' in client.get(f"/?focus={second}").text


def test_desk_focus_falls_back_without_a_404(client):
    first = hatch(client, "Pip")
    r = client.get("/?focus=nope")
    assert r.status_code == 200 and f'data-pet="{first}"' in r.text


def test_deck_fragment_for_pet_and_errors(client):
    pet_id = hatch(client, "Pip")
    r = client.get(f"/pets/{pet_id}/deck")
    assert r.status_code == 200 and 'id="deck"' in r.text and "Pip" in r.text
    assert client.get("/pets/nope/deck").status_code == 404
    client.post(f"/pets/{pet_id}/retire")
    assert client.get(f"/pets/{pet_id}/deck").status_code == 409


def test_track_blocks_carry_epoch_times(client):
    import re
    pet_id = hatch(client, "Pip")
    wait_for(lambda: store.pending_calls(client.app.state.conn, pet_id))
    lane = client.get("/").text
    lane = lane[lane.index(f'id="track-{pet_id}"'):]
    spans = re.findall(r'data-t0="(\d+)"(?: data-t1="(\d+)")?', lane)
    assert spans and all(int(t0) > 1_600_000_000_000 and (not t1 or int(t0) <= int(t1)) for t0, t1 in spans)


def test_status_key_counts_calls_and_links_the_oldest(client):
    first, _ = hatch(client, "Pip"), hatch(client, "Fig")
    wait_for(lambda: len(store.pending_calls(client.app.state.conn)) == 2)
    page = client.get("/").text
    assert "2 calling" in page and f'href="/?focus={first}"' in page


def test_retire_hides_module_and_track(client):
    pet_id = hatch(client, "Pip")
    bus, seen = client.app.state.bus, []
    real = bus.publish
    bus.publish = lambda msg: (seen.append(msg), real(msg))[1]
    client.post(f"/pets/{pet_id}/retire")
    hidden = {m.event for m in seen if "hidden" in m.data}
    assert {f"module-{pet_id}", f"track-{pet_id}"} <= hidden and "status" in {m.event for m in seen}
