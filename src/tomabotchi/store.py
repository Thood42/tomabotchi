"""Every SQL query, as named functions. Nothing else writes SQL.
Timestamps are stored as ISO-8601 UTC strings."""
from __future__ import annotations

import dataclasses
import json
import sqlite3
from datetime import datetime, time, timedelta, timezone

from .db import tx
from .models import (Call, CallOutcome, Event, EventKind, NotificationSettings, Pet, PetState,
                     PlanStep, SoulRevision, Task, ToolRule)


def now() -> datetime:
    return datetime.now(timezone.utc)


def _ts(d: datetime | None) -> str | None:
    return d.isoformat() if d else None


def _dt(s: str | None) -> datetime | None:
    return datetime.fromisoformat(s) if s else None


# ---- pets ----

_PET_COLS = [f.name for f in dataclasses.fields(Pet)]


def _pet(row: sqlite3.Row) -> Pet:
    d = {k: row[k] for k in _PET_COLS}
    d["born_at"] = _dt(d["born_at"])
    d["retired_at"] = _dt(d["retired_at"])
    return Pet(**d)


def insert_pet(conn, pet: Pet, token_hash: str | None = None) -> None:
    d = dataclasses.asdict(pet)
    d["born_at"] = _ts(pet.born_at)
    d["retired_at"] = _ts(pet.retired_at)
    d["token_hash"] = token_hash
    cols = ", ".join(d)
    conn.execute(f"INSERT INTO pets ({cols}) VALUES ({', '.join('?' * len(d))})", list(d.values()))


def get_pet(conn, pet_id: str) -> Pet:
    row = conn.execute("SELECT * FROM pets WHERE id = ?", (pet_id,)).fetchone()
    if row is None:
        raise LookupError(f"no pet {pet_id!r}")
    return _pet(row)


def list_pets(conn, *, retired: bool = False) -> list[Pet]:
    op = "=" if retired else "!="
    rows = conn.execute(f"SELECT * FROM pets WHERE state {op} 'retired' ORDER BY born_at", ()).fetchall()
    return [_pet(r) for r in rows]


def set_pet_state(conn, pet_id: str, state: PetState) -> None:
    conn.execute("UPDATE pets SET state = ? WHERE id = ?", (state, pet_id))


def update_pet(conn, pet_id: str, **fields) -> None:
    if not fields:
        return
    for k, v in fields.items():
        if isinstance(v, datetime):
            fields[k] = _ts(v)
    sets = ", ".join(f"{k} = ?" for k in fields)
    conn.execute(f"UPDATE pets SET {sets} WHERE id = ?", [*fields.values(), pet_id])


# ---- tasks and plan ----

def _task(row: sqlite3.Row) -> Task:
    return Task(id=row["id"], pet_id=row["pet_id"], prompt=row["prompt"], state=row["state"],
                started_at=_dt(row["started_at"]), ended_at=_dt(row["ended_at"]))


def start_task(conn, pet_id: str, prompt: str) -> Task:
    cur = conn.execute("INSERT INTO tasks (pet_id, prompt, state, started_at) VALUES (?, ?, 'running', ?)",
                       (pet_id, prompt, _ts(now())))
    return get_task(conn, cur.lastrowid)


def get_task(conn, task_id: int) -> Task:
    return _task(conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone())


def current_task(conn, pet_id: str) -> Task | None:
    row = conn.execute("SELECT * FROM tasks WHERE pet_id = ? ORDER BY id DESC LIMIT 1", (pet_id,)).fetchone()
    return _task(row) if row else None


def end_task(conn, task_id: int, state: str) -> None:
    with tx(conn):
        conn.execute("UPDATE tasks SET state = ?, ended_at = ? WHERE id = ?", (state, _ts(now()), task_id))
        if state == "done":
            conn.execute("UPDATE pets SET tasks_done = tasks_done + 1 WHERE id = "
                         "(SELECT pet_id FROM tasks WHERE id = ?)", (task_id,))


def replace_plan(conn, task_id: int, steps: list[PlanStep]) -> None:
    with tx(conn):
        conn.execute("DELETE FROM plan_steps WHERE task_id = ?", (task_id,))
        conn.executemany("INSERT INTO plan_steps (task_id, idx, content, status) VALUES (?, ?, ?, ?)",
                         [(task_id, s.idx, s.content, s.status) for s in steps])


def plan(conn, task_id: int) -> list[PlanStep]:
    rows = conn.execute("SELECT * FROM plan_steps WHERE task_id = ? ORDER BY idx", (task_id,)).fetchall()
    return [PlanStep(task_id=r["task_id"], idx=r["idx"], content=r["content"], status=r["status"]) for r in rows]


# ---- events ----

def append_event(conn, ev: Event) -> int:
    cur = conn.execute("INSERT INTO events (pet_id, task_id, ts, kind, payload) VALUES (?, ?, ?, ?, ?)",
                       (ev.pet_id, ev.task_id, _ts(ev.ts), ev.kind, json.dumps(ev.payload)))
    return cur.lastrowid


def recent_events(conn, pet_id: str, kinds: set[EventKind], limit: int) -> list[Event]:
    """Newest last."""
    marks = ", ".join("?" * len(kinds))
    rows = conn.execute(f"SELECT * FROM events WHERE pet_id = ? AND kind IN ({marks}) ORDER BY id DESC LIMIT ?",
                        (pet_id, *kinds, limit)).fetchall()
    return [Event(id=r["id"], pet_id=r["pet_id"], task_id=r["task_id"], ts=_dt(r["ts"]), kind=r["kind"],
                  payload=json.loads(r["payload"])) for r in reversed(rows)]


# ---- calls ----

def _call(row: sqlite3.Row) -> Call:
    return Call(id=row["id"], pet_id=row["pet_id"], task_id=row["task_id"],
                acp_request_id=json.loads(row["acp_request_id"]), tool_call=json.loads(row["tool_call"]),
                options=json.loads(row["options"]), asked_at=_dt(row["asked_at"]),
                answered_at=_dt(row["answered_at"]), outcome=row["outcome"], reason=row["reason"])


def open_call(conn, call: Call) -> int:
    cur = conn.execute(
        "INSERT INTO calls (pet_id, task_id, acp_request_id, tool_call, options, asked_at) VALUES (?, ?, ?, ?, ?, ?)",
        (call.pet_id, call.task_id, json.dumps(call.acp_request_id), json.dumps(call.tool_call),
         json.dumps(call.options), _ts(call.asked_at)))
    return cur.lastrowid


def get_call(conn, call_id: int) -> Call:
    row = conn.execute("SELECT * FROM calls WHERE id = ?", (call_id,)).fetchone()
    if row is None:
        raise LookupError(f"no call {call_id}")
    return _call(row)


def answer_call(conn, call_id: int, outcome: CallOutcome, reason: str | None, at: datetime) -> Call:
    conn.execute("UPDATE calls SET outcome = ?, reason = ?, answered_at = ? WHERE id = ? AND answered_at IS NULL",
                 (outcome, reason, _ts(at), call_id))
    return get_call(conn, call_id)


def pending_calls(conn, pet_id: str | None = None) -> list[Call]:
    if pet_id is None:
        rows = conn.execute("SELECT * FROM calls WHERE answered_at IS NULL ORDER BY id").fetchall()
    else:
        rows = conn.execute("SELECT * FROM calls WHERE answered_at IS NULL AND pet_id = ? ORDER BY id",
                            (pet_id,)).fetchall()
    return [_call(r) for r in rows]


def call_waits(conn, since: datetime) -> list[timedelta]:
    rows = conn.execute("SELECT asked_at, answered_at FROM calls WHERE answered_at IS NOT NULL AND asked_at >= ?",
                        (_ts(since),)).fetchall()
    return [_dt(r["answered_at"]) - _dt(r["asked_at"]) for r in rows]


# ---- tool rules ----

def tool_rules(conn, pet_id: str) -> list[ToolRule]:
    rows = conn.execute("SELECT kind, tool, path_glob, rule FROM tool_rules WHERE pet_id = ? ORDER BY rowid",
                        (pet_id,)).fetchall()
    return [ToolRule(kind=r["kind"], tool=r["tool"], path_glob=r["path_glob"], rule=r["rule"]) for r in rows]


def replace_tool_rules(conn, pet_id: str, rules: list[ToolRule]) -> None:
    with tx(conn):
        conn.execute("DELETE FROM tool_rules WHERE pet_id = ?", (pet_id,))
        conn.executemany("INSERT INTO tool_rules (pet_id, kind, tool, path_glob, rule) VALUES (?, ?, ?, ?, ?)",
                         [(pet_id, r.kind, r.tool, r.path_glob, r.rule) for r in rules])


# ---- soul ----

def add_soul_revision(conn, pet_id: str, content: str, source: str, note: str | None) -> SoulRevision:
    with tx(conn):
        version = conn.execute("SELECT COALESCE(MAX(version), 0) + 1 FROM soul_revisions WHERE pet_id = ?",
                               (pet_id,)).fetchone()[0]
        conn.execute("INSERT INTO soul_revisions (pet_id, version, content, source, note, created_at) "
                     "VALUES (?, ?, ?, ?, ?, ?)", (pet_id, version, content, source, note, _ts(now())))
    return latest_soul(conn, pet_id)


def latest_soul(conn, pet_id: str) -> SoulRevision:
    r = conn.execute("SELECT * FROM soul_revisions WHERE pet_id = ? ORDER BY version DESC LIMIT 1",
                     (pet_id,)).fetchone()
    if r is None:
        raise LookupError(f"pet {pet_id!r} has no soul yet")
    return SoulRevision(id=r["id"], pet_id=r["pet_id"], version=r["version"], content=r["content"],
                        source=r["source"], note=r["note"], created_at=_dt(r["created_at"]))


# ---- settings ----

def get_notification_settings(conn) -> NotificationSettings:
    row = conn.execute("SELECT value FROM settings WHERE key = 'notifications'").fetchone()
    if row is None:
        return NotificationSettings()
    d = json.loads(row["value"])
    d["quiet_from"] = time.fromisoformat(d["quiet_from"])
    d["quiet_to"] = time.fromisoformat(d["quiet_to"])
    return NotificationSettings(**d)


def put_notification_settings(conn, s: NotificationSettings) -> None:
    d = dataclasses.asdict(s)
    d["quiet_from"] = s.quiet_from.isoformat("minutes")
    d["quiet_to"] = s.quiet_to.isoformat("minutes")
    conn.execute("INSERT INTO settings (key, value) VALUES ('notifications', ?) "
                 "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (json.dumps(d),))
