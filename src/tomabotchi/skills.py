"""Skills: folders a pet's brain loads, each a SKILL.md (with a name and a description up top) plus any files it
uses. The store holds the skills that ship with tomabotchi (skill_store/) and the ones you download into
data/skills/.
Installing copies a skill into the brain's own skills folder in the pet's brain home, which is where Hermes and
Claude Code look for them.
"""
from __future__ import annotations

import io
import re
import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from urllib.parse import urlparse

import httpx

from .config import Config

BUILTIN = Path(__file__).with_name("skill_store")
MARKER = ".tomabotchi-skill"          # in an installed copy: where it came from
SOURCE = ".source"                    # in a downloaded skill: the URL it came from
MAX_BYTES = 5_000_000
MAX_FILES = 200
_GITHUB = re.compile(r"^https://github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+)/(?P<kind>tree|blob)/(?P<ref>[^/]+)/(?P<path>.+)$")


class SkillError(ValueError):
    """Why a skill couldn't be read, fetched or installed, in the keeper's words."""


@dataclass(frozen=True)
class Skill:
    slug: str
    name: str
    description: str
    path: Path
    builtin: bool
    source: str                       # "Built in", or the URL it was downloaded from


def frontmatter(md: str) -> dict[str, str]:
    """The `key: value` lines between the leading --- fences. Enough YAML for a skill's name and description,
    including a folded (>) or literal (|) description."""
    lines = md.lstrip("﻿").splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    out: dict[str, str] = {}
    key = None
    for line in lines[1:]:
        if line.strip() == "---":
            break
        m = re.match(r"^([A-Za-z_][\w-]*):\s*(.*)$", line)
        if m:
            key, value = m.group(1), m.group(2).strip()
            out[key] = "" if value in (">", "|", ">-", "|-") else value.strip("'\"")
        elif key and line.startswith((" ", "\t")):
            out[key] = (out[key] + " " + line.strip()).strip()
    return out


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:64]


def read(folder: Path, *, builtin: bool) -> Skill:
    md = folder / "SKILL.md"
    if not md.is_file():
        raise SkillError("a skill needs a SKILL.md")
    meta = frontmatter(md.read_text(errors="replace"))
    if not meta.get("name") or not meta.get("description"):
        raise SkillError("its SKILL.md needs a name and a description at the top, between --- lines")
    source = "Built in" if builtin else ((folder / SOURCE).read_text().strip() if (folder / SOURCE).is_file() else "Downloaded")
    return Skill(slug=folder.name, name=meta["name"], description=meta["description"], path=folder, builtin=builtin,
                 source=source)


def store_dir(cfg: Config) -> Path:
    return cfg.data_dir / "skills"


def catalog(cfg: Config) -> list[Skill]:
    """Every skill in the store, built-in ones first, then downloads, each by name."""
    out = []
    for root, builtin in ((BUILTIN, True), (store_dir(cfg), False)):
        if root.is_dir():
            for folder in sorted(p for p in root.iterdir() if p.is_dir()):
                try:
                    out.append(read(folder, builtin=builtin))
                except SkillError:
                    continue
    return out


def get(cfg: Config, slug: str) -> Skill:
    for s in catalog(cfg):
        if s.slug == slug:
            return s
    raise KeyError(slug)


# ---- in a pet ----

def install_root(brain: str, brain_home: Path) -> Path | None:
    """Where this brain loads skills from. Hermes groups skills in categories, so ours get their own."""
    if brain == "hermes":
        return brain_home / ".hermes" / "skills" / "tomabotchi"
    if brain == "claude":
        return brain_home / ".claude" / "skills"
    return None                          # the practice brain has nothing to load them into


def installed(brain: str, brain_home: Path) -> list[Skill]:
    root = install_root(brain, brain_home)
    out = []
    if root and root.is_dir():
        for folder in sorted(p for p in root.iterdir() if p.is_dir() and (p / MARKER).is_file()):
            try:
                s = read(folder, builtin=False)
            except SkillError:
                continue
            out.append(Skill(s.slug, s.name, s.description, s.path, False, (folder / MARKER).read_text().strip()))
    return out


def install(skill: Skill, brain: str, brain_home: Path) -> Path:
    root = install_root(brain, brain_home)
    if root is None:
        raise SkillError("practice pets have no brain to load skills into")
    dest = root / skill.slug
    if dest.exists() and not (dest / MARKER).is_file():
        raise SkillError(f"{dest.name} is already there and wasn't put there by tomabotchi")
    if dest.exists():
        shutil.rmtree(dest)
    root.mkdir(parents=True, exist_ok=True)
    shutil.copytree(skill.path, dest, ignore=shutil.ignore_patterns(SOURCE))
    (dest / MARKER).write_text(skill.source + "\n")
    return dest


def uninstall(slug: str, brain: str, brain_home: Path) -> None:
    root = install_root(brain, brain_home)
    dest = root / slug if root else None
    if not dest or not (dest / MARKER).is_file():
        raise SkillError("that skill isn't installed")
    shutil.rmtree(dest)


# ---- downloading into the store ----

def _safe(name: str) -> PurePosixPath | None:
    p = PurePosixPath(name)
    if p.is_absolute() or ".." in p.parts or not p.parts:
        return None
    return p


def _unpack(files: dict[str, bytes], into: Path) -> None:
    """Write a skill's files (relative path -> bytes), dropping one wrapping folder if everything sits in it."""
    if not files:
        raise SkillError("there was nothing in it")
    if len(files) > MAX_FILES or sum(len(b) for b in files.values()) > MAX_BYTES:
        raise SkillError(f"it's too big (a skill can have up to {MAX_FILES} files and {MAX_BYTES // 1_000_000} MB)")
    unsafe = next((n for n in files if _safe(n) is None), None)
    if unsafe:
        raise SkillError(f"it contains an unsafe path: {unsafe}")
    tops = {PurePosixPath(n).parts[0] for n in files}
    if "SKILL.md" not in files and len(tops) == 1:
        files = {str(PurePosixPath(*PurePosixPath(n).parts[1:])): b for n, b in files.items() if len(PurePosixPath(n).parts) > 1}
    if "SKILL.md" not in files:
        raise SkillError("there's no SKILL.md at its top")
    for name, data in files.items():
        target = into / _safe(name)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)


def _from_zip(data: bytes) -> dict[str, bytes]:
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise SkillError("that isn't a zip file") from None
    infos = [i for i in z.infolist() if not i.is_dir() and not i.filename.startswith("__MACOSX/")]
    if len(infos) > MAX_FILES or sum(i.file_size for i in infos) > MAX_BYTES:
        raise SkillError("it's too big for a skill")
    return {i.filename: z.read(i) for i in infos}


async def _get(client: httpx.AsyncClient, url: str) -> httpx.Response:
    r = await client.get(url)
    if r.status_code != 200:
        raise SkillError(f"{urlparse(url).netloc} answered {r.status_code}")
    if len(r.content) > MAX_BYTES:
        raise SkillError("it's too big for a skill")
    return r


async def _from_github_folder(client: httpx.AsyncClient, owner: str, repo: str, ref: str, path: str) -> dict[str, bytes]:
    files: dict[str, bytes] = {}

    async def walk(sub: str, rel: str) -> None:
        r = await _get(client, f"https://api.github.com/repos/{owner}/{repo}/contents/{sub}?ref={ref}")
        for item in r.json():
            if len(files) >= MAX_FILES:
                raise SkillError("it has too many files for a skill")
            name = f"{rel}{item['name']}"
            if item["type"] == "dir":
                await walk(item["path"], name + "/")
            elif item["type"] == "file" and item.get("download_url"):
                files[name] = (await _get(client, item["download_url"])).content
    await walk(path.rstrip("/"), "")
    return files


async def fetch(url: str) -> dict[str, bytes]:
    """The files of a skill at a URL: a .zip, a SKILL.md, or a folder on GitHub (…/tree/<branch>/<path>)."""
    url = url.strip()
    if urlparse(url).scheme != "https":
        raise SkillError("use an https:// link")
    async with httpx.AsyncClient(timeout=20, follow_redirects=True, headers={"User-Agent": "tomabotchi"}) as client:
        try:
            m = _GITHUB.match(url)
            if m and m["kind"] == "tree":
                return await _from_github_folder(client, m["owner"], m["repo"], m["ref"], m["path"])
            if m and m["kind"] == "blob":
                url = f"https://raw.githubusercontent.com/{m['owner']}/{m['repo']}/{m['ref']}/{m['path']}"
            r = await _get(client, url)
        except httpx.HTTPError as e:
            raise SkillError(f"couldn't fetch it ({e.__class__.__name__})") from None
    if r.content[:2] == b"PK" or urlparse(url).path.endswith(".zip"):
        return _from_zip(r.content)
    text = r.content.decode("utf-8", errors="replace")
    if not frontmatter(text):
        raise SkillError("that link isn't a zip or a SKILL.md")
    return {"SKILL.md": r.content}


async def download(cfg: Config, url: str) -> Skill:
    """Fetch a skill into the store. A skill with the same name replaces the earlier download."""
    files = await fetch(url)
    with tempfile.TemporaryDirectory() as tmp:
        staging = Path(tmp) / "skill"
        staging.mkdir()
        _unpack(files, staging)
        skill = read(staging, builtin=False)
        slug = slugify(skill.name)
        if not slug:
            raise SkillError("its name has no letters or digits")
        if (BUILTIN / slug).is_dir():
            raise SkillError(f"{skill.name} is already a built-in skill")
        (staging / SOURCE).write_text(url.strip() + "\n")
        dest = store_dir(cfg) / slug
        if dest.exists():
            shutil.rmtree(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(staging, dest)
    return read(dest, builtin=False)


def delete(cfg: Config, slug: str) -> None:
    if not re.fullmatch(r"[a-z0-9-]+", slug) or not (store_dir(cfg) / slug).is_dir():
        raise SkillError("only downloaded skills can be deleted")
    dest = store_dir(cfg) / slug
    shutil.rmtree(dest)
