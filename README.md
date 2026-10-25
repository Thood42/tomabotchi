# tomabotchi

Pocket pets for AI agents. Each pet is an agent with its own Docker container, its own soul
(SOUL.md or CLAUDE.md), and a toy-shelf dashboard where you watch it think, answer its calls
for tools, teach it lessons, and open its workspace in VS Code.

http://127.0.0.1:8770 (localhost only).

## Run it

```bash
platform/bin/stack up tomabotchi        # build and start the server container
platform/bin/stack logs tomabotchi      # follow its log
```

Hatch a pet at **Hatch a pet**. The first hatch on a home builds the pet runtime and that home's
image, which takes a few minutes. Later hatches take seconds.

- **Brains:** Hermes (the default) or Claude Code, chosen per pet. Both talk ACP to the server
  through `petd` inside the container.
- **Models:** the local Ollama model, or Claude Sonnet. Sonnet needs `ANTHROPIC_API_KEY`, set in
  `platform/stack/.env` (git-ignored) or the shell that runs `stack up`. The hatch screen says so
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
| Retire it | Retire under Home. Its workspace and brain are zipped to `platform/data/tomabotchi/archive/`, and it moves to the Memory shelf, where you can hatch a new pet from its soul. |

## Config

`platform/tomabotchi.toml` lists the **homes** (base images; `build = "runtime/homes/<slug>"` for
ones tomabotchi builds itself) and the **models**. Edit it and restart the server.

Environment variables: `ANTHROPIC_API_KEY`, `OLLAMA_BASE_URL` (default `http://host.docker.internal:11434`),
`TOMABOTCHI_DATA_DIR`, `TOMABOTCHI_CONFIG`, `TOMABOTCHI_DOCKER=0` (practice pets only).

## Layout

```
src/tomabotchi/      the server: web.py (pages, actions, SSE), fleet.py (pet lifecycle), supervisor.py
                     (one ACP client per pet), gateway.py (model proxy and metering), codeproxy.py
                     (VS Code), brains.py, rules.py (shared with the leash), soul.py, lessons.py,
                     archive.py, store.py + schema.sql
runtime/             the pet runtime image: petd, the leash (a Hermes plugin and a Claude hook),
                     smoke test, pet.Dockerfile.tmpl, homes/<slug>/Dockerfile
tests/               unit and integration tests; tests/test_docker_pets.py needs Docker and Ollama
```

Planning docs, from the product gates to the slice log: `sessions/tomabotchi/docs/plans/tomabotchi/`.

## Tests

```bash
cd platform/tomabotchi
.venv/bin/pytest -q              # unit and integration (practice brain, stub upstreams), ~2.5 min
.venv/bin/pytest -m docker -q    # real pets on the local model: slow on CPU (minutes per turn)
```
