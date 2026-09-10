"""The shipped test doubles are themselves load-bearing, so they get tests.

`kaco_rs485.testing` is public API: downstream consumers build their whole test
suite on it, and a fault here surfaces as a mysterious failure in *their* code.
The property that matters most is that the embedded frames stay byte-identical
to the captures they came from — hand-editing a hex literal is easy to do and
produces a frame that fails checksum validation, which reads like a parser bug.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from kaco_rs485.discovery import scan
from kaco_rs485.protocol import parse_cmd0, parse_cmd3, parse_cmd8
from kaco_rs485.testing import (
    BLUEPLANET,
    POWADOR_6400XI,
    POWADOR_8000XI,
    CannedInverter,
    FakeBus,
    a_bus,
)

REFERENCE = Path(__file__).parent / "reference" / "captures.json"

ALL_UNITS = [POWADOR_6400XI, POWADOR_8000XI, BLUEPLANET]


@pytest.fixture(scope="module")
def captured_frames() -> set[bytes]:
    return {bytes.fromhex(e["rx_hex"]) for e in json.loads(REFERENCE.read_text())}


@pytest.mark.parametrize("unit", ALL_UNITS, ids=lambda u: u.name)
def test_embedded_frames_are_verbatim_captures(
    unit: CannedInverter, captured_frames: set[bytes]
) -> None:
    """Every shipped frame must appear byte-for-byte in the capture corpus.

    This is the regression test for transcribing a hex literal by hand and
    dropping or adding a byte.
    """
    for command, raw in unit.replies.items():
        assert raw in captured_frames, f"{unit.name} cmd {command!r} is not a real capture"


def test_xi_frames_parse_and_checksum() -> None:
    """The two xi units must survive the real parsers, checksums included."""
    for unit, expected_type in ((POWADOR_6400XI, "6400xi"), (POWADOR_8000XI, "8000xi")):
        measured = parse_cmd0(unit.reply_to("0"))
        assert measured.inverter_type == expected_type
        assert measured.checksum_ok

        totals = parse_cmd3(unit.reply_to("3"))
        assert totals.total_yield_raw > 0

        assert parse_cmd8(unit.reply_to("8")).raw_text.startswith("K222.")


def test_unknown_command_is_answered_with_silence() -> None:
    """xi units return zero bytes for commands they do not implement."""
    assert POWADOR_6400XI.reply_to("s") == b""


async def test_vacant_address_is_silent_and_costs_a_full_timeout() -> None:
    bus = FakeBus({2: POWADOR_6400XI})

    reply = await bus.request(9, "0")

    assert not reply.responded
    assert reply.arrivals == []
    assert reply.first_byte_ms is None
    assert reply.elapsed_ms == pytest.approx(2500)


async def test_answering_address_reports_its_reply_latency() -> None:
    bus = FakeBus({2: POWADOR_6400XI}, reply_ms=250.0)

    reply = await bus.request(2, "0")

    assert reply.responded
    assert reply.first_byte_ms == pytest.approx(250.0)


async def test_silence_makes_a_unit_go_dark_like_dusk() -> None:
    bus = a_bus([1, 2])
    assert (await bus.request(1, "0")).responded

    bus.silence(1)

    assert not (await bus.request(1, "0")).responded
    assert (await bus.request(2, "0")).responded


async def test_requests_are_recorded_in_order() -> None:
    bus = a_bus([1])

    await bus.request(1, "0")
    await bus.request(1, "3")

    assert bus.requests == [(1, "0"), (1, "3")]
    assert bus.commands_for(1) == ["0", "3"]


async def test_scan_over_the_fake_bus_finds_what_is_there() -> None:
    """The doubles must drive the real discovery path end to end."""
    bus = FakeBus({1: POWADOR_6400XI, 3: BLUEPLANET, 4: POWADOR_8000XI})

    result = await scan(bus, range(1, 6), poll_gap_s=0)

    assert [d.address for d in result.supported] == [1, 4]
    assert [d.address for d in result.unsupported] == [3]
    assert result.saw_any_bytes


async def test_scan_of_a_dark_bus_finds_nothing_but_is_not_an_error() -> None:
    """Night: every unit silent. Distinguishable from a wiring fault only by
    `saw_any_bytes`, which is exactly why discovery reports it separately."""
    result = await scan(FakeBus(), range(1, 6), poll_gap_s=0)

    assert result.found == []
    assert not result.saw_any_bytes
