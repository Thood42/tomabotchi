"""Tool rules: does a tool call run freely, ask the keeper first, or never run?

Stdlib only. The runtime build copies this file into the leash package, so the server and
the pet decide with the same code.
"""
from __future__ import annotations

import json
import os
import posixpath
import tempfile
from fnmatch import fnmatchcase
from typing import Literal, Sequence, TypedDict

RuleValue = Literal["free", "ask", "never"]


class ToolRuleDict(TypedDict):
    kind: str
    tool: str | None
    path_glob: str | None
    rule: RuleValue


def _r(kind: str, rule: RuleValue) -> ToolRuleDict:
    return {"kind": kind, "tool": None, "path_glob": None, "rule": rule}


DEFAULT_RULES: tuple[ToolRuleDict, ...] = (
    _r("read", "free"), _r("search", "free"), _r("fetch", "free"), _r("think", "free"),
    _r("edit", "ask"), _r("delete", "ask"), _r("move", "ask"), _r("execute", "ask"),
    _r("switch_mode", "ask"), _r("other", "ask"),
)

_STRICTNESS = {"free": 0, "ask": 1, "never": 2}
WORKSPACE = "/workspace"
BLOCK_MARK = "My keeper's rules never allow"
BLOCKS_FILE = "tomabotchi_blocks.jsonl"     # next to the rules file; a blocked call never reaches ACP


def block_message(tool: str) -> str:
    """What the agent sees in place of a tool result when a "never" rule blocks it. The server
    spots BLOCK_MARK in tool results to count blocks."""
    return f"{BLOCK_MARK} {tool} here. Find another way, or ask my keeper to change the rule."


def normalize_path(path: str) -> str:
    """Paths are compared relative to the pet's workspace."""
    p = posixpath.normpath(path)
    if p == WORKSPACE:
        return "."
    if p.startswith(WORKSPACE + "/"):
        p = p[len(WORKSPACE) + 1:]
    return p.lstrip("/") if not p.startswith("..") else p


def _specificity(rule: ToolRuleDict) -> int:
    has_tool, has_path = rule["tool"] is not None, rule["path_glob"] is not None
    if has_tool:
        return 4 if has_path else 3
    if has_path:
        return 2
    return 0 if rule["kind"] == "*" else 1


def _matches(rule: ToolRuleDict, kind: str, tool: str, path: str | None) -> bool:
    if rule["tool"] is not None:
        if rule["tool"] != tool:
            return False
    elif rule["kind"] not in ("*", kind):
        return False
    if rule["path_glob"] is not None:
        return path is not None and fnmatchcase(path, rule["path_glob"])
    return True


def _decide_one(rules: Sequence[ToolRuleDict], kind: str, tool: str, path: str | None) -> RuleValue:
    best: tuple[int, int] | None = None
    verdict: RuleValue = "ask"            # nothing matched: ask, never silently allow
    for rule in rules:
        if _matches(rule, kind, tool, path):
            key = (_specificity(rule), _STRICTNESS[rule["rule"]])
            if best is None or key > best:
                best, verdict = key, rule["rule"]
    return verdict


def decide(rules: Sequence[ToolRuleDict], *, kind: str, tool: str, paths: Sequence[str]) -> RuleValue:
    """Most specific matching rule wins (tool+path > tool > kind+path > kind > "*");
    among equally specific rules the strictest wins. A call touching several paths takes
    the strictest per-path result."""
    norm = [normalize_path(p) for p in paths] or [None]
    return max((_decide_one(rules, kind, tool, p) for p in norm), key=_STRICTNESS.__getitem__)


def rule_for_allow_always(*, kind: str, tool: str | None, paths: Sequence[str]) -> ToolRuleDict:
    """The narrowest useful rule for "Always allow": this tool (or kind, when the brain didn't
    name the tool), under the common parent of its paths."""
    dirs = [posixpath.dirname(normalize_path(p)) for p in paths]
    common = posixpath.commonpath(dirs) if dirs and all(d and not d.startswith("..") for d in dirs) else ""
    return {"kind": kind, "tool": tool, "path_glob": f"{common}/**" if common else None, "rule": "free"}


def with_rule(rules: Sequence[ToolRuleDict], new: ToolRuleDict) -> list[ToolRuleDict]:
    """Add a rule, replacing any rule for the same kind, tool and path."""
    key = lambda r: (r["kind"], r["tool"], r["path_glob"])  # noqa: E731
    return [r for r in rules if key(r) != key(new)] + [new]


_cache: dict[str, tuple[float, list[ToolRuleDict]]] = {}


def load_rules_file(path: str) -> list[ToolRuleDict]:
    mtime = os.stat(path).st_mtime
    hit = _cache.get(path)
    if hit and hit[0] == mtime:
        return hit[1]
    with open(path) as f:
        rules = json.load(f)["rules"]
    _cache[path] = (mtime, rules)
    return rules


def dump_rules_file(path: str, rules: Sequence[ToolRuleDict]) -> None:
    d = os.path.dirname(path) or "."
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".rules-")
    with os.fdopen(fd, "w") as f:
        json.dump({"rules": list(rules)}, f, indent=1)
    os.replace(tmp, path)


def _blocks_path(rules_file: str) -> str:
    return os.path.join(os.path.dirname(rules_file), BLOCKS_FILE)


def record_block(rules_file: str, *, tool: str, kind: str, target: str) -> None:
    """The leash notes each block for the server, which counts it against Discipline."""
    try:
        with open(_blocks_path(rules_file), "a") as f:
            f.write(json.dumps({"tool": tool, "kind": kind, "target": target}) + "\n")
    except OSError:
        pass                                    # never let bookkeeping break the block itself


def drain_blocks(rules_file: str) -> list[dict]:
    """Take every block noted so far (the server side of record_block)."""
    path = _blocks_path(rules_file)
    taken = path + ".taken"
    try:
        os.replace(path, taken)
    except FileNotFoundError:
        return []
    with open(taken) as f:
        blocks = [json.loads(line) for line in f if line.strip()]
    os.remove(taken)
    return blocks
