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
from datetime import date
from pathlib import Path

from websockets.exceptions import InvalidHandshake, InvalidMessage

from . import brains, soul, store
from .bus import Bus
from .config import Config
from .docker_ops import DockerOps, container_name
from .fake_agent import FakeAgent
from .models import Event, HatchForm, Pet, ToolRule
from .rules import DEFAULT_RULES
from .supervisor import PetSupervisor, publish_pet

log = logging.getLogger(__name__)

BUDGETS = {"100k-2h": (100_000, 7200), "25k-30m": (25_000, 1800), "none": (None, None)}
ATTACH_TRIES = 90


class Fleet:
    def __init__(self, cfg: Config, conn: sqlite3.Connection, bus: Bus, docker: DockerOps | None = None) -> None:
        self.cfg, self.conn, self.bus, self.docker = cfg, conn, bus, docker
        self._sups: dict[str, PetSupervisor] = {}
        self._fakes: dict[str, FakeAgent] = {}
        self._tasks: set[asyncio.Task] = set()

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
            if not containers[pet.id].running:
                await self.docker.start(pet.id)
            self._spawn(self._attach(pet, self._token(pet.id), first_task=None))

    async def hatch(self, form: HatchForm) -> Pet:
        brain = brains.get(form.brain)                 # KeyError for an unknown brain
        self.cfg.home(form.home)
        model = self.cfg.model(form.model)
        if form.brain != "fake" and self.docker is None:
            raise RuntimeError("Docker isn't reachable from tomabotchi, so only practice pets can hatch")
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

    async def shutdown(self) -> None:
        """Close our side only: pet containers keep running and are reattached on the next boot."""
        for t in self._tasks:
            t.cancel()
        for sup in self._sups.values():
            await sup.stop()
        for agent in self._fakes.values():
            await agent.stop()

    # ---- internals ----

    def _spawn(self, coro) -> None:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

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
            container = await self.docker.run_pet(pet, image, network, token, self.cfg.limits_default, binds)
            store.update_pet(self.conn, pet.id, container_id=container.id)
            self._notice(pet, "progress", "Waking up")
            await self._attach(pet, token, first_task)
        except Exception as e:  # noqa: BLE001
            log.exception("hatching %s failed", pet.name)
            store.set_pet_state(self.conn, pet.id, "asleep")
            self._notice(pet, "hatch_failed", f"Hatching failed: {e}")

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
