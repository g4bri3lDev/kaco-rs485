"""Polling policy for a bus full of xi inverters.

The protocol is trivially simple; the *pacing* is what took a season of
on-site debugging to get right, and those lessons are encoded here:

- Never poll back-to-back. Transmitting while a straggler reply is still on
  the wire garbles the request for the next inverter — this was observed
  silencing WR2 entirely. Hence POLL_GAP_S between every request.
- Inverters go dark at night. Polling a dark inverter costs a full
  REPLY_START_TIMEOUT_S (2.5 s) per command, so a fleet that has gone to sleep
  otherwise spends all night timing out. Hence the backoff below.
- A sleeping inverter must still be probed occasionally, or the fleet never
  wakes up in the morning.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field

from .protocol import MeasuredValues, ParseError, TotalYield, parse_cmd0, parse_cmd3
from .transport import AsyncBus

# Bus-settle gap between consecutive requests.
POLL_GAP_S = 1.0

# Consecutive no-reply polls before an inverter is considered asleep.
SLEEP_AFTER_MISSES = 3

# How often a sleeping inverter gets a probe poll.
SLEEP_RETRY_S = 60.0

# Commands per cycle: `0` is fast-changing measured values, `3` is the yield
# and uptime counters. Static data (`8`, `9`) is read once at startup.
CYCLE_COMMANDS = ("0", "3")


@dataclass
class InverterState:
    """Everything known about one address, carried across cycles."""

    address: int
    measured: MeasuredValues | None = None
    totals: TotalYield | None = None
    consecutive_misses: int = 0
    last_polled: float = field(default=0.0)

    @property
    def asleep(self) -> bool:
        """True once the inverter has missed enough polls to be considered dark."""
        return self.consecutive_misses >= SLEEP_AFTER_MISSES

    @property
    def available(self) -> bool:
        """False means consumers should render this inverter as unavailable.

        Deliberately the same threshold as `asleep`: the ESPHome component
        published NAN at exactly this transition, and matching it keeps HA
        history continuous across the migration.
        """
        return not self.asleep


class KacoRs485Client:
    """Round-robins a set of addresses over a single shared bus."""

    def __init__(self, bus: AsyncBus, addresses: list[int]) -> None:
        self._bus = bus
        self.states: dict[int, InverterState] = {
            addr: InverterState(address=addr) for addr in addresses
        }

    async def poll_cycle(self) -> dict[int, InverterState]:
        """Visit every inverter due this cycle, then return the full state map."""
        now = time.monotonic()
        due = [s for s in self.states.values() if self.should_poll(s, now)]

        for i, state in enumerate(due):
            if i:
                await asyncio.sleep(POLL_GAP_S)
            await self._poll_one(state)

        return self.states

    async def _poll_one(self, state: InverterState) -> None:
        state.last_polled = time.monotonic()
        answered = False

        for j, command in enumerate(CYCLE_COMMANDS):
            if j:
                await asyncio.sleep(POLL_GAP_S)
            reply = await self._bus.request(state.address, command)
            if not reply.responded:
                continue
            answered = True
            try:
                if command == "0":
                    state.measured = parse_cmd0(reply.raw)
                elif command == "3":
                    state.totals = parse_cmd3(reply.raw)
            except ParseError:
                # A malformed frame is not a missing inverter — it answered.
                # Keep the previous value rather than inventing one.
                continue

            # A sleeping inverter that answers its probe stops being asleep
            # partway through the cycle, so don't wait for the cycle to end.
            if state.asleep:
                break

        state.consecutive_misses = 0 if answered else state.consecutive_misses + 1

    def should_poll(self, state: InverterState, now: float) -> bool:
        """Decide whether `state` gets a request this cycle.

        Awake inverters are polled every cycle. Sleeping ones are polled at
        most once per SLEEP_RETRY_S, which is the whole point of the backoff:
        three dark inverters at 2.5 s of timeout per command would otherwise
        consume 15 s of every cycle all night.

        Worst-case morning wake-up latency is therefore SLEEP_RETRY_S — one
        minute of a sunrise, which is not worth optimising. Note this is a
        per-inverter timer, not a global one, so the sleeping units stay spread
        across cycles instead of all probing in the same one.
        """
        if state.last_polled == 0.0:
            return True  # never polled — always establish a baseline
        if not state.asleep:
            return True
        return (now - state.last_polled) >= SLEEP_RETRY_S
