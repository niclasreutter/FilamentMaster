"""Shared fixtures."""

from __future__ import annotations

from collections.abc import Generator
import json
from typing import Any

from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.filament_manager.const import (
    CONF_CURRENCY,
    CONF_LOW_STOCK_THRESHOLD,
    CONF_SLOT_COUNT,
    DOMAIN,
)

pytest_plugins = ["pytest_homeassistant_custom_component"]


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(
    enable_custom_integrations: None,
) -> Generator[None]:
    """Make the custom integration importable in every test."""
    yield


@pytest.fixture(autouse=True)
def isolate_config_dir(hass: HomeAssistant, tmp_path) -> Generator[None]:
    """Keep /config/filament_db.json out of the shared testing config."""
    original = hass.config.config_dir
    hass.config.config_dir = str(tmp_path)
    yield
    hass.config.config_dir = original


@pytest.fixture
def options() -> dict[str, Any]:
    """Return sensible entry options."""
    return {
        CONF_SLOT_COUNT: 4,
        CONF_LOW_STOCK_THRESHOLD: 150,
        CONF_CURRENCY: "EUR",
    }


@pytest.fixture
def config_entry(hass: HomeAssistant, options: dict[str, Any]) -> MockConfigEntry:
    """Return a config entry added to hass."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Filament Manager",
        data={},
        options=options,
        entry_id="filament_manager_test",
    )
    entry.add_to_hass(hass)
    return entry


@pytest.fixture
async def setup_integration(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> MockConfigEntry:
    """Set the integration up and return the entry."""
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    return config_entry


@pytest.fixture
def coordinator(setup_integration: MockConfigEntry):
    """Return the running coordinator."""
    return setup_integration.runtime_data


@pytest.fixture
def user_db(hass: HomeAssistant, tmp_path):
    """Return a helper that writes /config/filament_db.json."""

    def _write(document: dict[str, Any]) -> None:
        (tmp_path / "filament_db.json").write_text(
            json.dumps(document), encoding="utf-8"
        )

    return _write
