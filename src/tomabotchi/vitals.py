"""Hearts, the LCD scene and the shelf metric, from store data. Pure functions."""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from statistics import median

from .models import Call, Event, Hearts, Pet, Scene, Stage, Task, Vitals

SICK_AFTER = 3


def stage(tasks_done: int) -> Stage:
    if tasks_done <= 0:
        return "egg"
    if tasks_done <= 2:
        return "baby"
    if tasks_done <= 9:
        return "kid"
    if tasks_done <= 24:
        return "teen"
    return "adult"


def _hearts_from_fraction(f: float) -> int:
    """0..1 → 0..4 hearts, rounding half up so 41% left shows 2."""
    return max(0, min(4, int(f * 4 + 0.5)))


def failed_streak(recent_tool_updates: list[Event]) -> list[Event]:
    """The trailing run of failures of one identical tool call (same tool and args), oldest first. Empty if the
    last finished update didn't fail."""
    streak, key = [], None
    for ev in reversed(recent_tool_updates):
        if ev.payload.get("status") != "failed":
            break
        k = (ev.payload.get("tool"), json.dumps(ev.payload.get("args"), sort_keys=True))
        if key is None:
            key = k
        if k != key:
            break
        streak.append(ev)
    return streak[::-1]


def sick_reason(recent_tool_updates: list[Event], loop_notice: Event | None) -> str | None:
    if loop_notice is not None:
        return loop_notice.payload.get("text") or "Stuck in a loop"
    streak = failed_streak(recent_tool_updates)
    if len(streak) >= SICK_AFTER:
        return f"The same {streak[0].payload.get('tool') or 'tool'} call has failed {len(streak)} times in a row"
    return None


def scene(pet: Pet, *, open_call: Call | None, sick_reason: str | None, task: Task | None) -> Scene:
    if pet.state == "hatching":
        return "egg"
    if open_call is not None:
        return "alert"
    if sick_reason:
        return "sick"
    if pet.state in ("paused", "asleep"):
        return "sleep"
    if task is not None and task.state in ("running", "waiting"):
        return "work"
    return "happy"


def compute(pet: Pet, *, task: Task | None, open_call: Call | None, recent_tool_updates: list[Event],
            tokens_used: int, elapsed: timedelta, loop_notice: Event | None, context_pct: float | None,
            discipline: tuple[int, int], now: datetime) -> Vitals:
    working = task is not None and task.state in ("running", "waiting")
    finished = [e for e in recent_tool_updates if e.payload.get("status") in ("completed", "failed")][-10:]
    happy_f = (sum(e.payload["status"] == "completed" for e in finished) / len(finished)) if finished else 1.0
    fractions = []
    if pet.budget_tokens:
        fractions.append(1 - tokens_used / pet.budget_tokens)
    if pet.budget_seconds:
        fractions.append(1 - elapsed.total_seconds() / pet.budget_seconds)
    energy_f = max(0.0, min(fractions)) if fractions else 1.0
    reason = sick_reason(recent_tool_updates, loop_notice)
    return Vitals(
        hearts=Hearts(hunger=4 if working else 1, happy=_hearts_from_fraction(happy_f),
                      energy=_hearts_from_fraction(energy_f), health=1 if reason else 4),
        scene=scene(pet, open_call=open_call, sick_reason=reason, task=task),
        stage=stage(pet.tasks_done),
        energy_pct=round(energy_f * 100, 1),
        mess_pct=context_pct,
        discipline=discipline,
        sick_reason=reason,
    )


def median_wait(waits: list[timedelta]) -> timedelta | None:
    return timedelta(seconds=median(w.total_seconds() for w in waits)) if waits else None
