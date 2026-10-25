"""A pet's recent work as blocks on a track: what the desk draws in each pet's lane. Pure data, no I/O."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from .models import Call, Event, Task

BlockKind = Literal["said", "think", "tool", "failed", "call", "done"]
WINDOW = timedelta(minutes=60)          # widest zoom; the browser shows 5, 15 or 60 minutes of it
MAX_EVENTS = 2000                       # per pet per render
EVENT_KINDS = {"thought", "message", "tool_call", "tool_update", "notice"}
LOOP_AT = 3                             # identical failures in a row that make a loop


@dataclass(frozen=True)
class Block:
    start: datetime
    end: datetime | None                # None: still going (an open call, or what it's doing right now)
    kind: BlockKind
    label: str                          # one line for the hover tip, e.g. "Write file notes/pgvector.md"


@dataclass(frozen=True)
class Bracket:
    start: datetime
    end: datetime
    label: str                          # e.g. "same pytest call failed 4 times"


@dataclass(frozen=True)
class Track:
    blocks: list[Block]
    loop: Bracket | None


def _clip(text: str, n: int = 90) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[: n - 1] + "…"


def _tool_label(call: Event) -> str:
    p = call.payload
    title = p.get("title") or p.get("tool") or "Tool"
    path = next(iter(p.get("paths") or []), None)
    return f"{title} {path}" if path and path not in title else title


def track(events: list[Event], calls: list[Call], ended: list[Task], *, streak: list[Event],
          loop_notice: Event | None, working: bool, now: datetime) -> Track:
    """Blocks in start order.
    - A thought or message runs until the next event, or stays open (end None) when it's the last and the pet is
      working; otherwise it ends at its own ts.
    - A tool runs from its tool_call to its last tool_update. It's 'failed' if that update failed or was blocked, and
      open if it has no final update while the pet is working.
    - A call runs from asked_at to answered_at, or stays open.
    - A finished task is a 'done' marker at ended_at (start == end).
    - The loop bracket spans the streak (first to last event) when it has 3 or more events, else the work leading up to
      the loop_notice (its task's first event in view, to the notice), else None."""
    events = sorted(events, key=lambda e: e.id)
    blocks: list[Block] = []
    tools: dict[str, list[Event]] = {}
    for i, ev in enumerate(events):
        nxt = events[i + 1].ts if i + 1 < len(events) else None
        if ev.kind in ("thought", "message"):
            text = _clip(ev.payload.get("text", ""))
            end = nxt if nxt else (None if working else ev.ts)
            blocks.append(Block(ev.ts, end, "think" if ev.kind == "thought" else "said",
                                text if ev.kind == "thought" else f"Said: {text}"))
        elif ev.kind == "tool_call":
            tools[ev.payload["id"]] = [ev]
        elif ev.kind == "tool_update" and ev.payload.get("id") in tools:
            tools[ev.payload["id"]].append(ev)
    for group in tools.values():
        first, last = group[0], group[-1]
        final = last if last.kind == "tool_update" and last.payload.get("status") in ("completed", "failed") else None
        failed = bool(final and (final.payload.get("status") == "failed" or final.payload.get("blocked")))
        blocked = any(e.payload.get("blocked") for e in group)
        end = final.ts if final else (None if working else last.ts)
        label = _tool_label(first) + (" (blocked by a rule)" if blocked else " (failed)" if failed else "")
        blocks.append(Block(first.ts, end, "failed" if failed or blocked else "tool", label))
    for call in calls:
        title = call.tool_call.get("title") or "use a tool"
        blocks.append(Block(call.asked_at, call.answered_at, "call",
                            f"Asked to {title[:1].lower()}{title[1:]}" + ("" if call.answered_at else ", waiting")))
    for task in ended:
        if task.state == "done" and task.kind == "task" and task.ended_at:
            blocks.append(Block(task.ended_at, task.ended_at, "done", f"Finished: {_clip(task.prompt, 60)}"))
    blocks.sort(key=lambda b: b.start)

    loop = None
    if len(streak) >= LOOP_AT:
        loop = Bracket(streak[0].ts, streak[-1].ts,
                       f"same {_clip(streak[0].payload.get('tool') or 'tool', 22)} call failed {len(streak)} times")
    elif loop_notice is not None:
        started = [e.ts for e in events if e.task_id == loop_notice.task_id]
        loop = Bracket(min(started, default=loop_notice.ts), loop_notice.ts,
                       _clip(loop_notice.payload.get("text") or "stuck in a loop", 40))
    return Track(blocks, loop)
