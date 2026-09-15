"""Colour helpers."""

from __future__ import annotations

import pytest

from custom_components.filament_manager.color import (
    color_distance,
    hex_to_rgb,
    normalize_hex,
    rgb_to_hex,
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("#f0a", "FF00AA"),
        ("FF0000", "FF0000"),
        ("#ff0000ff", "FF0000"),  # Bambu appends the alpha channel
        ("  #AbCdEf ", "ABCDEF"),
        ("", None),
        (None, None),
        ("nope", None),
        ("#12345", None),
    ],
)
def test_normalize_hex(value, expected) -> None:
    assert normalize_hex(value) == expected


def test_roundtrip() -> None:
    assert rgb_to_hex(hex_to_rgb("1F6FEB")) == "1F6FEB"


def test_distance_separates_dark_colors() -> None:
    """Navy and dark green are far apart in Lab, unlike in plain RGB."""
    assert color_distance("000080", "008000") > 100


def test_distance_accepts_shades_of_the_same_color() -> None:
    assert color_distance("FF0000", "FE0101") < 2


def test_distance_needs_both_sides() -> None:
    assert color_distance("FF0000", None) is None
