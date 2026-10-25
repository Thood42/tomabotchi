"""Slice 7: "Pause while I edit" stops only the agent, through petd, and the pet page says so."""
import asyncio
import os
import signal
import subprocess
import sys
from pathlib import Path

from test_web import HATCH, client, wait_for  # noqa: F401 - client is a fixture

from tomabotchi import store

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runtime" / "petd"))
import petd  # noqa: E402


def proc_state(pid: int) -> str:
    return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]


def test_petd_control_stops_and_continues_the_agents_whole_group():
    agent = subprocess.Popen(["sh", "-c", "sleep 30 & wait"], start_new_session=True)
    try:
        d = petd.Petd("t", "hermes")
        d.agent = agent
        wait_for(lambda: len(os.popen(f"pgrep -g {agent.pid}").read().split()) == 2)
        child = int([p for p in os.popen(f"pgrep -g {agent.pid}").read().split() if int(p) != agent.pid][0])
        assert d._control('{"jsonrpc": "2.0", "method": "petd/pause", "params": {}}')
        wait_for(lambda: proc_state(agent.pid) == "T" and proc_state(child) == "T")    # its tools stop too
        assert d._control('{"jsonrpc": "2.0", "method": "petd/resume", "params": {}}')
        wait_for(lambda: proc_state(agent.pid) != "T" and proc_state(child) != "T")
        assert not d._control('{"jsonrpc": "2.0", "id": 3, "method": "session/prompt", "params": {}}')
        assert not d._control('{"jsonrpc": "2.0", "method": "petd/selfdestruct"}')
    finally:
        os.killpg(agent.pid, signal.SIGKILL)
        agent.wait()


def test_pause_marks_the_pet_paused_and_feeding_waits(client):
    conn = client.app.state.conn
    pet_id = client.post("/pets", data={**HATCH, "first_task_md": ""}, follow_redirects=False).headers["location"].rsplit("/", 1)[1]
    wait_for(lambda: client.app.state.fleet.is_awake(pet_id))
    assert client.post(f"/pets/{pet_id}/pause").status_code == 204
    agent = client.app.state.fleet._fakes[pet_id]
    wait_for(lambda: any(m.get("method") == "petd/pause" for m in agent.received))
    assert store.get_pet(conn, pet_id).state == "paused"
    assert "Paused while you edit its workspace" in client.get("/").text
    assert client.post(f"/pets/{pet_id}/tasks", data={"prompt_md": "go"}).status_code == 409
    assert client.post(f"/pets/{pet_id}/resume").status_code == 204
    wait_for(lambda: any(m.get("method") == "petd/resume" for m in agent.received))
    assert store.get_pet(conn, pet_id).state == "awake"
