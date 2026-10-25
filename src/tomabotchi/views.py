"""Template rendering and the view models pages and SSE partials share.

The supervisor publishes partials over SSE, and web.py renders full pages; both build their
context here so a swapped partial always matches what a page load would show.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from . import store, vitals
from .models import Pet

ENV = Environment(loader=FileSystemLoader(Path(__file__).with_name("templates")),
                  autoescape=select_autoescape(["html"]))

SHELLS = ["grape", "lagoon", "tangerine", "bubblegum"]


def human_wait(d: timedelta | None) -> str:
    if d is None:
        return "No calls yet"
    s = int(d.total_seconds())
    return f"{s // 60}m {s % 60:02d}s" if s >= 60 else f"{s}s"


def _ago(ts: datetime) -> str:
    return ts.astimezone().strftime("%H:%M")


ENV.filters["hm"] = _ago
ENV.globals["human_wait"] = human_wait


def render(name: str, **ctx) -> str:
    return ENV.get_template(name).render(**ctx)


def tool_rows(events) -> list[dict]:
    """Merge tool_call and tool_update events into one row per tool call, in call order."""
    rows: dict[str, dict] = {}
    for ev in events:
        p = ev.payload
        row = rows.setdefault(p["id"], {"id": p["id"], "title": p.get("title") or p.get("tool") or "Tool",
                                        "kind": p.get("kind"), "detail": "", "status": "pending"})
        if ev.kind == "tool_call":
            row.update(title=p.get("title") or row["title"], kind=p.get("kind"),
                       detail=", ".join(p.get("paths") or []))
        row["status"] = p.get("status") or row["status"]
    return list(rows.values())


def pet_context(conn, pet: Pet) -> dict:
    task = store.current_task(conn, pet.id)
    calls = store.pending_calls(conn, pet.id)
    open_call = calls[0] if calls else None
    tool_evs = store.recent_events(conn, pet.id, {"tool_call", "tool_update"}, 200)
    updates = [e for e in tool_evs if e.kind == "tool_update"]
    thoughts = store.recent_events(conn, pet.id, {"thought", "message"}, 20)
    now = datetime.now(timezone.utc)
    v = vitals.compute(pet, task=task, open_call=open_call, recent_tool_updates=updates, tokens_used=0,
                       elapsed=(now - task.started_at) if task else timedelta(0), last_notice=None,
                       discipline=(0, 0), now=now)
    if open_call:
        title = open_call.tool_call.get("title") or "Use a tool"
        doing = f"Wants your OK to {title[:1].lower()}{title[1:]}"
    elif v.sick_reason:
        doing = f"Sick: {v.sick_reason}"
    elif pet.state == "hatching":
        doing = "Hatching..."
    elif task and task.state in ("running", "waiting"):
        last = next((t for t in reversed(thoughts) if t.kind == "thought"), None)
        doing = f"Thinking: “{last.payload['text']}”" if last else "Getting started"
    elif task:
        doing = "Finished its task. Hungry for another."
    else:
        doing = "Hungry for a task"
    steps = store.plan(conn, task.id) if task else []
    done = sum(s.status == "completed" for s in steps)
    return {"pet": pet, "task": task, "call": open_call, "vitals": v, "doing": doing, "steps": steps,
            "thoughts": thoughts, "tools": tool_rows(tool_evs)[-12:], "done_steps": done,
            "step_now": min(done + 1, len(steps)), "day": (now - pet.born_at).days + 1,
            "working": bool(task and task.state in ("running", "waiting"))}


def _names(ctxs: list[dict]) -> str:
    names = [c["pet"].name for c in ctxs]
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def summary(pets: list[dict]) -> str:
    if not pets:
        return "Nobody lives here yet. Hatch your first pet."
    calling = [c for c in pets if c["call"]]
    sick = [c for c in pets if not c["call"] and c["vitals"].sick_reason]
    working = [c for c in pets if c["working"] and not c["call"] and not c["vitals"].sick_reason]
    parts = []
    if working:
        parts.append(f"{len(working)} {'is' if len(working) == 1 else 'are'} working")
    if calling:
        parts.append(f"{_names(calling)} {'is' if len(calling) == 1 else 'are'} waiting for your answer")
    if sick:
        parts.append(f"{_names(sick)} {'is' if len(sick) == 1 else 'are'} stuck and needs medicine")
    if not parts:
        return "Everyone is idle and hungry for a task."
    text = parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + ", and " + parts[-1]
    return text[0].upper() + text[1:] + "."


def shelf_context(conn) -> dict:
    pets = [pet_context(conn, p) for p in store.list_pets(conn)]
    week = store.call_waits(conn, datetime.now(timezone.utc) - timedelta(days=7))
    calling = [c for c in pets if c["call"]]
    return {"pets": pets, "calling": calling, "median_wait": vitals.median_wait(week), "summary": summary(pets),
            "retired_count": len(store.list_pets(conn, retired=True))}
