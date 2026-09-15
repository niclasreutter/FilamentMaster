"""WebSocket API used by the card."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.typing import WebSocketGenerator

from custom_components.filament_manager.models import FilamentType


@pytest.fixture
async def client(
    hass: HomeAssistant,
    hass_ws_client: WebSocketGenerator,
    setup_integration: MockConfigEntry,
):
    """Return an authenticated WebSocket client."""
    return await hass_ws_client(hass)


async def send(client, type_: str, **payload: Any) -> dict[str, Any]:
    """Send a command and return the parsed result."""
    await client.send_json_auto_id({"type": type_, **payload})
    response = await client.receive_json()
    assert response["success"], response
    return response["result"]


async def test_list_returns_everything_the_card_needs(client, coordinator) -> None:
    coordinator.async_add_spool("generic_pla", price=20.0)
    result = await send(client, "filament_manager/list")

    assert len(result["spools"]) == 1
    assert len(result["slots"]) == 4
    assert result["stats"]["spool_count"] == 1
    assert result["stats"]["total_value"] == 20.0
    assert any(item["id"] == "generic_pla" for item in result["types"])
    assert result["spools"][0]["remaining_length"] > 300


async def test_scan_tells_known_from_unknown(client, coordinator, hass) -> None:
    """A barcode identifies the product, so a hit lists types, not rolls."""
    await coordinator.db.async_add_type(
        FilamentType(
            id="acme_pla",
            vendor_id="generic",
            name="PLA",
            material="PLA",
            gtin=["4260639533720"],
        )
    )
    coordinator.async_add_spool("acme_pla")

    hit = await send(client, "filament_manager/scan", code="4260639533720")
    assert hit["known"] is True
    assert [item["id"] for item in hit["types"]] == ["acme_pla"]
    assert len(hit["spools"]) == 1

    miss = await send(client, "filament_manager/scan", code="0000000000000")
    assert miss["known"] is False
    assert miss["types"] == []


async def test_subscribe_pushes_updates(client, coordinator) -> None:
    await client.send_json_auto_id({"type": "filament_manager/subscribe"})
    first = await client.receive_json()
    assert first["success"]
    assert first["result"]["stats"]["spool_count"] == 0

    coordinator.async_add_spool("generic_pla")
    event = await client.receive_json()
    assert event["type"] == "event"
    assert event["event"]["stats"]["spool_count"] == 1


async def test_add_spool_assign_and_consume(client, coordinator) -> None:
    added = await send(
        client, "filament_manager/add_spool", type_id="generic_pla", count=2
    )
    spool_id = added["spool_ids"][0]

    await send(client, "filament_manager/assign_slot", slot=2, spool_id=spool_id)
    assert coordinator.store.slots[2].spool_id == spool_id

    await send(client, "filament_manager/consume", spool_id=spool_id, amount=125)
    assert coordinator.store.get_spool(spool_id).remaining_weight == 875

    await send(
        client,
        "filament_manager/correct_weight",
        spool_id=spool_id,
        remaining_weight=500,
    )
    assert coordinator.store.get_spool(spool_id).remaining_weight == 500

    await send(client, "filament_manager/archive", spool_id=spool_id)
    assert coordinator.store.get_spool(spool_id).archived is True


async def test_add_type_creates_a_spool_too(client, coordinator) -> None:
    result = await send(
        client,
        "filament_manager/add_type",
        filament_type={
            "vendor_id": "generic",
            "name": "Scanned product",
            "material": "PETG",
            "color_hex": "FF8800",
            "gtin": ["12345"],
        },
        add_spool=True,
    )
    assert result["spool_id"] is not None
    created = coordinator.db.get_type(result["type_id"])
    assert created.material == "PETG"
    assert created.source == "user"


async def test_errors_come_back_as_websocket_errors(client) -> None:
    await client.send_json_auto_id(
        {"type": "filament_manager/assign_slot", "slot": 99, "spool_id": None}
    )
    response = await client.receive_json()
    assert response["success"] is False


async def test_link_tag_needs_a_target(client) -> None:
    await client.send_json_auto_id({"type": "filament_manager/link_tag", "tag_id": "x"})
    response = await client.receive_json()
    assert response["success"] is False
