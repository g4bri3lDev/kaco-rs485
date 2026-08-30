"""Find out what is actually on the bus.

Addresses are set on each inverter's own front panel and nothing announces
itself, so the only way to know is to ask every address in turn. That is slow
— a silent address costs a full REPLY_START_TIMEOUT_S — but it happens once,
during setup, and it is far better than asking a human to remember.

Two things this deliberately reports rather than hides:

- **Nothing found.** At night every xi unit stops answering, so an empty scan
  is ambiguous: it means "no inverters" or "no sun". The caller must be able
  to tell the difference, so `ScanResult` distinguishes a silent bus from a
  noisy one.
- **Wrong protocol family.** blueplanet and TL/TR units answer the same
  request with CRC16 Generic Protocol frames. They are real devices on a real
  bus, but this library cannot read them, and silently skipping them turns
  into a long confusing afternoon.
"""

from __future__ import annotations

import asyncio
import contextlib
import typing
from collections.abc import Callable
from dataclasses import dataclass, field, replace

from .client import POLL_GAP_S, STATIC_COMMAND
from .protocol import ParseError, Protocol, parse_cmd0, parse_cmd8
from .transport import Reply

# The KACO standard protocol allows addresses 1-32.
ALL_ADDRESSES = range(1, 33)


@dataclass(frozen=True)
class Discovered:
    """One device that answered."""

    address: int
    inverter_type: str
    """e.g. "6400xi". Empty when the reply came too early to include it."""

    supported: bool
    """False for CRC16 Generic Protocol devices — blueplanet and TL/TR units."""

    firmware: str = ""
    """e.g. "K222.36DE 6817". Empty when the unit did not answer command `8`.

    Read here, during discovery, rather than left to the caller's first poll.
    Static per-unit data is only obtainable while the inverter is awake, and
    these units leave the bus entirely at dusk — so anything that wants to
    record what a device *is* has to capture it at setup or not at all.
    """


@dataclass
class ScanResult:
    found: list[Discovered] = field(default_factory=list)
    saw_any_bytes: bool = False
    """True if *something* replied, even unparseably.

    Distinguishes "no inverters on this bus" from "the wiring is wrong": a
    silent bus is a wiring or polarity problem, a noisy one is not.
    """

    @property
    def supported(self) -> list[Discovered]:
        return [d for d in self.found if d.supported]

    @property
    def unsupported(self) -> list[Discovered]:
        return [d for d in self.found if not d.supported]


class Requestable(typing.Protocol):
    """The one thing a scan needs off a bus.

    Narrower than `AsyncBus` on purpose: a scan only ever asks a question and
    reads the answer, so anything that can do that — including a test double —
    is enough.
    """

    async def request(self, address: int, command: str) -> Reply: ...


async def scan(
    bus: Requestable,
    addresses: range | list[int] = ALL_ADDRESSES,
    *,
    on_progress: Callable[[int, int], None] | None = None,
    poll_gap_s: float = POLL_GAP_S,
) -> ScanResult:
    """Probe each address once with command `0`.

    `on_progress(done, total)` is called after every address so a UI can show
    something during what is, worst case, a couple of minutes of timeouts.

    Paced by `poll_gap_s` for the same reason polling is: transmitting while a
    straggler reply is still on the wire garbles the next request. A scan is
    especially exposed to this because it walks addresses back to back.

    The gap is only paid **after an address that actually replied**, which is
    what makes a full 32-address scan tolerable. The hazard the gap defends
    against is a straggler still arriving from the previous inverter; if
    nothing answered, there is no straggler and nothing to wait for. A silent
    address costs only its reply timeout, so a mostly-empty bus scans in a
    fraction of the time an unconditional gap would need.

    Each supported unit is asked for its firmware afterwards, so an address
    that answers costs two requests and two gaps rather than one. That is worth
    it here and nowhere else: discovery is the only moment the caller is
    guaranteed to be talking to an awake inverter.
    """
    targets = list(addresses)
    result = ScanResult()
    previous_replied = False

    for index, address in enumerate(targets, start=1):
        if previous_replied:
            await asyncio.sleep(poll_gap_s)

        reply = await bus.request(address, "0")
        previous_replied = reply.responded

        if reply.responded:
            result.saw_any_bytes = True
            discovered = _identify(address, reply.raw)

            if discovered.supported:
                await asyncio.sleep(poll_gap_s)
                discovered = await _read_firmware(bus, discovered)

            result.found.append(discovered)

        if on_progress is not None:
            on_progress(index, len(targets))

    return result


async def _read_firmware(bus: Requestable, discovered: Discovered) -> Discovered:
    """Ask a unit that has just answered for its firmware version.

    Only worth one attempt: a device that answered command `0` a moment ago is
    demonstrably present, so silence here means it does not implement the
    command rather than that it is absent. Failure leaves `firmware` empty and
    is never fatal — the scan's job is to find inverters, and a missing version
    string is cosmetic beside that.
    """
    reply = await bus.request(discovered.address, STATIC_COMMAND)
    if not reply.responded:
        return discovered

    with contextlib.suppress(ParseError):
        return replace(discovered, firmware=parse_cmd8(reply.raw).raw_text)
    return discovered


def _identify(address: int, raw: bytes) -> Discovered:
    if Protocol.from_reply(raw) is Protocol.GENERIC_CRC16:
        return Discovered(address=address, inverter_type="", supported=False)

    try:
        measured = parse_cmd0(raw)
    except ParseError:
        # It answered on the right protocol but the frame was mangled — most
        # likely a collision with another master. Still worth reporting: the
        # address is occupied.
        return Discovered(address=address, inverter_type="", supported=True)

    return Discovered(
        address=address,
        inverter_type=measured.inverter_type,
        supported=True,
    )
