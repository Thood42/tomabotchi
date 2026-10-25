"""Slice 9: homes from tomabotchi.toml, fetched or built on first hatch, listed on the Settings page."""
from types import SimpleNamespace

import pytest
from test_web import client  # noqa: F401 - client is a fixture

from tomabotchi import config
from tomabotchi.docker_ops import DockerOps
from tomabotchi.models import Home

NODE = Home(name="Node.js", slug="node", image="node:22-slim", description="", default=False)
LOCAL_REGISTRY = Home(name="Mine", slug="mine", image="localhost:5000/tools", description="", default=False)
DATA = Home(name="Data", slug="data", image="tomabotchi/python-data:1", description="", default=False,
            build="runtime/homes/python-data")


class FakeApi:
    def __init__(self, chunks):
        self.chunks, self.pulled, self.built = chunks, [], []

    def pull(self, repo, tag, stream, decode):
        self.pulled.append((repo, tag))
        return iter(self.chunks)


def ops(cfg, chunks=()):
    o = object.__new__(DockerOps)
    o.cfg, o.client = cfg, SimpleNamespace(api=FakeApi(list(chunks)))
    return o


def layer(i, current, total):
    return {"id": f"l{i}", "status": "Downloading", "progressDetail": {"current": current, "total": total}}


def test_pull_reports_progress_in_steps(cfg):
    said = []
    o = ops(cfg, [layer(1, 0, 50_000_000), layer(2, 0, 50_000_000), layer(1, 25_000_000, 50_000_000),
                  layer(1, 50_000_000, 50_000_000), layer(2, 50_000_000, 50_000_000)])
    o._pull(NODE, said.append)
    assert o.client.api.pulled == [("node", "22-slim")]
    assert said == ["Fetching the Node.js home: 25% of 100 MB", "Fetching the Node.js home: 50% of 100 MB",
                    "Fetching the Node.js home: 100% of 100 MB"]


def test_pull_names_the_tag_and_registry_port_correctly(cfg):
    o = ops(cfg)
    o._pull(LOCAL_REGISTRY, lambda s: None)
    assert o.client.api.pulled == [("localhost:5000/tools", "latest")]


def test_pull_failure_is_explained(cfg):
    with pytest.raises(RuntimeError, match="couldn't fetch node:22-slim: manifest unknown"):
        ops(cfg, [{"error": "manifest unknown"}])._pull(NODE, lambda s: None)


async def test_a_home_with_a_dockerfile_is_built_not_pulled(cfg, monkeypatch):
    o, built, said = ops(cfg), [], []

    async def missing(tag):
        return False
    monkeypatch.setattr(o, "_has_image", missing)
    monkeypatch.setattr(o, "_build", lambda **kw: built.append(kw))
    await o.ensure_home(DATA, said.append)
    assert built[0]["tag"] == "tomabotchi/python-data:1"
    assert built[0]["path"] == str(cfg.source_dir / "runtime/homes/python-data")
    assert said == ["Building the Data home (first time only)"] and not o.client.api.pulled


def test_config_homes_have_one_default_and_a_built_data_home():
    cfg = config.load()
    assert [h.slug for h in cfg.homes if h.default] == ["python"]
    data = cfg.home("python-data")
    assert data.build and (cfg.source_dir / data.build / "Dockerfile").is_file()


def test_settings_lists_homes_read_only(client):
    page = client.get("/settings").text
    for name in ("Python", "Python with data tools", "Node.js", "Web browser"):
        assert f"<b>{name}</b>" in page
    homes = page[page.index('id="homes-h"'):]
    assert "tomabotchi/browser:1" in homes and "<form" not in homes and "<input" not in homes
    assert "Docker isn&#39;t reachable" in page              # TOMABOTCHI_DOCKER=0 in tests
    assert 'href="/settings"' in client.get("/").text


async def test_networks_are_created_one_at_a_time(cfg):
    """Three pets hatching together once picked the same free /28 (Docker: "Pool overlaps")."""
    import asyncio
    import threading
    import time as _time
    o, inside, most = ops(cfg), [0], [0]
    o._net_lock = asyncio.Lock()
    lock = threading.Lock()

    def create(name):
        with lock:
            inside[0] += 1
            most[0] = max(most[0], inside[0])
        _time.sleep(0.05)
        with lock:
            inside[0] -= 1
        return name
    o._create_network = create
    await asyncio.gather(*(o.create_network(f"p{i}") for i in range(3)))
    assert most[0] == 1
