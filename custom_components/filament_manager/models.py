"""Data model for the Filament Manager integration.

Three levels, as in any catalogue: a ``Vendor`` sells a ``FilamentType``
(the product you can buy again), and every physical roll on the shelf is a
``Spool`` of that type.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from datetime import datetime
import math
from typing import Any, Self
from uuid import uuid4

from homeassistant.util import dt as dt_util

from .color import normalize_hex
from .const import (
    DEFAULT_DENSITY,
    DEFAULT_DIAMETER,
    MATERIAL_DENSITIES,
)


def new_id() -> str:
    """Return a fresh identifier."""
    return uuid4().hex


def slugify_id(value: str) -> str:
    """Return a stable, human readable id for catalogue entries."""
    slug = "".join(
        char if char.isalnum() else "_" for char in str(value).strip().lower()
    )
    while "__" in slug:
        slug = slug.replace("__", "_")
    return slug.strip("_") or new_id()


def _coerce_float(value: Any) -> float | None:
    """Return ``value`` as float, or ``None`` when it is not a number."""
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(number) or math.isinf(number):
        return None
    return number


class _FromDictMixin:
    """Build dataclasses from untrusted dicts without exploding on extras."""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        """Create an instance, ignoring unknown keys."""
        known = {f.name for f in fields(cls)}  # type: ignore[arg-type]
        return cls(**{key: value for key, value in data.items() if key in known})

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON serialisable representation."""
        return asdict(self)  # type: ignore[call-overload]


@dataclass(slots=True)
class Vendor(_FromDictMixin):
    """A filament manufacturer."""

    id: str
    name: str
    website: str | None = None

    @property
    def sort_key(self) -> str:
        """Return the key used to sort vendors in pickers."""
        return self.name.casefold()


@dataclass(slots=True)
class FilamentType(_FromDictMixin):
    """A product type — the thing you buy again when a roll runs out."""

    id: str
    vendor_id: str
    name: str
    material: str = "PLA"
    color_name: str | None = None
    color_hex: str | None = None
    diameter: float = DEFAULT_DIAMETER
    density: float | None = None
    net_weight: float = 1000.0
    spool_weight: float = 0.0
    spool_type: str = "plastic"
    nozzle_temp_min: int | None = None
    nozzle_temp_max: int | None = None
    bed_temp: int | None = None
    # Some vendors mint one GTIN per colour, others one for the whole range,
    # so a type can legitimately answer to several codes.
    gtin: list[str] = field(default_factory=list)
    source: str = "bundled"

    def __post_init__(self) -> None:
        """Normalise the fields that are compared or rendered later."""
        self.material = (self.material or "PLA").strip().upper()
        self.color_hex = normalize_hex(self.color_hex)
        self.diameter = _coerce_float(self.diameter) or DEFAULT_DIAMETER
        self.density = _coerce_float(self.density)
        self.net_weight = _coerce_float(self.net_weight) or 1000.0
        self.spool_weight = _coerce_float(self.spool_weight) or 0.0
        self.gtin = [
            str(code).strip() for code in (self.gtin or []) if str(code).strip()
        ]

    @property
    def effective_density(self) -> float:
        """Return the density to use for length conversion."""
        if self.density:
            return self.density
        return MATERIAL_DENSITIES.get(self.material, DEFAULT_DENSITY)

    def weight_to_length(self, grams: float) -> float:
        """Return the filament length in metres for ``grams``."""
        radius_cm = (self.diameter / 10) / 2
        area_cm2 = math.pi * radius_cm * radius_cm
        volume_cm3 = grams / self.effective_density
        length_cm = volume_cm3 / area_cm2 if area_cm2 else 0.0
        return length_cm / 100

    @property
    def sort_key(self) -> str:
        """Return the key used to sort types in pickers."""
        return f"{self.name} {self.color_name or ''}".casefold()


@dataclass(slots=True)
class Spool(_FromDictMixin):
    """A physical roll on the shelf."""

    id: str
    type_id: str
    remaining_weight: float = 1000.0
    initial_weight: float = 1000.0
    purchase_date: str | None = None
    price: float | None = None
    location: str | None = None
    archived: bool = False
    # HA tag id of the NFC sticker on this roll.
    nfc_tag_id: str | None = None
    # Bambu spools carry one RFID tag per side, with different UIDs, so that
    # the reader works whichever way round the roll is loaded.
    rfid_uids: list[str] = field(default_factory=list)
    assigned_slot: int | None = None
    assignment_source: str | None = None
    assignment_time: str | None = None
    total_consumed: float = 0.0
    low_stock_threshold: float | None = None
    first_used: str | None = None
    last_used: str | None = None
    note: str | None = None
    created: str | None = None

    def __post_init__(self) -> None:
        """Normalise numeric fields coming from YAML, JSON or the UI."""
        self.remaining_weight = max(0.0, _coerce_float(self.remaining_weight) or 0.0)
        self.initial_weight = (
            _coerce_float(self.initial_weight) or self.remaining_weight
        )
        self.price = _coerce_float(self.price)
        self.total_consumed = max(0.0, _coerce_float(self.total_consumed) or 0.0)
        self.low_stock_threshold = _coerce_float(self.low_stock_threshold)
        self.rfid_uids = [
            str(uid).strip().upper()
            for uid in (self.rfid_uids or [])
            if str(uid).strip()
        ]
        self.archived = bool(self.archived)

    @property
    def remaining_percent(self) -> float:
        """Return how much of the roll is left, in percent."""
        if not self.initial_weight:
            return 0.0
        return round(min(100.0, self.remaining_weight / self.initial_weight * 100), 1)

    @property
    def is_empty(self) -> bool:
        """Return whether the roll is used up."""
        return self.remaining_weight <= 0

    def value(self, filament_type: FilamentType | None) -> float | None:
        """Return the monetary value of the remaining filament."""
        if self.price is None or not self.initial_weight:
            return None
        return round(self.price * (self.remaining_weight / self.initial_weight), 2)


@dataclass(slots=True)
class SlotState:
    """What the resolver believes is loaded in one AMS slot."""

    slot: int
    spool_id: str | None = None
    source: str | None = None
    since: str | None = None
    reported_material: str | None = None
    reported_color: str | None = None
    reported_uid: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON serialisable representation."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SlotState:
        """Create an instance, ignoring unknown keys."""
        known = {f.name for f in fields(cls)}
        return cls(**{key: value for key, value in data.items() if key in known})


def utcnow_iso() -> str:
    """Return the current UTC time as an ISO string."""
    return dt_util.utcnow().isoformat()


def parse_iso(value: str | None) -> datetime | None:
    """Parse an ISO timestamp, returning ``None`` for junk."""
    if not value:
        return None
    return dt_util.parse_datetime(value)
