"""Subtracting filament when a print finishes.

The printer reports the weight of the job it just ran; this module decides
which roll that weight comes off. When the AMS switched slots mid-print —
the filament backup case, where a roll runs empty and the AMS quietly moves
on — the job is split over the slots by the time each one was active.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
import logging
import time
from typing import Any

from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.helpers.event import async_track_state_change_event

from .const import (
    CONF_ACTIVE_TRAY_ENTITY,
    CONF_AUTO_CONSUME,
    CONF_PRINT_STATE_ENTITY,
    CONF_PRINT_WEIGHT_ENTITY,
    CONF_SPLIT_STRATEGY,
    DEFAULT_SPLIT_STRATEGY,
    EXTERNAL_SPOOL_INDEX,
    SPLIT_LAST_SLOT,
    TRAYS_PER_AMS,
)
from .coordinator import FilamentCoordinator

_LOGGER = logging.getLogger(__name__)

RUNNING_STATES = {"running", "printing", "print", "busy", "prepare", "preparing"}
PAUSED_STATES = {"pause", "paused"}
FINISHED_STATES = {"finish", "finished", "complete", "completed", "success"}
FAILED_STATES = {"failed", "fail", "error", "cancelled", "canceled", "aborted"}


@dataclass(slots=True)
class PrintJob:
    """Bookkeeping for one print while it runs."""

    started: float
    slot_seconds: dict[int, float] = field(default_factory=dict)
    current_slot: int | None = None
    last_switch: float = 0.0
    weight: float | None = None

    def switch_to(self, slot: int | None, now: float) -> None:
        """Record that the printer moved on to another slot."""
        if self.current_slot is not None:
            elapsed = max(0.0, now - self.last_switch)
            self.slot_seconds[self.current_slot] = (
                self.slot_seconds.get(self.current_slot, 0.0) + elapsed
            )
        self.current_slot = slot
        self.last_switch = now

    def finish(self, now: float) -> dict[int, float]:
        """Close the books and return the seconds spent per slot."""
        self.switch_to(None, now)
        return {
            slot: seconds for slot, seconds in self.slot_seconds.items() if seconds > 0
        }


class ConsumptionTracker:
    """Watches the printer and books the filament it used."""

    def __init__(self, hass: HomeAssistant, coordinator: FilamentCoordinator) -> None:
        """Initialise the tracker."""
        self.hass = hass
        self.coordinator = coordinator
        self._unsub: Callable[[], None] | None = None
        self._job: PrintJob | None = None
        self._paused_at: float | None = None

    # -- lifecycle ---------------------------------------------------------

    @callback
    def async_setup(self) -> None:
        """Subscribe to the configured printer entities."""
        self.async_refresh()

    @callback
    def async_refresh(self) -> None:
        """(Re-)subscribe after an options change."""
        if self._unsub:
            self._unsub()
            self._unsub = None
        entities = [
            entity
            for entity in (
                self._option(CONF_PRINT_STATE_ENTITY),
                self._option(CONF_PRINT_WEIGHT_ENTITY),
                self._option(CONF_ACTIVE_TRAY_ENTITY),
            )
            if entity
        ]
        if not entities:
            _LOGGER.debug(
                "No printer entities configured, automatic consumption is off"
            )
            return
        self._unsub = async_track_state_change_event(
            self.hass, entities, self._handle_change
        )

    @callback
    def async_shutdown(self) -> None:
        """Stop listening."""
        if self._unsub:
            self._unsub()
            self._unsub = None

    @callback
    def _option(self, key: str) -> str | None:
        value = self.coordinator.options.get(key)
        return str(value) if value else None

    # -- event handling ----------------------------------------------------

    @callback
    def _handle_change(self, event: Event[EventStateChangedData]) -> None:
        """Dispatch a state change to the right handler."""
        entity_id = event.data["entity_id"]
        new_state = event.data["new_state"]
        if new_state is None or new_state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return
        if entity_id == self._option(CONF_PRINT_WEIGHT_ENTITY):
            self._handle_weight(new_state.state)
        elif entity_id == self._option(CONF_ACTIVE_TRAY_ENTITY):
            self._handle_active_tray(new_state.state, dict(new_state.attributes))
        elif entity_id == self._option(CONF_PRINT_STATE_ENTITY):
            old_state = event.data["old_state"]
            self._handle_print_state(
                old_state.state if old_state else None, new_state.state
            )

    @callback
    def _handle_weight(self, raw: str) -> None:
        """Remember the job weight reported by the printer."""
        try:
            weight = float(raw)
        except (TypeError, ValueError):
            return
        if weight <= 0:
            return
        if self._job is not None:
            self._job.weight = weight

    @callback
    def _handle_active_tray(self, raw: str, attributes: dict[str, Any]) -> None:
        """Follow the AMS as it switches slots."""
        slot = _parse_slot(raw, attributes)
        if self._job is None or slot is None:
            return
        if slot != self._job.current_slot:
            self._job.switch_to(slot, time.monotonic())

    @callback
    def _handle_print_state(self, old: str | None, new: str) -> None:
        """Start, pause, resume or close a print job."""
        new_key = new.strip().lower()
        old_key = (old or "").strip().lower()
        if new_key in RUNNING_STATES and self._job is None:
            self._start_job()
        elif new_key in RUNNING_STATES and self._paused_at is not None:
            self._resume_job()
        elif new_key in PAUSED_STATES and self._job is not None:
            self._pause_job()
        elif new_key in FINISHED_STATES and self._job is not None:
            self._finish_job()
        elif new_key in FAILED_STATES and self._job is not None:
            _LOGGER.info(
                "Print ended as '%s' — no filament booked, because the reported "
                "weight covers the whole job. Correct manually if needed",
                new,
            )
            self._job = None
            self._paused_at = None
        elif old_key in RUNNING_STATES and new_key not in (
            RUNNING_STATES | PAUSED_STATES | FINISHED_STATES | FAILED_STATES
        ):
            # Printer went idle without reporting a result.
            self._job = None
            self._paused_at = None

    @callback
    def _start_job(self) -> None:
        """Begin bookkeeping for a new print."""
        now = time.monotonic()
        self._job = PrintJob(started=now, last_switch=now)
        self._paused_at = None
        slot = self._current_slot()
        if slot is not None:
            self._job.switch_to(slot, now)
        if weight := self._current_weight():
            self._job.weight = weight
        _LOGGER.debug("Print started, tracking slot %s", slot)

    @callback
    def _pause_job(self) -> None:
        """Stop counting time while the printer is paused."""
        if self._job is None or self._paused_at is not None:
            return
        self._paused_at = time.monotonic()
        self._job.switch_to(None, self._paused_at)

    @callback
    def _resume_job(self) -> None:
        """Resume counting after a pause."""
        if self._job is None:
            return
        self._paused_at = None
        self._job.switch_to(self._current_slot(), time.monotonic())

    @callback
    def _finish_job(self) -> None:
        """Book the finished print onto the loaded rolls."""
        job = self._job
        self._job = None
        self._paused_at = None
        if job is None:
            return
        shares = job.finish(time.monotonic())
        weight = job.weight or self._current_weight()
        if not weight:
            _LOGGER.info(
                "Print finished but no job weight was reported — nothing booked"
            )
            return
        if not self.coordinator.options.get(CONF_AUTO_CONSUME, True):
            _LOGGER.debug("Automatic consumption is disabled, skipping %.1f g", weight)
            return
        for slot, grams in self.split(weight, shares).items():
            self.async_consume_slot(slot, grams)

    @callback
    def split(self, weight: float, shares: dict[int, float]) -> dict[int, float]:
        """Distribute a job weight over the slots it was printed from."""
        if not shares:
            slot = self._current_slot()
            return {slot: weight} if slot is not None else {}
        if len(shares) == 1:
            return {next(iter(shares)): weight}
        strategy = self.coordinator.options.get(
            CONF_SPLIT_STRATEGY, DEFAULT_SPLIT_STRATEGY
        )
        if strategy == SPLIT_LAST_SLOT:
            last_slot = max(shares, key=lambda slot: shares[slot])
            return {last_slot: weight}
        total = sum(shares.values())
        return {
            slot: round(weight * seconds / total, 2) for slot, seconds in shares.items()
        }

    @callback
    def async_consume_slot(self, slot: int, grams: float) -> None:
        """Subtract ``grams`` from whatever sits in ``slot``."""
        state = self.coordinator.store.slots.get(slot)
        if state is None or state.spool_id is None:
            _LOGGER.info(
                "Slot %s used %.1f g but no roll is assigned — not booked", slot, grams
            )
            return
        self.coordinator.async_consume(state.spool_id, grams, source="print")

    # -- helpers -----------------------------------------------------------

    @callback
    def _current_weight(self) -> float | None:
        """Return the currently reported job weight."""
        entity_id = self._option(CONF_PRINT_WEIGHT_ENTITY)
        if not entity_id or (state := self.hass.states.get(entity_id)) is None:
            return None
        try:
            weight = float(state.state)
        except (TypeError, ValueError):
            return None
        return weight if weight > 0 else None

    @callback
    def _current_slot(self) -> int | None:
        """Return the slot the printer is currently pulling from."""
        entity_id = self._option(CONF_ACTIVE_TRAY_ENTITY)
        if entity_id and (state := self.hass.states.get(entity_id)) is not None:
            slot = _parse_slot(state.state, dict(state.attributes))
            if slot is not None:
                return slot
        # Without an active tray sensor, fall back to the only occupied slot.
        occupied = [
            slot.slot
            for slot in self.coordinator.store.slots.values()
            if slot.spool_id is not None
        ]
        return occupied[0] if len(occupied) == 1 else None


def _as_index(value: Any) -> int | None:
    """Return ``value`` as a non-negative integer, or ``None``."""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def _parse_slot(raw: str, attributes: dict[str, Any]) -> int | None:
    """Return the slot number an active-tray sensor is pointing at.

    The Bambu integration's active-tray sensor reports the *filament's name*
    as its state and puts the position in ``ams_index``/``tray_index``, both
    counting from zero. Reading digits out of that state would happily turn
    "PLA Basic 2" into slot 2, so a non-numeric state yields nothing.
    """
    tray = _as_index(attributes.get("tray_index"))
    if tray is not None:
        ams = _as_index(attributes.get("ams_index")) or 0
        if tray >= EXTERNAL_SPOOL_INDEX or ams >= EXTERNAL_SPOOL_INDEX:
            # The external spool, or nothing loaded at all.
            return None
        return ams * TRAYS_PER_AMS + tray + 1

    # A tray sensor carries its own slot number, already counting from one.
    slot = _as_index(attributes.get("slot"))
    if slot is not None:
        return slot if 1 <= slot < EXTERNAL_SPOOL_INDEX else None

    # ``tray_now`` is the raw MQTT field and counts from zero.
    for key in ("tray_now", "ams_tray_now"):
        value = _as_index(attributes.get(key))
        if value is not None:
            return None if value >= EXTERNAL_SPOOL_INDEX else value + 1

    text = str(raw).strip()
    if text.isdigit():
        value = int(text)
        return value if 1 <= value < EXTERNAL_SPOOL_INDEX else None
    return None
