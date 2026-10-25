"""Lessons: the keeper's words (a deny reason, praise, or teaching) redrafted as one SOUL.md line.

One model call on the pet's own model, straight to the upstream (not through the gateway, so it
isn't metered against the pet's task). If the model is unreachable the keeper's words are used as
they are: a lesson is only ever proposed, and the keeper can reword it before accepting.
"""
from __future__ import annotations

import logging
import re

import httpx

from .config import Config
from .gateway import ensure_ollama_alias
from .models import LessonSource, Pet

log = logging.getLogger(__name__)

TIMEOUT = 600                       # seconds; a local model on CPU is slow, and nothing waits on this
MAX_WORDS = 40

_ASK = {
    "scold": "My keeper stopped me from doing something and said why: {text!r}",
    "praise": "My keeper praised my last task and said: {text!r}",
    "teach": "My keeper wants me to remember: {text!r}",
    "reflect": "Looking back at my last task, I noticed: {text!r}",
}
PROMPT = """You are {name}, an AI agent. Below is your soul file, which you read before every task.

{soul}

---
{ask}

Write ONE short lesson for the "Lessons" section of your soul, so you act on this in future tasks.
Write it as a plain instruction to yourself in the first person or the imperative, under {words} words.
Reply with the lesson line only: no quotes, no bullet, no preamble."""


def prompt_for(pet: Pet, soul_md: str, source: LessonSource, text: str) -> str:
    return PROMPT.format(name=pet.name, soul=soul_md.strip(), ask=_ASK[source].format(text=text.strip()),
                         words=MAX_WORDS)


def clean(reply: str) -> str:
    """The model's reply down to one lesson line: no thinking, quotes, bullets or labels."""
    reply = re.sub(r"<think>.*?</think>", "", reply, flags=re.S).strip()
    line = next((ln.strip() for ln in reply.splitlines() if ln.strip()), "")
    line = re.sub(r"^(?:[-*•]\s+|\d+[.)]\s+|lesson:\s*)", "", line, flags=re.I).strip().strip('"\'“”').strip()
    words = line.split()
    return " ".join(words[:MAX_WORDS]) if words else ""


async def _ask_model(cfg: Config, pet: Pet, prompt: str) -> str:
    model = cfg.model(pet.model)
    async with httpx.AsyncClient(timeout=TIMEOUT) as c:
        if model.provider == "ollama":
            alias = await ensure_ollama_alias(cfg.ollama_url, model.model)
            r = await c.post(f"{cfg.ollama_url}/api/chat", json={
                "model": alias, "stream": False, "think": False,
                "messages": [{"role": "user", "content": prompt}], "options": {"num_predict": 120}})
            r.raise_for_status()
            return r.json()["message"]["content"]
        if not cfg.anthropic_key:
            raise RuntimeError("no ANTHROPIC_API_KEY")
        r = await c.post(f"{cfg.anthropic_url}/v1/messages", headers={
            "x-api-key": cfg.anthropic_key, "anthropic-version": "2023-06-01"}, json={
            "model": model.model, "max_tokens": 120, "messages": [{"role": "user", "content": prompt}]})
        r.raise_for_status()
        return "".join(b.get("text", "") for b in r.json()["content"] if b.get("type") == "text")


async def draft(cfg: Config, pet: Pet, soul_md: str, source: LessonSource, text: str) -> str:
    """One plain-English lesson line drafted from the keeper's words."""
    try:
        line = clean(await _ask_model(cfg, pet, prompt_for(pet, soul_md, source, text)))
    except Exception as e:  # noqa: BLE001 - model down or slow: the keeper's own words still make a lesson
        log.warning("drafting a lesson for %s failed (%s); using the keeper's words", pet.name, e)
        line = ""
    return line or text.strip()
