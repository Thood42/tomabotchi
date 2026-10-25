"""A pet's recent work as blocks on a track: what the desk draws in each pet's lane. Pure data, no I/O."""
from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

BlockKind = Literal["said", "think", "tool", "failed", "call", "done"]
WINDOW = timedelta(minutes=60)          # widest zoom; the browser shows 5, 15 or 60 minutes of it


@dataclass(frozen=True)
class Block:
    start: datetime
    end: datetime | None                # None: still going (an open call, or what it's doing right now)
    kind: BlockKind
    label: str                          # one line for the hover tip


@dataclass(frozen=True)
class Bracket:
    start: datetime
    end: datetime
    label: str


@dataclass(frozen=True)
class Track:
    blocks: list[Block]
    loop: Bracket | None


def sample(seed: int, now: datetime, *, calling: bool, sick: bool, busy: bool) -> Track:
    """Stand-in blocks until the real timeline reads the event log."""
    rng = random.Random(seed)
    if not (calling or sick or busy):
        return Track([], None)
    kinds: list[BlockKind] = ["think", "tool", "said", "tool", "think"]
    blocks, at = [], now - timedelta(minutes=14)
    while at < now - timedelta(minutes=2):
        kind = rng.choice(kinds)
        length = timedelta(seconds=rng.randint(15, 70))
        blocks.append(Block(at, at + length, "failed" if sick and kind == "tool" else kind, f"Sample {kind} block"))
        at += length + timedelta(seconds=rng.randint(4, 40))
    if calling:
        blocks.append(Block(now - timedelta(minutes=1, seconds=30), None, "call", "Waiting on you"))
    loop = Bracket(now - timedelta(minutes=9), now - timedelta(minutes=2), "same step, again") if sick else None
    return Track(blocks, loop)
