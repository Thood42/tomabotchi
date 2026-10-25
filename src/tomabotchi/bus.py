"""In-process pub/sub feeding the SSE streams."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import AsyncIterator


@dataclass(frozen=True)
class BusMessage:
    event: str              # SSE event name: the id of the element to replace, or "notify"
    data: str               # HTML partial, or JSON for "notify"
    pet_id: str | None = None


class Bus:
    def __init__(self, maxsize: int = 500) -> None:
        self._maxsize = maxsize
        self._subs: set[tuple[str | None, asyncio.Queue[BusMessage]]] = set()

    def publish(self, msg: BusMessage) -> None:
        for pet_id, q in list(self._subs):
            if pet_id is not None and msg.pet_id not in (None, pet_id):
                continue
            if q.full():                     # slow client: drop its oldest message
                q.get_nowait()
            q.put_nowait(msg)

    async def subscribe(self, pet_id: str | None = None) -> AsyncIterator[BusMessage]:
        sub = (pet_id, asyncio.Queue(self._maxsize))
        self._subs.add(sub)
        try:
            while True:
                yield await sub[1].get()
        finally:
            self._subs.discard(sub)

    @property
    def subscribers(self) -> int:
        return len(self._subs)
