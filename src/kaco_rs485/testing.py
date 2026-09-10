"""Test doubles built from frames captured on a live bus.

This module ships with the package rather than living in `tests/`, because the
consumers that most need it are downstream: a Home Assistant integration testing
against `FakeBus` exercises the real framing and parsing code, where one built on
`unittest.mock` would only ever assert that its own mock was called.

The frames below are **verbatim captures**, not synthesised — bytes that three
real inverters put on a real RS485 bus. They are the same corpus
`tests/reference/captures.json` is drawn from. Two consequences worth knowing:

- A cmd `0` frame carries the address it was captured at (`*020` is address 2).
  Placing `POWADOR_6400XI` at address 7 therefore yields a frame whose embedded
  address still reads 2. Nothing in this library reads that field — state is
  keyed by the address that was *asked* — but a test asserting on
  `MeasuredValues.address` should place units at their captured addresses.
- The checksums are the real ones. Do not edit these byte strings; a hand-tweaked
  frame will fail `verify_checksum` and the failure will look like a parser bug.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Self

from .framing import REPLY_START_TIMEOUT_S
from .protocol import build_request
from .transport import Reply

# Typical time-to-first-byte, in milliseconds. The measured range through an
# ESPHome proxy is 44-413 ms; this sits in the middle of it so that a default
# `FakeBus` looks like an ordinary healthy bus to anything measuring latency.
DEFAULT_REPLY_MS = 120.0


@dataclass(frozen=True)
class CannedInverter:
    """One unit's canned answers, keyed by command byte.

    A command absent from `replies` is answered with silence, which is how these
    units behave for commands they do not implement — xi hardware returns zero
    bytes for the serial-number command, for instance.
    """

    name: str
    replies: Mapping[str, bytes]

    def reply_to(self, command: str) -> bytes:
        """Raw frame for `command`, or empty bytes if this unit ignores it."""
        return self.replies.get(command, b"")


# Powador 6400xi, firmware K222.36DE 6817, captured at address 2.
# cmd 0: status 4 (MPP tracking), 635 W AC / 671 W DC, 33801 Wh today.
POWADOR_6400XI = CannedInverter(
    name="Powador 6400xi",
    replies={
        "0": bytes.fromhex(
            "0a2a30323020202034203430362e382020312e3635202020363731203232382e37"
            "2020322e3830202020363335202033342020333338303120a5203634303078690d"
        ),
        "3": bytes.fromhex(
            "0a202034383939202033333830312031313237303620313132373036202020202031"
            "323a3132202037313835383a3030202037313835383a30300d"
        ),
        "8": bytes.fromhex("0a2a303238204b3232322e3336444520363831370d"),
    },
)

# Powador 8000xi, firmware K222.36DE 1C5F, captured at address 4.
# cmd 0: status 4 (MPP tracking), 681 W AC / 739 W DC, 38749 Wh today.
POWADOR_8000XI = CannedInverter(
    name="Powador 8000xi",
    replies={
        "0": bytes.fromhex(
            "0a2a30343020202034203532302e372020312e3432202020373339203232392e31"
            "2020322e3939202020363831202033352020333837343920ba203830303078690d"
        ),
        "3": bytes.fromhex(
            "0a202035373032202033383734392031333337383820313333373838202020202031"
            "323a3139202037333235313a3039202037333235313a30390d"
        ),
        "8": bytes.fromhex("0a2a303438204b3232322e3336444520314335460d"),
    },
)

# A blueplanet, captured at address 3. It answers on the same wire with CRC16
# Generic Protocol frames, which this library cannot read. Present here because
# "a device answered but we cannot use it" is a real bus condition that
# discovery has to report rather than silently skip.
BLUEPLANET = CannedInverter(
    name="blueplanet (CRC16 Generic Protocol)",
    replies={
        "0": bytes.fromhex(
            "0a2a30336e203230203038364c3332203420203532312e352020302e37322020"
            "2033373620203531382e382020302e373220202033373620203232362e352020"
            "312e333520203232372e352020312e333020203232392e332020312e32362020"
            "2037353320202037353920312e303030202034332e3520203435373034204641"
            "36420d"
        ),
    },
)


class FakeBus:
    """A stand-in for `AsyncBus`, serving canned frames. Silence is the default.

    Any address without an inverter answers with an empty `Reply` after the full
    start timeout, exactly as a vacant address does on real hardware. That makes
    an empty `FakeBus` a faithful stand-in for a bus at night, when every unit
    has gone dark.

    Does not sleep: pacing belongs to the caller, so pass `poll_gap_s=0` to
    `scan` or `KacoRs485Client` to keep tests fast.
    """

    inverters: dict[int, CannedInverter]

    reply_ms: float
    """Simulated time-to-first-byte for an address that answers. Raise it above
    a consumer's fast-path timeout to exercise slow-link handling."""

    requests: list[tuple[int, str]]
    """Every (address, command) asked, in order — for asserting on pacing and
    on which commands a consumer actually issues."""

    opened: bool
    """Whether the bus is currently open. Consumers are expected to release the
    port on failure, and this is how a test checks that they did."""

    open_error: Exception | None
    """Raised by `open()` when set — a port that cannot be opened at all."""

    request_error: Exception | None
    """Raised by `request()` when set — a connection lost mid-poll."""

    def __init__(
        self,
        inverters: Mapping[int, CannedInverter] | None = None,
        *,
        reply_ms: float = DEFAULT_REPLY_MS,
    ) -> None:
        self.inverters = dict(inverters or {})
        self.reply_ms = reply_ms
        self.requests = []
        self.opened = False
        self.open_error = None
        self.request_error = None

    async def open(self) -> None:
        """Open the bus, or raise whatever `open_error` holds."""
        if self.open_error is not None:
            raise self.open_error
        self.opened = True

    async def close(self) -> None:
        """Release the bus."""
        self.opened = False

    async def __aenter__(self) -> Self:
        await self.open()
        return self

    async def __aexit__(self, *exc_info: object) -> bool:
        await self.close()
        return False

    async def request(self, address: int, command: str) -> Reply:
        """Answer one request, or raise whatever `request_error` holds."""
        if self.request_error is not None:
            raise self.request_error
        self.requests.append((address, command))
        raw = self.inverters[address].reply_to(command) if address in self.inverters else b""

        if not raw:
            # A silent address costs a full start timeout and records no
            # arrivals, which is what distinguishes it from a fast reply.
            return Reply(
                request=build_request(address, command),
                raw=b"",
                elapsed_ms=REPLY_START_TIMEOUT_S * 1000,
                arrivals=[],
            )

        return Reply(
            request=build_request(address, command),
            raw=raw,
            elapsed_ms=self.reply_ms,
            arrivals=[(self.reply_ms, len(raw))],
        )

    def silence(self, *addresses: int) -> None:
        """Make these addresses stop answering, as every unit does at dusk."""
        for address in addresses:
            self.inverters.pop(address, None)

    def wake(self, address: int, inverter: CannedInverter) -> None:
        """Put a unit (back) on the bus."""
        self.inverters[address] = inverter

    def reset_requests(self) -> None:
        self.requests.clear()

    def commands_for(self, address: int) -> list[str]:
        """Commands asked of one address, in order."""
        return [c for a, c in self.requests if a == address]


def a_bus(
    addresses: Iterable[int] = (1, 2, 4),
    *,
    reply_ms: float = DEFAULT_REPLY_MS,
) -> FakeBus:
    """The common case: 6400xi units, with an 8000xi at address 4.

    Mirrors the installation these captures came from, so a test using it is
    describing a bus that demonstrably exists.
    """
    layout = {address: POWADOR_8000XI if address == 4 else POWADOR_6400XI for address in addresses}
    return FakeBus(layout, reply_ms=reply_ms)
