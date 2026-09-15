"""Serving the card and prompting the user."""

from __future__ import annotations

from typing import Any

from homeassistant.components.frontend import DATA_EXTRA_MODULE_URL
from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.filament_manager.const import (
    CONF_CURRENCY,
    CONF_NOTIFY_SERVICE,
    CONF_SLOT_COUNT,
    FRONTEND_URL_BASE,
)
from custom_components.filament_manager.notifications import (
    Notifier,
    build_action,
    parse_action,
)


async def test_the_card_is_served_and_loaded(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    """No second HACS repository and no dashboard resource to add by hand."""
    urls = hass.data[DATA_EXTRA_MODULE_URL].urls
    assert any(
        url.startswith(f"{FRONTEND_URL_BASE}/filament-manager-card.js") for url in urls
    )


async def test_the_card_is_reachable_over_http(
    hass: HomeAssistant, setup_integration: MockConfigEntry, hass_client
) -> None:
    client = await hass_client()
    response = await client.get(f"{FRONTEND_URL_BASE}/filament-manager-card.js")
    assert response.status == 200
    assert "filament-manager-card" in await response.text()


@pytest.mark.parametrize(
    ("action", "expected"),
    [
        ("FM|ASSIGN|abc|2", ("ASSIGN", ["abc", "2"])),
        ("FM|LEARN|abc|CAFE|1", ("LEARN", ["abc", "CAFE", "1"])),
        ("something-else", None),
        ("", None),
    ],
)
def test_action_ids_round_trip(action: str, expected: Any) -> None:
    assert parse_action(action) == expected


def test_build_action() -> None:
    assert build_action("ASSIGN", "abc", 2) == "FM|ASSIGN|abc|2"


async def test_prompts_reach_the_notify_service(hass: HomeAssistant) -> None:
    """A prompt always lands as a notification, and on the phone when set up."""
    calls: list[dict[str, Any]] = []

    async def record(call) -> None:
        calls.append(dict(call.data))

    hass.services.async_register("notify", "mobile_app_test", record)
    notifier = Notifier(hass, {CONF_NOTIFY_SERVICE: "notify.mobile_app_test"})

    await notifier.async_prompt(
        notification_id="demo",
        title="Which roll?",
        message="Pick one",
        actions=[("FM|ASSIGN|a|1", "Slot 1"), ("FM|ASSIGN|a|2", "Slot 2")],
    )
    await hass.async_block_till_done()

    assert "filament_manager_demo" in hass.data["persistent_notification"]
    assert len(calls) == 1
    assert [action["title"] for action in calls[0]["data"]["actions"]] == [
        "Slot 1",
        "Slot 2",
    ]

    notifier.async_dismiss("demo")
    assert "filament_manager_demo" not in hass.data["persistent_notification"]


async def test_a_broken_notify_service_does_not_break_the_resolver(
    hass: HomeAssistant,
) -> None:
    async def explode(call) -> None:
        raise RuntimeError("no phone here")

    hass.services.async_register("notify", "broken", explode)
    notifier = Notifier(hass, {CONF_NOTIFY_SERVICE: "notify.broken"})

    await notifier.async_prompt(
        notification_id="demo", title="t", message="m", actions=None
    )
    await hass.async_block_till_done()
    assert "filament_manager_demo" in hass.data["persistent_notification"]


async def test_only_three_actions_reach_the_phone(hass: HomeAssistant) -> None:
    """iOS shows at most three buttons on a notification."""
    calls: list[dict[str, Any]] = []

    async def record(call) -> None:
        calls.append(dict(call.data))

    hass.services.async_register("notify", "phone", record)
    notifier = Notifier(hass, {CONF_NOTIFY_SERVICE: "notify.phone"})
    await notifier.async_prompt(
        notification_id="demo",
        title="t",
        message="m",
        actions=[(f"FM|ASSIGN|a|{slot}", f"Slot {slot}") for slot in range(1, 6)],
    )
    await hass.async_block_till_done()
    assert len(calls[0]["data"]["actions"]) == 3


async def test_options_without_a_notify_service_are_fine(hass: HomeAssistant) -> None:
    notifier = Notifier(hass, {CONF_SLOT_COUNT: 4, CONF_CURRENCY: "EUR"})
    assert notifier.notify_service is None
    await notifier.async_prompt(notification_id="demo", title="t", message="m")
    assert "filament_manager_demo" in hass.data["persistent_notification"]
