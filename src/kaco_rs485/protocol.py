"""KACO Standard Protocol — series "00" frames.

Spec: APL_RS485protocol_KACO PDF, sections 2.1, 2.2.1, 2.3, 3.7.
See ../../kaco_powador_handoff.md for the consolidated field tables.

This module:
- builds request frames (trivial)
- decodes the reply protocol byte to detect series mismatches
- verifies the 1-byte sum checksum used by series "00"
- parses command-`0` (measured values) replies into a dataclass

Only command `0` parsing is fully implemented in this file. The sniffer logs
raw bytes for everything else; once we have real on-site data we'll know
exactly which other commands need parsers and can add them.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

# --- Series detection ----------------------------------------------------

class Protocol(Enum):
    """Identified by the command byte echoed in the reply (Section 1.4, Table 3)."""

    KACO_STANDARD_00 = "0"   # series "00"/"02"/"XP-old" — the xi units we have
    KACO_STANDARD_000XI = "4"  # series "000xi" — large park inverters
    GENERIC_CRC16 = "n"      # TL1/TL3/TR3/XP — different frame, CRC16

    @classmethod
    def from_reply(cls, raw: bytes) -> Protocol | None:
        """Pick out the command byte from a reply and map it to a protocol.

        Reply layout starts: LF * AA C ... where C is the command byte.
        Returns None if the frame is too short or doesn't begin with LF*.
        """
        if len(raw) < 5 or raw[0:1] != b"\n" or raw[1:2] != b"*":
            return None
        cmd = raw[4:5].decode("ascii", errors="replace")
        try:
            return cls(cmd)
        except ValueError:
            return None


# --- Request frames ------------------------------------------------------

def build_request(address: int, command: str) -> bytes:
    """`#<addr><cmd><CR>` — used by every command in the standard protocol."""
    if not 1 <= address <= 32:
        raise ValueError(f"address must be 1..32, got {address}")
    if len(command) != 1 or not command.isascii():
        raise ValueError(f"command must be one ASCII char, got {command!r}")
    return f"#{address:02d}{command}\r".encode("ascii")


# --- Checksum (series "00") ---------------------------------------------

def kaco_sum_checksum(payload: bytes) -> int:
    """Series "00" checksum: 1-byte sum of ASCII values mod 256.

    Per Section 2.2: "the ASCII values of the characters of a row are added,
    up to and including the blank after yield". `payload` is exactly that
    range — from `*` through the trailing space after E.
    """
    return sum(payload) & 0xFF


def verify_checksum(raw: bytes) -> tuple[bool, int, int]:
    """Locate the checksum byte in a series-"00" command-`0` reply and verify.

    The reply layout up to and including the checksum byte is fixed-width.
    Counting from the LF:

        offset 0       : LF                               (1)
        offset 1       : `*`                              (1)
        offset 2..4    : address(2) + command(1)          (3)
        offset 5       : space                            (1)
        offset 6..8    : status                           (3)
        offset 9       : space
        offset 10..14  : V                                (5)
        offset 15      : space
        offset 16..20  : I                                (5)
        offset 21      : space
        offset 22..26  : P                                (5)
        offset 27      : space
        offset 28..32  : UN                               (5)
        offset 33      : space
        offset 34..38  : IN                               (5)
        offset 39      : space
        offset 40..44  : PN                               (5)
        offset 45      : space
        offset 46..48  : T                                (3)
        offset 49      : space
        offset 50..55  : E                                (6)
        offset 56      : space  <-- last byte fed into the sum
        offset 57      : checksum byte (any value 0..255)

    Returns (ok, expected, actual). If the frame is too short, returns
    (False, 0, 0).
    """
    CHECKSUM_OFFSET = 57
    if len(raw) <= CHECKSUM_OFFSET:
        return False, 0, 0
    # Sum range starts at the `*` (offset 1) and ends at the space before
    # the checksum (offset 56), inclusive.
    expected = kaco_sum_checksum(raw[1:CHECKSUM_OFFSET])
    actual = raw[CHECKSUM_OFFSET]
    return expected == actual, expected, actual


# --- Command `0` reply parsing ------------------------------------------

@dataclass
class MeasuredValues:
    """Decoded `#<adr>0<CR>` reply for series "00" inverters."""
    address: int
    status: int           # raw status code; meaning is in the inverter manual
    dc_voltage_v: float
    dc_current_a: float
    dc_power_w: int
    ac_voltage_v: float
    ac_current_a: float
    ac_power_w: int
    temperature_c: int
    daily_yield_wh: int
    inverter_type: str    # e.g. "6400xi"
    checksum_ok: bool


class ParseError(ValueError):
    """Raised when a reply doesn't match the series-"00" command-`0` shape."""


def parse_cmd0(raw: bytes) -> MeasuredValues:
    """Parse a series-"00" reply to `#<addr>0<CR>` into a MeasuredValues.

    Verified against on-site bytes from a Powador 6400xi and 8000xi. Uses
    fixed offsets (see verify_checksum's docstring for the layout). The
    type string lives after the checksum byte and runs to the trailing CR.

    Checksum failure is not fatal — the parser sets ``checksum_ok=False``
    on the result so the caller can decide what to do.
    """
    # The numeric payload + checksum is complete at 58 bytes; the trailing
    # type string (" 6400xi<CR>") is decorative. On-site captures
    # (2026-07-12) show xi units pausing hundreds of ms between checksum
    # and type string, so real frames legitimately end at 58-65 bytes.
    if len(raw) < 58:
        raise ParseError(f"frame too short: {len(raw)} bytes (need >= 58)")
    if raw[0:2] != b"\n*":
        raise ParseError(f"missing LF* header: {raw[:2]!r}")
    if raw[4:5] != b"0":
        raise ParseError(f"command byte is {raw[4:5]!r}, expected b'0'")

    try:
        address = int(raw[2:4])
    except ValueError as e:
        raise ParseError(f"address bytes not numeric: {raw[2:4]!r}") from e

    def num(slice_: bytes, conv):
        s = slice_.strip().decode("ascii", errors="replace")
        try:
            return conv(s)
        except ValueError as e:
            raise ParseError(f"failed to parse {s!r}: {e}") from e

    checksum_ok, _expected, _actual = verify_checksum(raw)

    inverter_type = raw[58:].split(b"\r", 1)[0].strip().decode("ascii", errors="replace")

    return MeasuredValues(
        address=address,
        status=num(raw[6:9], int),
        dc_voltage_v=num(raw[10:15], float),
        dc_current_a=num(raw[16:21], float),
        dc_power_w=num(raw[22:27], int),
        ac_voltage_v=num(raw[28:33], float),
        ac_current_a=num(raw[34:39], float),
        ac_power_w=num(raw[40:45], int),
        temperature_c=num(raw[46:49], int),
        daily_yield_wh=num(raw[50:56], int),
        inverter_type=inverter_type,
        checksum_ok=checksum_ok,
    )


# --- Command `3` reply parsing (total yield + hours) ---------------------

@dataclass
class TotalYield:
    """Decoded `#<adr>3<CR>` reply.

    Field meanings come from APL_RS485protocol Section 2.3, but real on-site
    bytes diverge from the doc in two ways:
    - xi units (Powador 6400xi/8000xi) omit the `*<adr>3 ` reply header that
      the doc shows, sending only `\\n<spaces>D1 D2 ... D7\\r`.
    - The total-yield unit (D4) is **kWh** for xi units but **Wh** for the
      blueplanet (Generic Protocol). We expose `total_yield_raw` and let the
      caller scale.
    """
    address: int | None             # None when no header is present (xi quirk)
    daily_peak_w: int               # D1
    daily_yield_wh: int             # D2 — Wh on every series we've seen
    short_time_counter: int         # D3 — short-window accumulator, raw
    total_yield_raw: int            # D4 — kWh on xi, Wh on blueplanet (caller scales)
    daily_uptime: str               # D5 — `hhhhhh:mm`
    short_time_uptime: str          # D6
    total_uptime: str               # D7


def parse_cmd3(raw: bytes) -> TotalYield:
    """Parse a `#<addr>3<CR>` reply.

    Tolerates both shapes seen in the wild: `\\n*xx3 …` (doc-spec) and `\\n …`
    (xi units). Uses whitespace-splitting because real-world field widths
    don't perfectly match what the doc claims. The frame ends at the first
    CR — anything after it (0x00 bus-turnaround glitches, straggler bytes
    from an adjacent reply; both observed on-site 2026-07-12) is ignored,
    matching the C++ port's tokenizer.
    """
    if len(raw) < 10 or raw[0:1] != b"\n":
        raise ParseError(f"missing LF: {raw[:2]!r}")

    body = raw[1:].split(b"\r", 1)[0]
    address: int | None
    if body.startswith(b"*"):
        # Doc-spec: *<adr>3 <fields>
        if len(body) < 5 or body[3:4] != b"3":
            raise ParseError(f"unexpected header: {body[:5]!r}")
        try:
            address = int(body[1:3])
        except ValueError as e:
            raise ParseError(f"address bytes not numeric: {body[1:3]!r}") from e
        rest = body[4:]
    else:
        # xi quirk: no header at all.
        address = None
        rest = body

    tokens = rest.split()
    if len(tokens) != 7:
        raise ParseError(f"expected 7 fields, got {len(tokens)}: {tokens!r}")

    try:
        return TotalYield(
            address=address,
            daily_peak_w=int(tokens[0]),
            daily_yield_wh=int(tokens[1]),
            short_time_counter=int(tokens[2]),
            total_yield_raw=int(tokens[3]),
            daily_uptime=tokens[4].decode("ascii"),
            short_time_uptime=tokens[5].decode("ascii"),
            total_uptime=tokens[6].decode("ascii"),
        )
    except ValueError as e:
        raise ParseError(f"failed to parse cmd 3 fields {tokens!r}: {e}") from e


# --- Command `8` reply parsing (firmware) --------------------------------

@dataclass
class Firmware:
    """Decoded `#<adr>8<CR>` reply.

    Two shapes seen in the wild:
    - xi units: `*<adr>8 K222.36DE 6817` — single vendor version + 4-char hex
    - blueplanet: `*<adr>8 ARM V6.53 D6A5 Config V6.3124 2F4F DSP V4.08 6BA0`
    """
    address: int
    raw_text: str   # everything after `*<adr>8 `


def parse_cmd8(raw: bytes) -> Firmware:
    if len(raw) < 7 or raw[0:2] != b"\n*" or raw[4:5] != b"8":
        raise ParseError(f"not a cmd-8 reply: {raw[:6]!r}")
    try:
        address = int(raw[2:4])
    except ValueError as e:
        raise ParseError(f"address bytes not numeric: {raw[2:4]!r}") from e
    text = raw[5:].split(b"\r", 1)[0].strip().decode("ascii", errors="replace")
    return Firmware(address=address, raw_text=text)


# --- Command `9` reply parsing (inverter type) ---------------------------

def parse_cmd9(raw: bytes) -> str:
    """Return the inverter type string from a `#<adr>9<CR>` reply.

    Format is the same on every series we've seen: `\\n*<adr>9 <type> <chk>\\r`
    where `<chk>` is a single byte (1-byte sum for xi, single ASCII char for
    blueplanet — the doc's "1 / CRC16" line in §3.8). We just pull the type.
    """
    if len(raw) < 8 or raw[0:2] != b"\n*" or raw[4:5] != b"9":
        raise ParseError(f"not a cmd-9 reply: {raw[:6]!r}")
    body = raw[5:].split(b"\r", 1)[0]
    # Drop the trailing `<space><checksum-byte>`.
    if len(body) < 3 or body[-2:-1] != b" ":
        raise ParseError(f"unexpected cmd-9 body: {body!r}")
    return body[:-2].strip().decode("ascii", errors="replace")


# --- Command `s` reply parsing (serial number) ---------------------------

def parse_cmds(raw: bytes) -> str:
    """Return the serial number from a `#<adr>s<CR>` reply.

    Only the blueplanet (Generic Protocol) supports this — xi units return
    zero bytes. Format: `\\n*<adr>s <serial> <CRC16>\\r`.
    """
    if len(raw) < 8 or raw[0:2] != b"\n*" or raw[4:5] != b"s":
        raise ParseError(f"not a cmd-s reply: {raw[:6]!r}")
    body = raw[5:].split(b"\r", 1)[0].strip()
    parts = body.rsplit(b" ", 1)
    if len(parts) != 2:
        raise ParseError(f"unexpected cmd-s body: {body!r}")
    return parts[0].strip().decode("ascii", errors="replace")
