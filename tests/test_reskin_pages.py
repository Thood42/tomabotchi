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
    wait_for(lambda: store.pending_calls(client.app.state.conn, pet_id))      # so the calls key has something to drop
    bus, seen = client.app.state.bus, []
    real = bus.publish
    bus.publish = lambda msg: (seen.append(msg), real(msg))[1]
    client.post(f"/pets/{pet_id}/retire")
    hidden = {m.event for m in seen if "hidden" in m.data}
    assert {f"module-{pet_id}", f"track-{pet_id}"} <= hidden and "status" in {m.event for m in seen}


def test_deck_has_keycaps_task_log_switches_and_readout(client):
    pet_id = hatch(client, "Pip")
    wait_for(lambda: store.pending_calls(client.app.state.conn, pet_id))
    deck = client.get(f"/pets/{pet_id}/deck").text
    for key in ('data-key="a"', 'data-key="A"', 'data-key="d"'):
        assert key in deck
    assert f'id="stream-{pet_id}"' in deck and "needs you" in deck
    assert f'id="readout-{pet_id}"' in deck and "Budget left" in deck and "Asked before acting" in deck
    assert 'data-set="never"' in deck and f'id="rules-{pet_id}"' in deck
    assert 'hx-post="/pets/' in deck and "New task" in deck


def test_feeding_from_the_desk_answers_without_a_redirect(client):
    pet_id = hatch(client, "Pip", first_task_md="")
    r = client.post(f"/pets/{pet_id}/tasks", data={"prompt_md": "write a note", "mode": "task"},
                    headers={"HX-Request": "true"}, follow_redirects=False)
    assert r.status_code == 204


def test_answering_a_call_swaps_the_card_out_and_the_log_moves_on(client):
    pet_id = hatch(client, "Pip")
    conn = client.app.state.conn
    wait_for(lambda: store.pending_calls(conn, pet_id))
    call = store.pending_calls(conn, pet_id)[0]
    assert client.post(f"/calls/{call.id}", data={"option": "allow_once"}).status_code == 200
    wait_for(lambda: 'class="st wait"' not in client.get(f"/pets/{pet_id}/deck").text)


def test_console_shows_stream_dock_and_rack(client):
    first, second = hatch(client, "Pip"), hatch(client, "Fig")
    wait_for(lambda: store.pending_calls(client.app.state.conn, first))
    page = client.get(f"/pets/{first}").text
    assert f'id="stream-{first}"' in page and 'class="dockwrap"' in page
    for key in ('data-key="a"', 'data-key="A"', 'data-key="d"'):
        assert key in page
    assert f'id="module-{first}"' in page and f'id="module-{second}"' in page and 'id="rack" hx-preserve' in page
    assert f'id="readout-{first}"' in page and f'id="controls-{first}"' in page


def test_soul_and_workspace_wear_the_console_shell(client):
    pet_id = hatch(client, "Pip")
    soul = client.get(f"/pets/{pet_id}/soul").text
    assert 'id="rack"' in soul and 'aria-current="page">Soul' in soul
    assert 'id="rack"' in client.get(f"/pets/{pet_id}/workspace", follow_redirects=True).text   # practice pets bounce to Live


def test_pages_render_without_the_old_toy_words(client):
    pet_id = hatch(client, "Pip")
    for url in ("/", f"/pets/{pet_id}", f"/pets/{pet_id}/soul", "/hatch", "/memory", "/settings"):
        text = client.get(url).text
        for word in ("Feed ", "Medicine", "Lights", "hearts", "Memory shelf", "Hungry", "Pick a shell"):
            assert word not in text, (url, word)


def ctx_for(name, *, call=None, sick=None, asleep=False, working=False, brain="fake"):
    from types import SimpleNamespace
    pet = SimpleNamespace(id=name.lower(), name=name, state="asleep" if asleep else "awake", brain=brain)
    return {"pet": pet, "call": call, "vitals": SimpleNamespace(sick_reason=sick), "lights_off": asleep,
            "working": working, "task": object() if working else None}


def test_jump_index_offers_wake_only_for_sleeping_pets_and_unstick_only_for_sick():
    from tomabotchi import views
    labels = lambda *pets: [e["label"] for e in views.jump_index(list(pets))]            # noqa: E731
    awake, asleep, sick = ctx_for("Pip", working=True), ctx_for("Fig", asleep=True), ctx_for("Moth", sick="loop")
    got = labels(awake, asleep, sick)
    assert "Sleep Pip" in got and "Wake Fig" in got and "Sleep Fig" not in got and "Wake Pip" not in got
    assert "Unstick Moth" in got and "Unstick Pip" not in got
    assert "Answer the next call" not in got
    from datetime import datetime
    calling = ctx_for("Quill", call=type("C", (), {"asked_at": datetime(2026, 1, 1)})())
    assert "Answer the next call" in labels(calling)
    pets = [e for e in views.jump_index([awake]) if e["group"] == "Pets"]
    assert pets == [{"group": "Pets", "label": "Pip", "hint": "working", "href": "/pets/pip"}]


def test_every_shell_page_carries_the_palette_and_theme_toggle(client):
    pet_id = hatch(client, "Pip")
    for url in ("/", f"/pets/{pet_id}", f"/pets/{pet_id}/soul"):
        page = client.get(url).text
        assert 'id="jump-data"' in page and 'id="palette"' in page and "data-theme-toggle" in page
        assert '"label": "Pip"' in page or '"label":"Pip"' in page
