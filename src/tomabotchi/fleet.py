"""Fleet: hatch, look up and shut down pets, and reattach to them on boot.

Slice 1 knows one brain, "fake": a scripted agent running in-process. Docker-backed Hermes
and Claude Code pets arrive in slices 2 and 5.
"""
from __future__ import annotations

import hashlib
import logging
import secrets
import sqlite3
from datetime import date

from . import soul, store
from .bus import Bus
from .config import Config
from .fake_agent import FakeAgent
from .models import HatchForm, Pet, ToolRule
from .rules import DEFAULT_RULES
from .supervisor import PetSupervisor, publish_pet

log = logging.getLogger(__name__)

BUDGETS = {"100k-2h": (100_000, 7200), "25k-30m": (25_000, 1800), "none": (None, None)}


class Fleet:
    def __init__(self, cfg: Config, conn: sqlite3.Connection, bus: Bus) -> None:
        self.cfg, self.conn, self.bus = cfg, conn, bus
        self._sups: dict[str, PetSupervisor] = {}
        self._fakes: dict[str, FakeAgent] = {}

    async def boot(self) -> None:
        for pet in store.list_pets(self.conn):
            # A practice pet's agent lived in the old server process, so it wakes into a new session.
            # Calls it left unanswered can no longer reach it.
            for call in store.pending_calls(self.conn, pet.id):
                store.answer_call(self.conn, call.id, "cancelled", "server restarted", store.now())
            try:
                await self._wake(pet)
            except Exception:  # noqa: BLE001
                log.exception("could not wake %s", pet.name)

    async def hatch(self, form: HatchForm) -> Pet:
        tokens, seconds = BUDGETS.get(form.budget, BUDGETS["100k-2h"])
        token = secrets.token_urlsafe(24)
        pet = Pet(id=secrets.token_hex(4), name=form.name.strip() or "Nameless", shell=form.shell,
                  look_seed=form.look_seed, home=form.home, brain=form.brain, model=form.model,
                  state="hatching", born_at=store.now(), budget_tokens=tokens, budget_seconds=seconds)
        store.insert_pet(self.conn, pet, token_hash=hashlib.sha256(token.encode()).hexdigest())
        store.add_soul_revision(self.conn, pet.id, soul.render_initial(form, born=date.today()), "hatched", None)
        store.replace_tool_rules(self.conn, pet.id, [ToolRule(**r) for r in DEFAULT_RULES])
        publish_pet(self.conn, self.bus, pet)
        sup = await self._wake(pet, token)
        if form.first_task_md and form.first_task_md.strip():
            await sup.feed(form.first_task_md)
        return store.get_pet(self.conn, pet.id)

    def get(self, pet_id: str) -> PetSupervisor:
        return self._sups[pet_id]

    async def shutdown(self) -> None:
        for sup in self._sups.values():
            await sup.stop()
        for agent in self._fakes.values():
            await agent.stop()

    async def _wake(self, pet: Pet, token: str | None = None) -> PetSupervisor:
        if pet.brain != "fake":
            raise NotImplementedError(f"the {pet.brain} brain arrives in a later slice")
        token = token or secrets.token_urlsafe(24)
        agent = FakeAgent(token=token)
        url = await agent.start()
        self._fakes[pet.id] = agent
        sup = PetSupervisor(self.cfg, self.conn, self.bus, pet, url, token)
        await sup.start()
        self._sups[pet.id] = sup
        return sup
