"""FastAPI app: pages, keeper actions, and the SSE stream. Localhost only."""
from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import os
import random
from contextlib import asynccontextmanager
from datetime import time
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Query, Request
from fastapi.responses import (FileResponse, HTMLResponse, PlainTextResponse, RedirectResponse, Response,
                               StreamingResponse)
from fastapi.staticfiles import StaticFiles

from . import codeproxy, config, db, gateway, notify, soul, store, views
from .bus import Bus, BusMessage
from .fleet import Fleet
from .models import HatchForm, NotificationSettings, ToolRule


@asynccontextmanager
async def lifespan(app: FastAPI):
    cfg = config.load()
    conn = db.connect(cfg.db_path)
    bus = Bus()
    fleet = Fleet(cfg, conn, bus, _docker(cfg))
    app.state.cfg, app.state.conn, app.state.bus, app.state.fleet = cfg, conn, bus, fleet
    app.state.on_usage = fleet.on_usage
    app.state.on_budget = fleet.on_budget
    app.state.on_model_down = fleet.on_model_down
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
class ImmutableStatic(StaticFiles):
    """Asset URLs carry ?v=<hash of static/>, so a browser may keep each one for good."""

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        if response.status_code == 200:
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response


app.mount("/static", ImmutableStatic(directory=Path(__file__).with_name("static")), name="static")
app.include_router(gateway.router)
app.include_router(codeproxy.router)


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


@app.get("/healthz", response_class=PlainTextResponse)
def healthz() -> str:
    return "ok"


def _page(request: Request, name: str, **ctx) -> HTMLResponse:
    conn = request.app.state.conn
    pets = ctx.get("pets") or views.rack_context(conn)["pets"]
    return HTMLResponse(views.render(name, **{**views.status_context(conn), "jump": views.jump_index(pets), **ctx}))


def _pet_or_404(request: Request, pet_id: str):
    try:
        return store.get_pet(request.app.state.conn, pet_id)
    except LookupError:
        raise HTTPException(404, "No pet by that name lives here.") from None


# ---- pages ----

@app.get("/", response_class=HTMLResponse)
async def desk(request: Request, focus: str | None = None):
    return _page(request, "desk.html", **views.desk_context(request.app.state.conn, focus))


@app.get("/pets/{pet_id}/deck", response_class=HTMLResponse)
async def deck(request: Request, pet_id: str):
    """The desk's deck for one pet, swapped in when the keeper focuses it."""
    pet = _pet_or_404(request, pet_id)
    if pet.state == "retired":
        raise HTTPException(409, f"{pet.name} is in the archive.")
    return HTMLResponse(views.render("_deck.html", **views.deck_context(request.app.state.conn, pet)))


@app.get("/hatch", response_class=HTMLResponse)
async def hatch_page(request: Request, from_: str | None = Query(None, alias="from")):
    """?from=<retired pet> prefills the soul answers from that pet's soul, and keeps its lessons."""
    cfg, conn = request.app.state.cfg, request.app.state.conn
    parent = _pet_or_404(request, from_) if from_ else None
    prefill = soul.prefill_from(store.latest_soul(conn, parent.id).content) if parent else {}
    name = f"{parent.name} Jr" if parent else views.suggest_name(conn)
    return _page(request, "hatch.html", homes=cfg.homes, models=cfg.models, shells=views.SHELLS, pet_name=name,
                 seed=parent.look_seed if parent else random.randrange(1, 99999),
                 anthropic_ready=bool(cfg.anthropic_key), parent=parent, prefill=prefill)


@app.get("/memory", response_class=HTMLResponse)
async def memory_page(request: Request):
    return _page(request, "memory.html", **views.memory_context(request.app.state.conn))


@app.get("/memory/{pet_id}/backup")
async def download_backup(request: Request, pet_id: str):
    pet = _pet_or_404(request, pet_id)
    path = Path(pet.backup_path) if pet.backup_path else None
    if pet.state != "retired" or path is None or not path.is_file():
        raise HTTPException(404, "There's no backup for that pet.")
    return FileResponse(path, media_type="application/zip", filename=path.name)


@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    cfg = request.app.state.cfg
    return _page(request, "settings.html", homes=cfg.homes, home_status=await request.app.state.fleet.home_status(),
                 notes=store.get_notification_settings(request.app.state.conn))


@app.put("/settings/notifications", response_class=HTMLResponse)
async def put_notifications(request: Request):
    """The switches post on every change. Unchecked boxes are absent; with the master switch off the
    others arrive disabled (absent too), so keep what they were."""
    conn = request.app.state.conn
    form, old = await request.form(), store.get_notification_settings(conn)
    on = form.get("enabled") == "1"
    flags = {k: form.get(k) == "1" if on else getattr(old, k)
             for k in ("calling", "sick", "finished", "out_of_budget", "quiet_enabled")}
    try:
        quiet_from = time.fromisoformat(form["quiet_from"]) if on else old.quiet_from
        quiet_to = time.fromisoformat(form["quiet_to"]) if on else old.quiet_to
    except (KeyError, ValueError):
        raise HTTPException(400, "Pick quiet hours from the lists.") from None
    notes = NotificationSettings(enabled=on, quiet_from=quiet_from, quiet_to=quiet_to,
                                 tz=(form.get("tz") or "").strip() or old.tz, **flags)
    store.put_notification_settings(conn, notes)
    return views.render("_notifications.html", notes=notes, saved=True)


@app.post("/settings/notifications/test", status_code=204)
async def test_beep(request: Request):
    if not store.get_notification_settings(request.app.state.conn).enabled:
        raise HTTPException(409, "Notifications are off. Turn them on first.")
    request.app.state.bus.publish(BusMessage(event="notify", data=json.dumps(notify.test_notice().__dict__)))
    return Response(status_code=204)


@app.get("/pets/{pet_id}", response_class=HTMLResponse)
async def pet_page(request: Request, pet_id: str):
    pet = _pet_or_404(request, pet_id)
    if pet.state == "retired":
        return RedirectResponse(f"/pets/{pet_id}/soul", status_code=303)    # all that's left is its soul
    conn = request.app.state.conn
    return _page(request, "pet.html", **views.pet_context(conn, pet), **views.rack_context(conn))


@app.get("/pets/{pet_id}/workspace", response_class=HTMLResponse)
async def workspace_page(request: Request, pet_id: str):
    pet = _pet_or_404(request, pet_id)
    if pet.brain == "fake":
        return RedirectResponse(f"/pets/{pet_id}", status_code=303)       # practice pets have no home
    conn = request.app.state.conn
    return _page(request, "workspace.html", **views.pet_context(conn, pet), **views.rack_context(conn),
                 direct_url=await request.app.state.fleet.direct_code_url(pet_id))


@app.get("/pets/{pet_id}/soul", response_class=HTMLResponse)
async def soul_page(request: Request, pet_id: str):
    pet = _pet_or_404(request, pet_id)
    conn = request.app.state.conn
    return _page(request, "soul.html", **{**views.pet_context(conn, pet), **views.soul_context(conn, pet),
                                          **views.rack_context(conn)})


# ---- actions ----

@app.post("/pets")
async def hatch(request: Request, name: str = Form(...), shell: str = Form("grape"), look_seed: int = Form(15),
                purpose_md: str = Form(""), voice_md: str = Form(""), ask_first_md: str = Form(""),
                never_md: str = Form(""), home: str = Form("python"), brain: str = Form("fake"),
                model: str = Form("local-qwen"), budget: str = Form("100k-2h"), first_task_md: str = Form(""),
                lessons_from: str = Form("")):
    if shell not in views.SHELLS:
        raise HTTPException(400, f"Pick one of the shells: {', '.join(views.SHELLS)}.")
    form = HatchForm(name=name, shell=shell, look_seed=look_seed, purpose_md=purpose_md, voice_md=voice_md,
                     ask_first_md=ask_first_md, never_md=never_md, home=home, brain=brain, model=model,
                     budget=budget, first_task_md=first_task_md or None)
    try:
        pet = await request.app.state.fleet.hatch(form, lessons_from=lessons_from or None)
    except KeyError as e:
        raise HTTPException(400, f"Unknown choice: {e}. Pick from the lists on the hatch page.") from None
    except RuntimeError as e:
        raise HTTPException(400, f"{e}.") from None
    return RedirectResponse(f"/pets/{pet.id}", status_code=303)


@app.post("/pets/{pet_id}/tasks")
async def feed(request: Request, pet_id: str, prompt_md: str = Form(...), mode: str = Form("task")):
    """Give it a new task, or (mode=chat) just talk to it."""
    _pet_or_404(request, pet_id)
    sup = _ready(request, pet_id)
    await (sup.chat(prompt_md) if mode == "chat" else sup.feed(prompt_md))
    if request.headers.get("HX-Request"):
        return Response(status_code=204)            # the desk sends it in place; the live updates follow
    return RedirectResponse(f"/pets/{pet_id}", status_code=303)


def _ready(request: Request, pet_id: str):
    """The pet's supervisor, if it can take a new turn now. A new task wakes an out-of-budget pet:
    a new task brings a new budget."""
    fleet, conn = request.app.state.fleet, request.app.state.conn
    if fleet.lights_are_off(pet_id):
        raise HTTPException(409, "It's asleep. Wake it first.")
    if not fleet.is_awake(pet_id):
        raise HTTPException(409, "It's still waking up. Try again in a moment.")
    pet = store.get_pet(conn, pet_id)
    if pet.state == "paused":
        raise HTTPException(409, "It's paused while you edit. Resume it first.")
    sup = fleet.get(pet_id)
    if sup.busy:
        raise HTTPException(409, "It's busy with a task. Wait for it, or unstick it if it's stuck.")
    if pet.state == "asleep":
        store.set_pet_state(conn, pet_id, "awake")
    return sup


@app.post("/pets/{pet_id}/lights", status_code=204)
async def lights(request: Request, pet_id: str, on: int = Form(...)):
    pet = _pet_or_404(request, pet_id)
    fleet = request.app.state.fleet
    if not fleet.lights_are_off(pet_id) and not fleet.is_awake(pet_id):
        raise HTTPException(409, "It isn't awake yet.")
    if pet.state == "paused":
        raise HTTPException(409, "It's paused while you edit. Resume it first.")
    await fleet.lights(pet_id, bool(on))
    return Response(status_code=204)


@app.post("/pets/{pet_id}/medicine", status_code=204)
async def medicine(request: Request, pet_id: str):
    pet = _pet_or_404(request, pet_id)
    fleet = request.app.state.fleet
    if fleet.lights_are_off(pet_id) or not fleet.is_awake(pet_id) or pet.state == "paused":
        raise HTTPException(409, "It can't be unstuck right now: wake it or resume it first.")
    ctx = views.pet_context(request.app.state.conn, pet)
    await fleet.get(pet_id).medicine(ctx["vitals"].sick_reason)
    return Response(status_code=204)


@app.post("/pets/{pet_id}/tidy", status_code=204)
async def tidy(request: Request, pet_id: str):
    _pet_or_404(request, pet_id)
    await _ready(request, pet_id).tidy()
    return Response(status_code=204)


@app.post("/pets/{pet_id}/retire")
async def retire(request: Request, pet_id: str, reason_md: str = Form("")):
    _pet_or_404(request, pet_id)
    try:
        await request.app.state.fleet.retire(pet_id, reason_md.strip() or None)
    except ValueError as e:
        raise HTTPException(409, f"{e}.") from None
    except (OSError, RuntimeError) as e:
        raise HTTPException(500, f"Couldn't retire it: {e}. Nothing was deleted.") from None
    return RedirectResponse("/memory", status_code=303)


@app.post("/pets/{pet_id}/pause", status_code=204)
async def pause(request: Request, pet_id: str):
    """Pause while I edit: the agent holds still, its VS Code keeps running."""
    _pet_or_404(request, pet_id)
    fleet = request.app.state.fleet
    if not fleet.is_awake(pet_id):
        raise HTTPException(409, "It isn't awake, so there's nothing to pause.")
    await fleet.pause_for_edit(pet_id)
    return Response(status_code=204)


@app.post("/pets/{pet_id}/resume", status_code=204)
async def resume(request: Request, pet_id: str):
    _pet_or_404(request, pet_id)
    fleet = request.app.state.fleet
    if not fleet.is_awake(pet_id):
        raise HTTPException(409, "It isn't awake yet. Try again in a moment.")
    await fleet.resume_after_edit(pet_id)
    return Response(status_code=204)


@app.post("/pets/{pet_id}/soul")
async def save_soul(request: Request, pet_id: str, content_md: str = Form(...), note: str = Form("")):
    _pet_or_404(request, pet_id)
    if not content_md.strip():
        raise HTTPException(400, "A soul can't be empty. Write who the pet is before saving.")
    await request.app.state.fleet.edit_soul(pet_id, content_md, note.strip() or None)
    return RedirectResponse(f"/pets/{pet_id}/soul", status_code=303)


@app.post("/pets/{pet_id}/lessons", status_code=202)
async def teach(request: Request, pet_id: str, text_md: str = Form(""), source: str = Form("teach")):
    """Teach or praise. Drafting is one model call, so it runs in the background and the proposed
    lesson arrives over SSE."""
    _pet_or_404(request, pet_id)
    if source not in ("teach", "praise"):
        raise HTTPException(400, "Teach or praise, nothing else.")
    text = text_md.strip()
    if not text and source == "praise":
        last = store.current_task(request.app.state.conn, pet_id)
        text = f"Good job on the last task ({last.prompt.strip()[:200]})." if last else "Good job."
    if not text:
        raise HTTPException(400, "Write what it should remember first.")
    request.app.state.fleet.teach(pet_id, source, text)
    return Response(status_code=202)


@app.post("/lessons/{lesson_id}", status_code=204)
async def decide_lesson(request: Request, lesson_id: int, action: str = Form(...), text: str = Form("")):
    fleet = request.app.state.fleet
    try:
        if action == "accept":
            await fleet.accept_lesson(lesson_id, text)
        elif action == "discard":
            await fleet.discard_lesson(lesson_id)
        else:
            raise HTTPException(400, "Add or discard the lesson.")
    except LookupError:
        raise HTTPException(404, "No such lesson.") from None
    except ValueError as e:
        raise HTTPException(409, f"{e}.") from None
    return Response(status_code=204)


@app.post("/markdown/preview", response_class=HTMLResponse)
async def markdown_preview(md: str = Form("")):
    return HTMLResponse(soul.md_to_html(md) or '<p class="muted">Nothing to preview yet.</p>')


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
