"""Setup and teardown of the config entry."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.filament_manager.const import DOMAIN


async def test_setup_and_unload(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    """The entry sets up, creates its services and unloads again."""
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    assert config_entry.state is ConfigEntryState.LOADED
    assert hass.services.has_service(DOMAIN, "add_spool")
    assert hass.states.get("sensor.filament_manager_slot_1") is not None
    assert hass.states.get("sensor.filament_manager_filament_in_stock") is not None

    assert await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.NOT_LOADED


async def test_master_data_is_loaded(coordinator) -> None:
    """The bundled filament database ships with usable content."""
    assert coordinator.db.types
    assert coordinator.db.vendors
    bundled = coordinator.db.get_type("bambulab_pla_basic")
    assert bundled is not None
    assert bundled.material == "PLA"
    assert bundled.spool_weight > 0


async def test_slot_sensors_follow_the_slot_count(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    """Changing the slot count reloads and re-creates the slot sensors."""
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get("sensor.filament_manager_slot_4") is not None

    hass.config_entries.async_update_entry(
        config_entry, options={**config_entry.options, "slot_count": 6}
    )
    await hass.async_block_till_done()
    assert hass.states.get("sensor.filament_manager_slot_6") is not None
