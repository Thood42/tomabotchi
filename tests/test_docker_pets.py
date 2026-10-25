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
from tomabotchi.rules import DEFAULT_RULES, dump_rules_file  # noqa: E402

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


async def _drive(pet, prompt: str, answer: str | None):
    asked, messages = [], []

    async def on_update(params):
        u = params["update"]
        if u.get("sessionUpdate") == "agent_message_chunk":
            messages.append((u.get("content") or {}).get("text", ""))

    async def on_permission(req_id, params):
        asked.append(params)
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
    await acp.set_mode(sid, brains.HermesBrain.session_mode)
    await asyncio.wait_for(acp.prompt(sid, prompt), TURN)
    await acp.close()
    return asked, "".join(messages)


WRITE = "Create the file notes/hello.md containing one short greeting line, using your write_file tool."


def test_runtime_on_python_home(pet_image):
    out = docker.from_env().containers.run(pet_image, entrypoint="/opt/tomabotchi/bin/smoke", remove=True)
    assert b"runtime OK" in out


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
