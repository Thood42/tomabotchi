"""petd: the supervisor inside every pet container.

Starts code-server (the keeper's VS Code) and the brain's ACP agent, and bridges the agent's
stdio (newline-delimited JSON-RPC) to one WebSocket client: the tomabotchi server, which
authenticates with the pet's token. While no client is connected, agent output is buffered and
replayed on reconnect, so a pet keeps working through a dashboard restart.

Runs on Hermes's bundled Python; needs only the stdlib and websockets.
"""
from __future__ import annotations

import asyncio
import collections
import logging
import os
import signal
import sys

from websockets.asyncio.server import ServerConnection, serve

log = logging.getLogger("petd")

AGENT_ARGV = {
    "hermes": ["/opt/hermes/.venv/bin/python", "-m", "acp_adapter"],
}
CODE_SERVER = ["/opt/tomabotchi/code-server/bin/code-server", "--auth", "none", "--bind-addr", "0.0.0.0:8080",
               "--disable-telemetry", "--disable-update-check", "--disable-workspace-trust", "/workspace"]
BUFFER_MAX = 5000
LINE_LIMIT = 64 * 1024 * 1024


class Petd:
    def __init__(self, token: str, brain: str) -> None:
        self.token = token
        self.brain = brain
        self.ws: ServerConnection | None = None
        self.buffer: collections.deque[str] = collections.deque(maxlen=BUFFER_MAX)
        self.agent: asyncio.subprocess.Process | None = None

    async def run(self) -> int:
        code = await asyncio.create_subprocess_exec(*CODE_SERVER, stdout=asyncio.subprocess.DEVNULL,
                                                    stderr=asyncio.subprocess.DEVNULL, cwd="/workspace")
        self.agent = await asyncio.create_subprocess_exec(
            *AGENT_ARGV[self.brain], stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            cwd="/workspace", limit=LINE_LIMIT)
        log.info("petd up: brain=%s agent pid=%s code-server pid=%s", self.brain, self.agent.pid, code.pid)
        async with serve(self._client, "0.0.0.0", 7000, max_size=None, ping_interval=20):
            pump = asyncio.create_task(self._pump_agent_stdout())
            rc = await self.agent.wait()
            pump.cancel()
        log.error("agent exited with %s", rc)
        code.terminate()
        return rc or 1

    async def _client(self, ws: ServerConnection) -> None:
        if ws.request.headers.get("Authorization") != f"Bearer {self.token}":
            await ws.close(4401, "bad token")
            return
        if self.ws is not None:
            await self.ws.close(4409, "replaced by a newer connection")
        self.ws = ws
        log.info("client connected, replaying %d buffered messages", len(self.buffer))
        while self.buffer:
            await ws.send(self.buffer.popleft())
        try:
            async for msg in ws:
                text = msg if isinstance(msg, str) else msg.decode()
                self.agent.stdin.write(text.encode() + b"\n")
                await self.agent.stdin.drain()
        finally:
            if self.ws is ws:
                self.ws = None
            log.info("client disconnected")

    async def _pump_agent_stdout(self) -> None:
        while True:
            line = await self.agent.stdout.readline()
            if not line:
                return
            text = line.decode().strip()
            if not text:
                continue
            ws = self.ws
            if ws is None:
                self.buffer.append(text)
                continue
            try:
                await ws.send(text)
            except Exception:  # noqa: BLE001 - client went away mid-send; keep it for the next one
                self.buffer.append(text)


def main() -> None:
    logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="petd %(levelname)s %(message)s")
    token, brain = os.environ.get("PET_TOKEN", ""), os.environ.get("BRAIN", "hermes")
    if not token:
        sys.exit("petd: PET_TOKEN is not set")
    if brain not in AGENT_ARGV:
        sys.exit(f"petd: unknown brain {brain!r}")
    os.makedirs("/home/pet", exist_ok=True)
    loop = asyncio.new_event_loop()
    task = loop.create_task(Petd(token, brain).run())
    loop.add_signal_handler(signal.SIGTERM, task.cancel)
    try:
        sys.exit(loop.run_until_complete(task))
    except asyncio.CancelledError:
        sys.exit(0)


if __name__ == "__main__":
    main()
