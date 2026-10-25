"""Retirement backups: a pet's workspace and brain home, zipped into the archive folder and checked
before anything is deleted."""
from __future__ import annotations

import os
import re
import stat
import zipfile
from datetime import date
from pathlib import Path


class BackupError(RuntimeError):
    pass


def backup_name(pet_name: str, on: date, archive_dir: Path) -> Path:
    """<name>-<YYYY-MM-DD>.zip, with -2, -3... when that name is taken."""
    slug = re.sub(r"[^a-z0-9]+", "-", pet_name.lower()).strip("-") or "pet"
    path = archive_dir / f"{slug}-{on.isoformat()}.zip"
    n = 2
    while path.exists():
        path = archive_dir / f"{slug}-{on.isoformat()}-{n}.zip"
        n += 1
    return path


def _files(pet_dir: Path) -> dict[str, Path]:
    """arcname → file, for workspace/ and brain/: regular files, and symlinks kept as links (not
    followed). Sockets and pipes (Hermes leaves a gateway.sock) mean nothing once the pet is gone.
    The pet's gateway token lives beside these folders, so it dies with the pet."""
    out = {}
    for top in ("workspace", "brain"):
        root = pet_dir / top
        for dirpath, dirnames, filenames in os.walk(root):
            here = Path(dirpath)
            if not dirnames and not filenames and here != root:
                out[f"{top}/{here.relative_to(root).as_posix()}/"] = here     # keep empty folders too
            for name in filenames + [d for d in dirnames if (here / d).is_symlink()]:
                p = Path(dirpath) / name
                if p.is_symlink() or stat.S_ISREG(p.lstat().st_mode):
                    out[f"{top}/{p.relative_to(root).as_posix()}"] = p
    return out


def pack(pet_dir: Path, dest: Path) -> int:
    """Write the zip and verify it: every file present, same size, CRCs good. Returns its size."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    files = _files(pet_dir)
    tmp = dest.with_suffix(".zip.part")
    try:
        _write_and_check(files, tmp)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    tmp.replace(dest)
    return dest.stat().st_size


def _write_and_check(files: dict[str, Path], tmp: Path) -> None:
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for arc, p in sorted(files.items()):
            if arc.endswith("/"):
                z.writestr(zipfile.ZipInfo(arc), b"")               # an empty folder
            elif p.is_symlink():
                info = zipfile.ZipInfo(arc)
                info.external_attr = 0o120777 << 16              # stored as a symlink
                z.writestr(info, os.readlink(p))
            else:
                z.write(p, arc)
    with zipfile.ZipFile(tmp) as z:
        bad = z.testzip()
        if bad:
            raise BackupError(f"the backup is damaged at {bad}")
        sizes = {i.filename: i.file_size for i in z.infolist()}
    for arc, p in files.items():
        if arc not in sizes or (p.is_file() and not p.is_symlink() and sizes[arc] != p.stat().st_size):
            raise BackupError(f"the backup is missing {arc}")
