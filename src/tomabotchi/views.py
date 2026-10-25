"""Template rendering and the view models pages and SSE partials share.

The supervisor publishes partials over SSE, and web.py renders full pages; both build their
context here so a swapped partial always matches what a page load would show.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from markupsafe import Markup

from . import soul, store, vitals
from .models import Call, Lesson, Pet, SoulRevision, ToolRule
from .rules import rule_for_allow_always

ENV = Environment(loader=FileSystemLoader(Path(__file__).with_name("templates")),
                  autoescape=select_autoescape(["html"]))

SHELLS = ["grape", "lagoon", "tangerine", "bubblegum"]

# Latest container stats per pet, filled by Fleet's poller (docker_ops.HomeStats), read when rendering.
HOME_STATS: dict = {}
CONFIG = None                     # set by web.py at startup so views can name homes and models


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


# ---- tool rules ----

KIND_LABELS = {"read": "reading files", "search": "searching", "fetch": "reading web pages",
               "think": "thinking and planning", "edit": "editing files", "delete": "deleting files",
               "move": "moving files", "execute": "running commands", "switch_mode": "switching mode",
               "other": "other tools", "*": "any tool"}
RULE_VALUES = ("free", "ask", "never")


def rule_label(kind: str, tool: str | None, path_glob: str | None) -> str:
    what = tool or KIND_LABELS.get(kind, kind)
    if path_glob:
        where = path_glob[:-3] + "/" if path_glob.endswith("/**") else path_glob
        what += f" in {where}"
    return what


def rule_summary(rules: list[ToolRule]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {v: [] for v in RULE_VALUES}
    for r in rules:
        out[r.rule].append(rule_label(r.kind, r.tool, r.path_glob))
    return out


def always_rule(call: Call) -> dict | None:
    """The rule "Always allow" would add, or None when this call can't be always-allowed."""
    tc = call.tool_call
    if tc.get("dangerous"):
        return None
    return rule_for_allow_always(kind=tc.get("kind") or "other", tool=tc.get("tool"),
                                 paths=[loc["path"] for loc in tc.get("locations") or [] if loc.get("path")])


ENV.globals["rule_label"] = rule_label
ENV.filters["md"] = lambda text: Markup(soul.md_to_html(text))    # sanitised by nh3


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
        row["status"] = "blocked" if p.get("blocked") else p.get("status") or row["status"]
    return list(rows.values())


def pet_context(conn, pet: Pet) -> dict:
    task = store.current_task(conn, pet.id)
    calls = store.pending_calls(conn, pet.id)
    open_call = calls[0] if calls else None
    tool_evs = store.recent_events(conn, pet.id, {"tool_call", "tool_update"}, 200)
    updates = [e for e in tool_evs if e.kind == "tool_update"]
    thoughts = [e for e in store.recent_events(conn, pet.id, {"thought", "message"}, 20)
                if not task or e.task_id == task.id]       # what it thought about this task, not old ones
    now = datetime.now(timezone.utc)
    working = bool(task and task.state in ("running", "waiting"))
    usage = store.last_notice(conn, pet.id, "usage_update")
    loop = store.last_notice(conn, pet.id, "loop_guard")
    if loop and (not task or loop.task_id != task.id or not working):
        loop = None                  # a loop warning only makes a pet sick during the task it happened in
    tokens = store.task_tokens(conn, task.id) if task else 0
    v = vitals.compute(pet, task=task, open_call=open_call, recent_tool_updates=updates, tokens_used=tokens,
                       elapsed=((task.ended_at or now) - task.started_at) if task else timedelta(0),
                       loop_notice=loop, context_pct=usage.payload.get("context_pct") if usage else None,
                       discipline=store.discipline(conn, pet.id), now=now)
    if open_call:
        title = open_call.tool_call.get("title") or "Use a tool"
        doing = f"Wants your OK to {title[:1].lower()}{title[1:]}"
    elif v.sick_reason:
        doing = f"Sick: {v.sick_reason}"
    elif pet.state == "paused":
        doing = "Paused while you edit its workspace"
    elif pet.state in ("hatching", "asleep"):
        said = [e.payload["text"] for e in store.recent_events(conn, pet.id, {"notice"}, 10) if e.payload.get("text")]
        doing = said[-1] if said else ("Hatching..." if pet.state == "hatching" else "Asleep")
    elif working:
        last = next((t for t in reversed(thoughts) if t.kind == "thought"), None)
        doing = f"Thinking: “{last.payload['text']}”" if last else "Getting started"
    elif task:
        doing = "Finished its task. Hungry for another."
    else:
        doing = "Hungry for a task"
    trouble = next((e for e in reversed(store.recent_events(conn, pet.id, {"notice"}, 30))
                    if e.payload.get("type") in TROUBLE), None)
    if trouble and (last_answer := store.last_usage_at(conn, pet.id)) and last_answer > trouble.ts:
        trouble = None               # the model has answered since: whatever it was is over
    switches = [e.payload.get("type") for e in store.recent_events(conn, pet.id, {"notice"}, 30)
                if e.payload.get("type") in ("lights_off", "lights_on")]
    lights_off = pet.state == "asleep" and bool(switches) and switches[-1] == "lights_off"
    steps = store.plan(conn, task.id) if task else []
    done = sum(s.status == "completed" for s in steps)
    return {"pet": pet, "task": task, "call": open_call, "vitals": v, "doing": doing, "steps": steps,
            "thoughts": thoughts, "tools": tool_rows(tool_evs)[-12:], "done_steps": done,
            "step_now": min(done + 1, len(steps)), "day": (now - pet.born_at).days + 1,
            "working": working, "tokens": tokens, "lights_off": lights_off,
            "trouble": trouble.payload.get("text") if trouble else None, "home": home_context(pet), **rules_context(conn, pet, open_call),
            **lessons_context(conn, pet)}


TROUBLE = {"model_down", "docker_down", "home_missing", "hatch_failed", "retire_failed"}


# ---- soul ----

LESSON_KINDS = {"scold": "You scolded", "praise": "You praised", "teach": "You taught", "reflect": "{name} reflected"}
LESSON_ASKED = {"scold": "Drafted after you said no, because", "praise": "Drafted from your praise:",
                "teach": "Drafted from what you taught:", "reflect": "Drafted after looking back on a task:"}


def lessons_context(conn, pet: Pet) -> dict:
    return {"proposed": store.proposed_lessons(conn, pet.id), "lesson_asked": LESSON_ASKED}


def timeline(pet: Pet, revisions: list[SoulRevision], lessons: list[Lesson]) -> list[dict]:
    """What shaped this soul, newest first: hatching, hand edits, and every lesson (with its fate)."""
    items = []
    for r in revisions:
        if r.source == "hatched":
            items.append({"cls": "", "kind": "Hatched", "at": r.created_at,
                          "text": f"{r.note}, keeping its lessons." if r.note else "Soul written from your answers."})
        elif r.source == "edited":
            items.append({"cls": "edited", "kind": "You edited", "at": r.created_at,
                          "text": r.note or f"Rewrote the soul by hand (version {r.version})."})
    for le in lessons:
        cls = {"scold": "scolded", "praise": "praised", "teach": "taught", "reflect": "reflected"}[le.source]
        fate = {"proposed": "Lesson waiting for your OK.", "discarded": "You discarded the lesson."}.get(le.state)
        items.append({"cls": cls, "kind": LESSON_KINDS[le.source].format(name=pet.name), "at": le.created_at,
                      "quote": le.note, "text": fate, "diff": le.text if le.state == "accepted" else None})
    return sorted(items, key=lambda i: i["at"], reverse=True)


def soul_context(conn, pet: Pet) -> dict:
    revisions = store.soul_history(conn, pet.id)
    latest = revisions[0]
    return {"pet": pet, "soul": latest, "changes": len(revisions) - 1, "brain_file": soul_file_name(pet),
            "timeline": timeline(pet, revisions, store.lessons(conn, pet.id)), **lessons_context(conn, pet)}


def memory_context(conn) -> dict:
    """The memory shelf: retired pets, most recently retired first."""
    rested = []
    for pet in sorted(store.list_pets(conn, retired=True), key=lambda p: p.retired_at, reverse=True):
        home = home_context(pet)
        rested.append({"pet": pet, "stage": vitals.stage(pet.tasks_done),
                       "lived": max(1, (pet.retired_at - pet.born_at).days + 1), "home_name": home["home_name"],
                       "backup": Path(pet.backup_path).name if pet.backup_path else None})
    return {"rested": rested}


def soul_file_name(pet: Pet) -> str:
    return "CLAUDE.md" if pet.brain == "claude" else "SOUL.md"


def rules_context(conn, pet: Pet, open_call: Call | None = None, saved: bool = False) -> dict:
    rules = store.tool_rules(conn, pet.id)
    always = always_rule(open_call) if open_call else None
    return {"pet": pet, "rules": rules, "rule_summary": rule_summary(rules), "kind_labels": KIND_LABELS,
            "rules_saved": saved, "always": always,
            "always_label": ("Always allow " + rule_label(always["kind"], always["tool"], always["path_glob"]))
                            if always else None}


def _workspace_size(path: Path) -> tuple[int, int]:
    files = size = 0
    if path.is_dir():
        for p in path.rglob("*"):
            if p.is_file():
                files += 1
                size += p.stat().st_size
    return files, size


def human_bytes(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


ENV.filters["bytes"] = human_bytes


def home_context(pet: Pet) -> dict:
    cfg = CONFIG
    ctx = {"stats": HOME_STATS.get(pet.id), "home_name": pet.home, "model_label": pet.model,
           "brain_label": {"hermes": "Hermes", "claude": "Claude Code", "fake": "Practice"}.get(pet.brain, pet.brain),
           "files": 0, "size": 0}
    if cfg is not None:
        try:
            ctx["home_name"] = cfg.home(pet.home).name
            ctx["model_label"] = cfg.model(pet.model).label
        except KeyError:
            pass
        ctx["files"], ctx["size"] = _workspace_size(cfg.pet_dir(pet.id) / "workspace")
    return ctx


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
        parts.append(f"{_names(sick)} {'is stuck and needs' if len(sick) == 1 else 'are stuck and need'} medicine")
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
