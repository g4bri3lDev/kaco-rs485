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
from kaco_rs485.transport import Reply

from .conftest import CMD0_CAPTURES, CMD3_CAPTURES

CMD0_FRAME = CMD0_CAPTURES[0].raw
CMD3_FRAME = CMD3_CAPTURES[0].raw


class FakeBus:
    """Answers for the addresses in `alive`, stays silent for the rest."""

    def __init__(self, alive: set[int]) -> None:
        self.alive = alive
        self.requests: list[tuple[int, str]] = []

    async def request(self, address: int, command: str) -> Reply:
        self.requests.append((address, command))
        if address not in self.alive:
            return Reply(request=b"", raw=b"", elapsed_ms=2500.0)
        raw = CMD0_FRAME if command == "0" else CMD3_FRAME
        return Reply(request=b"", raw=raw, elapsed_ms=2000.0)

    def addresses_polled(self) -> set[int]:
        return {addr for addr, _ in self.requests}


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
    bus = FakeBus(alive={1, 2, 4})
    client = KacoRs485Client(bus, [1, 2, 4])  # type: ignore[arg-type]

    await client.poll_cycle()

    assert bus.addresses_polled() == {1, 2, 4}
    for state in client.states.values():
        assert state.available
        assert state.consecutive_misses == 0
        assert state.measured is not None
        assert state.totals is not None


async def test_silent_inverter_becomes_unavailable_after_three_misses() -> None:
    bus = FakeBus(alive=set())
    client = KacoRs485Client(bus, [1])  # type: ignore[arg-type]

    for cycle in range(SLEEP_AFTER_MISSES):
        await client.poll_cycle()
        assert client.states[1].consecutive_misses == cycle + 1

    assert client.states[1].asleep
    assert not client.states[1].available


async def test_sleeping_inverter_is_skipped_until_the_retry_window(fake_clock: Any) -> None:
    bus = FakeBus(alive=set())
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
    bus = FakeBus(alive=set())
    client = KacoRs485Client(bus, [1])  # type: ignore[arg-type]

    for _ in range(SLEEP_AFTER_MISSES):
        await client.poll_cycle()
        fake_clock.advance(10.0)
    assert not client.states[1].available

    bus.alive.add(1)
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
    bus = FakeBus(alive={1, 2, 4})
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
    # 3 inverters x 2 commands = 6 requests, strictly alternating with gaps.
    assert kinds.count("request") == 6
    assert kinds == ["request"] + ["sleep", "request"] * 5
    assert all(seconds >= client_module.POLL_GAP_S for kind, seconds in events if kind == "sleep")


async def test_parse_errors_do_not_count_as_a_missing_inverter() -> None:
    """A garbled frame means the inverter is there but the bytes were bad."""

    class GarbageBus(FakeBus):
        async def request(self, address: int, command: str) -> Reply:
            return Reply(request=b"", raw=b"\n*01" + b"\x00" * 60, elapsed_ms=2000.0)

    bus = GarbageBus(alive={1})
    client = KacoRs485Client(bus, [1])  # type: ignore[arg-type]

    await client.poll_cycle()

    assert client.states[1].consecutive_misses == 0
    assert client.states[1].available
    assert client.states[1].measured is None


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
    bus = FakeBus(alive=set())
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
