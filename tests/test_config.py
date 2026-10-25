from datetime import timedelta
from pathlib import Path

import pytest

from tomabotchi.config import AI_ROOT, clamp, from_dicts, parse_duration
from tomabotchi.models import Limits

HOMES = [{"slug": "python", "name": "Python", "image": "python:3.12-slim", "default": True},
         {"slug": "node", "name": "Node.js", "image": "node:22-slim"}]


def cfg(tb=None, containment=None):
    return from_dicts(tb or {"homes": HOMES}, containment or {}, data_dir=Path("/tmp/x"), config_file=Path("x"))


def test_limits_clamped_to_ceiling():
    c = cfg(containment={"defaults": {"cpu_quota": "1200%", "memory_max": "32G", "tasks_max": 4096},
                         "ceiling": {"cpu_quota": "800%", "memory_max": "16G", "tasks_max": 1024}})
    assert c.limits_default == Limits(memory="16G", cpus=8.0, pids=1024)


def test_limits_under_ceiling_kept():
    c = cfg(containment={"defaults": {"cpu_quota": "200%", "memory_max": "4G", "tasks_max": 256},
                         "ceiling": {"cpu_quota": "800%", "memory_max": "16G", "tasks_max": 1024}})
    assert c.limits_default == Limits(memory="4G", cpus=2.0, pids=256)


def test_clamp_compares_memory_units():
    assert clamp(Limits("2048M", 1, 1), Limits("4G", 1, 1)).memory == "2048M"
    assert clamp(Limits("8G", 1, 1), Limits("4096M", 1, 1)).memory == "4096M"


def test_unknown_home_raises():
    with pytest.raises(KeyError):
        cfg().home("alpine")


def test_exactly_one_default_home_required():
    with pytest.raises(ValueError, match="exactly one default"):
        cfg({"homes": [{**h, "default": True} for h in HOMES]})
    with pytest.raises(ValueError, match="exactly one default"):
        cfg({"homes": [{**h, "default": False} for h in HOMES]})


def test_parse_duration():
    assert parse_duration("8h") == timedelta(hours=8)
    assert parse_duration("30m") == timedelta(minutes=30)
    with pytest.raises(ValueError):
        parse_duration("soon")


def test_repo_config_file_loads():
    """The real platform/tomabotchi.toml and containment.toml parse and validate."""
    import tomllib
    tb = tomllib.loads((AI_ROOT / "platform" / "tomabotchi.toml").read_text())
    containment = tomllib.loads((AI_ROOT / "platform" / "containment.toml").read_text())
    c = from_dicts(tb, containment, data_dir=Path("/tmp/x"), config_file=Path("x"))
    assert c.default_home().slug == "python"
    assert c.max_awake_pets == 6
