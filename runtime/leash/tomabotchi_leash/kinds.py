"""Which ACP kind a brain's tool is, and which workspace paths it touches."""
from __future__ import annotations

import re

# Hermes's own bookkeeping: map to "think" so the default rules never interrupt the keeper for them.
HERMES_OVERRIDES = {"todo": "think", "memory": "think", "session_search": "read", "clarify": "think",
                    "skills_list": "read", "skill_view": "read"}

# Claude Code's tool names. Hidden bookkeeping (todos, plan mode exit) is "think" or "switch_mode".
CLAUDE_KINDS = {"Write": "edit", "Edit": "edit", "MultiEdit": "edit", "NotebookEdit": "edit",
                "Bash": "execute", "KillShell": "execute", "BashOutput": "read", "Read": "read",
                "Grep": "search", "Glob": "search", "LS": "search", "WebFetch": "fetch", "WebSearch": "fetch",
                "TodoWrite": "think", "ExitPlanMode": "switch_mode", "Task": "other", "Agent": "other"}

_V4A_FILE = re.compile(r"^\*\*\*\s+(?:Update|Add|Delete)\s+File:\s*(.+)$", re.MULTILINE)


def kind_for(brain: str, tool_name: str) -> str:
    if brain == "hermes":
        if tool_name in HERMES_OVERRIDES:
            return HERMES_OVERRIDES[tool_name]
        try:
            from acp_adapter.tools import get_tool_kind
            return get_tool_kind(tool_name)
        except ImportError:
            return "other"
    if brain == "claude":
        return CLAUDE_KINDS.get(tool_name, "other")           # mcp__* and anything new: other
    return "other"


def paths_in(brain: str, tool_name: str, args: dict) -> list[str]:
    paths = [str(args[k]) for k in ("path", "file_path", "notebook_path") if args.get(k)]
    if tool_name == "patch" and isinstance(args.get("patch"), str):
        paths += _V4A_FILE.findall(args["patch"])
    return paths


def command_of(tool_name: str, args: dict) -> str | None:
    return args.get("command") if tool_name in ("terminal", "process", "Bash") else None


def is_dangerous(tool_name: str, args: dict) -> bool:
    """Hermes flags dangerous shell commands itself; reuse its detector."""
    cmd = command_of(tool_name, args)
    if not cmd:
        return False
    try:
        from tools.approval_detection import detect_dangerous_command
        return bool(detect_dangerous_command(cmd)[0])
    except Exception:  # noqa: BLE001 - detector missing or changed shape: treat as not flagged
        return False
