"""Template rendering and the view models pages and SSE partials share.

The supervisor publishes partials over SSE, and web.py renders full pages; both build their
context here so a swapped partial always matches what a page load would show.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from markupsafe import Markup

from . import soul, store, vitals
from . import timeline as tracks
from .models import Call, Lesson, Pet, SoulRevision, ToolRule
from .rules import rule_for_allow_always

ENV = Environment(loader=FileSystemLoader(Path(__file__).with_name("templates")),
                  autoescape=select_autoescape(["html"]))

SHELLS = ["grape", "lagoon", "tangerine", "bubblegum"]

# Latest container stats per pet, filled by Fleet's poller (docker_ops.HomeStats), read when rendering.
HOME_STATS: dict = {}
# Workspace (file count, bytes) per pet, measured by Fleet's poller. Renders read it and never touch the disk.
WORKSPACE_STATS: dict[str, tuple[int, int]] = {}
CONFIG = None                     # set by web.py at startup so views can name homes and models


def human_wait(d: timedelta | None) -> str:
    if d is None:
        return "No calls yet"
    s = int(d.total_seconds())
    return f"{s // 60}m {s % 60:02d}s" if s >= 60 else f"{s}s"


def _ago(ts: datetime) -> str:
    return ts.astimezone().strftime("%H:%M")


ENV.filters["hm"] = _ago
ENV.filters["ms"] = lambda ts: int(ts.timestamp() * 1000)         # epoch milliseconds, for the timeline's data-t0 / data-t1
ENV.globals["human_wait"] = human_wait


def _asset_version() -> str:
    """A hash of everything under static/, appended to asset URLs so they can be cached forever."""
    digest = hashlib.sha1()
    root = Path(__file__).with_name("static")
    for path in sorted(root.rglob("*")):
        if path.is_file():
            digest.update(path.relative_to(root).as_posix().encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()[:10]


ASSET_VERSION = _asset_version()
ENV.globals["asset"] = lambda path: f"/static/{path}?v={ASSET_VERSION}"


def render(name: str, **ctx) -> str:
    return ENV.get_template(name).render(**ctx)


ENV.globals["partial"] = lambda name, ctx: Markup(render(name, **ctx))     # a partial rendered with another page's context


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


def stream_rows(events, open_call: Call | None, limit: int = 60) -> list[dict]:
    """Thoughts, messages and tool calls on one spine, oldest first, one row per tool call. The open call's tool
    reads 'needs you'. Keys: kind (think, said, tool, wait), at, text | title, detail, status."""
    rows: list[dict] = []
    tools: dict[str, dict] = {}
    for ev in events:
        p = ev.payload
        if ev.kind in ("thought", "message"):
            rows.append({"kind": "think" if ev.kind == "thought" else "said", "at": ev.ts, "text": p.get("text", "")})
        elif ev.kind == "tool_call":
            row = {"kind": "tool", "at": ev.ts, "title": p.get("title") or p.get("tool") or "Tool",
                   "detail": ", ".join(p.get("paths") or []), "status": p.get("status") or "pending"}
            tools[p["id"]] = row
            rows.append(row)
        elif ev.kind == "tool_update" and p.get("id") in tools:
            tools[p["id"]]["status"] = "blocked" if p.get("blocked") else p.get("status") or tools[p["id"]]["status"]
    if open_call:
        row = tools.get(open_call.tool_call.get("toolCallId"))
        if row is None:
            row = {"kind": "tool", "at": open_call.asked_at, "title": open_call.tool_call.get("title") or "Use a tool",
                   "detail": "", "status": "pending"}
            rows.append(row)
        row["status"] = "needs you"
    return rows[-limit:]


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
    stream = stream_rows([e for e in store.recent_events(conn, pet.id, {"thought", "message", "tool_call", "tool_update"}, 300)
                          if not task or e.task_id == task.id], open_call)
    track = track_context(conn, pet, updates, loop, working=working, now=now)
    return {"pet": pet, "track": track, "meters": module_meters(v), "led": led_state(pet, open_call, v, working), "task": task, "call": open_call, "vitals": v, "doing": doing, "steps": steps,
            "thoughts": thoughts, "tools": tool_rows(tool_evs)[-12:], "stream": stream, "done_steps": done,
            "step_now": min(done + 1, len(steps)), "day": (now - pet.born_at).days + 1,
            "working": working, "tokens": tokens, "lights_off": lights_off,
            "trouble": trouble.payload.get("text") if trouble else None, "home": home_context(pet), **rules_context(conn, pet, open_call),
            **lessons_context(conn, pet)}


def track_context(conn, pet: Pet, updates, loop_notice, *, working: bool, now: datetime) -> tracks.Track:
    """The pet's last hour as blocks. `updates` are its recent tool updates, for the loop bracket."""
    since = now - tracks.WINDOW
    return tracks.track(store.events_since(conn, pet.id, since, tracks.EVENT_KINDS, tracks.MAX_EVENTS),
                        store.calls_since(conn, pet.id, since), store.tasks_ended_since(conn, pet.id, since),
                        streak=vitals.failed_streak(updates), loop_notice=loop_notice, working=working, now=now)


def _segments(filled: int, warn_from: int | None = None, bad: bool = False) -> list[str]:
    """Five segments of a meter: 'f' filled, 'w' filled and warming, 'x' filled and bad, '' empty."""
    return [("x" if bad else "w" if warn_from is not None and i >= warn_from else "f") if i < filled else "" for i in range(5)]


def module_meters(v) -> list[dict]:
    """The three meters on a pet's module: budget left, context used, health."""
    r = v.readout
    budget = 5 if r.budget_left_pct is None else round(r.budget_left_pct / 20)
    context = 0 if r.context_pct is None else round(r.context_pct / 20)
    return [{"label": "Budget left", "segs": _segments(budget, warn_from=None if budget > 1 else 0),
             "text": f"{r.budget_left_pct:.0f}%" if r.budget_left_pct is not None else "no limit"},
            {"label": "Context used", "segs": _segments(context, warn_from=4),
             "text": f"{r.context_pct:.0f}%" if r.context_pct is not None else "unknown"},
            {"label": "Health", "segs": _segments(1 if v.sick_reason else 5, bad=bool(v.sick_reason)),
             "text": v.sick_reason or "no errors or loops"}]


def led_state(pet: Pet, call: Call | None, v, working: bool) -> str:
    if call:
        return "ring"
    if v.sick_reason:
        return "bad"
    return "go" if working else ""


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
        rested.append({"pet": pet,
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
            "always_scope": rule_label(always["kind"], always["tool"], always["path_glob"]) if always else None,
            "always_label": ("Always allow " + rule_label(always["kind"], always["tool"], always["path_glob"]))
                            if always else None}


def workspace_size(path: Path) -> tuple[int, int]:
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
           "workspace": WORKSPACE_STATS.get(pet.id)}
    if cfg is not None:
        try:
            ctx["home_name"] = cfg.home(pet.home).name
            ctx["model_label"] = cfg.model(pet.model).label
        except KeyError:
            pass
    return ctx


def status_context(conn) -> dict:
    """The top bar's "N calling" key: how many calls wait, and the pet that has waited longest."""
    calls = store.pending_calls(conn)
    owner = None
    for call in calls:
        pet = store.get_pet(conn, call.pet_id)
        if pet.state != "retired":
            owner = owner or pet
    return {"calling": len(calls), "next_pet": owner}


def desk_context(conn, focus: str | None) -> dict:
    """The desk: every pet, the focused one (the pet asked for, else the one waiting longest, else the first)."""
    pets = [pet_context(conn, p) for p in store.list_pets(conn)]
    status = status_context(conn)
    by_id = {c["pet"].id: c for c in pets}
    chosen = by_id.get(focus) or (by_id.get(status["next_pet"].id) if status["next_pet"] else None) or (pets[0] if pets else None)
    week = store.call_waits(conn, datetime.now(timezone.utc) - timedelta(days=7))
    return {"pets": pets, "focus": chosen, "median_wait": vitals.median_wait(week),
            "retired_count": len(store.list_pets(conn, retired=True)), **status}


def rack_context(conn) -> dict:
    """Every pet, for the console's rack."""
    return {"pets": [pet_context(conn, p) for p in store.list_pets(conn)]}


def deck_context(conn, pet: Pet) -> dict:
    return pet_context(conn, pet)
