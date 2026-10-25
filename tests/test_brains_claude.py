"""Slice 5: the Claude Code brain's config, and its leash (a PreToolUse hook) deciding calls."""
import io
import json
import sys
from pathlib import Path

import pytest

from tomabotchi import brains, rules
from tomabotchi.models import ModelChoice, Pet

LEASH = Path(__file__).resolve().parents[1] / "runtime" / "leash"
sys.path.insert(0, str(LEASH))
sys.modules.setdefault("tomabotchi_leash.rules", rules)     # in the image, rules.py is copied into the package
from tomabotchi_leash import claude_hook, kinds  # noqa: E402

LOCAL = ModelChoice(key="local-qwen", label="Local", provider="ollama", model="qwen3.6:35b")
SONNET = ModelChoice(key="claude-sonnet", label="Sonnet", provider="anthropic", model="claude-sonnet-5-5")


def pet(brain="claude", model="local-qwen") -> Pet:
    return Pet(id="c1a0de00", name="Quill", shell="lagoon", look_seed=1, home="python", brain=brain, model=model,
               state="awake", born_at="2026-10-25T00:00:00Z")


def test_claude_settings_hook_every_tool_and_never_bypass(cfg, tmp_path):
    [path] = brains.get("claude").write_config(cfg, pet(), LOCAL, "tok", tmp_path)
    assert path == tmp_path / ".claude" / "settings.json"
    s = json.loads(path.read_text())
    assert s["permissions"] == {"defaultMode": "default", "disableBypassPermissionsMode": "disable"}
    [entry] = s["hooks"]["PreToolUse"]
    assert entry["matcher"] == "*"
    assert entry["hooks"] == [{"type": "command", "command": brains.CLAUDE_HOOK, "timeout": brains.CLAUDE_HOOK_TIMEOUT}]


def test_claude_env_points_at_the_gateway_with_the_pet_token():
    env = brains.get("claude").container_env(pet(model="claude-sonnet"), SONNET, "pet-tok")
    assert env["ANTHROPIC_BASE_URL"] == "http://tomabotchi:8770/llm/pet-tok/anthropic"
    assert env["ANTHROPIC_API_KEY"] == "pet-tok"                 # never the real key; the gateway swaps it in



ROLES = ("ANTHROPIC_MODEL", "ANTHROPIC_DEFAULT_HAIKU_MODEL", "ANTHROPIC_DEFAULT_SONNET_MODEL",
         "ANTHROPIC_DEFAULT_OPUS_MODEL", "ANTHROPIC_SMALL_FAST_MODEL")


@pytest.mark.parametrize("model", [LOCAL, SONNET])
def test_claude_env_uses_the_pets_one_model_for_every_role(model):
    env = brains.get("claude").container_env(pet(model=model.key), model, "pet-tok")
    assert {env[r] for r in ROLES} == {model.model}       # the adapter asks for "opus" unless told otherwise


def test_hermes_on_claude_gets_its_gateway_token_as_the_key_and_on_ollama_needs_nothing():
    env = brains.get("hermes").container_env(pet("hermes", model="claude-sonnet"), SONNET, "pet-tok")
    assert env["ANTHROPIC_API_KEY"] == "pet-tok" and env["ANTHROPIC_BASE_URL"].endswith("/pet-tok/anthropic")
    assert brains.get("hermes").container_env(pet("hermes"), LOCAL, "t") == {}


def test_fake_needs_no_container_env():
    assert brains.get("fake").container_env(pet("fake"), LOCAL, "t") == {}


def test_soul_file_per_brain(tmp_path):
    claude = brains.write_soul(brains.get("claude"), tmp_path, "# Quill\n\nI love tidy notes.")
    hermes = brains.write_soul(brains.get("hermes"), tmp_path, "# Biscuit")
    assert claude == tmp_path / ".claude" / "CLAUDE.md"
    assert claude.read_text().endswith("# Quill\n\nI love tidy notes.")
    assert "This is my soul" in claude.read_text()
    assert hermes == tmp_path / ".hermes" / "SOUL.md" and hermes.read_text() == "# Biscuit"
    assert brains.get("claude").rules_path(tmp_path) == tmp_path / ".claude" / "tomabotchi_rules.json"


@pytest.mark.parametrize("tool,kind", [("Write", "edit"), ("Edit", "edit"), ("Bash", "execute"), ("Read", "read"),
                                       ("Glob", "search"), ("WebFetch", "fetch"), ("TodoWrite", "think"),
                                       ("mcp__x__y", "other")])
def test_claude_tool_kinds(tool, kind):
    assert kinds.kind_for("claude", tool) == kind


def test_claude_tool_paths():
    assert kinds.paths_in("claude", "Write", {"file_path": "/workspace/notes/a.md"}) == ["/workspace/notes/a.md"]
    assert kinds.paths_in("claude", "NotebookEdit", {"notebook_path": "n.ipynb"}) == ["n.ipynb"]
    assert kinds.command_of("Bash", {"command": "ls"}) == "ls"


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".claude").mkdir()

    def set_rules(extra=()):
        rs = list(rules.DEFAULT_RULES)
        for r in extra:
            rs = rules.with_rule(rs, r)
        rules.dump_rules_file(str(tmp_path / ".claude" / "tomabotchi_rules.json"), rs)
    set_rules()
    return tmp_path, set_rules


def decision(out: dict) -> str:
    assert out["hookSpecificOutput"]["hookEventName"] == "PreToolUse"
    return out["hookSpecificOutput"]["permissionDecision"]


def test_hook_free_allows_and_ask_asks(home):
    assert decision(claude_hook.judge("Read", {"file_path": "/workspace/a.py"})) == "allow"
    assert decision(claude_hook.judge("Write", {"file_path": "/workspace/notes/a.md", "content": "x"})) == "ask"


def test_hook_narrow_free_rule_allows_only_its_folder(home):
    _, set_rules = home
    set_rules([{"kind": "edit", "tool": "Write", "path_glob": "notes/**", "rule": "free"}])
    assert decision(claude_hook.judge("Write", {"file_path": "/workspace/notes/a.md"})) == "allow"
    assert decision(claude_hook.judge("Write", {"file_path": "/workspace/src/a.py"})) == "ask"


def test_hook_never_denies_with_the_reason_and_notes_the_block(home):
    tmp, set_rules = home
    set_rules([{"kind": "execute", "tool": None, "path_glob": None, "rule": "never"}])
    out = claude_hook.judge("Bash", {"command": "date"})
    assert decision(out) == "deny"
    assert rules.BLOCK_MARK in out["hookSpecificOutput"]["permissionDecisionReason"]
    [block] = rules.drain_blocks(str(tmp / ".claude" / "tomabotchi_rules.json"))
    assert (block["tool"], block["kind"], block["target"]) == ("Bash", "execute", "date")


def test_hook_dangerous_command_asks_even_when_free(home, monkeypatch):
    _, set_rules = home
    set_rules([{"kind": "execute", "tool": None, "path_glob": None, "rule": "free"}])
    monkeypatch.setattr(kinds, "is_dangerous", lambda tool, args: "rm -rf" in args.get("command", ""))
    assert decision(claude_hook.judge("Bash", {"command": "ls"})) == "allow"
    assert decision(claude_hook.judge("Bash", {"command": "rm -rf /workspace"})) == "ask"


def test_hook_unreadable_rules_ask_for_everything(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert decision(claude_hook.judge("Read", {"file_path": "/workspace/a.py"})) == "ask"


def test_hook_main_reads_stdin(home, monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"tool_name": "Read", "tool_input": {"file_path": "a"}})))
    claude_hook.main()
    assert decision(json.loads(capsys.readouterr().out)) == "allow"
