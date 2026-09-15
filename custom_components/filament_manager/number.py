"""Numbers for correcting a roll by hand.

The gross weight entity is the one that matters in practice: put the roll on
a kitchen scale, type in what it says, and the empty spool weight from the
filament type turns it into the remaining filament.
"""

from __future__ import annotations

from homeassistant.components.number import NumberDeviceClass, NumberEntity, NumberMode
from homeassistant.const import UnitOfMass
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import FilamentConfigEntry, FilamentCoordinator
from .entity import FilamentSpoolEntity, async_setup_spool_platform

PARALLEL_UPDATES = 0

MAX_WEIGHT = 10000


async def async_setup_entry(
    hass: HomeAssistant,
    entry: FilamentConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the number entities."""
    async_setup_spool_platform(
        hass,
        entry,
        async_add_entities,
        lambda coord, spool: (
            GrossWeightNumber(coord, spool.id),
            RemainingWeightNumber(coord, spool.id),
            LowStockThresholdNumber(coord, spool.id),
        ),
    )


class GrossWeightNumber(FilamentSpoolEntity, NumberEntity):
    """Enter the weight you measured, including the empty spool."""

    _attr_translation_key = "gross_weight"
    _attr_device_class = NumberDeviceClass.WEIGHT
    _attr_native_unit_of_measurement = UnitOfMass.GRAMS
    _attr_native_min_value = 0
    _attr_native_max_value = MAX_WEIGHT
    _attr_native_step = 1
    _attr_mode = NumberMode.BOX

    def __init__(self, coordinator: FilamentCoordinator, spool_id: str) -> None:
        """Initialise the number."""
        super().__init__(coordinator, spool_id, "gross_weight")

    @property
    def native_value(self) -> float | None:
        """Return what the scale should read right now."""
        spool = self.spool
        if spool is None:
            return None
        empty = self.coordinator.spool_weight_for(spool)
        return round(spool.remaining_weight + empty, 1)

    async def async_set_native_value(self, value: float) -> None:
        """Correct the roll from a measured gross weight."""
        self.coordinator.async_correct_weight(self._spool_id, gross_weight=value)


class RemainingWeightNumber(FilamentSpoolEntity, NumberEntity):
    """Set the remaining filament directly."""

    _attr_translation_key = "remaining_weight"
    _attr_device_class = NumberDeviceClass.WEIGHT
    _attr_native_unit_of_measurement = UnitOfMass.GRAMS
    _attr_native_min_value = 0
    _attr_native_max_value = MAX_WEIGHT
    _attr_native_step = 1
    _attr_mode = NumberMode.BOX
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: FilamentCoordinator, spool_id: str) -> None:
        """Initialise the number."""
        super().__init__(coordinator, spool_id, "remaining_weight_set")

    @property
    def native_value(self) -> float | None:
        """Return the remaining filament."""
        return self.spool.remaining_weight if self.spool else None

    async def async_set_native_value(self, value: float) -> None:
        """Set the remaining filament."""
        self.coordinator.async_correct_weight(self._spool_id, remaining_weight=value)


class LowStockThresholdNumber(FilamentSpoolEntity, NumberEntity):
    """Per-roll override of the low stock threshold."""

    _attr_translation_key = "low_stock_threshold"
    _attr_device_class = NumberDeviceClass.WEIGHT
    _attr_native_unit_of_measurement = UnitOfMass.GRAMS
    _attr_native_min_value = 0
    _attr_native_max_value = 2000
    _attr_native_step = 10
    _attr_mode = NumberMode.BOX
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: FilamentCoordinator, spool_id: str) -> None:
        """Initialise the number."""
        super().__init__(coordinator, spool_id, "low_stock_threshold")

    @property
    def native_value(self) -> float | None:
        """Return the threshold in effect."""
        spool = self.spool
        return self.coordinator.threshold_for(spool) if spool else None

    async def async_set_native_value(self, value: float) -> None:
        """Set a roll specific threshold."""
        self.coordinator.store.update_spool(self._spool_id, low_stock_threshold=value)
