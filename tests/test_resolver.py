"""Matching rolls to AMS slots."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_capture_events,
)

from custom_components.filament_manager.const import (
    CONF_CURRENCY,
    CONF_LOW_STOCK_THRESHOLD,
    CONF_SLOT_COUNT,
    CONF_TRAY_ENTITIES,
    EVENT_AMBIGUOUS_MATCH,
    EVENT_UNKNOWN_TAG,
    SOURCE_HEURISTIC,
    SOURCE_NFC,
    SOURCE_RFID,
)
from custom_components.filament_manager.resolver import (
    async_discover_tray_entities,
    canonical_material,
    read_tray,
)

TRAY_1 = "sensor.p1s_ams_1_tray_1"
TRAY_2 = "sensor.p1s_ams_1_tray_2"


@pytest.fixture
def options() -> dict[str, Any]:
    """Wire the resolver to two fake tray sensors."""
    return {
        CONF_SLOT_COUNT: 4,
        CONF_LOW_STOCK_THRESHOLD: 150,
        CONF_CURRENCY: "EUR",
        CONF_TRAY_ENTITIES: {"1": TRAY_1, "2": TRAY_2},
    }


@pytest.fixture
def user_types(user_db) -> None:
    """Three types that differ only in the ways the matcher cares about."""
    user_db(
        {
            "vendors": [{"id": "acme", "name": "Acme"}],
            "types": [
                {
                    "id": "black_pla",
                    "vendor_id": "acme",
                    "name": "PLA",
                    "material": "PLA",
                    "color_name": "Black",
                    "color_hex": "111111",
                    "spool_weight": 200,
                },
                {
                    "id": "white_pla",
                    "vendor_id": "acme",
                    "name": "PLA",
                    "material": "PLA",
                    "color_name": "White",
                    "color_hex": "FAFAFA",
                    "spool_weight": 200,
                },
                {
                    "id": "black_petg",
                    "vendor_id": "acme",
                    "name": "PETG",
                    "material": "PETG",
                    "color_name": "Black",
                    "color_hex": "111111",
                    "spool_weight": 200,
                },
            ],
        }
    )


def set_tray(hass: HomeAssistant, entity_id: str, **attributes: Any) -> None:
    """Publish a tray reading the way a printer integration would."""
    hass.states.async_set(entity_id, attributes.get("type") or "Empty", attributes)


def notifications(hass: HomeAssistant) -> dict[str, Any]:
    """Return the open persistent notifications."""
    return hass.data.get("persistent_notification", {})


# -- parsing ---------------------------------------------------------------


def test_read_tray_normalises_attribute_names() -> None:
    """The same fact goes by several names across integrations."""
    reading = read_tray(
        {
            "Spool serial number": "A1B2C3",
            "Type": "PLA",
            "Color": "#00FF00FF",
            "Remaining": 75,
        }
    )
    assert reading.uid == "A1B2C3"
    assert reading.material == "PLA"
    assert reading.color == "00FF00"
    assert reading.remaining_percent == 75
    assert reading.has_content


def test_read_tray_detects_an_empty_slot() -> None:
    assert read_tray({"empty": True, "type": "PLA"}).has_content is False
    assert read_tray({}).empty is True


def test_read_tray_ignores_a_negative_remaining() -> None:
    assert read_tray({"type": "PLA", "remaining": -1}).remaining_percent is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("pla+", "PLA"), ("PET-G", "PETG"), ("PLA-CF", "PLA-CF"), ("", None), ("?", None)],
)
def test_canonical_material(raw, expected) -> None:
    assert canonical_material(raw) == expected


async def test_discovery_finds_bambu_trays(hass: HomeAssistant) -> None:
    """Tray sensors are found by platform and name, AMS index included."""
    registry = er.async_get(hass)
    registry.async_get_or_create(
        "sensor", "bambu_lab", "serial_ams_1_tray_1", suggested_object_id="ams_1_tray_1"
    )
    registry.async_get_or_create(
        "sensor", "bambu_lab", "serial_ams_2_tray_3", suggested_object_id="ams_2_tray_3"
    )
    registry.async_get_or_create(
        "sensor", "bambu_lab", "serial_print_progress", suggested_object_id="progress"
    )
    registry.async_get_or_create("sensor", "other", "ams_1_tray_4")

    discovered = async_discover_tray_entities(hass)
    assert set(discovered) == {1, 7}


# -- RFID ------------------------------------------------------------------


async def test_known_rfid_tag_assigns_without_asking(
    hass: HomeAssistant, user_types, setup_integration: MockConfigEntry
) -> None:
    coordinator = setup_integration.runtime_data
    spool = coordinator.async_add_spool("black_pla")
    coordinator.store.learn_rfid(spool.id, "AABBCC")

    set_tray(hass, TRAY_1, tag_uid="aabbcc", type="PLA", color="111111")
    await hass.async_block_till_done()

    assert coordinator.store.slots[1].spool_id == spool.id
    assert coordinator.store.slots[1].source == SOURCE_RFID


async def test_unknown_rfid_tag_asks_once(
    hass: HomeAssistant, user_types, setup_integration: MockConfigEntry
) -> None:
    """An unknown tag never guesses; it asks and then remembers."""
    coordinator = setup_integration.runtime_data
    spool = coordinator.async_add_spool("black_pla")
    events = async_capture_events(hass, EVENT_UNKNOWN_TAG)

    set_tray(hass, TRAY_1, tag_uid="DEADBEEF", type="PLA", color="111111")
    await hass.async_block_till_done()

    assert coordinator.store.slots[1].spool_id is None
    assert len(events) == 1
    assert events[0].data["uid"] == "DEADBEEF"
    assert events[0].data["candidates"] == [spool.id]
    assert "filament_manager_unknown_tag_DEADBEEF" in notifications(hass)

    coordinator.store.learn_rfid(spool.id, "DEADBEEF")
    set_tray(hass, TRAY_1, tag_uid="DEADBEEF", type="PLA", color="111111", remaining=90)
    await hass.async_block_till_done()
    assert coordinator.store.slots[1].spool_id == spool.id


async def test_both_sides_of_a_bambu_spool_map_to_one_roll(
    hass: HomeAssistant, user_types, setup_integration: MockConfigEntry
) -> None:
    """Flipping the roll must not trigger another round of questions."""
    coordinator = setup_integration.runtime_data
    spool = coordinator.async_add_spool("black_pla")
    coordinator.store.learn_rfid(spool.id, "SIDE-A")
    coordinator.store.learn_rfid(spool.id, "SIDE-B")

    set_tray(hass, TRAY_1, tag_uid="SIDE-B", type="PLA")
    await hass.async_block_till_done()
    assert coordinator.store.slots[1].spool_id == spool.id


# -- heuristic -------------------------------------------------------------


async def test_single_heuristic_match_assigns(
    hass: HomeAssistant, user_types, setup_integration: MockConfigEntry
) -> None:
    coordinator = setup_integration.runtime_data
    black = coordinator.async_add_spool("black_pla")
    coordinator.async_add_spool("white_pla")
    coordinator.async_add_spool("black_petg")

    set_tray(hass, TRAY_2, type="PLA", color="0F0F0F")
    await hass.async_block_till_done()

    assert coordinator.store.slots[2].spool_id == black.id
    assert coordinator.store.slots[2].source == SOURCE_HEURISTIC


async def test_several_matches_ask_instead_of_guessing(
    hass: HomeAssistant, user_types, setup_integration: MockConfigEntry
) -> None:
    coordinator = setup_integration.runtime_data
    first = coordinator.async_add_spool("black_pla")
    second = coordinator.async_add_spool("black_pla")
    events = async_capture_events(hass, EVENT_AMBIGUOUS_MATCH)

    set_tray(hass, TRAY_1, type="PLA", color="111111")
    await hass.async_block_till_done()

    assert coordinator.store.slots[1].spool_id is None
    assert len(events) == 1
    assert set(events[0].data["candidates"]) == {first.id, second.id}


async def test_no_match_waits_for_nfc(
    hass: HomeAssistant, user_types, setup_integration: MockConfigEntry
) -> None:
    coordinator = setup_integration.runtime_data
    coordinator.async_add_spool("white_pla")

    set_tray(hass, TRAY_1, type="PLA", color="111111")
    await hass.async_block_till_done()

    assert coordinator.store.slots[1].spool_id is None
    assert coordinator.store.slots[1].reported_color == "111111"


async def test_material_must_match(
    hass: HomeAssistant, user_types, setup_integration: MockConfigEntry
) -> None:
    coordinator = setup_integration.runtime_data
    coordinator.async_add_spool("black_petg")

    set_tray(hass, TRAY_1, type="PLA", color="111111")
    await hass.async_block_till_done()
    assert coordinator.store.slots[1].spool_id is None


async def test_emptying_a_tray_clears_the_slot(
    hass: HomeAssistant, user_types, setup_integration: MockConfigEntry
) -> None:
    coordinator = setup_integration.runtime_data
    spool = coordinator.async_add_spool("black_pla")

    set_tray(hass, TRAY_1, type="PLA", color="111111")
    await hass.async_block_till_done()
    assert coordinator.store.slots[1].spool_id == spool.id

    set_tray(hass, TRAY_1, empty=True)
    await hass.async_block_till_done()
    assert coordinator.store.slots[1].spool_id is None


async def test_a_loaded_roll_is_not_offered_to_another_slot(
    hass: HomeAssistant, user_types, setup_integration: MockConfigEntry
) -> None:
    coordinator = setup_integration.runtime_data
    spool = coordinator.async_add_spool("black_pla")

    set_tray(hass, TRAY_1, type="PLA", color="111111")
    await hass.async_block_till_done()
    assert coordinator.store.slots[1].spool_id == spool.id

    set_tray(hass, TRAY_2, type="PLA", color="111111")
    await hass.async_block_till_done()
    assert coordinator.store.slots[2].spool_id is None


# -- NFC -------------------------------------------------------------------


async def test_two_sticker_workflow_in_both_orders(
    hass: HomeAssistant, user_types, setup_integration: MockConfigEntry
) -> None:
    """Scan roll then slot, or slot then roll — both end up assigned."""
    coordinator = setup_integration.runtime_data
    spool = coordinator.async_add_spool("black_pla")
    coordinator.store.link_tag("roll-tag", "spool", spool.id)
    coordinator.store.link_tag("slot-tag-3", "slot", 3)

    hass.bus.async_fire("tag_scanned", {"tag_id": "roll-tag"})
    await hass.async_block_till_done()
    hass.bus.async_fire("tag_scanned", {"tag_id": "slot-tag-3"})
    await hass.async_block_till_done()

    assert coordinator.store.slots[3].spool_id == spool.id
    assert coordinator.store.slots[3].source == SOURCE_NFC

    coordinator.async_assign_slot(3, None)
    hass.bus.async_fire("tag_scanned", {"tag_id": "slot-tag-3"})
    await hass.async_block_till_done()
    hass.bus.async_fire("tag_scanned", {"tag_id": "roll-tag"})
    await hass.async_block_till_done()
    assert coordinator.store.slots[3].spool_id == spool.id


async def test_unknown_sticker_explains_itself(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    hass.bus.async_fire("tag_scanned", {"tag_id": "brand-new"})
    await hass.async_block_till_done()
    assert "filament_manager_unknown_sticker_brand-new" in notifications(hass)


# -- actionable notifications ---------------------------------------------


async def test_notification_action_assigns(
    hass: HomeAssistant, user_types, setup_integration: MockConfigEntry
) -> None:
    coordinator = setup_integration.runtime_data
    spool = coordinator.async_add_spool("black_pla")

    hass.bus.async_fire(
        "mobile_app_notification_action", {"action": f"FM|ASSIGN|{spool.id}|2"}
    )
    await hass.async_block_till_done()
    assert coordinator.store.slots[2].spool_id == spool.id


async def test_notification_action_learns_a_tag(
    hass: HomeAssistant, user_types, setup_integration: MockConfigEntry
) -> None:
    coordinator = setup_integration.runtime_data
    spool = coordinator.async_add_spool("black_pla")

    hass.bus.async_fire(
        "mobile_app_notification_action", {"action": f"FM|LEARN|{spool.id}|CAFE01|1"}
    )
    await hass.async_block_till_done()

    assert coordinator.store.spool_by_rfid("CAFE01").id == spool.id
    assert coordinator.store.slots[1].spool_id == spool.id


async def test_foreign_notification_actions_are_ignored(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    hass.bus.async_fire("mobile_app_notification_action", {"action": "SOMETHING_ELSE"})
    await hass.async_block_till_done()
    assert setup_integration.runtime_data.store.slots == {}


async def test_an_unavailable_tray_sensor_keeps_the_assignment(
    hass: HomeAssistant, user_types, setup_integration: MockConfigEntry
) -> None:
    """A restarting printer integration must not wipe the inventory."""
    coordinator = setup_integration.runtime_data
    spool = coordinator.async_add_spool("black_pla")

    set_tray(hass, TRAY_1, type="PLA", color="111111")
    await hass.async_block_till_done()
    assert coordinator.store.slots[1].spool_id == spool.id

    hass.states.async_set(TRAY_1, "unavailable", {})
    await hass.async_block_till_done()
    assert coordinator.store.slots[1].spool_id == spool.id


async def test_an_all_zero_tag_uid_falls_through_to_the_heuristic(
    hass: HomeAssistant, user_types, setup_integration: MockConfigEntry
) -> None:
    """Bambu reports 0000000000000000 for filament that has no tag at all.

    Treating that as an unknown tag would ask about every third-party roll
    and never run the colour match.
    """
    coordinator = setup_integration.runtime_data
    spool = coordinator.async_add_spool("black_pla")
    unknown = async_capture_events(hass, EVENT_UNKNOWN_TAG)

    hass.states.async_set(
        TRAY_1,
        "PLA",
        {
            "tag_uid": "0000000000000000",
            "tray_uuid": "00000000000000000000000000000000",
            "type": "PLA",
            "color": "#111111FF",
            "remain": -1,
            "empty": False,
        },
    )
    await hass.async_block_till_done()

    assert unknown == []
    assert coordinator.store.slots[1].spool_id == spool.id
    assert coordinator.store.slots[1].source == SOURCE_HEURISTIC


async def test_tag_uids_match_whatever_separators_they_arrive_with(
    hass: HomeAssistant, user_types, setup_integration: MockConfigEntry
) -> None:
    coordinator = setup_integration.runtime_data
    spool = coordinator.async_add_spool("black_pla")
    coordinator.store.learn_rfid(spool.id, "04:a1:b2:c3")

    assert coordinator.store.get_spool(spool.id).rfid_uids == ["04A1B2C3"]
    assert coordinator.store.spool_by_rfid("04-A1-B2-C3").id == spool.id

    set_tray(hass, TRAY_2, tag_uid="04A1B2C3", type="PLA")
    await hass.async_block_till_done()
    assert coordinator.store.slots[2].spool_id == spool.id


async def test_an_all_zero_uid_is_never_learned(
    hass: HomeAssistant, user_types, setup_integration: MockConfigEntry
) -> None:
    coordinator = setup_integration.runtime_data
    spool = coordinator.async_add_spool("black_pla")

    assert coordinator.store.learn_rfid(spool.id, "0000000000000000") is None
    assert coordinator.store.get_spool(spool.id).rfid_uids == []
