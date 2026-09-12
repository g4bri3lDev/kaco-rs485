"""What the captured corpus proves about the hardware.

Some findings are silences. `conftest._load` drops unanswered requests because
they make poor parser fixtures, so those live in `ALL_RECORDS` instead — and the
most consequential one is asserted here, because a library decision rests on it.
"""

from __future__ import annotations

from .conftest import ALL_RECORDS

# The three units on the reference bus: 6400xi at 1 and 2, 8000xi at 4.
XI_ADDRESSES = {1, 2, 4}

# Captured 2026-09-10 17:47 CEST with the Home Assistant integration disabled,
# all three units awake and in MPP tracking.
SERIAL_PROBE = "20260910_xi_serial_probe"


def _probe(command: str) -> list[dict]:
    return [r for r in ALL_RECORDS if r["session"] == SERIAL_PROBE and r["command"] == command]


def test_xi_units_do_not_implement_the_serial_number_command() -> None:
    """The evidence behind having no serial number to identify a device by.

    Spec 3.5 documents `s` under Generic Protocol only, and Table 3 does not
    list it for series "00". That alone would not settle it — the same table
    omits `8` and `9`, which these units do answer — so it was measured.

    Consequence: devices built from this library cannot carry a serial number,
    and consumers must identify an inverter by its bus address instead.
    """
    probes = _probe("s")

    assert {r["address"] for r in probes} == XI_ADDRESSES
    for record in probes:
        assert not record["responded"], f"address {record['address']} answered `s`"
        assert record["rx_hex"] == ""


def test_the_same_units_answered_everything_else_in_that_sweep() -> None:
    """Controls: the silence above is a refusal, not a dead bus.

    Without this, a flat battery or an unplugged adapter would look identical.
    """
    for command in ("0", "3", "8", "9"):
        answered = {r["address"] for r in _probe(command) if r["responded"]}
        assert answered == XI_ADDRESSES, f"cmd {command!r} answered by {answered}"


LAN_FULL_SWEEP = "20260912_lan_full_sweep"
LAN_DUPLICATE = "20260912_lan_duplicate_frame"

# The blueplanet shares this bus; it answers, just not in a protocol we read.
BLUEPLANET_ADDRESS = 3


def _session(name: str) -> list[dict]:
    return [r for r in ALL_RECORDS if r["session"] == name]


def test_no_vacant_address_ever_answered() -> None:
    """A full 32-address sweep: only the four real devices replied.

    The scan's fast path assumes silence means empty, so a vacant address
    producing bytes would break it.
    """
    answered = {r["address"] for r in _session(LAN_FULL_SWEEP) if r["responded"]}

    assert answered == {1, 2, 4, BLUEPLANET_ADDRESS}


def test_the_blueplanet_answers_on_its_own_protocol() -> None:
    """Address 3 replies to cmd `0` with the CRC16 Generic Protocol marker."""
    cmd0 = next(
        r
        for r in _session(LAN_FULL_SWEEP)
        if r["address"] == BLUEPLANET_ADDRESS and r["command"] == "0"
    )

    assert bytes.fromhex(cmd0["rx_hex"])[4:5] == b"n"


def test_a_serial_reply_that_is_really_a_duplicate() -> None:
    """The reason `s` is not probed: its read window absorbs stale frames.

    `s` has no framing rule and no parser, so the read waits out the idle gap
    and accepts whatever turns up. Here a cmd `9` reply already received
    arrived a second time and was recorded as a serial number. Every real
    command echoes its own byte and is rejected instead.
    """
    spurious = [r for r in _session(LAN_DUPLICATE) if r["command"] == "s" and r["responded"]]

    assert spurious, "this capture exists to hold the duplicate"
    for record in spurious:
        echoed = bytes.fromhex(record["rx_hex"])[4:5]
        assert echoed != b"s", "would mean an xi unit really answered `s`"
        assert echoed == b"9"
