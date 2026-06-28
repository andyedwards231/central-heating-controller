"""Home Assistant coordinator for central heating control."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import timedelta
import logging
import math
from typing import Any

from homeassistant.components.climate import (
    ATTR_CURRENT_TEMPERATURE,
    ATTR_HVAC_ACTION,
    ATTR_TARGET_TEMP_STEP,
    DOMAIN as CLIMATE_DOMAIN,
    HVACMode,
)
from homeassistant.components.zone import in_zone
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    ATTR_FRIENDLY_NAME,
    ATTR_LATITUDE,
    ATTR_LONGITUDE,
    ATTR_TEMPERATURE,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import (
    EVENT_HOMEASSISTANT_STARTED,
    CoreState,
    Event,
    HomeAssistant,
    State,
    callback,
)
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .config_flow import _climate_capabilities, _validate_settings
from .const import (
    CONF_ACTIVE_HVAC_MODE,
    CONF_ARRIVAL_TIME,
    CONF_CLIMATE,
    CONF_DESTINATION,
    CONF_DESTINATION_HOME_VALUE,
    CONF_ECO_TEMP,
    CONF_FALLBACK_MINUTES,
    CONF_HIGH_TEMP,
    CONF_HOME_ZONE,
    CONF_LOW_TEMP,
    CONF_MAX_WARMUP_MINUTES,
    CONF_PERSONS,
    CONF_SCHEDULE,
    HEAT_BLAST_MINUTES,
    NAME,
    OPT_RESET_LEARNING_REQUESTED,
)
from .learning import HeatingRateLearner
from .models import ControlInputs, ControllerSettings, ControllerState, PersistentState
from .policy import evaluate_policy
from .storage import ControllerStore
from .travel import destination_is_home, preheat_timing

_LOGGER = logging.getLogger(__name__)
_INVALID_STATE_VALUES = {STATE_UNKNOWN, STATE_UNAVAILABLE}
_SETTING_KEYS = {
    CONF_ACTIVE_HVAC_MODE,
    CONF_HIGH_TEMP,
    CONF_LOW_TEMP,
    CONF_ECO_TEMP,
    CONF_FALLBACK_MINUTES,
    CONF_MAX_WARMUP_MINUTES,
    CONF_DESTINATION_HOME_VALUE,
}


def _finite_float(value: object) -> float | None:
    """Return a finite float while excluding booleans."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        converted = float(value)
    except OverflowError, TypeError, ValueError:
        return None
    return converted if math.isfinite(converted) else None


def _normalized_location(value: object) -> str | None:
    """Normalize a zone-like state for fallback occupancy matching."""
    if not isinstance(value, str):
        return None
    normalized = "_".join(value.strip().casefold().replace("-", " ").split())
    return normalized.removeprefix("zone.") or None


class ControllerCoordinator(DataUpdateCoordinator[ControllerState]):
    """Own snapshots, policy evaluation, persistence, and thermostat writes."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=NAME,
            update_interval=timedelta(minutes=1),
            always_update=False,
        )
        self.entry = entry
        self.store = ControllerStore(hass, entry.entry_id)
        self.persistent_state = PersistentState()
        self.learner = HeatingRateLearner()
        self.pending_hvac_mode: str | None = None
        self.pending_target: float | None = None
        self._event_unsubscribers: list[Callable[[], None]] = []
        self._skip_duplicate_listener_update = False
        self._shutting_down = False
        self._evaluation_idle = asyncio.Event()
        self._evaluation_idle.set()

    @property
    def config(self) -> dict[str, Any]:
        """Return entry data overlaid with editable options."""
        return dict(self.entry.data) | dict(self.entry.options)

    @property
    def settings(self) -> ControllerSettings:
        """Return typed settings from validated config-entry values."""
        config = self.config
        return ControllerSettings(
            high_temperature=config[CONF_HIGH_TEMP],
            low_temperature=config[CONF_LOW_TEMP],
            eco_temperature=config[CONF_ECO_TEMP],
            fallback_warmup_minutes=config[CONF_FALLBACK_MINUTES],
            maximum_warmup_minutes=config[CONF_MAX_WARMUP_MINUTES],
            active_hvac_mode=config[CONF_ACTIVE_HVAC_MODE],
            destination_home_override=config.get(CONF_DESTINATION_HOME_VALUE),
        )

    async def async_setup(self) -> None:
        """Restore state, consume one-shot actions, subscribe, and refresh."""
        try:
            self.persistent_state = await self.store.async_load()
            self.learner = HeatingRateLearner(
                self.persistent_state.learned_rate,
                self.persistent_state.learned_sample_count,
            )

            if self.entry.options.get(OPT_RESET_LEARNING_REQUESTED) is True:
                self.learner.reset()
                self._copy_learning_state()
                await self.store.async_save(self.persistent_state)
                options = dict(self.entry.options)
                options.pop(OPT_RESET_LEARNING_REQUESTED, None)
                self.hass.config_entries.async_update_entry(self.entry, options=options)

            config = self.config
            entity_ids = [
                config[CONF_CLIMATE],
                *config[CONF_PERSONS],
                config[CONF_HOME_ZONE],
                config[CONF_SCHEDULE],
                config[CONF_DESTINATION],
            ]
            if config.get(CONF_ARRIVAL_TIME):
                entity_ids.append(config[CONF_ARRIVAL_TIME])
            for entity_id in dict.fromkeys(entity_ids):
                self._event_unsubscribers.append(
                    async_track_state_change_event(self.hass, entity_id, self._handle_state_change)
                )
            if self.hass.state is not CoreState.running:
                self._event_unsubscribers.append(
                    self.hass.bus.async_listen_once(
                        EVENT_HOMEASSISTANT_STARTED, self._handle_state_change
                    )
                )
            await self.async_config_entry_first_refresh()
        except BaseException:
            await self.async_shutdown()
            raise

    @callback
    def _handle_state_change(self, event: Event) -> None:
        """Request a fresh immutable snapshot after any input change."""
        if not self._shutting_down:
            self.hass.async_create_task(self.async_request_refresh())

    @callback
    def async_update_listeners(self) -> None:
        """Suppress the refresh completion notification after early publication."""
        if self._skip_duplicate_listener_update:
            self._skip_duplicate_listener_update = False
            if self.last_update_success:
                return
        super().async_update_listeners()

    async def async_shutdown(self) -> None:
        """Stop new work, remove listeners, and wait for active evaluation."""
        self._shutting_down = True
        while self._event_unsubscribers:
            self._event_unsubscribers.pop()()
        await self._evaluation_idle.wait()
        await super().async_shutdown()

    def _climate_state(self) -> State | None:
        return self.hass.states.get(self.config[CONF_CLIMATE])

    @staticmethod
    def _available(state: State | None) -> bool:
        return state is not None and state.state not in _INVALID_STATE_VALUES

    def _occupied(self) -> bool:
        """Resolve occupancy with coordinates, names, and conservative fallbacks."""
        config = self.config
        zone = self.hass.states.get(config[CONF_HOME_ZONE])
        if not self._available(zone):
            return True
        assert zone is not None
        zone_candidates = {
            candidate
            for raw in (
                config[CONF_HOME_ZONE],
                config[CONF_HOME_ZONE].split(".", 1)[-1],
                zone.attributes.get(ATTR_FRIENDLY_NAME),
            )
            if (candidate := _normalized_location(raw)) is not None
        }

        any_valid = False
        for entity_id in config[CONF_PERSONS]:
            person = self.hass.states.get(entity_id)
            if not self._available(person):
                continue
            assert person is not None
            any_valid = True
            latitude = _finite_float(person.attributes.get(ATTR_LATITUDE))
            longitude = _finite_float(person.attributes.get(ATTR_LONGITUDE))
            if latitude is not None and longitude is not None:
                try:
                    if in_zone(zone, latitude, longitude):
                        return True
                    continue
                except KeyError, TypeError, ValueError:
                    pass
            if _normalized_location(person.state) in zone_candidates:
                return True
        return not any_valid

    def _copy_learning_state(self) -> None:
        self.persistent_state.learned_rate = self.learner.rate
        self.persistent_state.learned_sample_count = self.learner.sample_count

    async def _async_update_data(self) -> ControllerState:
        """Track a complete evaluation so shutdown can await it."""
        self._evaluation_idle.clear()
        try:
            return await self._async_evaluate_data()
        except asyncio.CancelledError:
            self._skip_duplicate_listener_update = False
            raise
        finally:
            self._evaluation_idle.set()

    async def _async_evaluate_data(self) -> ControllerState:
        """Build one snapshot, publish it, then safely apply its command intent."""
        if self._shutting_down and self.data is not None:
            return self.data
        now = dt_util.utcnow()
        config = self.config
        settings = self.settings
        climate = self._climate_state()
        thermostat_available = self._available(climate)
        current_temperature = (
            _finite_float(climate.attributes.get(ATTR_CURRENT_TEMPERATURE))
            if thermostat_available and climate is not None
            else None
        )
        current_target = (
            _finite_float(climate.attributes.get(ATTR_TEMPERATURE))
            if thermostat_available and climate is not None
            else None
        )

        learned = None
        if thermostat_available and climate is not None:
            learned = self.learner.observe(
                now,
                current_temperature,
                current_target,
                climate.attributes.get(ATTR_HVAC_ACTION) == "heating",
            )
        if learned is not None:
            self._copy_learning_state()
            await self.store.async_save(self.persistent_state)

        warmup_minutes = self.learner.warmup_minutes(
            current_temperature,
            settings.high_temperature,
            settings.fallback_warmup_minutes,
            settings.maximum_warmup_minutes,
        )

        occupied = self._occupied()
        schedule = self.hass.states.get(config[CONF_SCHEDULE])
        schedule_high = schedule is not None and schedule.state == "on"
        zone = self.hass.states.get(config[CONF_HOME_ZONE])
        destination = self.hass.states.get(config[CONF_DESTINATION])
        journey_home = False
        if self._available(destination) and zone is not None:
            assert destination is not None
            journey_home = destination_is_home(
                destination.state,
                config[CONF_HOME_ZONE],
                zone.attributes.get(ATTR_FRIENDLY_NAME),
                settings.destination_home_override,
            )

        arrival = None
        start = None
        preheat_ready = False
        if journey_home:
            eta_entity = config.get(CONF_ARRIVAL_TIME)
            eta = self.hass.states.get(eta_entity) if eta_entity else None
            raw_eta = eta.state if self._available(eta) and eta is not None else None
            local_tz = dt_util.get_time_zone(self.hass.config.time_zone)
            timing = preheat_timing(raw_eta, now, int(warmup_minutes), local_tz)
            preheat_ready = timing.ready
            arrival = timing.arrival
            start = timing.start

        blast_active = False
        if self.persistent_state.blast_until is not None:
            if self.persistent_state.blast_until > now:
                blast_active = True
            else:
                self.persistent_state.blast_until = None
                await self.store.async_save(self.persistent_state)

        result = evaluate_policy(
            ControlInputs(
                thermostat_available=thermostat_available,
                auto_mode=self.persistent_state.auto_mode,
                blast_active=blast_active,
                manual_override_target=self.persistent_state.manual_override_target,
                occupied=occupied,
                schedule_high=schedule_high,
                journey_home=journey_home,
                preheat_ready=preheat_ready,
                current_temperature=current_temperature,
                high_temperature=settings.high_temperature,
                low_temperature=settings.low_temperature,
                eco_temperature=settings.eco_temperature,
                active_hvac_mode=settings.active_hvac_mode,
                evaluated_at=now,
            )
        )
        state = ControllerState(
            result=result,
            current_temperature=current_temperature,
            learned_rate=self.learner.rate,
            learned_trusted=self.learner.trusted,
            arrival_time=arrival,
            preheat_start_time=start,
            warmup_minutes=warmup_minutes,
        )
        if state != self.data:
            self.async_set_updated_data(state)
            self._skip_duplicate_listener_update = True
        await self._async_apply_commands(climate, result.hvac_mode, result.target_temperature)
        return state

    async def _async_apply_commands(
        self, climate: State | None, desired_mode: str | None, desired_target: float | None
    ) -> None:
        """Apply policy intent in mode-then-temperature order."""
        if (
            self._shutting_down
            or self.hass.state is not CoreState.running
            or not self._available(climate)
            or desired_mode is None
            or climate is None
        ):
            return
        entity_id = self.config[CONF_CLIMATE]
        if climate.state != desired_mode:
            self.pending_hvac_mode = desired_mode
            try:
                await self.hass.services.async_call(
                    CLIMATE_DOMAIN,
                    "set_hvac_mode",
                    {"entity_id": entity_id, "hvac_mode": desired_mode},
                    blocking=True,
                )
            except HomeAssistantError as err:
                self.pending_hvac_mode = None
                _LOGGER.error("Failed setting HVAC mode for %s: %s", entity_id, err)

        if self._shutting_down or desired_mode == HVACMode.OFF or desired_target is None:
            return
        target = _finite_float(desired_target)
        current_target = _finite_float(climate.attributes.get(ATTR_TEMPERATURE))
        target_step = _finite_float(climate.attributes.get(ATTR_TARGET_TEMP_STEP))
        tolerance = target_step / 2 if target_step is not None and target_step > 0 else 0.01
        if target is None or (
            current_target is not None and abs(target - current_target) <= tolerance
        ):
            return
        self.pending_target = target
        try:
            await self.hass.services.async_call(
                CLIMATE_DOMAIN,
                "set_temperature",
                {"entity_id": entity_id, ATTR_TEMPERATURE: target},
                blocking=True,
            )
        except HomeAssistantError as err:
            self.pending_target = None
            _LOGGER.error("Failed setting target temperature for %s: %s", entity_id, err)

    async def async_set_auto_mode(self, enabled: bool) -> None:
        """Persist strict Auto mode and immediately re-evaluate."""
        if not isinstance(enabled, bool):
            raise TypeError("enabled must be a bool")
        self.persistent_state.auto_mode = enabled
        if not enabled:
            self.persistent_state.blast_until = None
            self.persistent_state.manual_override_target = None
            self.persistent_state.manual_override_fingerprint = None
        await self.store.async_save(self.persistent_state)
        await self.async_refresh()

    async def async_start_blast(self) -> bool:
        """Start or restart the fixed-duration heat blast."""
        if not self.persistent_state.auto_mode:
            return False
        self.persistent_state.manual_override_target = None
        self.persistent_state.manual_override_fingerprint = None
        self.persistent_state.blast_until = dt_util.utcnow() + timedelta(minutes=HEAT_BLAST_MINUTES)
        await self.store.async_save(self.persistent_state)
        await self.async_refresh()
        return True

    async def async_reset_learning(self) -> None:
        """Clear the learned model and immediately re-evaluate."""
        self.learner.reset()
        self._copy_learning_state()
        await self.store.async_save(self.persistent_state)
        await self.async_refresh()

    async def async_update_setting(self, key: str, value: Any) -> None:
        """Validate a complete setting set and write one option value."""
        if key not in _SETTING_KEYS:
            raise HomeAssistantError(f"Unsupported setting: {key}")
        climate = self._climate_state()
        if climate is None:
            raise HomeAssistantError("The configured climate entity is unavailable")
        capabilities, error = _climate_capabilities(climate)
        if error or capabilities is None:
            raise HomeAssistantError("The configured climate capabilities are invalid")
        candidate = {setting: self.config.get(setting) for setting in _SETTING_KEYS}
        candidate[key] = value
        errors, normalized = _validate_settings(candidate, capabilities)
        if errors or normalized is None:
            raise HomeAssistantError(f"Invalid setting: {next(iter(errors.values()))}")
        options = dict(self.entry.options) | normalized
        self.persistent_state.manual_override_target = None
        self.persistent_state.manual_override_fingerprint = None
        await self.store.async_save(self.persistent_state)
        updated = self.hass.config_entries.async_update_entry(self.entry, options=options)
        if not updated:
            await self.async_refresh()
