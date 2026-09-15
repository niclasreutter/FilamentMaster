"""Sensors for rolls, AMS slots and the inventory as a whole."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, UnitOfLength, UnitOfMass
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import ATTR_SLOT, ATTR_SPOOL_ID
from .coordinator import FilamentConfigEntry, FilamentCoordinator
from .entity import FilamentHubEntity, FilamentSpoolEntity, async_setup_spool_platform

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: FilamentConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the sensors."""
    coordinator = entry.runtime_data

    async_setup_spool_platform(
        hass,
        entry,
        async_add_entities,
        lambda coord, spool: (
            RemainingWeightSensor(coord, spool.id),
            ConsumedSensor(coord, spool.id),
            RemainingPercentSensor(coord, spool.id),
            RemainingLengthSensor(coord, spool.id),
        ),
    )

    entities: list[SensorEntity] = [
        InventoryWeightSensor(coordinator),
        InventoryValueSensor(coordinator),
        SpoolCountSensor(coordinator),
        LowStockCountSensor(coordinator),
    ]
    entities.extend(
        SlotSensor(coordinator, slot) for slot in range(1, coordinator.slot_count + 1)
    )
    async_add_entities(entities)


class RemainingWeightSensor(FilamentSpoolEntity, SensorEntity):
    """How much filament is left on a roll."""

    _attr_translation_key = "remaining_weight"
    _attr_device_class = SensorDeviceClass.WEIGHT
    _attr_native_unit_of_measurement = UnitOfMass.GRAMS
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 0

    def __init__(self, coordinator: FilamentCoordinator, spool_id: str) -> None:
        """Initialise the sensor."""
        super().__init__(coordinator, spool_id, "remaining_weight")

    @property
    def native_value(self) -> float | None:
        """Return the remaining weight in grams."""
        return self.spool.remaining_weight if self.spool else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the details a dashboard wants next to the number."""
        spool = self.spool
        if spool is None:
            return {}
        filament_type = self.filament_type
        return {
            ATTR_SPOOL_ID: spool.id,
            "type_id": spool.type_id,
            "vendor": self.coordinator.db.vendor_name(spool.type_id),
            "material": filament_type.material if filament_type else None,
            "color_name": filament_type.color_name if filament_type else None,
            "color_hex": (
                f"#{filament_type.color_hex}"
                if filament_type and filament_type.color_hex
                else None
            ),
            "diameter": filament_type.diameter if filament_type else None,
            "spool_weight": self.coordinator.spool_weight_for(spool),
            "initial_weight": spool.initial_weight,
            "location": spool.location,
            "purchase_date": spool.purchase_date,
            "price": spool.price,
            ATTR_SLOT: self.coordinator.store.slot_of(spool.id),
            "assignment_source": spool.assignment_source,
            "assignment_time": spool.assignment_time,
            "nfc_tag_id": spool.nfc_tag_id,
            "rfid_uids": list(spool.rfid_uids),
            "note": spool.note,
        }


class ConsumedSensor(FilamentSpoolEntity, SensorEntity):
    """Everything this roll has ever printed.

    ``total_increasing`` is what turns this into a long term statistic
    without any extra configuration.
    """

    _attr_translation_key = "total_consumed"
    _attr_device_class = SensorDeviceClass.WEIGHT
    _attr_native_unit_of_measurement = UnitOfMass.GRAMS
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_suggested_display_precision = 0

    def __init__(self, coordinator: FilamentCoordinator, spool_id: str) -> None:
        """Initialise the sensor."""
        super().__init__(coordinator, spool_id, "total_consumed")

    @property
    def native_value(self) -> float | None:
        """Return the lifetime consumption in grams."""
        return self.spool.total_consumed if self.spool else None


class RemainingPercentSensor(FilamentSpoolEntity, SensorEntity):
    """How full the roll still is."""

    _attr_translation_key = "remaining_percent"
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 0

    def __init__(self, coordinator: FilamentCoordinator, spool_id: str) -> None:
        """Initialise the sensor."""
        super().__init__(coordinator, spool_id, "remaining_percent")

    @property
    def native_value(self) -> float | None:
        """Return the remaining share in percent."""
        return self.spool.remaining_percent if self.spool else None


class RemainingLengthSensor(FilamentSpoolEntity, SensorEntity):
    """The remaining weight expressed as filament length."""

    _attr_translation_key = "remaining_length"
    _attr_device_class = SensorDeviceClass.DISTANCE
    _attr_native_unit_of_measurement = UnitOfLength.METERS
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 1

    def __init__(self, coordinator: FilamentCoordinator, spool_id: str) -> None:
        """Initialise the sensor."""
        super().__init__(coordinator, spool_id, "remaining_length")

    @property
    def native_value(self) -> float | None:
        """Return the remaining length in metres."""
        spool = self.spool
        filament_type = self.filament_type
        if spool is None or filament_type is None:
            return None
        return round(filament_type.weight_to_length(spool.remaining_weight), 1)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the density used for the conversion."""
        filament_type = self.filament_type
        if filament_type is None:
            return {}
        return {
            "density": filament_type.effective_density,
            "diameter": filament_type.diameter,
        }


class SlotSensor(FilamentHubEntity, SensorEntity):
    """Which roll is loaded in one AMS slot."""

    _attr_translation_key = "slot"

    def __init__(self, coordinator: FilamentCoordinator, slot: int) -> None:
        """Initialise the sensor."""
        super().__init__(coordinator, f"slot_{slot}")
        self._slot = slot
        self._attr_translation_placeholders = {"slot": str(slot)}

    @property
    def native_value(self) -> str | None:
        """Return the name of the loaded roll."""
        state = self.coordinator.store.slots.get(self._slot)
        if state is None or state.spool_id is None:
            return "empty"
        spool = self.coordinator.store.get_spool(state.spool_id)
        return self.coordinator.spool_name(spool) if spool else "unknown"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return what the printer reported and how we resolved it."""
        state = self.coordinator.store.slots.get(self._slot)
        spool = (
            self.coordinator.store.get_spool(state.spool_id)
            if state and state.spool_id
            else None
        )
        filament_type = self.coordinator.type_of(spool) if spool else None
        return {
            ATTR_SLOT: self._slot,
            ATTR_SPOOL_ID: state.spool_id if state else None,
            # "Detected automatically" and "chosen by hand" must stay
            # distinguishable — a silent mis-assignment subtracts grams from
            # the wrong roll.
            "assignment_source": state.source if state else None,
            "since": state.since if state else None,
            "remaining_weight": spool.remaining_weight if spool else None,
            "material": filament_type.material if filament_type else None,
            "color_hex": (
                f"#{filament_type.color_hex}"
                if filament_type and filament_type.color_hex
                else None
            ),
            "reported_material": state.reported_material if state else None,
            "reported_color": (
                f"#{state.reported_color}" if state and state.reported_color else None
            ),
            "reported_uid": state.reported_uid if state else None,
        }


class InventoryWeightSensor(FilamentHubEntity, SensorEntity):
    """Total filament in stock."""

    _attr_translation_key = "inventory_weight"
    _attr_device_class = SensorDeviceClass.WEIGHT
    _attr_native_unit_of_measurement = UnitOfMass.GRAMS
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 0

    def __init__(self, coordinator: FilamentCoordinator) -> None:
        """Initialise the sensor."""
        super().__init__(coordinator, "inventory_weight")

    @property
    def native_value(self) -> float:
        """Return the total remaining weight."""
        return round(
            sum(
                spool.remaining_weight
                for spool in self.coordinator.store.active_spools()
            ),
            1,
        )


class InventoryValueSensor(FilamentHubEntity, SensorEntity):
    """What the filament on the shelf is still worth."""

    _attr_translation_key = "inventory_value"
    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_state_class = SensorStateClass.TOTAL
    _attr_suggested_display_precision = 2

    def __init__(self, coordinator: FilamentCoordinator) -> None:
        """Initialise the sensor."""
        super().__init__(coordinator, "inventory_value")

    @property
    def native_unit_of_measurement(self) -> str:
        """Return the configured currency."""
        return self.coordinator.currency

    @property
    def native_value(self) -> float:
        """Return the value of the remaining filament."""
        total = 0.0
        for spool in self.coordinator.store.active_spools():
            value = spool.value(self.coordinator.type_of(spool))
            if value:
                total += value
        return round(total, 2)


class SpoolCountSensor(FilamentHubEntity, SensorEntity):
    """How many rolls are in stock."""

    _attr_translation_key = "spool_count"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: FilamentCoordinator) -> None:
        """Initialise the sensor."""
        super().__init__(coordinator, "spool_count")

    @property
    def native_value(self) -> int:
        """Return the number of non-archived rolls."""
        return len(self.coordinator.store.active_spools())

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the archived count alongside."""
        active = len(self.coordinator.store.active_spools())
        return {"archived": len(self.coordinator.store.spools) - active}


class LowStockCountSensor(FilamentHubEntity, SensorEntity):
    """How many rolls are running out."""

    _attr_translation_key = "low_stock_count"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: FilamentCoordinator) -> None:
        """Initialise the sensor."""
        super().__init__(coordinator, "low_stock_count")

    @property
    def native_value(self) -> int:
        """Return the number of rolls below their threshold."""
        return sum(
            1
            for spool in self.coordinator.store.active_spools()
            if spool.remaining_weight <= self.coordinator.threshold_for(spool)
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return which rolls should go on the shopping list."""
        return {
            "spools": [
                self.coordinator.spool_name(spool)
                for spool in self.coordinator.store.active_spools()
                if spool.remaining_weight <= self.coordinator.threshold_for(spool)
            ]
        }
