"""The scripting interface."""

from __future__ import annotations

import json
from pathlib import Path

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
import yaml

from custom_components.filament_manager import services as service_module
from custom_components.filament_manager.const import DOMAIN


async def call(hass: HomeAssistant, service: str, **data):
    """Call one of our services and return its response."""
    return await hass.services.async_call(
        DOMAIN, service, data, blocking=True, return_response=True
    )


async def call_void(hass: HomeAssistant, service: str, **data) -> None:
    """Call a service that returns nothing."""
    await hass.services.async_call(DOMAIN, service, data, blocking=True)


async def test_every_documented_service_is_registered(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    """services.yaml and the registration list must not drift apart."""
    path = Path(service_module.__file__).parent / "services.yaml"
    documented = set(yaml.safe_load(path.read_text(encoding="utf-8")))
    registered = set(hass.services.async_services_for_domain(DOMAIN))
    assert documented == registered


async def test_add_and_duplicate_spool(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    coordinator = setup_integration.runtime_data
    response = await call(hass, "add_spool", type_id="generic_pla", count=2, price=20.0)
    assert len(response["spool_ids"]) == 2

    spool_id = response["spool_ids"][0]
    duplicate = await call(hass, "duplicate_spool", spool_id=spool_id)
    assert duplicate["spool_id"] != spool_id
    assert len(coordinator.store.active_spools()) == 3
    assert coordinator.store.get_spool(duplicate["spool_id"]).price == 20.0


async def test_consume_by_weight_and_by_length(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    coordinator = setup_integration.runtime_data
    spool = coordinator.async_add_spool("generic_pla")

    await call(hass, "consume", spool_id=spool.id, amount=100)
    assert coordinator.store.get_spool(spool.id).remaining_weight == 900

    # 335 m of 1.75 mm PLA is roughly a kilo, so 33.5 m is roughly 100 g.
    await call(hass, "consume", spool_id=spool.id, length=33.5)
    assert 795 < coordinator.store.get_spool(spool.id).remaining_weight < 805


async def test_consume_via_slot(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    coordinator = setup_integration.runtime_data
    spool = coordinator.async_add_spool("generic_pla")
    coordinator.async_assign_slot(2, spool.id)

    await call(hass, "consume", slot=2, amount=50)
    assert coordinator.store.get_spool(spool.id).remaining_weight == 950

    with pytest.raises(ServiceValidationError):
        await call(hass, "consume", slot=3, amount=50)


async def test_correct_weight_subtracts_the_empty_spool(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    """Gross weight in, remaining filament out."""
    coordinator = setup_integration.runtime_data
    spool = coordinator.async_add_spool("bambulab_pla_basic")

    response = await call(hass, "correct_weight", spool_id=spool.id, gross_weight=812)
    # The Bambu spool itself weighs 212 g.
    assert response["remaining_weight"] == 600


async def test_assign_and_clear_slot(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    coordinator = setup_integration.runtime_data
    spool = coordinator.async_add_spool("generic_pla")

    await call_void(hass, "assign_slot", slot=1, spool_id=spool.id)
    assert coordinator.store.slots[1].spool_id == spool.id

    await call_void(hass, "clear_slot", slot=1)
    assert coordinator.store.slots[1].spool_id is None


async def test_archive_restore_and_delete(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    coordinator = setup_integration.runtime_data
    spool = coordinator.async_add_spool("generic_pla")

    await call_void(hass, "archive_spool", spool_id=spool.id)
    assert coordinator.store.get_spool(spool.id).archived is True

    await call_void(hass, "restore_spool", spool_id=spool.id)
    assert coordinator.store.get_spool(spool.id).archived is False

    await call_void(hass, "delete_spool", spool_id=spool.id)
    assert coordinator.store.get_spool(spool.id) is None


async def test_add_vendor_and_type(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    coordinator = setup_integration.runtime_data
    vendor = await call(hass, "add_vendor", name="Acme Filaments")
    assert vendor["vendor_id"] == "acme_filaments"

    created = await call(
        hass,
        "add_type",
        vendor_id=vendor["vendor_id"],
        name="Shiny",
        material="petg",
        color_hex="#00ff00",
        spool_weight=210,
        gtin=["1234567890123"],
    )
    filament_type = coordinator.db.get_type(created["type_id"])
    assert filament_type.material == "PETG"
    assert filament_type.color_hex == "00FF00"
    assert coordinator.db.find_by_gtin("1234567890123") == [filament_type]


async def test_tags_and_rfid(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    coordinator = setup_integration.runtime_data
    spool = coordinator.async_add_spool("generic_pla")

    await call_void(hass, "link_tag", tag_id="abc", spool_id=spool.id)
    assert coordinator.store.spool_by_tag("abc").id == spool.id

    await call_void(hass, "learn_rfid", spool_id=spool.id, rfid_uid="ff10", slot=1)
    assert coordinator.store.spool_by_rfid("FF10").id == spool.id
    assert coordinator.store.slots[1].spool_id == spool.id

    await call_void(hass, "unlink_tag", tag_id="abc")
    assert coordinator.store.spool_by_tag("abc") is None

    with pytest.raises(ServiceValidationError):
        await call_void(hass, "unlink_tag", tag_id="abc")


async def test_link_tag_rejects_an_unknown_spool(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    with pytest.raises(ServiceValidationError):
        await call_void(hass, "link_tag", tag_id="abc", spool_id="nope")


async def test_import_export_and_contribution(
    hass: HomeAssistant, setup_integration: MockConfigEntry, tmp_path
) -> None:
    document = {
        "vendors": [{"id": "acme", "name": "Acme"}],
        "types": [
            {"id": "acme_pla", "vendor_id": "acme", "name": "PLA", "material": "PLA"}
        ],
    }
    result = await call(hass, "import_db", data=document)
    assert result == {"vendors": 1, "types": 1}

    exported = await call(hass, "export_db", only_user=True, filename="export.json")
    assert [item["id"] for item in exported["database"]["types"]] == ["acme_pla"]
    assert json.loads((tmp_path / "export.json").read_text())["types"]

    snippet = await call(hass, "export_contribution")
    assert "acme_pla" in snippet["snippet"]


async def test_import_rejects_a_path_outside_the_allowlist(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    with pytest.raises(ServiceValidationError):
        await call(hass, "import_db", filename="/etc/passwd")


async def test_reload_db_picks_up_edits(
    hass: HomeAssistant, setup_integration: MockConfigEntry, user_db
) -> None:
    coordinator = setup_integration.runtime_data
    assert coordinator.db.get_type("hand_written") is None

    user_db(
        {
            "vendors": [{"id": "acme", "name": "Acme"}],
            "types": [
                {
                    "id": "hand_written",
                    "vendor_id": "acme",
                    "name": "Edited by hand",
                    "material": "PLA",
                }
            ],
        }
    )
    await call_void(hass, "reload_db")
    assert coordinator.db.get_type("hand_written") is not None


async def test_services_go_away_with_the_last_entry(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    assert hass.services.has_service(DOMAIN, "add_spool")
    await hass.config_entries.async_unload(setup_integration.entry_id)
    await hass.async_block_till_done()
    assert not hass.services.has_service(DOMAIN, "add_spool")


async def test_relative_paths_cannot_escape_the_config_directory(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    with pytest.raises(ServiceValidationError):
        await call(hass, "export_db", filename="../../etc/filament.json")
