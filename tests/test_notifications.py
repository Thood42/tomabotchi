"""Slice 10: notification settings (switches, quiet hours in the keeper's timezone), the test beep,
and notifications reaching every open page."""
import asyncio
import json
from datetime import datetime, time, timezone

from test_web import HATCH, client, wait_for  # noqa: F401 - client is a fixture

from tomabotchi import notify, store
from tomabotchi.bus import Bus, BusMessage
from tomabotchi.models import NotificationSettings

ALL_ON = {"enabled": "1", "calling": "1", "sick": "1", "finished": "1", "out_of_budget": "1",
          "quiet_enabled": "1", "quiet_from": "21:00", "quiet_to": "06:00", "tz": "America/New_York"}


def test_switches_save_and_render(client):
    r = client.put("/settings/notifications", data={**ALL_ON, "finished": ""})
    assert r.status_code == 200 and "Saved." in r.text
    s = store.get_notification_settings(client.app.state.conn)
    assert (s.enabled, s.calling, s.finished, s.quiet_enabled) == (True, True, False, True)
    assert (s.quiet_from, s.quiet_to, s.tz) == (time(21), time(6), "America/New_York")
    page = client.get("/settings").text
    assert 'name="finished" value="1" aria-label' in page                 # unchecked
    assert 'name="calling" value="1" checked' in page and "<option selected>21:00</option>" in page


def test_master_off_keeps_the_other_switches_and_greys_them(client):
    client.put("/settings/notifications", data={**ALL_ON, "sick": ""})
    r = client.put("/settings/notifications", data={"enabled": "", "tz": ""})   # disabled inputs aren't posted
    s = store.get_notification_settings(client.app.state.conn)
    assert not s.enabled and s.calling and not s.sick and s.quiet_from == time(21) and s.tz == "America/New_York"
    assert 'class="rows off"' in r.text and 'name="calling" value="1" checked disabled' in r.text
    assert client.post("/settings/notifications/test").status_code == 409


def test_quiet_hours_use_the_keepers_timezone():
    s = NotificationSettings(quiet_enabled=True, tz="America/New_York")
    utc_3am = datetime(2026, 10, 25, 3, 0, tzinfo=timezone.utc)                 # 23:00 in New York
    assert notify.keeper_now(s, utc_3am) == time(23, 0)
    assert notify.keeper_now(NotificationSettings(tz="Not/AZone"), utc_3am) == time(3, 0)
    pet = type("P", (), {"name": "Pip", "id": "p"})()
    assert notify.for_event("calling", pet, "", s, notify.keeper_now(s, utc_3am)) is None


def test_test_beep_goes_to_every_page(client):
    bus: Bus = client.app.state.bus

    async def listen():
        sub = bus.subscribe("some-other-pet")                    # a pet page filters by pet...
        got = asyncio.ensure_future(anext(sub))
        await asyncio.sleep(0)
        bus.publish(BusMessage(event="notify", data=json.dumps(notify.test_notice().__dict__)))
        return await asyncio.wait_for(got, 1)
    msg = asyncio.run(listen())
    assert msg.event == "notify" and json.loads(msg.data)["tag"] == "test"        # ...and still hears it
    assert client.post("/settings/notifications/test").status_code == 204


def test_a_call_notifies_pages_of_other_pets(client):
    conn = client.app.state.conn
    bus: Bus = client.app.state.bus
    seen = []
    real = bus.publish
    bus.publish = lambda m: (seen.append(m), real(m))[1]
    pet_id = client.post("/pets", data=HATCH, follow_redirects=False).headers["location"].rsplit("/", 1)[1]
    wait_for(lambda: store.pending_calls(conn, pet_id))
    [n] = [m for m in seen if m.event == "notify"]
    assert n.pet_id is None and json.loads(n.data)["title"] == "Biscuit is calling you"


def test_opt_in_banner_is_on_every_page(client):
    for path in ("/", "/hatch", "/settings"):
        page = client.get(path).text
        assert 'class="optin"' in page and 'data-optin="ask"' in page
