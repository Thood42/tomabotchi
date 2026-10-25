"""The call flow end to end, minus the browser: fake agent ↔ real ACP client ↔ supervisor ↔ SQLite ↔ bus."""
import asyncio
from datetime import date, timedelta

import pytest

from tomabotchi import soul, store, views
from tomabotchi.fake_agent import FakeAgent
from tomabotchi.models import HatchForm, Pet
from tomabotchi.supervisor import PetSupervisor


async def until(pred, timeout=5.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while not pred():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition never became true")
        await asyncio.sleep(0.01)


@pytest.fixture
async def setup(cfg, conn, bus):
    agent = FakeAgent(step_delay=0, token="tok")
    url = await agent.start()
    pet = Pet(id="p1", name="Biscuit", shell="grape", look_seed=15, home="python", brain="fake",
              model="local-qwen", state="hatching", born_at=store.now())
    store.insert_pet(conn, pet)
    sup = PetSupervisor(cfg, conn, bus, pet, url, "tok")
    await sup.start()
    yield sup, agent
    await sup.stop()
    await agent.stop()


async def test_permission_request_opens_call_and_publishes(setup, conn, bus):
    sup, _ = setup
    seen = []

    async def collect():
        async for msg in bus.subscribe("p1"):
            seen.append(msg.event)

    collector = asyncio.create_task(collect())
    await asyncio.sleep(0)
    await sup.feed("write a hello note")
    await until(lambda: store.pending_calls(conn, "p1"))
    [call] = store.pending_calls(conn, "p1")
    assert call.tool_call["toolCallId"] == "write-1"
    await until(lambda: "call-p1" in seen)
    assert {"module-p1", "track-p1", "stream-p1", "controls-p1"} <= set(seen)
    assert views.pet_context(conn, store.get_pet(conn, "p1"))["vitals"].scene == "alert"
    collector.cancel()


async def test_answer_reject_resolves_agent(setup, conn):
    sup, agent = setup
    task_id = await sup.feed("write a hello note")
    await until(lambda: store.pending_calls(conn, "p1"))
    [call] = store.pending_calls(conn, "p1")
    await sup.answer(call.id, "reject_once", "check notes/ first")
    await until(lambda: store.get_task(conn, task_id).state == "done")
    reply = next(m for m in agent.received if str(m.get("id", "")).startswith("fake-"))
    assert reply["result"] == {"outcome": {"outcome": "selected", "optionId": "reject_once"}}
    answered = store.get_call(conn, call.id)
    assert (answered.outcome, answered.reason) == ("reject_once", "check notes/ first")
    texts = [e.payload["text"] for e in store.recent_events(conn, "p1", {"message"}, 5)]
    assert texts == ["Okay, I didn't write it."]


async def test_answer_allow_completes_plan_and_counts_task(setup, conn):
    sup, _ = setup
    task_id = await sup.feed("write a hello note")
    await until(lambda: store.pending_calls(conn, "p1"))
    await sup.answer(store.pending_calls(conn, "p1")[0].id, "allow_once", None)
    await until(lambda: store.get_task(conn, task_id).state == "done")
    assert [s.status for s in store.plan(conn, task_id)] == ["completed"] * 3
    assert store.get_pet(conn, "p1").tasks_done == 1


async def test_metric_median_from_answered_calls(setup, conn):
    sup, _ = setup
    for _ in range(3):
        task_id = await sup.feed("write a hello note")
        await until(lambda: store.pending_calls(conn, "p1"))
        await sup.answer(store.pending_calls(conn, "p1")[0].id, "allow_once", None)
        await until(lambda: store.get_task(conn, task_id).state == "done")
    waits = store.call_waits(conn, store.now() - timedelta(days=7))
    assert len(waits) == 3
    assert views.desk_context(conn, None)["median_wait"] is not None


def test_soul_render_initial_has_sections():
    form = HatchForm(name="Biscuit", shell="grape", look_seed=1, purpose_md="Research things.", voice_md="Short.",
                     ask_first_md="Before writing files.", never_md="", home="python", brain="fake",
                     model="local-qwen", budget="none", first_task_md=None)
    md = soul.render_initial(form, born=date(2026, 10, 19))
    assert md.startswith("# Biscuit\n")
    assert "Hatched 19 October 2026." in md
    assert "## What my keeper wants to approve\nBefore writing files." in md
    assert "I don't ask for permission in chat first" in md
    assert "## Never" not in md              # empty answers leave their section out
    assert "## Lessons" in md and "## Working with my keeper" in md
