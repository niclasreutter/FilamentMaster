"""Putting filament on a shopping list before it runs out.

A roll dropping below its threshold only matters if there is no full spare
of the same type on the shelf, so that is the condition for adding an item —
and the type is remembered, so restocking is what makes it eligible again
rather than every restart.
"""

from __future__ import annotations

import logging

from homeassistant.components.todo import (
    ATTR_DESCRIPTION,
    ATTR_ITEM,
    DOMAIN as TODO_DOMAIN,
)
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant, callback

from .const import CONF_SHOPPING_LIST_ENTITY
from .coordinator import FilamentCoordinator

_LOGGER = logging.getLogger(__name__)

SERVICE_ADD_ITEM = "add_item"


class ShoppingList:
    """Adds a line to a to-do list when a filament type runs low."""

    def __init__(self, hass: HomeAssistant, coordinator: FilamentCoordinator) -> None:
        """Initialise the helper."""
        self.hass = hass
        self.coordinator = coordinator
        self._unsub: callable | None = None

    @callback
    def async_setup(self) -> None:
        """Start watching the inventory."""
        if not self.entity_id:
            return
        self._unsub = self.coordinator.store.async_add_listener(self._handle_change)

    @callback
    def async_shutdown(self) -> None:
        """Stop watching."""
        if self._unsub:
            self._unsub()
            self._unsub = None

    @property
    def entity_id(self) -> str | None:
        """Return the configured to-do list, if any."""
        value = self.coordinator.options.get(CONF_SHOPPING_LIST_ENTITY)
        return str(value) if value else None

    @callback
    def _handle_change(self) -> None:
        """Re-evaluate every type after a change to the inventory."""
        listed = set(self.coordinator.store.shopping_listed)
        needed = self._types_to_buy()

        for type_id in listed - needed:
            # Restocked: eligible for the list again next time.
            self.coordinator.store.set_shopping_listed(type_id, False)

        for type_id in needed - listed:
            self.coordinator.store.set_shopping_listed(type_id, True)
            self.hass.async_create_task(self._async_add(type_id))

    @callback
    def _types_to_buy(self) -> set[str]:
        """Return the types with nothing but low rolls left."""
        by_type: dict[str, list[bool]] = {}
        for spool in self.coordinator.store.active_spools():
            low = spool.remaining_weight <= self.coordinator.threshold_for(spool)
            by_type.setdefault(spool.type_id, []).append(low)
        return {type_id for type_id, flags in by_type.items() if all(flags)}

    async def _async_add(self, type_id: str) -> None:
        """Put one type on the list."""
        entity_id = self.entity_id
        if not entity_id:
            return
        remaining = sum(
            spool.remaining_weight
            for spool in self.coordinator.store.spools_of_type(type_id)
            if not spool.archived
        )
        try:
            await self.hass.services.async_call(
                TODO_DOMAIN,
                SERVICE_ADD_ITEM,
                {
                    ATTR_ENTITY_ID: entity_id,
                    ATTR_ITEM: self.coordinator.db.describe_type(type_id),
                    ATTR_DESCRIPTION: (
                        f"Filament Manager: only {remaining:.0f} g left in stock."
                    ),
                },
                blocking=True,
            )
        except Exception:
            # A missing or broken list must not take the inventory down.
            _LOGGER.exception("Could not add %s to %s", type_id, entity_id)
            self.coordinator.store.set_shopping_listed(type_id, False)
