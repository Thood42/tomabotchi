# Slices: tomabotchi

Build order. Each slice ends in something running that you can see, plus its tests from
03 § Test plan. "Proof" says how the slice is demonstrated before it's checked off.

1. **Tracer bullet: mocked pet, end to end.** The compose service is up on `127.0.0.1:8770` through `platform/bin/stack`, with the shelf and pet pages ported from the mockups. Hatching creates a pet backed by an in-process *fake ACP agent* that plays a script (thoughts, a tool call, a permission request). The call card appears over SSE, Allow or Deny answers it, and the wait time shows on the shelf. No Docker pets yet.
   *Proof:* `stack up`, hatch in the browser, answer the call, screenshot. `curl /events` shows the stream. Unit tests for rules, vitals and notify, and the call-flow integration tests, are green.
2. **A real Hermes pet on the happy path.** The runtime image and the Python home image are built, with petd running `hermes acp` plus code-server. The model gateway's OpenAI route goes to local Ollama with metering. The leash plugin gates `write_file`: a real call card, Allow, and the file appears. The pet-subnet guard is in place. This retires Gate 2 risks 1, 2 and 4 (Python home).
   *Proof:* hatch "Biscuit" with "write hello.md", approve it, and `cat` the file on the host. `pytest -m docker`: leash gates write_file, soul loaded in ACP, runtime on the Python home.
3. **The live view tells the truth.** Thoughts (ACP chunks plus the gateway fallback), Tools used, plan steps, hearts and LCD scene from real data, sick detection, Energy from metered tokens, and Home panel stats from the Docker API.
   *Proof:* a multi-step research task fills every panel, and a forced failure loop turns the pet sick on the shelf. Screenshots.
4. **Tool rules.** Default rules at hatch, the rules editor on the pet screen, "Always allow in notes/" adding a narrow rule, "never" blocking with a message the agent sees, and dangerous commands forced to ask.
   *Proof:* the same write is asked about, then auto-allowed after Always. A "never" tool is blocked and the pet says so. Rules unit tests and `test_allow_always_adds_rule…`.
5. **Claude Code brain.** Brain and model selection at hatch, `ClaudeBrain` config and CLAUDE.md, the leash `PreToolUse` hook, and the gateway's Anthropic route to the Claude API and to Ollama `/v1/messages`. This retires risk 5.
   *Proof:* a Claude-brain pet on Sonnet and one on the local model each write a file after one leash call, with no native prompt. Claude docker tests are green.
6. **Soul and lessons.**
   - Soul page with a Markdown editor (Write/Preview) and revisions.
   - Denying with a reason drafts a lesson. Accepting it rewrites SOUL.md or CLAUDE.md, and the next task uses it.
   - Teach and praise also draft lessons, and the "What shaped this soul" timeline shows them.
   - Markdown fields on the hatch screen.

   *Proof:* deny with a reason, accept the lesson, run a new task, and the behaviour changes (PICKLE-style check). Screenshots of the timeline.
7. **Workspace in VS Code.** The code-server HTTP and WebSocket proxy is embedded in the Workspace tab, with "Open in a new tab" and the optional direct host port. "Pause while I edit" pauses the pet. This retires risk 3.
   *Proof:* edit a file in the embedded editor, and the pet sees the change. `test_codeproxy…` is green, plus screenshots.
8. **Care actions and budget.** Feed a task, Chat, Lights off and on (`docker pause`), Medicine, Tidy, plus budget enforcement: an out-of-budget pet falls asleep with a notice.
   *Proof:* pause mid-task and resume it. Unstick a sick pet. A tiny budget puts the pet to sleep.
9. **Homes.** The `tomabotchi.toml` homes list, per-home image builds with progress shown on the hatching egg, the Node and Playwright homes passing smoke tests, and the Settings page's read-only Homes list.
   *Proof:* hatch one pet on each home. `test_runtime_on_homes` is green.
10. **Notifications.** The opt-in banner, the settings switches, quiet hours, and a test beep. Notifications for calling, sick, finished and out-of-budget fire from a backgrounded tab.
    *Proof:* with the tab in the background, a call produces a desktop notification. Quiet hours suppress it. Notify unit tests are green.
11. **Retire and the memory shelf.** The Retire dialog stops the container, removes the network, writes a verified zip to `archive/` and cleans up. The memory shelf lists retired pets, offers the backup download, and "Hatch a new pet from this soul" prefills the hatch screen.
    *Proof:* retire Biscuit, unzip the backup and diff it against the workspace, then re-hatch from the soul. Archive tests are green.
12. **Resilience.** On a server restart the server reattaches to running pets, petd replays its buffer, and pending calls reappear without duplicates. Error states are clear: Docker down, Ollama down, or an image build failing.
    *Proof:* restart the server container mid-task while a call is pending, and the call is still answerable. `test_petd_buffers_across_server_restart` is green.
13. **Polish and docs.** The hatch shuffle and look persistence, phone layouts, keyboard focus, README rows and "Daily use", `.gitignore`, and a final screenshot pass of every screen against the mockups.
    *Proof:* screenshots at desktop and 390 px widths for all 7 screens. A full `pytest` run (unit, integration, docker) is green.
