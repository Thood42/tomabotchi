"""tomabotchi's leash for Claude Code: a PreToolUse command hook applying the pet's tool rules.

Reads the hook input on stdin ({"tool_name", "tool_input", ...}) and prints a decision:
- free  → "allow": Claude's own permission prompt is skipped
- never → "deny": the reason is what Claude sees, and the block is noted for the server
- ask   → "ask": Claude asks over ACP (session/request_permission), which is tomabotchi's call card

A dangerous shell command always asks, whatever the rules say (unless a rule says never).
Run as /opt/tomabotchi/bin/leash-claude.
"""
from __future__ import annotations

import json
import logging
import os
import sys

from . import kinds
from .rules import block_message, decide, load_rules_file, record_block

log = logging.getLogger("tomabotchi_leash.claude")


def rules_path() -> str:
    return os.path.join(os.environ.get("HOME", "/home/pet"), ".claude", "tomabotchi_rules.json")


def _decision(decision: str, reason: str) -> dict:
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": decision,
                                   "permissionDecisionReason": reason}}


def judge(tool_name: str, tool_input: dict) -> dict:
    tool_input = tool_input if isinstance(tool_input, dict) else {}
    kind = kinds.kind_for("claude", tool_name)
    paths = kinds.paths_in("claude", tool_name, tool_input)
    try:
        rules = load_rules_file(rules_path())
    except (OSError, ValueError, KeyError):
        rules = []                       # unreadable rules: nothing matches, so everything asks
    verdict = decide(rules, kind=kind, tool=tool_name, paths=paths)
    if verdict == "never":
        target = ", ".join(paths) or kinds.command_of(tool_name, tool_input) or tool_name
        record_block(rules_path(), tool=tool_name, kind=kind, target=target)
        return _decision("deny", block_message(tool_name))
    if kind == "execute" and kinds.is_dangerous(tool_name, tool_input):
        return _decision("ask", "This command looks dangerous, so my keeper decides.")
    if verdict == "free":
        return _decision("allow", "My keeper's rules let me use this freely.")
    return _decision("ask", "My keeper's rules say to ask first.")


def main() -> None:
    try:
        data = json.load(sys.stdin)
    except ValueError:
        data = {}
    print(json.dumps(judge(str(data.get("tool_name", "")), data.get("tool_input") or {})))


if __name__ == "__main__":
    main()
