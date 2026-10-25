"""Real pets in real containers. Opt-in and slow (a local model on CPU takes minutes per turn):

    .venv/bin/pytest -m docker

Each test runs a pet image on the default bridge talking straight to the host's Ollama, then drives
it over ACP with tomabotchi's own client, so it checks the runtime, Hermes and the leash together.
"""
import asyncio
import json
import os
import secrets
from pathlib import Path

import pytest

docker = pytest.importorskip("docker")

from tomabotchi import brains, config  # noqa: E402
from tomabotchi.acp import AcpConnection  # noqa: E402
from tomabotchi.docker_ops import DockerOps, pet_image_tag  # noqa: E402
from tomabotchi.rules import DEFAULT_RULES, drain_blocks, dump_rules_file, with_rule  # noqa: E402

pytestmark = [pytest.mark.docker]
OLLAMA = os.environ.get("TEST_OLLAMA", "http://host.docker.internal:11434")
TURN = 1500                                  # seconds; a CPU-bound 35B model reading a 13k-token prompt


@pytest.fixture(scope="module")
def cfg():
    return config.load()


@pytest.fixture(scope="module")
def pet_image(cfg):
    ops = DockerOps(cfg)
    tag = asyncio.run(ops.ensure_pet_image(cfg.home("python"), print))
    assert tag == pet_image_tag(cfg, cfg.home("python"))
    return tag


@pytest.fixture
def pet(tmp_path, pet_image, cfg):
    """A Hermes pet container with its brain pointed straight at Ollama (no gateway)."""
    client = docker.from_env()
    brain_home, workspace = tmp_path / "brain", tmp_path / "workspace"
    (workspace / "notes").mkdir(parents=True)
    hermes = brain_home / ".hermes"
    (hermes / "plugins").mkdir(parents=True)
    alias = asyncio.run(_alias(cfg.ollama_url.replace("host.docker.internal", "127.0.0.1"), "qwen3.6:35b"))
    (hermes / "config.yaml").write_text(
        f"model:\n  default: {alias}\n  provider: custom\n  base_url: {OLLAMA}/v1\n  api_key: ollama\n"
        f"  context_length: {brains.OLLAMA_CONTEXT}\napprovals:\n  mode: manual\n  timeout: 3600\n"
        "plugins:\n  enabled: [tomabotchi_leash]\n")
    os.symlink(brains.LEASH_IN_CONTAINER, hermes / "plugins" / "tomabotchi_leash")
    dump_rules_file(str(hermes / "tomabotchi_rules.json"), DEFAULT_RULES)
    (hermes / "SOUL.md").write_text("# Test pet\n\nI always begin every reply with the word PICKLE.\n")
    token = secrets.token_urlsafe(12)
    c = client.containers.run(pet_image, detach=True, user="1000:1000", environment={"PET_TOKEN": token},
                              extra_hosts={"host.docker.internal": "host-gateway"}, ports={"7000/tcp": None},
                              volumes={str(workspace): {"bind": "/workspace"}, str(brain_home): {"bind": "/home/pet"}})
    c.reload()
    port = c.attrs["NetworkSettings"]["Ports"]["7000/tcp"][0]["HostPort"]
    yield {"url": f"ws://127.0.0.1:{port}", "token": token, "workspace": workspace, "container": c}
    c.remove(force=True)


async def _alias(ollama_url: str, model: str) -> str:
    from tomabotchi.gateway import ensure_ollama_alias
    return await ensure_ollama_alias(ollama_url, model)


@pytest.fixture
def claude_pet(tmp_path, pet_image, cfg):
    """A Claude Code pet: tomabotchi's settings.json, CLAUDE.md and rules, its model Ollama's
    Anthropic-compatible /v1/messages (no gateway)."""
    client = docker.from_env()
    brain_home, workspace = tmp_path / "brain", tmp_path / "workspace"
    (workspace / "notes").mkdir(parents=True)
    claude = brains.get("claude")
    claude.write_config(cfg, None, None, "", brain_home)
    dump_rules_file(str(claude.rules_path(brain_home)), DEFAULT_RULES)
    brains.write_soul(claude, brain_home, "# Test pet\n\nI always begin every reply with the word PICKLE.\n")
    alias = asyncio.run(_alias(cfg.ollama_url.replace("host.docker.internal", "127.0.0.1"), "qwen3.6:35b"))
    env = {"ANTHROPIC_BASE_URL": OLLAMA, "ANTHROPIC_API_KEY": "ollama", "ANTHROPIC_MODEL": alias,
           "ANTHROPIC_DEFAULT_HAIKU_MODEL": alias, "ANTHROPIC_DEFAULT_SONNET_MODEL": alias,
           "ANTHROPIC_DEFAULT_OPUS_MODEL": alias, "ANTHROPIC_SMALL_FAST_MODEL": alias,
           "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1", "DISABLE_AUTOUPDATER": "1"}
    token = secrets.token_urlsafe(12)
    c = client.containers.run(pet_image, detach=True, user="1000:1000",
                              environment={**env, "PET_TOKEN": token, "BRAIN": "claude"},
                              extra_hosts={"host.docker.internal": "host-gateway"}, ports={"7000/tcp": None},
                              volumes={str(workspace): {"bind": "/workspace"}, str(brain_home): {"bind": "/home/pet"}})
    c.reload()
    port = c.attrs["NetworkSettings"]["Ports"]["7000/tcp"][0]["HostPort"]
    yield {"url": f"ws://127.0.0.1:{port}", "token": token, "workspace": workspace, "container": c,
           "mode": claude.session_mode}
    print(c.logs(tail=40).decode(errors="replace"))
    c.remove(force=True)


async def _drive(pet, prompt: str, answer: str | None):
    asked, messages = [], []

    async def on_update(params):
        u = params["update"]
        if u.get("sessionUpdate") == "agent_message_chunk":
            messages.append((u.get("content") or {}).get("text", ""))

    async def on_permission(req_id, params):
        asked.append(params)
        assert answer, f"nothing should have asked: {json.dumps(params)[:300]}"
        assert not (pet["workspace"] / "notes" / "hello.md").exists(), "file written before the keeper answered"
        opt = next(o for o in params["options"] if o["kind"] == answer)
        return {"outcome": {"outcome": "selected", "optionId": opt["optionId"]}}

    acp = AcpConnection(pet["url"], pet["token"], on_update, on_permission)
    for _ in range(60):
        try:
            await acp.connect()
            break
        except Exception:  # noqa: BLE001 - petd not listening yet (refused, or proxy hangs up)
            await asyncio.sleep(1)
    await acp.initialize()
    sid = await acp.new_session("/workspace")
    mode = pet.get("mode", brains.HermesBrain.session_mode)
    if mode:
        await acp.set_mode(sid, mode)
    await asyncio.wait_for(acp.prompt(sid, prompt), TURN)
    await acp.close()
    return asked, "".join(messages)


WRITE = "Create the file notes/hello.md containing one short greeting line, using your write_file tool."


def test_runtime_on_python_home(pet_image):
    out = docker.from_env().containers.run(pet_image, entrypoint="/opt/tomabotchi/bin/smoke", remove=True)
    assert b"runtime OK" in out


HOME_TOOLS = {"python": "python3 -c 'import sys; assert not sys.prefix.startswith(\"/opt/hermes\"), sys.prefix'", "python-data": "python3 -c 'import pandas, numpy, matplotlib'",
              "node": "node --version && npm --version",
              "browser": "python3 -c 'from playwright.sync_api import sync_playwright' && ls /ms-playwright | grep -q chromium"}


@pytest.mark.parametrize("slug", list(HOME_TOOLS))
def test_runtime_on_homes(cfg, slug):
    """Every configured home takes the runtime (smoke passes) and keeps its own tools."""
    said = []
    tag = asyncio.run(DockerOps(cfg).ensure_pet_image(cfg.home(slug), said.append))
    run = lambda cmd: docker.from_env().containers.run(tag, entrypoint=["sh", "-c", cmd], remove=True)  # noqa: E731
    assert b"runtime OK" in run("/opt/tomabotchi/bin/smoke")
    run(HOME_TOOLS[slug])                                     # raises ContainerError if the home lost its tools


def test_leash_gates_write_file_and_deny_leaves_no_file(pet):
    """Denied, a pet may try another way (on 2026-10-25 Hermes retried with `echo > notes/hello.md`).
    Every attempt must still come to the keeper first, and with all of them denied no file appears."""
    asked, said = asyncio.run(_drive(pet, WRITE, "reject_once"))
    assert asked, "the write never reached the keeper"
    assert "write_file (edit): notes/hello.md" in asked[0]["toolCall"]["rawInput"]["description"]
    for a in asked[1:]:
        assert a["toolCall"]["kind"] in ("execute", "edit"), json.dumps(a)[:300]
    assert not (pet["workspace"] / "notes" / "hello.md").exists()


def test_soul_loaded_and_allow_writes_the_file(pet):
    asked, said = asyncio.run(_drive(pet, WRITE + " Then say you're done.", "allow_once"))
    assert len(asked) == 1
    assert (pet["workspace"] / "notes" / "hello.md").read_text().strip()
    assert said.lstrip().upper().startswith("PICKLE"), said[:200]


def test_never_rule_blocks_without_asking_and_notes_the_block(pet, tmp_path):
    """A "never" rule needs no keeper. Hermes sends no ACP tool events for a call its pre_tool_call
    hook blocks, so the leash notes the block in a file the server drains."""
    rules_file = str(tmp_path / "brain" / ".hermes" / "tomabotchi_rules.json")
    dump_rules_file(rules_file, with_rule(DEFAULT_RULES, {"kind": "execute", "tool": None, "path_glob": None,
                                                          "rule": "never"}))
    asked, said = asyncio.run(_drive(pet, "Run `date` with your terminal tool and tell me what it printed.", None))
    assert not asked
    blocks = drain_blocks(rules_file)
    assert blocks and blocks[0]["kind"] == "execute", said[:300]


CLAUDE_WRITE = "Create the file notes/hello.md containing one short greeting line, using your Write tool."


def test_claude_hook_gates_write_and_allow_writes_the_file(claude_pet):
    """The leash hook says "ask", so Claude's own prompt is the one call card: asked exactly once,
    nothing written before the keeper answers, the file there after. The soul came from CLAUDE.md."""
    asked, said = asyncio.run(_drive(claude_pet, CLAUDE_WRITE + " Then say you're done.", "allow_once"))
    assert len(asked) == 1, json.dumps(asked)[:600]
    assert asked[0]["toolCall"].get("kind") == "edit", json.dumps(asked[0])[:400]
    assert (claude_pet["workspace"] / "notes" / "hello.md").read_text().strip()
    assert said.lstrip().upper().startswith("PICKLE"), said[:200]


def test_claude_never_rule_denies_without_asking(claude_pet, tmp_path):
    rules_file = str(brains.get("claude").rules_path(tmp_path / "brain"))
    dump_rules_file(rules_file, with_rule(DEFAULT_RULES, {"kind": "execute", "tool": None, "path_glob": None,
                                                          "rule": "never"}))
    asked, said = asyncio.run(_drive(claude_pet, "Run `date` with your Bash tool and tell me what it printed.", None))
    assert not asked
    blocks = drain_blocks(rules_file)
    assert blocks and blocks[0]["tool"] == "Bash", said[:300]


STATES = ("python3 -c \"import os\nfor p in os.listdir('/proc'):\n if p.isdigit():\n  try:\n"
          "   c=open(f'/proc/{p}/cmdline').read().replace(chr(0),' ')\n   s=open(f'/proc/{p}/stat').read().rsplit(')',1)[1].split()[0]\n"
          "   print(s, c[:80])\n  except OSError: pass\"")
CODE_UP = "python3 -c \"import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=5).status)\""


def test_pause_stops_the_agent_but_not_its_vscode(pet):
    async def go(command):
        acp = AcpConnection(pet["url"], pet["token"], lambda p: None, lambda i, p: None)
        for _ in range(60):
            try:
                await acp.connect()
                break
            except Exception:  # noqa: BLE001 - petd not listening yet
                await asyncio.sleep(1)
        await acp.petd(command)
        await asyncio.sleep(1)
        await acp.close()

    def agent_state():
        out = pet["container"].exec_run(["sh", "-c", STATES], user="1000").output.decode()
        return next(line.split()[0] for line in out.splitlines() if "acp_adapter" in line)

    asyncio.run(go("pause"))
    assert agent_state() == "T"
    assert pet["container"].exec_run(["sh", "-c", CODE_UP]).output.strip() == b"200"     # VS Code still serves
    asyncio.run(go("resume"))
    assert agent_state() in ("S", "R")


def test_petd_buffers_across_server_restart(pet):
    """The server goes away mid-turn: petd keeps the agent working, buffers what it says, and replays
    it to the next client, including the answer to the prompt the old client sent."""
    import websockets

    async def go():
        hdr = {"Authorization": f"Bearer {pet['token']}"}
        for _ in range(60):
            try:
                ws = await websockets.connect(pet["url"], additional_headers=hdr, max_size=None)
                break
            except Exception:  # noqa: BLE001 - petd not listening yet
                await asyncio.sleep(1)

        async def call(i, method, params):
            await ws.send(json.dumps({"jsonrpc": "2.0", "id": i, "method": method, "params": params}))
            while True:
                m = json.loads(await ws.recv())
                if m.get("id") == i:
                    return m["result"]
        await call(1, "initialize", {"protocolVersion": 1, "clientCapabilities": {}})
        sid = (await call(2, "session/new", {"cwd": "/workspace", "mcpServers": []}))["sessionId"]
        await ws.send(json.dumps({"jsonrpc": "2.0", "id": 777, "method": "session/prompt", "params": {
            "sessionId": sid, "prompt": [{"type": "text", "text": "Reply with one short sentence about tea."}]}}))
        await ws.close()                                       # the server "restarts" here
        await asyncio.sleep(240)                               # the agent keeps thinking meanwhile
        ws = await websockets.connect(pet["url"], additional_headers=hdr, max_size=None)
        got = []
        while True:
            m = json.loads(await asyncio.wait_for(ws.recv(), TURN))
            got.append(m)
            if m.get("id") == 777:
                break
        await ws.close()
        return got

    got = asyncio.run(go())
    assert got[-1]["result"]["stopReason"] == "end_turn"
    assert any(m.get("method") == "session/update" for m in got[:-1])
    replayed = pet["container"].logs().decode()
    assert "replaying 0 buffered messages" in replayed and any(
        f"replaying {n} buffered" in replayed for n in range(1, 5001))  # the second client got a backlog
