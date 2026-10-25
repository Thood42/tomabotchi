from datetime import datetime, timezone

from tomabotchi.supervisor import _permission_result, translate

NOW = datetime(2026, 10, 25, tzinfo=timezone.utc)


def tr(update, index=None):
    return translate(update, pet_id="p1", task_id=1, tool_index={} if index is None else index, now=NOW)


def test_thought_chunk_to_thought_event():
    [ev] = tr({"sessionUpdate": "agent_thought_chunk", "content": {"type": "text", "text": "Hmm."}})
    assert (ev.kind, ev.payload) == ("thought", {"text": "Hmm."})


def test_empty_chunk_is_dropped():
    assert tr({"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": ""}}) == []


def test_tool_call_then_update_completed():
    index = {}
    [call] = tr({"sessionUpdate": "tool_call", "toolCallId": "t1", "title": "Write notes/a.md", "kind": "edit",
                 "status": "pending", "locations": [{"path": "/workspace/notes/a.md"}],
                 "rawInput": {"path": "notes/a.md"}}, index)
    assert call.kind == "tool_call"
    assert call.payload["paths"] == ["/workspace/notes/a.md"]
    [upd] = tr({"sessionUpdate": "tool_call_update", "toolCallId": "t1", "status": "completed"}, index)
    assert upd.kind == "tool_update"
    assert upd.payload == {"id": "t1", "status": "completed", "tool": "Write notes/a.md",
                           "title": "Write notes/a.md", "args": {"path": "notes/a.md"}}


def test_plan_update_keeps_entries():
    entries = [{"content": "a", "priority": "high", "status": "completed"}]
    [ev] = tr({"sessionUpdate": "plan", "entries": entries})
    assert (ev.kind, ev.payload) == ("plan", {"entries": entries})


def test_unknown_update_kind_becomes_notice():
    [ev] = tr({"sessionUpdate": "something_new", "detail": 1})
    assert ev.kind == "notice"
    assert ev.payload["type"] == "something_new"


OPTIONS = [{"optionId": "a1", "kind": "allow_once"}, {"optionId": "r1", "kind": "reject_once"}]


def test_permission_result_picks_option_by_kind():
    assert _permission_result("reject_once", OPTIONS) == {"outcome": {"outcome": "selected", "optionId": "r1"}}


def test_allow_always_falls_back_to_allow_once_when_not_offered():
    assert _permission_result("allow_always", OPTIONS) == {"outcome": {"outcome": "selected", "optionId": "a1"}}


def test_timeout_cancels():
    assert _permission_result("timeout", OPTIONS) == {"outcome": {"outcome": "cancelled"}}


# Real payload from Hermes 0.22.5 when the leash escalates write_file (captured 2026-10-25).
HERMES_ESCALATION = {
    "content": [{"content": {"text": "write_file (edit): notes/hello.md\n$ <write_file> (plugin approval rule)",
                             "type": "text"}, "type": "content"}],
    "kind": "execute",
    "rawInput": {"command": "<write_file> (plugin approval rule)",
                 "description": "write_file (edit): notes/hello.md\n---\n# Hello\nHi there.\n"},
    "status": "pending",
    "title": "write_file (edit): notes/hello.md: <write_file> (plugin approval rule)",
    "toolCallId": "perm-check-1",
}


def test_leash_escalation_becomes_a_readable_call():
    from tomabotchi.supervisor import normalize_permission
    tc = normalize_permission(HERMES_ESCALATION)
    assert tc["title"] == "Write notes/hello.md"
    assert tc["kind"] == "edit"
    assert tc["locations"] == [{"path": "notes/hello.md"}]
    assert tc["rawInput"] == {"content": "# Hello\nHi there.\n"}
    assert tc["toolCallId"] == "perm-check-1"


def test_hermes_dangerous_command_prompt_shows_the_command():
    from tomabotchi.supervisor import normalize_permission
    tc = normalize_permission({"kind": "execute", "title": "x", "rawInput": {"command": "rm -rf build/",
                                                                           "description": "recursive delete"}})
    assert tc["title"] == "Run rm -rf build/"


def test_ordinary_permission_passes_through():
    from tomabotchi.supervisor import normalize_permission
    tc = {"toolCallId": "write-1", "title": "Write notes/hello.md", "kind": "edit"}
    assert normalize_permission(tc) == tc
