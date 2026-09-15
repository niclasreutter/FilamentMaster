"""Booking filament when a print finishes."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_capture_events,
)

from custom_components.filament_manager.const import (
    CONF_ACTIVE_TRAY_ENTITY,
    CONF_AUTO_CONSUME,
    CONF_CURRENCY,
    CONF_LOW_STOCK_THRESHOLD,
    CONF_PRINT_STATE_ENTITY,
    CONF_PRINT_WEIGHT_ENTITY,
    CONF_SLOT_COUNT,
    CONF_SPLIT_STRATEGY,
    DOMAIN,
    EVENT_SPOOL_CONSUMED,
    SPLIT_LAST_SLOT,
)

PRINT_STATE = "sensor.p1s_print_status"
PRINT_WEIGHT = "sensor.p1s_print_weight"
ACTIVE_TRAY = "sensor.p1s_active_tray"


@pytest.fixture
def options() -> dict[str, Any]:
    """Point the tracker at the printer sensors."""
    return {
        CONF_SLOT_COUNT: 4,
        CONF_LOW_STOCK_THRESHOLD: 150,
        CONF_CURRENCY: "EUR",
        CONF_PRINT_STATE_ENTITY: PRINT_STATE,
        CONF_PRINT_WEIGHT_ENTITY: PRINT_WEIGHT,
        CONF_ACTIVE_TRAY_ENTITY: ACTIVE_TRAY,
    }


async def run_print(
    hass: HomeAssistant,
    *,
    weight: float | None = 120,
    slot: int | None = 1,
    result: str = "finish",
) -> None:
    """Drive one print from idle to done."""
    if slot is not None:
        hass.states.async_set(ACTIVE_TRAY, str(slot))
    if weight is not None:
        hass.states.async_set(PRINT_WEIGHT, str(weight))
    hass.states.async_set(PRINT_STATE, "idle")
    await hass.async_block_till_done()
    hass.states.async_set(PRINT_STATE, "running")
    await hass.async_block_till_done()
    hass.states.async_set(PRINT_STATE, result)
    await hass.async_block_till_done()


async def test_a_finished_print_is_booked(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    coordinator = setup_integration.runtime_data
    spool = coordinator.async_add_spool("generic_pla")
    coordinator.async_assign_slot(1, spool.id)
    events = async_capture_events(hass, EVENT_SPOOL_CONSUMED)

    await run_print(hass, weight=120, slot=1)

    assert coordinator.store.get_spool(spool.id).remaining_weight == 880
    assert coordinator.store.get_spool(spool.id).total_consumed == 120
    assert events[-1].data["amount"] == 120


async def test_a_failed_print_is_not_booked(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    """The reported weight covers the whole job, not the part that printed."""
    coordinator = setup_integration.runtime_data
    spool = coordinator.async_add_spool("generic_pla")
    coordinator.async_assign_slot(1, spool.id)

    await run_print(hass, weight=120, slot=1, result="failed")

    assert coordinator.store.get_spool(spool.id).remaining_weight == 1000


async def test_nothing_is_booked_without_a_weight(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    coordinator = setup_integration.runtime_data
    spool = coordinator.async_add_spool("generic_pla")
    coordinator.async_assign_slot(1, spool.id)

    await run_print(hass, weight=None, slot=1)

    assert coordinator.store.get_spool(spool.id).remaining_weight == 1000


async def test_an_unassigned_slot_is_skipped(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    coordinator = setup_integration.runtime_data
    spool = coordinator.async_add_spool("generic_pla")

    await run_print(hass, weight=120, slot=2)

    assert coordinator.store.get_spool(spool.id).remaining_weight == 1000


async def test_auto_consume_can_be_switched_off(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    hass.config_entries.async_update_entry(
        config_entry, options={**config_entry.options, CONF_AUTO_CONSUME: False}
    )
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    coordinator = config_entry.runtime_data
    spool = coordinator.async_add_spool("generic_pla")
    coordinator.async_assign_slot(1, spool.id)

    await run_print(hass, weight=120, slot=1)

    assert coordinator.store.get_spool(spool.id).remaining_weight == 1000


async def test_overdraw_raises_a_repair_issue(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    """A deduction larger than the roll never writes a negative weight."""
    coordinator = setup_integration.runtime_data
    spool = coordinator.async_add_spool("generic_pla", remaining_weight=50)
    coordinator.async_assign_slot(1, spool.id)

    await run_print(hass, weight=120, slot=1)

    assert coordinator.store.get_spool(spool.id).remaining_weight == 0
    registry = ir.async_get(hass)
    assert registry.async_get_issue(DOMAIN, f"overdraw_{spool.id}") is not None

    coordinator.async_correct_weight(spool.id, gross_weight=1000)
    assert registry.async_get_issue(DOMAIN, f"overdraw_{spool.id}") is None


async def test_a_job_across_two_slots_is_split_by_time(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    """The AMS filament backup case: one roll runs out, the next takes over."""
    coordinator = setup_integration.runtime_data
    first = coordinator.async_add_spool("generic_pla")
    second = coordinator.async_add_spool("generic_pla")
    coordinator.async_assign_slot(1, first.id)
    coordinator.async_assign_slot(2, second.id)

    clock = iter([0, 0, 0, 30, 30, 40, 40, 40, 40])
    with patch(
        "custom_components.filament_manager.consumption.time.monotonic",
        side_effect=lambda: next(clock),
    ):
        hass.states.async_set(ACTIVE_TRAY, "1")
        hass.states.async_set(PRINT_WEIGHT, "100")
        hass.states.async_set(PRINT_STATE, "running")
        await hass.async_block_till_done()
        hass.states.async_set(ACTIVE_TRAY, "2")
        await hass.async_block_till_done()
        hass.states.async_set(PRINT_STATE, "finish")
        await hass.async_block_till_done()

    assert coordinator.store.get_spool(first.id).total_consumed == 75
    assert coordinator.store.get_spool(second.id).total_consumed == 25


async def test_last_slot_strategy_books_everything_on_one_roll(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    hass.config_entries.async_update_entry(
        config_entry,
        options={**config_entry.options, CONF_SPLIT_STRATEGY: SPLIT_LAST_SLOT},
    )
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    coordinator = config_entry.runtime_data

    tracker = coordinator.tracker
    assert tracker.split(100, {1: 30, 2: 10}) == {1: 100}


def test_proportional_split_is_the_default(setup_integration: MockConfigEntry) -> None:
    tracker = setup_integration.runtime_data.tracker
    assert tracker.split(100, {1: 30, 2: 10}) == {1: 75.0, 2: 25.0}
    assert tracker.split(100, {3: 5}) == {3: 100}


async def test_pausing_does_not_count_towards_the_split(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    coordinator = setup_integration.runtime_data
    spool = coordinator.async_add_spool("generic_pla")
    coordinator.async_assign_slot(1, spool.id)

    hass.states.async_set(ACTIVE_TRAY, "1")
    hass.states.async_set(PRINT_WEIGHT, "60")
    hass.states.async_set(PRINT_STATE, "running")
    await hass.async_block_till_done()
    hass.states.async_set(PRINT_STATE, "paused")
    await hass.async_block_till_done()
    hass.states.async_set(PRINT_STATE, "running")
    await hass.async_block_till_done()
    hass.states.async_set(PRINT_STATE, "finish")
    await hass.async_block_till_done()

    assert coordinator.store.get_spool(spool.id).total_consumed == 60


async def test_the_bambu_active_tray_sensor_is_understood(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    """End to end with the attribute shape ha-bambulab actually publishes."""
    coordinator = setup_integration.runtime_data
    spool = coordinator.async_add_spool("generic_pla")
    coordinator.async_assign_slot(2, spool.id)

    hass.states.async_set(
        ACTIVE_TRAY,
        "Bambu PLA Basic",
        {"ams_index": 0, "tray_index": 1, "type": "PLA", "remain": 80},
    )
    hass.states.async_set(PRINT_WEIGHT, "75")
    hass.states.async_set(PRINT_STATE, "running")
    await hass.async_block_till_done()
    hass.states.async_set(PRINT_STATE, "finish")
    await hass.async_block_till_done()

    assert coordinator.store.get_spool(spool.id).total_consumed == 75
