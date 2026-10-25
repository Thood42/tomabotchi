from pathlib import Path

import pytest

from tomabotchi import db
from tomabotchi.bus import Bus
from tomabotchi.config import from_dicts

HOMES = [{"slug": "python", "name": "Python", "image": "python:3.12-slim", "default": True}]
MODELS = [{"key": "local-qwen", "label": "Local", "provider": "ollama", "model": "qwen3.6:35b"}]


@pytest.fixture
def cfg(tmp_path: Path):
    return from_dicts({"homes": HOMES, "models": MODELS}, data_dir=tmp_path, config_file=tmp_path / "t.toml")


@pytest.fixture
def conn(cfg):
    c = db.connect(cfg.db_path)
    yield c
    c.close()


@pytest.fixture
def bus():
    return Bus()
