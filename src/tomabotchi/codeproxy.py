"""The keeper's VS Code: an HTTP and WebSocket reverse proxy to a pet's code-server, same origin as
the dashboard so the Workspace tab can embed it in an iframe.

/pets/<id>/code/<path> → http://tbx-pet-<id>:8080/<path>. code-server builds relative URLs, so
stripping the prefix is enough. The browser's Host is passed through, which is what code-server's
WebSocket origin check compares against.
"""
from __future__ import annotations

import asyncio
import logging

import httpx
import websockets
from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import RedirectResponse, StreamingResponse
from starlette.background import BackgroundTask
from websockets.exceptions import ConnectionClosed

from . import store
from .docker_ops import container_name

log = logging.getLogger(__name__)
router = APIRouter()

CODE_PORT = 8080
# Hop-by-hop headers (RFC 9110 §7.6.1) belong to one connection, not to the proxied message.
HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailer",
       "transfer-encoding", "upgrade", "content-length"}
_client: httpx.AsyncClient | None = None


def upstream(pet_id: str) -> str:
    """host:port of the pet's code-server, on the pet's own network (tests point this elsewhere)."""
    return f"{container_name(pet_id)}:{CODE_PORT}"


def _client_for() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=httpx.Timeout(30, read=None), follow_redirects=False)
    return _client


def _check_pet(conn, pet_id: str) -> None:
    try:
        pet = store.get_pet(conn, pet_id)
    except LookupError:
        raise HTTPException(404, "No pet by that name lives here.") from None
    if pet.brain == "fake":
        raise HTTPException(404, "Practice pets have no workspace to open.")


@router.get("/pets/{pet_id}/code")
async def code_root(pet_id: str):
    return RedirectResponse(f"/pets/{pet_id}/code/", status_code=307)   # relative URLs need the slash


@router.api_route("/pets/{pet_id}/code/{path:path}", methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
async def http_proxy(request: Request, pet_id: str, path: str):
    _check_pet(request.app.state.conn, pet_id)
    url = f"http://{upstream(pet_id)}/{path}"
    headers = {k: v for k, v in request.headers.items() if k.lower() not in HOP}
    client = _client_for()
    req = client.build_request(request.method, url, params=request.query_params, headers=headers,
                               content=request.stream())
    try:
        resp = await client.send(req, stream=True)
    except httpx.HTTPError as e:
        log.info("code-server for %s unreachable: %s", pet_id, e)
        raise HTTPException(502, "Its VS Code isn't answering yet. If the pet is asleep, wake it first.") from None
    out = {k: v for k, v in resp.headers.items() if k.lower() not in HOP}
    return StreamingResponse(resp.aiter_raw(), status_code=resp.status_code, headers=out,
                             background=BackgroundTask(resp.aclose))


@router.websocket("/pets/{pet_id}/code/{path:path}")
async def ws_proxy(ws: WebSocket, pet_id: str, path: str):
    try:
        _check_pet(ws.app.state.conn, pet_id)
    except HTTPException:
        await ws.close(code=4404)
        return
    query = f"?{ws.url.query}" if ws.url.query else ""
    passed = {k: v for k, v in ws.headers.items()
              if k.lower() in ("cookie", "origin", "user-agent", "accept-language")}
    protocols = [p.strip() for p in ws.headers.get("sec-websocket-protocol", "").split(",") if p.strip()]
    host, _, port = upstream(pet_id).rpartition(":")
    try:
        # The URI carries the browser's Host (code-server's origin check); host/port say where to dial.
        up = await websockets.connect(f"ws://{ws.headers.get('host', host)}/{path}{query}", host=host, port=int(port),
                                      additional_headers=passed, subprotocols=protocols or None,
                                      max_size=None, open_timeout=15, proxy=None)
    except (OSError, websockets.InvalidHandshake, asyncio.TimeoutError) as e:
        log.info("code-server websocket for %s failed: %s", pet_id, e)
        await ws.close(code=1011)
        return
    await ws.accept(subprotocol=up.subprotocol)

    async def browser_to_pet():
        while True:
            msg = await ws.receive()
            if msg["type"] == "websocket.disconnect":
                return
            await up.send(msg["bytes"] if msg.get("bytes") is not None else msg["text"])

    async def pet_to_browser():
        async for data in up:
            if isinstance(data, bytes):
                await ws.send_bytes(data)
            else:
                await ws.send_text(data)

    tasks = [asyncio.create_task(browser_to_pet()), asyncio.create_task(pet_to_browser())]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    except (WebSocketDisconnect, ConnectionClosed):
        pass
    finally:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await up.close()
        try:
            await ws.close()
        except RuntimeError:
            pass                           # the browser already went
