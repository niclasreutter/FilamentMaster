"""Persistence and inventory arithmetic."""

from __future__ import annotations

from homeassistant.core import HomeAssistant

from custom_components.filament_manager.models import Spool
from custom_components.filament_manager.store import SpoolStore


async def test_store_roundtrip(hass: HomeAssistant) -> None:
    """Everything survives a restart, because the store is the truth."""
    store = SpoolStore(hass)
    await store.async_load()
    spool = store.add_spool(Spool(id="a", type_id="generic_pla", remaining_weight=900))
    store.assign_slot(1, spool.id, "manual")
    store.link_tag("tag-1", "spool", spool.id)
    store.learn_rfid(spool.id, "ab12")
    await store.async_save()

    reloaded = SpoolStore(hass)
    await reloaded.async_load()
    assert reloaded.get_spool("a").remaining_weight == 900
    assert reloaded.slots[1].spool_id == "a"
    assert reloaded.spool_by_tag("tag-1").id == "a"
    assert reloaded.spool_by_rfid("AB12").id == "a"


async def test_consume_clamps_at_zero(hass: HomeAssistant) -> None:
    store = SpoolStore(hass)
    await store.async_load()
    store.add_spool(
        Spool(id="a", type_id="t", remaining_weight=100, initial_weight=1000)
    )

    applied, overdraw = store.consume("a", 40)
    assert (applied, overdraw) == (40, 0)

    applied, overdraw = store.consume("a", 100)
    assert applied == 60
    assert overdraw == 40
    assert store.get_spool("a").remaining_weight == 0
    assert store.get_spool("a").total_consumed == 100


async def test_set_remaining_only_counts_downward(hass: HomeAssistant) -> None:
    """Correcting upwards is a measurement fix, not filament coming back."""
    store = SpoolStore(hass)
    await store.async_load()
    store.add_spool(
        Spool(id="a", type_id="t", remaining_weight=500, initial_weight=1000)
    )

    store.set_remaining("a", 400)
    assert store.get_spool("a").total_consumed == 100

    store.set_remaining("a", 800)
    assert store.get_spool("a").total_consumed == 100


async def test_a_spool_sits_in_one_slot_only(hass: HomeAssistant) -> None:
    store = SpoolStore(hass)
    await store.async_load()
    store.add_spool(Spool(id="a", type_id="t"))

    store.assign_slot(1, "a")
    store.assign_slot(3, "a")

    assert store.slots[1].spool_id is None
    assert store.slots[3].spool_id == "a"
    assert store.slot_of("a") == 3


async def test_rfid_uid_moves_between_spools(hass: HomeAssistant) -> None:
    """Re-learning a UID takes it off the roll that had it."""
    store = SpoolStore(hass)
    await store.async_load()
    store.add_spool(Spool(id="a", type_id="t"))
    store.add_spool(Spool(id="b", type_id="t"))

    store.learn_rfid("a", "ff01")
    store.learn_rfid("b", "FF01")

    assert store.get_spool("a").rfid_uids == []
    assert store.spool_by_rfid("ff01").id == "b"


async def test_removing_a_spool_cleans_up_references(hass: HomeAssistant) -> None:
    store = SpoolStore(hass)
    await store.async_load()
    store.add_spool(Spool(id="a", type_id="t"))
    store.assign_slot(2, "a")
    store.link_tag("tag-a", "spool", "a")

    assert store.remove_spool("a") is True
    assert store.slots[2].spool_id is None
    assert store.resolve_tag("tag-a") is None


async def test_recent_types_are_ordered_by_use(hass: HomeAssistant) -> None:
    store = SpoolStore(hass)
    await store.async_load()
    store.add_spool(Spool(id="a", type_id="one"))
    store.add_spool(Spool(id="b", type_id="two"))
    store.add_spool(Spool(id="c", type_id="one"))
    assert store.recent_types[:2] == ["one", "two"]
