"""Config and options flow."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.filament_manager.const import (
    CONF_COLOR_TOLERANCE,
    CONF_CURRENCY,
    CONF_LOW_STOCK_THRESHOLD,
    CONF_SLOT_COUNT,
    CONF_TRAY_ENTITIES,
    DOMAIN,
)


async def test_user_flow_creates_the_entry(hass: HomeAssistant) -> None:
    """Setup asks the few things that shape the entities, nothing more."""
    registry = er.async_get(hass)
    registry.async_get_or_create(
        "sensor", "bambu_lab", "abc_ams_1_tray_2", suggested_object_id="ams_1_tray_2"
    )

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_SLOT_COUNT: 4, CONF_LOW_STOCK_THRESHOLD: 200, CONF_CURRENCY: "CHF"},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["options"][CONF_SLOT_COUNT] == 4
    assert result["options"][CONF_CURRENCY] == "CHF"
    # The discovered tray sensor is pre-filled.
    assert result["options"][CONF_TRAY_ENTITIES] == {"2": "sensor.ams_1_tray_2"}


async def test_only_one_entry(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "single_instance_allowed"


async def open_menu(hass: HomeAssistant, entry: MockConfigEntry) -> dict[str, Any]:
    """Open the options menu."""
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.MENU
    return result


async def pick(
    hass: HomeAssistant, result: dict[str, Any], step: str
) -> dict[str, Any]:
    """Pick a menu entry."""
    return await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": step}
    )


async def test_add_spool_via_options(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    coordinator = setup_integration.runtime_data
    menu = await open_menu(hass, setup_integration)
    form = await pick(hass, menu, "add_spool")
    assert form["step_id"] == "add_spool"

    result = await hass.config_entries.options.async_configure(
        form["flow_id"],
        {"type_id": "generic_petg", "count": 2, "price": 19.5, "location": "Box 1"},
    )
    assert result["type"] is FlowResultType.MENU

    spools = coordinator.store.active_spools()
    assert len(spools) == 2
    assert spools[0].location == "Box 1"
    assert spools[0].price == 19.5
    assert spools[0].initial_weight == 1000


async def test_add_spool_without_types(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    """With an empty database the form says so instead of failing."""
    setup_integration.runtime_data.db.types.clear()
    menu = await open_menu(hass, setup_integration)
    form = await pick(hass, menu, "add_spool")
    assert form["errors"] == {"base": "no_types"}


async def test_edit_and_assign_a_spool(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    coordinator = setup_integration.runtime_data
    spool = coordinator.async_add_spool("generic_pla")

    menu = await open_menu(hass, setup_integration)
    picker = await pick(hass, menu, "manage_spool")
    actions = await hass.config_entries.options.async_configure(
        picker["flow_id"], {"spool_id": spool.id}
    )
    assert actions["type"] is FlowResultType.MENU

    form = await pick(hass, actions, "edit_spool")
    await hass.config_entries.options.async_configure(
        form["flow_id"],
        {
            "remaining_weight": 640,
            "initial_weight": 1000,
            "location": "Shelf",
            "note": "opened",
        },
    )
    updated = coordinator.store.get_spool(spool.id)
    assert (updated.remaining_weight, updated.location, updated.note) == (
        640,
        "Shelf",
        "opened",
    )

    menu = await open_menu(hass, setup_integration)
    picker = await pick(hass, menu, "manage_spool")
    actions = await hass.config_entries.options.async_configure(
        picker["flow_id"], {"spool_id": spool.id}
    )
    form = await pick(hass, actions, "assign_spool")
    await hass.config_entries.options.async_configure(form["flow_id"], {"slot": "3"})
    assert coordinator.store.slots[3].spool_id == spool.id


async def test_duplicate_archive_and_delete(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    coordinator = setup_integration.runtime_data
    spool = coordinator.async_add_spool("generic_pla")

    async def action(step: str, payload: dict[str, Any] | None = None):
        menu = await open_menu(hass, setup_integration)
        picker = await pick(hass, menu, "manage_spool")
        actions = await hass.config_entries.options.async_configure(
            picker["flow_id"], {"spool_id": spool.id}
        )
        result = await pick(hass, actions, step)
        if payload is not None:
            result = await hass.config_entries.options.async_configure(
                result["flow_id"], payload
            )
        return result

    await action("duplicate_spool")
    assert len(coordinator.store.active_spools()) == 2

    await action("archive_spool")
    assert coordinator.store.get_spool(spool.id).archived is True

    await action("delete_spool", {"confirm": True})
    assert coordinator.store.get_spool(spool.id) is None


async def test_link_tags_from_the_options(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    coordinator = setup_integration.runtime_data
    spool = coordinator.async_add_spool("generic_pla")

    menu = await open_menu(hass, setup_integration)
    picker = await pick(hass, menu, "manage_spool")
    actions = await hass.config_entries.options.async_configure(
        picker["flow_id"], {"spool_id": spool.id}
    )
    form = await pick(hass, actions, "link_spool_tag")

    empty = await hass.config_entries.options.async_configure(form["flow_id"], {})
    assert empty["errors"] == {"base": "tag_required"}

    await hass.config_entries.options.async_configure(
        empty["flow_id"], {"tag_id": "sticker-1", "rfid_uid": "aa01"}
    )
    assert coordinator.store.spool_by_tag("sticker-1").id == spool.id
    assert coordinator.store.spool_by_rfid("AA01").id == spool.id


async def test_add_vendor_leads_into_add_type(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    coordinator = setup_integration.runtime_data
    menu = await open_menu(hass, setup_integration)
    form = await pick(hass, menu, "add_vendor")

    type_form = await hass.config_entries.options.async_configure(
        form["flow_id"], {"name": "Acme Filaments", "website": "https://acme.test"}
    )
    assert type_form["step_id"] == "add_type"

    result = await hass.config_entries.options.async_configure(
        type_form["flow_id"],
        {
            "vendor_id": "acme_filaments",
            "name": "Shiny",
            "material": "PETG",
            "color_name": "Signal red",
            "color": [255, 0, 0],
            "diameter": "1.75",
            "net_weight": 750,
            "spool_weight": 210,
            "spool_type": "plastic",
            "nozzle_temp_min": 230,
            "gtin": "111, 222",
            "add_spool": True,
        },
    )
    assert result["type"] is FlowResultType.MENU

    created = next(
        item for item in coordinator.db.types.values() if item.name == "Shiny"
    )
    assert created.color_hex == "FF0000"
    assert created.gtin == ["111", "222"]
    assert created.net_weight == 750
    assert created.source == "user"
    assert len(coordinator.store.active_spools()) == 1


async def test_settings_are_written_back(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    menu = await open_menu(hass, setup_integration)
    form = await pick(hass, menu, "settings")

    result = await hass.config_entries.options.async_configure(
        form["flow_id"],
        {
            CONF_SLOT_COUNT: 8,
            CONF_LOW_STOCK_THRESHOLD: 250,
            CONF_COLOR_TOLERANCE: 12,
            CONF_CURRENCY: "EUR",
            "auto_assign_rfid": True,
            "auto_assign_heuristic": False,
            "auto_consume": True,
            "split_strategy": "last_slot",
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()

    assert setup_integration.options[CONF_SLOT_COUNT] == 8
    assert setup_integration.options[CONF_COLOR_TOLERANCE] == 12
    assert setup_integration.options["auto_assign_heuristic"] is False
    assert hass.states.get("sensor.filament_manager_slot_8") is not None


async def test_printer_entities_are_stored(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    menu = await open_menu(hass, setup_integration)
    form = await pick(hass, menu, "printer")

    result = await hass.config_entries.options.async_configure(
        form["flow_id"],
        {
            "tray_1": "sensor.tray_one",
            "print_state_entity": "sensor.print_status",
            "print_weight_entity": "sensor.print_weight",
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()

    assert setup_integration.options[CONF_TRAY_ENTITIES] == {"1": "sensor.tray_one"}
    assert setup_integration.options["print_state_entity"] == "sensor.print_status"
    assert "active_tray_entity" not in setup_integration.options


async def test_tools_import_and_contribute(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    coordinator = setup_integration.runtime_data
    menu = await open_menu(hass, setup_integration)
    tools = await pick(hass, menu, "tools")
    form = await pick(hass, tools, "import_db")

    broken = await hass.config_entries.options.async_configure(
        form["flow_id"], {"document": "{nope"}
    )
    assert broken["errors"] == {"document": "invalid_json"}

    await hass.config_entries.options.async_configure(
        broken["flow_id"],
        {
            "document": (
                '{"vendors": [{"id": "acme", "name": "Acme"}], "types": '
                '[{"id": "acme_pla", "vendor_id": "acme", "name": "PLA", '
                '"material": "PLA"}]}'
            )
        },
    )
    assert coordinator.db.get_type("acme_pla") is not None

    menu = await open_menu(hass, setup_integration)
    tools = await pick(hass, menu, "tools")
    snippet = await pick(hass, tools, "contribute")
    assert "acme_pla" in snippet["description_placeholders"]["snippet"]


async def test_slot_stickers_can_be_linked(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    coordinator = setup_integration.runtime_data
    menu = await open_menu(hass, setup_integration)
    tools = await pick(hass, menu, "tools")
    form = await pick(hass, tools, "link_slot_tag")

    await hass.config_entries.options.async_configure(
        form["flow_id"], {"tag_id": "ams-slot-2", "slot": "2"}
    )
    assert coordinator.store.slot_tags() == {"ams-slot-2": 2}


async def test_done_closes_without_reloading(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    """Managing the inventory must not reload the integration."""
    before = dict(setup_integration.options)
    menu = await open_menu(hass, setup_integration)
    result = await pick(hass, menu, "done")
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert dict(setup_integration.options) == before
