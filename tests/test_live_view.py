"""Slice 3: what the live view shows comes from what the pet actually did."""
import asyncio
from datetime import datetime, timezone

import pytest

from tomabotchi import store, views
from tomabotchi.fake_agent import FakeAgent, Turn
from tomabotchi.models import Pet, Usage
from tomabotchi.supervisor import PetSupervisor, translate

NOW = datetime(2026, 10, 25, tzinfo=timezone.utc)


async def until(pred, timeout=5.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while not pred():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition never became true")
        await asyncio.sleep(0.01)


async def streamy(t: Turn, prompt: str) -> str:
    for piece in ("I'll ", "start ", "by ", "listing ", "files."):
        await t._update({"sessionUpdate": "agent_thought_chunk", "content": {"type": "text", "text": piece}})
    for piece in ("All ", "done."):
        await t.message(piece)
    return "end_turn"


@pytest.fixture
async def make_pet(cfg, conn, bus):
    made = []

    async def make(script=None, **pet_fields):
        agent = FakeAgent(script=script, step_delay=0, token="tok")
        url = await agent.start()
        pet = Pet(**{"id": f"p{len(made)}", "name": "Biscuit", "shell": "grape", "look_seed": 15, "home": "python",
                     "brain": "fake", "model": "local-qwen", "state": "hatching", "born_at": store.now(), **pet_fields})
        store.insert_pet(conn, pet)
        sup = PetSupervisor(cfg, conn, bus, pet, url, "tok")
        await sup.start()
        made.append((sup, agent))
        return sup
    yield make
    for sup, agent in made:
        await sup.stop()
        await agent.stop()


async def test_streamed_chunks_grow_one_thought_and_one_message(make_pet, conn):
    sup = await make_pet(streamy)
    task_id = await sup.feed("go")
    await until(lambda: store.get_task(conn, task_id).state == "done")
    evs = store.recent_events(conn, sup.pet.id, {"thought", "message"}, 10)
    assert [(e.kind, e.payload["text"]) for e in evs] == [("thought", "I'll start by listing files."),
                                                          ("message", "All done.")]


async def test_repeated_identical_failures_make_the_pet_sick(make_pet, conn, bus):
    sup = await make_pet()
    seen = []

    async def collect():
        async for m in bus.subscribe(sup.pet.id):
            seen.append(m)
    c = asyncio.create_task(collect())
    await asyncio.sleep(0)
    await sup.feed("summarise papers #sick")
    await until(lambda: views.pet_context(conn, store.get_pet(conn, sup.pet.id))["vitals"].scene == "sick")
    ctx = views.pet_context(conn, store.get_pet(conn, sup.pet.id))
    assert "times in a row" in ctx["vitals"].sick_reason
    assert ctx["doing"].startswith("Sick:")
    await until(lambda: any(m.event == "notify" and "is sick" in m.data for m in seen))
    c.cancel()


async def test_reasoning_from_the_gateway_shows_only_when_the_brain_sent_no_thoughts(make_pet, conn):
    async def silent(t: Turn, prompt: str) -> str:
        await asyncio.sleep(0.2)
        return "end_turn"
    sup = await make_pet(silent)
    await sup.feed("go")
    sup.on_gateway_usage("Thinking about which file to open.")
    assert [e.payload["text"] for e in store.recent_events(conn, sup.pet.id, {"thought"}, 5)] == \
        ["Thinking about which file to open."]
    sup._thoughts_this_turn = 1                       # the brain streamed its own thoughts this turn
    sup.on_gateway_usage("Duplicate reasoning.")
    assert len(store.recent_events(conn, sup.pet.id, {"thought"}, 5)) == 1


async def test_energy_uses_metered_tokens(make_pet, conn):
    sup = await make_pet(budget_tokens=100_000)
    task_id = await sup.feed("go")
    store.add_usage(conn, Usage(pet_id=sup.pet.id, task_id=task_id, ts=store.now(), model="m",
                                prompt_tokens=50_000, completion_tokens=9_000))
    ctx = views.pet_context(conn, store.get_pet(conn, sup.pet.id))
    assert ctx["tokens"] == 59_000
    assert ctx["vitals"].energy_pct == 41.0


def test_usage_update_becomes_context_gauge():
    [ev] = translate({"sessionUpdate": "usage_update", "size": 65536, "used": 13280}, pet_id="p", task_id=1,
                     tool_index={}, now=NOW)
    assert ev.payload == {"type": "usage_update", "context_pct": 20.3}


def test_hermes_permission_bookkeeping_is_not_a_tool_the_pet_used():
    assert translate({"sessionUpdate": "tool_call_update", "toolCallId": "perm-check-1", "status": "completed"},
                     pet_id="p", task_id=1, tool_index={}, now=NOW) == []
    assert translate({"sessionUpdate": "session_info_update", "title": "x"}, pet_id="p", task_id=1,
                     tool_index={}, now=NOW) == []


async def test_thinking_shows_only_the_current_task(make_pet, conn):
    sup = await make_pet(streamy)
    first = await sup.feed("go")
    await until(lambda: store.get_task(conn, first).state == "done")
    second = await sup.feed("again")
    await until(lambda: store.get_task(conn, second).state == "done")
    ctx = views.pet_context(conn, store.get_pet(conn, sup.pet.id))
    assert {e.task_id for e in ctx["thoughts"]} == {second}
