"""The slug table is a stability contract, so it is tested like one.

`STATUS_SLUG` exists so consumers with a fixed vocabulary — a Home Assistant
enum sensor, which must declare every possible state up front and translate it —
have something that never moves. The display labels in `STATUS_TEXT` have been
corrected more than once and may be corrected again; the slugs must not follow
them. These tests pin the properties that consumers are entitled to rely on.
"""

from __future__ import annotations

import re

from kaco_rs485.status import (
    FAULT,
    OPERATING,
    STATUS_OPTIONS,
    STATUS_SLUG,
    STATUS_TEXT,
    is_fault,
    status_slug,
    status_text,
)

# Codes the vendor names identically, which therefore share one slug on purpose.
INTENTIONAL_ALIASES = {
    "mpp_tracking": [4, 5],
    "waiting": [6, 7],
    "selftest_error": [32, 59],
}


def test_every_documented_code_has_a_slug() -> None:
    """No code may be in the text table but missing from the slug table.

    This is the failure that would ship a sensor able to report a state it never
    declared, so it is worth an explicit assertion rather than trusting review.
    """
    assert set(STATUS_SLUG) == set(STATUS_TEXT)


def test_slugs_are_valid_identifiers() -> None:
    """Lowercase, digits and underscores only, no leading or trailing underscore.

    This is the format Home Assistant accepts for enum options and translation
    keys; anything else fails at runtime rather than at import.
    """
    for code, slug in STATUS_SLUG.items():
        assert re.fullmatch(r"[a-z0-9]+(?:_[a-z0-9]+)*", slug), (code, slug)


def test_only_the_intended_codes_share_a_slug() -> None:
    """Any *other* collision is a mistake that would merge two distinct states."""
    shared: dict[str, list[int]] = {}
    for code, slug in sorted(STATUS_SLUG.items()):
        shared.setdefault(slug, []).append(code)

    assert {s: c for s, c in shared.items() if len(c) > 1} == INTENTIONAL_ALIASES


def test_options_is_the_deduplicated_sorted_vocabulary() -> None:
    assert STATUS_OPTIONS == sorted(set(STATUS_SLUG.values()))
    assert len(STATUS_OPTIONS) == len(STATUS_SLUG) - 3  # the three aliases above


def test_status_slug_returns_none_for_unknown_codes() -> None:
    """Unlike `status_text`, this must not invent a value.

    A caller declaring a fixed vocabulary cannot accept an out-of-vocabulary
    state; `None` lets it render unknown instead.
    """
    unknown = max(STATUS_TEXT) + 1
    assert status_slug(unknown) is None
    assert status_text(unknown) == f"Code {unknown}"


def test_slug_table_covers_both_halves() -> None:
    """Operating and fault codes alike — the enum spans the whole table."""
    assert set(OPERATING) <= set(STATUS_SLUG)
    assert set(FAULT) <= set(STATUS_SLUG)


def test_fault_classification_is_unchanged_by_slugs() -> None:
    """Slugs carry no fault information; `is_fault` remains the only source.

    Guards against a future refactor deciding a slug prefix means something.
    """
    assert is_fault(14) is True  # Grid failure
    assert is_fault(4) is False  # MPP tracking
    assert status_slug(14) == "grid_failure"
    assert status_slug(4) == "mpp_tracking"
