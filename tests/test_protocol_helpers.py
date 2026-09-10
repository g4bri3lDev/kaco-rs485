"""Derived readings that consumers would otherwise each reimplement."""

from __future__ import annotations

import pytest

from kaco_rs485 import uptime_hours
from kaco_rs485.protocol import parse_cmd0, parse_cmd3
from kaco_rs485.testing import POWADOR_6400XI, POWADOR_8000XI


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("71858:00", 71858.0),
        ("12:12", 12.2),
        ("000000:30", 0.5),
        ("", None),
        (None, None),
        ("not a time", None),
        ("ab:cd", None),
    ],
)
def test_uptime_hours(value: str | None, expected: float | None) -> None:
    """Six-digit hour counts overflow any time type, so this returns hours."""
    assert uptime_hours(value) == expected


def test_uptime_properties_read_the_captured_counters() -> None:
    totals = parse_cmd3(POWADOR_6400XI.reply_to("3"))

    assert totals.total_uptime == "71858:00"
    assert totals.total_uptime_hours == 71858.0
    assert totals.daily_uptime_hours == pytest.approx(12.2)


def test_efficiency_is_reported_while_converting() -> None:
    measured = parse_cmd0(POWADOR_8000XI.reply_to("0"))

    assert measured.dc_power_w == 739
    assert measured.ac_power_w == 681
    assert measured.efficiency_percent == pytest.approx(92.2, abs=0.1)


def test_efficiency_is_suppressed_at_low_dc_power() -> None:
    """Below the floor the quotient is dominated by the inverter's own draw."""
    measured = parse_cmd0(POWADOR_8000XI.reply_to("0"))
    measured.dc_power_w = measured.EFFICIENCY_FLOOR_W

    assert measured.efficiency_percent is None
