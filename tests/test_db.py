"""Master data: merging, lookups and the contribution export."""

from __future__ import annotations

import json

from homeassistant.core import HomeAssistant

from custom_components.filament_manager.db import FilamentDatabase
from custom_components.filament_manager.models import FilamentType, Vendor


async def test_user_layer_overrides_the_bundle(
    hass: HomeAssistant, user_db, tmp_path
) -> None:
    """The user's own file wins over what HACS shipped."""
    user_db(
        {
            "spec_version": 1,
            "vendors": [{"id": "bambulab", "name": "Bambu Lab (mine)"}],
            "types": [
                {
                    "id": "bambulab_pla_basic",
                    "vendor_id": "bambulab",
                    "name": "PLA Basic",
                    "material": "PLA",
                    "spool_weight": 215,
                }
            ],
        }
    )
    database = FilamentDatabase(hass)
    await database.async_load()

    assert database.vendors["bambulab"].name == "Bambu Lab (mine)"
    assert database.get_type("bambulab_pla_basic").spool_weight == 215
    assert database.get_type("bambulab_pla_basic").source == "user"
    # The rest of the bundle is still there.
    assert database.get_type("generic_petg").source == "bundled"


async def test_gtin_lookup_handles_upc_and_ean(hass: HomeAssistant, user_db) -> None:
    user_db(
        {
            "vendors": [{"id": "v", "name": "V"}],
            "types": [
                {
                    "id": "t",
                    "vendor_id": "v",
                    "name": "T",
                    "material": "PLA",
                    "gtin": ["0123456789012"],
                }
            ],
        }
    )
    database = FilamentDatabase(hass)
    await database.async_load()

    assert [item.id for item in database.find_by_gtin("0123456789012")] == ["t"]
    # The same product as a 12 digit UPC-A.
    assert [item.id for item in database.find_by_gtin("123456789012")] == ["t"]
    assert database.find_by_gtin("999") == []


async def test_adding_a_type_writes_the_user_file(
    hass: HomeAssistant, tmp_path
) -> None:
    database = FilamentDatabase(hass)
    await database.async_load()

    await database.async_add_vendor(Vendor(id="", name="Acme Filaments"))
    created = await database.async_add_type(
        FilamentType(
            id="",
            vendor_id="acme_filaments",
            name="Shiny PLA",
            material="PLA",
            color_name="Red",
        )
    )

    assert created.id == "acme_filaments_shiny_pla_red"
    assert created.source == "user"

    document = json.loads((tmp_path / "filament_db.json").read_text())
    assert document["spec_version"] == 1
    assert {entry["id"] for entry in document["types"]} == {created.id}
    assert "source" not in document["types"][0]


async def test_contribution_snippet_only_holds_user_entries(
    hass: HomeAssistant,
) -> None:
    database = FilamentDatabase(hass)
    await database.async_load()
    await database.async_add_vendor(Vendor(id="acme", name="Acme"))
    await database.async_add_type(
        FilamentType(id="acme_pla", vendor_id="acme", name="PLA", material="PLA")
    )

    snippet = json.loads(database.contribution_snippet())
    assert [item["id"] for item in snippet["types"]] == ["acme_pla"]
    assert [item["id"] for item in snippet["vendors"]] == ["acme"]


async def test_import_merges_and_respects_overwrite(hass: HomeAssistant) -> None:
    database = FilamentDatabase(hass)
    await database.async_load()

    document = {
        "vendors": [{"id": "acme", "name": "Acme"}],
        "types": [
            {"id": "acme_pla", "vendor_id": "acme", "name": "PLA", "material": "PLA"}
        ],
    }
    assert await database.async_import(document) == (1, 1)
    # A second import without overwrite changes nothing.
    assert await database.async_import(document) == (0, 0)

    document["types"][0]["name"] = "PLA Pro"
    assert await database.async_import(document, overwrite=True) == (1, 1)
    assert database.get_type("acme_pla").name == "PLA Pro"


async def test_sorted_types_puts_recent_first(hass: HomeAssistant) -> None:
    database = FilamentDatabase(hass)
    await database.async_load()
    ordered = database.sorted_types(["generic_petg", "generic_abs"])
    assert [item.id for item in ordered[:2]] == ["generic_petg", "generic_abs"]


async def test_gtin_lookup_terminates_on_a_zero_code(
    hass: HomeAssistant, user_db
) -> None:
    """A code of nothing but zeroes must not send the lookup in circles."""
    user_db({"vendors": [], "types": []})
    database = FilamentDatabase(hass)
    await database.async_load()
    assert database.find_by_gtin("0000000000000") == []
    assert database.find_by_gtin("000000000000") == []
