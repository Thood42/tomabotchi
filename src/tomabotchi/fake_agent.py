"""A scripted ACP agent over a WebSocket.

It is the "practice brain" for the slice 1 tracer bullet, and the agent the integration tests
talk to. It speaks the same frames petd will: one JSON-RPC message per WebSocket message.
"""
from __future__ import annotations

import asyncio
import itertools
import json
from typing import Awaitable, Callable

from websockets.asyncio.server import Server, ServerConnection, serve

OPTIONS = [
    {"optionId": "allow_once", "name": "Allow once", "kind": "allow_once"},
    {"optionId": "allow_always", "name": "Always allow", "kind": "allow_always"},
    {"optionId": "reject_once", "name": "Deny", "kind": "reject_once"},
]


class Turn:
    """What a script can do during one session/prompt turn."""

    def __init__(self, agent: "FakeAgent", ws: ServerConnection, session_id: str) -> None:
        self.agent, self.ws, self.session_id = agent, ws, session_id

    async def _update(self, update: dict) -> None:
        await asyncio.sleep(self.agent.step_delay)
        await self.ws.send(json.dumps({"jsonrpc": "2.0", "method": "session/update",
                                       "params": {"sessionId": self.session_id, "update": update}}))

    async def thought(self, text: str) -> None:
        await self._update({"sessionUpdate": "agent_thought_chunk", "content": {"type": "text", "text": text}})

    async def message(self, text: str) -> None:
        await self._update({"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": text}})

    async def plan(self, steps: list[str], current: int) -> None:
        status = lambda i: "completed" if i < current else "in_progress" if i == current else "pending"  # noqa: E731
        await self._update({"sessionUpdate": "plan", "entries": [
            {"content": s, "priority": "medium", "status": status(i)} for i, s in enumerate(steps)]})

    async def tool_call(self, call: dict) -> None:
        await self._update({"sessionUpdate": "tool_call", "status": "pending", **call})

    async def tool_update(self, tool_call_id: str, status: str, **extra) -> None:
        await self._update({"sessionUpdate": "tool_call_update", "toolCallId": tool_call_id, "status": status, **extra})

    async def ask(self, tool_call: dict, options: list[dict] = OPTIONS) -> str:
        """Send session/request_permission and wait. Returns the chosen optionId, or "cancelled"."""
        await asyncio.sleep(self.agent.step_delay)
        req_id = f"fake-{next(self.agent._ids)}"
        fut = asyncio.get_running_loop().create_future()
        self.agent._waiting[req_id] = fut
        await self.ws.send(json.dumps({"jsonrpc": "2.0", "id": req_id, "method": "session/request_permission",
                                       "params": {"sessionId": self.session_id, "toolCall": tool_call,
                                                  "options": options}}))
        result = await fut
        outcome = result.get("outcome", {})
        return outcome.get("optionId", "cancelled") if outcome.get("outcome") == "selected" else "cancelled"


Script = Callable[[Turn, str], Awaitable[str]]     # returns the stopReason


async def write_note_script(t: Turn, prompt: str) -> str:
    steps = ["Look at what's already in notes/", "Write the note", "Report back"]
    await t.thought(f"My keeper asked: “{prompt.strip()[:120]}”. I'll make a short plan first.")
    await t.plan(steps, current=0)
    await t.tool_call({"toolCallId": "read-1", "title": "List notes/", "kind": "search",
                       "locations": [{"path": "/workspace/notes"}], "rawInput": {"path": "notes/"}})
    await t.tool_update("read-1", "in_progress")
    await t.tool_update("read-1", "completed")
    await t.thought("There's no note on this yet, so I'll write a new one.")
    await t.plan(steps, current=1)
    write = {"toolCallId": "write-1", "title": "Write notes/hello.md", "kind": "edit",
             "locations": [{"path": "/workspace/notes/hello.md"}],
             "rawInput": {"path": "notes/hello.md", "content": "# Hello\n\nWritten by a practice pet.\n"}}
    await t.tool_call(write)
    await t.thought("I have what I need. Writing a file needs your OK, so I'm asking.")
    choice = await t.ask(write)
    if choice.startswith("allow"):
        await t.tool_update("write-1", "completed")
        await t.plan(steps, current=2)
        await t.message("Done: I wrote notes/hello.md.")
        await t.plan(steps, current=3)
        return "end_turn"
    await t.tool_update("write-1", "failed")
    await t.thought("My keeper said no. I'll leave the file alone.")
    await t.message("Okay, I didn't write it.")
    return "end_turn"


async def sick_script(t: Turn, prompt: str) -> str:
    """Keeps retrying the same failing fetch: what a stuck pet looks like."""
    await t.thought("I'll fetch the paper list first.")
    await t.plan(["Fetch this week's papers", "Summarise them"], current=0)
    for i in range(4):
        await t.tool_call({"toolCallId": f"fetch-{i}", "title": "Read page arxiv.org/list/cs.AI/new", "kind": "fetch",
                           "rawInput": {"url": "https://arxiv.org/list/cs.AI/new"}})
        await t.tool_update(f"fetch-{i}", "failed")
        await t.thought("That failed. I'll try the exact same thing again.")
    return "end_turn"


def pick_script(prompt: str) -> Script:
    return sick_script if "#sick" in prompt else write_note_script


class FakeAgent:
    def __init__(self, script: Script | None = None, step_delay: float = 0.8, token: str | None = None) -> None:
        self.script = script
        self.step_delay = step_delay
        self.token = token
        self.received: list[dict] = []          # every message from the client, for tests
        self._server: Server | None = None
        self._ids = itertools.count(1)
        self._sessions = itertools.count(1)
        self._waiting: dict[str, asyncio.Future] = {}
        self._turns: set[asyncio.Task] = set()

    async def start(self, host: str = "127.0.0.1", port: int = 0) -> str:
        self._server = await serve(self._handle, host, port, max_size=None)
        sock = next(iter(self._server.sockets))
        h, p = sock.getsockname()[:2]
        return f"ws://{h}:{p}"

    async def stop(self) -> None:
        for t in list(self._turns):
            t.cancel()
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()

    async def _handle(self, ws: ServerConnection) -> None:
        if self.token and ws.request.headers.get("Authorization") != f"Bearer {self.token}":
            await ws.close(4401, "bad token")
            return
        async for raw in ws:
            msg = json.loads(raw)
            self.received.append(msg)
            method, req_id = msg.get("method"), msg.get("id")
            if method == "initialize":
                await self._reply(ws, req_id, {"protocolVersion": 1, "agentCapabilities": {"loadSession": False}})
            elif method == "session/new":
                await self._reply(ws, req_id, {"sessionId": f"fake-session-{next(self._sessions)}"})
            elif method == "session/prompt":
                text = " ".join(b.get("text", "") for b in msg["params"]["prompt"] if b.get("type") == "text")
                task = asyncio.create_task(self._turn(ws, req_id, msg["params"]["sessionId"], text))
                self._turns.add(task)
                task.add_done_callback(self._turns.discard)
            elif method == "session/cancel":
                for t in list(self._turns):
                    t.cancel()
            elif method is None and req_id in self._waiting:
                self._waiting.pop(req_id).set_result(msg.get("result") or {})

    async def _turn(self, ws: ServerConnection, req_id, session_id: str, text: str) -> None:
        try:
            stop = await (self.script or pick_script(text))(Turn(self, ws, session_id), text)
        except asyncio.CancelledError:
            stop = "cancelled"
        await self._reply(ws, req_id, {"stopReason": stop})

    @staticmethod
    async def _reply(ws: ServerConnection, req_id, result: dict) -> None:
        await ws.send(json.dumps({"jsonrpc": "2.0", "id": req_id, "result": result}))
