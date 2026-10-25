from datetime import datetime, timedelta, timezone

from tomabotchi import vitals
from tomabotchi.models import Call, Event, Pet, Task

NOW = datetime(2026, 10, 25, 14, 0, tzinfo=timezone.utc)


def pet(**kw):
    return Pet(**{"id": "p1", "name": "Biscuit", "shell": "grape", "look_seed": 15, "home": "python",
                  "brain": "fake", "model": "local-qwen", "state": "awake", "born_at": NOW, **kw})


def upd(status, tool="Read page", args=None, i=0):
    return Event(id=i, pet_id="p1", task_id=1, ts=NOW, kind="tool_update",
                 payload={"id": f"t{i}", "status": status, "tool": tool, "args": args or {"url": "x"}})


def task(state="running"):
    return Task(id=1, pet_id="p1", prompt="x", state=state, started_at=NOW)


CALL = Call(id=1, pet_id="p1", task_id=1, acp_request_id=1, tool_call={}, options=[], asked_at=NOW)


def test_scene_priority_call_beats_sick_beats_sleep():
    p = pet(state="paused")
    assert vitals.scene(p, open_call=CALL, sick_reason="stuck", task=task()) == "alert"
    assert vitals.scene(p, open_call=None, sick_reason="stuck", task=task()) == "sick"
    assert vitals.scene(p, open_call=None, sick_reason=None, task=task()) == "sleep"
    assert vitals.scene(pet(), open_call=None, sick_reason=None, task=task()) == "work"
    assert vitals.scene(pet(), open_call=None, sick_reason=None, task=task("done")) == "happy"
    assert vitals.scene(pet(state="hatching"), open_call=CALL, sick_reason=None, task=None) == "egg"


def test_sick_after_three_identical_failures():
    assert vitals.sick_reason([upd("failed"), upd("failed")], None) is None
    assert vitals.sick_reason([upd("failed"), upd("failed"), upd("failed")], None) is not None
    varied = [upd("failed", args={"url": str(i)}) for i in range(3)]
    assert vitals.sick_reason(varied, None) is None
    recovered = [upd("failed"), upd("failed"), upd("failed"), upd("completed")]
    assert vitals.sick_reason(recovered, None) is None


def compute(p=None, ups=(), **kw):
    args = dict(task=task(), open_call=None, recent_tool_updates=list(ups), tokens_used=0, elapsed=timedelta(0),
                loop_notice=None, context_pct=None, discipline=(0, 0), now=NOW)
    return vitals.compute(p or pet(), **{**args, **kw})


def test_budget_left_takes_the_lower_of_tokens_and_time():
    v = compute(pet(budget_tokens=100_000, budget_seconds=3600), tokens_used=40_000, elapsed=timedelta(minutes=42))
    assert v.readout.budget_left_pct == 30.0


def test_budget_left_from_tokens_alone():
    assert compute(pet(budget_tokens=100_000), tokens_used=59_000).readout.budget_left_pct == 41.0


def test_no_budget_reads_none():
    assert compute().readout.budget_left_pct is None


def test_steps_ok_counts_the_last_ten_finished():
    ups = [upd("failed", args={"n": i}, i=i) for i in range(3)] + [upd("completed", i=10 + i) for i in range(9)]
    assert compute(ups=ups).readout.steps_ok == (9, 10)      # one early failure falls outside the last ten
    ups = [upd("completed", i=i) for i in range(7)] + [upd("failed", args={"n": i}, i=10 + i) for i in range(3)]
    assert compute(ups=ups).readout.steps_ok == (7, 10)
    assert compute().readout.steps_ok == (0, 0)


def test_readout_carries_context_and_discipline():
    r = compute(context_pct=78.0, discipline=(14, 14)).readout
    assert (r.context_pct, r.asked_first) == (78.0, (14, 14))


def test_median_wait_odd_even_empty():
    s = lambda *xs: [timedelta(seconds=x) for x in xs]  # noqa: E731
    assert vitals.median_wait(s(10, 30, 200)) == timedelta(seconds=30)
    assert vitals.median_wait(s(10, 30)) == timedelta(seconds=20)
    assert vitals.median_wait([]) is None


def test_failed_streak_is_trailing_identical_failures():
    a, b = {"url": "a"}, {"url": "b"}
    evs = [upd("failed", args=a, i=1), upd("completed", i=2), upd("failed", args=b, i=3), upd("failed", args=b, i=4),
           upd("failed", args=b, i=5)]
    assert [e.id for e in vitals.failed_streak(evs)] == [3, 4, 5]
    assert vitals.failed_streak(evs + [upd("completed", i=6)]) == []
    assert vitals.failed_streak([]) == []
