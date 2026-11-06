# Contributing to tomabotchi

Thanks for your interest! Bug reports, ideas and pull requests are all welcome.

## Reporting bugs and ideas

Open an issue. For bugs, include:

- what you did, what you expected, and what happened
- your OS, Docker version, and which brain/model the pet was using
- the server log around the problem (`bin/tomabotchi logs`)

Please check that logs and screenshots don't contain your `ANTHROPIC_API_KEY` or other secrets.

## Development setup

You need Python 3.11+ and Docker. Ollama is optional, because the **practice brain** runs scripted
pets with no model and no Docker.

```bash
git clone https://github.com/Thood42/tomabotchi.git
cd tomabotchi
bin/tomabotchi test        # makes .venv on first use, then runs the unit and integration tests
```

To run the server from your checkout without Docker, with practice pets only:

```bash
TOMABOTCHI_DOCKER=0 .venv/bin/tomabotchi serve    # then open http://127.0.0.1:8770
```

## Tests

```bash
bin/tomabotchi test              # unit and integration (practice brain, stub upstreams), ~2.5 min
.venv/bin/pytest -m docker -q    # real pets on the local model: slow on CPU (minutes per turn)
```

Please make sure `bin/tomabotchi test` passes before opening a pull request, and add tests for new
behaviour.

## Code layout

```
bin/tomabotchi       start, stop, logs and tests
compose.yml          the server (the repo is mounted at its host path, data/ read-write); --profile ollama adds Ollama
docker/              the server's entrypoint (joins the socket's group, drops to uid 1000) and the Ollama model pull
tomabotchi.toml      homes, models and pet limits
data/                git-ignored: tomabotchi.db, pets/<id>/{workspace,brain}, archive/*.zip
src/tomabotchi/      the server:
  web.py             pages, actions, SSE
  fleet.py           pet lifecycle, container stats poller
  supervisor.py      one ACP client per pet
  gateway.py         model proxy and metering
  codeproxy.py       VS Code in the page
  brains.py          Hermes and Claude Code brains
  rules.py           tool rules (shared with the leash)
  soul.py, lessons.py, archive.py
  skills.py          the skill store (+ skill_store/)
  store.py           SQLite (+ schema.sql)
  views.py           page and partial contexts
  timeline.py        a pet's recent work as blocks
  vitals.py          scene and readout
  bus.py             live updates
  templates/         Jinja pages and the partials swapped live
  static/            shell.css (tokens and parts), desk, console and page styles, app.js, desk.js, vendor/
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

## Pull requests

- Keep each PR focused on one change, and explain the why in the description.
- Match the existing style: small modules, server-rendered HTML with htmx, no frontend build step.
- By contributing, you agree that your contributions are licensed under the [MIT License](LICENSE).
