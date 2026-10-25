from datetime import timedelta
from pathlib import Path

import pytest

from tomabotchi.config import ROOT, from_dicts, parse_duration
from tomabotchi.models import Limits

HOMES = [{"slug": "python", "name": "Python", "image": "python:3.12-slim", "default": True},
         {"slug": "node", "name": "Node.js", "image": "node:22-slim"}]


def cfg(tb=None, pets=None):
    return from_dicts({**(tb or {"homes": HOMES}), "pets": pets or {}}, data_dir=Path("/tmp/x"), config_file=Path("x"))


def test_pet_limits_from_the_pets_table():
    c = cfg(pets={"memory": "8G", "cpus": 4, "pids": 512, "max_awake": 3})
    assert c.limits_default == Limits(memory="8G", cpus=4.0, pids=512) and c.max_awake_pets == 3


def test_pet_limits_default_without_a_pets_table():
    c = cfg()
    assert c.limits_default == Limits(memory="4G", cpus=2.0, pids=256) and c.max_awake_pets == 6


def test_pet_memory_must_be_a_size():
    with pytest.raises(ValueError, match="isn't a size"):
        cfg(pets={"memory": "lots"})


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
    """The repo's own tomabotchi.toml parses and validates."""
    import tomllib
    tb = tomllib.loads((ROOT / "tomabotchi.toml").read_text())
    c = from_dicts(tb, data_dir=Path("/tmp/x"), config_file=Path("x"))
    assert c.default_home().slug == "python"
    assert c.max_awake_pets == 6 and c.limits_default == Limits(memory="4G", cpus=2.0, pids=256)
    assert c.source_dir == ROOT and (ROOT / "runtime" / "Dockerfile").is_file()
