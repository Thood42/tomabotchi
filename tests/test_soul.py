"""Slice 6: the soul page, lessons drafted from the keeper's words, and Markdown everywhere."""
from datetime import date

import pytest
from test_web import HATCH, client, wait_for  # noqa: F401 - client is a fixture

from tomabotchi import lessons, soul, store

SOUL = "# Biscuit\n\nHatched 25 October 2026.\n\n## Who I am\nNotes.\n\n## Working with my keeper\n- Keep a todo list.\n"


@pytest.fixture(autouse=True)
def model(monkeypatch):
    """The lesson model: says what it was asked, so tests can see the keeper's words reached it."""
    asked = []

    async def fake(cfg, pet, prompt):
        asked.append(prompt)
        return "<think>hmm</think>\n- “Check notes/ for an existing note before writing a new one.”\nExtra line."
    monkeypatch.setattr(lessons, "_ask_model", fake)
    return asked


def test_add_lesson_creates_section_when_missing():
    out = soul.add_lesson_line(SOUL, "Cite sources", on=date(2026, 10, 25))
    assert "## Lessons\n- Cite sources. (learned 25 Oct)\n\n## Working with my keeper" in out


def test_add_lesson_appends_with_date():
    once = soul.add_lesson_line(SOUL, "Cite sources.", on=date(2026, 10, 25))
    twice = soul.add_lesson_line(once, "Be brief", on=date(2026, 10, 26))
    assert "- Cite sources. (learned 25 Oct)\n- Be brief. (learned 26 Oct)\n\n## Working" in twice
    assert twice.count("## Lessons") == 1


def test_markdown_is_sanitised():
    html = soul.md_to_html("**hi** <script>alert(1)</script> [x](javascript:alert(1)) [ok](https://a.b)\n\n- one")
    assert "<strong>hi</strong>" in html and "<li>one</li>" in html
    assert "<script>" not in html and 'href="javascript' not in html
    assert 'href="https://a.b"' in html


def test_markdown_preview_endpoint_sanitised(client):
    r = client.post("/markdown/preview", data={"md": "# Hi\n<img src=x onerror=alert(1)>"})
    assert r.status_code == 200 and "<h1>Hi</h1>" in r.text and "<img" not in r.text      # shown as text


def test_clean_keeps_one_plain_line():
    assert lessons.clean("<think>x</think>\n\n1. 'Be brief.'\nmore") == "Be brief."


async def test_draft_falls_back_to_the_keepers_words(monkeypatch, cfg):
    async def down(*a):
        raise OSError("connection refused")
    monkeypatch.setattr(lessons, "_ask_model", down)
    from tomabotchi.models import Pet
    pet = Pet(id="p", name="B", shell="grape", look_seed=1, home="python", brain="fake", model="local-qwen",
              state="awake", born_at=store.now())
    assert await lessons.draft(cfg, pet, SOUL, "teach", "  Cite sources  ") == "Cite sources"


def hatch(client):
    pet_id = client.post("/pets", data=HATCH, follow_redirects=False).headers["location"].rsplit("/", 1)[1]
    conn = client.app.state.conn
    wait_for(lambda: store.pending_calls(conn, pet_id))
    return pet_id, store.pending_calls(conn, pet_id)[0]


def soul_file(client, pet_id):
    return (client.app.state.fleet.brain_home(pet_id) / "SOUL.md").read_text()


def test_deny_with_reason_drafts_a_lesson_and_accepting_rewrites_the_soul(client, model):
    conn = client.app.state.conn
    pet_id, call = hatch(client)
    client.post(f"/calls/{call.id}", data={"option": "reject_once", "reason": "check notes/ first"})
    wait_for(lambda: store.proposed_lessons(conn, pet_id))
    [lesson] = store.proposed_lessons(conn, pet_id)
    assert (lesson.source, lesson.call_id, lesson.note) == ("scold", call.id, "check notes/ first")
    assert lesson.text == "Check notes/ for an existing note before writing a new one."
    assert "check notes/ first" in model[0] and "# Biscuit" in model[0]     # the model saw the soul and the reason
    assert "Add lesson" in client.get(f"/pets/{pet_id}").text              # proposed on the live page too
    assert "Check notes/" not in soul_file(client, pet_id)                  # nothing changes until it's accepted

    assert client.post(f"/lessons/{lesson.id}", data={"action": "accept", "text": ""}).status_code == 204
    latest = store.latest_soul(conn, pet_id)
    assert (latest.version, latest.source) == (2, "lesson")
    assert "- Check notes/ for an existing note before writing a new one. (learned" in latest.content
    assert soul_file(client, pet_id) == latest.content
    assert not store.proposed_lessons(conn, pet_id)
    assert client.post(f"/lessons/{lesson.id}", data={"action": "accept"}).status_code == 409


def test_deny_without_reason_drafts_nothing(client, model):
    pet_id, call = hatch(client)
    client.post(f"/calls/{call.id}", data={"option": "reject_once", "reason": " "})
    assert not store.proposed_lessons(client.app.state.conn, pet_id) and not model


def test_accepted_lesson_reaches_the_next_task_in_a_fresh_session(client):
    conn = client.app.state.conn
    pet_id, call = hatch(client)
    client.post(f"/calls/{call.id}", data={"option": "allow_once"})
    first = store.get_pet(conn, pet_id).acp_session_id
    wait_for(lambda: store.current_task(conn, pet_id).state == "done")
    client.post(f"/pets/{pet_id}/lessons", data={"text_md": "Cite sources", "source": "teach"})
    wait_for(lambda: store.proposed_lessons(conn, pet_id))
    client.post(f"/lessons/{store.proposed_lessons(conn, pet_id)[0].id}", data={"action": "accept", "text": "Cite"})
    assert store.get_pet(conn, pet_id).acp_session_id == first       # no task yet: the session waits
    client.post(f"/pets/{pet_id}/tasks", data={"prompt_md": "Write a hello note"})
    assert store.get_pet(conn, pet_id).acp_session_id != first
    assert "- Cite. (learned" in soul_file(client, pet_id)                # the keeper's rewording won


def test_teach_and_praise_draft_lessons_and_fill_the_timeline(client, model):
    conn = client.app.state.conn
    pet_id, _ = hatch(client)
    assert client.post(f"/pets/{pet_id}/lessons", data={"text_md": "", "source": "teach"}).status_code == 400
    assert client.post(f"/pets/{pet_id}/lessons", data={"text_md": "", "source": "praise"}).status_code == 202
    wait_for(lambda: store.proposed_lessons(conn, pet_id))
    [praise] = store.proposed_lessons(conn, pet_id)
    assert praise.source == "praise" and "Write a hello note" in praise.note       # praise of the last task
    client.post(f"/lessons/{praise.id}", data={"action": "discard"})
    client.post(f"/pets/{pet_id}/soul", data={"content_md": SOUL, "note": "Trimmed it down"})
    page = client.get(f"/pets/{pet_id}/soul").text
    assert "SOUL.md" in page and "version 2" in page and "Changed 1 time since" in page
    timeline = page[page.index("What shaped this soul"):]
    assert timeline.index("You edited") < timeline.index("You praised") < timeline.index("Hatched")
    assert "Trimmed it down" in timeline and "You discarded the lesson." in timeline


def test_soul_edit_rejects_empty_and_page_404s_for_strangers(client):
    pet_id, _ = hatch(client)
    assert client.post(f"/pets/{pet_id}/soul", data={"content_md": "  "}).status_code == 400
    assert client.get("/pets/nobody/soul").status_code == 404


def test_hatch_fields_take_markdown(client):
    page = client.get("/hatch").text
    assert page.count('data-md="preview"') == 5
    assert 'name="purpose_md"' in page and 'name="first_task_md"' in page
