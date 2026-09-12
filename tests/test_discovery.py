"""Bus discovery, including the cases that are easy to report wrongly."""

from __future__ import annotations

import pytest

from kaco_rs485.discovery import ESCALATE_AT, FAST_START_TIMEOUT_S, scan
from kaco_rs485.testing import CannedInverter, FakeBus
from kaco_rs485.transport import Reply

from .conftest import ALL_CAPTURES, CMD0_CAPTURES, CMD8_XI_CAPTURES

CMD0_FRAME = CMD0_CAPTURES[0].raw
CMD8_FRAME = CMD8_XI_CAPTURES[0].raw
# What `parse_cmd8` pulls out of CMD8_FRAME, e.g. "K222.36DE 6817".
CMD8_TEXT = CMD8_FRAME[5:].split(b"\r", 1)[0].strip().decode()

# A blueplanet / TL-series reply: same request, CRC16 Generic Protocol answer.
GENERIC_FRAME = next(
    (c.raw for c in ALL_CAPTURES if c.raw[4:5] == b"n"),
    b"\n*03n" + b"\x01" * 20 + b"\r",
)


class ScriptedBus(FakeBus):
    """The library double, plus the read window each request was given.

    Only the window recording is local: asserting which pass probed what is
    the whole point of the calibrated-scan tests, and `FakeBus` has no reason
    to expose it.
    """

    def __init__(
        self,
        replies: dict[int, bytes],
        firmware: dict[int, bytes] | None = None,
        reply_ms: float = 100.0,
    ) -> None:
        fw = firmware or {}
        super().__init__(
            {
                addr: CannedInverter(
                    name=f"addr {addr}",
                    replies={"0": raw, "8": fw.get(addr, CMD8_FRAME)},
                )
                for addr, raw in replies.items()
            },
            reply_ms=reply_ms,
        )
        self.windows: list[float | None] = []

    async def request(
        self, address: int, command: str, *, start_timeout_s: float | None = None
    ) -> Reply:
        self.windows.append(start_timeout_s)
        return await super().request(address, command, start_timeout_s=start_timeout_s)

    @property
    def probed(self) -> list[int]:
        """Addresses asked for measured values, in order."""
        return [addr for addr, command in self.requests if command == "0"]


async def test_a_bus_where_nothing_replies_is_probed_twice() -> None:
    """Silence everywhere is ambiguous, so the fast pass is not believed."""
    bus = ScriptedBus({})
    await scan(bus, range(1, 6), poll_gap_s=0)  # type: ignore[arg-type]

    assert bus.probed == [1, 2, 3, 4, 5, 1, 2, 3, 4, 5]
    assert bus.windows[:5] == [FAST_START_TIMEOUT_S] * 5
    assert bus.windows[5:] == [None] * 5


async def test_scan_identifies_inverter_type() -> None:
    bus = ScriptedBus({2: CMD0_FRAME})
    result = await scan(bus, range(1, 6), poll_gap_s=0)  # type: ignore[arg-type]

    assert [d.address for d in result.supported] == [2]
    assert result.supported[0].inverter_type == "6400xi"


async def test_generic_protocol_devices_are_reported_not_skipped() -> None:
    """A blueplanet on the bus is a real device this library cannot read.

    Dropping it silently sends the user hunting for a wiring fault that does
    not exist.
    """
    bus = ScriptedBus({3: GENERIC_FRAME})
    result = await scan(bus, range(1, 6), poll_gap_s=0)  # type: ignore[arg-type]

    assert not result.supported
    assert [d.address for d in result.unsupported] == [3]


async def test_silent_bus_is_distinguishable_from_a_bus_with_no_inverters() -> None:
    """At night everything goes quiet, which must not read as 'wiring fault'."""
    silent = await scan(ScriptedBus({}), range(1, 6), poll_gap_s=0)  # type: ignore[arg-type]
    assert not silent.found
    assert not silent.saw_any_bytes

    answering = await scan(ScriptedBus({1: CMD0_FRAME}), range(1, 6), poll_gap_s=0)  # type: ignore[arg-type]
    assert answering.saw_any_bytes


async def test_progress_is_reported_for_every_address() -> None:
    """Total grows when a re-probe is needed: the work was not known upfront."""
    seen: list[tuple[int, int]] = []
    await scan(
        ScriptedBus({2: CMD0_FRAME}),
        range(1, 6),
        on_progress=lambda d, t: seen.append((d, t)),
        poll_gap_s=0,
    )  # type: ignore[arg-type]
    assert seen == [(1, 5), (2, 5), (3, 5), (4, 5), (5, 5)]


async def test_progress_total_grows_when_the_scan_escalates() -> None:
    seen: list[tuple[int, int]] = []
    await scan(
        ScriptedBus({}),
        range(1, 4),
        on_progress=lambda d, t: seen.append((d, t)),
        poll_gap_s=0,
    )  # type: ignore[arg-type]
    assert seen == [(1, 3), (2, 3), (3, 3), (4, 6), (5, 6), (6, 6)]


async def test_a_fast_reply_is_trusted_and_nothing_is_re_probed() -> None:
    """This bus answered well inside the window, so silence means empty."""
    bus = ScriptedBus({2: CMD0_FRAME}, reply_ms=100.0)

    result = await scan(bus, range(1, 6), poll_gap_s=0)  # type: ignore[arg-type]

    assert [d.address for d in result.supported] == [2]
    assert bus.probed == [1, 2, 3, 4, 5]
    probe_windows = [w for w, (_, c) in zip(bus.windows, bus.requests, strict=True) if c == "0"]
    assert probe_windows == [FAST_START_TIMEOUT_S] * 5


async def test_a_reply_near_the_window_re_probes_only_the_silent() -> None:
    """Slow enough to suggest the window was tight, so the silences are suspect."""
    slow = FAST_START_TIMEOUT_S * 1000 * ESCALATE_AT + 50
    bus = ScriptedBus({2: CMD0_FRAME}, reply_ms=slow)

    await scan(bus, range(1, 6), poll_gap_s=0)  # type: ignore[arg-type]

    assert bus.probed == [1, 2, 3, 4, 5, 1, 3, 4, 5]


async def test_an_inverter_slower_than_the_fast_pass_is_still_found() -> None:
    """The whole point of the re-probe: a slow bus must not lose an inverter."""
    bus = ScriptedBus({2: CMD0_FRAME}, reply_ms=FAST_START_TIMEOUT_S * 1000 + 500)

    result = await scan(bus, range(1, 6), poll_gap_s=0)  # type: ignore[arg-type]

    assert [d.address for d in result.supported] == [2]


async def test_the_fast_pass_can_be_skipped() -> None:
    bus = ScriptedBus({2: CMD0_FRAME})

    await scan(bus, range(1, 4), poll_gap_s=0, fast_timeout_s=None)  # type: ignore[arg-type]

    assert bus.probed == [1, 2, 3]
    assert all(w is None for w in bus.windows)


@pytest.mark.parametrize("garbled", [b"\n*01" + b"\x00" * 60, b"\n*99" + b"\xff" * 60])
async def test_occupied_but_unparseable_addresses_are_still_reported(garbled: bytes) -> None:
    """Something answered. The address is taken, even if the frame was junk."""
    result = await scan(ScriptedBus({4: garbled}), range(1, 6), poll_gap_s=0)  # type: ignore[arg-type]
    assert [d.address for d in result.found] == [4]


async def test_scan_pauses_only_after_a_reply(monkeypatch: pytest.MonkeyPatch) -> None:
    """The settle gap guards against a straggler from the previous inverter.

    A silent address produces no straggler, so waiting after one is pure cost
    — and it is the difference between a 32-address scan taking two minutes
    and taking a few seconds. Addresses 2 and 4 answer here; the other three
    must cost nothing.
    """
    from kaco_rs485 import discovery

    gaps: list[float] = []

    async def recording_sleep(seconds: float) -> None:
        gaps.append(seconds)

    monkeypatch.setattr(discovery.asyncio, "sleep", recording_sleep)
    await scan(ScriptedBus({2: CMD0_FRAME, 4: CMD0_FRAME}), range(1, 6))  # type: ignore[arg-type]

    # Two gaps per replying address: one before its firmware read, one before
    # the next address. Silent addresses still cost nothing.
    assert len(gaps) == 4, "two gaps for each of the two replying addresses"
    assert all(g >= discovery.POLL_GAP_S for g in gaps)


async def test_scan_of_a_silent_bus_pays_no_gaps(monkeypatch: pytest.MonkeyPatch) -> None:
    from kaco_rs485 import discovery

    gaps: list[float] = []

    async def recording_sleep(seconds: float) -> None:
        gaps.append(seconds)

    monkeypatch.setattr(discovery.asyncio, "sleep", recording_sleep)
    await scan(ScriptedBus({}), range(1, 33))  # type: ignore[arg-type]

    assert gaps == []


# --- firmware captured during discovery ----------------------------------


async def test_scan_records_firmware_for_supported_units() -> None:
    """Static data has to be captured while the inverter is demonstrably awake.

    These units leave the bus at dusk, so a caller that waits until its first
    poll to ask may never get an answer at all.
    """
    bus = ScriptedBus({2: CMD0_FRAME})
    result = await scan(bus, range(1, 6), poll_gap_s=0)  # type: ignore[arg-type]

    assert result.supported[0].firmware == CMD8_TEXT
    assert (2, "8") in bus.requests


async def test_silent_addresses_are_not_asked_for_firmware() -> None:
    bus = ScriptedBus({})
    await scan(bus, range(1, 6), poll_gap_s=0)  # type: ignore[arg-type]

    assert [r for r in bus.requests if r[1] == "8"] == []


async def test_generic_protocol_devices_are_not_asked_for_firmware() -> None:
    """A blueplanet is reported, not read — this library cannot poll it."""
    bus = ScriptedBus({3: GENERIC_FRAME})
    result = await scan(bus, range(1, 6), poll_gap_s=0)  # type: ignore[arg-type]

    assert result.unsupported[0].address == 3
    assert [r for r in bus.requests if r[1] == "8"] == []


async def test_unreadable_firmware_still_reports_the_inverter() -> None:
    """A missing version string must never cost us a discovered inverter."""
    bus = ScriptedBus({2: CMD0_FRAME}, firmware={2: b""})
    result = await scan(bus, range(1, 6), poll_gap_s=0)  # type: ignore[arg-type]

    assert [d.address for d in result.supported] == [2]
    assert result.supported[0].inverter_type == "6400xi"
    assert result.supported[0].firmware == ""
