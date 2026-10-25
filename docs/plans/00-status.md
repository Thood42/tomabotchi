# Status: tomabotchi

- Gate 1 — Product: APPROVED 2026-10-25
- Gate 2 — Architecture: APPROVED 2026-10-25 (re-approved after the Claude Code brain amendment)
- Gate 3 — Program Design: APPROVED 2026-10-25
- Gate 4 — Slice plan: APPROVED 2026-10-25

## Slices
- [x] Slice 1 — tracer bullet (done 2026-10-25): mocked pet (fake ACP agent) end to end, from hatch to call card to answer to shelf metric, served by compose on :8770
- [ ] Slice 2 — real Hermes pet: runtime and Python-home images, petd, OpenAI gateway route, leash gates write_file, pet-subnet guard
- [ ] Slice 3 — live view from real data: thoughts, tools, plan, hearts and scene, sick detection, Energy, Home stats
- [ ] Slice 4 — tool rules: defaults, editor, allow-always narrowing, never, dangerous commands forced to ask
- [ ] Slice 5 — Claude Code brain: selection, ClaudeBrain config, PreToolUse leash, Anthropic gateway route (Claude API and Ollama /v1/messages)
- [ ] Slice 6 — soul and lessons: Markdown editor and preview, revisions, deny-reason lessons, teach and praise, timeline
- [ ] Slice 7 — workspace: code-server proxy embed, new-tab and direct-port fallbacks, pause while editing
- [ ] Slice 8 — care actions and budget: feed, chat, lights (docker pause), medicine, tidy, out-of-budget sleep
- [ ] Slice 9 — homes: tomabotchi.toml list, per-home builds with egg progress, Node and Playwright smoke tests, settings homes list
- [ ] Slice 10 — notifications: opt-in, switches, quiet hours, test beep, background-tab notifications
- [ ] Slice 11 — retire and memory shelf: verified zip archive, memory page, download, hatch from soul
- [ ] Slice 12 — resilience: server restart reattach, petd replay, pending calls survive, clear error states
- [ ] Slice 13 — polish and docs: shuffle, phone layouts, focus, README, final screenshot pass, full test run

## Notes for a fresh session
- Code, config and data live under `~/Projects/ai/platform/` (`platform/tomabotchi/`, `platform/tomabotchi.toml`, `platform/data/tomabotchi/`). This folder (`sessions/tomabotchi/docs/plans/tomabotchi/`) holds only the planning docs. Repo: `~/Projects/ai` (local agentic platform).
- Existing platform pieces worth considering at Gate 2 (do not decide before then):
  - aihub (`platform/hub`, FastAPI+HTMX, :8765): contained runs as systemd --user units in `aihub.slice`; README lists "human approval gates" as not built yet.
  - Langfuse (docker, :3000, v4 with OTLP ingestion): traces and token usage.
  - Ollama (system service, :11434), default local model `qwen3.6:35b`.
  - `agents/soul-cluster` already reads and rewrites SOUL.md files.
  - Everything binds to 127.0.0.1.
- The user asked for an architecture recommendation (they named "herder" and "hermes", plus other popular multi-agent tools, and Docker). That's Gate 2: research Hermes Agent (Nous Research) and alternatives, and confirm what "herder" refers to.
- User decision, 2026-10-25: **each agent runs in its own Docker container.** The default is a basic Python image. The images offered in the hatch dropdown come from a configured list in the backend (config, not UI). Gate 2 must design this: image list config, per-pet container lifecycle, resource limits, workspace volume, network policy.
- User decision, 2026-10-25: **pet looks are randomised.** `mockups/kit.js` has a seeded generator (`genome(seed)` → body shape, topper, eyes, feet, arms, mouth; `creature(seed, mood)` → 16×16 pixel map for happy/work/alert/sleep/sick). A pet stores its seed, and the hatch screen shows six rolls with a Shuffle button. The real build can port this generator directly.
- User decision, 2026-10-25: **embedded VS Code per pet workspace.** Prefer embedding it in the dashboard, with a fallback link that opens it directly from the host. Gate 2 should evaluate code-server or openvscode-server (one per pet container, or one shared server opening each pet's workspace volume), iframe embedding (headers, auth, same-origin) and the host port or URL for the fallback.
- User decision, 2026-10-25: **v1 is new (hatched) agents only.** Adopting existing agents or sessions is v2.
- User decision, 2026-10-25: **no death.** The user retires a pet explicitly (Retire button plus confirmation). The pet's workspace volume is zipped into an archive folder inside the platform's main volume. Retired pets get a separate memory-shelf UI (download backup, read soul, hatch from soul).
- User decision, 2026-10-25: **browser notifications**, opt-in through the browser permission prompt, with a master switch, per-event switches and quiet hours.
- Gate 2 research, 2026-10-25: "herder" most likely means **Herdr**, an AGPL Rust terminal multiplexer for Claude Code and Codex with agent-state tracking and a socket API. There is also the "Agent Herder" MCP server. Neither fits v1; both are candidates for v2 "adopt existing sessions". The recommended stack is in 02-architecture.md: Hermes Agent as the default brain, talking ACP to tomabotchi, with each pet in its own container.
- Visual direction for the mockups: a 1997 toy shelf. Translucent candy-plastic egg devices, a grey-green LCD with ghost pixels, Baloo 2 type, lilac halftone shelf, indigo ink, alarm red only for "calling you".
- User decisions at the Gate 3 review, 2026-10-25:
  - **Claude Code is a second brain, chosen per pet** (Hermes stays the default). It runs through `@zed-industries/claude-agent-acp`.
  - **Native approvals are off in both brains.** The leash is the single gate: a Hermes `pre_tool_call` plugin, and a Claude `PreToolUse` hook that returns allow/deny. Claude stays in default permission mode, never `bypassPermissions`.
  - **`docker pause` for Lights off.**
  - **The in-house model gateway** (not LiteLLM). It has OpenAI and Anthropic routes, and Ollama ≥ 0.14 serves `/v1/messages`; this host runs 0.33.3.
- Slice 1 implementation notes (2026-10-25):
  - **Running:** `platform/bin/stack up tomabotchi` builds and starts only the tomabotchi service on `127.0.0.1:8770`, without touching Langfuse or Open WebUI. `bin/stack` now exports `AI_ROOT`, creates `platform/data/tomabotchi/`, and accepts `up [svc]` (always `--build`).
  - **Tests:** `cd platform/tomabotchi && .venv/bin/pytest -q` gives 53 passed. uv isn't installed on this host, so the venv was made with `python3 -m venv` + `pip install -e '.[dev]'`. The pyproject stays uv-compatible. Docker tests are opt-in with `-m docker`.
  - **Deviations from 03:**
    - Added `fake_agent.py`, a scripted ACP agent over WebSocket. It is the "practice brain" (`brain="fake"`), used by the tracer and the tests.
    - Added `views.py`, a shared Jinja env and view models, so SSE partials and full pages render the same context.
    - SSE is handled by `static/app.js` with a plain EventSource, swapping by element id, instead of htmx-ext-sse. htmx is still used for the call form.
    - The supervisor takes `(url, token)` and builds its own AcpConnection, avoiding a circular handler wiring.
  - **Practice pets on restart:** their agent lived in the old process, so on boot their unanswered calls are marked `cancelled` and the pet wakes into a new session.
  - **Screenshots:** use `chromium --headless=new --timeout=3500 --screenshot=…`, not `--virtual-time-budget`. The open SSE stream stops virtual time, so the shot never finishes.
  - **Known nit for slice 3:** a finished task's plate still shows its last step progress, for example "step 2 of 3" after a deny.
