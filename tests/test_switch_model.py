"""Switching a pet's model: config rewritten, home recreated with the new settings, everything kept."""
import asyncio
from types import SimpleNamespace

import pytest
from test_reskin_pages import hatch
from test_web import client  # noqa: F401 - client is a fixture

from tomabotchi import store
from tomabotchi.config import from_dicts
from tomabotchi.fleet import Fleet
from tomabotchi.models import Pet

HOMES = [{"slug": "python", "name": "Python", "image": "python:3.12-slim", "default": True}]
MODELS = [{"key": "local-qwen", "label": "Local", "provider": "ollama", "model": "qwen3.6:35b"},
          {"key": "claude-sonnet", "label": "Claude Sonnet", "provider": "anthropic", "model": "claude-sonnet-5-5"}]


class FakeDocker:
    def __init__(self):
        self.runs = []

    async def ensure_pet_image(self, home, progress):
        return "img"

    async def create_network(self, pet_id):
        return f"net-{pet_id}"

    async def run_pet(self, pet, image, network, token, limits, binds, env):
        self.runs.append({"pet": pet, "env": env, "binds": binds})
        return SimpleNamespace(id="c2")

    async def stats(self, pet_id):
        raise RuntimeError("none")


@pytest.fixture
def setup(tmp_path, conn, bus, monkeypatch):
    def make(anthropic_key="sk-real"):
        cfg = from_dicts({"homes": HOMES, "models": MODELS}, data_dir=tmp_path, config_file=tmp_path / "t.toml")
        cfg = cfg.__class__(**{**cfg.__dict__, "anthropic_key": anthropic_key})
        docker = FakeDocker()
        fleet = Fleet(cfg, conn, bus, docker)
        for t in list(fleet._tasks):
            t.cancel()
        pet = Pet(id="p1", name="Pip", shell="grape", look_seed=1, home="python", brain="hermes", model="local-qwen",
                  state="awake", born_at=store.now())
        store.insert_pet(conn, pet)
        (cfg.pet_dir("p1") / "brain").mkdir(parents=True)
        (cfg.pet_dir("p1") / ".token").write_text("pet-token")
        stopped = []
        fleet._sups["p1"] = SimpleNamespace(busy=False, stop=lambda: asyncio.sleep(0, stopped.append(True)))
        attached = []

        async def attach(pet, token, first_task, reattach=False):
            attached.append(pet.model)
            store.set_pet_state(conn, pet.id, "awake")
        monkeypatch.setattr(fleet, "_attach", attach)
        return fleet, docker, cfg, stopped, attached
    return make


async def test_switching_to_claude_rewrites_config_and_recreates_the_home(setup, conn):
    fleet, docker, cfg, stopped, attached = setup()
    await fleet.switch_model("p1", "claude-sonnet")
    for _ in range(50):
        if attached:
            break
        await asyncio.sleep(0.01)
    assert store.get_pet(conn, "p1").model == "claude-sonnet" and stopped and attached == ["claude-sonnet"]
    config = (cfg.pet_dir("p1") / "brain" / ".hermes" / "config.yaml").read_text()
    assert "provider: anthropic" in config and "claude-sonnet-5-5" in config
    [run] = docker.runs
    assert run["env"]["ANTHROPIC_API_KEY"] == "pet-token" and "sk-real" not in str(run["env"])
    assert set(run["binds"].values()) == {"/workspace", "/home/pet"}          # the same folders, kept
    assert store.get_pet(conn, "p1").state == "awake"
    assert "Now on Claude Sonnet" in store.last_notice(conn, "p1", "model_switched").payload["text"]


async def test_switching_needs_a_key_for_claude_and_an_idle_awake_pet(setup, conn):
    fleet, docker, *_ = setup(anthropic_key=None)
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        await fleet.switch_model("p1", "claude-sonnet")
    fleet.cfg = fleet.cfg.__class__(**{**fleet.cfg.__dict__, "anthropic_key": "k"})
    fleet._sups["p1"].busy = True
    with pytest.raises(ValueError, match="busy"):
        await fleet.switch_model("p1", "claude-sonnet")
    fleet._sups["p1"].busy = False
    store.set_pet_state(conn, "p1", "asleep")
    with pytest.raises(ValueError, match="awake"):
        await fleet.switch_model("p1", "claude-sonnet")
    with pytest.raises(KeyError):
        await fleet.switch_model("p1", "gpt-9")
    assert docker.runs == [] and store.get_pet(conn, "p1").model == "local-qwen"


def test_a_practice_pet_switches_in_place_and_the_inspector_offers_it(client):
    pet_id = hatch(client, "Pip", first_task_md="")
    page = client.get(f"/pets/{pet_id}").text
    assert f'hx-post="/pets/{pet_id}/model"' in page and 'value="local-qwen" selected' in page
    r = client.post(f"/pets/{pet_id}/model", data={"model": "claude-sonnet"})
    if client.app.state.cfg.anthropic_key:
        assert r.status_code == 204 and store.get_pet(client.app.state.conn, pet_id).model == "claude-sonnet"
    else:
        assert r.status_code == 409 and "ANTHROPIC_API_KEY" in r.text
    assert client.post(f"/pets/{pet_id}/model", data={"model": "nope"}).status_code == 400
