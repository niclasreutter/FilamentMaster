"""Binary sensors: low stock and "currently loaded"."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import ATTR_SLOT
from .coordinator import FilamentConfigEntry, FilamentCoordinator
from .entity import FilamentSpoolEntity, async_setup_spool_platform

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: FilamentConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the binary sensors."""
    async_setup_spool_platform(
        hass,
        entry,
        async_add_entities,
        lambda coord, spool: (
            LowStockBinarySensor(coord, spool.id),
            LoadedBinarySensor(coord, spool.id),
        ),
    )


class LowStockBinarySensor(FilamentSpoolEntity, BinarySensorEntity):
    """Whether a roll has dropped below its threshold."""

    _attr_translation_key = "low_stock"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(self, coordinator: FilamentCoordinator, spool_id: str) -> None:
        """Initialise the binary sensor."""
        super().__init__(coordinator, spool_id, "low_stock")

    @property
    def is_on(self) -> bool | None:
        """Return whether the roll is running out."""
        spool = self.spool
        if spool is None:
            return None
        return spool.remaining_weight <= self.coordinator.threshold_for(spool)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the threshold in effect."""
        spool = self.spool
        if spool is None:
            return {}
        return {
            "threshold": self.coordinator.threshold_for(spool),
            "custom_threshold": spool.low_stock_threshold is not None,
        }


class LoadedBinarySensor(FilamentSpoolEntity, BinarySensorEntity):
    """Whether a roll is currently sitting in an AMS slot."""

    _attr_translation_key = "loaded"

    def __init__(self, coordinator: FilamentCoordinator, spool_id: str) -> None:
        """Initialise the binary sensor."""
        super().__init__(coordinator, spool_id, "loaded")

    @property
    def is_on(self) -> bool:
        """Return whether the roll is loaded."""
        return self.coordinator.store.slot_of(self._spool_id) is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the slot and how the assignment was made."""
        spool = self.spool
        return {
            ATTR_SLOT: self.coordinator.store.slot_of(self._spool_id),
            "assignment_source": spool.assignment_source if spool else None,
        }
