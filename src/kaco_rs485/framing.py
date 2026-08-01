"""Where one reply ends and the next begins.

This is the module that exists because the transport changed. When the bus was
read through a local serial port, framing could cheat: read chunks until one
came back empty and let the driver's inter-byte timeout do the work. Reading
the same bus through an ESPHome serial proxy destroys that. The proxy batches
up to 256 bytes per ESP loop cycle and ships them over wifi, so byte arrival
time at this end says nothing about byte arrival time on the wire.

So completion is decided from frame *content*, with an idle gap only as the
fallback for shapes we cannot recognise. Four field-verified quirks constrain
the rules (all from on-site captures, 2026-04 and 2026-07):

- Replies start 1-2 s after the request. Anything that gives up after 1 s
  truncates a healthy reply.
- xi units pause >250 ms *inside* a cmd `0` frame, between the checksum byte
  and the trailing type string. Real frames end anywhere from 58 to 65 bytes.
- The cmd `0` checksum byte may be any value 0-255 — including 0x0A and 0x0D.
  Scanning for the first CR would truncate at the checksum roughly 1 frame in
  256. This is why cmd `0` is length-gated before any CR search.
- 0x00 glitch bytes appear at bus turnaround, before and after frames.
"""

from __future__ import annotations

# Both timeouts below were measured against Powador 6400xi and 8000xi units.
# They are defaults, not protocol constants: a different series, a long cable
# run, or a slower proxy may need more. `AsyncBus` takes overrides.

# No bytes at all within this window means the inverter is genuinely not
# answering. Sized for the observed 1-2 s reply latency, not for the wire.
REPLY_START_TIMEOUT_S = 2.5

# Silence *after* at least one byte has arrived means the frame is over. Sized
# to clear the >250 ms mid-frame pause the xi units take before the type
# string. Only reached for shapes `is_complete` cannot recognise.
REPLY_GAP_S = 0.4

# A cmd `0` frame's numeric block plus its checksum byte. The trailing type
# string (" 6400xi") is decorative — parse_cmd0 tolerates its absence.
MIN_CMD0_LEN = 58

LF = 0x0A
CR = 0x0D


def trim_leading_junk(raw: bytes) -> bytes:
    """Drop bus-turnaround glitch bytes preceding the frame.

    Every reply shape in this protocol starts with LF, so anything before the
    first LF is noise. Returns empty if no LF has arrived yet.
    """
    idx = raw.find(LF)
    return b"" if idx < 0 else raw[idx:]


def is_complete(raw: bytes, command: str) -> bool:
    """True when `raw` holds a whole reply and reading can stop early.

    Must never return True on a partial frame — a false positive here becomes
    a truncated parse, which is far worse than waiting out `REPLY_GAP_S`.
    Returning False is always safe: the caller falls back to the idle gap.

    `raw` is expected to be already trimmed by `trim_leading_junk`.
    """
    if command == "0":
        # Length-gate first, then look for CR from the checksum byte onwards.
        #
        # The checksum at offset 57 can itself be CR, which is why the length
        # gate has to come first — but it is safe to *accept* a CR there. Every
        # byte before offset 57 is ASCII numeric or space and so cannot be
        # 0x0D, meaning the earliest possible CR is the last byte the frame
        # needs. A CR at 57 therefore ends the frame exactly on time rather
        # than truncating it. The vendor datalogger's own driver does the same:
        # break on CR, require count >= 58, checksum buf[1..56] against
        # buf[57].
        #
        # Scanning from MIN_CMD0_LEN instead would still be correct, but would
        # wait out the inter-byte gap on every frame whose checksum happens to
        # be CR.
        return len(raw) >= MIN_CMD0_LEN and CR in raw[MIN_CMD0_LEN - 1 :]

    if command in ("3", "8"):
        # No trailing checksum byte in these shapes, so the first CR past the
        # header is unambiguously the terminator.
        return CR in raw[1:]

    # Commands `9` and `s` end with `<space><checksum><CR>`, and that checksum
    # may be CR itself. There is no length to gate on because the payload is a
    # variable-length type/serial string, so early exit is not safe: fall back
    # to the idle gap. These are one-shot identification reads at startup, so
    # paying REPLY_GAP_S for them costs nothing.
    return False
