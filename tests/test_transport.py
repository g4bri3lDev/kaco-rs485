"""Tests for `AsyncBus` against a scripted stream pair.

`FakeBus` stands in for this class everywhere else, so the real reader and
writer handling is only exercised here.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from kaco_rs485 import AsyncBus, BusError
from kaco_rs485.testing import POWADOR_6400XI


class StubReader:
    """Serves queued chunks, then behaves as the caller asked at the end.

    `b""` is what `asyncio.StreamReader.read` returns at EOF, and only then.
    """

    def __init__(self, chunks: list[bytes], *, then: str = "eof") -> None:
        self._chunks = list(chunks)
        self._then = then
        self._started = False

    async def read(self, _n: int) -> bytes:
        if not self._started:
            # Outlast the pre-transmit drain, so it does not eat the reply.
            self._started = True
            await asyncio.sleep(0.02)
        if self._chunks:
            return self._chunks.pop(0)
        if self._then == "hang":
            await asyncio.sleep(3600)
        return b""


class StubWriter:
    """Collects what was written; closing is a no-op."""

    def __init__(self) -> None:
        self.written = b""

    def write(self, data: bytes) -> None:
        self.written += data

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        return None

    async def wait_closed(self) -> None:
        return None


async def a_bus(reader: StubReader) -> AsyncBus:
    """An open bus reading from `reader`, with short timeouts."""
    bus = AsyncBus("/dev/null", start_timeout_s=0.05, gap_s=0.01)
    with patch(
        "kaco_rs485.transport.serialx.open_serial_connection",
        new=AsyncMock(return_value=(reader, StubWriter())),
    ):
        await bus.open()
    return bus


async def test_a_complete_reply_is_returned() -> None:
    """Test the happy path: a whole frame in one chunk."""
    frame = POWADOR_6400XI.reply_to("0")
    bus = await a_bus(StubReader([frame], then="hang"))

    reply = await bus.request(2, "0")

    assert reply.raw == frame
    assert reply.responded


async def test_a_silent_address_returns_an_empty_reply() -> None:
    """Test a vacant address times out rather than raising."""
    bus = await a_bus(StubReader([], then="hang"))

    assert not (await bus.request(9, "0")).responded


async def test_a_port_closing_mid_wait_is_an_error_not_a_silence() -> None:
    """Test EOF raises: a dead proxy must not read as an inverter asleep."""
    bus = await a_bus(StubReader([]))

    with pytest.raises(BusError, match="closed"):
        await bus.request(2, "0")


async def test_a_port_closing_mid_frame_is_an_error() -> None:
    """Test EOF part-way through a reply raises rather than truncating."""
    bus = await a_bus(StubReader([POWADOR_6400XI.reply_to("0")[:20]]))

    with pytest.raises(BusError, match="closed"):
        await bus.request(2, "0")


async def test_hearing_nothing_while_listening_is_not_an_error() -> None:
    """Test `read_raw` reports an idle window as no bytes."""
    bus = await a_bus(StubReader([], then="hang"))

    assert await bus.read_raw(0.01) == b""


async def test_listening_on_a_closed_port_is_an_error() -> None:
    """Test `read_raw` separates EOF from hearing nothing."""
    bus = await a_bus(StubReader([]))

    with pytest.raises(BusError, match="closed"):
        await bus.read_raw(0.1)


async def test_a_port_that_will_not_open_raises() -> None:
    """Test the transport's own failures arrive as `BusError`."""
    bus = AsyncBus("/dev/null")

    with (
        patch(
            "kaco_rs485.transport.serialx.open_serial_connection",
            new=AsyncMock(side_effect=OSError("no such device")),
        ),
        pytest.raises(BusError, match="could not open"),
    ):
        await bus.open()


async def test_a_closed_bus_refuses_to_be_used() -> None:
    """Test using a bus that was never opened is reported, not asserted."""
    bus = AsyncBus("/dev/null")

    with pytest.raises(BusError, match="not open"):
        await bus.request(1, "0")
    with pytest.raises(BusError, match="not open"):
        await bus.read_raw(0.01)
