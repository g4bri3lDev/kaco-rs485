"""Framing must survive arbitrary re-fragmentation.

Over a local serial port, bytes arrived in roughly the order and timing the
wire produced them. Through an ESPHome serial proxy they arrive in whatever
chunks the ESP's loop cycle and the TCP stack decide on. These tests replay
real captured frames split at every boundary that could plausibly occur — and
several that are deliberately hostile — and assert the framing rules reach the
same answer every time.
"""

from __future__ import annotations

import pytest

from kaco_rs485.framing import MIN_CMD0_LEN, is_complete, trim_leading_junk
from kaco_rs485.protocol import parse_cmd0, parse_cmd3

from .conftest import ALL_CAPTURES, Capture


def accumulate(chunks: list[bytes], command: str) -> tuple[bytes, bool]:
    """Feed `chunks` through the framing rules exactly as the transport does."""
    buf = b""
    for chunk in chunks:
        buf = trim_leading_junk(buf + chunk)
        if buf and is_complete(buf, command):
            return buf, True
    return buf, False


def every_split(raw: bytes) -> list[list[bytes]]:
    """All two-way splits, plus byte-at-a-time and all-at-once."""
    splits: list[list[bytes]] = [[raw], [bytes([b]) for b in raw]]
    splits.extend([raw[:i], raw[i:]] for i in range(1, len(raw)))
    return splits


# --- cmd `0` -------------------------------------------------------------


def test_cmd0_reassembles_identically_under_every_split(cmd0_capture: Capture) -> None:
    expected = parse_cmd0(cmd0_capture.raw)

    for chunks in every_split(cmd0_capture.raw):
        buf, complete = accumulate(chunks, "0")
        assert complete, f"framing never completed for split {[len(c) for c in chunks]}"
        assert parse_cmd0(buf) == expected


def test_cmd0_never_completes_before_the_numeric_block(cmd0_capture: Capture) -> None:
    """A false 'complete' here would silently truncate a reply."""
    for length in range(len(cmd0_capture.raw)):
        prefix = trim_leading_junk(cmd0_capture.raw[:length])
        if is_complete(prefix, "0"):
            assert len(prefix) >= MIN_CMD0_LEN
            # CR from the checksum byte onward, never earlier.
            assert b"\r" in prefix[MIN_CMD0_LEN - 1 :]


def test_cmd0_tolerates_turnaround_glitch_bytes(cmd0_capture: Capture) -> None:
    """0x00 bytes appear before frames at bus turnaround."""
    noisy = b"\x00\x00\x00" + cmd0_capture.raw
    buf, complete = accumulate([bytes([b]) for b in noisy], "0")
    assert complete
    assert parse_cmd0(buf) == parse_cmd0(cmd0_capture.raw)


@pytest.mark.parametrize("checksum", [0x0D, 0x0A])
def test_cmd0_checksum_byte_may_be_cr_or_lf(cmd0_capture: Capture, checksum: int) -> None:
    """The reason cmd `0` is length-gated rather than CR-scanned.

    The checksum at offset 57 can legitimately be any byte 0-255. Scanning for
    the first CR *without* a length gate would truncate on a 0x0D checksum;
    with the gate, a CR there is simply the frame ending on time. Either way
    the numeric block must survive intact, which is what this asserts.
    """
    mutated = bytearray(cmd0_capture.raw)
    mutated[57] = checksum
    raw = bytes(mutated)

    buf, complete = accumulate([bytes([b]) for b in raw], "0")
    assert complete
    # The full numeric block survived; only the checksum comparison changes.
    assert len(buf) >= MIN_CMD0_LEN
    assert parse_cmd0(buf).ac_power_w == parse_cmd0(cmd0_capture.raw).ac_power_w


def test_cmd0_ignores_trailing_bytes_from_the_next_reply(cmd0_capture: Capture) -> None:
    """Framing must stop at its own frame, not swallow the following one."""
    raw = cmd0_capture.raw + b"\x00\n*020   4 398.5  1.56   621 227.8"
    buf, complete = accumulate([raw], "0")
    assert complete
    assert parse_cmd0(buf) == parse_cmd0(cmd0_capture.raw)


# --- cmd `3` -------------------------------------------------------------


def test_cmd3_reassembles_identically_under_every_split(cmd3_capture: Capture) -> None:
    expected = parse_cmd3(cmd3_capture.raw)

    for chunks in every_split(cmd3_capture.raw):
        buf, complete = accumulate(chunks, "3")
        assert complete
        assert parse_cmd3(buf) == expected


# --- commands with a trailing checksum byte ------------------------------


@pytest.mark.parametrize("command", ["9", "s"])
def test_variable_length_commands_never_early_exit(command: str) -> None:
    """`9` and `s` end with `<space><checksum><CR>` and have no fixed length.

    There is nothing safe to gate on, so framing must defer to the idle gap
    rather than guess. These are one-shot startup reads, so the cost is nil.
    """
    for capture in ALL_CAPTURES:
        if capture.command != command:
            continue
        for length in range(len(capture.raw) + 1):
            assert not is_complete(trim_leading_junk(capture.raw[:length]), command)


# --- trim_leading_junk ---------------------------------------------------


def test_trim_waits_for_the_lf() -> None:
    assert trim_leading_junk(b"") == b""
    assert trim_leading_junk(b"\x00\x00") == b""
    assert trim_leading_junk(b"\x00\n*01") == b"\n*01"
    assert trim_leading_junk(b"\n*01") == b"\n*01"


def test_cmd0_completes_at_58_bytes_when_the_checksum_is_cr(cmd0_capture: Capture) -> None:
    """A CR checksum ends the frame exactly on time, it does not truncate it.

    Every byte before offset 57 is ASCII numeric or space, so the earliest
    possible CR is the last byte the frame needs. Accepting it lets the read
    finish immediately instead of waiting out the inter-byte gap. The vendor
    datalogger's driver behaves identically.
    """
    mutated = bytearray(cmd0_capture.raw)
    mutated[57] = 0x0D

    buf, complete = accumulate([bytes([b]) for b in mutated], "0")

    assert complete
    assert len(buf) == MIN_CMD0_LEN, "should stop at the checksum, not read on"
    # The whole numeric block survived; only the checksum comparison differs.
    assert parse_cmd0(buf).ac_power_w == parse_cmd0(cmd0_capture.raw).ac_power_w
