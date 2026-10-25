"""Fleet: hatch, look up and shut down pets, and reattach to them on boot. The only caller of docker_ops.

The practice brain ("fake") runs in-process. Hermes pets live in their own containers: hatching
builds the home image if needed, gives the pet its own network, starts it, and attaches over ACP.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import secrets
import sqlite3
from dataclasses import asdict
from datetime import date
from pathlib import Path

from websockets.exceptions import InvalidHandshake, InvalidMessage

from . import brains, lessons, soul, store, views
from .bus import Bus, BusMessage
from .config import Config
from .docker_ops import DockerOps, container_name
from .fake_agent import FakeAgent
from .models import Call, CallOutcome, Event, HatchForm, Lesson, LessonSource, Pet, ToolRule
from .rules import DEFAULT_RULES, with_rule
from .supervisor import PetSupervisor, publish_pet

log = logging.getLogger(__name__)

BUDGETS = {"100k-2h": (100_000, 7200), "25k-30m": (25_000, 1800), "none": (None, None)}
ATTACH_TRIES = 90
STATS_EVERY = 15                # seconds between container stats polls


class Fleet:
    def __init__(self, cfg: Config, conn: sqlite3.Connection, bus: Bus, docker: DockerOps | None = None) -> None:
        self.cfg, self.conn, self.bus, self.docker = cfg, conn, bus, docker
        self._sups: dict[str, PetSupervisor] = {}
        self._fakes: dict[str, FakeAgent] = {}
        self._lights_off: dict[str, str | None] = {}       # pet id → the task Lights off interrupted
        self._tasks: set[asyncio.Task] = set()
        if docker is not None:
            self._spawn(self._poll_stats())

    # ---- paths ----

    def brain_home(self, pet_id: str) -> Path:
        return self.cfg.pet_dir(pet_id) / "brain"

    def workspace(self, pet_id: str) -> Path:
        return self.cfg.pet_dir(pet_id) / "workspace"

    def _token(self, pet_id: str) -> str:
        return (self.cfg.pet_dir(pet_id) / ".token").read_text().strip()

    # ---- lifecycle ----

    async def boot(self) -> None:
        containers = {c.pet_id: c for c in await self.docker.list_pet_containers()} if self.docker else {}
        for pet in store.list_pets(self.conn):
            if pet.brain == "fake":
                # A practice pet's agent lived in the old server process, so it wakes into a new session.
                for call in store.pending_calls(self.conn, pet.id):
                    store.answer_call(self.conn, call.id, "cancelled", "server restarted", store.now())
                try:
                    await self._wake_fake(pet, secrets.token_urlsafe(24))
                except Exception:  # noqa: BLE001
                    log.exception("could not wake %s", pet.name)
                continue
            if pet.id not in containers or self.docker is None:
                self._notice(pet, "home_missing", "Its home isn't running. Hatch it again or retire it.")
                store.set_pet_state(self.conn, pet.id, "asleep")
                continue
            if containers[pet.id].paused:
                self._lights_off[pet.id] = None                 # it went to sleep before the restart
                store.set_pet_state(self.conn, pet.id, "asleep")
                continue
            if not containers[pet.id].running:
                await self.docker.start(pet.id)
            await self.docker.create_network(pet.id)      # a recreated server must rejoin the pet's network
            self._spawn(self._attach(pet, self._token(pet.id), first_task=None))

    async def hatch(self, form: HatchForm) -> Pet:
        brain = brains.get(form.brain)                 # KeyError for an unknown brain
        self.cfg.home(form.home)
        model = self.cfg.model(form.model)
        if form.brain != "fake" and self.docker is None:
            raise RuntimeError("Docker isn't reachable from tomabotchi, so only practice pets can hatch")
        if model.provider == "anthropic" and not self.cfg.anthropic_key:
            raise RuntimeError(f"{model.label} needs ANTHROPIC_API_KEY set for the tomabotchi server")
        tokens, seconds = BUDGETS.get(form.budget, BUDGETS["100k-2h"])
        token = secrets.token_urlsafe(24)
        pet = Pet(id=secrets.token_hex(4), name=form.name.strip() or "Nameless", shell=form.shell,
                  look_seed=form.look_seed, home=form.home, brain=form.brain, model=model.key,
                  state="hatching", born_at=store.now(), budget_tokens=tokens, budget_seconds=seconds)
        store.insert_pet(self.conn, pet, token_hash=hashlib.sha256(token.encode()).hexdigest())

        pet_dir = self.cfg.pet_dir(pet.id)
        self.workspace(pet.id).mkdir(parents=True, exist_ok=True)
        self.brain_home(pet.id).mkdir(parents=True, exist_ok=True)
        token_file = pet_dir / ".token"
        token_file.write_text(token)
        token_file.chmod(0o600)

        soul_md = soul.render_initial(form, born=date.today())
        store.add_soul_revision(self.conn, pet.id, soul_md, "hatched", None)
        store.replace_tool_rules(self.conn, pet.id, [ToolRule(**r) for r in DEFAULT_RULES])
        brains.write_soul(brain, self.brain_home(pet.id), soul_md)
        brains.write_rules(brain, self.brain_home(pet.id), list(DEFAULT_RULES))
        brain.write_config(self.cfg, pet, model, token, self.brain_home(pet.id))
        publish_pet(self.conn, self.bus, pet)

        first = form.first_task_md if form.first_task_md and form.first_task_md.strip() else None
        if pet.brain == "fake":
            sup = await self._wake_fake(pet, token)
            if first:
                await sup.feed(first)
        else:
            self._spawn(self._bring_up(pet, token, first))
        return store.get_pet(self.conn, pet.id)

    def get(self, pet_id: str) -> PetSupervisor:
        return self._sups[pet_id]

    async def answer(self, call_id: int, outcome: CallOutcome, reason: str | None) -> Call:
        """Answer a call. "Always allow" first adds the narrow rule, so the pet's next try runs free."""
        call = store.get_call(self.conn, call_id)
        new = views.always_rule(call) if outcome == "allow_always" else None    # None: dangerous, allowed once
        if new:
            current = [asdict(r) for r in store.tool_rules(self.conn, call.pet_id)]
            await self.set_rules(call.pet_id, [ToolRule(**r) for r in with_rule(current, new)])
        answered = await self.get(call.pet_id).answer(call_id, outcome, reason)
        if outcome == "reject_once" and reason:
            self._spawn(self.draft_lesson(call.pet_id, "scold", reason, call_id=call_id))
        return answered

    # ---- care ----

    def lights_are_off(self, pet_id: str) -> bool:
        return pet_id in self._lights_off

    async def lights(self, pet_id: str, on: bool) -> None:
        """Off: stop the turn, hang up, and docker pause the whole home. On: unpause, reconnect (petd
        kept the session), and pick up the interrupted task."""
        pet = store.get_pet(self.conn, pet_id)
        sup = self._sups.get(pet_id)
        container = self.docker is not None and pet.brain != "fake"
        if not on:
            if pet_id in self._lights_off:
                return
            task = store.current_task(self.conn, pet_id)
            interrupted = task.prompt if sup and sup.busy and task and task.kind == "task" else None
            if sup:
                await sup.cancel_turn()
                await sup.acp.close()            # a frozen petd can't answer pings, so don't leave it to time out
            if container:
                await self.docker.pause(pet_id)
            self._lights_off[pet_id] = interrupted
            store.set_pet_state(self.conn, pet_id, "asleep")
            self._notice(pet, "lights_off", "Lights off. Sleeping until you turn them on.")
            return
        if pet_id not in self._lights_off:
            return
        interrupted = self._lights_off.pop(pet_id)
        if container:
            await self.docker.unpause(pet_id)
        if sup:
            await sup.acp.connect()
            store.set_pet_state(self.conn, pet_id, "awake")
        else:
            await self._attach(pet, self._token(pet_id), first_task=None)
            sup = self._sups[pet_id]
        self._notice(pet, "lights_on", "Lights on. Awake again.")
        if interrupted:
            await sup.feed(interrupted, send=f"Continue where you left off. Your task was: {interrupted}")

    def on_budget(self, pet_id: str, reason: str) -> None:
        """The gateway refused a model call: the pet falls asleep until it's fed a new task (a new budget)."""
        pet = store.get_pet(self.conn, pet_id)
        if pet.state != "awake":
            return
        store.set_pet_state(self.conn, pet_id, "asleep")
        self._spawn(self._out_of_budget(pet, reason))

    async def _out_of_budget(self, pet: Pet, reason: str) -> None:
        sup = self._sups.get(pet.id)
        if sup:
            await sup.cancel_turn()
        store.set_pet_state(self.conn, pet.id, "asleep")
        self._notice(pet, "out_of_budget", f"Out of budget: {reason}. Feed it a new task to give it a fresh budget.")
        if sup:
            sup.notify("out_of_budget", f"{reason}. It's asleep until you give it a new task.")

    # ---- workspace ----

    async def pause_for_edit(self, pet_id: str) -> None:
        """Stop the agent (petd SIGSTOPs its process group) but not code-server, so the keeper can
        edit while the pet holds still. Unlike Lights off, which pauses the whole container."""
        await self.get(pet_id).acp.petd("pause")
        store.set_pet_state(self.conn, pet_id, "paused")
        publish_pet(self.conn, self.bus, store.get_pet(self.conn, pet_id))

    async def resume_after_edit(self, pet_id: str) -> None:
        await self.get(pet_id).acp.petd("resume")
        store.set_pet_state(self.conn, pet_id, "awake")
        publish_pet(self.conn, self.bus, store.get_pet(self.conn, pet_id))

    async def direct_code_url(self, pet_id: str) -> str | None:
        """The host-only VS Code address, when direct_code_ports is on."""
        if not (self.cfg.direct_code_ports and self.docker):
            return None
        port = await self.docker.code_port(pet_id)
        return f"http://127.0.0.1:{port}/?folder=/workspace" if port else None

    # ---- soul and lessons ----

    def teach(self, pet_id: str, source: LessonSource, text: str) -> None:
        """Draft in the background: the lesson arrives over SSE when the model has worded it."""
        self._spawn(self.draft_lesson(pet_id, source, text))

    async def draft_lesson(self, pet_id: str, source: LessonSource, text: str, call_id: int | None = None) -> Lesson:
        """Turn the keeper's words into a proposed lesson. Nothing changes until the keeper accepts it."""
        pet = store.get_pet(self.conn, pet_id)
        line = await lessons.draft(self.cfg, pet, store.latest_soul(self.conn, pet_id).content, source, text)
        lesson_id = store.add_lesson(self.conn, Lesson(id=0, pet_id=pet_id, text=line, source=source, call_id=call_id,
                                                       state="proposed", created_at=store.now(), note=text.strip()))
        self._publish_soul(pet)
        return store.get_lesson(self.conn, lesson_id)

    async def accept_lesson(self, lesson_id: int, text: str | None = None) -> Lesson:
        lesson = store.get_lesson(self.conn, lesson_id)
        if lesson.state != "proposed":
            raise ValueError("That lesson was already decided")
        lesson = store.set_lesson_state(self.conn, lesson_id, "accepted", (text or "").strip() or None)
        content = soul.add_lesson_line(store.latest_soul(self.conn, lesson.pet_id).content, lesson.text,
                                       on=date.today())
        self._save_soul(lesson.pet_id, content, "lesson", f"lesson:{lesson.id}")
        return lesson

    async def discard_lesson(self, lesson_id: int) -> Lesson:
        lesson = store.set_lesson_state(self.conn, lesson_id, "discarded")
        self._publish_soul(store.get_pet(self.conn, lesson.pet_id))
        return lesson

    async def edit_soul(self, pet_id: str, content: str, note: str | None = None) -> None:
        self._save_soul(pet_id, content.replace("\r\n", "\n").rstrip() + "\n", "edited", note)

    def _save_soul(self, pet_id: str, content: str, source: str, note: str | None) -> None:
        """A new revision, written to the brain's soul file; the pet reads it at its next task."""
        pet = store.get_pet(self.conn, pet_id)
        store.add_soul_revision(self.conn, pet_id, content, source, note)
        brains.write_soul(brains.get(pet.brain), self.brain_home(pet_id), content)
        if pet_id in self._sups:
            self._sups[pet_id].soul_changed()
        self._publish_soul(pet)

    def _publish_soul(self, pet: Pet) -> None:
        publish_pet(self.conn, self.bus, pet)
        html = views.render("_soul_doc.html", **views.soul_context(self.conn, pet))
        self.bus.publish(BusMessage(event=f"soul-{pet.id}", data=html, pet_id=pet.id))
        html = views.render("_timeline.html", **views.soul_context(self.conn, pet))
        self.bus.publish(BusMessage(event=f"timeline-{pet.id}", data=html, pet_id=pet.id))

    async def set_rules(self, pet_id: str, rules: list[ToolRule]) -> None:
        """Rules live in the DB and in the file the pet's leash reads before every tool call."""
        pet = store.get_pet(self.conn, pet_id)
        store.replace_tool_rules(self.conn, pet_id, rules)
        brains.write_rules(brains.get(pet.brain), self.brain_home(pet_id), [asdict(r) for r in rules])
        html = views.render("_rules.html", **views.rules_context(self.conn, pet))
        self.bus.publish(BusMessage(event=f"rules-{pet_id}", data=html, pet_id=pet_id))

    async def shutdown(self) -> None:
        """Close our side only: pet containers keep running and are reattached on the next boot."""
        for t in self._tasks:
            t.cancel()
        for sup in self._sups.values():
            await sup.stop()
        for agent in self._fakes.values():
            await agent.stop()

    def on_usage(self, pet_id: str, metered) -> None:
        """Called by the gateway after each model call."""
        sup = self._sups.get(pet_id)
        if sup is not None:
            sup.on_gateway_usage(metered.reasoning)

    # ---- internals ----

    async def _poll_stats(self) -> None:
        while True:
            for pet_id, sup in list(self._sups.items()):
                if sup.pet.brain == "fake":
                    continue
                try:
                    views.HOME_STATS[pet_id] = await self.docker.stats(pet_id)
                    publish_pet(self.conn, self.bus, store.get_pet(self.conn, pet_id))
                except Exception:  # noqa: BLE001 - container gone or paused; try again next round
                    log.debug("no stats for %s", pet_id, exc_info=True)
            await asyncio.sleep(STATS_EVERY)

    def _spawn(self, coro) -> None:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._done)

    def _done(self, task: asyncio.Task) -> None:
        self._tasks.discard(task)
        if not task.cancelled() and task.exception() is not None:
            log.error("background task failed", exc_info=task.exception())

    def _notice(self, pet: Pet, kind: str, text: str) -> None:
        store.append_event(self.conn, Event(id=0, pet_id=pet.id, task_id=None, ts=store.now(), kind="notice",
                                            payload={"type": kind, "text": text}))
        publish_pet(self.conn, self.bus, store.get_pet(self.conn, pet.id))

    async def _bring_up(self, pet: Pet, token: str, first_task: str | None) -> None:
        try:
            home = self.cfg.home(pet.home)
            image = await self.docker.ensure_pet_image(home, lambda text: self._notice(pet, "progress", text))
            self._notice(pet, "progress", "Giving it a home of its own")
            network = await self.docker.create_network(pet.id)
            binds = {str(self.workspace(pet.id)): "/workspace", str(self.brain_home(pet.id)): "/home/pet"}
            env = brains.get(pet.brain).container_env(pet, self.cfg.model(pet.model), token)
            container = await self.docker.run_pet(pet, image, network, token, self.cfg.limits_default, binds, env)
            store.update_pet(self.conn, pet.id, container_id=container.id)
            self._notice(pet, "progress", "Waking up")
            await self._attach(pet, token, first_task)
        except Exception as e:  # noqa: BLE001
            log.exception("hatching %s failed", pet.name)
            store.set_pet_state(self.conn, pet.id, "asleep")
            self._notice(pet, "hatch_failed", f"Hatching failed: {e}")

    def is_awake(self, pet_id: str) -> bool:
        return pet_id in self._sups

    async def _attach(self, pet: Pet, token: str, first_task: str | None) -> None:
        sup = PetSupervisor(self.cfg, self.conn, self.bus, pet, f"ws://{container_name(pet.id)}:7000", token,
                            session_mode=brains.get(pet.brain).session_mode)
        for attempt in range(ATTACH_TRIES):
            try:
                await sup.start()
                break
            except (OSError, ConnectionError, asyncio.TimeoutError, InvalidHandshake, InvalidMessage) as e:
                if attempt == ATTACH_TRIES - 1:
                    raise RuntimeError(f"{pet.name}'s home never answered: {e}") from e
                await asyncio.sleep(1)
        self._sups[pet.id] = sup
        if first_task:
            await sup.feed(first_task)

    async def _wake_fake(self, pet: Pet, token: str) -> PetSupervisor:
        agent = FakeAgent(token=token)
        url = await agent.start()
        self._fakes[pet.id] = agent
        sup = PetSupervisor(self.cfg, self.conn, self.bus, pet, url, token)
        await sup.start()
        self._sups[pet.id] = sup
        return sup
