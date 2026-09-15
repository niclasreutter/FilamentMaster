"""Putting filament on a shopping list before it runs out."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant, ServiceCall
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.filament_manager.const import (
    CONF_CURRENCY,
    CONF_LOW_STOCK_THRESHOLD,
    CONF_SHOPPING_LIST_ENTITY,
    CONF_SLOT_COUNT,
)

TODO_ENTITY = "todo.shopping"


@pytest.fixture
def options() -> dict[str, Any]:
    """Point the integration at a to-do list."""
    return {
        CONF_SLOT_COUNT: 4,
        CONF_LOW_STOCK_THRESHOLD: 150,
        CONF_CURRENCY: "EUR",
        CONF_SHOPPING_LIST_ENTITY: TODO_ENTITY,
    }


@pytest.fixture
def added(hass: HomeAssistant) -> list[dict[str, Any]]:
    """Capture todo.add_item calls."""
    calls: list[dict[str, Any]] = []

    async def record(call: ServiceCall) -> None:
        calls.append(dict(call.data))

    hass.services.async_register("todo", "add_item", record)
    return calls


async def test_a_type_lands_on_the_list_when_the_last_roll_runs_low(
    hass: HomeAssistant, added, setup_integration: MockConfigEntry
) -> None:
    coordinator = setup_integration.runtime_data
    spool = coordinator.async_add_spool("generic_petg")

    coordinator.async_consume(spool.id, 900)
    await hass.async_block_till_done()

    assert len(added) == 1
    assert added[0]["item"] == "Generic PETG"
    assert "100 g left" in added[0]["description"]


async def test_a_full_spare_keeps_it_off_the_list(
    hass: HomeAssistant, added, setup_integration: MockConfigEntry
) -> None:
    """You do not need to buy more while an unopened roll is on the shelf."""
    coordinator = setup_integration.runtime_data
    opened = coordinator.async_add_spool("generic_pla")
    spare = coordinator.async_add_spool("generic_pla")

    coordinator.async_consume(opened.id, 900)
    await hass.async_block_till_done()
    assert added == []

    coordinator.async_consume(spare.id, 900)
    await hass.async_block_till_done()
    assert len(added) == 1


async def test_a_type_is_listed_once_until_it_is_restocked(
    hass: HomeAssistant, added, setup_integration: MockConfigEntry
) -> None:
    coordinator = setup_integration.runtime_data
    spool = coordinator.async_add_spool("generic_pla")

    coordinator.async_consume(spool.id, 900)
    await hass.async_block_till_done()
    coordinator.async_consume(spool.id, 10)
    await hass.async_block_till_done()
    assert len(added) == 1

    # Restocking makes the type eligible again.
    fresh = coordinator.async_add_spool("generic_pla")
    await hass.async_block_till_done()
    assert coordinator.store.shopping_listed == []

    coordinator.async_consume(fresh.id, 900)
    await hass.async_block_till_done()
    assert len(added) == 2


async def test_archiving_the_last_roll_does_not_add_anything(
    hass: HomeAssistant, added, setup_integration: MockConfigEntry
) -> None:
    coordinator = setup_integration.runtime_data
    spool = coordinator.async_add_spool("generic_pla")
    coordinator.async_archive_spool(spool.id)
    await hass.async_block_till_done()
    assert added == []


async def test_nothing_happens_without_a_configured_list(
    hass: HomeAssistant, added, config_entry: MockConfigEntry
) -> None:
    hass.config_entries.async_update_entry(
        config_entry,
        options={
            key: value
            for key, value in config_entry.options.items()
            if key != CONF_SHOPPING_LIST_ENTITY
        },
    )
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    coordinator = config_entry.runtime_data
    spool = coordinator.async_add_spool("generic_pla")
    coordinator.async_consume(spool.id, 900)
    await hass.async_block_till_done()
    assert added == []


async def test_a_broken_list_does_not_break_the_inventory(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    async def explode(call: ServiceCall) -> None:
        raise RuntimeError("no such list")

    hass.services.async_register("todo", "add_item", explode)
    coordinator = setup_integration.runtime_data
    spool = coordinator.async_add_spool("generic_pla")

    coordinator.async_consume(spool.id, 900)
    await hass.async_block_till_done()

    assert coordinator.store.get_spool(spool.id).remaining_weight == 100
    # The failure is rolled back, so a later attempt can still succeed.
    assert coordinator.store.shopping_listed == []
