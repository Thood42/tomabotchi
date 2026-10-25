"""ACP (Agent Client Protocol) client over a WebSocket.

petd exposes the brain's stdio JSON-RPC as one JSON message per WebSocket frame. We are the
client: we send initialize / session/new / session/prompt / session/cancel, and answer the
agent's session/request_permission. session/update notifications go to on_update in order.
"""
from __future__ import annotations

import asyncio
import itertools
import json
import logging
import secrets
from typing import Any, Awaitable, Callable

from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed

log = logging.getLogger(__name__)

PROTOCOL_VERSION = 1

UpdateHandler = Callable[[dict], Awaitable[None]]
PermissionHandler = Callable[[Any, dict], Awaitable[dict]]


class AcpError(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(f"ACP error {code}: {message}")
        self.code = code
        self.message = message


class AcpConnection:
    def __init__(self, url: str, token: str, on_update: UpdateHandler, on_permission: PermissionHandler) -> None:
        self.url = url
        self.token = token
        self.on_update = on_update
        self.on_permission = on_permission
        self._ws: ClientConnection | None = None
        self._reader: asyncio.Task | None = None
        # Start ids somewhere random: after a server restart, a response to the old process's request
        # (still in flight in the agent) must not land on one of ours.
        self._ids = itertools.count(secrets.randbelow(2**31) * 1000 + 1)
        self._pending: dict[int, asyncio.Future] = {}
        self._handlers: set[asyncio.Task] = set()
        self.on_unmatched: Callable[[dict], None] | None = None   # responses to requests we didn't send

    @property
    def connected(self) -> bool:
        return self._ws is not None and self._reader is not None and not self._reader.done()

    async def connect(self) -> None:
        self._ws = await connect(self.url, additional_headers={"Authorization": f"Bearer {self.token}"},
                                 max_size=None, ping_interval=20)
        self._reader = asyncio.create_task(self._read())

    async def initialize(self) -> dict:
        return await self._request("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "clientCapabilities": {"fs": {"readTextFile": False, "writeTextFile": False}, "terminal": False},
        })

    async def new_session(self, cwd: str = "/workspace") -> str:
        res = await self._request("session/new", {"cwd": cwd, "mcpServers": []})
        return res["sessionId"]

    async def set_mode(self, session_id: str, mode_id: str) -> dict:
        return await self._request("session/set_mode", {"sessionId": session_id, "modeId": mode_id})

    async def prompt(self, session_id: str, text: str) -> dict:
        return await self._request("session/prompt", {
            "sessionId": session_id, "prompt": [{"type": "text", "text": text}]})

    async def cancel(self, session_id: str) -> None:
        await self._send({"jsonrpc": "2.0", "method": "session/cancel", "params": {"sessionId": session_id}})

    async def respond(self, req_id: Any, result: dict) -> None:
        """Answer an agent request this connection didn't receive (one adopted after a restart)."""
        await self._send({"jsonrpc": "2.0", "id": req_id, "result": result})

    async def petd(self, command: str) -> None:
        """A control message for petd itself ("pause" or "resume"); never reaches the agent."""
        await self._send({"jsonrpc": "2.0", "method": f"petd/{command}", "params": {}})

    async def close(self) -> None:
        if self._ws is not None:
            await self._ws.close()
        if self._reader is not None:
            await asyncio.gather(self._reader, return_exceptions=True)

    # ---- internals ----

    async def _send(self, msg: dict) -> None:
        if self._ws is None:
            raise ConnectionError("not connected")
        try:
            await self._ws.send(json.dumps(msg))
        except ConnectionClosed as e:
            raise ConnectionError(f"ACP connection closed: {e}") from e

    async def _request(self, method: str, params: dict) -> dict:
        req_id = next(self._ids)
        fut = asyncio.get_running_loop().create_future()
        self._pending[req_id] = fut
        await self._send({"jsonrpc": "2.0", "id": req_id, "method": method, "params": params})
        return await fut

    async def _read(self) -> None:
        try:
            async for raw in self._ws:
                msg = json.loads(raw)
                if "method" in msg and "id" in msg:
                    task = asyncio.create_task(self._handle_request(msg))
                    self._handlers.add(task)
                    task.add_done_callback(self._handlers.discard)
                elif "method" in msg:
                    if msg["method"] == "session/update":
                        try:
                            await self.on_update(msg["params"])
                        except Exception:  # noqa: BLE001 - one bad update must not kill the stream
                            log.exception("session/update handler failed")
                elif "id" in msg:
                    fut = self._pending.pop(msg["id"], None)
                    if fut is None:
                        if self.on_unmatched:
                            self.on_unmatched(msg)
                        continue
                    if fut.done():
                        continue
                    if "error" in msg:
                        fut.set_exception(AcpError(msg["error"].get("code", -1), msg["error"].get("message", "")))
                    else:
                        fut.set_result(msg.get("result") or {})
        except ConnectionClosed as e:
            log.warning("ACP connection to %s closed: %s", self.url, e)
        finally:
            for fut in self._pending.values():
                if not fut.done():
                    fut.set_exception(ConnectionError("ACP connection closed"))
            self._pending.clear()

    async def _handle_request(self, msg: dict) -> None:
        if msg["method"] == "session/request_permission":
            try:
                result = await self.on_permission(msg["id"], msg["params"])
                reply = {"jsonrpc": "2.0", "id": msg["id"], "result": result}
            except Exception as e:  # noqa: BLE001
                log.exception("permission handler failed")
                reply = {"jsonrpc": "2.0", "id": msg["id"], "error": {"code": -32603, "message": str(e)}}
        else:
            reply = {"jsonrpc": "2.0", "id": msg["id"],
                     "error": {"code": -32601, "message": f"client does not implement {msg['method']}"}}
        try:
            await self._send(reply)
        except Exception:  # noqa: BLE001
            log.warning("could not reply to %s (connection gone)", msg["method"])
