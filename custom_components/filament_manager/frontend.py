"""Serving the Lovelace card from inside the integration.

The card ships in this repository and is registered from Python, so there is
no second HACS repository to install and nothing to add under Settings →
Dashboards → Resources.
"""

from __future__ import annotations

import logging
from pathlib import Path

from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant
from homeassistant.loader import async_get_integration

from .const import DOMAIN, FRONTEND_CARD_FILENAME, FRONTEND_URL_BASE

_LOGGER = logging.getLogger(__name__)

DATA_FRONTEND_REGISTERED = f"{DOMAIN}_frontend_registered"


async def async_register_frontend(hass: HomeAssistant) -> None:
    """Serve the card and load it into the dashboard."""
    if hass.data.get(DATA_FRONTEND_REGISTERED):
        return
    hass.data[DATA_FRONTEND_REGISTERED] = True

    www_path = Path(__file__).parent / "www"
    if not await hass.async_add_executor_job(www_path.is_dir):
        _LOGGER.warning("Card directory %s is missing, skipping registration", www_path)
        return

    await hass.http.async_register_static_paths(
        [
            StaticPathConfig(
                FRONTEND_URL_BASE,
                str(www_path),
                # Cache-busting happens via the version query string below;
                # letting the browser cache the file forever would strand
                # users on an old card after a HACS update.
                cache_headers=False,
            )
        ]
    )

    integration = await async_get_integration(hass, DOMAIN)
    version = integration.version or "dev"
    add_extra_js_url(hass, f"{FRONTEND_URL_BASE}/{FRONTEND_CARD_FILENAME}?v={version}")
    _LOGGER.debug("Registered the Filament Manager card (version %s)", version)
