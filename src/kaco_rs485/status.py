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

# Stable machine-readable name per code, for consumers that need a fixed
# vocabulary rather than display text — Home Assistant's enum sensor being the
# motivating case, where every possible state must be declared up front and
# translated.
#
# These are DATA, not derived from the labels above, and that is deliberate. The
# labels have been corrected more than once (see the vendor-bug notes), and if a
# slug were computed from its label then any future wording fix would silently
# rename a state and break every automation keyed on it. A label may change; a
# slug never may.
#
# Codes the vendor names identically share a slug — 4/5 "MPP tracking", 6/7
# "Waiting", 32/59 "Selftest error". That is intentional: they are the same
# state as far as the vendor is concerned, and a consumer asking "is it
# tracking?" should not have to test two values. The raw code remains available
# for anyone who needs to tell them apart.
STATUS_SLUG: Final[dict[int, str]] = {
    0: "startup",
    1: "waiting_for_dc_voltage",
    2: "waiting_for_shutdown",
    3: "constant_voltage_mode",
    4: "mpp_tracking",
    5: "mpp_tracking",
    6: "waiting",
    7: "waiting",
    8: "relay_test",
    9: "fault_finding_mode",
    10: "overtemperature_shutdown",
    11: "power_limiting_active",
    12: "overload_shutdown",
    13: "overvoltage_shutdown",
    14: "grid_failure",
    15: "night_shutdown",
    16: "operation_inhibited",
    17: "powador_protect_shutdown",
    18: "residual_current_shutdown_afi_rcd",
    19: "insulation_resistance_too_low",
    21: "protective_shutdown_pv_string_1",
    22: "protective_shutdown_pv_string_2",
    23: "protective_shutdown_pv_string_3",
    24: "dsp_error",
    25: "testing_l_electronics",
    26: "testing_grid_relay",
    27: "extended_selftest",
    28: "hardware_error",
    29: "dc_ground_fault",
    30: "measurement_transformer_error",
    31: "rcd_module_error",
    32: "selftest_error",
    33: "dc_feed_in_error",
    34: "communication_error",
    35: "protective_shutdown_software",
    36: "protective_shutdown_hardware",
    37: "unknown_hardware",
    38: "pv_overvoltage_error",
    39: "temperature_sensor_defective",
    40: "snow_melting",
    41: "grid_undervoltage_l1",
    42: "grid_overvoltage_l1",
    43: "grid_undervoltage_l2",
    44: "grid_overvoltage_l2",
    45: "grid_undervoltage_l3",
    46: "grid_overvoltage_l3",
    47: "grid_phase_conductor_fault",
    48: "grid_underfrequency",
    49: "grid_overfrequency",
    50: "grid_average_voltage_fault",
    51: "grid_mean_voltage_under_l1",
    52: "grid_mean_voltage_over_l1",
    53: "grid_mean_voltage_under_l2",
    54: "grid_mean_voltage_over_l2",
    55: "dc_link_error",
    57: "waiting_for_reconnect",
    58: "control_card_overtemperature",
    59: "selftest_error",
    60: "dc_overvoltage_waiting_for_pv_voltage_to_drop",
    61: "external_power_limiting_active",
    62: "island_operation_pac",
    63: "frequency_dependent_power_reduction",
    64: "ac_current_limit_reached",
    65: "rocof_error",
    66: "plausibility_error",
    67: "power_unit_1_failure",
    68: "power_unit_2_failure",
    69: "power_unit_3_failure",
    70: "fan_1_failure",
    71: "fan_2_failure",
    72: "fan_3_failure",
    73: "island_operation_error",
    74: "external_reactive_power_demand",
    75: "selftest_in_progress",
    76: "waiting_for_wind",
    77: "check_dc_isolator_switch",
    78: "residual_current_too_high",
    79: "insulation_measurement",
    80: "insulation_measurement_not_possible",
    81: "shutdown_grid_voltage_l1",
    82: "shutdown_grid_voltage_l2",
    83: "shutdown_grid_voltage_l3",
    84: "shutdown_dc_link_undervoltage",
    85: "shutdown_dc_link_overvoltage",
    86: "shutdown_dc_link_asymmetry",
    87: "shutdown_overcurrent_l1",
    88: "shutdown_overcurrent_l2",
    89: "shutdown_overcurrent_l3",
    90: "shutdown_5v_supply_collapse",
    91: "shutdown_2_5v_supply_collapse",
    92: "shutdown_1_5v_supply_collapse",
    93: "selftest_error_buffer_1",
    94: "selftest_error_buffer_2",
    95: "selftest_error_relay_1",
    96: "selftest_error_relay_2",
    97: "shutdown_hardware_overcurrent",
    98: "shutdown_hardware_gate_driver",
    99: "shutdown_hardware_buffer_enable",
    100: "shutdown_hardware_overtemperature",
    101: "plausibility_error_temperature_sensor",
    102: "plausibility_error_efficiency",
    103: "plausibility_error_voltage",
    104: "plausibility_error_afi_module",
    105: "plausibility_error_relay_voltage",
    106: "plausibility_error_dc_dc",
    107: "check_overvoltage_protection",
    108: "critical_overvoltage_l1",
    109: "critical_overvoltage_l2",
    110: "critical_overvoltage_l3",
    111: "critical_undervoltage_l1",
    112: "critical_undervoltage_l2",
    113: "critical_undervoltage_l3",
    114: "dc_dc_converter_communication_error",
    115: "negative_pv_current_1",
    116: "negative_pv_current_2",
    117: "negative_pv_current_3",
    118: "pv_overvoltage_1",
    119: "pv_overvoltage_2",
    120: "pv_overvoltage_3",
}

# Every distinct slug, sorted. Consumers that must declare their full vocabulary
# up front (again: HA enum sensors) can use this directly.
STATUS_OPTIONS: Final[list[str]] = sorted(set(STATUS_SLUG.values()))

# The inverter's own report that it has shut down for the night. The vendor
# datalogger treats this as a reason to stop polling until morning.
NIGHT_SHUTDOWN: Final = 15


def status_text(code: int) -> str:
    """Human-readable state, falling back to the raw code.

    Unknown codes are surfaced as `Code <n>` rather than "unknown", because the
    number is the only thing that lets you look it up in the manual.
    """
    return STATUS_TEXT.get(code, f"Code {code}")


def status_slug(code: int) -> str | None:
    """Stable machine-readable name, or `None` for a code not in the table.

    Deliberately does *not* fall back the way `status_text` does. A caller with
    a fixed vocabulary — an enum sensor declaring its options up front — cannot
    accept an invented value: emitting `code_42` for an undocumented code would
    either be rejected as out-of-vocabulary or, worse, silently become a new
    state nobody declared. `None` says "no documented state for this", which the
    caller can render as unknown while keeping the raw code visible elsewhere.
    """
    return STATUS_SLUG.get(code)


def is_fault(code: int) -> bool:
    """True for the FS table - the inverter has tripped, not merely idled."""
    return code in FAULT
