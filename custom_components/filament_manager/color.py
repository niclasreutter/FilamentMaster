"""Colour helpers for the heuristic spool matcher.

Filament colours are compared in CIE L*a*b* space instead of plain RGB: a
euclidean distance in RGB says that dark blue and dark green are close
neighbours, which produces the kind of silent mis-assignment that later
subtracts grams from the wrong spool.
"""

from __future__ import annotations

import math

# D65 reference white, 2 degree observer.
_WHITE_X = 95.047
_WHITE_Y = 100.0
_WHITE_Z = 108.883


def normalize_hex(value: str | None) -> str | None:
    """Return ``value`` as an upper case ``RRGGBB`` string, or ``None``.

    Accepts ``#RGB``, ``#RRGGBB`` and ``RRGGBBAA`` (as reported by Bambu
    printers, where the last byte is the alpha channel and is dropped).
    """
    if not value:
        return None
    text = str(value).strip().lstrip("#").upper()
    if not text:
        return None
    if len(text) == 3:
        text = "".join(char * 2 for char in text)
    if len(text) == 8:
        text = text[:6]
    if len(text) != 6:
        return None
    try:
        int(text, 16)
    except ValueError:
        return None
    return text


def hex_to_rgb(value: str) -> tuple[int, int, int]:
    """Convert a normalised hex string to an RGB tuple."""
    return (int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16))


def rgb_to_hex(rgb: tuple[int, int, int]) -> str:
    """Convert an RGB tuple to a normalised hex string."""
    return "".join(f"{max(0, min(255, int(channel))):02X}" for channel in rgb)


def _srgb_to_linear(channel: float) -> float:
    if channel <= 0.04045:
        return channel / 12.92
    return ((channel + 0.055) / 1.055) ** 2.4


def _pivot_xyz(value: float) -> float:
    if value > 0.008856:
        return value ** (1 / 3)
    return (7.787 * value) + (16 / 116)


def rgb_to_lab(rgb: tuple[int, int, int]) -> tuple[float, float, float]:
    """Convert sRGB to CIE L*a*b*."""
    red, green, blue = (_srgb_to_linear(channel / 255) * 100 for channel in rgb)

    x = red * 0.4124 + green * 0.3576 + blue * 0.1805
    y = red * 0.2126 + green * 0.7152 + blue * 0.0722
    z = red * 0.0193 + green * 0.1192 + blue * 0.9505

    fx = _pivot_xyz(x / _WHITE_X)
    fy = _pivot_xyz(y / _WHITE_Y)
    fz = _pivot_xyz(z / _WHITE_Z)

    return (116 * fy) - 16, 500 * (fx - fy), 200 * (fy - fz)


def color_distance(first: str | None, second: str | None) -> float | None:
    """Return the CIE76 distance between two hex colours.

    ``None`` means at least one side carries no usable colour, which the
    matcher treats as "cannot decide" rather than "no match".
    """
    left = normalize_hex(first)
    right = normalize_hex(second)
    if left is None or right is None:
        return None
    lab_left = rgb_to_lab(hex_to_rgb(left))
    lab_right = rgb_to_lab(hex_to_rgb(right))
    return math.dist(lab_left, lab_right)
