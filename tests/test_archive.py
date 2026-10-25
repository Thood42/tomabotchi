"""Slice 11: retiring a pet into a verified backup, the archive, and hatching from a soul."""
import os
import socket
import zipfile
from datetime import date

import pytest
from test_web import HATCH, client, wait_for  # noqa: F401 - client is a fixture

from tomabotchi import archive, soul, store


def make_pet_dir(root):
    (root / "workspace" / "notes").mkdir(parents=True)
    (root / "workspace" / "notes" / "a.md").write_text("# A\n")
    (root / "workspace" / "data.bin").write_bytes(os.urandom(4096))
    (root / "brain" / ".hermes").mkdir(parents=True)
    (root / "brain" / ".hermes" / "SOUL.md").write_text("# Pet\n")
    (root / "brain" / ".hermes" / "cache" / "audio").mkdir(parents=True)
    os.symlink("/opt/tomabotchi/leash/tomabotchi_leash", root / "brain" / ".hermes" / "leash")   # dangling here
    (root / ".token").write_text("secret")
    sock = socket.socket(socket.AF_UNIX)
    sock.bind(str(root / "brain" / ".hermes" / "gateway.sock"))         # Hermes leaves one of these
    sock.close()


def test_pack_zips_workspace_and_brain_and_verifies(tmp_path):
    make_pet_dir(tmp_path / "pet")
    dest = tmp_path / "archive" / "pip-2026-10-25.zip"
    size = archive.pack(tmp_path / "pet", dest)
    assert size == dest.stat().st_size and not dest.with_suffix(".zip.part").exists()
    with zipfile.ZipFile(dest) as z:
        names = set(z.namelist())
        assert {"workspace/notes/a.md", "workspace/data.bin", "brain/.hermes/SOUL.md", "brain/.hermes/leash",
                "brain/.hermes/cache/audio/"} == names
        assert z.read("workspace/data.bin") == (tmp_path / "pet" / "workspace" / "data.bin").read_bytes()
        assert z.read("brain/.hermes/leash") == b"/opt/tomabotchi/leash/tomabotchi_leash"   # the link, not followed
    assert not any(".token" in n for n in names)                                           # the key dies with the pet
    assert "brain/.hermes/gateway.sock" not in names


def test_backup_names_never_collide(tmp_path):
    first = archive.backup_name("Mr. Pip!", date(2026, 10, 25), tmp_path)
    assert first.name == "mr-pip-2026-10-25.zip"
    first.write_text("x")
    assert archive.backup_name("Mr. Pip!", date(2026, 10, 25), tmp_path).name == "mr-pip-2026-10-25-2.zip"


def test_pack_refuses_a_backup_missing_a_file(tmp_path, monkeypatch):
    make_pet_dir(tmp_path / "pet")
    real = archive._files
    monkeypatch.setattr(archive, "_files", lambda d: {**real(d), "workspace/ghost.txt": tmp_path / "pet" / ".token"})
    real_write = zipfile.ZipFile.write
    monkeypatch.setattr(zipfile.ZipFile, "write",
                        lambda self, p, arc=None, *a, **k: None if arc == "workspace/ghost.txt" else real_write(self, p, arc, *a, **k))
    with pytest.raises(archive.BackupError, match="missing workspace/ghost.txt"):
        archive.pack(tmp_path / "pet", tmp_path / "out.zip")
    assert not (tmp_path / "out.zip").exists() and not (tmp_path / "out.zip.part").exists()


def hatch(client, **extra):
    pet_id = client.post("/pets", data={**HATCH, **extra}, follow_redirects=False).headers["location"].rsplit("/", 1)[1]
    wait_for(lambda: store.pending_calls(client.app.state.conn, pet_id))
    return pet_id


def test_retire_backs_up_cleans_up_and_shelves_the_pet(client):
    conn, cfg = client.app.state.conn, client.app.state.cfg
    pet_id = hatch(client)
    (cfg.pet_dir(pet_id) / "workspace" / "notes").mkdir(parents=True)
    (cfg.pet_dir(pet_id) / "workspace" / "notes" / "keep.md").write_text("Keep me\n")
    call = store.pending_calls(conn, pet_id)[0]
    r = client.post(f"/pets/{pet_id}/retire", data={"reason_md": "Replaced by Ziggy"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/memory"

    pet = store.get_pet(conn, pet_id)
    assert (pet.state, pet.retire_reason) == ("retired", "Replaced by Ziggy")
    assert store.get_call(conn, call.id).outcome == "cancelled"
    assert not cfg.pet_dir(pet_id).exists() and not client.app.state.fleet.is_awake(pet_id)
    backup = cfg.archive_dir / f"biscuit-{date.today().isoformat()}.zip"
    assert pet.backup_path == str(backup) and pet.backup_bytes == backup.stat().st_size
    with zipfile.ZipFile(backup) as z:
        assert z.read("workspace/notes/keep.md") == b"Keep me\n" and "brain/SOUL.md" in z.namelist()

    shelf = client.get("/memory").text
    assert "<h2" in shelf and "Biscuit" in shelf and "“Replaced by Ziggy”" in shelf and backup.name in shelf
    assert f'href="/hatch?from={pet_id}"' in shelf
    dl = client.get(f"/memory/{pet_id}/backup")
    assert dl.status_code == 200 and dl.content == backup.read_bytes()
    home = client.get("/").text
    assert f'id="module-{pet_id}"' not in home and "1 retired pet in the archive" in home
    assert client.get(f"/pets/{pet_id}", follow_redirects=False).headers["location"] == f"/pets/{pet_id}/soul"
    assert "Teach Biscuit" not in client.get(f"/pets/{pet_id}/soul").text
    assert client.post(f"/pets/{pet_id}/retire").status_code == 409


def test_a_failed_backup_deletes_nothing(client, monkeypatch):
    conn, cfg = client.app.state.conn, client.app.state.cfg
    pet_id = hatch(client)

    def broken(pet_dir, dest):
        raise archive.BackupError("the backup is damaged at workspace/x")
    monkeypatch.setattr(archive, "pack", broken)
    assert client.post(f"/pets/{pet_id}/retire").status_code == 500
    assert cfg.pet_dir(pet_id).exists() and store.get_pet(conn, pet_id).state == "asleep"
    assert "Nothing was deleted" in client.get("/").text


def test_hatch_from_a_soul_prefills_and_keeps_its_lessons(client):
    conn = client.app.state.conn
    parent = hatch(client, shell="lagoon", purpose_md="Researching **vector databases**.", never_md="Email anyone.")
    old = store.latest_soul(conn, parent).content
    store.add_soul_revision(conn, parent, soul.add_lesson_line(old, "Cite sources", on=date(2026, 10, 20)), "lesson", None)
    client.post(f"/pets/{parent}/retire")

    page = client.get(f"/hatch?from={parent}").text
    assert f'name="lessons_from" value="{parent}"' in page and 'value="Biscuit Jr"' in page
    assert 'name="shell" value="lagoon"' in page                                        # the parent's shell travels along
    assert "Researching **vector databases**.</textarea>" in page and "Email anyone.</textarea>" in page
    child = client.post("/pets", data={**HATCH, "name": "Biscuit Jr", "purpose_md": "Researching.",
                                       "lessons_from": parent}, follow_redirects=False).headers["location"].rsplit("/", 1)[1]
    new = store.latest_soul(conn, child)
    assert "# Biscuit Jr" in new.content and "- Cite sources. (learned 20 Oct)" in new.content
    assert "Hatched from Biscuit&#39;s soul, keeping its lessons." in client.get(f"/pets/{child}/soul").text
