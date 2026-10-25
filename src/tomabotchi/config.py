"""Env and tomabotchi.toml, loaded once into typed config. This is the only place paths are worked out.

ROOT is the repo checkout: it holds tomabotchi.toml, the runtime image's sources (the Docker build
context for pets) and, by default, data/. In the server container it's mounted at the same absolute
path as on the host and named by TOMABOTCHI_ROOT, so the bind mounts handed to Docker for each pet
resolve on the host."""
from __future__ import annotations

import hashlib
import os
import re
import tomllib
from dataclasses import dataclass
from datetime import timedelta
from ipaddress import IPv4Network
from pathlib import Path

from . import __version__
from .models import Home, Limits, ModelChoice

ROOT = Path(os.environ.get("TOMABOTCHI_ROOT", Path(__file__).resolve().parents[2])).resolve()


@dataclass(frozen=True)
class Config:
    data_dir: Path
    config_file: Path
    port: int
    ollama_url: str
    anthropic_key: str | None
    anthropic_url: str            # the real Claude API, which only the gateway talks to
    homes: list[Home]
    models: list[ModelChoice]
    limits_default: Limits
    max_awake_pets: int
    pet_subnet_pool: IPv4Network
    direct_code_ports: bool
    runtime_version: str
    approval_timeout: timedelta
    source_dir: Path              # the repo root: the runtime image's build context

    @property
    def db_path(self) -> Path:
        return self.data_dir / "tomabotchi.db"

    def pet_dir(self, pet_id: str) -> Path:
        return self.data_dir / "pets" / pet_id

    @property
    def archive_dir(self) -> Path:
        return self.data_dir / "archive"

    def home(self, slug: str) -> Home:
        for h in self.homes:
            if h.slug == slug:
                return h
        raise KeyError(f"unknown home {slug!r}")

    def default_home(self) -> Home:
        return next(h for h in self.homes if h.default)

    def model(self, key: str) -> ModelChoice:
        for m in self.models:
            if m.key == key:
                return m
        raise KeyError(f"unknown model {key!r}")


_DURATION = re.compile(r"^(\d+)\s*([smhd])$")


def parse_duration(s: str) -> timedelta:
    m = _DURATION.match(s.strip())
    if not m:
        raise ValueError(f"bad duration {s!r} (use e.g. 30m, 8h)")
    n, unit = int(m[1]), m[2]
    return timedelta(**{{"s": "seconds", "m": "minutes", "h": "hours", "d": "days"}[unit]: n})


_MEMORY = re.compile(r"^\d+(\.\d+)?[KMGT]?$", re.I)


def pet_limits(pets: dict) -> Limits:
    """[pets] in tomabotchi.toml: what each pet's container may use."""
    memory = str(pets.get("memory", "4G"))
    if not _MEMORY.match(memory):
        raise ValueError(f"[pets] memory {memory!r} isn't a size (use e.g. 4G or 512M)")
    return Limits(memory=memory, cpus=float(pets.get("cpus", 2)), pids=int(pets.get("pids", 256)))


def runtime_version(source_dir: Path) -> str:
    """Package version plus a hash of everything baked into pet images, so editing the runtime
    gives new image tags and the next hatch rebuilds instead of reusing a stale home."""
    h = hashlib.sha256()
    files = sorted((source_dir / "runtime").rglob("*")) + [source_dir / "src" / "tomabotchi" / "rules.py"]
    for f in files:
        if f.is_file() and "__pycache__" not in f.parts:
            h.update(str(f.relative_to(source_dir)).encode())
            h.update(f.read_bytes())
    return f"{__version__}-{h.hexdigest()[:8]}" if (source_dir / "runtime").is_dir() else __version__


def from_dicts(tb: dict, *, data_dir: Path, config_file: Path) -> Config:
    homes = [Home(name=h["name"], slug=h["slug"], image=h["image"],
                  description=h.get("description", ""), default=bool(h.get("default", False)), build=h.get("build"))
             for h in tb.get("homes", [])]
    defaults = [h for h in homes if h.default]
    if len(defaults) != 1:
        raise ValueError(f"tomabotchi.toml needs exactly one default home, found {len(defaults)}")
    models = [ModelChoice(key=m["key"], label=m["label"], provider=m["provider"], model=m["model"])
              for m in tb.get("models", [])]
    server = tb.get("server", {})
    source_dir = Path(os.environ.get("TOMABOTCHI_SOURCE", ROOT))
    pets = tb.get("pets", {})
    return Config(
        data_dir=data_dir,
        config_file=config_file,
        port=int(os.environ.get("TOMABOTCHI_PORT", server.get("port", 8770))),
        ollama_url=os.environ.get("OLLAMA_BASE_URL", "http://host.docker.internal:11434"),
        anthropic_key=os.environ.get("ANTHROPIC_API_KEY") or None,
        anthropic_url=os.environ.get("ANTHROPIC_UPSTREAM_URL", "https://api.anthropic.com"),
        homes=homes,
        models=models,
        limits_default=pet_limits(pets),
        max_awake_pets=int(pets.get("max_awake", 6)),
        pet_subnet_pool=IPv4Network(tb.get("network", {}).get("pet_subnet_pool", "172.30.0.0/16")),
        direct_code_ports=bool(server.get("direct_code_ports", False)),
        runtime_version=runtime_version(source_dir),
        approval_timeout=parse_duration(server.get("approval_timeout", "8h")),
        source_dir=source_dir,
    )


def load() -> Config:
    config_file = Path(os.environ.get("TOMABOTCHI_CONFIG", ROOT / "tomabotchi.toml"))
    data_dir = Path(os.environ.get("TOMABOTCHI_DATA_DIR", ROOT / "data"))
    with config_file.open("rb") as f:
        tb = tomllib.load(f)
    data_dir.mkdir(parents=True, exist_ok=True)
    return from_dicts(tb, data_dir=data_dir, config_file=config_file)
