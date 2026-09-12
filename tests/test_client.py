"""Scheduling and availability behaviour.

The parsing is covered elsewhere; what matters here is that the client is a
good citizen on a shared bus — it paces itself, it stops hammering inverters
that have gone dark, and it notices when they come back.
"""

from __future__ import annotations

from typing import Any

import pytest

from kaco_rs485 import client as client_module
from kaco_rs485.client import SLEEP_AFTER_MISSES, SLEEP_RETRY_S, KacoRs485Client
from kaco_rs485.testing import CannedInverter, FakeBus, a_bus
from kaco_rs485.transport import Reply

from .conftest import CMD0_CAPTURES, CMD3_CAPTURES, CMD8_XI_CAPTURES

CMD0_FRAME = CMD0_CAPTURES[0].raw
CMD3_FRAME = CMD3_CAPTURES[0].raw
CMD8_FRAME = CMD8_XI_CAPTURES[0].raw

# What `parse_cmd8` pulls out of CMD8_FRAME, e.g. "K222.36DE 6817".
CMD8_TEXT = CMD8_FRAME[5:].split(b"\r", 1)[0].strip().decode()


# The frames this module's assertions are written against, as a canned unit.
XI_UNIT = CannedInverter(
    name="xi under test",
    replies={"0": CMD0_FRAME, "3": CMD3_FRAME, "8": CMD8_FRAME},
)


def a_test_bus(alive: set[int], **kwargs: Any) -> FakeBus:
    """The library double, answering for `alive` and silent elsewhere."""
    return FakeBus({addr: XI_UNIT for addr in alive}, **kwargs)


def commands(bus: FakeBus, command: str) -> list[tuple[int, str]]:
    return [r for r in bus.requests if r[1] == command]


@pytest.fixture(autouse=True)
def no_real_pacing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the bus-settle gaps out of the test runtime, not out of the code."""

    async def instant(_seconds: float) -> None:
        return None

    monkeypatch.setattr(client_module.asyncio, "sleep", instant)


@pytest.fixture
def fake_clock(monkeypatch: pytest.MonkeyPatch) -> Any:
    class Clock:
        now = 1000.0

        def advance(self, seconds: float) -> None:
            self.now += seconds

    clock = Clock()
    monkeypatch.setattr(client_module.time, "monotonic", lambda: clock.now)
    return clock


async def test_live_inverters_are_polled_every_cycle() -> None:
    bus = a_test_bus({1, 2, 4})
    client = KacoRs485Client(bus, [1, 2, 4])  # type: ignore[arg-type]

    await client.poll_cycle()

    assert {addr for addr, _ in bus.requests} == {1, 2, 4}
    for state in client.states.values():
        assert state.available
        assert state.consecutive_misses == 0
        assert state.measured is not None
        assert state.totals is not None


async def test_silent_inverter_becomes_unavailable_after_three_misses() -> None:
    bus = a_test_bus(set())
    client = KacoRs485Client(bus, [1])  # type: ignore[arg-type]

    for cycle in range(SLEEP_AFTER_MISSES):
        await client.poll_cycle()
        assert client.states[1].consecutive_misses == cycle + 1

    assert client.states[1].asleep
    assert not client.states[1].available


async def test_sleeping_inverter_is_skipped_until_the_retry_window(fake_clock: Any) -> None:
    bus = a_test_bus(set())
    client = KacoRs485Client(bus, [1])  # type: ignore[arg-type]

    for _ in range(SLEEP_AFTER_MISSES):
        await client.poll_cycle()
        fake_clock.advance(10.0)
    assert client.states[1].asleep

    polled_so_far = len(bus.requests)

    # Cycles inside the retry window must not touch the bus at all — this is
    # the whole point: three dark inverters would otherwise burn 2.5 s of
    # timeout per command, every cycle, all night.
    fake_clock.advance(SLEEP_RETRY_S / 2)
    await client.poll_cycle()
    assert len(bus.requests) == polled_so_far

    fake_clock.advance(SLEEP_RETRY_S)
    await client.poll_cycle()
    assert len(bus.requests) > polled_so_far


async def test_inverter_recovers_when_the_sun_comes_up(fake_clock: Any) -> None:
    bus = a_test_bus(set())
    client = KacoRs485Client(bus, [1])  # type: ignore[arg-type]

    for _ in range(SLEEP_AFTER_MISSES):
        await client.poll_cycle()
        fake_clock.advance(10.0)
    assert not client.states[1].available

    bus.wake(1, XI_UNIT)
    fake_clock.advance(SLEEP_RETRY_S + 1)
    await client.poll_cycle()

    assert client.states[1].consecutive_misses == 0
    assert client.states[1].available
    assert client.states[1].measured is not None


async def test_requests_are_paced(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every request after the first must be preceded by a settle gap.

    Regression guard for the failure that silenced WR2 on-site: transmitting
    while a straggler reply is still on the wire garbles the next request.
    """
    bus = a_test_bus({1, 2, 4})
    client = KacoRs485Client(bus, [1, 2, 4])  # type: ignore[arg-type]

    events: list[tuple[str, float]] = []
    real_request = bus.request

    async def recording_sleep(seconds: float) -> None:
        events.append(("sleep", seconds))

    async def recording_request(address: int, command: str) -> Reply:
        events.append(("request", 0.0))
        return await real_request(address, command)

    monkeypatch.setattr(bus, "request", recording_request)
    monkeypatch.setattr(client_module.asyncio, "sleep", recording_sleep)

    await client.poll_cycle()

    kinds = [kind for kind, _ in events]
    # 3 inverters x 2 cycle commands, plus a one-shot firmware read each on
    # first contact = 9 requests, strictly alternating with gaps.
    assert kinds.count("request") == 9
    assert kinds == ["request"] + ["sleep", "request"] * 8
    assert all(seconds >= client_module.POLL_GAP_S for kind, seconds in events if kind == "sleep")


async def test_parse_errors_do_not_count_as_a_missing_inverter() -> None:
    """A garbled frame means the inverter is there but the bytes were bad."""

    garbled = b"\n*01" + b"\x00" * 60
    bus = FakeBus({1: CannedInverter(name="garbling", replies=dict.fromkeys("038", garbled))})
    client = KacoRs485Client(bus, [1])  # type: ignore[arg-type]

    await client.poll_cycle()

    assert client.states[1].consecutive_misses == 0
    assert client.states[1].available
    assert client.states[1].measured is None


# --- static per-unit data ------------------------------------------------


async def test_firmware_is_read_on_first_contact() -> None:
    bus = a_test_bus({1})
    client = KacoRs485Client(bus, [1])  # type: ignore[arg-type]

    await client.poll_cycle()

    assert client.states[1].firmware == CMD8_TEXT
    assert len(commands(bus, "8")) == 1


async def test_firmware_is_not_re_read_every_cycle() -> None:
    """It is static data on a shared bus — asking again costs a slot forever."""
    bus = a_test_bus({1})
    client = KacoRs485Client(bus, [1])  # type: ignore[arg-type]

    for _ in range(5):
        await client.poll_cycle()

    assert len(commands(bus, "8")) == 1


async def test_silent_inverter_is_not_asked_for_firmware() -> None:
    """A dark inverter must not pay an extra 2.5 s timeout for static data."""
    bus = a_test_bus(set())
    client = KacoRs485Client(bus, [1])  # type: ignore[arg-type]

    await client.poll_cycle()

    assert client.states[1].firmware is None
    assert commands(bus, "8") == []


async def test_firmware_is_read_when_a_dark_inverter_wakes(fake_clock: Any) -> None:
    """Set up at dusk, the type is unknown; it must be filled in at sunrise."""
    bus = a_test_bus(set())
    client = KacoRs485Client(bus, [1])  # type: ignore[arg-type]

    for _ in range(SLEEP_AFTER_MISSES):
        await client.poll_cycle()
        fake_clock.advance(10.0)
    assert client.states[1].firmware is None

    bus.wake(1, XI_UNIT)
    fake_clock.advance(SLEEP_RETRY_S + 1)
    await client.poll_cycle()

    assert client.states[1].firmware == CMD8_TEXT


async def test_unreadable_firmware_does_not_affect_availability() -> None:
    """Static data is a bonus read; failing it must not mark a live unit dark."""

    bus = FakeBus(
        {1: CannedInverter(name="no firmware", replies={"0": CMD0_FRAME, "3": CMD3_FRAME})}
    )
    client = KacoRs485Client(bus, [1])  # type: ignore[arg-type]

    await client.poll_cycle()

    assert client.states[1].firmware is None
    assert client.states[1].consecutive_misses == 0
    assert client.states[1].available
    assert client.states[1].measured is not None


# --- retry policy --------------------------------------------------------


class FlakyBus:
    """Returns `script` entries in order, then good frames forever."""

    def __init__(self, script: list[bytes]) -> None:
        self.script = list(script)
        self.requests: list[tuple[int, str]] = []

    async def request(self, address: int, command: str) -> Reply:
        self.requests.append((address, command))
        raw = self.script.pop(0) if self.script else CMD0_FRAME
        return Reply(request=b"", raw=raw, elapsed_ms=2000.0)


GARBAGE = b"\n*01" + b"\x00" * 60


async def test_corrupt_reply_is_retried() -> None:
    bus = FlakyBus([GARBAGE, GARBAGE, CMD0_FRAME])
    client = KacoRs485Client(bus, [1])  # type: ignore[arg-type]

    await client.poll_cycle()

    cmd0 = [r for r in bus.requests if r[1] == "0"]
    assert len(cmd0) == 3, "should have retried the two garbled frames"
    assert client.states[1].measured is not None


async def test_silence_is_never_retried() -> None:
    """A dead address must cost one timeout, not three.

    Retrying silence would triple the cost of every dark inverter at night,
    which is exactly what the backoff exists to avoid.
    """
    bus = a_test_bus(set())
    client = KacoRs485Client(bus, [1])  # type: ignore[arg-type]

    await client.poll_cycle()

    assert len(bus.requests) == 2, "one request per command, no retries"
    assert client.states[1].consecutive_misses == 1


async def test_retries_are_capped() -> None:
    bus = FlakyBus([GARBAGE] * 10)
    client = KacoRs485Client(bus, [1], max_attempts=3)  # type: ignore[arg-type]

    await client.poll_cycle()

    assert len([r for r in bus.requests if r[1] == "0"]) == 3


async def test_persistent_corruption_keeps_the_inverter_available() -> None:
    """Garbled frames prove the inverter is alive; only silence means absent."""
    bus = FlakyBus([GARBAGE] * 100)
    client = KacoRs485Client(bus, [1])  # type: ignore[arg-type]

    for _ in range(SLEEP_AFTER_MISSES + 1):
        await client.poll_cycle()

    assert client.states[1].consecutive_misses == 0
    assert client.states[1].available
    assert client.states[1].measured is None


# --- connection loss -----------------------------------------------------


class NotAnOSError(Exception):
    """How a dropped ESPHome proxy connection actually fails.

    aioesphomeapi raises APIConnectionError, which is neither OSError nor
    TimeoutError. Catching only those let it escape the transport and kill a
    long-running poll outright.
    """


async def test_connection_loss_surfaces_as_bus_error() -> None:
    from kaco_rs485.transport import AsyncBus, BusError

    class DeadWriter:
        def write(self, data: bytes) -> None:
            raise NotAnOSError("Not connected to proxy!")

        async def drain(self) -> None:
            pass

        def close(self) -> None:
            pass

    class DeadReader:
        async def read(self, n: int) -> bytes:
            return b""

    bus = AsyncBus("esphome://nowhere:6053/?port_name=X")
    bus._reader = DeadReader()  # type: ignore[assignment]
    bus._writer = DeadWriter()  # type: ignore[assignment]

    with pytest.raises(BusError):
        await bus.request(1, "0")


async def test_reply_latency_is_recorded_per_inverter() -> None:
    """Diagnostics need bus health without re-plumbing Reply objects out."""
    bus = a_bus([1], reply_ms=250.0)
    client = KacoRs485Client(bus, [1], poll_gap_s=0)

    assert client.states[1].last_reply_ms is None

    await client.poll_cycle()

    assert client.states[1].last_reply_ms == pytest.approx(250.0)


async def test_a_silent_inverter_records_no_latency() -> None:
    """A timeout is not a measurement."""
    client = KacoRs485Client(a_bus([]), [1], poll_gap_s=0)

    await client.poll_cycle()

    assert client.states[1].last_reply_ms is None
