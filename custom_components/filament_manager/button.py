"""Buttons on the roll device: buy another one, or take it off the shelf."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import FilamentConfigEntry, FilamentCoordinator
from .entity import FilamentSpoolEntity, async_setup_spool_platform

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: FilamentConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the buttons."""
    async_setup_spool_platform(
        hass,
        entry,
        async_add_entities,
        lambda coord, spool: (
            DuplicateSpoolButton(coord, spool.id),
            ArchiveSpoolButton(coord, spool.id),
        ),
    )


class DuplicateSpoolButton(FilamentSpoolEntity, ButtonEntity):
    """Add another roll of the same type — one click for a re-buy."""

    _attr_translation_key = "duplicate"

    def __init__(self, coordinator: FilamentCoordinator, spool_id: str) -> None:
        """Initialise the button."""
        super().__init__(coordinator, spool_id, "duplicate")

    async def async_press(self) -> None:
        """Clone this roll."""
        self.coordinator.async_duplicate_spool(self._spool_id)


class ArchiveSpoolButton(FilamentSpoolEntity, ButtonEntity):
    """Archive the roll: entities go, the history stays."""

    _attr_translation_key = "archive"

    def __init__(self, coordinator: FilamentCoordinator, spool_id: str) -> None:
        """Initialise the button."""
        super().__init__(coordinator, spool_id, "archive")

    async def async_press(self) -> None:
        """Archive this roll."""
        self.coordinator.async_archive_spool(self._spool_id, True)
