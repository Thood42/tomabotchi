"""One compose app: the stack resolves without outside help, and the server image reaches Docker as uid 1000."""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
pytestmark = pytest.mark.docker


def run(*cmd, env=None):
    return subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=600, env={**os.environ, **(env or {})})


def test_compose_config_resolves_without_docker_gid():
    env = {k: v for k, v in os.environ.items() if k not in ("DOCKER_GID", "TOMABOTCHI_ROOT")}
    base = subprocess.run(["docker", "compose", "config", "--services"], cwd=ROOT, capture_output=True, text=True, env=env)
    full = subprocess.run(["docker", "compose", "--profile", "ollama", "config", "--services"], cwd=ROOT,
                          capture_output=True, text=True, env=env)
    assert base.returncode == 0 and base.stdout.split() == ["tomabotchi"]
    assert sorted(full.stdout.split()) == ["ollama", "ollama-pull", "tomabotchi"]


@pytest.mark.skipif(not shutil.which("docker"), reason="needs Docker")
def test_server_container_can_reach_docker_as_uid_1000():
    built = run("docker", "build", "-q", "-t", "tomabotchi-server:entrypoint-test", ".")
    assert built.returncode == 0, built.stderr
    out = run("docker", "run", "--rm", "-v", "/var/run/docker.sock:/var/run/docker.sock", "tomabotchi-server:entrypoint-test",
              "sh", "-c", "id -u && python -c \"import docker; print(docker.from_env().ping())\"")
    assert out.returncode == 0, out.stderr
    assert out.stdout.split() == ["1000", "True"]
