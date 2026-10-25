# tomabotchi

Pocket pets for AI agents. Each pet is an agent with its own Docker container, its own soul
(SOUL.md or CLAUDE.md), and a toy-shelf dashboard where you watch it think, answer its calls
for tools, teach it lessons, and open its workspace in VS Code.

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

## Daily use

| You want to… | Do this |
|---|---|
| Approve or deny a tool | Answer the red call card. "Always allow" adds a narrow rule you can see under Tool rules. |
| Change what it may do | Tool rules → Change its rules (free / ask / never, per kind, tool and folder). |
| Teach it | Deny with a reason, or Soul → Teach / Praise. Accept the drafted lesson and the next task uses it. |
| Edit its files | Workspace tab (VS Code in the page). Pause it while you edit if it's busy. |
| Stop it for a while | Lights (pauses the whole container). Lights again picks the task back up. |
| Unstick it | Medicine: stops the turn and nudges it to try another way. |
| Chat without a task | Type in the feed box → Just chat. |
| Get beeped | Allow notifications in the banner, then tune them in Settings (quiet hours use your timezone). |
| Retire it | Retire under Home. Its workspace and brain are zipped to `data/archive/`, and it moves to the Memory shelf, where you can hatch a new pet from its soul. |

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
compose.yml          the server container (the repo is mounted at its host path, data/ read-write)
tomabotchi.toml      homes, models and pet limits
data/                git-ignored: tomabotchi.db, pets/<id>/{workspace,brain}, archive/*.zip
src/tomabotchi/      the server: web.py (pages, actions, SSE), fleet.py (pet lifecycle), supervisor.py
                     (one ACP client per pet), gateway.py (model proxy and metering), codeproxy.py
                     (VS Code), brains.py, rules.py (shared with the leash), soul.py, lessons.py,
                     archive.py, store.py + schema.sql
runtime/             the pet runtime image: petd, the leash (a Hermes plugin and a Claude hook),
                     smoke test, pet.Dockerfile.tmpl, homes/<slug>/Dockerfile
tests/               unit and integration tests; tests/test_docker_pets.py needs Docker and Ollama
docs/plans/          product, architecture, program design, slice plan, status log, mockups
```

Planning docs, from the product gates to the slice log: `docs/plans/` (start with `00-status.md`).

## Tests

```bash
bin/tomabotchi test              # unit and integration (practice brain, stub upstreams), ~2.5 min;
                                 # makes .venv on first use
.venv/bin/pytest -m docker -q    # real pets on the local model: slow on CPU (minutes per turn)
```
