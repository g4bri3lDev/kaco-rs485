"""Polling policy for a bus full of xi inverters.

The protocol is trivially simple; the *pacing* is what took a season of
on-site debugging to get right, and those lessons are encoded here:

- Never poll back-to-back. Transmitting while a straggler reply is still on
  the wire garbles the request for the next inverter — this was observed
  silencing WR2 entirely. Hence POLL_GAP_S between every request.
- Inverters go dark at night, completely. Measured on a 6400xi/8000xi bus
  (2026-08-30, sunset 19:58 local): output reached 0 W around 19:15, after
  which the units cycled `Waiting` -> `Constant voltage mode` -> `MPP tracking`
  for an hour while still answering every poll, then stopped answering between
  20:13 and 20:19 — one reporting status 2, `Waiting for shutdown`, as its last
  word. From then until morning they return nothing: a passive listen on the
  bus hears zero bytes, and every address times out.

  Note they never report status 15, `Night shutdown`, even though the vendor
  defines it and its own datalogger treats it as a reason to skip polling.
  These units simply leave the bus, so silence is the only signal available —
  and it is indistinguishable from a unit that has been disconnected.

  Polling a dark inverter costs a full REPLY_START_TIMEOUT_S (2.5 s) per
  command, so a fleet that has gone to sleep otherwise spends all night timing
  out. Hence the backoff below.
- A sleeping inverter must still be probed occasionally, or the fleet never
  wakes up in the morning.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field

from .protocol import (
    Firmware,
    MeasuredValues,
    ParseError,
    TotalYield,
    parse_cmd0,
    parse_cmd3,
    parse_cmd8,
)
from .transport import AsyncBus

# Defaults, all overridable per client. These came out of debugging a real
# three-inverter installation; they are reasonable starting points, not
# protocol requirements.

# Bus-settle gap between consecutive requests.
POLL_GAP_S = 1.0

# Consecutive no-reply polls before an inverter is considered asleep.
SLEEP_AFTER_MISSES = 3

# How often a sleeping inverter gets a probe poll.
SLEEP_RETRY_S = 60.0

# Attempts per request when a reply arrives but is unusable, and the pause
# between them. Both match the vendor datalogger's own driver.
#
# The distinction that matters: this retries a *corrupt* reply, never a silent
# one. A silent address has already cost a full start timeout and retrying it
# would triple that for no reason — the inverter is off, not shy. The vendor
# makes exactly this distinction, returning immediately on silence and entering
# the retry loop only after a frame arrives and fails validation.
MAX_ATTEMPTS = 3
RETRY_DELAY_S = 1.0

# Commands per cycle: `0` is fast-changing measured values, `3` is the yield
# and uptime counters.
CYCLE_COMMANDS = ("0", "3")

# Static per-unit data, asked for once per address on first contact rather than
# every cycle. Command `8` is the only one worth asking for, which took asking
# the hardware to establish (xi units, firmware K222.36DE, 2026-08):
#
#   cmd `8` -> "K222.36DE 6817"   the firmware version. Useful.
#   cmd `9` -> "6400xi"           the same type string cmd `0` already carries
#                                 in every reply, so it buys nothing.
#   cmd `s` -> zero bytes         serial number, blueplanet-only. xi units do
#                                 not answer it at all, which is why devices
#                                 built from this library have no serial number
#                                 and must be identified by bus address.
STATIC_COMMAND = "8"


@dataclass
class InverterState:
    """Everything known about one address, carried across cycles."""

    address: int
    measured: MeasuredValues | None = None
    totals: TotalYield | None = None
    firmware: str | None = None
    """Vendor firmware string, e.g. "K222.36DE 6817". Read once, on first
    contact — `None` until the inverter has answered at least one poll."""
    consecutive_misses: int = 0
    last_polled: float = field(default=0.0)
    sleep_after: int = SLEEP_AFTER_MISSES

    @property
    def asleep(self) -> bool:
        """True once the inverter has missed enough polls to be considered dark."""
        return self.consecutive_misses >= self.sleep_after

    @property
    def available(self) -> bool:
        """False means consumers should render this inverter as unavailable.

        Deliberately the same threshold as `asleep`. A dark inverter must not
        keep reporting the last value it managed to send — that is how a
        dashboard ends up showing yesterday's watts at midnight.
        """
        return not self.asleep


class KacoRs485Client:
    """Round-robins a set of addresses over a single shared bus."""

    def __init__(
        self,
        bus: AsyncBus,
        addresses: list[int],
        *,
        poll_gap_s: float = POLL_GAP_S,
        sleep_after_misses: int = SLEEP_AFTER_MISSES,
        sleep_retry_s: float = SLEEP_RETRY_S,
        max_attempts: int = MAX_ATTEMPTS,
        retry_delay_s: float = RETRY_DELAY_S,
    ) -> None:
        self._bus = bus
        self._poll_gap_s = poll_gap_s
        self._sleep_retry_s = sleep_retry_s
        self._max_attempts = max_attempts
        self._retry_delay_s = retry_delay_s
        self.states: dict[int, InverterState] = {
            addr: InverterState(address=addr, sleep_after=sleep_after_misses) for addr in addresses
        }

    async def poll_cycle(self) -> dict[int, InverterState]:
        """Visit every inverter due this cycle, then return the full state map."""
        now = time.monotonic()
        due = [s for s in self.states.values() if self.should_poll(s, now)]

        for i, state in enumerate(due):
            if i:
                await asyncio.sleep(self._poll_gap_s)
            await self._poll_one(state)

        return self.states

    async def _poll_one(self, state: InverterState) -> None:
        state.last_polled = time.monotonic()
        answered = False

        for j, command in enumerate(CYCLE_COMMANDS):
            if j:
                await asyncio.sleep(self._poll_gap_s)

            responded, parsed = await self._request_with_retry(state.address, command)
            answered = answered or responded

            # The command decides which shape comes back; narrow so that is
            # checked rather than assumed.
            if isinstance(parsed, MeasuredValues):
                state.measured = parsed
            elif isinstance(parsed, TotalYield):
                state.totals = parsed

            # A sleeping inverter that answers its probe stops being asleep
            # partway through the cycle, so don't wait for the cycle to end.
            if responded and state.asleep:
                break

        state.consecutive_misses = 0 if answered else state.consecutive_misses + 1

        if answered and state.firmware is None:
            await self._read_static(state)

    async def _read_static(self, state: InverterState) -> None:
        """Fetch the data that never changes, once per address.

        Deliberately does not touch `consecutive_misses`: this is a bonus read,
        and an inverter that answers its measured values but not command `8` is
        alive and must not be counted towards the sleep backoff for it. If it
        fails, `firmware` stays `None` and the next cycle tries again.
        """
        await asyncio.sleep(self._poll_gap_s)
        _responded, parsed = await self._request_with_retry(state.address, STATIC_COMMAND)
        if isinstance(parsed, Firmware):
            state.firmware = parsed.raw_text

    async def _request_with_retry(
        self, address: int, command: str
    ) -> tuple[bool, MeasuredValues | TotalYield | Firmware | None]:
        """Poll one (address, command), retrying only corrupt replies.

        Returns (the inverter answered at all, parsed value or None). Those are
        two different questions: a garbled frame proves the inverter is alive
        and must not count towards the sleep backoff, but it yields no value
        and the previous reading is kept rather than a fabricated one.
        """
        for attempt in range(self._max_attempts):
            if attempt:
                await asyncio.sleep(self._retry_delay_s)

            reply = await self._bus.request(address, command)

            if not reply.responded:
                # Silence is not worth retrying — the inverter is off, and we
                # have already paid a full start timeout finding that out.
                return False, None

            try:
                if command == "0":
                    return True, parse_cmd0(reply.raw)
                if command == "3":
                    return True, parse_cmd3(reply.raw)
                if command == STATIC_COMMAND:
                    return True, parse_cmd8(reply.raw)
            except ParseError:
                continue  # corrupt frame: worth another attempt

            return True, None

        # Every attempt produced a frame, none of them parseable. The inverter
        # is present but something on the bus is mangling its replies.
        return True, None

    def should_poll(self, state: InverterState, now: float) -> bool:
        """Decide whether `state` gets a request this cycle.

        Awake inverters are polled every cycle. Sleeping ones are polled at
        most once per SLEEP_RETRY_S, which is the whole point of the backoff:
        three dark inverters at 2.5 s of timeout per command would otherwise
        consume 15 s of every cycle all night.

        Worst-case morning wake-up latency is therefore `sleep_retry_s` — one
        minute of a sunrise, which is not worth optimising. Note this is a
        per-inverter timer, not a global one, so the sleeping units stay spread
        across cycles instead of all probing in the same one.
        """
        if state.last_polled == 0.0:
            return True  # never polled — always establish a baseline
        if not state.asleep:
            return True
        return (now - state.last_polled) >= self._sleep_retry_s
