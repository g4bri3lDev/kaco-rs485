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

from collections.abc import Callable
from dataclasses import dataclass, field

from .protocol import ParseError, Protocol, parse_cmd0
from .transport import AsyncBus

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


async def scan(
    bus: AsyncBus,
    addresses: range | list[int] = ALL_ADDRESSES,
    *,
    on_progress: Callable[[int, int], None] | None = None,
) -> ScanResult:
    """Probe each address once with command `0`.

    `on_progress(done, total)` is called after every address so a UI can show
    something during what is, worst case, a couple of minutes of timeouts.
    """
    targets = list(addresses)
    result = ScanResult()

    for index, address in enumerate(targets, start=1):
        reply = await bus.request(address, "0")

        if reply.responded:
            result.saw_any_bytes = True
            result.found.append(_identify(address, reply.raw))

        if on_progress is not None:
            on_progress(index, len(targets))

    return result


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
