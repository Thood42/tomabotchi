"""Should this happening beep the keeper's browser? Pure."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import time
from typing import Literal

from .models import NotificationSettings, Pet

NoticeKind = Literal["calling", "sick", "finished", "out_of_budget"]

_TITLES = {
    "calling": "{name} is calling you",
    "sick": "{name} is sick",
    "finished": "{name} finished its task",
    "out_of_budget": "{name} ran out of budget",
}


@dataclass(frozen=True)
class Notice:
    title: str
    body: str
    pet_id: str
    url: str
    tag: str


def in_quiet_hours(now: time, start: time, end: time) -> bool:
    if start == end:
        return False
    if start < end:
        return start <= now < end
    return now >= start or now < end      # wraps past midnight


def for_event(kind: NoticeKind, pet: Pet, detail: str, settings: NotificationSettings, now: time) -> Notice | None:
    if not settings.enabled or not getattr(settings, kind):
        return None
    if settings.quiet_enabled and in_quiet_hours(now, settings.quiet_from, settings.quiet_to):
        return None
    return Notice(title=_TITLES[kind].format(name=pet.name), body=detail, pet_id=pet.id,
                  url=f"/pets/{pet.id}", tag=f"{kind}-{pet.id}")
