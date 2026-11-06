# User guide

How to use tomabotchi day to day. For installing it, see the [README](../README.md#quick-start).

## Brains and models

- **Brains:** Hermes (the default) or Claude Code, chosen per pet. Both talk ACP to the server
  through `petd` inside the container.
- **Models:** the local Ollama model, or Claude Sonnet. Sonnet needs `ANTHROPIC_API_KEY`, set in
  `.env` (git-ignored). The hatch screen tells you when it's missing. Pets only ever see their own
  gateway token; the gateway swaps in the real key.
- **The practice brain** needs no Docker and no model. It plays a script, which is handy for
  trying the UI. Put `#sick` in a task for a stuck pet, or `#slow` for a long one.

Hatch a pet at **Hatch a pet**. The first hatch on a home builds the pet runtime and that home's
image, which takes a few minutes. Later hatches take seconds.

## The interface

- **The desk** (`/`): every pet is a channel strip, and its work plays across a shared timeline (5m, 15m or 1h;
  `[` and `]` zoom). Thoughts, messages and tools are blocks, a failed step is red, a loop is bracketed, a call
  that's waiting on you is an orange block that grows until you answer, and a finished task leaves a marker.
  Click a strip or lane, or press `1` to `9`, to focus a pet: its deck under the timeline holds the call, a
  compact log, a box for the next task, its rules and its readout.
- **A pet's console** (`/pets/<id>`): every pet racked down the left, the live stream in the middle with the
  approval dock and composer under it, and an inspector (readout, rules, container, controls). Tabs: Live,
  Workspace, Soul, Skills, History.
- **Moving around:** links, tabs and the rack don't reload the page, so the live stream stays connected.
  `Ctrl K` jumps to a pet, an action or a page; `N` (or the calls key in the top bar) goes to the pet that has
  waited longest for you; `A`, `Shift A` and `D` answer the call in view. The theme is light, dark or the
  system's, remembered per browser.
- **Readout, not hearts:** budget left, context used, steps succeeding and how often it asked before acting.
  Each pet's face is a pixel creature drawn from its look, with a scene for working, calling, stuck and asleep.

## Keyboard shortcuts

| Key | Does |
|---|---|
| `1`–`9` | Focus a pet on the desk |
| `[` / `]` | Zoom the timeline |
| `Ctrl K` | Command palette: jump to a pet, an action or a page |
| `N` | Go to the pet that has waited longest for you |
| `A` | Approve the call in view, once |
| `Shift A` | Always approve (adds a narrow rule) |
| `D` | Deny |

## Daily use

| You want to… | Do this |
|---|---|
| Approve or deny a tool | Press `A` (once), `Shift A` (always) or `D` (deny) on the call, or click the keycap. "Always" adds a narrow rule you can see under Rules. A reason on a denial becomes a lesson. |
| Change what it may do | Rules → flip a row between free / ask / never (it saves at once), or Add a rule for a tool or folder. |
| Teach it | Deny with a reason, or Soul → Teach / Praise. Accept the drafted lesson and the next task uses it. |
| Give it work | Type in the New task box on the desk or console. Chat sends a message without starting a task. |
| Edit its files | Workspace tab (VS Code in the page). Pause it while you edit if it's busy. |
| Stop it for a while | Sleep (pauses the whole container). Wake picks the task back up. |
| Unstick it | Unstick: stops the turn and nudges it to try another way. |
| Get beeped | Allow notifications in the banner, then tune them in Settings (quiet hours use your timezone). |
| Read what it did | History tab: every task and chat with its full transcript (thoughts, messages, tools, calls and your answers). Download one as Markdown, or save it to `tomabotchi-logs/` in the workspace. |
| Move it to another model | Controls → Model → Switch (or Ctrl K → "Move … to …"). Its home restarts into a fresh conversation; files, soul, lessons, rules and memory stay. Claude models need `ANTHROPIC_API_KEY`. |
| Give it skills | Skills tab → Install from the store. The store ships a few skills (in `src/tomabotchi/skill_store/`) and keeps ones you add by link (a `.zip`, a `SKILL.md`, or a GitHub folder) in `data/skills/`. Installed skills are copied to `~/.hermes/skills/tomabotchi/` or `~/.claude/skills/` in its brain home and apply from its next task. |
| Retire it | Retire in its inspector. Its workspace and brain are zipped to `data/archive/`, and it moves to the Archive, where you can hatch a new pet from its soul. |

## Configuration

`tomabotchi.toml` lists:

- **homes**: the base images a pet can live in. Use `build = "runtime/homes/<slug>"` for ones
  tomabotchi builds itself. Each home must be glibc-based (Debian/Ubuntu).
- **models**: the models offered at hatch time.
- **[pets]**: the memory, CPU and process limits of each pet's container.

Edit it and run `bin/tomabotchi restart`.

### Environment variables

Set these in `.env` (copy `.env.example`):

| Variable | Default | Meaning |
|---|---|---|
| `ANTHROPIC_API_KEY` | (none) | Needed for pets on Claude models. The server keeps it; pets never see it. |
| `TOMABOTCHI_PORT` | `8770` | The dashboard's port on 127.0.0.1 |
| `OLLAMA_BASE_URL` | `http://host.docker.internal:11434` | Where the server finds Ollama |

For development and tests: `TOMABOTCHI_ROOT` (the repo folder), `TOMABOTCHI_DATA_DIR` (default `data/`),
`TOMABOTCHI_CONFIG`, `TOMABOTCHI_DOCKER=0` (practice pets only).

### No Ollama on the host?

`docker compose --profile ollama up -d` also runs an Ollama of its own and pulls the models listed in
`tomabotchi.toml`. Then set `OLLAMA_BASE_URL=http://host.docker.internal:11435` in `.env`. It publishes on
the Docker bridge address (172.17.0.1); set `OLLAMA_BIND` to change it.
