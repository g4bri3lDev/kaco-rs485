"""Operating and fault state codes.

The full KACO code table, 0-120. Carried over as data from an analysis of the
original vendor datalogger firmware, cross-checked against the vendor protocol
specification; see `~/Developer/solarlog-re` (local only).

Two things worth knowing about these labels:

- They are **normalised**, not verbatim vendor text. Where the vendor's English
  and German tables disagree, the German is used, because the English table has
  demonstrable errors — codes 51-54 and 84/85 have over/under inverted. Those
  rows are marked below.
- Codes 4 and 6 are the two states a working inverter spends nearly all its
  time in. An earlier version of this library guessed them as "Feeding in" and
  "Standby" from field observation; the vendor names them "MPP tracking" and
  "Waiting". The guesses described the behaviour correctly but the names were
  invented, so the vendor's are used here.
"""

from __future__ import annotations

from typing import Final

# BS - operating states.
OPERATING: Final[dict[int, str]] = {
    0: "Startup",
    1: "Waiting for DC voltage",
    # vendor EN table says 'DC voltage too low'; German disagrees, German used
    2: "Waiting for shutdown",
    3: "Constant voltage mode",
    4: "MPP tracking",  # normal feed-in; handoff previously guessed 'Feeding in'
    5: "MPP tracking",
    6: "Waiting",  # handoff previously guessed 'Standby'
    7: "Waiting",
    8: "Relay test",  # vendor EN says 'Selftest in progress'; German used
    9: "Fault-finding mode",
    11: "Power limiting active",
    15: "Night shutdown",  # vendor treats this as a reason to skip polling
    25: "Testing L electronics",
    26: "Testing grid relay",
    40: "Snow melting",
    60: "DC overvoltage, waiting for PV voltage to drop",
    61: "External power limiting active",  # set by the b018 power-limit telegram
    62: "Island operation (PAC)",
    63: "Frequency-dependent power reduction",
    64: "AC current limit reached",
    74: "External reactive power demand",  # set by the b048 reactive-power telegram
    75: "Selftest in progress",
    76: "Waiting for wind",
    77: "Check DC isolator switch",
    79: "Insulation measurement",
    107: "Check overvoltage protection",
}

# FS - fault states. This is the half that is actionable: an inverter reporting
# any of these has tripped, rather than merely being idle.
FAULT: Final[dict[int, str]] = {
    10: "Overtemperature shutdown",
    12: "Overload shutdown",  # vendor EN mistranslates as 'overcharge'
    13: "Overvoltage shutdown",
    14: "Grid failure",
    16: "Operation inhibited",
    17: "Powador-protect shutdown",
    18: "Residual current shutdown (AFI/RCD)",
    19: "Insulation resistance too low",
    21: "Protective shutdown PV string 1",
    22: "Protective shutdown PV string 2",
    23: "Protective shutdown PV string 3",
    24: "DSP error",
    27: "Extended selftest",
    28: "Hardware error",
    29: "DC ground fault",
    30: "Measurement transformer error",  # vendor EN 'Fault in transformer' duplicates code 66
    31: "RCD module error",
    32: "Selftest error",
    33: "DC feed-in error",
    34: "Communication error",
    35: "Protective shutdown (software)",
    36: "Protective shutdown (hardware)",
    37: "Unknown hardware",
    38: "PV overvoltage error",
    39: "Temperature sensor defective",  # vendor EN table left untranslated
    41: "Grid undervoltage L1",
    42: "Grid overvoltage L1",
    43: "Grid undervoltage L2",
    44: "Grid overvoltage L2",
    45: "Grid undervoltage L3",
    46: "Grid overvoltage L3",
    47: "Grid phase conductor fault",
    48: "Grid underfrequency",
    49: "Grid overfrequency",
    50: "Grid average voltage fault",
    # VENDOR BUG: EN table says over/under backwards vs German for 51-54; German used
    51: "Grid mean voltage under L1",
    52: "Grid mean voltage over L1",  # VENDOR BUG: see code 51
    53: "Grid mean voltage under L2",  # VENDOR BUG: see code 51
    54: "Grid mean voltage over L2",  # VENDOR BUG: see code 51
    55: "DC link error",
    57: "Waiting for reconnect",  # classed as fault by vendor though it reads as a state
    58: "Control card overtemperature",
    59: "Selftest error",
    65: "ROCOF error",
    66: "Plausibility error",  # vendor EN mislabels as 'Fault in transformer'
    67: "Power unit 1 failure",
    68: "Power unit 2 failure",
    69: "Power unit 3 failure",
    70: "Fan 1 failure",
    71: "Fan 2 failure",
    72: "Fan 3 failure",
    73: "Island operation error",
    78: "Residual current too high",
    80: "Insulation measurement not possible",
    81: "Shutdown grid voltage L1",
    82: "Shutdown grid voltage L2",
    83: "Shutdown grid voltage L3",
    # VENDOR BUG: EN table swaps 84/85 vs German; German used
    84: "Shutdown DC link undervoltage",
    85: "Shutdown DC link overvoltage",  # VENDOR BUG: see code 84
    86: "Shutdown DC link asymmetry",
    87: "Shutdown overcurrent L1",
    88: "Shutdown overcurrent L2",
    89: "Shutdown overcurrent L3",
    90: "Shutdown 5V supply collapse",
    91: "Shutdown 2.5V supply collapse",
    92: "Shutdown 1.5V supply collapse",
    93: "Selftest error buffer 1",
    94: "Selftest error buffer 2",
    95: "Selftest error relay 1",
    96: "Selftest error relay 2",
    97: "Shutdown hardware overcurrent",
    98: "Shutdown hardware gate driver",
    99: "Shutdown hardware buffer enable",
    100: "Shutdown hardware overtemperature",
    101: "Plausibility error temperature sensor",
    102: "Plausibility error efficiency",
    103: "Plausibility error voltage",  # vendor EN table left untranslated
    104: "Plausibility error AFI module",
    105: "Plausibility error relay voltage",
    106: "Plausibility error DC/DC",
    108: "Critical overvoltage L1",
    109: "Critical overvoltage L2",
    110: "Critical overvoltage L3",
    111: "Critical undervoltage L1",
    112: "Critical undervoltage L2",
    113: "Critical undervoltage L3",
    114: "DC/DC converter communication error",
    115: "Negative PV current 1",
    116: "Negative PV current 2",
    117: "Negative PV current 3",
    118: "PV overvoltage 1",
    119: "PV overvoltage 2",
    120: "PV overvoltage 3",
}

STATUS_TEXT: Final[dict[int, str]] = {**OPERATING, **FAULT}

# The inverter's own report that it has shut down for the night. The vendor
# datalogger treats this as a reason to stop polling until morning.
NIGHT_SHUTDOWN: Final = 15


def status_text(code: int) -> str:
    """Human-readable state, falling back to the raw code.

    Unknown codes are surfaced as `Code <n>` rather than "unknown", because the
    number is the only thing that lets you look it up in the manual.
    """
    return STATUS_TEXT.get(code, f"Code {code}")


def is_fault(code: int) -> bool:
    """True for the FS table - the inverter has tripped, not merely idled."""
    return code in FAULT
