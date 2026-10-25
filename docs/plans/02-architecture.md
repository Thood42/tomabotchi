# Architecture: tomabotchi

## Recommendation: what to build on

**tomabotchi is a thin control plane. Every pet is a Docker container running a pluggable
agent "brain" that tomabotchi talks to over ACP (Agent Client Protocol). Each pet's brain is
chosen at hatch: Hermes Agent (the default) or Claude Code.** (Claude Code as a second brain
added at the user's request, 2026-10-25.)

| Option | Verdict | Why |
|---|---|---|
| **Hermes Agent** (Nous Research) | **Default brain** | SOUL.md is its native personality file. It runs on local models through Ollama (`provider: custom`, `base_url …/v1`) and on Anthropic. It has memory, skills and a ready-made Docker image (`nousresearch/hermes-agent`). It speaks ACP (`hermes acp`), with streamed reasoning, tool-call events with file diffs, and a permission bridge. |
| **ACP** (agentclientprotocol.com) | **The seam** | A standard JSON-RPC protocol between a UI and an agent: `session/new`, `session/prompt`, `session/cancel`; `session/update` (message, thought, tool_call, tool_call_update, plan); `session/request_permission` with options `allow_once / allow_always / reject_once / reject_always`. Those map one-to-one onto the call card's buttons. Claude Code (through Zed's ACP adapter), Gemini CLI and Codex also speak ACP, so the brain can be swapped without touching tomabotchi. |
| **Claude Code via ACP adapter** | **Second brain, selectable per pet in v1** | `@zed-industries/claude-agent-acp` (Apache-2.0, upstream `agentclientprotocol/claude-agent-acp`, formerly `claude-code-acp`) wraps the Claude Agent SDK as an ACP agent. It streams thoughts and tool calls, and its TodoWrite tool becomes ACP `plan` updates. It runs on Claude models through the Anthropic API, or on local models through Ollama's Anthropic-compatible `/v1/messages` endpoint (Ollama ≥ 0.14; this host runs 0.33.3). SOUL.md reaches it as `~/.claude/CLAUDE.md`. |
| **OpenHands** agent-server | Considered, not chosen | It is the closest off-the-shelf match: a per-agent Docker sandbox with VS Code Web and a confirmation mode. But it uses its own event protocol rather than ACP, is focused on coding, has no SOUL.md concept, and its large fixed sandbox images conflict with "pick any home image". |
| **Herdr** (terminal agent multiplexer) | Not a fit for v1 | It tracks working/blocked/idle/done for interactive CLI agents (Claude Code, Codex) in terminal panes, through a Unix socket API, and is licensed AGPL. It has no containers and no web UI. It is a good candidate for **v2 "adopt existing sessions"**, alongside the Agent Herder MCP server. We borrow its state model. |
| CrewAI / LangGraph / AutoGen | Not chosen | They are libraries for orchestrating agents inside one process. tomabotchi needs long-lived, isolated, individually supervised agents, which is a process-and-container problem rather than a graph library problem. |

**One gate for both brains: the leash.** The brains' own approval systems are switched
off (user decision, 2026-10-25), so you aren't asked twice and tomabotchi's rules are the
only rules. The `tomabotchi_leash` package in the pet runtime decides every tool call
against the pet's rules (free / ask / never). For "ask" it waits on `petd`, which sends a
standard `session/request_permission` to tomabotchi, so every approval looks the same to
the server whichever brain asked.
- **Hermes:** `approvals.mode: off`. The leash is a Hermes plugin on the `pre_tool_call` hook, returning `{"action": "block" | "approve", "message": …}`. It is needed anyway, because Hermes's ACP bridge only covers dangerous terminal commands.
- **Claude Code:** the leash is a `PreToolUse` command hook in `~/.claude/settings.json`, matching every tool. It returns `hookSpecificOutput.permissionDecision` of `"allow"` (skipping Claude's own prompt) or `"deny"` (with the reason shown to Claude). Its timeout matches tomabotchi's approval timeout. We don't use `bypassPermissions`: keeping the normal mode means the few actions Claude always prompts for, or a hook that times out, still arrive as ACP permission requests and go through the same call flow.

**Slice 2 (the first real slice, right after the mocked tracer bullet) must prove the Hermes plugin path. The Claude hook path gets its own slice.**

## Fit
- **All code, config and data live under `platform/`** (user decision, 2026-10-25). `sessions/tomabotchi/docs/plans/` holds only these planning docs.
- **Tool rules are structured** (user decision, 2026-10-25): free / ask / never, by ACP tool kind, with optional tool-name and path narrowing. Defaults: free for `read`, `search`, `fetch`, `think`; ask for `edit`, `delete`, `move`, `execute`, `other`. Edited in a rules editor on the pet screen. The free-text "ask first / never" answers go into SOUL.md as guidance only.
- **Free text is Markdown** (user decision, 2026-10-25). Every free-text field uses a Markdown textarea with a Write/Preview toggle, and the result is rendered server-side with sanitising.
- **New service, `platform/tomabotchi/`**, alongside `platform/hub` (aihub). It follows aihub's idiom: Python, FastAPI, Jinja templates with HTMX, no frontend build step, and `kit.js`/`kit.css` ported from the mockups. It doesn't use aihub's systemd runner, because pets are containers rather than transient units.
- **Reuses `platform/containment.toml`.** `[defaults]` and `[ceiling]` become each pet container's `--memory`, `--cpus` and `--pids-limit` (memory_max, cpu_quota, tasks_max). A new `[tomabotchi]` table caps concurrent awake pets.
- **Joins the Docker stack.** It adds `platform/stack/tomabotchi.compose.yml`, which `platform/bin/stack` includes like the Langfuse and Open WebUI files. The server is published on `127.0.0.1:8770`, matching the localhost-only rule.
- **Networking follows the host's ufw rule.** The server uses `network_mode: bridge` plus `host.docker.internal:host-gateway`, the same as Open WebUI, because ufw only lets the default bridge reach Ollama on :11434. Each pet gets **its own** user network, so pets can't see each other or the host. They reach models only through the server's model gateway (see Flow).
- **New config file, `platform/tomabotchi.toml`.** It holds the list of homes (base images), the default home, model choices and the runtime version. It is read-only in the UI.
- **Data lives on the host** under `platform/data/tomabotchi/` (gitignored, bind-mounted into the server):
  ```
  tomabotchi.db                      SQLite: pets, events, calls, souls, settings
  pets/<pet_id>/workspace/           the pet's files (VS Code opens this)
  pets/<pet_id>/brain/               the pet's $HOME: .hermes/ (SOUL.md, config.yaml, memories) or .claude/ (CLAUDE.md, settings.json)
  archive/<name>-<YYYY-MM-DD>.zip    retired pet backups (workspace + brain home)
  ```
- **Langfuse:** not wired in for v1. tomabotchi keeps its own event store, because the UI needs it live. Exporting through Langfuse's OTLP ingestion can come later.

### Pet container anatomy
- **Image:** `tomabotchi-pet:<home-slug>-<runtime-version>`, built once per home the first time it's used:
  ```dockerfile
  FROM <home image from tomabotchi.toml>            # e.g. python:3.12-slim
  COPY --from=tomabotchi-runtime:<ver> /opt/tomabotchi /opt/tomabotchi
  ENTRYPOINT ["/opt/tomabotchi/bin/petd"]
  ```
- **Runtime bundle `/opt/tomabotchi`** (built from `platform/tomabotchi/runtime/Dockerfile`). Everything is self-contained so it works on any home:
  - Standalone Python (python-build-standalone via uv).
  - `hermes-agent[acp]` and the `tomabotchi_leash` package (Hermes plugin and Claude hook script).
  - Standalone Node 22 with `@zed-industries/claude-agent-acp`.
  - The code-server standalone release.
  - `petd`.
  - Both brains are in every pet image, so changing a pet's brain needs no rebuild.
- **Home images must be glibc-based** (Debian/Ubuntu). Alpine isn't supported, and the build smoke test rejects it.
- **`petd`**, the in-container supervisor:
  - Runs `code-server --auth none --bind-addr 0.0.0.0:8080 /workspace`.
  - Runs the brain's ACP command as a child process (`BRAIN=hermes`: `hermes acp`; `BRAIN=claude`: `claude-agent-acp`) and exposes its stdio as a WebSocket on `:7000`, authenticated with a per-pet token.
  - Serves the leash plugin on `127.0.0.1:7001`.
  - Buffers events and holds pending permission requests while the server is disconnected, so **pets keep working if the dashboard restarts**.
- **Mounts:** `pets/<id>/workspace` → `/workspace`, `pets/<id>/brain` → `/home/pet` ($HOME, holding `.hermes/` or `.claude/`). Runs as uid 1000, the host user, so files stay editable from the host.
- **Container settings:** `--memory`, `--cpus` and `--pids-limit` come from containment.toml. `--cap-drop ALL`, `no-new-privileges`, no Docker socket. Network `tbx-pet-<id>`.

## Endpoints
Pages (HTML, Jinja + HTMX):
- `GET /`: the shelf
- `GET /hatch`: the hatch screen (`?from=<retired_id>` prefills the soul)
- `GET /pets/{id}`: live view
- `GET /pets/{id}/workspace`: VS Code embed page
- `GET /pets/{id}/soul`: soul page
- `GET /memory`: the memory shelf
- `GET /settings`: notifications and homes

Actions (form posts or HTMX; they return partials or redirect):
- `POST /pets`: hatch (name, shell, look seed, soul answers, home, brain, model, budget, optional first task)
- `POST /pets/{id}/tasks`: feed a task
- `POST /pets/{id}/chat`: send a message into the current session
- `POST /pets/{id}/lights`: pause or resume (`on=0|1`)
- `POST /pets/{id}/medicine`: cancel the stuck turn and nudge with context
- `POST /pets/{id}/tidy`: compact the pet's memory
- `POST /calls/{call_id}`: answer a permission request (`option=allow_once|allow_always|reject_once`, `reason?`)
- `PUT /pets/{id}/soul`: save a manual edit (creates a new revision)
- `PUT /pets/{id}/rules`: replace the pet's tool rules (rules editor on the pet screen). The leash picks them up without a restart
- `POST /markdown/preview`: render Markdown to sanitised HTML for the Write/Preview toggle on every free-text field (hatch answers, SOUL.md editor, teach box, deny reason, retire reason)
- `POST /pets/{id}/lessons`: teach. Drafts a lesson from your words
- `POST /pets/{id}/praise`: praise the last task. Drafts a lesson
- `POST /lessons/{id}`: accept, reword or discard a proposed lesson
- `POST /pets/{id}/retire`: retire (`reason?`)
- `GET /memory/{id}/backup`: download the zip
- `PUT /settings/notifications`: master switch, per-event switches, quiet hours

Live updates:
- `GET /events`: Server-Sent Events for every pet (state changes, calls, thoughts, tool calls, plan, vitals). `?pet={id}` narrows it to one pet. It drives the shelf, pet page and browser notifications.

Proxies (same origin, so the iframe embed just works):
- `ANY /pets/{id}/code/{path:path}`: HTTP and WebSocket reverse proxy to `tbx-pet-<id>:8080` (code-server), with the prefix stripped. This same URL is the "Open in a new tab" fallback. Setting `direct_code_ports = true` in the config also publishes each pet's code-server on `127.0.0.1:<port>` as the host-only fallback.
- The **model gateway**, built in-house (user decision, 2026-10-25). It is only reachable from pet networks. It counts tokens for each pet and task (Energy), enforces the budget, captures reasoning text as a fallback for the Thinking panel, and keeps API keys out of pet containers. Two inbound formats:
  - `POST /llm/{pet_token}/v1/chat/completions` (OpenAI-compatible): Hermes on a local model → Ollama `/v1/chat/completions`.
  - `POST /llm/{pet_token}/anthropic/v1/messages` (Anthropic Messages): Claude Code (any model), or Hermes on a Claude model → `api.anthropic.com`, or Ollama `/v1/messages` when the chosen model is local.

## Data
SQLite, `tomabotchi.db`, in WAL mode. One writer: the server.

- **pets**: id, name, shell, look_seed, home, brain (`hermes|claude`), model, state (`hatching|awake|asleep|paused|sick|retired`), stage, born_at, token_hash, container_id, acp_session_id, budget_tokens, budget_seconds, retired_at, retire_reason, backup_path, backup_bytes
- **tasks**: id, pet_id, prompt, state (`running|waiting|done|failed|cancelled`), started_at, ended_at
- **plan_steps**: task_id, idx, content, status (`pending|in_progress|completed`). Replaced wholesale on each ACP `plan` update
- **events**: id, pet_id, task_id, ts, kind (`thought|message|tool_call|tool_update|notice|state`), payload JSON. An append-only log of ACP updates
- **calls**: id, pet_id, task_id, tool_call JSON, options JSON, asked_at, answered_at, outcome (`allow_once|allow_always|reject_once|timeout`), reason
- **tool_rules**: pet_id, match (an ACP tool `kind` such as `edit`, or a tool name with a path glob, e.g. `edit:notes/**`), rule (`free|ask|never`). Seeded with defaults at hatch, and `allow_always` adds rows
- **usage**: pet_id, task_id, ts, model, prompt_tokens, completion_tokens. Written by the model gateway
- **soul_revisions**: id, pet_id, version, content, source (`hatched|edited|lesson`), note, created_at. The latest one is written to the brain's soul file in `pets/<id>/brain/` (`.hermes/SOUL.md` or `.claude/CLAUDE.md`)
- **lessons**: id, pet_id, text, source (`scold|praise|teach|reflect`), call_id, state (`proposed|accepted|discarded`), created_at
- **settings**: key, value JSON (notifications, quiet hours)

Main queries:
- Shelf: `pets WHERE state != 'retired'`, plus each pet's open call (`calls WHERE answered_at IS NULL`), current task and plan_steps.
- Success metric: the median of `answered_at - asked_at` over calls in the last 7 days. SQLite has no median, so this is computed in Python over the at most few hundred rows.
- Happy hearts: the share of `completed` among the pet's last 10 `tool_update` events.
- Health or sick: the same tool name and arguments `failed` 3 or more times in a row, or a brain loop-guard notice.
- Energy: `SUM(usage tokens) WHERE task_id = current` against budget_tokens, and elapsed time against budget_seconds.
- Mess: the last context-usage or compaction notice in `events`.
- Discipline: ask-calls divided by (ask-calls + leash `never` blocks) over the pet's lifetime.
- Thinking panel: the last 20 `events WHERE kind='thought'` for the pet.

## Flow
**Hatch**
1. The browser posts `POST /pets`. The server writes the pet row, renders SOUL.md from the answers (revision 1) and seeds the tool_rules defaults.
2. The server builds `tomabotchi-pet:<home>` if it's missing, streaming progress to the egg on the LCD. It then creates network `tbx-pet-<id>`, connects itself to that network, and runs the container with its mounts, limits and `PET_TOKEN`.
3. The server writes the brain's config into `pets/<id>/brain/`:
   - **Hermes:** `.hermes/config.yaml` with the model pointed at the gateway, `approvals.mode: off` and the leash plugin enabled, plus `.hermes/SOUL.md`.
   - **Claude Code:** `.claude/settings.json` with the leash `PreToolUse` hook and env `ANTHROPIC_BASE_URL` / `ANTHROPIC_AUTH_TOKEN` set to the gateway, plus `.claude/CLAUDE.md` holding the soul.

   `petd` then starts code-server and the brain's ACP command.
4. The server dials `ws://tbx-pet-<id>:7000`, then sends ACP `initialize` and `session/new` (cwd `/workspace`). If there's a first task, it sends `session/prompt`.

**Working, and a call**
1. The brain streams `session/update` messages through `petd` to the server. The server appends them to `events`, updates `plan_steps` and `tasks`, and pushes them over SSE. The browser's `kit.js` updates the LCD scene, the hearts, Thinking, Tools and Steps.
2. The brain decides to write a file: Hermes's `write_file` goes through the `pre_tool_call` plugin, and Claude's `Write` goes through the `PreToolUse` hook. Either way the leash asks `petd` on `127.0.0.1:7001`, and `petd` sends `session/request_permission` to the server. Anything a brain still asks about natively arrives as the same ACP request.
3. The server writes a `calls` row (asked_at), sets the pet's state to "calling" and pushes SSE. The shelf egg wobbles, and a browser notification fires if it's enabled and outside quiet hours.
4. You click Deny with a reason. The server sends the ACP response and sets answered_at and outcome. Because there's a reason, it drafts a lesson: a one-shot model call through the gateway that rewrites the reason as a SOUL.md line. The lesson row is created with state `proposed`.
5. Accepting the lesson creates a new soul revision and rewrites the brain's soul file (`.hermes/SOUL.md` or `.claude/CLAUDE.md`). The brain picks it up at the next `session/new`, which is the next task.

**Workspace**
- The iframe loads `/pets/{id}/code/`. The server proxies HTTP and WebSocket traffic to `tbx-pet-<id>:8080`.
- "Pause while I edit" is the same action as Lights off.

**Lights off and on**
- Off sends `session/cancel` (if a turn is running), then `docker pause`.
- On runs `docker unpause`, then sends `session/prompt "Continue where you left off."`.

**Retire**
1. Cancel the session and stop and remove the container and its network.
2. Zip `pets/<id>/{workspace,brain}` to `archive/<name>-<date>.zip` and verify the zip.
3. Mark the pet retired with backup_path, then delete `pets/<id>/`.
4. The souls stay in the DB, so "Hatch from this soul" just prefills `/hatch`.

**Server restart**
- On boot the server lists containers labelled `tomabotchi.pet=<id>` and redials each `petd`.
- `petd` replays buffered events and re-sends any permission request that is still pending.

## External
- **Docker Engine API**, via a `/var/run/docker.sock` mount in the server container. It is used to build images, create networks and containers, pause, stop and stats. The server is the only thing with the socket. Pets never get it.
- **Ollama** (host): `http://host.docker.internal:11434`, reached only by the server. It is used through both `/v1/chat/completions` and the Anthropic-compatible `/v1/messages`, which needs ≥ 0.14. Local models used by Claude Code may need an Anthropic-style alias via `ollama cp`.
- **Anthropic API**: through the model gateway only. Claude models need `ANTHROPIC_API_KEY`. Logging Claude Code into a Claude subscription isn't supported inside pets in v1.
- **Packages and images:**
  - `hermes-agent[acp]` (pip). `nousresearch/hermes-agent` is reference only.
  - `@zed-industries/claude-agent-acp` (npm).
  - Node 22 standalone, the code-server release tarball, python-build-standalone.
  - Each home's base image from `tomabotchi.toml`.
- **Browser Notification API** (no push service). Notifications fire from any open tomabotchi tab, even in the background. Closed-tab Web Push is out of scope for v1.
- **Env var names:**
  - `TOMABOTCHI_DATA_DIR`
  - `TOMABOTCHI_CONFIG` (path to tomabotchi.toml)
  - `TOMABOTCHI_PORT` (default 8770)
  - `OLLAMA_BASE_URL`
  - `ANTHROPIC_API_KEY` (optional, in `platform/stack/.env`)
  - `PET_TOKEN` (generated per pet, passed to the pet container)
- **Security notes:**
  - Pets can reach the server's IP on their network, so the server rejects every non-gateway route whose source address is in the pet subnet pool (`172.30.0.0/16`). That stops a pet approving its own calls.
  - The gateway authenticates each pet by its token.

## Risks the first real slices must retire
1. Does the `tomabotchi_leash` `pre_tool_call` plugin reliably gate `write_file` and patch tools in ACP mode, and can it block while waiting for a human?
2. Does Hermes in ACP mode load `SOUL.md` from HERMES_HOME, and does it emit thought chunks and `plan` updates? If it doesn't emit plan updates, map its todo tool's tool_call events to plan_steps.
3. Can FastAPI reverse-proxy code-server (HTTP and WebSocket, prefix stripped) cleanly inside an iframe? If not, put Caddy in front.
4. Does the runtime bundle (standalone Python, Node, both brains, code-server) run unchanged on `python:3.12-slim`, `node:22-slim` and the Playwright image?
5. Claude Code brain:
   - Does a `PreToolUse` hook that returns `"allow"` really stop Claude's own prompt over ACP?
   - Does a hook that blocks for minutes, waiting for you, work within its timeout?
   - Does `claude-agent-acp` read `~/.claude/CLAUDE.md` and turn TodoWrite into `plan`?
   - Does it run on a local Ollama model through `/v1/messages`?
