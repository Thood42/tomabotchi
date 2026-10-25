"""Brains: how each kind of pet thinks. The one place that knows a brain's config files, soul file,
ACP session mode and how it reaches the model gateway. Adding an ACP brain is one more class here."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Protocol

from . import rules as rules_mod
from .config import Config
from .models import BrainName, ModelChoice, Pet

GATEWAY = "http://tomabotchi:8770/llm"
LEASH_IN_CONTAINER = "/opt/tomabotchi/leash/tomabotchi_leash"
OLLAMA_CONTEXT = 65536               # Hermes refuses models under 64k


def ollama_alias(model: str) -> str:
    """The context-widened Ollama model a pet actually talks to (see gateway.ensure_ollama_alias)."""
    return "tomabotchi-" + model.replace(":", "-").replace("/", "-") + "-ctx64k"


def _yaml(value) -> str:
    return json.dumps(value)           # JSON scalars are valid YAML


class Brain(Protocol):
    name: BrainName
    label: str
    session_mode: str | None

    def soul_path(self, brain_home: Path) -> Path: ...
    def rules_path(self, brain_home: Path) -> Path: ...
    def render_soul_file(self, soul_md: str) -> str: ...
    def write_config(self, cfg: Config, pet: Pet, model: ModelChoice, token: str, brain_home: Path) -> list[Path]: ...


class HermesBrain:
    name: BrainName = "hermes"
    label = "Hermes"
    session_mode = "dont_ask"          # Hermes's native edit prompts off; the leash decides

    def soul_path(self, brain_home: Path) -> Path:
        return brain_home / ".hermes" / "SOUL.md"

    def rules_path(self, brain_home: Path) -> Path:
        return brain_home / ".hermes" / "tomabotchi_rules.json"

    def render_soul_file(self, soul_md: str) -> str:
        return soul_md

    def write_config(self, cfg: Config, pet: Pet, model: ModelChoice, token: str, brain_home: Path) -> list[Path]:
        home = brain_home / ".hermes"
        (home / "plugins").mkdir(parents=True, exist_ok=True)
        if model.provider == "ollama":
            model_block = (f"  default: {_yaml(model.model)}\n  provider: custom\n"
                           f"  base_url: {_yaml(f'{GATEWAY}/{token}/v1')}\n  api_key: {_yaml(token)}\n"
                           f"  context_length: {OLLAMA_CONTEXT}\n")
        else:
            model_block = (f"  default: {_yaml(model.model)}\n  provider: anthropic\n"
                           f"  base_url: {_yaml(f'{GATEWAY}/{token}/anthropic')}\n  api_key: {_yaml(token)}\n")
        timeout = int(cfg.approval_timeout.total_seconds())
        config = (
            "# Written by tomabotchi; rewritten on every hatch and rules change. Edit the pet in tomabotchi instead.\n"
            f"model:\n{model_block}"
            "approvals:\n"
            "  mode: manual                 # dangerous shell commands still ask; the leash escalates the rest\n"
            f"  timeout: {timeout}\n"
            "plugins:\n  enabled: [tomabotchi_leash]\n"
        )
        cfg_path = home / "config.yaml"
        cfg_path.write_text(config)
        link = home / "plugins" / "tomabotchi_leash"
        if link.is_symlink() or link.exists():
            link.unlink()
        os.symlink(LEASH_IN_CONTAINER, link)   # dangling on the host, valid inside the pet
        return [cfg_path, link]


class FakeBrain:
    """The scripted practice brain from slice 1: no container, no files."""
    name: BrainName = "fake"
    label = "Practice brain"
    session_mode = None

    def soul_path(self, brain_home: Path) -> Path:
        return brain_home / "SOUL.md"

    def rules_path(self, brain_home: Path) -> Path:
        return brain_home / "tomabotchi_rules.json"

    def render_soul_file(self, soul_md: str) -> str:
        return soul_md

    def write_config(self, cfg, pet, model, token, brain_home) -> list[Path]:
        return []


BRAINS: dict[str, Brain] = {"hermes": HermesBrain(), "fake": FakeBrain()}


def get(name: str) -> Brain:
    return BRAINS[name]


def write_rules(brain: Brain, brain_home: Path, rules: list[dict]) -> Path:
    path = brain.rules_path(brain_home)
    rules_mod.dump_rules_file(str(path), rules)
    return path


def write_soul(brain: Brain, brain_home: Path, soul_md: str) -> Path:
    path = brain.soul_path(brain_home)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(brain.render_soul_file(soul_md))
    tmp.replace(path)
    return path
