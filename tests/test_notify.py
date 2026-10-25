from dataclasses import replace
from datetime import datetime, time, timezone

from tomabotchi.models import NotificationSettings, Pet
from tomabotchi.notify import for_event, in_quiet_hours

PET = Pet(id="p1", name="Biscuit", shell="grape", look_seed=15, home="python", brain="fake",
          model="local-qwen", state="awake", born_at=datetime(2026, 10, 25, tzinfo=timezone.utc))
NOON = time(12, 0)


def test_calling_notice_names_the_pet_and_links_to_it():
    n = for_event("calling", PET, "Wants your OK to write notes/hello.md", NotificationSettings(), NOON)
    assert n.title == "Biscuit is calling you"
    assert n.url == "/pets/p1"


def test_master_off_suppresses_all():
    s = NotificationSettings(enabled=False)
    assert all(for_event(k, PET, "", s, NOON) is None for k in ("calling", "sick", "finished", "out_of_budget"))


def test_per_event_switch():
    s = NotificationSettings(sick=False)
    assert for_event("sick", PET, "", s, NOON) is None
    assert for_event("calling", PET, "", s, NOON) is not None


def test_finished_off_by_default():
    assert for_event("finished", PET, "", NotificationSettings(), NOON) is None


def test_quiet_hours_wrap_midnight():
    start, end = time(22, 0), time(7, 0)
    assert in_quiet_hours(time(23, 30), start, end)
    assert in_quiet_hours(time(6, 59), start, end)
    assert not in_quiet_hours(time(7, 0), start, end)
    assert not in_quiet_hours(NOON, start, end)


def test_quiet_hours_only_apply_when_enabled():
    s = NotificationSettings(quiet_enabled=True)
    assert for_event("calling", PET, "", s, time(23, 0)) is None
    assert for_event("calling", PET, "", replace(s, quiet_enabled=False), time(23, 0)) is not None
