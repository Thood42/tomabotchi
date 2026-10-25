"""Dataclasses and literal types shared by every module. No I/O."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from typing import Literal

BrainName = Literal["hermes", "claude", "fake"]
TaskKind = Literal["task", "chat", "care"]      # only tasks count towards growing up
PetState = Literal["hatching", "awake", "paused", "asleep", "retired"]
Scene = Literal["egg", "alert", "work", "happy", "sleep", "sick"]
Stage = Literal["egg", "baby", "kid", "teen", "adult"]
Rule = Literal["free", "ask", "never"]
CallOutcome = Literal["allow_once", "allow_always", "reject_once", "timeout", "cancelled"]
EventKind = Literal["thought", "message", "tool_call", "tool_update", "plan", "notice", "state"]
LessonSource = Literal["scold", "praise", "teach", "reflect"]


@dataclass(frozen=True)
class Home:
    name: str
    slug: str
    image: str
    description: str
    default: bool
    build: str | None = None     # a Dockerfile folder under platform/tomabotchi, built as `image`


@dataclass(frozen=True)
class ModelChoice:
    key: str
    label: str
    provider: Literal["ollama", "anthropic"]
    model: str


@dataclass(frozen=True)
class Limits:
    memory: str
    cpus: float
    pids: int


@dataclass
class Pet:
    id: str
    name: str
    shell: str
    look_seed: int
    home: str
    brain: BrainName
    model: str
    state: PetState
    born_at: datetime
    tasks_done: int = 0
    budget_tokens: int | None = None
    budget_seconds: int | None = None
    container_id: str | None = None
    acp_session_id: str | None = None
    retired_at: datetime | None = None
    retire_reason: str | None = None
    backup_path: str | None = None
    backup_bytes: int | None = None


@dataclass
class Task:
    id: int
    pet_id: str
    prompt: str
    state: Literal["running", "waiting", "done", "failed", "cancelled"]
    started_at: datetime
    ended_at: datetime | None = None
    kind: TaskKind = "task"


@dataclass
class PlanStep:
    task_id: int
    idx: int
    content: str
    status: Literal["pending", "in_progress", "completed"]


@dataclass
class Event:
    id: int
    pet_id: str
    task_id: int | None
    ts: datetime
    kind: EventKind
    payload: dict


@dataclass
class Call:
    id: int
    pet_id: str
    task_id: int | None
    acp_request_id: str | int
    tool_call: dict
    options: list[dict]
    asked_at: datetime
    answered_at: datetime | None = None
    outcome: CallOutcome | None = None
    reason: str | None = None


@dataclass(frozen=True)
class ToolRule:
    kind: str
    tool: str | None
    path_glob: str | None
    rule: Rule


@dataclass
class Usage:
    pet_id: str
    task_id: int | None
    ts: datetime
    model: str
    prompt_tokens: int
    completion_tokens: int


@dataclass
class SoulRevision:
    id: int
    pet_id: str
    version: int
    content: str
    source: Literal["hatched", "edited", "lesson"]
    note: str | None
    created_at: datetime


@dataclass
class Lesson:
    id: int
    pet_id: str
    text: str
    source: LessonSource
    call_id: int | None
    state: Literal["proposed", "accepted", "discarded"]
    created_at: datetime
    note: str | None = None          # what prompted it, in the keeper's words


@dataclass
class Hearts:
    hunger: int
    happy: int
    energy: int
    health: int


@dataclass
class Vitals:
    hearts: Hearts
    scene: Scene
    stage: Stage
    energy_pct: float
    mess_pct: float | None
    discipline: tuple[int, int]
    sick_reason: str | None


@dataclass
class NotificationSettings:
    enabled: bool = True
    calling: bool = True
    sick: bool = True
    finished: bool = False
    out_of_budget: bool = True
    quiet_enabled: bool = False
    quiet_from: time = time(22, 0)
    quiet_to: time = time(7, 0)
    tz: str | None = None            # the keeper's timezone (from their browser), for quiet hours


@dataclass
class HatchForm:
    name: str
    shell: str
    look_seed: int
    purpose_md: str
    voice_md: str
    ask_first_md: str
    never_md: str
    home: str
    brain: BrainName
    model: str
    budget: str
    first_task_md: str | None
