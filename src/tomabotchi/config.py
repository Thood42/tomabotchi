"""Env, platform/tomabotchi.toml and platform/containment.toml, loaded once into typed config.
This is the only place paths are worked out."""
from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass
from datetime import timedelta
from ipaddress import IPv4Network
from pathlib import Path

from . import __version__
from .models import Home, Limits, ModelChoice

AI_ROOT = Path(os.environ.get("AI_ROOT", Path.home() / "Projects" / "ai")).resolve()


@dataclass(frozen=True)
class Config:
    data_dir: Path
    config_file: Path
    port: int
    ollama_url: str
    anthropic_key: str | None
    homes: list[Home]
    models: list[ModelChoice]
    limits_default: Limits
    limits_ceiling: Limits
    max_awake_pets: int
    pet_subnet_pool: IPv4Network
    direct_code_ports: bool
    runtime_version: str
    approval_timeout: timedelta

    @property
    def db_path(self) -> Path:
        return self.data_dir / "tomabotchi.db"

    def pet_dir(self, pet_id: str) -> Path:
        return self.data_dir / "pets" / pet_id

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


def _cpus(quota: str) -> float:
    """containment.toml writes CPU as a systemd quota: "200%" means two cores."""
    return float(str(quota).rstrip("%")) / 100


def _limits(table: dict, fallback: Limits | None = None) -> Limits:
    fb = fallback or Limits(memory="4G", cpus=2.0, pids=256)
    return Limits(
        memory=str(table.get("memory_max", fb.memory)),
        cpus=_cpus(table["cpu_quota"]) if "cpu_quota" in table else fb.cpus,
        pids=int(table.get("tasks_max", fb.pids)),
    )


_UNITS = {"K": 1 << 10, "M": 1 << 20, "G": 1 << 30, "T": 1 << 40}


def _bytes(mem: str) -> int:
    mem = mem.strip().upper()
    return int(float(mem[:-1]) * _UNITS[mem[-1]]) if mem[-1] in _UNITS else int(mem)


def clamp(limits: Limits, ceiling: Limits) -> Limits:
    return Limits(
        memory=limits.memory if _bytes(limits.memory) <= _bytes(ceiling.memory) else ceiling.memory,
        cpus=min(limits.cpus, ceiling.cpus),
        pids=min(limits.pids, ceiling.pids),
    )


def from_dicts(tb: dict, containment: dict, *, data_dir: Path, config_file: Path) -> Config:
    homes = [Home(name=h["name"], slug=h["slug"], image=h["image"],
                  description=h.get("description", ""), default=bool(h.get("default", False)))
             for h in tb.get("homes", [])]
    defaults = [h for h in homes if h.default]
    if len(defaults) != 1:
        raise ValueError(f"tomabotchi.toml needs exactly one default home, found {len(defaults)}")
    models = [ModelChoice(key=m["key"], label=m["label"], provider=m["provider"], model=m["model"])
              for m in tb.get("models", [])]
    server = tb.get("server", {})
    ceiling = _limits(containment.get("ceiling", {}))
    return Config(
        data_dir=data_dir,
        config_file=config_file,
        port=int(os.environ.get("TOMABOTCHI_PORT", server.get("port", 8770))),
        ollama_url=os.environ.get("OLLAMA_BASE_URL", "http://host.docker.internal:11434"),
        anthropic_key=os.environ.get("ANTHROPIC_API_KEY") or None,
        homes=homes,
        models=models,
        limits_default=clamp(_limits(containment.get("defaults", {})), ceiling),
        limits_ceiling=ceiling,
        max_awake_pets=int(containment.get("tomabotchi", {}).get("max_awake_pets", 6)),
        pet_subnet_pool=IPv4Network(tb.get("network", {}).get("pet_subnet_pool", "172.30.0.0/16")),
        direct_code_ports=bool(server.get("direct_code_ports", False)),
        runtime_version=__version__,
        approval_timeout=parse_duration(server.get("approval_timeout", "8h")),
    )


def load() -> Config:
    config_file = Path(os.environ.get("TOMABOTCHI_CONFIG", AI_ROOT / "platform" / "tomabotchi.toml"))
    containment_file = Path(os.environ.get("TOMABOTCHI_CONTAINMENT", AI_ROOT / "platform" / "containment.toml"))
    data_dir = Path(os.environ.get("TOMABOTCHI_DATA_DIR", AI_ROOT / "platform" / "data" / "tomabotchi"))
    with config_file.open("rb") as f:
        tb = tomllib.load(f)
    containment = {}
    if containment_file.exists():
        with containment_file.open("rb") as f:
            containment = tomllib.load(f)
    data_dir.mkdir(parents=True, exist_ok=True)
    return from_dicts(tb, containment, data_dir=data_dir, config_file=config_file)
