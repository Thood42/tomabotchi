"""One PetSupervisor per awake pet: owns its ACP connection, turns ACP updates into store rows and
bus messages, and holds the calls waiting for the keeper."""
from __future__ import annotations

import asyncio
import json
import logging
import re
import sqlite3
from datetime import datetime, timezone

from . import brains, notify, store, views
from .acp import AcpConnection
from .bus import Bus, BusMessage
from .config import Config
from .models import Call, CallOutcome, Event, Pet, PlanStep
from .rules import BLOCK_MARK, drain_blocks

log = logging.getLogger(__name__)

_CHUNK_KINDS = {"agent_thought_chunk": "thought", "agent_message_chunk": "message"}
INTERNAL_TOOL_IDS = ("perm-check-",)
QUIET_UPDATES = {"available_commands_update", "session_info_update", "current_mode_update", "config_option_update"}
PUBLISH_EVERY = 0.4            # seconds between live re-renders while text streams in

_LEASH_MESSAGE = re.compile(r"^(?P<tool>\S+) \((?P<kind>\w+)\): (?P<target>[^\n]*)(?:\n---\n(?P<preview>.*))?$", re.S)
_VERBS = {"write_file": "Write", "patch": "Edit", "read_file": "Read", "terminal": "Run", "process": "Run",
          "execute_code": "Run code in", "web_search": "Search the web for", "web_extract": "Read the page"}


def normalize_permission(tool_call: dict) -> dict:
    """Make a permission request's tool call readable on the call card.

    Hermes escalations from the leash arrive as a generic execute call whose description is the
    leash's message ("<tool> (<kind>): <target>", then an optional preview); Hermes's own
    dangerous-command prompts carry the command. Anything else passes through unchanged."""
    raw = tool_call.get("rawInput") or {}
    m = _LEASH_MESSAGE.match(str(raw.get("description") or ""))
    if m:
        tool, kind, target = m["tool"], m["kind"], m["target"]
        out = {**tool_call, "title": f"{_VERBS.get(tool, tool)} {target}", "kind": kind, "tool": tool,
               "rawInput": {"content": m["preview"]} if m["preview"] else {}}
        if kind in ("read", "edit", "delete", "move", "search"):
            out["locations"] = [{"path": p.strip()} for p in target.split(",") if p.strip()]
        return out
    if raw.get("command") and tool_call.get("kind") == "execute":
        # Hermes's own dangerous-command gate: it asks every time, whatever the rules say
        return {**tool_call, "title": f"Run {raw['command']}", "tool": "terminal", "dangerous": True}
    return tool_call


def _paths(update: dict) -> list[str]:
    return [loc["path"] for loc in update.get("locations") or [] if loc.get("path")]


def translate(update: dict, *, pet_id: str, task_id: int | None, tool_index: dict[str, dict],
              now: datetime) -> list[Event]:
    """ACP session/update → events. tool_index (toolCallId → title/kind/args) is updated in place,
    so a tool_call_update can carry the tool and args its tool_call announced."""
    kind = update.get("sessionUpdate")
    ev = lambda k, payload: Event(id=0, pet_id=pet_id, task_id=task_id, ts=now, kind=k, payload=payload)  # noqa: E731
    if kind in _CHUNK_KINDS:
        text = (update.get("content") or {}).get("text", "")
        return [ev(_CHUNK_KINDS[kind], {"text": text})] if text else []
    if kind in ("tool_call", "tool_call_update") and str(update.get("toolCallId", "")).startswith(INTERNAL_TOOL_IDS):
        return []                       # Hermes's own permission-check bookkeeping, not a tool the pet used
    if kind == "tool_call":
        info = {"title": update.get("title"), "kind": update.get("kind", "other"),
                "args": update.get("rawInput"), "paths": _paths(update)}
        tool_index[update["toolCallId"]] = info
        return [ev("tool_call", {"id": update["toolCallId"], "status": update.get("status", "pending"),
                                 "tool": info["title"], **info})]
    if kind == "tool_call_update":
        info = tool_index.get(update["toolCallId"], {})
        payload = {"id": update["toolCallId"], "status": update.get("status"), "tool": info.get("title"),
                   "title": info.get("title"), "args": info.get("args")}
        if BLOCK_MARK in json.dumps([update.get("content"), update.get("rawOutput")]):
            payload["blocked"] = True              # a "never" rule stopped it
        return [ev("tool_update", payload)]
    if kind == "plan":
        return [ev("plan", {"entries": update.get("entries", [])})]
    if kind == "usage_update" and update.get("size"):
        return [ev("notice", {"type": "usage_update", "context_pct": round(100 * update.get("used", 0) / update["size"], 1)})]
    if kind in QUIET_UPDATES:
        return []
    return [ev("notice", {"type": kind, "raw": update})]


class PetSupervisor:
    def __init__(self, cfg: Config, conn: sqlite3.Connection, bus: Bus, pet: Pet, url: str, token: str,
                 session_mode: str | None = None) -> None:
        self.cfg, self.conn, self.bus, self.pet = cfg, conn, bus, pet
        self.session_mode = session_mode
        self.acp = AcpConnection(url, token, self._on_update, self._on_permission)
        self.session_id: str | None = None
        self._tools: dict[str, dict] = {}
        self._waiting: dict[int, tuple[asyncio.Future, list[dict]]] = {}
        self._turn: asyncio.Task | None = None
        self._stream: dict | None = None            # the thought/message being streamed: kind, event id, text
        self._thoughts_this_turn = 0
        self._last_publish = 0.0
        self._publish_later: asyncio.TimerHandle | None = None
        self._blocks = 0
        self._rules_file = str(brains.get(pet.brain).rules_path(cfg.pet_dir(pet.id) / "brain"))

    # ---- lifecycle ----

    async def start(self) -> None:
        await self.acp.connect()
        await self.acp.initialize()
        self.session_id = await self.acp.new_session()
        if self.session_mode:
            await self.acp.set_mode(self.session_id, self.session_mode)
        store.update_pet(self.conn, self.pet.id, acp_session_id=self.session_id, state="awake")
        self._refresh()

    async def stop(self) -> None:
        if self._turn:
            self._turn.cancel()
        await self.acp.close()

    # ---- keeper actions ----

    async def feed(self, prompt_md: str) -> int:
        task = store.start_task(self.conn, self.pet.id, prompt_md)
        self._stream, self._thoughts_this_turn = None, 0
        self._turn = asyncio.create_task(self._run_turn(task.id, prompt_md))
        self._refresh()
        return task.id

    async def answer(self, call_id: int, outcome: CallOutcome, reason: str | None) -> Call:
        call = store.answer_call(self.conn, call_id, outcome, reason, store.now())
        fut, options = self._waiting.pop(call_id, (None, []))
        if fut is not None and not fut.done():
            # "Always" lives in tomabotchi's rules (the fleet adds one), so the brain is told "once"
            # and keeps no allowlist of its own that the rules editor couldn't see or undo.
            fut.set_result(_permission_result("allow_once" if outcome == "allow_always" else outcome, options))
        self._refresh()
        return call

    # ---- ACP callbacks ----

    async def _run_turn(self, task_id: int, text: str) -> None:
        state = "failed"
        try:
            res = await self.acp.prompt(self.session_id, text)
            state = {"end_turn": "done", "cancelled": "cancelled"}.get(res.get("stopReason"), "done")
        except asyncio.CancelledError:
            state = "cancelled"
        except Exception:  # noqa: BLE001
            log.exception("turn failed for %s", self.pet.name)
        finally:
            self._stream = None
            self._take_blocks(task_id)
            store.end_task(self.conn, task_id, state)
            self.pet = store.get_pet(self.conn, self.pet.id)
            self._refresh()
            if state == "done":
                self._notify("finished", "Its task is done. It's ready for another.")

    async def _on_update(self, params: dict) -> None:
        update = params.get("update") or {}
        task = store.current_task(self.conn, self.pet.id)
        task_id = task.id if task else None
        kind = update.get("sessionUpdate")
        if kind in _CHUNK_KINDS:
            self._stream_chunk(_CHUNK_KINDS[kind], (update.get("content") or {}).get("text", ""), task_id)
            return
        self._stream = None
        before_sick = views.pet_context(self.conn, self.pet)["vitals"].sick_reason
        self._take_blocks(task_id)
        for ev in translate(update, pet_id=self.pet.id, task_id=task_id, tool_index=self._tools, now=store.now()):
            store.append_event(self.conn, ev)
            if ev.kind == "plan" and task_id is not None:
                store.replace_plan(self.conn, task_id, [
                    PlanStep(task_id=task_id, idx=i, content=e.get("content", ""), status=e.get("status", "pending"))
                    for i, e in enumerate(ev.payload["entries"])])
        self._refresh()
        after = views.pet_context(self.conn, self.pet)["vitals"].sick_reason
        if after and not before_sick:
            self._notify("sick", after)

    def _take_blocks(self, task_id: int | None) -> None:
        """Calls a "never" rule blocked never reach ACP; the leash notes them in a file instead."""
        for b in drain_blocks(self._rules_file):
            self._blocks += 1
            base = {"id": f"blocked-{store.now().timestamp()}-{self._blocks}", "status": "failed",
                    "title": f"{b.get('tool')}: {b.get('target')}", "blocked": True}
            for kind in ("tool_call", "tool_update"):
                store.append_event(self.conn, Event(id=0, pet_id=self.pet.id, task_id=task_id, ts=store.now(),
                                                    kind=kind, payload={**base, "kind": b.get("kind")}))

    async def _on_permission(self, req_id, params: dict) -> dict:
        task = store.current_task(self.conn, self.pet.id)
        call = Call(id=0, pet_id=self.pet.id, task_id=task.id if task else None, acp_request_id=req_id,
                    tool_call=normalize_permission(params.get("toolCall") or {}), options=params.get("options") or [],
                    asked_at=store.now())
        call_id = store.open_call(self.conn, call)
        fut = asyncio.get_running_loop().create_future()
        self._waiting[call_id] = (fut, call.options)
        self._refresh()
        self._notify("calling", f"Wants your OK to {call.tool_call.get('title', 'use a tool')}")
        return await fut

    # ---- streamed text ----

    def _stream_chunk(self, kind: str, text: str, task_id: int | None) -> None:
        """Brains stream thoughts and messages a few tokens at a time. Consecutive chunks of one kind
        grow a single event, re-rendered at most every PUBLISH_EVERY seconds."""
        if not text:
            return
        if kind == "thought":
            self._thoughts_this_turn += 1
        s = self._stream
        if s and s["kind"] == kind:
            s["text"] += text
            store.update_event_payload(self.conn, s["id"], {"text": s["text"]})
        else:
            ev_id = store.append_event(self.conn, Event(id=0, pet_id=self.pet.id, task_id=task_id, ts=store.now(),
                                                        kind=kind, payload={"text": text}))
            self._stream = {"kind": kind, "id": ev_id, "text": text}
        self._refresh_soon()

    def on_gateway_usage(self, reasoning: str | None) -> None:
        """The gateway saw the model's reasoning. If the brain didn't stream it as thoughts, show it."""
        if reasoning and reasoning.strip() and self._thoughts_this_turn == 0:
            task = store.current_task(self.conn, self.pet.id)
            store.append_event(self.conn, Event(id=0, pet_id=self.pet.id, task_id=task.id if task else None,
                                                ts=store.now(), kind="thought", payload={"text": reasoning.strip()}))
            self._refresh_soon()

    # ---- publishing ----

    def _refresh_soon(self) -> None:
        loop = asyncio.get_running_loop()
        wait = PUBLISH_EVERY - (loop.time() - self._last_publish)
        if wait <= 0:
            self._refresh()
        elif self._publish_later is None:
            self._publish_later = loop.call_later(wait, self._refresh)

    def _refresh(self) -> None:
        if self._publish_later is not None:
            self._publish_later.cancel()
            self._publish_later = None
        try:
            self._last_publish = asyncio.get_running_loop().time()
        except RuntimeError:
            pass
        self.pet = store.get_pet(self.conn, self.pet.id)
        publish_pet(self.conn, self.bus, self.pet)

    def _notify(self, kind: notify.NoticeKind, detail: str) -> None:
        settings = store.get_notification_settings(self.conn)
        notice = notify.for_event(kind, self.pet, detail, settings, datetime.now().time())
        if notice:
            self.bus.publish(BusMessage(event="notify", data=json.dumps(notice.__dict__), pet_id=self.pet.id))


def _permission_result(outcome: CallOutcome, options: list[dict]) -> dict:
    if outcome in ("timeout", "cancelled"):
        return {"outcome": {"outcome": "cancelled"}}
    wanted = [outcome] + (["allow_once"] if outcome == "allow_always" else [])
    for kind in wanted:
        for opt in options:
            if opt.get("kind") == kind:
                return {"outcome": {"outcome": "selected", "optionId": opt["optionId"]}}
    reject = next((o for o in options if o.get("kind", "").startswith("reject")), None)
    if outcome == "reject_once" and reject:
        return {"outcome": {"outcome": "selected", "optionId": reject["optionId"]}}
    return {"outcome": {"outcome": "cancelled"}}


PARTIALS = ("_slot.html", "_bigegg.html", "_call.html", "_steps.html", "_thoughts.html", "_tools.html", "_vitals.html",
            "_home.html")


def publish_pet(conn, bus: Bus, pet: Pet) -> None:
    """Re-render every live partial for this pet, plus the shelf header. Each partial's root
    element id is its SSE event name, so the browser swaps it in place."""
    ctx = views.pet_context(conn, pet)
    for name in PARTIALS:
        html = views.render(name, **ctx)
        bus.publish(BusMessage(event=f"{name[1:-5]}-{pet.id}", data=html, pet_id=pet.id))
    bus.publish(BusMessage(event="shelf-head", data=views.render("_shelf_head.html", **views.shelf_context(conn))))
