"""The Filament Manager integration.

Filament inventory and consumption tracking for 3D printing, entirely inside
Home Assistant: no extra server, no add-on, no container.
"""

from __future__ import annotations

import logging

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .consumption import ConsumptionTracker
from .coordinator import FilamentConfigEntry, FilamentCoordinator
from .frontend import async_register_frontend
from .notifications import Notifier
from .resolver import SpoolResolver
from .services import async_register_services, async_unregister_services
from .store import SpoolStore
from .websocket import async_register_websocket_api

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.NUMBER,
    Platform.SENSOR,
]


async def async_setup_entry(hass: HomeAssistant, entry: FilamentConfigEntry) -> bool:
    """Set up Filament Manager from a config entry."""
    coordinator = FilamentCoordinator(hass, entry)
    await coordinator.async_setup()
    entry.runtime_data = coordinator

    coordinator.notifier = Notifier(hass, dict(entry.options))
    coordinator.resolver = SpoolResolver(hass, coordinator, coordinator.notifier)
    await coordinator.resolver.async_setup()
    entry.async_on_unload(coordinator.resolver.async_shutdown)

    coordinator.tracker = ConsumptionTracker(hass, coordinator)
    coordinator.tracker.async_setup()
    entry.async_on_unload(coordinator.tracker.async_shutdown)

    async_register_services(hass)
    async_register_websocket_api(hass)
    await async_register_frontend(hass)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: FilamentConfigEntry) -> bool:
    """Unload a config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.store.async_save()
        if len(hass.config_entries.async_entries(entry.domain)) == 1:
            async_unregister_services(hass)
    return unloaded


async def async_remove_entry(hass: HomeAssistant, entry: FilamentConfigEntry) -> None:
    """Delete the stored inventory when the integration is removed.

    The master data in ``/config/filament_db.json`` is deliberately left
    alone: it is the user's own catalogue, not integration state.
    """
    await SpoolStore(hass).async_remove()


async def _async_update_listener(
    hass: HomeAssistant, entry: FilamentConfigEntry
) -> None:
    """Reload when the options change.

    Slot count, tray entities and printer sensors all shape the entities and
    the listeners, so a reload is both simpler and less error prone than
    patching each of them in place.
    """
    await hass.config_entries.async_reload(entry.entry_id)
