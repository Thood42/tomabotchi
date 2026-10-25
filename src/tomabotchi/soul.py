"""SOUL.md: rendered from the hatch answers, then grown by lessons."""
from __future__ import annotations

from datetime import date

import nh3
from markdown_it import MarkdownIt

from .models import HatchForm

KEEPER_BLOCK = """## Working with my keeper
- I keep a short todo list of the steps I'm taking, and update it as I go.
- My keeper approves my actions through tomabotchi. When a tool needs their OK, tomabotchi asks
  them for me and waits, so I just use the tool. I don't ask for permission in chat first.
- When my keeper says no and tells me why, I take the reason seriously."""


def _section(title: str, body: str) -> str:
    return f"## {title}\n{body.strip()}\n" if body.strip() else ""


def render_initial(form: HatchForm, *, born: date) -> str:
    parts = [
        f"# {form.name.strip()}\n",
        f"Hatched {born.day} {born:%B %Y}.\n",
        _section("Who I am", form.purpose_md),
        _section("How I talk", form.voice_md),
        _section("What my keeper wants to approve", form.ask_first_md),
        _section("Never", form.never_md),
        "## Lessons\n",
        KEEPER_BLOCK + "\n",
    ]
    return "\n".join(p for p in parts if p)


def add_lesson_line(soul_md: str, lesson: str, *, on: date) -> str:
    """Append "- <lesson>. (learned 25 Oct)" at the end of ## Lessons, creating the section if missing."""
    return insert_lesson(soul_md, f"- {lesson.strip().rstrip('.')}. (learned {on.day} {on:%b})")


def insert_lesson(soul_md: str, line: str) -> str:
    """Append an already-formatted lesson line at the end of ## Lessons."""
    lines = soul_md.rstrip("\n").split("\n")
    try:
        start = next(i for i, ln in enumerate(lines) if ln.strip().lower() == "## lessons")
    except StopIteration:
        keeper = next((i for i, ln in enumerate(lines) if ln.strip() == KEEPER_BLOCK.split("\n")[0]), len(lines))
        block = ["## Lessons", line, ""]
        return "\n".join(lines[:keeper] + ([""] if keeper and lines[keeper - 1].strip() else []) + block
                         + lines[keeper:]).rstrip("\n") + "\n"
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("#")), len(lines))
    while end > start + 1 and not lines[end - 1].strip():
        end -= 1
    lines[end:end] = [line]
    if end + 1 < len(lines) and lines[end + 1].strip():
        lines.insert(end + 1, "")
    return "\n".join(lines) + "\n"


_MD = MarkdownIt("commonmark", {"html": False, "linkify": False}).enable("table").enable("strikethrough")
_TAGS = {"p", "br", "strong", "em", "code", "pre", "blockquote", "ul", "ol", "li", "h1", "h2", "h3", "h4", "h5",
         "h6", "hr", "a", "table", "thead", "tbody", "tr", "th", "td", "del", "s"}


def md_to_html(md: str) -> str:
    """Keeper-written Markdown to HTML that is safe to put in the page."""
    return nh3.clean(_MD.render(md or ""), tags=_TAGS, attributes={"a": {"href", "title"}},
                     url_schemes={"http", "https", "mailto"}, link_rel="noopener noreferrer")


def sections(soul_md: str) -> dict[str, str]:
    """## heading → its body, as written."""
    out, title = {}, None
    for line in soul_md.splitlines():
        if line.startswith("## "):
            title = line[3:].strip()
            out[title] = []
        elif title is not None:
            out[title].append(line)
    return {t: "\n".join(body).strip() for t, body in out.items()}


def prefill_from(soul_md: str) -> dict[str, str]:
    """The hatch screen's answers, read back out of a soul (for "Hatch a new pet from this soul")."""
    s = sections(soul_md)
    return {"purpose_md": s.get("Who I am", ""), "voice_md": s.get("How I talk", ""),
            "ask_first_md": s.get("What my keeper wants to approve", ""), "never_md": s.get("Never", "")}


def with_lessons(soul_md: str, lessons_md: str) -> str:
    """A fresh soul that keeps an old one's lessons."""
    for line in (ln for ln in lessons_md.splitlines() if ln.strip()):
        soul_md = insert_lesson(soul_md, line)
    return soul_md
