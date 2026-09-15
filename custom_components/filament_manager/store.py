"""Persistence layer.

The store is the truth, not the entities: everything the user owns lives in
``.storage`` (and therefore in every HA backup) and the entities are derived
from it. There is deliberately no ``RestoreEntity`` in this integration.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
import logging
from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.storage import Store

from .const import (
    DOMAIN,
    SOURCE_MANUAL,
    STORAGE_KEY,
    STORAGE_MINOR_VERSION,
    STORAGE_VERSION,
    TAG_KIND_SLOT,
    TAG_KIND_SPOOL,
)
from .models import SlotState, Spool, new_id, normalize_uid, utcnow_iso

_LOGGER = logging.getLogger(__name__)

SAVE_DELAY = 2

# Number of recently used types remembered for the "sorted by last used"
# picker in the options flow.
RECENT_TYPES_LIMIT = 25


class FilamentManagerStore(Store[dict[str, Any]]):
    """Versioned store with a migration hook wired up from day one."""

    async def _async_migrate_func(
        self,
        old_major_version: int,
        old_minor_version: int,
        old_data: dict[str, Any],
    ) -> dict[str, Any]:
        """Migrate stored data to the current schema.

        Version 1 is the only schema so far; the branch exists so that a
        future change has an obvious place to land instead of silently
        dropping a user's inventory.
        """
        if old_major_version > STORAGE_VERSION:
            raise ValueError(
                f"Cannot downgrade {DOMAIN} storage from version "
                f"{old_major_version} to {STORAGE_VERSION}"
            )
        return old_data


class SpoolStore:
    """In-memory view of the inventory, backed by :class:`Store`."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialise the store."""
        self.hass = hass
        self._store = FilamentManagerStore(
            hass,
            STORAGE_VERSION,
            STORAGE_KEY,
            minor_version=STORAGE_MINOR_VERSION,
        )
        self.spools: dict[str, Spool] = {}
        self.slots: dict[int, SlotState] = {}
        self.tags: dict[str, dict[str, Any]] = {}
        self.recent_types: list[str] = []
        self.shopping_listed: list[str] = []
        self._listeners: list[Callable[[], None]] = []

    # -- lifecycle ---------------------------------------------------------

    async def async_load(self) -> None:
        """Load the inventory from disk."""
        data = await self._store.async_load() or {}
        self.spools = {
            spool["id"]: Spool.from_dict(spool)
            for spool in data.get("spools", [])
            if spool.get("id")
        }
        self.slots = {
            int(slot["slot"]): SlotState.from_dict(slot)
            for slot in data.get("slots", [])
            if slot.get("slot") is not None
        }
        self.tags = {
            str(tag_id): dict(payload)
            for tag_id, payload in (data.get("tags") or {}).items()
        }
        self.recent_types = list(data.get("recent_types") or [])
        self.shopping_listed = list(data.get("shopping_listed") or [])
        _LOGGER.debug(
            "Loaded %s spools, %s slot assignments, %s tags",
            len(self.spools),
            len(self.slots),
            len(self.tags),
        )

    @callback
    def async_add_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        """Register a callback fired after every mutation."""
        self._listeners.append(listener)

        def _remove() -> None:
            self._listeners.remove(listener)

        return _remove

    @callback
    def _write(self) -> None:
        """Persist (debounced) and notify listeners."""
        self._store.async_delay_save(self._as_dict, SAVE_DELAY)
        for listener in list(self._listeners):
            listener()

    @callback
    def _as_dict(self) -> dict[str, Any]:
        """Return the serialisable snapshot handed to :class:`Store`."""
        return {
            "spools": [spool.to_dict() for spool in self.spools.values()],
            "slots": [slot.to_dict() for slot in self.slots.values()],
            "tags": self.tags,
            "recent_types": self.recent_types,
            "shopping_listed": self.shopping_listed,
        }

    async def async_save(self) -> None:
        """Write pending changes immediately."""
        await self._store.async_save(self._as_dict())

    async def async_remove(self) -> None:
        """Delete the store — used when the entry is removed."""
        await self._store.async_remove()

    # -- spools ------------------------------------------------------------

    @callback
    def active_spools(self) -> list[Spool]:
        """Return every spool that is not archived."""
        return [spool for spool in self.spools.values() if not spool.archived]

    @callback
    def get_spool(self, spool_id: str) -> Spool | None:
        """Return one spool by id."""
        return self.spools.get(spool_id)

    @callback
    def add_spool(self, spool: Spool) -> Spool:
        """Add a spool to the inventory."""
        if not spool.id:
            spool.id = new_id()
        spool.created = spool.created or utcnow_iso()
        self.spools[spool.id] = spool
        self.touch_type(spool.type_id)
        self._write()
        return spool

    @callback
    def update_spool(self, spool_id: str, **changes: Any) -> Spool | None:
        """Apply field updates to a spool."""
        spool = self.spools.get(spool_id)
        if spool is None:
            return None
        for key, value in changes.items():
            if hasattr(spool, key):
                setattr(spool, key, value)
        spool.__post_init__()
        self._write()
        return spool

    @callback
    def remove_spool(self, spool_id: str) -> bool:
        """Delete a spool and every reference to it."""
        if spool_id not in self.spools:
            return False
        del self.spools[spool_id]
        for slot in self.slots.values():
            if slot.spool_id == spool_id:
                slot.spool_id = None
                slot.source = None
        for tag_id in [
            tag_id
            for tag_id, payload in self.tags.items()
            if payload.get("kind") == TAG_KIND_SPOOL
            and payload.get("target") == spool_id
        ]:
            del self.tags[tag_id]
        self._write()
        return True

    @callback
    def set_archived(self, spool_id: str, archived: bool) -> Spool | None:
        """Archive or restore a spool."""
        spool = self.spools.get(spool_id)
        if spool is None:
            return None
        spool.archived = archived
        if archived:
            self.clear_spool_from_slots(spool_id)
        self._write()
        return spool

    @callback
    def clear_spool_from_slots(self, spool_id: str) -> None:
        """Remove a spool from every slot it is assigned to."""
        for slot in self.slots.values():
            if slot.spool_id == spool_id:
                slot.spool_id = None
                slot.source = None
                slot.since = utcnow_iso()

    @callback
    def consume(self, spool_id: str, grams: float) -> tuple[float, float]:
        """Subtract ``grams`` from a spool.

        Returns the amount actually subtracted and the overdraw, so the
        caller can raise a repair issue instead of writing a negative
        remaining weight.
        """
        spool = self.spools[spool_id]
        applied = min(max(grams, 0.0), spool.remaining_weight)
        overdraw = max(0.0, grams - applied)
        spool.remaining_weight = round(spool.remaining_weight - applied, 2)
        spool.total_consumed = round(spool.total_consumed + applied, 2)
        now = utcnow_iso()
        spool.first_used = spool.first_used or now
        spool.last_used = now
        self.touch_type(spool.type_id)
        self._write()
        return applied, overdraw

    @callback
    def set_remaining(self, spool_id: str, grams: float) -> Spool | None:
        """Set the remaining weight after re-weighing a roll."""
        spool = self.spools.get(spool_id)
        if spool is None:
            return None
        grams = max(0.0, round(float(grams), 2))
        # A correction upwards is a measurement fix, not filament coming
        # back, so the lifetime counter only follows downward corrections.
        if grams < spool.remaining_weight:
            spool.total_consumed = round(
                spool.total_consumed + (spool.remaining_weight - grams), 2
            )
        spool.remaining_weight = grams
        self._write()
        return spool

    # -- slots -------------------------------------------------------------

    @callback
    def get_slot(self, slot: int) -> SlotState:
        """Return the state of one slot, creating it on first use."""
        if slot not in self.slots:
            self.slots[slot] = SlotState(slot=slot)
        return self.slots[slot]

    @callback
    def assign_slot(
        self,
        slot: int,
        spool_id: str | None,
        source: str = SOURCE_MANUAL,
        **reported: Any,
    ) -> SlotState:
        """Put a spool into a slot (or empty the slot with ``None``)."""
        state = self.get_slot(slot)
        if spool_id is not None:
            # A roll can only sit in one slot at a time.
            for other in self.slots.values():
                if other.slot != slot and other.spool_id == spool_id:
                    other.spool_id = None
                    other.source = None
                    other.since = utcnow_iso()
        changed = state.spool_id != spool_id
        state.spool_id = spool_id
        state.source = source if spool_id else None
        if changed:
            state.since = utcnow_iso()
        for key, value in reported.items():
            if hasattr(state, key):
                setattr(state, key, value)
        if spool_id and (spool := self.spools.get(spool_id)):
            spool.assigned_slot = slot
            spool.assignment_source = source
            spool.assignment_time = state.since
            self.touch_type(spool.type_id)
        for spool in self.spools.values():
            if spool.assigned_slot == slot and spool.id != spool_id:
                spool.assigned_slot = None
                spool.assignment_source = None
        self._write()
        return state

    @callback
    def slot_of(self, spool_id: str) -> int | None:
        """Return the slot a spool sits in, if any."""
        for slot in self.slots.values():
            if slot.spool_id == spool_id:
                return slot.slot
        return None

    # -- tags --------------------------------------------------------------

    @callback
    def link_tag(self, tag_id: str, kind: str, target: str | int) -> None:
        """Map an HA tag to a spool or to an AMS slot."""
        self.tags[str(tag_id)] = {"kind": kind, "target": target}
        if kind == TAG_KIND_SPOOL and (spool := self.spools.get(str(target))):
            spool.nfc_tag_id = str(tag_id)
        self._write()

    @callback
    def unlink_tag(self, tag_id: str) -> bool:
        """Forget a tag mapping."""
        payload = self.tags.pop(str(tag_id), None)
        if payload is None:
            return False
        if payload.get("kind") == TAG_KIND_SPOOL and (
            spool := self.spools.get(str(payload.get("target")))
        ):
            spool.nfc_tag_id = None
        self._write()
        return True

    @callback
    def resolve_tag(self, tag_id: str) -> dict[str, Any] | None:
        """Return the mapping for a scanned tag."""
        return self.tags.get(str(tag_id))

    @callback
    def slot_tags(self) -> dict[str, int]:
        """Return every tag that identifies a slot."""
        return {
            tag_id: int(payload["target"])
            for tag_id, payload in self.tags.items()
            if payload.get("kind") == TAG_KIND_SLOT
        }

    @callback
    def learn_rfid(self, spool_id: str, uid: str) -> Spool | None:
        """Teach a Bambu RFID UID to a spool.

        Bambu rolls carry a tag on each side with different UIDs; both end up
        on the same spool so that flipping the roll does not trigger another
        round of questions.
        """
        spool = self.spools.get(spool_id)
        if spool is None:
            return None
        normalized = normalize_uid(uid)
        if normalized is None:
            return None
        uid = normalized
        for other in self.spools.values():
            if other.id != spool_id and uid in other.rfid_uids:
                other.rfid_uids.remove(uid)
        if uid not in spool.rfid_uids:
            spool.rfid_uids.append(uid)
        self._write()
        return spool

    @callback
    def spool_by_rfid(self, uid: str) -> Spool | None:
        """Return the spool a Bambu tag UID belongs to."""
        normalized = normalize_uid(uid)
        if normalized is None:
            return None
        uid = normalized
        for spool in self.spools.values():
            if uid in spool.rfid_uids:
                return spool
        return None

    @callback
    def spool_by_tag(self, tag_id: str) -> Spool | None:
        """Return the spool an NFC sticker belongs to."""
        payload = self.resolve_tag(tag_id)
        if payload and payload.get("kind") == TAG_KIND_SPOOL:
            return self.spools.get(str(payload.get("target")))
        return None

    # -- misc --------------------------------------------------------------

    @callback
    def touch_type(self, type_id: str) -> None:
        """Remember a type as recently used, for the pickers."""
        if not type_id:
            return
        if type_id in self.recent_types:
            self.recent_types.remove(type_id)
        self.recent_types.insert(0, type_id)
        del self.recent_types[RECENT_TYPES_LIMIT:]

    @callback
    def set_shopping_listed(self, type_id: str, listed: bool) -> None:
        """Remember whether a type is already on the shopping list.

        Written without notifying listeners: this is bookkeeping about the
        inventory, not a change to it, and re-entering the listener that set
        it would loop.
        """
        if listed and type_id not in self.shopping_listed:
            self.shopping_listed.append(type_id)
        elif not listed and type_id in self.shopping_listed:
            self.shopping_listed.remove(type_id)
        else:
            return
        self._store.async_delay_save(self._as_dict, SAVE_DELAY)

    @callback
    def spools_of_type(self, type_id: str) -> Iterable[Spool]:
        """Return every spool of a given type."""
        return [spool for spool in self.spools.values() if spool.type_id == type_id]
