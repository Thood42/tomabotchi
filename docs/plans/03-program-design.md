# Program Design: tomabotchi

All paths are relative to the repo root `~/Projects/ai`.

## Files

### Server package: `platform/tomabotchi/`
| File | Why it lives there |
|---|---|
| `pyproject.toml` | A uv project like `platform/hub`. Deps: fastapi, uvicorn, jinja2, python-multipart, httpx, websockets, docker, markdown-it-py, nh3. Dev deps: pytest, pytest-asyncio |
| `Dockerfile` | The server image: python:3.12-slim plus a uv install. It runs as uid 1000 with the docker group added |
| `src/tomabotchi/__init__.py` | Package marker and `__version__` (also used as the runtime version tag) |
| `src/tomabotchi/config.py` | Loads env vars, `platform/tomabotchi.toml` and `platform/containment.toml` into typed config. This is the only place paths are worked out |
| `src/tomabotchi/schema.sql` | DDL for every table in 02-architecture § Data |
| `src/tomabotchi/db.py` | SQLite connection (WAL), applies the schema, `tx()` context manager |
| `src/tomabotchi/models.py` | Dataclasses and literal types shared by every module. No I/O |
| `src/tomabotchi/store.py` | Every SQL query, as named functions. Nothing else writes SQL |
| `src/tomabotchi/rules.py` | Matching a tool call against tool rules (pure, stdlib only). The runtime build **copies this file** into the leash plugin, so the server and the pet share one implementation |
| `src/tomabotchi/vitals.py` | Turns store data into hearts, the LCD scene and the shelf metric (pure) |
| `src/tomabotchi/soul.py` | Renders SOUL.md from hatch answers, applies lessons, renders Markdown to sanitised HTML |
| `src/tomabotchi/notify.py` | Decides whether an event should trigger a browser notification, given settings and quiet hours (pure) |
| `src/tomabotchi/bus.py` | In-process pub/sub feeding the SSE streams |
| `src/tomabotchi/acp.py` | ACP JSON-RPC client over a WebSocket: requests, notifications, and incoming agent→client requests |
| `src/tomabotchi/supervisor.py` | One `PetSupervisor` per awake pet. It owns the ACP connection, turns ACP updates into store rows and bus events, and holds pending calls |
| `src/tomabotchi/fleet.py` | `Fleet`: a registry of supervisors, plus hatch, retire and reconnect-on-boot. It is the only caller of `docker_ops` |
| `src/tomabotchi/brains.py` | The `Brain` adapter plus `HermesBrain` and `ClaudeBrain`. This is the one place that knows each brain's config files, soul file, ACP command and gateway route. Adding a third ACP brain later means one more class here |
| `src/tomabotchi/docker_ops.py` | A thin async wrapper over the docker SDK: images, networks, containers, pause, stats. Nothing else imports `docker` |
| `src/tomabotchi/archive.py` | Zips a pet's directory into `archive/`, verifies the zip, removes the source |
| `src/tomabotchi/gateway.py` | The model gateway router. It passes requests through to Ollama (OpenAI-compatible) or Anthropic (Messages), with usage accounting, budget enforcement and reasoning capture |
| `src/tomabotchi/codeproxy.py` | HTTP and WebSocket reverse proxy to a pet's code-server |
| `src/tomabotchi/lessons.py` | Drafts a SOUL.md lesson line from a reason, praise or teach text, using one model call through the gateway |
| `src/tomabotchi/web.py` | FastAPI app: pages, actions, SSE, markdown preview, and the guard that rejects requests from pet subnets. It mounts the gateway and codeproxy routers |
| `src/tomabotchi/cli.py` | `tomabotchi serve` (uvicorn) and `tomabotchi build-runtime` |
| `src/tomabotchi/templates/base.html` | Layout, top bar, htmx and htmx-ext-sse loaded from unpkg (pinned, as aihub does), kit.css |
| `src/tomabotchi/templates/{shelf,pet,workspace,soul,hatch,memory,settings}.html` | One template per approved mockup screen |
| `src/tomabotchi/templates/_*.html` | Partials swapped over SSE: `_egg.html`, `_plate.html`, `_call.html`, `_thoughts.html`, `_tools.html`, `_steps.html`, `_vitals.html`, `_home.html`, `_rules.html`, `_lesson.html`, `_md_field.html` |
| `src/tomabotchi/static/kit.css`, `kit.js` | Ported from the mockups. kit.js keeps the creature generator unchanged and gains `renderScene()` for SSE-driven scene changes |
| `src/tomabotchi/static/app.js` | Notification permission flow, `notify` SSE handling, the hatch shuffle, and the Markdown Write/Preview toggle |
| `tests/…` | See Test plan |

### Pet runtime: `platform/tomabotchi/runtime/`
| File | Why it lives there |
|---|---|
| `Dockerfile` | Builds `tomabotchi-runtime:<ver>`. It installs:<br>• standalone Python into `/opt/tomabotchi/python`, with `hermes-agent[acp]`, websockets and the leash package<br>• standalone Node 22 into `/opt/tomabotchi/node`, with `@zed-industries/claude-agent-acp` (pinned)<br>• the code-server tarball into `/opt/tomabotchi/code-server`<br>• petd and `rules.py` |
| `pet.Dockerfile.tmpl` | The `FROM {home}` + `COPY --from` template that `docker_ops.ensure_pet_image` fills in |
| `petd/petd.py` | In-container supervisor: starts code-server and `hermes acp`, bridges ACP on :7000, serves the leash on 127.0.0.1:7001, buffers while disconnected |
| `leash/tomabotchi_leash/__init__.py` | Hermes plugin. Registers the `pre_tool_call` hook |
| `leash/tomabotchi_leash/kinds.py` | Maps each brain's tool names to ACP kinds and paths:<br>• Hermes: `write_file` and `patch` → edit, `terminal` → execute<br>• Claude: `Write`, `Edit` and `MultiEdit` → edit, `Bash` → execute, `Read` → read, `Grep` and `Glob` → search, `WebFetch` and `WebSearch` → fetch, `TodoWrite` → think, `Task` and `mcp__*` → other |
| `leash/tomabotchi_leash/claude_hook.py` | Claude Code `PreToolUse` command hook. It reads the hook JSON from stdin, decides with the shared rules, asks petd if needed, and prints `hookSpecificOutput` |
| `leash/plugin.yaml` | Hermes plugin manifest |
| `bin/petd` | Shell shim that runs `/opt/tomabotchi/python/bin/python -m petd` |
| `smoke.sh` | Run during the image build: checks glibc, starts petd for 5 s, confirms code-server and hermes respond |

### Changed or new files elsewhere in the platform
| File | Change |
|---|---|
| `platform/tomabotchi.toml` | **New.** Homes list, models, server, gateway and network-pool settings |
| `platform/containment.toml` | **Changed.** Adds a `[tomabotchi]` table: `max_awake_pets`, plus optional per-pet overrides of `[defaults]` |
| `platform/stack/tomabotchi.compose.yml` | **New.** The server service: `network_mode: bridge`, `127.0.0.1:8770:8770`, docker.sock, and the data dir bind-mounted **at the same absolute path** so pet bind mounts resolve on the host |
| `platform/bin/stack` | **Changed.** Adds `-f tomabotchi.compose.yml` to `dc()` |
| `.gitignore` | **Changed.** Adds `platform/data/` and `platform/tomabotchi/.venv/` |
| `README.md` | **Changed.** Adds a tomabotchi row to the services table and a short "Daily use" note |

## Types & signatures

### `models.py`
```python
BrainName = Literal["hermes", "claude"]
PetState  = Literal["hatching", "awake", "paused", "asleep", "retired"]
Scene     = Literal["egg", "alert", "work", "happy", "sleep", "sick"]   # matches kit.js scenes
Stage     = Literal["egg", "baby", "kid", "teen", "adult"]
Rule      = Literal["free", "ask", "never"]
ToolKind  = Literal["read", "edit", "delete", "move", "search", "execute", "think", "fetch", "switch_mode", "other"]
CallOutcome = Literal["allow_once", "allow_always", "reject_once", "timeout", "cancelled"]
EventKind = Literal["thought", "message", "tool_call", "tool_update", "plan", "notice", "state"]
LessonSource = Literal["scold", "praise", "teach", "reflect"]

@dataclass(frozen=True)
class Home:            name: str; slug: str; image: str; description: str; default: bool
@dataclass(frozen=True)
class ModelChoice:     key: str; label: str; provider: Literal["ollama", "anthropic"]; model: str
@dataclass(frozen=True)
class Limits:          memory: str; cpus: float; pids: int

@dataclass
class Pet:
    id: str; name: str; shell: str; look_seed: int; home: str; brain: BrainName; model: str
    state: PetState; born_at: datetime; tasks_done: int
    budget_tokens: int | None; budget_seconds: int | None
    container_id: str | None; acp_session_id: str | None
    retired_at: datetime | None = None; retire_reason: str | None = None
    backup_path: str | None = None; backup_bytes: int | None = None

@dataclass
class Task:      id: int; pet_id: str; prompt: str; state: Literal["running", "waiting", "done", "failed", "cancelled"]; started_at: datetime; ended_at: datetime | None
@dataclass
class PlanStep:  task_id: int; idx: int; content: str; status: Literal["pending", "in_progress", "completed"]
@dataclass
class Event:     id: int; pet_id: str; task_id: int | None; ts: datetime; kind: EventKind; payload: dict
@dataclass
class Call:
    id: int; pet_id: str; task_id: int | None; acp_request_id: str | int
    tool_call: dict; options: list[dict]; asked_at: datetime
    answered_at: datetime | None = None; outcome: CallOutcome | None = None; reason: str | None = None
@dataclass(frozen=True)
class ToolRule:  kind: ToolKind | Literal["*"]; tool: str | None; path_glob: str | None; rule: Rule
@dataclass
class Usage:     pet_id: str; task_id: int | None; ts: datetime; model: str; prompt_tokens: int; completion_tokens: int
@dataclass
class SoulRevision: id: int; pet_id: str; version: int; content: str; source: Literal["hatched", "edited", "lesson"]; note: str | None; created_at: datetime
@dataclass
class Lesson:    id: int; pet_id: str; text: str; source: LessonSource; call_id: int | None; state: Literal["proposed", "accepted", "discarded"]; created_at: datetime

@dataclass
class Hearts:    hunger: int; happy: int; energy: int; health: int           # each 0..4
@dataclass
class Vitals:
    hearts: Hearts; scene: Scene; stage: Stage
    energy_pct: float; mess_pct: float | None; discipline: tuple[int, int]   # (asked, asked + blocked)
    sick_reason: str | None

@dataclass
class NotificationSettings:
    enabled: bool; calling: bool; sick: bool; finished: bool; out_of_budget: bool
    quiet_enabled: bool; quiet_from: time; quiet_to: time

@dataclass
class HatchForm:
    name: str; shell: str; look_seed: int
    purpose_md: str; voice_md: str; ask_first_md: str; never_md: str
    home: str; brain: BrainName; model: str; budget: str; first_task_md: str | None
```

### `config.py`
```python
@dataclass(frozen=True)
class Config:
    data_dir: Path                 # TOMABOTCHI_DATA_DIR; identical path on host and in the server container
    config_file: Path              # TOMABOTCHI_CONFIG
    port: int                      # TOMABOTCHI_PORT, default 8770
    ollama_url: str                # OLLAMA_BASE_URL
    anthropic_key: str | None      # ANTHROPIC_API_KEY
    homes: list[Home]; models: list[ModelChoice]
    limits_default: Limits; limits_ceiling: Limits; max_awake_pets: int
    pet_subnet_pool: IPv4Network   # 172.30.0.0/16, carved into /28s
    direct_code_ports: bool
    runtime_version: str
    approval_timeout: timedelta    # how long a call waits for you before the leash gives up (default 8h)
    @property
    def db_path(self) -> Path: ...
    def pet_dir(self, pet_id: str) -> Path: ...
    def archive_dir(self) -> Path: ...
    def home(self, slug: str) -> Home: ...                      # KeyError on unknown slug
    def default_home(self) -> Home: ...

def load() -> Config: ...
def clamp(limits: Limits, ceiling: Limits) -> Limits: ...
```

### `db.py` / `store.py`
```python
def connect(path: Path) -> sqlite3.Connection: ...          # WAL, foreign keys, row factory, applies schema.sql
@contextmanager
def tx(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]: ...

# store.py: every function takes conn first
def insert_pet(conn, pet: Pet) -> None: ...
def get_pet(conn, pet_id: str) -> Pet: ...                   # LookupError if missing
def list_pets(conn, *, retired: bool = False) -> list[Pet]: ...
def set_pet_state(conn, pet_id: str, state: PetState) -> None: ...
def update_pet(conn, pet_id: str, **fields) -> None: ...

def start_task(conn, pet_id: str, prompt: str) -> Task: ...
def current_task(conn, pet_id: str) -> Task | None: ...
def end_task(conn, task_id: int, state: str) -> None: ...
def replace_plan(conn, task_id: int, steps: list[PlanStep]) -> None: ...
def plan(conn, task_id: int) -> list[PlanStep]: ...

def append_event(conn, ev: Event) -> int: ...
def recent_events(conn, pet_id: str, kinds: set[EventKind], limit: int) -> list[Event]: ...

def open_call(conn, call: Call) -> int: ...
def answer_call(conn, call_id: int, outcome: CallOutcome, reason: str | None, at: datetime) -> Call: ...
def pending_calls(conn, pet_id: str | None = None) -> list[Call]: ...
def call_waits(conn, since: datetime) -> list[timedelta]: ...

def tool_rules(conn, pet_id: str) -> list[ToolRule]: ...
def replace_tool_rules(conn, pet_id: str, rules: list[ToolRule]) -> None: ...
def add_tool_rule(conn, pet_id: str, rule: ToolRule) -> None: ...

def add_usage(conn, u: Usage) -> None: ...
def task_tokens(conn, task_id: int) -> int: ...

def add_soul_revision(conn, pet_id: str, content: str, source: str, note: str | None) -> SoulRevision: ...
def latest_soul(conn, pet_id: str) -> SoulRevision: ...
def soul_history(conn, pet_id: str) -> list[SoulRevision]: ...
def add_lesson(conn, lesson: Lesson) -> int: ...
def set_lesson_state(conn, lesson_id: int, state: str, text: str | None = None) -> Lesson: ...
def proposed_lessons(conn, pet_id: str) -> list[Lesson]: ...

def get_notification_settings(conn) -> NotificationSettings: ...
def put_notification_settings(conn, s: NotificationSettings) -> None: ...
```

### `rules.py` (stdlib only, shared with the leash)
```python
DEFAULT_RULES: tuple[ToolRuleDict, ...]   # free: read, search, fetch, think; ask: edit, delete, move, execute, other, switch_mode

class ToolRuleDict(TypedDict):
    kind: str; tool: str | None; path_glob: str | None; rule: Literal["free", "ask", "never"]

def decide(rules: Sequence[ToolRuleDict], *, kind: str, tool: str, paths: Sequence[str]) -> Literal["free", "ask", "never"]: ...
    # Most specific match wins (tool+path > tool > kind+path > kind > "*").
    # Among equally specific matches, "never" beats "ask" beats "free".
    # A call touching several paths takes the strictest result.
def rule_for_allow_always(*, kind: str, tool: str, paths: Sequence[str]) -> ToolRuleDict: ...
    # Narrowest useful rule: the tool plus the common parent dir of paths (e.g. "notes/**"), or the tool alone
def load_rules_file(path: str) -> list[ToolRuleDict]: ...          # used by the leash; mtime-cached
def dump_rules_file(path: str, rules: Sequence[ToolRuleDict]) -> None: ...   # atomic write
```

### `vitals.py`
```python
def compute(pet: Pet, *, task: Task | None, open_call: Call | None, recent_tool_updates: list[Event],
            tokens_used: int, elapsed: timedelta, last_notice: Event | None,
            discipline: tuple[int, int], now: datetime) -> Vitals: ...
def scene(pet: Pet, *, open_call: Call | None, sick_reason: str | None, task: Task | None) -> Scene: ...
    # priority: hatching→egg > open call→alert > sick > paused|asleep→sleep > running task→work > happy
def sick_reason(recent_tool_updates: list[Event], last_notice: Event | None) -> str | None: ...
    # same tool+args failed >= 3 times in a row, or a tool-loop guardrail notice
def stage(tasks_done: int) -> Stage: ...      # 0 egg, 1–2 baby, 3–9 kid, 10–24 teen, 25+ adult
def median_wait(waits: list[timedelta]) -> timedelta | None: ...
```

### `soul.py`
```python
def render_initial(form: HatchForm, *, born: date) -> str: ...
    # Sections: # {name}, ## Who I am, ## How I talk, ## Ask first, ## Never, ## Lessons, then a fixed
    # "## Working with my keeper" block (keep a todo list of steps, ask before anything my rules mark "ask")
def add_lesson_line(soul_md: str, lesson: str, *, on: date) -> str: ...     # appends under ## Lessons, creating it if missing
def write_soul_file(cfg: Config, pet: Pet, content: str) -> Path: ...       # atomic write via brains.get(pet.brain).soul_path
def md_to_html(md: str) -> str: ...                                          # markdown-it-py (commonmark + tables), nh3 sanitise
```

### `notify.py`
```python
@dataclass(frozen=True)
class Notice: title: str; body: str; pet_id: str; url: str; tag: str
def for_event(kind: Literal["calling", "sick", "finished", "out_of_budget"], pet: Pet, detail: str,
              settings: NotificationSettings, now: time) -> Notice | None: ...
def in_quiet_hours(now: time, start: time, end: time) -> bool: ...          # handles wrap past midnight
```

### `bus.py`
```python
@dataclass(frozen=True)
class BusMessage: event: str; data: str; pet_id: str | None     # event = SSE event name; data = HTML partial or JSON
class Bus:
    def publish(self, msg: BusMessage) -> None: ...
    def subscribe(self, pet_id: str | None = None) -> AsyncIterator[BusMessage]: ...    # bounded queue; slow clients drop oldest
```

### `acp.py`
```python
class AcpError(Exception): code: int; message: str
UpdateHandler     = Callable[[dict], Awaitable[None]]                  # params.update of session/update
PermissionHandler = Callable[[str | int, dict], Awaitable[dict]]       # (request id, params) -> result {"outcome": ...}

class AcpConnection:
    def __init__(self, url: str, token: str, on_update: UpdateHandler, on_permission: PermissionHandler) -> None: ...
    async def connect(self) -> None: ...                                # opens the ws and starts the reader task
    async def initialize(self) -> dict: ...                             # protocolVersion 1, clientCapabilities fs/terminal false
    async def new_session(self, cwd: str = "/workspace") -> str: ...
    async def prompt(self, session_id: str, text: str) -> dict: ...     # resolves when the turn ends (stopReason)
    async def cancel(self, session_id: str) -> None: ...                # notification
    async def close(self) -> None: ...
    @property
    def connected(self) -> bool: ...
```

### `supervisor.py`
```python
class PetSupervisor:
    def __init__(self, cfg: Config, conn: sqlite3.Connection, bus: Bus, pet: Pet, acp: AcpConnection) -> None: ...
    async def start(self) -> None: ...                         # connect, initialize, new or reuse session
    async def feed(self, prompt_md: str) -> Task: ...          # start_task + prompt in the background; ends the task on stopReason
    async def chat(self, text_md: str) -> None: ...
    async def lights(self, on: bool) -> None: ...              # cancel + docker pause / unpause + "Continue"
    async def medicine(self) -> None: ...                      # cancel + nudge prompt built from sick_reason
    async def tidy(self) -> None: ...                          # prompt "/compress"
    async def answer(self, call_id: int, outcome: CallOutcome, reason: str | None) -> Call: ...
    async def stop(self) -> None: ...
    # internal
    async def _on_update(self, update: dict) -> None: ...      # dispatch on update["sessionUpdate"]
    async def _on_permission(self, req_id: str | int, params: dict) -> dict: ...   # opens a call; awaits a Future
    def _translate(self, update: dict) -> list[Event]: ...     # pure: ACP update → events (+ plan steps); unknown kinds become notice
    def _publish_pet(self) -> None: ...                        # recompute vitals, publish _egg/_plate/_vitals partials
```

### `fleet.py`
```python
class Fleet:
    def __init__(self, cfg: Config, conn: sqlite3.Connection, bus: Bus, docker: DockerOps) -> None: ...
    async def boot(self) -> None: ...                           # reattach to labelled containers, rebuild supervisors
    async def hatch(self, form: HatchForm) -> Pet: ...
    def get(self, pet_id: str) -> PetSupervisor: ...            # KeyError if the pet isn't awake
    async def set_rules(self, pet_id: str, rules: list[ToolRule]) -> None: ...   # DB + brain.rules_path() file
    async def retire(self, pet_id: str, reason: str | None) -> Pet: ...
    async def shutdown(self) -> None: ...                       # closes sockets only; containers keep running

```

### `brains.py`
```python
class Brain(Protocol):
    name: BrainName
    label: str                                        # "Hermes", "Claude Code"
    def acp_command(self) -> list[str]: ...           # what petd runs (passed as BRAIN env; petd owns the actual argv)
    def soul_path(self, brain_home: Path) -> Path: ...
    def rules_path(self, brain_home: Path) -> Path: ...    # where the leash reads tomabotchi_rules.json
    def render_soul_file(self, soul_md: str) -> str: ...   # Claude wraps the soul in a short CLAUDE.md preamble
    def write_config(self, cfg: Config, pet: Pet, model: ModelChoice, token: str, brain_home: Path) -> list[Path]: ...

class HermesBrain:
    # .hermes/config.yaml:
    #   ollama model    → provider custom, base_url {gw}/llm/{token}/v1
    #   anthropic model → provider anthropic, base_url {gw}/llm/{token}/anthropic
    #   approvals.mode "off"; plugins.enabled [tomabotchi_leash]; platform_toolsets.acp default
    # soul at .hermes/SOUL.md; rules at .hermes/tomabotchi_rules.json
    ...

class ClaudeBrain:
    # .claude/settings.json:
    #   env.ANTHROPIC_BASE_URL = {gw}/llm/{token}/anthropic; env.ANTHROPIC_AUTH_TOKEN = token
    #   env.ANTHROPIC_MODEL = model.model (local models use their Ollama name or an `ollama cp` alias)
    #   hooks.PreToolUse = [{"matcher": ".*", "hooks": [{"type": "command",
    #       "command": "/opt/tomabotchi/bin/leash-claude", "timeout": cfg.approval_timeout seconds}]}]
    #   permissions.defaultMode "default"   (never bypassPermissions; see 02 § the leash)
    # soul at .claude/CLAUDE.md; rules at .claude/tomabotchi_rules.json
    ...

BRAINS: dict[BrainName, Brain]
def get(name: BrainName) -> Brain: ...                # KeyError on unknown brain
```

### `docker_ops.py`
```python
@dataclass(frozen=True)
class PetContainer: id: str; pet_id: str; network: str; ip: str; running: bool; paused: bool
@dataclass(frozen=True)
class HomeStats: mem_bytes: int; mem_limit: int; cpu_pct: float

class DockerOps:
    def __init__(self, cfg: Config) -> None: ...
    async def ensure_runtime(self, progress: Callable[[str], None]) -> str: ...          # builds tomabotchi-runtime:<ver> if missing
    async def ensure_pet_image(self, home: Home, progress: Callable[[str], None]) -> str: ...
    async def create_network(self, pet_id: str) -> str: ...      # tbx-pet-<id>, next free /28 from the pool; connects the server
    async def run_pet(self, pet: Pet, image: str, network: str, token: str, limits: Limits) -> PetContainer: ...
    async def pause(self, container_id: str) -> None: ...
    async def unpause(self, container_id: str) -> None: ...
    async def stats(self, container_id: str) -> HomeStats: ...
    async def remove_pet(self, pet_id: str) -> None: ...         # container + network; idempotent
    async def list_pet_containers(self) -> list[PetContainer]: ...   # label tomabotchi.pet
```

### `archive.py`
```python
@dataclass(frozen=True)
class Backup: path: Path; bytes: int
def pack(src_dir: Path, dest_dir: Path, name: str, on: date) -> Backup: ...   # name-YYYY-MM-DD[-n].zip; verifies with testzip()
def remove_source(src_dir: Path, backup: Backup) -> None: ...                 # only after pack() verified; refuses otherwise
```

### `gateway.py`
```python
router: APIRouter            # mounted at /llm

@dataclass(frozen=True)
class Metered: prompt_tokens: int; completion_tokens: int; reasoning: str | None
class BudgetExceeded(Exception): ...

async def openai_chat(pet_token: str, request: Request) -> Response: ...     # POST /llm/{pet_token}/v1/chat/completions → Ollama
async def anthropic_messages(pet_token: str, request: Request) -> Response: ...  # POST /llm/{pet_token}/anthropic/v1/messages
    # upstream chosen by the pet's ModelChoice.provider: anthropic → api.anthropic.com (adds x-api-key);
    # ollama → {ollama_url}/v1/messages. Both are metered by meter_anthropic_stream
def upstream_for(model: ModelChoice, fmt: Literal["openai", "anthropic"], cfg: Config) -> tuple[str, dict[str, str]]: ...  # url, headers
def meter_openai_stream(chunks: Iterable[bytes]) -> Metered: ...             # pure; sets stream_options.include_usage on the way in
def meter_anthropic_stream(chunks: Iterable[bytes]) -> Metered: ...          # pure
def check_budget(pet: Pet, tokens_used: int, elapsed: timedelta) -> None: ...   # raises BudgetExceeded
```

### `codeproxy.py` and `lessons.py`
```python
router: APIRouter            # mounted at /pets/{pet_id}/code
async def http_proxy(pet_id: str, path: str, request: Request) -> StreamingResponse: ...
async def ws_proxy(pet_id: str, path: str, ws: WebSocket) -> None: ...

async def draft(cfg: Config, pet: Pet, soul_md: str, source: LessonSource, text: str) -> str: ...
    # one model call through the gateway's upstream (not metered against the task); returns one plain-English line
```

### `web.py`
```python
app: FastAPI
class PetSubnetGuard(BaseHTTPMiddleware): ...    # 403 for any non-/llm path from cfg.pet_subnet_pool
def get_fleet() -> Fleet: ...; def get_conn() -> sqlite3.Connection: ...; def get_bus() -> Bus: ...
# one handler per route in 02-architecture § Endpoints, e.g.
async def post_call(call_id: int, option: CallOutcome = Form(), reason: str | None = Form(None)) -> HTMLResponse: ...
async def events(pet: str | None = None) -> EventSourceResponse: ...
```

### Pet runtime
```python
# petd/petd.py
AGENT_ARGV: dict[str, list[str]]   # "hermes": [python, -m, acp_adapter]; "claude": [node, …/claude-agent-acp]
class Petd:
    def __init__(self, token: str, brain: str, workspace: Path, home: Path) -> None: ...
    async def run(self) -> None: ...                                # spawn code-server + AGENT_ARGV[brain]; serve :7000 and 127.0.0.1:7001
    async def _client(self, ws) -> None: ...                        # one client at a time; replays buffer, re-sends pending asks
    async def _pump_agent_stdout(self) -> None: ...                 # agent → client (or buffer, max 5000 msgs)
    async def _pump_client(self, ws) -> None: ...                   # client → agent; responses to petd-* ids resolve leash futures
    async def ask(self, tool_call: dict, options: list[dict], timeout: float) -> str: ...   # returns optionId or "timeout"

# leash/tomabotchi_leash/__init__.py
def register(ctx) -> None: ...                                      # registers pre_tool_call
def pre_tool_call(tool_name: str, args: dict, **ctx) -> dict | None: ...
    # verdict = core.judge("hermes", tool_name, args, rules_path=$HOME/.hermes/tomabotchi_rules.json, ...)
    # never → {"action": "block", "message": "My keeper doesn't allow <tool>."}
    # ask   → POST 127.0.0.1:7001/ask, blocking; reject or timeout → block with the reason; allow → None
    # free  → None (also: dangerous-command patterns force "ask" even when execute is free)
# leash/tomabotchi_leash/core.py: shared by both brains' entry points
@dataclass(frozen=True)
class Verdict: allow: bool; message: str | None
def judge(brain: str, tool_name: str, args: dict, *, rules_path: str, ask_url: str, timeout: float) -> Verdict: ...
    # rules → free | never | ask (petd /ask, blocking). Dangerous commands force ask. Unreachable petd → deny

# leash/tomabotchi_leash/claude_hook.py: run as /opt/tomabotchi/bin/leash-claude
def main(stdin: TextIO = sys.stdin, stdout: TextIO = sys.stdout) -> int: ...
    # input {"tool_name", "tool_input", ...} → judge("claude", ...) →
    # {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "allow" | "deny",
    #                         "permissionDecisionReason": message}}; exit 0

# leash/tomabotchi_leash/kinds.py
def kind_for(brain: str, tool_name: str) -> str: ...
def paths_in(brain: str, tool_name: str, args: dict) -> list[str]: ...
def is_dangerous(brain: str, tool_name: str, args: dict) -> bool: ...   # Hermes's detector if importable, else a built-in pattern list
```

## Call stack

**Hatch** (`POST /pets`)
```
web.post_pet(form)
└ fleet.hatch(form)
  ├ store.insert_pet(state="hatching"); bus.publish(_plate egg)
  ├ soul.render_initial(form) → store.add_soul_revision("hatched") → soul.write_soul_file
  ├ brain = brains.get(form.brain)
  ├ store.replace_tool_rules(DEFAULT_RULES) → rules.dump_rules_file(brain.rules_path(brain_home))
  ├ brain.write_config(cfg, pet, model, token, brain_home)   # Hermes config.yaml, or Claude settings.json + hook
  ├ docker.ensure_pet_image(home) → ensure_runtime() → build (progress → bus → LCD egg wiggle text)
  ├ docker.create_network(pet.id) → docker.run_pet(...)
  ├ PetSupervisor(...).start() → acp.connect → initialize → new_session
  ├ store.set_pet_state("awake")
  └ if form.first_task_md: supervisor.feed(first_task_md)
→ 303 redirect to /pets/{id}
```

**Working update** (Hermes → server → browser)
```
hermes acp (stdout) → petd._pump_agent_stdout → ws → AcpConnection reader
└ supervisor._on_update(update)
  ├ _translate(update) → store.append_event / store.replace_plan
  ├ vitals.compute(...) → if scene changed or sick_reason appeared: notify.for_event("sick")
  └ _publish_pet() → bus.publish(_egg, _plate, _thoughts | _tools | _steps, notify?)
web.events → bus.subscribe → SSE → htmx sse swap / app.js Notification
```

**Ask-first call** (the critical path)
```
Hermes:  tool dispatch → pre_tool_call(tool_name="write_file", args) → core.judge("hermes", …)
Claude:  PreToolUse → leash-claude (stdin {"tool_name":"Write", …}) → core.judge("claude", …)
└ judge: decide(rules, kind="edit", tool=…, paths=["notes/x.md"]) == "ask"
  └ POST 127.0.0.1:7001/ask  (blocks the hermes tool thread)
    └ petd.ask → JSON-RPC request {id:"petd-17", method:"session/request_permission"} → ws
      └ AcpConnection reader → supervisor._on_permission("petd-17", params)
        ├ store.open_call(...) ; store.set_pet_state(...)  ; bus.publish(_call, _egg alert, notify calling)
        └ await future
browser POST /calls/{id} option=reject_once reason="check notes/ first"
└ supervisor.answer(call_id, ...)
  ├ store.answer_call(...) → future.set_result({"outcome":"selected","optionId":"reject_once"})
  ├ if allow_always: fleet.set_rules(+ rules.rule_for_allow_always(...))
  └ if reason: lessons.draft(...) → store.add_lesson(proposed) → bus.publish(_lesson)
AcpConnection sends the JSON-RPC result → petd resolves "petd-17" → /ask returns → Verdict(allow=False, "Denied: check notes/ first")
  Hermes: {"action":"block","message":…}      Claude: permissionDecision "deny" + reason
```
Anything a brain still asks about natively follows the same path from `_on_permission` onwards: a Claude action that no hook can auto-approve, or a timed-out hook. Native approvals are otherwise off, so in practice only the leash asks.

**Model call** (Hermes → gateway)
```
hermes → POST tomabotchi:8770/llm/<token>/v1/chat/completions
└ gateway.openai_chat
  ├ resolve the pet by token hash; check_budget → 429 + supervisor marks the pet asleep ("out_of_budget" notice)
  ├ httpx stream → Ollama /v1/chat/completions (include_usage)
  ├ tee chunks → client ; meter_openai_stream → store.add_usage
  └ if reasoning and no thought chunk this turn → supervisor emits thought events
```

**Retire**
```
web.post_retire → fleet.retire(pet_id, reason)
├ supervisor.stop() (cancel the session, close ws)
├ docker.remove_pet(pet_id)
├ archive.pack(pet_dir, archive_dir, name, today) → archive.remove_source
└ store.update_pet(state="retired", retired_at, retire_reason, backup_path, backup_bytes)
→ 303 to /memory
```

**Server boot**
```
cli serve → web lifespan → config.load → db.connect → Fleet.boot
└ docker.list_pet_containers → for each non-retired pet: PetSupervisor.start()
  (petd replays its buffer; pending petd-* asks re-sent → calls re-opened, matched to existing rows by acp_request_id)
```

## Test plan
Unit tests are pure and fast. They run with `uv run pytest`.
- `test_rules.py`
  - `test_defaults_read_free_edit_ask`: the default rules give read → free and edit → ask.
  - `test_tool_path_rule_beats_kind_rule`: a rule for `edit:notes/**` free plus a rule for edit ask gives free for `notes/a.md`.
  - `test_path_outside_glob_falls_back_to_kind`: the same rules give ask for `src/a.py`.
  - `test_never_beats_ask_at_same_specificity`
  - `test_multi_path_takes_strictest`
  - `test_allow_always_rule_uses_common_parent`: two paths under `notes/` give `notes/**`. No path gives a tool-only rule.
  - `test_rules_file_roundtrip_atomic`
- `test_vitals.py`
  - `test_scene_priority_call_beats_sick_beats_sleep`
  - `test_sick_after_three_identical_failures`: two failures aren't enough, and three with different args don't count.
  - `test_energy_hearts_from_budget`: 41% left gives 2 hearts.
  - `test_happy_hearts_from_last_ten`
  - `test_stage_thresholds`
  - `test_median_wait_odd_even_empty`
- `test_soul.py`
  - `test_render_initial_has_all_sections_and_keeper_block`
  - `test_add_lesson_creates_section_when_missing`
  - `test_add_lesson_appends_with_date`
  - `test_md_to_html_strips_script_and_on_attrs`: XSS is removed, while tables and code survive.
- `test_notify.py`
  - `test_master_off_suppresses_all`
  - `test_per_event_switch`
  - `test_quiet_hours_wrap_midnight`: 23:30 and 06:59 are quiet, 07:00 is not.
  - `test_finished_off_by_default`
- `test_gateway_metering.py`
  - `test_openai_stream_usage_from_final_chunk`
  - `test_openai_stream_reasoning_captured`: both `reasoning_content` deltas and `<think>` text.
  - `test_anthropic_stream_usage_from_message_delta`
  - `test_budget_exceeded_raises_at_limit_not_before`
- `test_supervisor_translate.py`
  - `test_thought_chunk_to_thought_event`
  - `test_tool_call_then_update_completed`
  - `test_plan_update_replaces_steps`
  - `test_unknown_update_kind_becomes_notice`
- `test_archive.py`
  - `test_pack_creates_verified_zip_with_all_files`
  - `test_pack_name_collision_adds_suffix`
  - `test_remove_source_refuses_without_valid_backup`
- `test_config.py`
  - `test_limits_clamped_to_ceiling`
  - `test_unknown_home_raises`
  - `test_exactly_one_default_home_required`
- `test_brains.py`
  - `test_hermes_config_approvals_off_leash_enabled_gateway_url`: an ollama model gives a `/v1` base_url, and an anthropic model gives `/anthropic`.
  - `test_claude_settings_hook_matches_all_tools_with_approval_timeout`
  - `test_claude_settings_never_bypass_permissions`
  - `test_claude_env_points_at_gateway_not_real_key`: the real `ANTHROPIC_API_KEY` never appears in written files.
  - `test_soul_paths_per_brain`: `.hermes/SOUL.md` and `.claude/CLAUDE.md`, the latter containing the soul text.
- `test_leash.py` (runtime package, pure parts)
  - `test_kinds_claude_tool_names`: Write, Edit and MultiEdit map to edit; Bash to execute; Grep and Glob to search; mcp__x to other.
  - `test_kinds_hermes_tool_names`
  - `test_paths_in_claude_write_and_multiedit`
  - `test_claude_hook_free_prints_allow`
  - `test_claude_hook_never_prints_deny_with_reason`
  - `test_claude_hook_ask_waits_on_petd_then_allows`: uses a stub /ask server.
  - `test_judge_denies_when_petd_unreachable`
- `test_gateway_routing.py`
  - `test_upstream_anthropic_model_adds_key_header`
  - `test_upstream_local_model_on_anthropic_route_goes_to_ollama_messages`: no key header is sent.

Integration tests use a real SQLite file and a **fake ACP agent**: a tiny WebSocket server in the test that speaks scripted JSON-RPC. No Docker.
- `test_acp_connection.py`
  - `test_initialize_and_new_session`
  - `test_prompt_resolves_on_stop_reason`
  - `test_incoming_permission_request_routed_to_handler`
  - `test_reconnect_after_server_drop`
- `test_call_flow.py`
  - `test_permission_request_opens_call_and_publishes`: a bus message arrives, and `pending_calls` returns 1.
  - `test_answer_reject_resolves_agent_and_drafts_lesson`: the fake agent receives `reject_once`, and the lesson is `proposed`. The model call is stubbed.
  - `test_allow_always_adds_rule_and_rewrites_rules_file`
  - `test_metric_median_from_answered_calls`
- `test_web.py` (FastAPI TestClient)
  - `test_pet_subnet_guard_blocks_api_allows_llm`: a 172.30.0.5 source gets 403 on `/calls/1` and passes on `/llm/...`.
  - `test_markdown_preview_sanitised`
  - `test_shelf_renders_pets_and_open_call`
  - `test_retire_redirects_and_lists_on_memory`: the Docker side is stubbed.

Docker tests are marked `@pytest.mark.docker` and are opt-in, for example with `uv run pytest -m docker`. They retire the Gate 2 risks.
- `test_runtime_on_homes`: build the runtime plus pet images for python:3.12-slim, node:22-slim and the Playwright image. `smoke.sh` passes on each.
- `test_leash_gates_write_file`: a real pet asked to write a file produces exactly one `session/request_permission` before the file exists. Rejecting it leaves no file.
- `test_soul_loaded_in_acp`: SOUL.md says "always answer with the word PICKLE first", and the first agent message starts with PICKLE.
- `test_codeproxy_serves_workbench_and_ws`: `/pets/{id}/code/` returns the workbench HTML, and a WebSocket upgrade through the proxy succeeds.
- `test_petd_buffers_across_server_restart`: drop the ws mid-turn, reconnect, and the replayed events include the turn's end.
- `test_claude_hook_gates_write`: a Claude-brain pet asked to write a file produces exactly one `session/request_permission`, which comes from the leash and not from Claude natively. Rejecting it leaves no file. Free tools produce no permission request at all.
- `test_claude_soul_loaded`: the PICKLE check, via `.claude/CLAUDE.md`.
- `test_claude_todowrite_becomes_plan`: a multi-step task produces ACP `plan` updates.
- `test_claude_on_local_model`: a Claude-brain pet on the local Ollama model completes "write hello.txt" after approval.

Tests for the browser and kit.js are manual: Chromium screenshots per slice, as with the mockups.

## Decided after review (user, 2026-10-25)
- Both Hermes and Claude Code are brains, chosen per pet at hatch.
- Native approvals are off in both brains, and the leash is the single gate.
- `docker pause` for Lights off.
- The in-house model gateway rather than LiteLLM.

## Least confident decisions
1. **The Hermes leash relies on the `pre_tool_call` contract**, known only from community docs: `{"action": "block", "message"}`, and some tools bypass the hook. If it fails, patch Hermes's ACP permission bridge to cover all tools. Until then, Claude Code is the safe brain for pets that need strict rules. Slice 1 tests this first.
2. **The Claude leash depends on two behaviours.** A PreToolUse `"allow"` must suppress Claude's own prompt, and a hook must be able to block for as long as `approval_timeout` (8 hours by default). If a hook times out, Claude falls back to its native prompt, which still reaches us as an ACP permission request, so you'd see a second, plainer call card rather than losing control.
3. **Both brains in every pet image.** It's simpler, and brains can be switched without a rebuild, but the runtime bundle grows by about 150 MB because of Node and the Claude SDK. The alternative is a per-brain runtime layer.
4. **Claude Code on local models** goes through Ollama's `/v1/messages`. Tool calling is supported, but `tool_choice` isn't, and qwen-class models may follow Claude Code's long system prompt poorly. That's acceptable for v1, and the hatch screen shows Claude Sonnet as the recommended model for Claude Code.
5. **The data dir is mounted at the same absolute path** in the server container, so Docker bind mounts for pets resolve on the host. It's simple but ties the compose file to `AI_ROOT`.
6. **Thinking text has two sources**: ACP thought chunks, and gateway-captured reasoning as a fallback. That risks duplicates or odd ordering. Dedupe is per turn and can't be perfect.
7. **Tidy differs per brain.** Hermes gets `/compress`, and Claude gets `/compact` sent as a prompt. This assumes slash commands work over ACP for both. The fallback starts a new session with a summary of the last one.
8. **Plan steps depend on the "keep a todo list" instruction** in the soul, and on each brain emitting ACP `plan` updates. The Claude adapter maps TodoWrite to plan updates. For Hermes, if it doesn't emit them, map its todo tool's tool_call events to steps.
9. **One SQLite writer in-process**, with `asyncio.to_thread` for Docker SDK calls. That's fine for about 10 pets, but not designed for 100.
10. **htmx and htmx-ext-sse are loaded from unpkg**, matching aihub. The shelf needs network access on first load. Vendoring them into `static/` would remove that dependency.
