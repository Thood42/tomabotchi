-- tomabotchi schema. Applied on every connect; every statement is idempotent.

CREATE TABLE IF NOT EXISTS pets (
    id             TEXT PRIMARY KEY,
    name           TEXT NOT NULL,
    shell          TEXT NOT NULL,
    look_seed      INTEGER NOT NULL,
    home           TEXT NOT NULL,
    brain          TEXT NOT NULL,
    model          TEXT NOT NULL,
    state          TEXT NOT NULL,
    born_at        TEXT NOT NULL,
    tasks_done     INTEGER NOT NULL DEFAULT 0,
    budget_tokens  INTEGER,
    budget_seconds INTEGER,
    token_hash     TEXT,
    container_id   TEXT,
    acp_session_id TEXT,
    retired_at     TEXT,
    retire_reason  TEXT,
    backup_path    TEXT,
    backup_bytes   INTEGER
);

CREATE TABLE IF NOT EXISTS tasks (
    id         INTEGER PRIMARY KEY,
    pet_id     TEXT NOT NULL REFERENCES pets(id),
    prompt     TEXT NOT NULL,
    kind       TEXT NOT NULL DEFAULT 'task',
    state      TEXT NOT NULL,
    started_at TEXT NOT NULL,
    ended_at   TEXT
);
CREATE INDEX IF NOT EXISTS tasks_pet ON tasks(pet_id, id);

CREATE TABLE IF NOT EXISTS plan_steps (
    task_id INTEGER NOT NULL REFERENCES tasks(id),
    idx     INTEGER NOT NULL,
    content TEXT NOT NULL,
    status  TEXT NOT NULL,
    PRIMARY KEY (task_id, idx)
);

CREATE TABLE IF NOT EXISTS events (
    id      INTEGER PRIMARY KEY,
    pet_id  TEXT NOT NULL REFERENCES pets(id),
    task_id INTEGER,
    ts      TEXT NOT NULL,
    kind    TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS events_pet_kind ON events(pet_id, kind, id);
CREATE INDEX IF NOT EXISTS events_pet_ts ON events(pet_id, ts);

CREATE TABLE IF NOT EXISTS calls (
    id             INTEGER PRIMARY KEY,
    pet_id         TEXT NOT NULL REFERENCES pets(id),
    task_id        INTEGER,
    acp_request_id TEXT NOT NULL,
    tool_call      TEXT NOT NULL,
    options        TEXT NOT NULL,
    asked_at       TEXT NOT NULL,
    answered_at    TEXT,
    outcome        TEXT,
    reason         TEXT
);
CREATE INDEX IF NOT EXISTS calls_open ON calls(pet_id, answered_at);

CREATE TABLE IF NOT EXISTS tool_rules (
    pet_id    TEXT NOT NULL REFERENCES pets(id),
    kind      TEXT NOT NULL,
    tool      TEXT,
    path_glob TEXT,
    rule      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS usage (
    pet_id            TEXT NOT NULL REFERENCES pets(id),
    task_id           INTEGER,
    ts                TEXT NOT NULL,
    model             TEXT NOT NULL,
    prompt_tokens     INTEGER NOT NULL,
    completion_tokens INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS soul_revisions (
    id         INTEGER PRIMARY KEY,
    pet_id     TEXT NOT NULL REFERENCES pets(id),
    version    INTEGER NOT NULL,
    content    TEXT NOT NULL,
    source     TEXT NOT NULL,
    note       TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS lessons (
    id         INTEGER PRIMARY KEY,
    pet_id     TEXT NOT NULL REFERENCES pets(id),
    text       TEXT NOT NULL,
    source     TEXT NOT NULL,
    call_id    INTEGER,
    state      TEXT NOT NULL,
    note       TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
