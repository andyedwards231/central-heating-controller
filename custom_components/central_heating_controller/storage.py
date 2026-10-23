"""Validated persistence for the central heating controller."""

from __future__ import annotations

from datetime import datetime, timezone
import logging
import math
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import STORAGE_KEY, STORAGE_VERSION
from .models import JsonPrimitive, PersistentState

_LOGGER = logging.getLogger(__name__)


def _finite_float(value: object) -> float | None:
    """Return a finite float while rejecting booleans."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        converted = float(value)
    except OverflowError, TypeError, ValueError:
        return None
    return converted if math.isfinite(converted) else None


def _utc_datetime(value: object) -> datetime | None:
    """Parse an aware ISO timestamp and normalize it to UTC."""
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            return None
        return parsed.astimezone(timezone.utc)
    except OverflowError, TypeError, ValueError:
        return None


def _json_primitive(value: object) -> bool:
    """Return whether a value is a finite JSON primitive."""
    return (
        value is None
        or isinstance(value, (str, bool, int))
        or (isinstance(value, float) and math.isfinite(value))
    )


def _fingerprint(value: object) -> tuple[JsonPrimitive, ...] | None:
    """Validate a flat persisted policy fingerprint."""
    if not isinstance(value, (list, tuple)) or not all(_json_primitive(item) for item in value):
        return None
    return tuple(value)


def _json_mapping(value: object) -> dict:
    """Return a JSON-safe mapping with bounded nesting, or an empty mapping if malformed."""
    if not isinstance(value, dict):
        return {}

    def clean(item: object, depth: int = 0) -> object:
        if depth > 8:
            return None
        if item is None or isinstance(item, (str, bool, int)):
            return item
        if isinstance(item, float):
            return item if math.isfinite(item) else None
        if isinstance(item, dict):
            return {key: clean(val, depth + 1) for key, val in item.items() if isinstance(key, str)}
        if isinstance(item, (list, tuple)):
            return [clean(val, depth + 1) for val in item]
        return None

    return clean(value)


class ControllerStore:
    """Persist independently validated controller state fields."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, STORAGE_KEY.format(entry_id=entry_id)
        )

    async def async_load(self) -> PersistentState:
        """Load state, retaining every independently valid field."""
        try:
            raw = await self._store.async_load()
        except Exception:
            _LOGGER.warning("Unable to load controller storage; using safe defaults")
            return PersistentState()
        if raw is None:
            return PersistentState()
        if not isinstance(raw, dict):
            _LOGGER.warning("Invalid controller storage root; using safe defaults")
            return PersistentState()

        auto_mode = raw.get("auto_mode")
        blast_until = _utc_datetime(raw.get("blast_until"))
        manual_target = _finite_float(raw.get("manual_override_target"))
        fingerprint = _fingerprint(raw.get("manual_override_fingerprint"))

        rate = _finite_float(raw.get("learned_rate"))
        count = raw.get("learned_sample_count")
        valid_count = isinstance(count, int) and not isinstance(count, bool) and count >= 0
        learned_consistent = valid_count and (
            (rate is None and count == 0) or (rate is not None and 0 < rate <= 10 and count > 0)
        )

        invalid_fields: list[str] = []
        if "auto_mode" in raw and not isinstance(auto_mode, bool):
            invalid_fields.append("auto_mode")
        if raw.get("blast_until") is not None and blast_until is None:
            invalid_fields.append("blast_until")
        if raw.get("manual_override_target") is not None and manual_target is None:
            invalid_fields.append("manual_override_target")
        if raw.get("manual_override_fingerprint") is not None and fingerprint is None:
            invalid_fields.append("manual_override_fingerprint")
        if ("learned_rate" in raw or "learned_sample_count" in raw) and not learned_consistent:
            invalid_fields.extend(("learned_rate", "learned_sample_count"))
        for field in dict.fromkeys(invalid_fields):
            _LOGGER.debug("Ignoring invalid controller storage field: %s", field)

        return PersistentState(
            auto_mode=auto_mode if isinstance(auto_mode, bool) else True,
            smart_control_enabled=raw.get("smart_control_enabled") is True,
            forced_away=raw.get("forced_away") is True,
            away_seen_absent=[v for v in raw.get("away_seen_absent", []) if isinstance(v, str)]
                if isinstance(raw.get("away_seen_absent"), list) else [],
            away_selected_at=_utc_datetime(raw.get("away_selected_at")),
            blast_until=blast_until,
            manual_override_target=manual_target,
            manual_override_fingerprint=fingerprint,
            learned_rate=rate if learned_consistent else None,
            learned_sample_count=count if learned_consistent else 0,
            thermal_model=raw.get("thermal_model", {}),
            forecast_accuracy=raw.get("forecast_accuracy", {}),
            heating_cost=_json_mapping(raw.get("heating_cost", {})),
        )

    async def async_save(self, state: PersistentState) -> None:
        """Save only durable state in JSON-compatible form."""
        await self._store.async_save(
            {
                "auto_mode": state.auto_mode,
                "smart_control_enabled": state.smart_control_enabled,
                "forced_away": state.forced_away,
                "away_seen_absent": state.away_seen_absent,
                "away_selected_at": state.away_selected_at.isoformat() if state.away_selected_at else None,
                "blast_until": (
                    state.blast_until.astimezone(timezone.utc).isoformat()
                    if state.blast_until is not None
                    else None
                ),
                "manual_override_target": state.manual_override_target,
                "manual_override_fingerprint": (
                    list(state.manual_override_fingerprint)
                    if state.manual_override_fingerprint is not None
                    else None
                ),
                "learned_rate": state.learned_rate,
                "learned_sample_count": state.learned_sample_count,
                "thermal_model": state.thermal_model,
                "forecast_accuracy": state.forecast_accuracy,
                "heating_cost": _json_mapping(state.heating_cost),
            }
        )
