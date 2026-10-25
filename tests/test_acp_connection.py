import asyncio

import pytest

from tomabotchi.acp import AcpConnection
from tomabotchi.fake_agent import FakeAgent, Turn


async def no_updates(params):
    return None


async def deny(req_id, params):
    return {"outcome": {"outcome": "selected", "optionId": "reject_once"}}


@pytest.fixture
async def agent():
    a = FakeAgent(step_delay=0, token="tok")
    url = await a.start()
    a.url = url
    yield a
    await a.stop()


async def test_initialize_and_new_session(agent):
    acp = AcpConnection(agent.url, "tok", no_updates, deny)
    await acp.connect()
    init = await acp.initialize()
    assert init["protocolVersion"] == 1
    assert (await acp.new_session()).startswith("fake-session-")
    assert agent.received[0]["params"]["clientCapabilities"]["terminal"] is False
    await acp.close()


async def test_prompt_resolves_on_stop_reason(agent):
    async def quick(t: Turn, prompt: str) -> str:
        await t.message(f"echo {prompt}")
        return "end_turn"
    agent.script = quick
    updates = []

    async def on_update(params):
        updates.append(params["update"])

    acp = AcpConnection(agent.url, "tok", on_update, deny)
    await acp.connect()
    await acp.initialize()
    sid = await acp.new_session()
    res = await asyncio.wait_for(acp.prompt(sid, "hi"), 5)
    assert res == {"stopReason": "end_turn"}
    assert updates == [{"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "echo hi"}}]
    await acp.close()


async def test_incoming_permission_request_routed_to_handler(agent):
    asked = []

    async def on_permission(req_id, params):
        asked.append(params["toolCall"]["toolCallId"])
        return {"outcome": {"outcome": "selected", "optionId": "reject_once"}}

    acp = AcpConnection(agent.url, "tok", no_updates, on_permission)
    await acp.connect()
    await acp.initialize()
    sid = await acp.new_session()
    await asyncio.wait_for(acp.prompt(sid, "write a note"), 5)
    assert asked == ["write-1"]
    reply = next(m for m in agent.received if m.get("id", "").__str__().startswith("fake-"))
    assert reply["result"]["outcome"]["optionId"] == "reject_once"
    await acp.close()


async def test_bad_token_is_refused(agent):
    acp = AcpConnection(agent.url, "wrong", no_updates, deny)
    await acp.connect()
    with pytest.raises(ConnectionError):
        await asyncio.wait_for(acp.initialize(), 5)
