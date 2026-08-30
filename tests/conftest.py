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

REFERENCE_DIR = Path(__file__).parent / "reference"


@dataclass(frozen=True)
class Capture:
    session: str
    address: int
    command: str
    raw: bytes

    def __repr__(self) -> str:  # keeps pytest -v output readable
        return f"{self.session}:addr{self.address}:cmd{self.command}:{len(self.raw)}B"


def _load() -> list[Capture]:
    """Load every capture file in `tests/reference/`.

    Any JSON written by `kaco-rs485 sweep --log-dir` drops straight in — that
    is deliberate. Supporting a KACO series we do not own requires bytes from
    somebody who does, and the cost of contributing should be "run one command
    and open a PR with the file", not "learn our fixture format".
    """
    captures: list[Capture] = []
    for path in sorted(REFERENCE_DIR.glob("*.json")):
        for entry in json.loads(path.read_text()):
            raw = bytes.fromhex(entry["rx_hex"])
            if not raw:  # non-responding addresses are recorded too
                continue
            captures.append(
                Capture(
                    # `session` is optional: sweep output does not carry one.
                    session=entry.get("session", path.stem),
                    address=entry["address"],
                    command=entry["command"],
                    raw=raw,
                )
            )
    return captures


ALL_CAPTURES = _load()

# Well-formed single cmd `0` replies. The oversized ones (131/133 bytes) are
# two overlapping replies from the era when the blueplanet still shared
# address 1 with WR1; they are exercised separately as junk-tolerance cases.
CMD0_CAPTURES = [c for c in ALL_CAPTURES if c.command == "0" and len(c.raw) <= 70]
CMD3_CAPTURES = [c for c in ALL_CAPTURES if c.command == "3" and len(c.raw) <= 70]

# Well-formed single cmd `8` replies. Address 1's 79-byte capture is two
# overlapping replies from the era when the blueplanet still shared address 1
# with WR1, so it is excluded the same way the oversized cmd `0` ones are.
CMD8_CAPTURES = [c for c in ALL_CAPTURES if c.command == "8" and c.raw.count(b"\n*") == 1]

# An xi firmware reply specifically. The blueplanet answers cmd `8` with a much
# longer ARM/Config/DSP string, which is a different shape and not what the
# integration names its devices from.
CMD8_XI_CAPTURES = [c for c in CMD8_CAPTURES if b"K222" in c.raw]


@pytest.fixture(params=CMD0_CAPTURES, ids=repr)
def cmd0_capture(request: pytest.FixtureRequest) -> Capture:
    return request.param


@pytest.fixture(params=CMD3_CAPTURES, ids=repr)
def cmd3_capture(request: pytest.FixtureRequest) -> Capture:
    return request.param
