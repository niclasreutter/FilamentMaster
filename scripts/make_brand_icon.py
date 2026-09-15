"""Regenerate the brand assets: a filament spool seen from the side.

Run from the repository root with Pillow installed:

    python scripts/make_brand_icon.py

HACS looks for these under ``custom_components/<domain>/brand/`` until the
integration is listed in the home-assistant/brands repository.
"""

import math

from PIL import Image, ImageDraw

SS = 8  # supersampling factor for smooth edges

FLANGE = (46, 52, 64, 255)  # dark slate spool body
FLANGE_EDGE = (28, 32, 42, 255)  # rim
FILAMENT = (31, 111, 235, 255)  # the wound filament
FILAMENT_DARK = (21, 84, 185, 255)
HUB = (236, 239, 244, 255)  # the hole in the middle


def draw(size: int) -> Image.Image:
    """Render the icon at ``size`` pixels square."""
    canvas = size * SS
    image = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    center = canvas / 2
    outer = canvas * 0.46
    filament_outer = canvas * 0.355
    filament_inner = canvas * 0.175
    hub = canvas * 0.105

    def circle(radius: float, fill, width: int = 0, outline=None) -> None:
        box = (center - radius, center - radius, center + radius, center + radius)
        draw.ellipse(box, fill=fill, outline=outline, width=width)

    # Flange, with a slightly darker rim so the shape reads on light and dark.
    circle(outer, FLANGE, width=int(canvas * 0.018), outline=FLANGE_EDGE)
    # The wound filament.
    circle(filament_outer, FILAMENT)
    # Two darker turns hint at winding without adding noise at 32 px.
    for radius in (filament_outer * 0.86, filament_outer * 0.66):
        circle(radius, None, width=int(canvas * 0.016), outline=FILAMENT_DARK)
    # Back to the flange between filament and hub, then the hole.
    circle(filament_inner, FLANGE)

    # Ventilation holes in the flange: without them the rings read as a
    # target rather than as a spool.
    hole_radius = canvas * 0.032
    hole_orbit = (outer + filament_outer) / 2
    for index in range(6):
        angle = math.radians(index * 60 - 90)
        x = center + hole_orbit * math.cos(angle)
        y = center + hole_orbit * math.sin(angle)
        draw.ellipse(
            (x - hole_radius, y - hole_radius, x + hole_radius, y + hole_radius),
            fill=HUB,
        )

    circle(hub, HUB)

    return image.resize((size, size), Image.LANCZOS)


for size, name in ((256, "icon.png"), (512, "icon@2x.png")):
    draw(size).save(f"custom_components/filament_manager/brand/{name}")
    print(name, size)

# The logo may be wider; a square one is valid and keeps the set consistent.
for size, name in ((256, "logo.png"), (512, "logo@2x.png")):
    draw(size).save(f"custom_components/filament_manager/brand/{name}")
    print(name, size)
