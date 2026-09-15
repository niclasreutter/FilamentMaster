"""Shared entity plumbing.

Entities are a view of the store, never a place to keep data: every one of
them reads through the coordinator, and archived rolls simply lose their
entities while their history stays in ``.storage``.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER, SIGNAL_SPOOLS_CHANGED
from .coordinator import FilamentConfigEntry, FilamentCoordinator
from .models import FilamentType, Spool


class FilamentBaseEntity(CoordinatorEntity[FilamentCoordinator]):
    """Common behaviour for every entity of this integration."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: FilamentCoordinator, key: str) -> None:
        """Initialise the entity."""
        super().__init__(coordinator)
        self._key = key

    @property
    def db(self):
        """Return the master data."""
        return self.coordinator.db


class FilamentHubEntity(FilamentBaseEntity):
    """An entity that belongs to the integration itself, not to one roll."""

    def __init__(self, coordinator: FilamentCoordinator, key: str) -> None:
        """Initialise the entity."""
        super().__init__(coordinator, key)
        self._attr_unique_id = f"{coordinator.config_entry.entry_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={coordinator.hub_identifier},
            name="Filament Manager",
            manufacturer=MANUFACTURER,
            entry_type=None,
        )


class FilamentSpoolEntity(FilamentBaseEntity):
    """An entity that belongs to one physical roll."""

    def __init__(
        self, coordinator: FilamentCoordinator, spool_id: str, key: str
    ) -> None:
        """Initialise the entity."""
        super().__init__(coordinator, key)
        self._spool_id = spool_id
        self._attr_unique_id = f"{spool_id}_{key}"
        self._attr_device_info = self._build_device_info()

    def _build_device_info(self) -> DeviceInfo:
        """Return the device this entity belongs to."""
        spool = self.coordinator.store.get_spool(self._spool_id)
        filament_type = self.filament_type
        return DeviceInfo(
            identifiers={self.coordinator.spool_device_identifier(self._spool_id)},
            name=(self.coordinator.spool_name(spool) if spool else self._spool_id),
            manufacturer=(
                self.coordinator.db.vendor_name(spool.type_id)
                if spool
                else MANUFACTURER
            )
            or MANUFACTURER,
            model=filament_type.material if filament_type else None,
            model_id=filament_type.id if filament_type else None,
            serial_number=self._spool_id[:8],
            via_device=self.coordinator.hub_identifier,
        )

    @property
    def spool(self) -> Spool | None:
        """Return the roll this entity describes."""
        return self.coordinator.store.get_spool(self._spool_id)

    @property
    def filament_type(self) -> FilamentType | None:
        """Return the filament type of this roll."""
        spool = self.coordinator.store.get_spool(self._spool_id)
        return self.coordinator.type_of(spool) if spool else None

    @property
    def available(self) -> bool:
        """Return whether the roll still exists and is not archived."""
        spool = self.spool
        return super().available and spool is not None and not spool.archived


@callback
def async_setup_spool_platform(
    hass: HomeAssistant,
    entry: FilamentConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
    factory: Callable[[FilamentCoordinator, Spool], Iterable[Entity]],
) -> None:
    """Add per-roll entities and keep up with new rolls.

    Rolls come and go while HA runs, so every platform subscribes to the
    inventory instead of taking a snapshot at setup time.
    """
    coordinator = entry.runtime_data
    known: set[str] = set()

    @callback
    def _sync() -> None:
        new_entities: list[Entity] = []
        for spool in coordinator.store.active_spools():
            if spool.id in known:
                continue
            known.add(spool.id)
            new_entities.extend(factory(coordinator, spool))
        if new_entities:
            async_add_entities(new_entities)

    @callback
    def _handle_change() -> None:
        active = {spool.id for spool in coordinator.store.active_spools()}
        # Forget archived rolls so that restoring one brings its entities back.
        known.intersection_update(active)
        _sync()

    entry.async_on_unload(
        async_dispatcher_connect(hass, SIGNAL_SPOOLS_CHANGED, _handle_change)
    )
    _sync()


__all__ = [
    "DOMAIN",
    "FilamentBaseEntity",
    "FilamentHubEntity",
    "FilamentSpoolEntity",
    "async_setup_spool_platform",
]
