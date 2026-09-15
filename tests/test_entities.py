"""Entities derived from the store."""

from __future__ import annotations

from homeassistant.components.sensor import SensorStateClass
from homeassistant.const import ATTR_DEVICE_CLASS, ATTR_UNIT_OF_MEASUREMENT
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.filament_manager.const import DOMAIN


def spool_entity(hass: HomeAssistant, spool_id: str, key: str) -> str:
    """Return the entity id created for one roll."""
    registry = er.async_get(hass)
    for domain in ("sensor", "binary_sensor", "number", "button"):
        entity_id = registry.async_get_entity_id(domain, DOMAIN, f"{spool_id}_{key}")
        if entity_id:
            return entity_id
    raise AssertionError(f"no entity for {spool_id}/{key}")


async def test_spool_entities_appear_and_disappear(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    """Rolls come and go while HA runs, and the entities follow."""
    coordinator = setup_integration.runtime_data
    spool = coordinator.async_add_spool("generic_pla", price=25.0)
    await hass.async_block_till_done()

    remaining = spool_entity(hass, spool.id, "remaining_weight")
    assert hass.states.get(remaining).state == "1000.0"

    coordinator.async_archive_spool(spool.id)
    await hass.async_block_till_done()
    assert hass.states.get(remaining) is None

    coordinator.async_archive_spool(spool.id, False)
    await hass.async_block_till_done()
    assert hass.states.get(spool_entity(hass, spool.id, "remaining_weight")) is not None


async def test_sensor_metadata(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    coordinator = setup_integration.runtime_data
    spool = coordinator.async_add_spool("bambulab_pla_basic", price=24.0)
    await hass.async_block_till_done()

    remaining = hass.states.get(spool_entity(hass, spool.id, "remaining_weight"))
    assert remaining.attributes[ATTR_UNIT_OF_MEASUREMENT] == "g"
    assert remaining.attributes[ATTR_DEVICE_CLASS] == "weight"
    assert remaining.attributes["vendor"] == "Bambu Lab"
    assert remaining.attributes["material"] == "PLA"
    assert remaining.attributes["spool_weight"] == 212

    consumed = hass.states.get(spool_entity(hass, spool.id, "total_consumed"))
    # total_increasing is what makes the long term statistics work.
    assert consumed.attributes["state_class"] == SensorStateClass.TOTAL_INCREASING

    length = hass.states.get(spool_entity(hass, spool.id, "remaining_length"))
    assert float(length.state) > 300


async def test_low_stock_binary_sensor(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    coordinator = setup_integration.runtime_data
    spool = coordinator.async_add_spool("generic_pla")
    await hass.async_block_till_done()
    entity_id = spool_entity(hass, spool.id, "low_stock")
    assert hass.states.get(entity_id).state == "off"

    coordinator.async_consume(spool.id, 900)
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == "on"
    assert hass.states.get(entity_id).attributes["threshold"] == 150


async def test_loaded_binary_sensor_follows_the_slot(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    coordinator = setup_integration.runtime_data
    spool = coordinator.async_add_spool("generic_pla")
    await hass.async_block_till_done()
    entity_id = spool_entity(hass, spool.id, "loaded")
    assert hass.states.get(entity_id).state == "off"

    coordinator.async_assign_slot(2, spool.id)
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == "on"
    assert hass.states.get(entity_id).attributes["slot"] == 2


async def test_gross_weight_number_does_the_subtraction(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    """Type in what the kitchen scale says and the spool weight comes off."""
    coordinator = setup_integration.runtime_data
    spool = coordinator.async_add_spool("bambulab_pla_basic")
    await hass.async_block_till_done()
    entity_id = spool_entity(hass, spool.id, "gross_weight")
    assert hass.states.get(entity_id).state == "1212.0"

    await hass.services.async_call(
        "number",
        "set_value",
        {"entity_id": entity_id, "value": 712},
        blocking=True,
    )
    assert coordinator.store.get_spool(spool.id).remaining_weight == 500


async def test_buttons_duplicate_and_archive(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    coordinator = setup_integration.runtime_data
    spool = coordinator.async_add_spool("generic_pla")
    await hass.async_block_till_done()

    await hass.services.async_call(
        "button",
        "press",
        {"entity_id": spool_entity(hass, spool.id, "duplicate")},
        blocking=True,
    )
    await hass.async_block_till_done()
    assert len(coordinator.store.active_spools()) == 2

    await hass.services.async_call(
        "button",
        "press",
        {"entity_id": spool_entity(hass, spool.id, "archive")},
        blocking=True,
    )
    await hass.async_block_till_done()
    assert coordinator.store.get_spool(spool.id).archived is True


async def test_slot_sensor_reports_source_and_content(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    coordinator = setup_integration.runtime_data
    spool = coordinator.async_add_spool("generic_petg")
    assert hass.states.get("sensor.filament_manager_slot_1").state == "empty"

    coordinator.async_assign_slot(1, spool.id, "manual")
    await hass.async_block_till_done()

    state = hass.states.get("sensor.filament_manager_slot_1")
    assert state.state == "Generic PETG"
    assert state.attributes["spool_id"] == spool.id
    assert state.attributes["assignment_source"] == "manual"
    assert state.attributes["material"] == "PETG"


async def test_inventory_sensors(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    coordinator = setup_integration.runtime_data
    coordinator.async_add_spool("generic_pla", price=20.0)
    second = coordinator.async_add_spool("generic_petg", price=30.0)
    coordinator.async_consume(second.id, 500)
    await hass.async_block_till_done()

    assert hass.states.get("sensor.filament_manager_spools_in_stock").state == "2"
    assert (
        hass.states.get("sensor.filament_manager_filament_in_stock").state == "1500.0"
    )
    # The PETG is half used, so half its price is left.
    assert hass.states.get("sensor.filament_manager_inventory_value").state == "35.0"
    assert (
        hass.states.get("sensor.filament_manager_inventory_value").attributes[
            ATTR_UNIT_OF_MEASUREMENT
        ]
        == "EUR"
    )


async def test_devices_are_cleaned_up_with_the_spool(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    coordinator = setup_integration.runtime_data
    spool = coordinator.async_add_spool("generic_pla")
    await hass.async_block_till_done()

    device_registry = dr.async_get(hass)
    identifier = coordinator.spool_device_identifier(spool.id)
    assert device_registry.async_get_device(identifiers={identifier}) is not None

    coordinator.async_delete_spool(spool.id)
    await hass.async_block_till_done()
    device = device_registry.async_get_device(identifiers={identifier})
    assert device is None or setup_integration.entry_id not in device.config_entries


async def test_entities_survive_a_restart(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    """The store is the truth, so a reload rebuilds everything from it."""
    coordinator = setup_integration.runtime_data
    spool = coordinator.async_add_spool("generic_pla")
    coordinator.async_consume(spool.id, 250)
    coordinator.async_assign_slot(1, spool.id)
    await hass.async_block_till_done()

    await hass.config_entries.async_reload(setup_integration.entry_id)
    await hass.async_block_till_done()

    assert (
        hass.states.get(spool_entity(hass, spool.id, "remaining_weight")).state
        == "750.0"
    )
    assert (
        hass.states.get("sensor.filament_manager_slot_1").attributes["spool_id"]
        == spool.id
    )


async def test_a_refill_uses_its_own_empty_spool_weight(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    """A refill has no spool; it goes on one the user already owned."""
    coordinator = setup_integration.runtime_data
    # The bundled refill type declares a spool weight of zero.
    spool = coordinator.async_add_spool("bambulab_pla_basic_refill")
    await hass.async_block_till_done()
    entity_id = spool_entity(hass, spool.id, "gross_weight")
    assert hass.states.get(entity_id).state == "1000.0"

    # The user's own reusable spool weighs 190 g.
    coordinator.store.update_spool(spool.id, spool_weight=190)
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == "1190.0"

    await hass.services.async_call(
        "number",
        "set_value",
        {"entity_id": entity_id, "value": 690},
        blocking=True,
    )
    assert coordinator.store.get_spool(spool.id).remaining_weight == 500


async def test_the_per_roll_weight_wins_over_the_type(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    coordinator = setup_integration.runtime_data
    spool = coordinator.async_add_spool("bambulab_pla_basic")
    assert coordinator.spool_weight_for(spool) == 212

    coordinator.store.update_spool(spool.id, spool_weight=150)
    assert coordinator.spool_weight_for(spool) == 150

    coordinator.store.update_spool(spool.id, spool_weight=None)
    assert coordinator.spool_weight_for(spool) == 212
