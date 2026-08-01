"""Half-duplex request/reply over any serialx-supported transport.

The bus is addressed by an opaque URL string, never by a host/port/key triple.
serialx resolves the scheme, so one code path covers every deployment:

    /dev/tty.usbserial-0001                              local RS485 adapter
    esphome://host:6053/?port_name=RS-485                ESPHome proxy, standalone
    esphome-hass://esphome/<entry_id>?port_name=RS-485   ESPHome proxy, inside HA
    socket://host:port                                   any ser2net-style bridge

The `esphome-hass` scheme is registered by Home Assistant itself and resolves
the ESPHome integration's already-authenticated client, which is why the HA
integration built on this library stores no host and no encryption key.

Direction control is never our problem: every adapter we target (the CP2102
dongle, the MAX485 module, the ATOMIC RS485 Base's SP3485EE) switches DE/RE in
hardware. From here this is an ordinary half-duplex serial port.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from types import TracebackType
from typing import Self

import serialx

from . import framing
from .protocol import build_request


@dataclass
class Reply:
    """Raw reply bytes plus what was sent and how long it took."""

    request: bytes
    raw: bytes
    elapsed_ms: float

    arrivals: list[tuple[float, int]] = field(default_factory=list)
    """(ms since the request was written, bytes in that chunk), per read.

    Recorded so the timeout constants can be checked against reality instead
    of trusted. `first_byte_ms` and `max_gap_ms` below are the two numbers the
    constants are actually derived from — but note that reading through a
    network proxy adds its own batching, so these measure the whole path, not
    the wire alone.
    """

    @property
    def responded(self) -> bool:
        return bool(self.raw)

    @property
    def first_byte_ms(self) -> float | None:
        """When the reply started. Compare against `start_timeout_s`."""
        return self.arrivals[0][0] if self.arrivals else None

    @property
    def max_gap_ms(self) -> float | None:
        """Longest silence *inside* the reply. Compare against `gap_s`.

        On xi units this is expected to exceed 250 ms, from the documented
        pause between the checksum byte and the trailing type string.
        """
        if len(self.arrivals) < 2:
            return None
        return max(b[0] - a[0] for a, b in zip(self.arrivals, self.arrivals[1:], strict=False))


class BusError(Exception):
    """The bus could not be opened or used."""


class AsyncBus:
    """One request at a time onto a shared RS485 bus.

    Not internally serialised: RS485 is a shared medium, and interleaving two
    requests garbles both. `KacoRs485Client` owns the scheduling; if you drive
    this directly, do so from a single task.
    """

    def __init__(
        self,
        url: str,
        *,
        baudrate: int = 9600,
        key: str | None = None,
        start_timeout_s: float = framing.REPLY_START_TIMEOUT_S,
        gap_s: float = framing.REPLY_GAP_S,
    ) -> None:
        """`start_timeout_s` and `gap_s` default to values measured against
        Powador xi units. Raise them for slower hardware, longer cable runs,
        or a congested network path to a proxy.
        """
        self._url = url
        self._baudrate = baudrate
        self._key = key
        self._start_timeout_s = start_timeout_s
        self._gap_s = gap_s
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None

    async def open(self) -> None:
        kwargs: dict[str, object] = {"url": self._url, "baudrate": self._baudrate}
        if self._key is not None:
            kwargs["key"] = self._key
        try:
            self._reader, self._writer = await serialx.open_serial_connection(**kwargs)  # type: ignore[arg-type]
        # serialx surfaces failures as plain OSError/TimeoutError rather than a
        # pyserial SerialException — see the HA pyserial->serialx migration note.
        except (OSError, TimeoutError) as err:
            raise BusError(f"could not open {self._url}: {err}") from err

    async def close(self) -> None:
        if self._writer is not None:
            self._writer.close()
            try:
                await self._writer.wait_closed()
            except (OSError, TimeoutError):
                pass
        self._reader = None
        self._writer = None

    async def __aenter__(self) -> Self:
        await self.open()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.close()

    async def request(self, address: int, command: str) -> Reply:
        """Send `#<addr><cmd><CR>` and collect the reply.

        Returns a Reply with empty `raw` on timeout rather than raising —
        a silent inverter is normal operation (they go dark at night), not an
        error condition.
        """
        if self._reader is None or self._writer is None:
            raise BusError("bus is not open")

        frame = build_request(address, command)

        await self._discard_stale()

        try:
            self._writer.write(frame)
            await self._writer.drain()
        except (OSError, TimeoutError) as err:
            raise BusError(f"write failed: {err}") from err

        t0 = time.monotonic()
        raw, arrivals = await self._read_reply(command, t0)
        return Reply(
            request=frame,
            raw=raw,
            elapsed_ms=(time.monotonic() - t0) * 1000,
            arrivals=arrivals,
        )

    async def read_raw(self, timeout: float) -> bytes:
        """Read whatever arrives within `timeout`, transmitting nothing.

        For passive bus diagnostics: safe to call while another master owns
        the bus, which `request()` is not.
        """
        if self._reader is None:
            raise BusError("bus is not open")
        try:
            return await asyncio.wait_for(self._reader.read(256), timeout)
        except TimeoutError:
            return b""
        except OSError as err:
            raise BusError(f"read failed: {err}") from err

    async def _read_reply(self, command: str, t0: float) -> tuple[bytes, list[tuple[float, int]]]:
        assert self._reader is not None
        buf = b""
        arrivals: list[tuple[float, int]] = []
        while True:
            # First byte gets the long window (replies start 1-2 s late);
            # subsequent bytes only need to clear the mid-frame pause.
            timeout = self._start_timeout_s if not buf else self._gap_s
            try:
                chunk = await asyncio.wait_for(self._reader.read(256), timeout)
            except TimeoutError:
                break
            except OSError as err:
                raise BusError(f"read failed: {err}") from err

            if not chunk:  # port closed underneath us
                break

            arrivals.append(((time.monotonic() - t0) * 1000, len(chunk)))
            buf = framing.trim_leading_junk(buf + chunk)
            if buf and framing.is_complete(buf, command):
                break

        return buf, arrivals

    async def _discard_stale(self) -> None:
        """Drop anything already on the wire before transmitting.

        A straggler reply from the previous poll, or turnaround glitch bytes,
        would otherwise be parsed as the head of the next reply.
        """
        assert self._reader is not None
        while True:
            try:
                chunk = await asyncio.wait_for(self._reader.read(256), 0.01)
            except TimeoutError:
                return
            except OSError:
                return
            if not chunk:
                return
