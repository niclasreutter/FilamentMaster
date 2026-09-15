"""User prompts for the cases the resolver cannot decide on its own.

Every prompt exists twice: as a persistent notification, which always works,
and as an actionable mobile notification when the user has configured a
notify service. The action ids are parsed back in :mod:`resolver`.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components import persistent_notification
from homeassistant.core import HomeAssistant

from .const import CONF_NOTIFY_SERVICE, DOMAIN

_LOGGER = logging.getLogger(__name__)

ACTION_PREFIX = "FM"
VERB_ASSIGN = "ASSIGN"
VERB_LEARN = "LEARN"
VERB_IGNORE = "IGNORE"
SEPARATOR = "|"

# Companion app notifications cap out at three actions on iOS.
MAX_ACTIONS = 3


def build_action(verb: str, *parts: str | int) -> str:
    """Return an action id understood by :func:`parse_action`."""
    return SEPARATOR.join([ACTION_PREFIX, verb, *(str(part) for part in parts)])


def parse_action(action: str) -> tuple[str, list[str]] | None:
    """Return ``(verb, parts)`` for one of our action ids."""
    if not action or not action.startswith(f"{ACTION_PREFIX}{SEPARATOR}"):
        return None
    _, verb, *parts = action.split(SEPARATOR)
    return verb, parts


class Notifier:
    """Send the integration's prompts."""

    def __init__(self, hass: HomeAssistant, options: dict[str, Any]) -> None:
        """Initialise the notifier."""
        self.hass = hass
        self._options = options

    def update_options(self, options: dict[str, Any]) -> None:
        """Adopt new entry options."""
        self._options = options

    @property
    def notify_service(self) -> str | None:
        """Return the configured notify service, if any."""
        service = self._options.get(CONF_NOTIFY_SERVICE)
        return str(service) if service else None

    async def async_prompt(
        self,
        *,
        notification_id: str,
        title: str,
        message: str,
        actions: list[tuple[str, str]] | None = None,
    ) -> None:
        """Show a prompt on every channel that is available."""
        persistent_notification.async_create(
            self.hass,
            message,
            title=title,
            notification_id=f"{DOMAIN}_{notification_id}",
        )
        service = self.notify_service
        if not service or "." not in service:
            return
        domain, service_name = service.split(".", 1)
        payload: dict[str, Any] = {
            "title": title,
            "message": message,
            "data": {"tag": f"{DOMAIN}_{notification_id}"},
        }
        if actions:
            payload["data"]["actions"] = [
                {"action": action, "title": label}
                for action, label in actions[:MAX_ACTIONS]
            ]
        try:
            await self.hass.services.async_call(
                domain, service_name, payload, blocking=False
            )
        except Exception:
            # take the resolver down; the persistent notification still stands.
            _LOGGER.exception("Could not send notification via %s", service)

    def async_dismiss(self, notification_id: str) -> None:
        """Remove a prompt once it has been answered."""
        persistent_notification.async_dismiss(self.hass, f"{DOMAIN}_{notification_id}")
