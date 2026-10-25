"""SOUL.md: rendered from the hatch answers, then grown by lessons."""
from __future__ import annotations

from datetime import date

from .models import HatchForm

KEEPER_BLOCK = """## Working with my keeper
- I keep a short todo list of the steps I'm taking, and update it as I go.
- Before anything my rules mark "ask", I ask and wait for my keeper's answer.
- When my keeper says no and tells me why, I take the reason seriously."""


def _section(title: str, body: str) -> str:
    return f"## {title}\n{body.strip()}\n" if body.strip() else ""


def render_initial(form: HatchForm, *, born: date) -> str:
    parts = [
        f"# {form.name.strip()}\n",
        f"Hatched {born.day} {born:%B %Y}.\n",
        _section("Who I am", form.purpose_md),
        _section("How I talk", form.voice_md),
        _section("Ask first", form.ask_first_md),
        _section("Never", form.never_md),
        "## Lessons\n",
        KEEPER_BLOCK + "\n",
    ]
    return "\n".join(p for p in parts if p)
