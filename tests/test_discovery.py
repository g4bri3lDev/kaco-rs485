"""Bus discovery, including the cases that are easy to report wrongly."""

from __future__ import annotations

import pytest

from kaco_rs485.discovery import scan
from kaco_rs485.transport import Reply

from .conftest import ALL_CAPTURES, CMD0_CAPTURES

CMD0_FRAME = CMD0_CAPTURES[0].raw
# A blueplanet / TL-series reply: same request, CRC16 Generic Protocol answer.
GENERIC_FRAME = next(
    (c.raw for c in ALL_CAPTURES if c.raw[4:5] == b"n"),
    b"\n*03n" + b"\x01" * 20 + b"\r",
)


class ScriptedBus:
    def __init__(self, replies: dict[int, bytes]) -> None:
        self.replies = replies
        self.probed: list[int] = []

    async def request(self, address: int, command: str) -> Reply:
        self.probed.append(address)
        raw = self.replies.get(address, b"")
        return Reply(request=b"", raw=raw, elapsed_ms=2000.0)


async def test_scan_probes_every_address_once() -> None:
    bus = ScriptedBus({})
    await scan(bus, range(1, 6))  # type: ignore[arg-type]
    assert bus.probed == [1, 2, 3, 4, 5]


async def test_scan_identifies_inverter_type() -> None:
    bus = ScriptedBus({2: CMD0_FRAME})
    result = await scan(bus, range(1, 6))  # type: ignore[arg-type]

    assert [d.address for d in result.supported] == [2]
    assert result.supported[0].inverter_type == "6400xi"


async def test_generic_protocol_devices_are_reported_not_skipped() -> None:
    """A blueplanet on the bus is a real device this library cannot read.

    Dropping it silently sends the user hunting for a wiring fault that does
    not exist.
    """
    bus = ScriptedBus({3: GENERIC_FRAME})
    result = await scan(bus, range(1, 6))  # type: ignore[arg-type]

    assert not result.supported
    assert [d.address for d in result.unsupported] == [3]


async def test_silent_bus_is_distinguishable_from_a_bus_with_no_inverters() -> None:
    """At night everything goes quiet, which must not read as 'wiring fault'."""
    silent = await scan(ScriptedBus({}), range(1, 6))  # type: ignore[arg-type]
    assert not silent.found
    assert not silent.saw_any_bytes

    answering = await scan(ScriptedBus({1: CMD0_FRAME}), range(1, 6))  # type: ignore[arg-type]
    assert answering.saw_any_bytes


async def test_progress_is_reported_for_every_address() -> None:
    seen: list[tuple[int, int]] = []
    await scan(ScriptedBus({}), range(1, 6), on_progress=lambda d, t: seen.append((d, t)))  # type: ignore[arg-type]
    assert seen == [(1, 5), (2, 5), (3, 5), (4, 5), (5, 5)]


@pytest.mark.parametrize("garbled", [b"\n*01" + b"\x00" * 60, b"\n*99" + b"\xff" * 60])
async def test_occupied_but_unparseable_addresses_are_still_reported(garbled: bytes) -> None:
    """Something answered. The address is taken, even if the frame was junk."""
    result = await scan(ScriptedBus({4: garbled}), range(1, 6))  # type: ignore[arg-type]
    assert [d.address for d in result.found] == [4]
