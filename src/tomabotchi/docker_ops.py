"""A thin async wrapper over the Docker SDK: images, networks, pet containers. Nothing else imports docker."""
from __future__ import annotations

import asyncio
import io
import ipaddress
import logging
import socket
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import docker
from docker.errors import APIError, ImageNotFound, NotFound

from .config import Config
from .models import Home, Limits, Pet

log = logging.getLogger(__name__)

LABEL = "tomabotchi.pet"
SERVER_ALIAS = "tomabotchi"

Progress = Callable[[str], None]


@dataclass(frozen=True)
class PetContainer:
    id: str
    pet_id: str
    network: str
    running: bool
    paused: bool


@dataclass(frozen=True)
class HomeStats:
    mem_bytes: int
    mem_limit: int
    cpu_pct: float


def runtime_tag(cfg: Config) -> str:
    return f"tomabotchi-runtime:{cfg.runtime_version}"


def pet_image_tag(cfg: Config, home: Home) -> str:
    return f"tomabotchi-pet:{home.slug}-{cfg.runtime_version}"


def network_name(pet_id: str) -> str:
    return f"tbx-pet-{pet_id}"


def container_name(pet_id: str) -> str:
    return f"tbx-pet-{pet_id}"


def free_subnet(pool: ipaddress.IPv4Network, taken: list[ipaddress.IPv4Network], prefix: int = 28):
    for net in pool.subnets(new_prefix=prefix):
        if not any(net.overlaps(t) for t in taken):
            return net
    raise RuntimeError(f"no free /{prefix} left in {pool}")


class DockerOps:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.client = docker.from_env()
        self._build_lock = asyncio.Lock()

    # ---- images ----

    async def ensure_runtime(self, progress: Progress) -> str:
        tag = runtime_tag(self.cfg)
        if await self._has_image(tag):
            return tag
        progress("Building the pet runtime (first hatch only, a few minutes)")
        await asyncio.to_thread(self._build, path=str(self.cfg.source_dir), dockerfile="runtime/Dockerfile", tag=tag,
                                progress=progress)
        return tag

    async def ensure_pet_image(self, home: Home, progress: Progress) -> str:
        async with self._build_lock:
            tag = pet_image_tag(self.cfg, home)
            if await self._has_image(tag):
                return tag
            runtime = await self.ensure_runtime(progress)
            progress(f"Building the {home.name} home")
            dockerfile = (self.cfg.source_dir / "runtime" / "pet.Dockerfile.tmpl").read_text() \
                .replace("{runtime}", runtime).replace("{home}", home.image)
            await asyncio.to_thread(self._build, fileobj=io.BytesIO(dockerfile.encode()), tag=tag, progress=progress)
            return tag

    async def _has_image(self, tag: str) -> bool:
        try:
            await asyncio.to_thread(self.client.images.get, tag)
            return True
        except ImageNotFound:
            return False

    def _build(self, *, tag: str, progress: Progress, **kw) -> None:
        last_error = None
        for chunk in self.client.api.build(tag=tag, rm=True, decode=True, pull=False, **kw):
            if "error" in chunk:
                last_error = chunk["error"].strip()
            line = (chunk.get("stream") or "").strip()
            if line.startswith("tomabotchi:"):
                progress(line.removeprefix("tomabotchi:").strip())
        if last_error:
            raise RuntimeError(f"image build failed: {last_error}")

    # ---- networks ----

    async def create_network(self, pet_id: str) -> str:
        name = network_name(pet_id)
        return await asyncio.to_thread(self._create_network, name)

    def _create_network(self, name: str) -> str:
        try:
            self.client.networks.get(name)
        except NotFound:
            taken = []
            for n in self.client.networks.list():
                for c in (n.attrs.get("IPAM") or {}).get("Config") or []:
                    if c.get("Subnet") and ":" not in c["Subnet"]:
                        taken.append(ipaddress.IPv4Network(c["Subnet"]))
            subnet = free_subnet(self.cfg.pet_subnet_pool, taken)
            ipam = docker.types.IPAMConfig(pool_configs=[docker.types.IPAMPool(subnet=str(subnet))])
            self.client.networks.create(name, driver="bridge", ipam=ipam, labels={LABEL: name})
        self._connect_self(name)
        return name

    def _connect_self(self, name: str) -> None:
        """Join the server container to a pet network as "tomabotchi", so the pet can reach the gateway."""
        me = socket.gethostname()
        try:
            self.client.networks.get(name).connect(me, aliases=[SERVER_ALIAS])
        except NotFound:
            log.warning("not running in a container (%s); pet %s can't reach the gateway", me, name)
        except APIError as e:
            if "already exists" not in str(e) and "already attached" not in str(e):
                raise

    # ---- containers ----

    async def run_pet(self, pet: Pet, image: str, network: str, token: str, limits: Limits,
                      binds: dict[str, str]) -> PetContainer:
        def run():
            try:
                self.client.containers.get(container_name(pet.id)).remove(force=True)
            except NotFound:
                pass
            c = self.client.containers.run(
                image, detach=True, name=container_name(pet.id), hostname=f"pet-{pet.id}",
                user="1000:1000", network=network,
                environment={"PET_TOKEN": token, "BRAIN": pet.brain},
                volumes={src: {"bind": dst, "mode": "rw"} for src, dst in binds.items()},
                mem_limit=limits.memory, memswap_limit=limits.memory, nano_cpus=int(limits.cpus * 1e9),
                pids_limit=limits.pids, cap_drop=["ALL"], security_opt=["no-new-privileges"],
                labels={LABEL: pet.id}, restart_policy={"Name": "unless-stopped"})
            return PetContainer(id=c.id, pet_id=pet.id, network=network, running=True, paused=False)
        return await asyncio.to_thread(run)

    async def pause(self, pet_id: str) -> None:
        await asyncio.to_thread(lambda: self.client.containers.get(container_name(pet_id)).pause())

    async def unpause(self, pet_id: str) -> None:
        await asyncio.to_thread(lambda: self.client.containers.get(container_name(pet_id)).unpause())

    async def stats(self, pet_id: str) -> HomeStats:
        def get():
            s = self.client.containers.get(container_name(pet_id)).stats(stream=False)
            mem = s.get("memory_stats") or {}
            cpu, pre = s.get("cpu_stats") or {}, s.get("precpu_stats") or {}
            d_cpu = (cpu.get("cpu_usage") or {}).get("total_usage", 0) - (pre.get("cpu_usage") or {}).get("total_usage", 0)
            d_sys = cpu.get("system_cpu_usage", 0) - pre.get("system_cpu_usage", 0)
            online = cpu.get("online_cpus") or 1
            pct = (d_cpu / d_sys) * online * 100 if d_sys > 0 else 0.0
            return HomeStats(mem_bytes=mem.get("usage", 0), mem_limit=mem.get("limit", 0), cpu_pct=round(pct, 1))
        return await asyncio.to_thread(get)

    async def remove_pet(self, pet_id: str) -> None:
        def rm():
            try:
                self.client.containers.get(container_name(pet_id)).remove(force=True)
            except NotFound:
                pass
            try:
                net = self.client.networks.get(network_name(pet_id))
                for c in net.attrs.get("Containers") or {}:
                    try:
                        net.disconnect(c, force=True)
                    except APIError:
                        pass
                net.remove()
            except NotFound:
                pass
        await asyncio.to_thread(rm)

    async def list_pet_containers(self) -> list[PetContainer]:
        def ls():
            out = []
            for c in self.client.containers.list(all=True, filters={"label": LABEL}):
                out.append(PetContainer(id=c.id, pet_id=c.labels[LABEL], network=network_name(c.labels[LABEL]),
                                        running=c.status in ("running", "paused"), paused=c.status == "paused"))
            return out
        return await asyncio.to_thread(ls)

    async def start(self, pet_id: str) -> None:
        await asyncio.to_thread(lambda: self.client.containers.get(container_name(pet_id)).start())
