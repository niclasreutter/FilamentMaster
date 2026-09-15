"""What happens when something is wrong."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.filament_manager.consumption import _parse_slot
from custom_components.filament_manager.services import async_get_coordinator
from custom_components.filament_manager.store import FilamentManagerStore


async def test_downgrading_the_store_is_refused(hass: HomeAssistant) -> None:
    """A store from a newer version must not be silently reinterpreted."""
    store = FilamentManagerStore(hass, 1, "filament_manager.test")
    with pytest.raises(ValueError, match="Cannot downgrade"):
        await store._async_migrate_func(2, 1, {})


async def test_migrating_from_the_current_version_is_a_no_op(
    hass: HomeAssistant,
) -> None:
    store = FilamentManagerStore(hass, 1, "filament_manager.test")
    payload = {"spools": []}
    assert await store._async_migrate_func(1, 1, payload) == payload


async def test_unknown_ids_are_reported(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    coordinator = setup_integration.runtime_data

    with pytest.raises(HomeAssistantError, match="Unknown filament type"):
        coordinator.async_add_spool("does_not_exist")
    with pytest.raises(HomeAssistantError, match="Unknown spool"):
        coordinator.async_consume("nope", 10)
    with pytest.raises(HomeAssistantError, match="Unknown spool"):
        coordinator.async_duplicate_spool("nope")
    with pytest.raises(HomeAssistantError, match="Unknown spool"):
        coordinator.async_delete_spool("nope")
    with pytest.raises(HomeAssistantError, match="Unknown spool"):
        coordinator.async_archive_spool("nope")


async def test_slots_outside_the_configured_range_are_refused(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    coordinator = setup_integration.runtime_data
    spool = coordinator.async_add_spool("generic_pla")

    with pytest.raises(HomeAssistantError, match="outside the configured range"):
        coordinator.async_assign_slot(9, spool.id)
    with pytest.raises(HomeAssistantError, match="outside the configured range"):
        coordinator.async_assign_slot(0, spool.id)


async def test_an_archived_roll_cannot_be_loaded(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    coordinator = setup_integration.runtime_data
    spool = coordinator.async_add_spool("generic_pla")
    coordinator.async_archive_spool(spool.id)

    with pytest.raises(HomeAssistantError, match="archived"):
        coordinator.async_assign_slot(1, spool.id)


async def test_negative_consumption_is_refused(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    coordinator = setup_integration.runtime_data
    spool = coordinator.async_add_spool("generic_pla")
    with pytest.raises(HomeAssistantError, match="negative"):
        coordinator.async_consume(spool.id, -5)


async def test_correcting_needs_a_weight(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    coordinator = setup_integration.runtime_data
    spool = coordinator.async_add_spool("generic_pla")
    with pytest.raises(HomeAssistantError, match="gross_weight or remaining_weight"):
        coordinator.async_correct_weight(spool.id)


async def test_a_type_without_a_spool_weight_uses_the_gross_value(
    hass: HomeAssistant, setup_integration: MockConfigEntry, caplog
) -> None:
    """Better a logged warning than a silently wrong subtraction."""
    coordinator = setup_integration.runtime_data
    filament_type = coordinator.db.get_type("generic_pla")
    filament_type.spool_weight = 0
    spool = coordinator.async_add_spool("generic_pla")

    coordinator.async_correct_weight(spool.id, gross_weight=640)
    assert coordinator.store.get_spool(spool.id).remaining_weight == 640
    assert "No empty spool weight is known" in caplog.text


async def test_services_need_a_loaded_entry(hass: HomeAssistant) -> None:
    with pytest.raises(HomeAssistantError, match="not set up"):
        async_get_coordinator(hass)


def test_active_tray_parsing() -> None:
    """The Bambu active-tray sensor names the filament, not the slot."""
    # ha-bambulab: the state is the filament's name, the position sits in
    # ams_index/tray_index, both counting from zero.
    assert _parse_slot("Bambu PLA Basic", {"ams_index": 0, "tray_index": 1}) == 2
    assert _parse_slot("Generic PETG", {"ams_index": 1, "tray_index": 0}) == 5

    # A tray sensor carries its own slot number, already counting from one.
    assert _parse_slot("PLA", {"slot": 3}) == 3

    # The raw MQTT field counts from zero.
    assert _parse_slot("", {"tray_now": "0"}) == 1
    assert _parse_slot("", {"tray_now": "3"}) == 4

    # A plain number is a slot; a name that happens to contain one is not.
    assert _parse_slot("2", {}) == 2
    assert _parse_slot("PLA Basic 2", {}) is None
    assert _parse_slot("none", {}) is None
    assert _parse_slot("", {}) is None

    # 254 is the external spool, 255 means nothing is loaded.
    assert _parse_slot("x", {"tray_index": 254}) is None
    assert _parse_slot("x", {"ams_index": 255, "tray_index": 255}) is None
    assert _parse_slot("", {"tray_now": "254"}) is None
    assert _parse_slot("255", {}) is None
