"""FastAPI app: pages, keeper actions, and the SSE stream. Localhost only."""
from __future__ import annotations

import asyncio
import ipaddress
import logging
import os
import random
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import config, db, gateway, store, views
from .bus import Bus
from .fleet import Fleet
from .models import HatchForm, ToolRule


@asynccontextmanager
async def lifespan(app: FastAPI):
    cfg = config.load()
    conn = db.connect(cfg.db_path)
    bus = Bus()
    fleet = Fleet(cfg, conn, bus, _docker(cfg))
    app.state.cfg, app.state.conn, app.state.bus, app.state.fleet = cfg, conn, bus, fleet
    app.state.on_usage = fleet.on_usage
    views.CONFIG = cfg
    await fleet.boot()
    yield
    await fleet.shutdown()
    conn.close()


log = logging.getLogger(__name__)


def _docker(cfg):
    """Docker-backed pets need the Docker socket; without it only practice pets can hatch."""
    if os.environ.get("TOMABOTCHI_DOCKER") == "0":
        return None
    try:
        from .docker_ops import DockerOps
        ops = DockerOps(cfg)
        ops.client.ping()
        return ops
    except Exception as e:  # noqa: BLE001
        log.warning("Docker unavailable (%s): only practice pets can hatch", e)
        return None


app = FastAPI(title="tomabotchi", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=Path(__file__).with_name("static")), name="static")
app.include_router(gateway.router)


@app.middleware("http")
async def pet_subnet_guard(request: Request, call_next):
    """Pets share a network with the server so they can reach the model gateway. Everything else
    (approving calls, editing rules) is the keeper's, so requests from pet subnets get a 403."""
    host = request.client.host if request.client else None
    if host and not request.url.path.startswith("/llm/"):
        try:
            if ipaddress.ip_address(host) in request.app.state.cfg.pet_subnet_pool:
                return PlainTextResponse("Pets can only reach the model gateway.", status_code=403)
        except ValueError:
            pass
    return await call_next(request)


def _page(name: str, **ctx) -> HTMLResponse:
    return HTMLResponse(views.render(name, **ctx))


def _pet_or_404(request: Request, pet_id: str):
    try:
        return store.get_pet(request.app.state.conn, pet_id)
    except LookupError:
        raise HTTPException(404, "No pet by that name lives here.") from None


# ---- pages ----

@app.get("/", response_class=HTMLResponse)
def shelf(request: Request):
    return _page("shelf.html", **views.shelf_context(request.app.state.conn))


@app.get("/hatch", response_class=HTMLResponse)
def hatch_page(request: Request):
    cfg = request.app.state.cfg
    return _page("hatch.html", homes=cfg.homes, models=cfg.models, shells=views.SHELLS,
                 seed=random.randrange(1, 99999))


@app.get("/pets/{pet_id}", response_class=HTMLResponse)
def pet_page(request: Request, pet_id: str):
    pet = _pet_or_404(request, pet_id)
    return _page("pet.html", **views.pet_context(request.app.state.conn, pet))


# ---- actions ----

@app.post("/pets")
async def hatch(request: Request, name: str = Form(...), shell: str = Form("grape"), look_seed: int = Form(15),
                purpose_md: str = Form(""), voice_md: str = Form(""), ask_first_md: str = Form(""),
                never_md: str = Form(""), home: str = Form("python"), brain: str = Form("fake"),
                model: str = Form("local-qwen"), budget: str = Form("100k-2h"), first_task_md: str = Form("")):
    if shell not in views.SHELLS:
        raise HTTPException(400, f"Pick one of the shells: {', '.join(views.SHELLS)}.")
    form = HatchForm(name=name, shell=shell, look_seed=look_seed, purpose_md=purpose_md, voice_md=voice_md,
                     ask_first_md=ask_first_md, never_md=never_md, home=home, brain=brain, model=model,
                     budget=budget, first_task_md=first_task_md or None)
    try:
        pet = await request.app.state.fleet.hatch(form)
    except KeyError as e:
        raise HTTPException(400, f"Unknown choice: {e}. Pick from the lists on the hatch page.") from None
    except RuntimeError as e:
        raise HTTPException(400, f"{e}.") from None
    return RedirectResponse(f"/pets/{pet.id}", status_code=303)


@app.post("/pets/{pet_id}/tasks")
async def feed(request: Request, pet_id: str, prompt_md: str = Form(...)):
    _pet_or_404(request, pet_id)
    fleet = request.app.state.fleet
    if not fleet.is_awake(pet_id):
        raise HTTPException(409, "It's still waking up. Try again in a moment.")
    await fleet.get(pet_id).feed(prompt_md)
    return RedirectResponse(f"/pets/{pet_id}", status_code=303)


@app.put("/pets/{pet_id}/rules", response_class=HTMLResponse)
async def put_rules(request: Request, pet_id: str):
    """The rules editor posts one row per rule (kind, tool, path_glob, rule); rule "" skips the
    blank add row and "remove" drops a row."""
    pet = _pet_or_404(request, pet_id)
    form = await request.form()
    rows = zip(form.getlist("kind"), form.getlist("tool"), form.getlist("path_glob"), form.getlist("rule"))
    rules = []
    for kind, tool, path_glob, rule in rows:
        if rule in ("", "remove"):
            continue
        if kind not in views.KIND_LABELS or rule not in views.RULE_VALUES:
            raise HTTPException(400, f"Pick a kind and a rule from the lists (got {kind!r}, {rule!r}).")
        rules.append(ToolRule(kind=kind, tool=tool.strip() or None, path_glob=path_glob.strip().lstrip("/") or None,
                              rule=rule))
    await request.app.state.fleet.set_rules(pet_id, rules)
    return views.render("_rules.html", **views.rules_context(request.app.state.conn, pet, saved=True))


@app.post("/calls/{call_id}", response_class=HTMLResponse)
async def answer_call(request: Request, call_id: int, option: str = Form(...), reason: str = Form("")):
    if option not in ("allow_once", "allow_always", "reject_once"):
        raise HTTPException(400, "Answer with allow_once, allow_always or reject_once.")
    conn = request.app.state.conn
    try:
        call = store.get_call(conn, call_id)
    except LookupError:
        raise HTTPException(404, "That call isn't waiting any more.") from None
    if call.answered_at is not None:
        raise HTTPException(409, "That call was already answered.")
    if not request.app.state.fleet.is_awake(call.pet_id):
        raise HTTPException(409, "That pet is still waking up. Try again in a moment.")
    await request.app.state.fleet.answer(call_id, option, reason.strip() or None)
    pet = store.get_pet(conn, call.pet_id)
    return views.render("_call.html", **views.pet_context(conn, pet))


# ---- live updates ----

def _sse(event: str, data: str) -> str:
    lines = "".join(f"data: {line}\n" for line in data.splitlines() or [""])
    return f"event: {event}\n{lines}\n"


@app.get("/events")
async def events(request: Request, pet: str | None = None):
    bus: Bus = request.app.state.bus

    async def stream():
        yield ": hello\n\n"
        sub = bus.subscribe(pet)
        nxt = asyncio.ensure_future(anext(sub))
        try:
            while not await request.is_disconnected():
                done, _ = await asyncio.wait({nxt}, timeout=15)
                if not done:
                    yield ": keepalive\n\n"
                    continue
                msg = nxt.result()
                yield _sse(msg.event, msg.data)
                nxt = asyncio.ensure_future(anext(sub))
        finally:
            nxt.cancel()
            await asyncio.gather(nxt, return_exceptions=True)    # let the pending read unwind first
            await sub.aclose()

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
