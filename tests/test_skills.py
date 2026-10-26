"""Skills: the store (built in and downloaded), and installing them where each brain looks."""
import io
import json
import zipfile

import httpx
import pytest
from test_reskin_pages import hatch
from test_web import client  # noqa: F401 - client is a fixture

from tomabotchi import skills, store

SKILL = b"---\nname: PDF tools\ndescription: >\n  Read and fill PDF\n  forms.\n---\n\n# PDF\nUse pypdf.\n"


def zipped(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in files.items():
            z.writestr(name, data)
    return buf.getvalue()


@pytest.fixture
def web(monkeypatch):
    """Serve fake URLs to skills.fetch: {url: (status, bytes)}."""
    pages: dict[str, tuple[int, bytes]] = {}
    real = httpx.AsyncClient

    def handler(request):
        status, body = pages.get(str(request.url), (404, b""))
        return httpx.Response(status, content=body)
    monkeypatch.setattr(skills.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    return pages


def test_frontmatter_reads_name_and_a_folded_description():
    meta = skills.frontmatter(SKILL.decode())
    assert meta == {"name": "PDF tools", "description": "Read and fill PDF forms."}
    assert skills.frontmatter("# no fences\n") == {}


def test_the_built_in_store_ships_skills(cfg):
    names = {s.slug for s in skills.catalog(cfg)}
    assert {"concise-notes", "commit-messages", "python-tests", "web-research"} <= names
    assert all(s.builtin and s.description for s in skills.catalog(cfg))


def test_install_puts_skills_where_each_brain_looks(cfg, tmp_path):
    skill = skills.get(cfg, "concise-notes")
    hermes = skills.install(skill, "hermes", tmp_path / "h")
    assert hermes == tmp_path / "h" / ".hermes" / "skills" / "tomabotchi" / "concise-notes"
    assert (hermes / "SKILL.md").is_file() and (hermes / skills.MARKER).read_text().strip() == "Built in"
    claude = skills.install(skill, "claude", tmp_path / "c")
    assert claude == tmp_path / "c" / ".claude" / "skills" / "concise-notes"
    assert [s.slug for s in skills.installed("claude", tmp_path / "c")] == ["concise-notes"]
    skills.uninstall("concise-notes", "claude", tmp_path / "c")
    assert skills.installed("claude", tmp_path / "c") == []
    with pytest.raises(skills.SkillError, match="practice"):
        skills.install(skill, "fake", tmp_path / "f")


def test_install_leaves_a_skill_it_did_not_put_there_alone(cfg, tmp_path):
    mine = tmp_path / "c" / ".claude" / "skills" / "concise-notes"
    mine.mkdir(parents=True)
    (mine / "SKILL.md").write_text("my own")
    with pytest.raises(skills.SkillError, match="wasn't put there"):
        skills.install(skills.get(cfg, "concise-notes"), "claude", tmp_path / "c")
    assert (mine / "SKILL.md").read_text() == "my own"
    with pytest.raises(skills.SkillError):
        skills.uninstall("concise-notes", "claude", tmp_path / "c")


async def test_download_a_zip_with_a_wrapping_folder(cfg, web):
    web["https://example.com/pdf.zip"] = (200, zipped({"pdf/SKILL.md": SKILL, "pdf/scripts/fill.py": b"print(1)"}))
    s = await skills.download(cfg, "https://example.com/pdf.zip")
    assert (s.slug, s.name, s.builtin, s.source) == ("pdf-tools", "PDF tools", False, "https://example.com/pdf.zip")
    assert (s.path / "scripts" / "fill.py").read_bytes() == b"print(1)"
    assert "pdf-tools" in {x.slug for x in skills.catalog(cfg)}
    skills.delete(cfg, "pdf-tools")
    assert "pdf-tools" not in {x.slug for x in skills.catalog(cfg)}


async def test_download_a_skill_md_and_a_github_folder(cfg, web):
    web["https://raw.githubusercontent.com/o/r/main/s/SKILL.md"] = (200, SKILL)
    s = await skills.download(cfg, "https://github.com/o/r/blob/main/s/SKILL.md")
    assert s.name == "PDF tools"
    listing = [{"name": "SKILL.md", "type": "file", "path": "s/x/SKILL.md", "download_url": "https://raw.example/SKILL.md"},
               {"name": "lib", "type": "dir", "path": "s/x/lib"}]
    web["https://api.github.com/repos/o/r/contents/s/x?ref=main"] = (200, json.dumps(listing).encode())
    web["https://api.github.com/repos/o/r/contents/s/x/lib?ref=main"] = (200, json.dumps(
        [{"name": "a.py", "type": "file", "path": "s/x/lib/a.py", "download_url": "https://raw.example/a.py"}]).encode())
    web["https://raw.example/SKILL.md"] = (200, SKILL.replace(b"PDF tools", b"Lib skill"))
    web["https://raw.example/a.py"] = (200, b"x = 1")
    s = await skills.download(cfg, "https://github.com/o/r/tree/main/s/x")
    assert s.slug == "lib-skill" and (s.path / "lib" / "a.py").read_bytes() == b"x = 1"


@pytest.mark.parametrize("files, why", [
    ({"../evil/SKILL.md": SKILL}, "unsafe"),
    ({"README.md": b"hi"}, "no SKILL.md"),
    ({"SKILL.md": b"# no frontmatter"}, "name and a description"),
])
async def test_bad_downloads_are_refused(cfg, web, files, why):
    web["https://example.com/x.zip"] = (200, zipped(files))
    with pytest.raises(skills.SkillError, match=why):
        await skills.download(cfg, "https://example.com/x.zip")
    assert not (skills.store_dir(cfg)).exists() or not any(skills.store_dir(cfg).iterdir())


async def test_only_https_and_no_shadowing_a_built_in(cfg, web):
    with pytest.raises(skills.SkillError, match="https"):
        await skills.download(cfg, "http://example.com/x.zip")
    web["https://example.com/n.md"] = (200, b"---\nname: concise notes\ndescription: mine\n---\n")
    with pytest.raises(skills.SkillError, match="built-in"):
        await skills.download(cfg, "https://example.com/n.md")
    web["https://example.com/gone.zip"] = (404, b"")
    with pytest.raises(skills.SkillError, match="404"):
        await skills.download(cfg, "https://example.com/gone.zip")


def test_skills_tab_installs_and_removes_for_a_hermes_pet(client):
    from tomabotchi.models import Pet
    conn = client.app.state.conn
    store.insert_pet(conn, Pet(id="h1", name="Quill", shell="grape", look_seed=1, home="python", brain="hermes",
                               model="local-qwen", state="asleep", born_at=store.now()))
    page = client.get("/pets/h1/skills").text
    assert "concise-notes" in page and 'aria-current="page">Skills' in page and "No skills yet" in page
    r = client.post("/pets/h1/skills/concise-notes/install")
    assert r.status_code == 200 and "Remove" in r.text and "next task" in r.headers["hx-trigger"]
    home = client.app.state.cfg.pet_dir("h1") / "brain"
    assert (home / ".hermes" / "skills" / "tomabotchi" / "concise-notes" / "SKILL.md").is_file()
    assert client.post("/pets/h1/skills/concise-notes/remove").status_code == 200
    assert not (home / ".hermes" / "skills" / "tomabotchi" / "concise-notes").exists()
    assert client.post("/pets/h1/skills/nope/install").status_code == 404


def test_practice_pets_see_the_store_but_cannot_install(client):
    pet_id = hatch(client, "Pip", first_task_md="")
    page = client.get(f"/pets/{pet_id}/skills").text
    assert "no brain to load skills into" in page and "Install</button>" not in page
    assert client.post(f"/pets/{pet_id}/skills/concise-notes/install").status_code == 409
