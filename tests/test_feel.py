"""How the app feels: nothing slow on the render path, only changes published, writes and assets cheap."""
import asyncio
import re
import time
from types import SimpleNamespace

from test_web import client  # noqa: F401 - client is a fixture

from tomabotchi import store, views
from tomabotchi.bus import Bus, BusMessage
from tomabotchi.models import Pet
from tomabotchi.supervisor import publish_pet


def msg(event="module-p1", data="<div/>", pet_id="p1"):
    return BusMessage(event=event, data=data, pet_id=pet_id)


async def drain(sub, n=1):
    return [await asyncio.wait_for(anext(sub), 1) for _ in range(n)]


async def test_unchanged_partial_is_not_published_twice():
    bus = Bus()
    sub = bus.subscribe()
    task = asyncio.ensure_future(anext(sub))
    await asyncio.sleep(0)
    assert bus.publish_changed(msg()) is True
    assert bus.publish_changed(msg()) is False
    assert (await task).data == "<div/>"
    assert bus._subs and next(iter(bus._subs))[1].empty()
    await sub.aclose()


async def test_changed_partial_is_published():
    bus = Bus()
    sub = bus.subscribe()
    first = asyncio.ensure_future(anext(sub))
    await asyncio.sleep(0)
    bus.publish_changed(msg(data="<div>a</div>"))
    assert bus.publish_changed(msg(data="<div>b</div>")) is True
    assert (await first).data == "<div>a</div>"
    assert (await anext(sub)).data == "<div>b</div>"
    await sub.aclose()


async def test_notify_is_never_deduplicated():
    bus = Bus()
    sub = bus.subscribe()
    first = asyncio.ensure_future(anext(sub))
    await asyncio.sleep(0)
    bus.publish(msg("notify", "{}", None))
    bus.publish(msg("notify", "{}", None))
    assert (await first).event == "notify" and (await anext(sub)).event == "notify"
    await sub.aclose()


def test_refresh_with_five_big_workspaces_stays_fast(cfg, conn, bus):
    """The workspace walk used to run on every refresh: 656 ms for five pets with 20,000 files each."""
    views.CONFIG = cfg
    pets = []
    for i in range(5):
        pet = Pet(id=f"p{i}", name=f"Pet{i}", shell="grape", look_seed=i, home="python", brain="hermes",
                  model="local-qwen", state="awake", born_at=store.now())
        store.insert_pet(conn, pet)
        ws = cfg.pet_dir(pet.id) / "workspace"
        for d in range(200):
            (ws / f"d{d}").mkdir(parents=True)
            for f in range(100):
                (ws / f"d{d}" / f"f{f}.txt").write_text("x")
        pets.append(pet)
    start = time.perf_counter()
    for _ in range(5):
        for pet in pets:
            publish_pet(conn, bus, pet)
    per_refresh = (time.perf_counter() - start) / 25
    assert per_refresh < 0.05, f"{per_refresh * 1000:.0f} ms per refresh"


def test_render_never_walks_the_workspace(cfg, conn, monkeypatch):
    views.CONFIG = cfg
    pet = Pet(id="p1", name="Pip", shell="grape", look_seed=1, home="python", brain="hermes", model="local-qwen",
              state="awake", born_at=store.now())
    store.insert_pet(conn, pet)
    monkeypatch.setattr(views, "workspace_size", lambda path: (_ for _ in ()).throw(AssertionError("walked")), raising=False)
    ctx = views.pet_context(conn, pet)
    assert "Checking..." in views.render("_home.html", **ctx)


def test_db_uses_wal_and_normal_sync(conn):
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert conn.execute("PRAGMA synchronous").fetchone()[0] == 1


def test_healthz_is_cheap(client, monkeypatch):
    monkeypatch.setattr(views, "pet_context", lambda *a, **k: (_ for _ in ()).throw(AssertionError("rendered")))
    r = client.get("/healthz")
    assert r.status_code == 200 and r.text == "ok"


def test_assets_are_versioned_and_cached_forever(client):
    page = client.get("/").text
    urls = re.findall(r'(?:src|href)="(/static/[^"]+)"', page)
    assert urls and all(u.endswith(f"?v={views.ASSET_VERSION}") for u in urls)
    r = client.get(urls[0])
    assert "immutable" in r.headers["cache-control"] and "max-age=31536000" in r.headers["cache-control"]


def test_the_desk_loads_nothing_from_a_cdn(client):
    page = client.get("/").text
    assert not any(host in page for host in ("unpkg.com", "googleapis", "gstatic"))


async def test_poller_fills_workspace_stats(cfg, conn, bus):
    from tomabotchi.fleet import Fleet
    views.CONFIG = cfg
    views.WORKSPACE_STATS.clear()
    pet = Pet(id="p1", name="Pip", shell="grape", look_seed=1, home="python", brain="hermes", model="local-qwen",
              state="awake", born_at=store.now())
    store.insert_pet(conn, pet)
    ws = cfg.pet_dir("p1") / "workspace"
    ws.mkdir(parents=True)
    (ws / "a.txt").write_text("abc")
    (ws / "b.txt").write_text("de")

    class FakeDocker:
        async def stats(self, pet_id):
            return SimpleNamespace(mem_bytes=1, mem_limit=2, cpu_pct=3.0)

    fleet = Fleet(cfg, conn, bus, FakeDocker())
    fleet._sups["p1"] = SimpleNamespace(pet=pet)
    for t in list(fleet._tasks):
        t.cancel()
    await fleet._poll_once()
    assert views.WORKSPACE_STATS["p1"] == (2, 5)
    assert views.HOME_STATS["p1"].cpu_pct == 3.0


async def test_boot_warms_the_default_home_and_settings_says_so(cfg, conn, bus):
    from tomabotchi.fleet import Fleet
    started = asyncio.Event()
    release = asyncio.Event()

    class FakeDocker:
        async def list_pet_containers(self):
            return []

        async def has_pet_image(self, home):
            return release.is_set()

        async def ensure_pet_image(self, home, progress):
            progress("Building the pet runtime")
            started.set()
            await release.wait()
            return "tag"

        async def stats(self, pet_id):
            raise RuntimeError("none")

    fleet = Fleet(cfg, conn, bus, FakeDocker())
    for t in list(fleet._tasks):
        t.cancel()
    await fleet.boot()
    await asyncio.wait_for(started.wait(), 1)
    assert "Building the pet runtime" in (await fleet.home_status())["python"]
    release.set()
    await asyncio.sleep(0.05)
    assert (await fleet.home_status())["python"].startswith("Ready")
