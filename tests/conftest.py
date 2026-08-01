"""Shared fixtures: real frames captured from the live bus.

`tests/reference/captures.json` was extracted from the hexdumps in the
kaco_esphome repo's `sessions/*.log` — bytes seen on the actual RS485 bus at
the site, not synthesised. Any framing or parsing change is validated against
these before it is trusted.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest

REFERENCE = Path(__file__).parent / "reference" / "captures.json"


@dataclass(frozen=True)
class Capture:
    session: str
    address: int
    command: str
    raw: bytes

    def __repr__(self) -> str:  # keeps pytest -v output readable
        return f"{self.session}:addr{self.address}:cmd{self.command}:{len(self.raw)}B"


def _load() -> list[Capture]:
    entries = json.loads(REFERENCE.read_text())
    return [
        Capture(
            session=e["session"],
            address=e["address"],
            command=e["command"],
            raw=bytes.fromhex(e["rx_hex"]),
        )
        for e in entries
    ]


ALL_CAPTURES = _load()

# Well-formed single cmd `0` replies. The oversized ones (131/133 bytes) are
# two overlapping replies from the era when the blueplanet still shared
# address 1 with WR1; they are exercised separately as junk-tolerance cases.
CMD0_CAPTURES = [c for c in ALL_CAPTURES if c.command == "0" and len(c.raw) <= 70]
CMD3_CAPTURES = [c for c in ALL_CAPTURES if c.command == "3" and len(c.raw) <= 70]


@pytest.fixture(params=CMD0_CAPTURES, ids=repr)
def cmd0_capture(request: pytest.FixtureRequest) -> Capture:
    return request.param


@pytest.fixture(params=CMD3_CAPTURES, ids=repr)
def cmd3_capture(request: pytest.FixtureRequest) -> Capture:
    return request.param
