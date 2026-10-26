# tomabotchi

Pocket pets for AI agents. Each pet is an agent with its own Docker container, its own soul
(SOUL.md or CLAUDE.md), and a dashboard where you watch every pet work on a shared timeline, answer
its calls for tools from the keyboard, teach it lessons, and open its workspace in VS Code.

http://127.0.0.1:8770 (localhost only).

## Run it

Needs Docker, and Ollama on the host for local models (`ollama pull qwen3.6:35b`).

```bash
cp .env.example .env                    # add ANTHROPIC_API_KEY if you want Claude models
docker compose up -d --build            # build and start the server (bin/tomabotchi up does the same)
bin/tomabotchi logs                     # follow its log
bin/tomabotchi ps                       # the server, and the pets' containers
docker compose down                     # stop the server (pets keep running; it reattaches)
```

The server joins whichever group owns the Docker socket, so there's no group id to look up. On a machine
with no Ollama of its own, `docker compose --profile ollama up -d` also runs one and pulls the models listed
in `tomabotchi.toml`; set `OLLAMA_BASE_URL` as `.env.example` says.

Hatch a pet at **Hatch a pet**. The first hatch on a home builds the pet runtime and that home's
image, which takes a few minutes. Later hatches take seconds.

- **Brains:** Hermes (the default) or Claude Code, chosen per pet. Both talk ACP to the server
  through `petd` inside the container.
- **Models:** the local Ollama model, or Claude Sonnet. Sonnet needs `ANTHROPIC_API_KEY`, set in
  `.env` (git-ignored). The hatch screen says so
  when it's missing. Pets only ever see their own gateway token; the gateway swaps in the real key.
- **The practice brain** needs no Docker and no model. It plays a script, which is handy for
  trying the UI. Put `#sick` in a task for a stuck pet, or `#slow` for a long one.

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

## Config

`tomabotchi.toml` lists the **homes** (base images; `build = "runtime/homes/<slug>"` for ones
tomabotchi builds itself), the **models**, and **[pets]**, the memory, CPU and process limits of each
pet's container. Edit it and run `bin/tomabotchi restart`.

Environment variables (in `.env`): `ANTHROPIC_API_KEY`, `TOMABOTCHI_PORT` (default 8770),
`OLLAMA_BASE_URL` (default `http://host.docker.internal:11434`). For development and tests:
`TOMABOTCHI_ROOT` (this folder), `TOMABOTCHI_DATA_DIR` (default `data/`), `TOMABOTCHI_CONFIG`,
`TOMABOTCHI_DOCKER=0` (practice pets only).

## Layout

```
bin/tomabotchi       start, stop, logs and tests
compose.yml          the server (the repo is mounted at its host path, data/ read-write); --profile ollama adds Ollama
docker/              the server's entrypoint (joins the socket's group, drops to uid 1000) and the Ollama model pull
tomabotchi.toml      homes, models and pet limits
data/                git-ignored: tomabotchi.db, pets/<id>/{workspace,brain}, archive/*.zip
src/tomabotchi/      the server: web.py (pages, actions, SSE), fleet.py (pet lifecycle), supervisor.py
                     (one ACP client per pet), gateway.py (model proxy and metering), codeproxy.py
                     (VS Code), brains.py, rules.py (shared with the leash), soul.py, lessons.py,
                     archive.py, skills.py + skill_store/ (the skill store), store.py + schema.sql, views.py (page and partial contexts), timeline.py
                     (a pet's recent work as blocks), vitals.py (scene and readout), bus.py (live updates)
                     templates/  Jinja pages and the partials swapped live; static/  shell.css (tokens and parts),
                     desk, console and page styles, app.js (live swaps, keys, palette), desk.js, vendor/
runtime/             the pet runtime image: petd, the leash (a Hermes plugin and a Claude hook),
                     smoke test, pet.Dockerfile.tmpl, homes/<slug>/Dockerfile
tests/               unit and integration tests; tests/test_docker_pets.py needs Docker and Ollama
docs/plans/          planning docs for the original build
```

### How the page stays live

Each pet's refresh renders a handful of partials (module, track, stream, call, readout, …) whose root element
ids are their event names. `bus.publish_changed` sends only the ones whose HTML changed, `/events` streams them,
and `app.js` morphs each into the page with Idiomorph, so scroll, focus and a half-typed reason survive. Renders
never touch the disk or Docker: container stats and workspace sizes come from a poller in `fleet.py`. The live
bus is in process (Redis is the planned next step if the server splits into a web tier and a fleet process).

## Tests

```bash
bin/tomabotchi test              # unit and integration (practice brain, stub upstreams), ~2.5 min;
                                 # makes .venv on first use
.venv/bin/pytest -m docker -q    # real pets on the local model: slow on CPU (minutes per turn)
```
