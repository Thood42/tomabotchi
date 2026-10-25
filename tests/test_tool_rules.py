"""Slice 4: tool rules the keeper edits, "Always allow" adding a narrow rule, and blocks counted."""
import json
from datetime import datetime, timezone

from test_web import HATCH, client, wait_for  # noqa: F401 - client is a fixture

from tomabotchi import store, views
from tomabotchi.models import Call, Event, Pet, ToolRule
from tomabotchi.rules import block_message
from tomabotchi.supervisor import normalize_permission, translate

NOW = datetime(2026, 10, 25, tzinfo=timezone.utc)


def hatch(client):
    pet_id = client.post("/pets", data=HATCH, follow_redirects=False).headers["location"].rsplit("/", 1)[1]
    conn = client.app.state.conn
    wait_for(lambda: store.pending_calls(conn, pet_id))
    return pet_id, store.pending_calls(conn, pet_id)[0]


def rules_file(client, pet_id):
    return json.loads((client.app.state.fleet.brain_home(pet_id) / "tomabotchi_rules.json").read_text())["rules"]


def test_allow_always_adds_rule_and_rewrites_rules_file(client):
    pet_id, call = hatch(client)
    assert "Always allow editing files in notes/" in client.get(f"/pets/{pet_id}").text
    assert client.post(f"/calls/{call.id}", data={"option": "allow_always"}).status_code == 200
    narrow = ToolRule(kind="edit", tool=None, path_glob="notes/**", rule="free")
    assert narrow in store.tool_rules(client.app.state.conn, pet_id)
    assert {"kind": "edit", "tool": None, "path_glob": "notes/**", "rule": "free"} in rules_file(client, pet_id)
    agent = client.app.state.fleet._fakes[pet_id]
    wait_for(lambda: any(m.get("result", {}).get("outcome") for m in agent.received))
    [answer] = [m["result"]["outcome"] for m in agent.received if m.get("result", {}).get("outcome")]
    assert answer["optionId"] == "allow_once"       # the brain keeps no "always" list of its own


def test_rules_editor_saves_rows_drops_removed_and_skips_blank(client):
    pet_id, _ = hatch(client)
    r = client.put(f"/pets/{pet_id}/rules", data={
        "kind": ["read", "execute", "edit", "edit"], "tool": ["", "terminal", "", ""],
        "path_glob": ["", "", "/drafts/**", ""], "rule": ["free", "never", "remove", ""]})
    assert r.status_code == 200 and "Saved." in r.text
    want = [{"kind": "read", "tool": None, "path_glob": None, "rule": "free"},
            {"kind": "execute", "tool": "terminal", "path_glob": None, "rule": "never"}]
    assert rules_file(client, pet_id) == want
    assert [vars(x) for x in store.tool_rules(client.app.state.conn, pet_id)] == want
    assert "<b>Never:</b> terminal" in client.get(f"/pets/{pet_id}").text


def test_rules_editor_rejects_unknown_kind(client):
    pet_id, _ = hatch(client)
    r = client.put(f"/pets/{pet_id}/rules", data={"kind": "teleport", "tool": "", "path_glob": "", "rule": "free"})
    assert r.status_code == 400
    assert len(rules_file(client, pet_id)) == 10               # the defaults are untouched


def test_dangerous_command_is_never_always_allowed(conn):
    tc = normalize_permission({"kind": "execute", "title": "rm", "rawInput": {"command": "rm -rf /workspace"}})
    call = Call(id=1, pet_id="p", task_id=None, acp_request_id=1, tool_call=tc, options=[], asked_at=NOW)
    assert tc["dangerous"] and views.always_rule(call) is None
    store.insert_pet(conn, _pet())
    assert views.rules_context(conn, _pet(), call)["always_label"] is None


def test_blocked_tool_counts_against_discipline(conn):
    store.insert_pet(conn, _pet())
    tool_index = {"t1": {"title": "terminal: ls", "kind": "execute", "args": None}}
    content = [{"type": "content", "content": {"type": "text", "text": block_message("terminal")}}]
    [ev] = translate({"sessionUpdate": "tool_call_update", "toolCallId": "t1", "status": "failed", "content": content},
                     pet_id="p", task_id=None, tool_index=tool_index, now=NOW)
    assert ev.payload["blocked"] is True
    store.append_event(conn, ev)
    store.open_call(conn, Call(id=0, pet_id="p", task_id=None, acp_request_id=1, tool_call={}, options=[], asked_at=NOW))
    assert store.discipline(conn, "p") == (1, 2)
    assert "Asked first 1 of 2 times" in views.render("_vitals.html", **views.pet_context(conn, store.get_pet(conn, "p")))


def _pet():
    return Pet(id="p", name="Biscuit", shell="grape", look_seed=1, home="python", brain="fake", model="local-qwen",
               state="awake", born_at=NOW)



async def test_blocks_noted_by_the_leash_show_as_tools_and_count(cfg, conn, bus):
    from tomabotchi import brains
    from tomabotchi.fake_agent import FakeAgent
    from tomabotchi.rules import record_block
    from tomabotchi.supervisor import PetSupervisor
    agent = FakeAgent(step_delay=0, token="tok")
    store.insert_pet(conn, _pet())
    sup = PetSupervisor(cfg, conn, bus, _pet(), await agent.start(), "tok")
    await sup.start()
    rules_file = str(brains.get("fake").rules_path(cfg.pet_dir("p") / "brain"))
    (cfg.pet_dir("p") / "brain").mkdir(parents=True)
    record_block(rules_file, tool="terminal", kind="execute", target="date")
    await sup._on_update({"update": {"sessionUpdate": "usage_update", "size": 100, "used": 1}})
    ctx = views.pet_context(conn, store.get_pet(conn, "p"))
    assert [(t["title"], t["status"]) for t in ctx["tools"]] == [("terminal: date", "blocked")]
    assert ctx["vitals"].discipline == (0, 1)
    await sup._on_update({"update": {"sessionUpdate": "usage_update", "size": 100, "used": 1}})
    assert store.discipline(conn, "p") == (0, 1)          # drained once, not counted again
    await sup.stop()
    await agent.stop()
