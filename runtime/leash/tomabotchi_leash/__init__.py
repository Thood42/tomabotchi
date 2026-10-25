"""tomabotchi's leash for Hermes: a pre_tool_call plugin applying the pet's tool rules.

- free  → no directive; the tool runs
- never → {"action": "block"}; the message becomes the tool result the agent sees
- ask   → {"action": "approve"}; Hermes's human-approval gate asks the keeper over ACP
          (session/request_permission), with approvals.timeout as the wait

A dangerous shell command under an "ask" rule is left to Hermes's own dangerous-command gate,
which asks over the same ACP channel, so the keeper is asked once, not twice.
"""
from __future__ import annotations

import logging
import os

from . import kinds
from .rules import decide, load_rules_file

log = logging.getLogger("tomabotchi_leash")


def rules_path() -> str:
    return os.path.join(os.environ.get("HERMES_HOME", os.path.expanduser("~/.hermes")), "tomabotchi_rules.json")


def judge(tool_name: str, args: dict) -> dict | None:
    args = args if isinstance(args, dict) else {}
    kind = kinds.kind_for("hermes", tool_name)
    paths = kinds.paths_in("hermes", tool_name, args)
    try:
        rules = load_rules_file(rules_path())
    except (OSError, ValueError, KeyError):
        log.exception("tomabotchi rules unreadable; asking the keeper")
        rules = []
    verdict = decide(rules, kind=kind, tool=tool_name, paths=paths)
    if verdict == "free":
        return None
    if verdict == "never":
        return {"action": "block", "message": f"My keeper's rules never allow {tool_name} here. "
                                             "Find another way, or ask my keeper to change the rule."}
    if kind == "execute" and kinds.is_dangerous(tool_name, args):
        return None                       # Hermes's dangerous-command gate will ask
    target = ", ".join(paths) or kinds.command_of(tool_name, args) or tool_name
    return {"action": "approve", "message": describe(tool_name, kind, target, args),
            "rule_key": f"tomabotchi:{tool_name}:{target}"}


PREVIEW_CHARS = 600


def describe(tool_name: str, kind: str, target: str, args: dict) -> str:
    """The approval message. tomabotchi's server parses it back into a call card:
    first line "<tool> (<kind>): <target>", then an optional preview after a "---" line."""
    preview = args.get("content") or args.get("patch") or ""
    text = f"{tool_name} ({kind}): {target}"
    if isinstance(preview, str) and preview:
        text += "\n---\n" + preview[:PREVIEW_CHARS] + ("\n..." if len(preview) > PREVIEW_CHARS else "")
    return text


def _pre_tool_call(tool_name: str = "", args: dict | None = None, **_ctx) -> dict | None:
    return judge(tool_name, args or {})


def register(ctx) -> None:
    ctx.register_hook("pre_tool_call", _pre_tool_call)
