"""Operating and fault state codes.

From the KACO RS485 protocol specification's BS (operating state) and FS
(fault state) tables, plus two codes that the specification does not list but
that the hardware emits constantly: 4 while feeding in, and 6 overnight. Any
table built from the document alone will render the two most common states of
a working inverter as "unknown".
"""

from __future__ import annotations

from typing import Final

# BS — operating states.
OPERATING: Final[dict[int, str]] = {
    1: "Waiting for feed-in",
    2: "Generator voltage low",
    4: "Feeding in",  # observed running; absent from the spec table
    6: "Standby",  # observed when the sun is down; absent from the spec table
    8: "Self-test",
    57: "Waiting for reconnection",
    60: "Generator voltage high",
    61: "External power limit",
    63: "Frequency-dependent reduction",
    64: "Output current limit",
    74: "Reactive power limit",
    79: "Insulation test",
}

# FS — fault states.
FAULT: Final[dict[int, str]] = {
    10: "Fault: Overtemperature",
    18: "Fault: Residual current",
    19: "Fault: Insulation",
    30: "Fault: Sensor",
    32: "Fault: Self-test",
    33: "Fault: DC injection",
    34: "Fault: Internal comms",
    35: "Protective shutdown (SW)",
    36: "Protective shutdown (HW)",
    38: "Fault: Generator overvoltage",
    41: "Fault: Grid undervoltage L1",
    42: "Fault: Grid overvoltage L1",
    43: "Fault: Grid undervoltage L2",
    44: "Fault: Grid overvoltage L2",
    45: "Fault: Grid undervoltage L3",
    46: "Fault: Grid overvoltage L3",
    47: "Fault: Line voltage",
    48: "Fault: Underfrequency",
    49: "Fault: Overfrequency",
    50: "Fault: Mean voltage exceeded",
    58: "Fault: Control board overtemperature",
    59: "Fault: Self-test",
    67: "Fault: Power stage",
    70: "Fault: Fan",
    73: "Fault: Islanding detected",
    80: "Insulation test failed",
}

STATUS_TEXT: Final[dict[int, str]] = {**OPERATING, **FAULT}


def status_text(code: int) -> str:
    """Human-readable state, falling back to the raw code.

    Unknown codes are surfaced as `Code <n>` rather than "unknown", because
    the number is the only thing that lets you look it up in the manual.
    """
    return STATUS_TEXT.get(code, f"Code {code}")


def is_fault(code: int) -> bool:
    """True for the FS table — the inverter is not merely idle, it has tripped."""
    return code in FAULT
