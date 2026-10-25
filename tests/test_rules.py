from tomabotchi.rules import (DEFAULT_RULES, decide, dump_rules_file, load_rules_file, normalize_path,
                              rule_for_allow_always)


def r(kind, rule, tool=None, path_glob=None):
    return {"kind": kind, "tool": tool, "path_glob": path_glob, "rule": rule}


def test_defaults_read_free_edit_ask():
    assert decide(DEFAULT_RULES, kind="read", tool="read_file", paths=["notes/a.md"]) == "free"
    assert decide(DEFAULT_RULES, kind="edit", tool="write_file", paths=["notes/a.md"]) == "ask"
    assert decide(DEFAULT_RULES, kind="execute", tool="terminal", paths=[]) == "ask"


def test_unknown_kind_with_no_matching_rule_asks():
    assert decide([r("read", "free")], kind="edit", tool="write_file", paths=[]) == "ask"


def test_tool_path_rule_beats_kind_rule():
    rules = [r("edit", "ask"), r("edit", "free", tool="write_file", path_glob="notes/**")]
    assert decide(rules, kind="edit", tool="write_file", paths=["/workspace/notes/a.md"]) == "free"


def test_path_outside_glob_falls_back_to_kind():
    rules = [r("edit", "ask"), r("edit", "free", tool="write_file", path_glob="notes/**")]
    assert decide(rules, kind="edit", tool="write_file", paths=["src/a.py"]) == "ask"


def test_never_beats_ask_at_same_specificity():
    rules = [r("execute", "ask"), r("execute", "never")]
    assert decide(rules, kind="execute", tool="terminal", paths=[]) == "never"


def test_multi_path_takes_strictest():
    rules = [r("edit", "ask"), r("edit", "free", tool="write_file", path_glob="notes/**")]
    assert decide(rules, kind="edit", tool="write_file", paths=["notes/a.md", "src/b.py"]) == "ask"


def test_allow_always_rule_uses_common_parent():
    rule = rule_for_allow_always(kind="edit", tool="write_file", paths=["notes/a.md", "/workspace/notes/x/b.md"])
    assert rule == {"kind": "edit", "tool": "write_file", "path_glob": "notes/**", "rule": "free"}
    assert rule_for_allow_always(kind="execute", tool="terminal", paths=[]) == \
        {"kind": "execute", "tool": "terminal", "path_glob": None, "rule": "free"}


def test_allow_always_at_workspace_root_has_no_glob():
    assert rule_for_allow_always(kind="edit", tool="write_file", paths=["a.md"])["path_glob"] is None


def test_normalize_path_strips_workspace():
    assert normalize_path("/workspace/notes/../notes/a.md") == "notes/a.md"
    assert normalize_path("./b.md") == "b.md"


def test_rules_file_roundtrip_atomic(tmp_path):
    path = str(tmp_path / "home" / "tomabotchi_rules.json")
    dump_rules_file(path, DEFAULT_RULES)
    assert load_rules_file(path) == list(DEFAULT_RULES)
    assert [p.name for p in (tmp_path / "home").iterdir()] == ["tomabotchi_rules.json"]   # no temp files left
