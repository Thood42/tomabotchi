"""timeline.track: events, calls and tasks in, blocks out. Pure, with a fixed NOW."""
from datetime import datetime, timedelta, timezone

from tomabotchi import store, timeline
from tomabotchi.models import Call, Event, Pet, Task

NOW = datetime(2026, 10, 25, 14, 0, tzinfo=timezone.utc)


def at(seconds):
    return NOW + timedelta(seconds=seconds)


def ev(i, seconds, kind, **payload):
    return Event(id=i, pet_id="p1", task_id=1, ts=at(seconds), kind=kind, payload=payload)


def tool_call(i, seconds, tid="t1", title="Write file", paths=("notes/a.md",)):
    return ev(i, seconds, "tool_call", id=tid, title=title, tool=title, paths=list(paths), status="pending")


def tool_update(i, seconds, status, tid="t1", tool="Write file", args=None):
    return ev(i, seconds, "tool_update", id=tid, status=status, tool=tool, args=args or {"p": 1})


def run(events, calls=(), ended=(), streak=(), loop_notice=None, working=True):
    return timeline.track(list(events), list(calls), list(ended), streak=list(streak), loop_notice=loop_notice,
                          working=working, now=NOW)


def test_thought_runs_until_the_next_event():
    t = run([ev(1, -60, "thought", text="Hmm"), tool_call(2, -30)])
    think = next(b for b in t.blocks if b.kind == "think")
    assert (think.start, think.end) == (at(-60), at(-30))


def test_last_thought_stays_open_while_working():
    evs = [ev(1, -10, "thought", text="Hmm")]
    assert run(evs, working=True).blocks[0].end is None
    assert run(evs, working=False).blocks[0].end == at(-10)


def test_tool_spans_call_to_final_update():
    t = run([tool_call(1, -60), tool_update(2, -59, "in_progress"), tool_update(3, -55, "completed")])
    [block] = t.blocks
    assert (block.kind, block.start, block.end) == ("tool", at(-60), at(-55))
    assert "Write file notes/a.md" in block.label


def test_failed_or_blocked_tool_is_failed():
    failed = run([tool_call(1, -60), tool_update(2, -58, "failed")])
    assert failed.blocks[0].kind == "failed"
    blocked = tool_update(2, -58, "completed")
    blocked.payload["blocked"] = True
    assert run([tool_call(1, -60), blocked]).blocks[0].kind == "failed"


def test_tool_without_a_final_update_is_open_only_while_working():
    assert run([tool_call(1, -60)], working=True).blocks[0].end is None
    assert run([tool_call(1, -60)], working=False).blocks[0].end == at(-60)


def test_open_call_has_no_end_and_answered_call_does():
    open_call = Call(id=1, pet_id="p1", task_id=1, acp_request_id=1, tool_call={"title": "Write notes/a.md"},
                     options=[], asked_at=at(-120))
    answered = Call(id=2, pet_id="p1", task_id=1, acp_request_id=2, tool_call={}, options=[], asked_at=at(-300),
                    answered_at=at(-250))
    blocks = run([], calls=[answered, open_call]).blocks
    assert [(b.kind, b.end) for b in blocks] == [("call", at(-250)), ("call", None)]
    assert blocks[1].label == "Asked to write notes/a.md, waiting"


def test_finished_task_is_a_done_marker_and_chat_is_not():
    done = Task(id=1, pet_id="p1", prompt="Write a note", state="done", started_at=at(-300), ended_at=at(-20))
    chat = Task(id=2, pet_id="p1", prompt="hi", state="done", started_at=at(-30), ended_at=at(-10), kind="chat")
    [block] = run([], ended=[done, chat]).blocks
    assert (block.kind, block.start, block.end) == ("done", at(-20), at(-20))


def test_loop_bracket_spans_the_failed_streak():
    streak = [tool_update(i, -100 + 10 * i, "failed", tool="pytest") for i in range(1, 5)]
    t = run(streak, streak=streak)
    assert (t.loop.start, t.loop.end) == (streak[0].ts, streak[-1].ts)
    assert "pytest" in t.loop.label and "4" in t.loop.label


def test_no_bracket_for_two_failures_or_mixed_failures():
    two = [tool_update(i, -50 + i, "failed") for i in (1, 2)]
    assert run(two, streak=two).loop is None
    assert run([], streak=[]).loop is None


def test_loop_notice_brackets_the_work_leading_up_to_it():
    notice = Event(id=9, pet_id="p1", task_id=1, ts=at(-5), kind="notice", payload={"type": "loop_guard", "text": "Looping"})
    t = run([ev(1, -80, "thought", text="a"), notice], loop_notice=notice)
    assert (t.loop.start, t.loop.end, t.loop.label) == (at(-80), at(-5), "Looping")


def test_blocks_are_in_start_order_and_the_query_honours_the_cap(conn):
    t = run([tool_call(1, -10, "b"), ev(2, -50, "thought", text="x"), tool_call(3, -30, "a")])
    assert [b.start for b in t.blocks] == sorted(b.start for b in t.blocks)

    store.insert_pet(conn, Pet(id="p1", name="Pip", shell="grape", look_seed=1, home="python", brain="fake",
                               model="local-qwen", state="awake", born_at=store.now()))
    for i in range(30):
        store.append_event(conn, Event(id=0, pet_id="p1", task_id=None, ts=store.now(), kind="thought", payload={"text": str(i)}))
    got = store.events_since(conn, "p1", store.now() - timedelta(hours=1), timeline.EVENT_KINDS, 10)
    assert [e.payload["text"] for e in got] == [str(i) for i in range(20, 30)]          # the newest ten, oldest first
    assert store.events_since(conn, "p1", store.now() + timedelta(hours=1), timeline.EVENT_KINDS, 10) == []
