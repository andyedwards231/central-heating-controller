"""Home Assistant coordinator for central heating control."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from functools import partial
import json
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import StrEnum
import logging
import math
import time
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
    ATTR_UNIT_OF_MEASUREMENT,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
    UnitOfTemperature,
)
from homeassistant.core import (
    EVENT_HOMEASSISTANT_STARTED,
    Context,
    CoreState,
    Event,
    HomeAssistant,
    State,
    callback,
)
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util
from homeassistant.util.unit_conversion import TemperatureConverter

from .config_flow import _climate_capabilities, _default_settings, _validate_settings
from .const import (
    CONF_SMART_SCHEDULE_ENABLED,
    CONF_SMART_COMFORT_SCHEDULE,
    CONF_SMART_NIGHT_START,
    CONF_SMART_NIGHT_END,
    CONF_SMART_NIGHT_TEMPERATURE,
    CONF_SMART_DAYTIME_DROP,
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
    CONF_GAS_RATE,
    CONF_GAS_CONSUMPTION,
    CONF_GAS_PREVIOUS_CONSUMPTION,
    CONF_WEATHER,
    CONF_OUTDOOR_TEMP,
    CONF_HEATING_ACTIVITY,
    HEAT_BLAST_MINUTES,
    NAME,
    OPT_RESET_LEARNING_REQUESTED,
)
from .learning import HeatingRateLearner
from .forecasting import ThermalForecaster, ForecastPoint
from .heating_cost import HeatingCostModel
from .forecast_accuracy import ForecastAccuracyTracker
from .forecast_inputs import ForecastInputs, _celsius
from .models import (
    ControlInputs,
    ControlResult,
    ControllerStatus,
    ControllerSettings,
    ControllerState,
    JsonPrimitive,
    PersistentState,
    TemperatureCapabilities,
)
from .policy import evaluate_policy
from .repairs import sync_missing_entity_issues
from .readiness import evaluate_readiness
from .storage import ControllerStore
from .smart_schedule import plan_smart_schedule, parse_weekly_schedule, is_comfort_window
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
_COMMAND_RECORD_LIMIT = 16
_COMMAND_RECORD_TTL_SECONDS = 300
_COMMAND_ACK_WINDOW_SECONDS = 10
_EVENT_CLASSIFICATION_RETRY_LIMIT = 2
_VALID_TEMPERATURE_UNITS = frozenset(unit.value for unit in UnitOfTemperature)


class _CommandMatchStrength(StrEnum):
    """Confidence that a climate event acknowledges one of our commands."""

    STRONG = "strong"
    HEURISTIC = "heuristic"


@dataclass(frozen=True)
class _CommandRecord:
    """Transient provenance for one climate service write."""

    value: str | float
    issued_at: datetime
    issued_monotonic: float
    generation: int
    context: Context


@dataclass(frozen=True)
class _CapturedEvent:
    """Immutable state transition captured synchronously at admission."""

    entity_id: str | None
    old_state: State | None
    new_state: State | None
    policy_transition: bool


@dataclass(frozen=True)
class _OverrideAction:
    """Final event-ordered manual override mutation for reconciliation."""

    target: float | None


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


def _target_tolerance(climate: State) -> float:
    """Return the thermostat's target comparison tolerance."""
    target_step = _finite_float(climate.attributes.get(ATTR_TARGET_TEMP_STEP))
    return target_step / 2 if target_step is not None and target_step > 0 else 0.01


def _targets_match(climate: State, first: object, second: object) -> bool:
    """Compare two finite targets using the thermostat's declared precision."""
    first_target = _finite_float(first)
    second_target = _finite_float(second)
    return (
        first_target is not None
        and second_target is not None
        and abs(first_target - second_target) <= _target_tolerance(climate)
    )


def _temperature_capabilities(
    climate: State,
    fallback_unit: str,
) -> TemperatureCapabilities | None:
    """Return validated thermostat metadata with a safe native unit."""
    capabilities, error = _climate_capabilities(climate, fallback_unit)
    if error is not None or capabilities is None:
        return None

    raw_unit = climate.attributes.get(ATTR_UNIT_OF_MEASUREMENT)
    if raw_unit is None:
        unit = fallback_unit
    elif isinstance(raw_unit, str) and raw_unit in _VALID_TEMPERATURE_UNITS:
        unit = raw_unit
    else:
        return None
    if unit not in _VALID_TEMPERATURE_UNITS:
        unit = UnitOfTemperature.CELSIUS

    return TemperatureCapabilities(
        minimum=capabilities.min_temp,
        maximum=capabilities.max_temp,
        step=capabilities.temp_step,
        unit=unit,
    )


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
        self.heating_cost_model = HeatingCostModel()
        self._cost_forecast = []
        self._cost_saved_at = None
        self.thermal_model = ThermalForecaster()
        self.forecast_inputs = ForecastInputs(hass)
        self.forecast_accuracy = ForecastAccuracyTracker()
        self.pending_hvac_mode: str | None = None
        self.pending_target: float | None = None
        self._command_generation = 0
        self._target_command_records: deque[_CommandRecord] = deque(maxlen=_COMMAND_RECORD_LIMIT)
        self._hvac_command_records: deque[_CommandRecord] = deque(maxlen=_COMMAND_RECORD_LIMIT)
        self._event_unsubscribers: list[Callable[[], None]] = []
        self._skip_duplicate_listener_update = False
        self._accepting_events = True
        self._draining_events = False
        self._commands_stopped = False
        self._shutting_down = False
        self._last_policy_fingerprint: tuple[JsonPrimitive, ...] | None = None
        self._event_queue: deque[_CapturedEvent] = deque()
        self._event_worker: asyncio.Task[Any] | None = None
        self._event_tasks: set[asyncio.Task[Any]] = set()
        self._admitted_person_states: dict[str, State | None] = {}
        self._pending_override_action: _OverrideAction | None = None
        self._stale_own_target_echo = False
        self._override_dirty = False
        self._override_revision = 0
        self._evaluation_idle = asyncio.Event()
        self._evaluation_idle.set()
        self._preview_context = None
        self._preview_cache = None
        self._smart_target = None
        self._smart_healthy_since: datetime | None = None
        self._smart_health_context = None
        self._smart_health_checked_at: datetime | None = None
        self._command_error: str | None = None
        self._in_place_options: dict | None = None

    @property
    def controller_mode(self) -> str:
        if not self.persistent_state.auto_mode:
            return "off"
        return "away" if self.persistent_state.forced_away else "auto"

    def _away_observe_person(self, entity_id: str, state: State | None) -> bool:
        """Track real per-person absence/return; unknown is never a departure."""
        if self.controller_mode != "away" or not self._available(state):
            return False
        zone = self.hass.states.get(self.config[CONF_HOME_ZONE])
        if not self._available(zone):
            return False
        people = {person: None for person in self.config[CONF_PERSONS]}
        people[entity_id] = state
        home = self._occupied(people, zone)
        armed = self.persistent_state.away_seen_absent
        if not home and entity_id not in armed:
            armed.append(entity_id)
            return True
        if home and entity_id in armed:
            self.persistent_state.forced_away = False
            self.persistent_state.away_seen_absent = []
            self.persistent_state.away_selected_at = None
            return True
        return False

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

            stored_cost = self.persistent_state.heating_cost
            if isinstance(stored_cost, dict) and stored_cost.get("sources") == self._cost_sources():
                self.heating_cost_model = HeatingCostModel.restore_state(stored_cost, now=dt_util.utcnow())
            stored_model = self.persistent_state.thermal_model
            if isinstance(stored_model, dict) and stored_model.get("sources") == self._learning_sources():
                self.thermal_model = ThermalForecaster.from_dict(stored_model)
                self.forecast_accuracy = ForecastAccuracyTracker.from_dict(
                    self.persistent_state.forecast_accuracy, now=dt_util.utcnow()
                )
            elif self.persistent_state.smart_control_enabled:
                # Consent for one room/sensor combination cannot authorise a
                # different installation after it eventually learns again.
                self.persistent_state.smart_control_enabled = False
                await self.store.async_save(self.persistent_state)

            if self.entry.options.get(OPT_RESET_LEARNING_REQUESTED) is True:
                self.persistent_state.smart_control_enabled = False
                self.heating_cost_model = HeatingCostModel()
                self.learner.reset()
                self.thermal_model = ThermalForecaster()
                self.forecast_accuracy = ForecastAccuracyTracker()
                self._copy_learning_state()
                await self.store.async_save(self.persistent_state)
                options = dict(self.entry.options)
                options.pop(OPT_RESET_LEARNING_REQUESTED, None)
                self.hass.config_entries.async_update_entry(self.entry, options=options)

            config = self.config
            self._admitted_person_states = {
                entity_id: self.hass.states.get(entity_id) for entity_id in config[CONF_PERSONS]
            }
            entity_ids = [
                config[CONF_CLIMATE],
                *config[CONF_PERSONS],
                config[CONF_HOME_ZONE],
                config[CONF_SCHEDULE],
                config[CONF_DESTINATION],
            ]
            if config.get(CONF_ARRIVAL_TIME):
                entity_ids.append(config[CONF_ARRIVAL_TIME])
            entity_ids.extend(config[key] for key in (CONF_WEATHER, CONF_OUTDOOR_TEMP, CONF_HEATING_ACTIVITY, CONF_GAS_RATE, CONF_GAS_CONSUMPTION, CONF_GAS_PREVIOUS_CONSUMPTION) if config.get(key))
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
        """Admit an immutable event and ensure one ordered worker is running."""
        if not self._accepting_events:
            return
        old_state = event.data.get("old_state")
        new_state = event.data.get("new_state")
        entity_id = event.data.get("entity_id")
        if entity_id == self.config[CONF_SCHEDULE]:
            self.forecast_inputs.invalidate_schedule(entity_id)
            self._preview_cache = None
            self._smart_healthy_since = None
        old_state = old_state if isinstance(old_state, State) else None
        new_state = new_state if isinstance(new_state, State) else None
        transition_probe = _CapturedEvent(
            entity_id=entity_id,
            old_state=old_state,
            new_state=new_state,
            policy_transition=False,
        )
        captured = _CapturedEvent(
            entity_id=entity_id,
            old_state=old_state,
            new_state=new_state,
            policy_transition=self._event_changes_policy(transition_probe),
        )
        self._event_queue.append(captured)
        self._start_event_worker()

    @callback
    def _start_event_worker(self) -> None:
        """Start one queue worker when admission or shutdown draining permits."""
        if self._event_worker is not None and not self._event_worker.done():
            return
        self._event_worker = None
        task = self.hass.async_create_task(self._async_event_worker())
        self._event_worker = task
        self._event_tasks.add(task)
        task.add_done_callback(self._event_worker_done)

    @callback
    def _event_worker_done(self, task: asyncio.Task[Any]) -> None:
        """Retrieve worker errors and restart only for newly admitted work."""
        self._event_tasks.discard(task)
        if self._event_worker is task:
            self._event_worker = None
        try:
            task.result()
        except asyncio.CancelledError:
            pass
        except Exception:
            _LOGGER.exception("Unexpected state-event reconciliation failure")
        if (
            self._event_queue
            and (self._accepting_events or self._draining_events)
            and self._event_worker is None
        ):
            self._start_event_worker()

    async def _async_event_worker(self) -> None:
        """Classify each admitted event in order, then reconcile final state once."""
        while True:
            while self._event_queue:
                event = self._event_queue[0]
                for attempt in range(1, _EVENT_CLASSIFICATION_RETRY_LIMIT + 2):
                    try:
                        await self._async_classify_event(event)
                    except Exception:
                        if attempt <= _EVENT_CLASSIFICATION_RETRY_LIMIT:
                            _LOGGER.warning(
                                "State-event classification failed; retrying accepted event "
                                "(attempt %d of %d)",
                                attempt,
                                _EVENT_CLASSIFICATION_RETRY_LIMIT + 1,
                                exc_info=True,
                            )
                            continue
                        _LOGGER.error(
                            "Dropping accepted state event after %d classification attempts",
                            attempt,
                            exc_info=True,
                        )
                    break
                self._event_queue.popleft()
            await self.async_refresh()
            if not self._event_queue:
                return

    async def _async_classify_event(self, event: _CapturedEvent) -> None:
        """Classify command echoes and event-ordered policy transitions."""
        if event.entity_id in self.config[CONF_PERSONS]:
            selected = self.persistent_state.away_selected_at
            if (event.new_state is not None and (selected is None or event.new_state.last_updated > selected)
                    and self._away_observe_person(event.entity_id, event.new_state)):
                await self.store.async_save(self.persistent_state)
        if event.policy_transition:
            self._pending_override_action = _OverrideAction(None)
        if event.entity_id == self.config[CONF_CLIMATE] and event.new_state is not None:
            await self._async_handle_climate_change(event.old_state, event.new_state)

    async def _async_handle_climate_change(self, old_state: State | None, new_state: State) -> None:
        """Consume command acknowledgements or persist an external target."""
        mode_match = self._matching_command_record(
            self._hvac_command_records,
            new_state.state,
            new_state,
            target=False,
        )
        if mode_match is not None:
            mode_record, _strength = mode_match
            if new_state.state == mode_record.value:
                self._retire_command_record(self._hvac_command_records, mode_record)

        new_target = _finite_float(new_state.attributes.get(ATTR_TEMPERATURE))
        old_target = (
            _finite_float(old_state.attributes.get(ATTR_TEMPERATURE))
            if isinstance(old_state, State)
            else None
        )
        target_match = self._matching_command_record(
            self._target_command_records,
            new_target,
            new_state,
            target=True,
        )
        if target_match is not None:
            target_record, _strength = target_match
            # Some devices first echo the service context with the old value.
            # Keep provenance until the requested target actually arrives.
            if not _targets_match(new_state, new_target, target_record.value):
                return
            self._retire_command_record(self._target_command_records, target_record)
            manual_target = self.persistent_state.manual_override_target
            if (
                manual_target is not None
                and not _targets_match(new_state, new_target, manual_target)
            ):
                self._stale_own_target_echo = True
            return

        # Changing HVAC mode can restore the thermostat's saved setpoint.
        # A target carried by our mode acknowledgement is not a manual change.
        if mode_match is not None and mode_match[1] is _CommandMatchStrength.STRONG:
            return

        if (
            new_target is None
            or old_target is None
            or _targets_match(new_state, old_target, new_target)
            or self._last_policy_fingerprint is None
            or not self.persistent_state.auto_mode
            or (
                self.persistent_state.blast_until is not None
                and self.persistent_state.blast_until > dt_util.utcnow()
            )
        ):
            return

        if (
            self.persistent_state.manual_override_target == new_target
            and self._pending_override_action is None
        ):
            return
        self._pending_override_action = _OverrideAction(new_target)

    def _new_command_record(self, value: str | float) -> _CommandRecord:
        """Create fresh bounded command provenance."""
        self._command_generation += 1
        return _CommandRecord(
            value=value,
            issued_at=dt_util.utcnow(),
            issued_monotonic=time.monotonic(),
            generation=self._command_generation,
            context=Context(),
        )

    def _prune_command_records(self, records: deque[_CommandRecord]) -> None:
        """Expire command provenance that can no longer be a credible echo."""
        cutoff = time.monotonic() - _COMMAND_RECORD_TTL_SECONDS
        while records and records[0].issued_monotonic < cutoff:
            records.popleft()
        self._sync_pending_views()

    def _matching_command_record(
        self,
        records: deque[_CommandRecord],
        value: object,
        state: State,
        *,
        target: bool,
    ) -> tuple[_CommandRecord, _CommandMatchStrength] | None:
        """Return strong provenance or a narrow newest-record heuristic match."""
        self._prune_command_records(records)
        for record in records:
            if (
                state.context.id == record.context.id
                or state.context.parent_id == record.context.id
            ):
                return record, _CommandMatchStrength.STRONG
        if (
            not records
            or state.context.user_id is not None
            or state.context.parent_id is not None
        ):
            return None
        record = records[-1]
        if time.monotonic() - record.issued_monotonic > _COMMAND_ACK_WINDOW_SECONDS:
            return None
        if target:
            if _targets_match(state, value, record.value):
                return record, _CommandMatchStrength.HEURISTIC
        elif value == record.value:
            return record, _CommandMatchStrength.HEURISTIC
        return None

    def _retire_command_record(
        self, records: deque[_CommandRecord], record: _CommandRecord
    ) -> None:
        """Retire acknowledged or failed provenance and update compatibility views."""
        try:
            records.remove(record)
        except ValueError:
            pass
        self._sync_pending_views()

    def _sync_pending_views(self) -> None:
        """Expose the newest live command through legacy scalar attributes."""
        self.pending_target = (
            float(self._target_command_records[-1].value) if self._target_command_records else None
        )
        self.pending_hvac_mode = (
            str(self._hvac_command_records[-1].value) if self._hvac_command_records else None
        )

    def _command_pending(self, records: deque[_CommandRecord], value: str | float) -> bool:
        """Allow a short acknowledgement window before repeating the latest write."""
        return bool(
            records
            and records[-1].value == value
            and time.monotonic() - records[-1].issued_monotonic < _COMMAND_ACK_WINDOW_SECONDS
        )

    def _set_manual_override(
        self,
        target: float | None,
        fingerprint: tuple[JsonPrimitive, ...] | None,
    ) -> bool:
        """Mutate manual override state and mark the durable snapshot dirty."""
        if (
            self.persistent_state.manual_override_target == target
            and self.persistent_state.manual_override_fingerprint == fingerprint
        ):
            return False
        self.persistent_state.manual_override_target = target
        self.persistent_state.manual_override_fingerprint = fingerprint
        self._override_revision += 1
        self._override_dirty = True
        return True

    async def _async_try_save_override(self) -> bool:
        """Attempt one dirty override save without losing retry state."""
        if not self._override_dirty:
            return True
        revision = self._override_revision
        try:
            await self.store.async_save(self.persistent_state)
        except Exception:
            _LOGGER.exception("Failed persisting manual thermostat override")
            return False
        if revision == self._override_revision:
            self._override_dirty = False
        return True

    async def _async_ensure_override_saved(self) -> None:
        """Block publication and climate writes until dirty state is durable."""
        if self._override_dirty and not await self._async_try_save_override():
            raise UpdateFailed("Manual thermostat override could not be persisted")

    @callback
    def async_update_listeners(self) -> None:
        """Suppress the refresh completion notification after early publication."""
        if self._skip_duplicate_listener_update:
            self._skip_duplicate_listener_update = False
            if self.last_update_success:
                return
        super().async_update_listeners()

    async def async_shutdown(self) -> None:
        """Stop new work, drain event handlers, and wait for active evaluation."""
        self._commands_stopped = True
        self._accepting_events = False
        self._draining_events = True
        while self._event_unsubscribers:
            self._event_unsubscribers.pop()()
        while self._event_queue or self._event_tasks:
            if self._event_queue:
                self._start_event_worker()
            tasks = tuple(self._event_tasks)
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            await asyncio.sleep(0)
        self._draining_events = False
        self._shutting_down = True
        await self._evaluation_idle.wait()
        await super().async_shutdown()

    def _climate_state(self) -> State | None:
        return self.hass.states.get(self.config[CONF_CLIMATE])

    @staticmethod
    def _available(state: State | None) -> bool:
        return state is not None and state.state not in _INVALID_STATE_VALUES

    def _event_changes_policy(self, event: _CapturedEvent) -> bool:
        """Detect a meaningful policy transition from the event payload itself."""
        old_state = event.old_state
        new_state = event.new_state
        if old_state is None or new_state is None or old_state == new_state:
            return False
        config = self.config
        entity_id = event.entity_id
        if entity_id == config[CONF_SCHEDULE]:
            return (old_state.state == "on") != (new_state.state == "on")
        if entity_id in config[CONF_PERSONS]:
            old_people = dict(self._admitted_person_states)
            old_people[entity_id] = old_state
            new_people = dict(old_people)
            new_people[entity_id] = new_state
            self._admitted_person_states = new_people
            return self._occupied(old_people) != self._occupied(new_people)
        if entity_id == config[CONF_DESTINATION]:
            return self._journey_home(old_state) != self._journey_home(new_state)
        if entity_id == config.get(CONF_ARRIVAL_TIME):
            now = dt_util.utcnow()
            return self._preheat_ready(old_state, now) != self._preheat_ready(new_state, now)
        if entity_id == config[CONF_HOME_ZONE]:
            people = dict(self._admitted_person_states)
            destination = self.hass.states.get(config[CONF_DESTINATION])
            return self._occupied(people, old_state) != self._occupied(
                people, new_state
            ) or self._journey_home(destination, old_state) != self._journey_home(
                destination, new_state
            )
        return False

    def _journey_home(self, destination: State | None, zone: State | None = None) -> bool:
        """Resolve whether a supplied destination state means home."""
        if zone is None:
            zone = self.hass.states.get(self.config[CONF_HOME_ZONE])
        if not self._available(destination) or zone is None:
            return False
        assert destination is not None
        return destination_is_home(
            destination.state,
            self.config[CONF_HOME_ZONE],
            zone.attributes.get(ATTR_FRIENDLY_NAME),
            self.settings.destination_home_override,
        )

    def _preheat_ready(self, eta: State | None, now: datetime) -> bool:
        """Resolve ETA readiness for one event payload at admission time."""
        if not self._journey_home(self.hass.states.get(self.config[CONF_DESTINATION])):
            return False
        if not self.config.get(CONF_ARRIVAL_TIME):
            return True
        if not self._available(eta):
            return False
        climate = self._climate_state()
        current_temperature = (
            _finite_float(climate.attributes.get(ATTR_CURRENT_TEMPERATURE))
            if self._available(climate) and climate is not None
            else None
        )
        settings = self.settings
        warmup_minutes = self.learner.warmup_minutes(
            current_temperature,
            settings.high_temperature,
            settings.fallback_warmup_minutes,
            settings.maximum_warmup_minutes,
        )
        raw_eta = eta.state if self._available(eta) and eta is not None else None
        local_tz = dt_util.get_time_zone(self.hass.config.time_zone)
        return preheat_timing(raw_eta, now, int(warmup_minutes), local_tz).ready

    def _occupied(
        self,
        substitutions: dict[str, State | None] | None = None,
        zone: State | None = None,
    ) -> bool:
        """Resolve occupancy with coordinates, names, and conservative fallbacks."""
        config = self.config
        if zone is None:
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
            person = (
                substitutions[entity_id]
                if substitutions is not None and entity_id in substitutions
                else self.hass.states.get(entity_id)
            )
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

    def _learning_sources(self) -> list[str | None]:
        """Prevent coefficients learnt in one room being reused with different sensors."""
        return [self.config.get(key) for key in (CONF_CLIMATE, CONF_OUTDOOR_TEMP, CONF_HEATING_ACTIVITY)]

    def _copy_learning_state(self) -> None:
        self.persistent_state.learned_rate = self.learner.rate
        self.persistent_state.learned_sample_count = self.learner.sample_count
        self.persistent_state.thermal_model = self.thermal_model.to_dict() | {"sources": self._learning_sources()}
        self.persistent_state.forecast_accuracy = self.forecast_accuracy.to_dict()
        self.persistent_state.heating_cost = self.heating_cost_model.export_state() | {"sources": self._cost_sources()}

    async def _async_update_data(self) -> ControllerState:
        """Track a complete evaluation so shutdown can await it."""
        self._evaluation_idle.clear()
        try:
            return await self._async_evaluate_data()
        except asyncio.CancelledError:
            self._skip_duplicate_listener_update = False
            raise
        except Exception:
            if not self.persistent_state.smart_control_enabled:
                raise
            _LOGGER.exception("Intelligent heating failed; restoring the existing schedule")
            self._smart_healthy_since = None
            result = self._fallback_result(dt_util.utcnow())
            if self.data is None:
                state = ControllerState(result, None, self.learner.rate, self.learner.trusted,
                                        None, None, self.settings.fallback_warmup_minutes)
            else:
                state = replace(self.data, result=result)
            readiness = {"ready": False, "checks": [{"key": "calculation", "label": "Heating calculation",
                         "passed": False, "detail": "A calculation failed. The existing heating schedule is in control; check Home Assistant logs."}],
                         "blockers": ["Heating calculation"]}
            readiness.update(self._smart_control_description(readiness, False, result.status))
            state = replace(state, smart_readiness=readiness,
                            smart_plan={"state": "unavailable", "preview": True, "forecast": []},
                            temperature_forecast={"forecast": [], "confidence": "learning",
                                                  "reason": "Intelligent heating calculation failed"})
            self._publish_state(state)
            await self._async_apply_commands(self._climate_state(), result.hvac_mode, result.target_temperature)
            return state
        finally:
            self._evaluation_idle.set()

    async def _async_evaluate_data(self) -> ControllerState:
        """Build one snapshot, publish it, then safely apply its command intent."""
        if self._shutting_down and self.data is not None:
            return self.data
        self._prune_command_records(self._target_command_records)
        self._prune_command_records(self._hvac_command_records)
        await self._async_ensure_override_saved()
        now = dt_util.utcnow()
        config = self.config
        settings = self.settings
        climate = self._climate_state()
        person_states = {
            entity_id: self.hass.states.get(entity_id)
            for entity_id in config[CONF_PERSONS]
        }
        home_zone = self.hass.states.get(config[CONF_HOME_ZONE])
        schedule = self.hass.states.get(config[CONF_SCHEDULE])
        destination = self.hass.states.get(config[CONF_DESTINATION])
        arrival_entity = config.get(CONF_ARRIVAL_TIME)
        eta = self.hass.states.get(arrival_entity) if arrival_entity else None
        arrival_entity_missing = bool(arrival_entity) and eta is None
        missing_keys = {
            key
            for key, missing in (
                (CONF_CLIMATE, climate is None),
                (
                    CONF_PERSONS,
                    not person_states
                    or any(state is None for state in person_states.values()),
                ),
                (CONF_HOME_ZONE, home_zone is None),
                (CONF_SCHEDULE, schedule is None),
                (CONF_DESTINATION, destination is None),
                (CONF_ARRIVAL_TIME, arrival_entity_missing),
            )
            if missing
        }
        sync_missing_entity_issues(self.hass, self.entry, missing_keys)
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
        temperature_capabilities = (
            _temperature_capabilities(climate, self.hass.config.units.temperature_unit)
            if thermostat_available and climate is not None
            else None
        )

        # Observe unavailable periods too, so an outage breaks the sample window.
        learned = self.learner.observe(
            now,
            current_temperature,
            current_target,
            self._heating_active(climate) is True,
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

        away_changed = False
        for person_id, person in person_states.items():
            away_changed = self._away_observe_person(person_id, person) or away_changed
        if away_changed:
            await self.store.async_save(self.persistent_state)
        occupied = self._occupied(person_states, home_zone)
        schedule_high = schedule is not None and schedule.state == "on"
        journey_home = self._journey_home(destination, home_zone)

        arrival = None
        start = None
        preheat_ready = False
        if journey_home and not arrival_entity:
            preheat_ready = True
        elif journey_home and not arrival_entity_missing:
            raw_eta = eta.state if self._available(eta) and eta is not None else None
            local_tz = dt_util.get_time_zone(self.hass.config.time_zone)
            timing = preheat_timing(raw_eta, now, int(warmup_minutes), local_tz)
            preheat_ready = timing.ready
            arrival = timing.arrival
            start = timing.start

        if (self.controller_mode == "away" and journey_home and self._available(destination)
                and now - destination.last_reported <= timedelta(minutes=30)
                and (arrival is not None and arrival >= now or not arrival_entity)):
            self.persistent_state.forced_away = False
            self.persistent_state.away_seen_absent = []
            self.persistent_state.away_selected_at = None
            await self.store.async_save(self.persistent_state)
        if self.controller_mode == "away":
            occupied = False
            journey_home = False
            preheat_ready = False

        blast_active = False
        if self.persistent_state.blast_until is not None:
            if self.persistent_state.blast_until > now:
                blast_active = True
            else:
                self.persistent_state.blast_until = None
                await self.store.async_save(self.persistent_state)

        fingerprint: tuple[JsonPrimitive, ...] = (
            occupied,
            schedule_high,
            journey_home,
            preheat_ready,
            settings.high_temperature,
            settings.low_temperature,
            settings.eco_temperature,
            settings.active_hvac_mode,
        )
        if self.persistent_state.smart_control_enabled:
            fingerprint += (self._smart_period(now),)
        override_action = self._pending_override_action
        self._pending_override_action = None
        if override_action is not None:
            self._set_manual_override(
                override_action.target,
                fingerprint if override_action.target is not None else None,
            )
            if not await self._async_try_save_override():
                await self._async_ensure_override_saved()
        preserve_stale_echo = False
        if self.persistent_state.manual_override_target is not None and thermostat_available:
            assert climate is not None
            target_matches = _targets_match(
                climate,
                current_target,
                self.persistent_state.manual_override_target,
            )
            fingerprint_matches = self.persistent_state.manual_override_fingerprint == fingerprint
            preserve_stale_echo = (
                self._stale_own_target_echo and not target_matches and fingerprint_matches
            )
            if (not target_matches or not fingerprint_matches) and not preserve_stale_echo:
                self._set_manual_override(None, None)
                await self._async_ensure_override_saved()
        self._last_policy_fingerprint = fingerprint

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
        if self.controller_mode == "away" and thermostat_available:
            result = ControlResult(ControllerStatus.AWAY, settings.eco_temperature,
                                   settings.active_hvac_mode, "away_mode_latched")
        state = ControllerState(
            result=result,
            current_temperature=current_temperature,
            learned_rate=self.learner.rate,
            learned_trusted=self.learner.trusted,
            arrival_time=arrival,
            preheat_start_time=start,
            warmup_minutes=warmup_minutes,
            temperature_capabilities=temperature_capabilities,
            auto_mode=self.persistent_state.auto_mode,
            temperature_forecast=(self.data.temperature_forecast if self.data else {}),
            heating_cost=(self.data.heating_cost if self.data else {}),
            smart_plan=(self.data.smart_plan if self.data else {}),
            smart_readiness=(self.data.smart_readiness if self.data else {}),
        )
        # Urgent modes and legacy control never wait for planning. An opted-in
        # Auto cycle waits for readiness before issuing exactly one target.
        defer_control = (self.persistent_state.smart_control_enabled
                         and self.controller_mode == "auto"
                         and result.status not in (ControllerStatus.OFF, ControllerStatus.UNAVAILABLE,
                                                   ControllerStatus.HEAT_BLAST, ControllerStatus.MANUAL_OVERRIDE))
        input_signature = self._smart_input_signature()
        cohort_signature = self._smart_input_signature(include_climate=False)
        if not defer_control:
            self._publish_state(state)
            await self._async_apply_commands(climate, result.hvac_mode, result.target_temperature)
        if (
            preserve_stale_echo
            and self.controller_mode == "auto"
            and climate is not None
            and self.persistent_state.manual_override_target is not None
        ):
            await self._async_apply_target_command(
                climate, self.persistent_state.manual_override_target
            )
        self._stale_own_target_echo = False
        self._cost_forecast = []
        self._preview_context = None
        if config.get(CONF_WEATHER) and config.get(CONF_OUTDOOR_TEMP):
            forecast = await self._async_temperature_forecast(
                now, state, climate, occupied, schedule_high, journey_home, preheat_ready,
                issue_accuracy=False,
            )
            state = replace(state, temperature_forecast=forecast)
        # Ingest this cycle's meter intervals before deciding whether the gas
        # relationship is still usable. A new poor fit must block this cycle.
        try:
            cost = await self._async_heating_cost(now, climate)
            cost_ok = True
        except Exception:
            _LOGGER.warning("Heating cost estimate unavailable", exc_info=True)
            cost_ok = False
            cost = {day: {"cost_gbp": None, "reason": "Cost inputs could not be processed"}
                    for day in ("today", "tomorrow")}
        preview = await self._async_smart_preview(now, state, occupied, journey_home)
        readiness = self._smart_readiness(now, state, preview, occupied, journey_home)
        if not cost_ok:
            self._smart_healthy_since = None
            readiness["ready"] = False
            readiness["checks"].append({"key": "cost_inputs", "label": "Heating cost inputs",
                                        "passed": False, "detail": "Heating cost inputs could not be processed."})
            readiness["blockers"].append("Heating cost inputs")
        active = defer_control and readiness["ready"]
        if active:
            target = self._safe_smart_target(now, state)
            if target is None:
                active = False
                self._smart_healthy_since = None
                readiness["ready"] = False
                readiness["checks"].append({"key": "target", "label": "Safe thermostat target",
                                            "passed": False, "detail": "The planned target is invalid or outside thermostat limits."})
                readiness["blockers"].append("Safe thermostat target")
            else:
                status = (ControllerStatus.PREHEATING if not occupied and target > settings.eco_temperature
                          else ControllerStatus.HIGH if target >= settings.high_temperature
                          else ControllerStatus.LOW if occupied else ControllerStatus.AWAY)
                result = ControlResult(status, target, settings.active_hvac_mode, "intelligent_schedule")
                state = replace(state, result=result)
        if defer_control:
            # Only the chosen policy is scored; counterfactual preview forecasts
            # must never be compared with temperatures from a different policy.
            forecast = await self._async_temperature_forecast(
                now, state, climate, occupied, schedule_high, journey_home, preheat_ready,
                observe=False, issue_accuracy=False, smart_target=self._smart_target if active else None,
            )
            state = replace(state, temperature_forecast=forecast)
            if active and not forecast.get("forecast"):
                active = False
                self._smart_healthy_since = None
                result = self._fallback_result(dt_util.utcnow())
                state = replace(state, result=result)
                readiness["ready"] = False
                readiness["blockers"].append("Live forecast unavailable")
        if defer_control and input_signature != self._smart_input_signature():
            active = False
            self._smart_healthy_since = None
            result = self._fallback_result(dt_util.utcnow())
            state = replace(state, result=result)
            readiness["ready"] = False
            readiness["checks"].append({"key": "input_changed", "label": "Current inputs",
                                        "passed": False, "detail": "Inputs changed while planning. Using the existing schedule while a new plan is calculated."})
            readiness["blockers"].append("Inputs changed while planning")
        readiness.update(self._smart_control_description(readiness, active, state.result.status))
        if active:
            preview = dict(preview, state="active", preview=False,
                           explanation="Intelligent heating is controlling the thermostat. Safety checks are monitored continuously.")
        try:
            if defer_control:
                cost = await self._async_heating_cost(now, climate)
            for details in cost.values():
                details["forecast_confidence"] = state.temperature_forecast.get("confidence", "learning")
        except Exception:
            _LOGGER.warning("Heating cost estimate unavailable", exc_info=True)
            cost = {day: {"cost_gbp": None, "reason": "Cost inputs could not be processed"}
                    for day in ("today", "tomorrow")}
            if active:
                self._smart_healthy_since = None
                active = False
                result = self._fallback_result(dt_util.utcnow())
                readiness = dict(readiness, ready=False, blockers=readiness["blockers"] + ["Heating cost inputs"])
                readiness.update(self._smart_control_description(readiness, False, result.status))
                preview = dict(preview, state="preview", preview=True)
                state = replace(state, result=result, temperature_forecast={"forecast": [], "confidence": "learning",
                                    "reason": "Cost inputs changed; recalculating the existing schedule forecast"})
        state = replace(state, heating_cost=cost, smart_plan=preview, smart_readiness=readiness)
        if defer_control and input_signature != self._smart_input_signature():
            # Service/storage awaits may admit a final mode change. Never send
            # the superseded target, or score it as the policy in force.
            self._smart_healthy_since = None
            result = self._fallback_result(dt_util.utcnow())
            readiness = dict(readiness, ready=False,
                             blockers=readiness["blockers"] + ["Inputs changed while planning"])
            readiness.update(self._smart_control_description(readiness, False, result.status))
            state = replace(state, result=result, smart_readiness=readiness,
                            smart_plan=dict(preview, state="preview", preview=True),
                            temperature_forecast={"forecast": [], "confidence": "learning",
                                                  "reason": "Inputs changed while planning; recalculating"})
        self._publish_state(state)
        if defer_control:
            command_signature = self._smart_input_signature(include_climate=False)
            await self._async_apply_commands(climate, result.hvac_mode, result.target_temperature)
            if self._command_error or command_signature != self._smart_input_signature(include_climate=False):
                self._smart_healthy_since = None
                result = self._fallback_result(dt_util.utcnow())
                label = "Thermostat command failed" if self._command_error else "Inputs changed during thermostat command"
                readiness = dict(readiness, ready=False, blockers=readiness["blockers"] + [label])
                readiness["checks"] = readiness["checks"] + [{"key": "command_guard", "label": label,
                    "passed": False, "detail": self._command_error or "Using the current mode and existing schedule while a new plan is calculated."}]
                # One bounded attempt restores the current legacy intent even
                # when a priority change arrived during set_temperature itself.
                await self._async_apply_commands(self._climate_state(), result.hvac_mode,
                                                 result.target_temperature)
                readiness.update(self._smart_control_description(readiness, False, result.status))
                state = replace(state, result=result, smart_readiness=readiness,
                                smart_plan=dict(preview, state="preview", preview=True),
                                temperature_forecast={"forecast": [], "confidence": "learning",
                                                      "reason": label})
                self._publish_state(state)
        if not self._command_error and cohort_signature == self._smart_input_signature(include_climate=False):
            await self._async_record_forecast(now, state.temperature_forecast)
        return state

    async def _async_record_forecast(self, now, forecast) -> None:
        unit = forecast.get("temperature_unit")
        points = []
        for item in forecast.get("forecast", []):
            at = dt_util.parse_datetime(str(item.get("datetime", "")))
            temperature = _celsius(item.get("temperature"), unit)
            if at is not None and at.tzinfo is not None and temperature is not None:
                points.append(ForecastPoint(at, temperature, 0.0, None, False))
        if points and self.forecast_accuracy.issue(now, tuple(points)):
            self._copy_learning_state()
            try:
                await self.store.async_save(self.persistent_state)
            except Exception:
                _LOGGER.warning("Unable to persist forecast accuracy", exc_info=True)

    def _publish_state(self, state: ControllerState) -> None:
        if state != self.data:
            self.async_set_updated_data(state)
            self._skip_duplicate_listener_update = True

    def _smart_input_signature(self, *, include_climate=True) -> tuple:
        """Detect source changes while planning yields to the event loop."""
        keys = (CONF_CLIMATE, CONF_HOME_ZONE, CONF_SCHEDULE, CONF_DESTINATION,
                CONF_ARRIVAL_TIME, CONF_OUTDOOR_TEMP, CONF_WEATHER, CONF_HEATING_ACTIVITY,
                CONF_GAS_RATE, CONF_GAS_CONSUMPTION, CONF_GAS_PREVIOUS_CONSUMPTION)
        entities = [self.config.get(key) for key in keys] + self.config[CONF_PERSONS]
        return (json.dumps(self.config, sort_keys=True),
                self.controller_mode, self.persistent_state.smart_control_enabled,
                self.persistent_state.blast_until, self.persistent_state.manual_override_target,
                tuple((entity, state.last_updated if (state := self.hass.states.get(entity)) else None)
                      for entity in entities if entity and (include_climate or entity != self.config[CONF_CLIMATE])))

    def _fallback_result(self, now: datetime) -> ControlResult:
        """Re-read priority modes and the legacy schedule after a changed input."""
        settings = self.settings
        climate = self._climate_state()
        schedule = self.hass.states.get(self.config[CONF_SCHEDULE])
        result = evaluate_policy(ControlInputs(
            thermostat_available=self._available(climate),
            auto_mode=self.persistent_state.auto_mode,
            blast_active=self.persistent_state.blast_until is not None and self.persistent_state.blast_until > now,
            manual_override_target=self.persistent_state.manual_override_target,
            occupied=self._occupied(), schedule_high=schedule is not None and schedule.state == "on",
            journey_home=self._journey_home(self.hass.states.get(self.config[CONF_DESTINATION])),
            preheat_ready=self._preheat_ready(self.hass.states.get(self.config.get(CONF_ARRIVAL_TIME, "")), now),
            current_temperature=None,
            high_temperature=settings.high_temperature, low_temperature=settings.low_temperature,
            eco_temperature=settings.eco_temperature, active_hvac_mode=settings.active_hvac_mode,
            evaluated_at=now,
        ))
        if self.controller_mode == "away" and self._available(climate):
            return ControlResult(ControllerStatus.AWAY, settings.eco_temperature,
                                 settings.active_hvac_mode, "away_mode_latched")
        return result

    def _smart_period(self, now: datetime) -> str:
        """Manual overrides end at a comfort/night boundary, never each replan."""
        settings = self.smart_settings()
        local = now.astimezone(dt_util.get_time_zone(self.hass.config.time_zone))
        weekly = settings["weekly_schedule"]
        if any(weekly.values()):
            comfort = is_comfort_window(local, parse_weekly_schedule(weekly))
        else:
            schedule = self.hass.states.get(self.config[CONF_SCHEDULE])
            comfort = schedule is not None and schedule.state == "on"
        clock = local.strftime("%H:%M")
        start, end = settings["night_start"], settings["night_end"]
        night = start <= clock < end if start < end else clock >= start or clock < end
        return "comfort" if comfort else "night" if night else "daytime"

    def _smart_readiness(self, now, state, plan, occupied, journey_home) -> dict:
        """Fail closed on missing evidence; require an hour of passing checks."""
        def fresh(entity, limit):
            return (self._available(entity) and timedelta(0) <= now - entity.last_reported <= limit)

        def check(key, label, passed, detail):
            return {"key": key, "label": label, "passed": bool(passed), "detail": detail}

        climate = self._climate_state()
        outdoor = self.hass.states.get(self.config.get(CONF_OUTDOOR_TEMP, ""))
        weather = self.hass.states.get(self.config.get(CONF_WEATHER, ""))
        schedule = self.hass.states.get(self.config[CONF_SCHEDULE])
        people = [self.hass.states.get(entity) for entity in self.config[CONF_PERSONS]]
        zone = self.hass.states.get(self.config[CONF_HOME_ZONE])
        eta_id = self.config.get(CONF_ARRIVAL_TIME)
        eta = self.hass.states.get(eta_id) if eta_id else None
        destination = self.hass.states.get(self.config[CONF_DESTINATION])
        arrival_ok = (not journey_home or occupied or (
            fresh(destination, timedelta(minutes=30)) and fresh(eta, timedelta(minutes=30))
            and state.arrival_time is not None and state.arrival_time >= now
        ))
        generated = dt_util.parse_datetime(str(plan.get("generated_at", "")))
        plan_fresh = (generated is not None and generated.tzinfo is not None
                      and timedelta(0) <= now - generated <= timedelta(minutes=6))
        capabilities, error = (_climate_capabilities(climate, self.hass.config.units.temperature_unit)
                               if climate else (None, "unavailable"))
        rate = self.hass.states.get(self.config.get(CONF_GAS_RATE, ""))
        rate_value = None
        try:
            if self._available(rate) and rate.attributes.get("unit_of_measurement") == "GBP/kWh":
                rate_value = _finite_float(float(rate.state))
        except (ValueError, TypeError, OverflowError):
            pass
        calibration = self.heating_cost_model.calibration()
        interval = dt_util.parse_datetime(str(calibration.get("latest_interval_end", "")))
        gas_fresh = (interval is not None and interval.tzinfo is not None
                     and timedelta(0) <= now - interval <= timedelta(hours=72))
        checks = [
            check("thermostat", "Indoor temperature and thermostat",
                  fresh(climate, timedelta(minutes=30)) and state.current_temperature is not None
                  and not error and capabilities is not None
                  and self.settings.active_hvac_mode in ("heat", "auto"),
                  "Needs a valid heating thermostat and an indoor reading within 30 minutes."),
            check("outdoor", "Outdoor temperature", fresh(outdoor, timedelta(hours=2)),
                  "Needs a usable outdoor temperature reported within two hours."),
            check("weather", "Weather forecast", self._available(weather) and bool(state.temperature_forecast.get("forecast")),
                  state.temperature_forecast.get("reason") or "Needs a complete, usable 24-hour hourly weather forecast."),
            check("demand", "Heating activity signal", self._heating_demand(climate) is not None,
                  "Needs a valid heating demand reading within 30 minutes."),
            check("occupancy", "Household presence", bool(people) and all(self._available(p) for p in people)
                  and self._available(zone), "All configured people and the home zone must be available."),
            check("schedule", "Existing fallback schedule", self._available(schedule) and schedule.state in ("on", "off"),
                  "The existing on/off schedule must be available for fallback."),
            check("arrival", "Arrival time", arrival_ok,
                  "A homeward journey needs a valid future arrival time and fresh journey reports; otherwise existing preheating is used."),
            check("plan_fresh", "Up-to-date heating plan", plan_fresh,
                  "The plan must have been calculated within six minutes."),
            check("gas_fresh", "Recent gas measurements", gas_fresh,
                  "Needs a matched gas interval from the last 72 hours; delayed smart-meter readings are supported."),
            check("preview_enabled", "Schedule planning enabled", self.smart_settings()["enabled"],
                  "Enable schedule planning before using intelligent control."),
            check("commands", "Thermostat commands", self._command_error is None,
                  self._command_error or "No thermostat command failures are outstanding."),
        ]
        evidence = self.forecast_accuracy.readiness_metrics(now)
        result = evaluate_readiness(now, forecast=state.temperature_forecast, plan=plan,
                                    accuracy=evidence, input_checks=checks,
                                    cost_calibration=calibration,
                                    rate_available=rate_value is not None and rate_value > 0)
        context = (json.dumps(self.smart_settings(), sort_keys=True), self.settings.high_temperature,
                   self.settings.eco_temperature, tuple(self._learning_sources()), occupied,
                   self.controller_mode)
        if context != self._smart_health_context:
            self._smart_healthy_since = None
            self._smart_health_context = context
        if (self._smart_health_checked_at is None or now < self._smart_health_checked_at
                or now - self._smart_health_checked_at > timedelta(minutes=2)):
            self._smart_healthy_since = None
        self._smart_health_checked_at = now
        if result["ready"]:
            if self._smart_healthy_since is None or self._smart_healthy_since > now:
                self._smart_healthy_since = now
        else:
            self._smart_healthy_since = None
        minutes = ((now - self._smart_healthy_since).total_seconds() / 60
                   if self._smart_healthy_since else 0)
        stable = minutes >= 60
        result["checks"].append(check("stable", "Consistent safety checks", stable,
                                      f"{min(60, int(minutes))} of 60 consecutive minutes passing all checks. Restarts and failures restart this check."))
        if not stable:
            result["blockers"].append("Consistent safety checks")
        result["ready"] = result["ready"] and stable
        result["recent_accuracy"] = evidence
        return result

    def _smart_control_description(self, readiness, active, status) -> dict:
        failed_labels = {item["label"] for item in readiness["checks"] if not item.get("passed")}
        for index, label in enumerate(readiness["blockers"]):
            if label not in failed_labels:
                readiness["checks"].append({"key": f"live_guard_{index}", "label": label,
                                             "passed": False, "detail": label})
        enabled = self.persistent_state.smart_control_enabled
        paused = self.controller_mode != "auto" or status in (
            ControllerStatus.MANUAL_OVERRIDE, ControllerStatus.HEAT_BLAST, ControllerStatus.UNAVAILABLE)
        if active:
            control, explanation = "active", "Intelligent heating is controlling the thermostat."
        elif enabled and paused:
            control, explanation = "paused", "Intelligent heating is paused by the current mode or override."
        elif enabled:
            control, explanation = "fallback", "Using the existing heating schedule until all safety checks pass again."
        else:
            control = "preview"
            explanation = ("Ready to enable. The existing heating schedule remains in control."
                           if readiness["ready"] else "Preview only. The existing heating schedule remains in control while learning continues.")
        if self._command_error:
            explanation = self._command_error
        learning_keys = {"thermal_windows", "model_score", "heating_response", "demand_model",
                         "accuracy_1h", "accuracy_6h", "accuracy_24h", "gas_calibration"}
        learning = any(item["key"] in learning_keys and not item["passed"] for item in readiness["checks"])
        return {"state": "ready" if readiness["ready"] else "learning" if learning else "blocked",
                "live_control_enabled": enabled, "control_state": control,
                "explanation": explanation,
                "fallback_reason": (self._command_error or ", ".join(readiness["blockers"])) if control == "fallback" else None}

    def _safe_smart_target(self, now, state) -> float | None:
        if self._smart_target is None or state.temperature_capabilities is None:
            return None
        cap = state.temperature_capabilities
        try:
            value = _finite_float(TemperatureConverter.convert(self._smart_target(now), "°C", cap.unit))
        except (ValueError, TypeError, OverflowError):
            return None
        lower = min(self.settings.eco_temperature, self.smart_settings()["night_temperature"])
        upper = self.settings.high_temperature
        if value is None or not max(lower, cap.minimum) - 1e-6 <= value <= min(upper, cap.maximum) + 1e-6:
            return None
        if cap.step:
            value = cap.minimum + math.ceil((value - cap.minimum) / cap.step - 1e-9) * cap.step
        return round(value, 6) if value <= min(upper, cap.maximum) + 1e-6 else None

    @staticmethod
    def _target_transform(capabilities):
        """Simulate the same min-anchored thermostat grid used by live commands."""
        if capabilities is None:
            return None
        def transform(value):
            cap = capabilities
            native = TemperatureConverter.convert(value, "°C", cap.unit)
            if cap.step:
                native = cap.minimum + math.ceil((native - cap.minimum) / cap.step - 1e-9) * cap.step
            if not cap.minimum - 1e-6 <= native <= cap.maximum + 1e-6:
                raise ValueError("Planned temperature is outside thermostat limits")
            return TemperatureConverter.convert(native, cap.unit, "°C")
        return transform

    async def async_set_smart_control(self, enabled: bool) -> None:
        """Refresh before explicit opt-in; never arm an unready controller."""
        if not isinstance(enabled, bool):
            raise TypeError("enabled must be a bool")
        if enabled:
            await self.async_refresh()
            if not self.data.smart_readiness.get("ready") or self.controller_mode != "auto":
                raise HomeAssistantError("Intelligent heating is not ready. Review the remaining checks on the Heating dashboard.")
        previous = self.persistent_state.smart_control_enabled
        self.persistent_state.smart_control_enabled = enabled
        try:
            await self.store.async_save(self.persistent_state)
        except Exception:
            self.persistent_state.smart_control_enabled = previous
            raise
        await self.async_refresh()

    def smart_settings(self) -> dict:
        """Return native-unit settings even when no forecast is available."""
        climate = self._climate_state()
        capabilities, error = _climate_capabilities(climate, self.hass.config.units.temperature_unit) if climate else (None, None)
        defaults = _default_settings(self.hass, capabilities) if capabilities else {
            CONF_SMART_SCHEDULE_ENABLED: True, CONF_SMART_COMFORT_SCHEDULE: "{}",
            CONF_SMART_NIGHT_START: "23:00", CONF_SMART_NIGHT_END: "07:00",
            CONF_SMART_NIGHT_TEMPERATURE: 16, CONF_SMART_DAYTIME_DROP: 2,
        }
        config = defaults | self.config
        return {
            "enabled": config[CONF_SMART_SCHEDULE_ENABLED],
            "weekly_schedule": json.loads(config[CONF_SMART_COMFORT_SCHEDULE]),
            "night_start": config[CONF_SMART_NIGHT_START],
            "night_end": config[CONF_SMART_NIGHT_END],
            "night_temperature": config[CONF_SMART_NIGHT_TEMPERATURE],
            "daytime_drop": config[CONF_SMART_DAYTIME_DROP],
        }

    async def async_update_smart_schedule(self, payload: dict) -> None:
        """Validate the complete settings before saving an editable preview."""
        climate = self._climate_state()
        capabilities, error = _climate_capabilities(climate, self.hass.config.units.temperature_unit) if climate else (None, None)
        if error or capabilities is None:
            raise HomeAssistantError("The configured thermostat capabilities are unavailable")
        candidate = _default_settings(self.hass, capabilities) | self.config
        mapping = {
            "enabled": CONF_SMART_SCHEDULE_ENABLED, "weekly_schedule": CONF_SMART_COMFORT_SCHEDULE,
            "night_start": CONF_SMART_NIGHT_START, "night_end": CONF_SMART_NIGHT_END,
            "night_temperature": CONF_SMART_NIGHT_TEMPERATURE, "daytime_drop": CONF_SMART_DAYTIME_DROP,
        }
        candidate.update({key: payload[name] for name, key in mapping.items()})
        errors, normalized = _validate_settings(candidate, capabilities)
        if errors or normalized is None:
            raise HomeAssistantError(f"Invalid schedule: {next(iter(errors.values()))}")
        await self._async_update_options_in_place(dict(self.entry.options) | normalized)

    def handles_options_in_place(self, options) -> bool:
        """Only our validated runtime writes may bypass the normal source reload."""
        return self._in_place_options is not None and self._in_place_options == dict(options)

    async def _async_update_options_in_place(self, options: dict) -> None:
        """Persist a settings edit and refresh without removing HA entities."""
        self._in_place_options = dict(options)
        self._preview_cache = None
        self._smart_target = None
        self._smart_healthy_since = None
        self._smart_health_context = None
        self.hass.config_entries.async_update_entry(self.entry, options=options)
        await self.async_refresh()
        # Number entities read options directly. Even if the policy/result is
        # unchanged (e.g. editing High during a Low period), publish their values.
        self._skip_duplicate_listener_update = False
        super().async_update_listeners()

    async def _async_smart_preview(self, now, state, occupied, journey_home) -> dict:
        """Simulate proposed targets separately from every live control decision."""
        settings = self.smart_settings()
        base = {"settings": settings, "preview": True, "forecast": [], "state": "disabled"}
        if not settings["enabled"] or self.controller_mode == "off":
            self._smart_target = None
            return base | {"explanation": "Schedule preview is disabled"}
        if self._preview_context is None:
            self._smart_target = None
            return base | {"state": "unavailable", "explanation": "Waiting for temperature and weather forecasts"}
        indoor, inputs, unit = self._preview_context
        arrival = state.arrival_time if journey_home and self.controller_mode == "auto" else None
        key = (round(indoor, 1), occupied, arrival, self.controller_mode,
               json.dumps(settings, sort_keys=True), self.settings.high_temperature, self.settings.eco_temperature,
               self.thermal_model.sample_count, self.thermal_model.confidence,
               self.forecast_inputs.revision, inputs.current_outdoor_temperature,
               state.temperature_capabilities)
        cache = self._preview_cache
        if cache and cache[0] == key and timedelta(0) <= now - cache[1] < timedelta(minutes=5):
            self._smart_target = cache[3]
            return cache[2]
        try:
            zone = dt_util.get_time_zone(self.hass.config.time_zone)
            local_now = now.astimezone(zone)
            use_weekly_schedule = any(settings["weekly_schedule"].values())
            tomorrow_end = (local_now + timedelta(days=2)).replace(hour=0, minute=0, second=0, microsecond=0)
            desired_hours = max(36, math.ceil((tomorrow_end.astimezone(now.tzinfo) - now).total_seconds() / 300) / 12)
            hours = 24
            for candidate in (desired_hours, 36):
                try:
                    inputs.outdoor_temperature(now + timedelta(hours=candidate))
                    hours = candidate
                    break
                except ValueError:
                    continue
            planner = partial(
                plan_smart_schedule, local_now, indoor, inputs.outdoor_temperature,
                deepcopy(self.thermal_model), settings["weekly_schedule"],
                occupied_at=lambda at: occupied, arrival_at=arrival,
                comfort_at=None if use_weekly_schedule else inputs.schedule_high,
                night_start=settings["night_start"], night_end=settings["night_end"],
                comfort_temperature=_celsius(self.settings.high_temperature, unit),
                daytime_drop=settings["daytime_drop"] * (5 / 9 if unit == "°F" else 1),
                night_minimum=_celsius(settings["night_temperature"], unit),
                away_eco=_celsius(self.settings.eco_temperature, unit), hours=hours,
                target_transform=self._target_transform(state.temperature_capabilities),
            )
            plan = await self.hass.async_add_executor_job(planner)
            def native(value):
                return round(TemperatureConverter.convert(value, "°C", unit), 2) if value is not None else None
            explanation = "Preview only — your current heating schedule remains in control."
            if plan.provisional:
                explanation += " Still learning how your house heats and cools."
            if not plan.feasible:
                explanation += " The model cannot yet reach all comfort temperatures in time."
            result = base | {
                "state": "preview", "temperature_unit": unit, "generated_at": now.isoformat(),
                "explanation": explanation, "provisional": plan.provisional, "feasible": plan.feasible,
                "comfort_shortfall_c": plan.comfort_shortfall_c,
                "schedule_source": "integration" if use_weekly_schedule else "existing_schedule",
                "recovery_lead_minutes": plan.recovery_lead_minutes,
                "estimated_demand_hours": round(plan.total_demand_hours, 3),
                "terminal_recovery_demand_hours": round(plan.terminal_recovery_cost_hours, 3),
                "objective_demand_hours": round(plan.objective_demand_hours, 3),
                "cost_basis": "Minimises predicted heating demand at a constant gas rate; savings are not measured",
                "gap_drops": [{"start": gap.start.isoformat(), "end": gap.end.isoformat(),
                               "drop": round(gap.drop * (1.8 if unit == "°F" else 1), 2)} for gap in plan.gap_drops],
                "forecast": [{"datetime": point.at.isoformat(), "temperature": native(point.indoor_temperature),
                              "target_temperature": native(point.target_temperature),
                              "heating_demand_percent": round(point.demand * 100, 1)}
                             for point in plan.points if point.at <= now + timedelta(hours=24)],
            }
            # The callable and serialised points always belong to the same plan.
            end = now + timedelta(hours=hours)
            def bounded_target(at):
                if at > end:
                    raise ValueError("Intelligent plan does not cover this forecast horizon")
                return plan.target_at(at)
            self._smart_target = bounded_target
            self._preview_cache = (key, now, result, bounded_target)
            return result
        except Exception:
            self._smart_target = None
            _LOGGER.warning("Smart schedule preview unavailable", exc_info=True)
            return base | {"state": "unavailable", "explanation": "The schedule preview could not be calculated"}

    def _cost_sources(self) -> list[str | None]:
        return [self.config.get(key) for key in
                (CONF_CLIMATE, CONF_HEATING_ACTIVITY, CONF_GAS_CONSUMPTION, CONF_GAS_PREVIOUS_CONSUMPTION)]

    async def _async_heating_cost(self, now: datetime, climate: State | None) -> dict:
        config = self.config
        if not config.get(CONF_GAS_RATE) or not config.get(CONF_GAS_CONSUMPTION):
            return {}
        demand = self._heating_demand(climate)
        self.heating_cost_model.observe(now, float(demand) if demand is not None else None)
        intervals = []
        for key in (CONF_GAS_CONSUMPTION, CONF_GAS_PREVIOUS_CONSUMPTION):
            source = self.hass.states.get(config.get(key, "")) if config.get(key) else None
            if not self._available(source) or source is None:
                continue
            if source.attributes.get("unit_of_measurement") != "kWh" or source.attributes.get("is_estimated") is True:
                continue
            charges = source.attributes.get("charges", [])
            if not isinstance(charges, list):
                continue
            for item in charges:
                if not isinstance(item, dict):
                    continue
                start = dt_util.parse_datetime(str(item.get("start", "")))
                end = dt_util.parse_datetime(str(item.get("end", "")))
                # Ignore the latest/incomplete meter interval and future placeholder zeroes.
                if start is None or end is None or start.tzinfo is None or end.tzinfo is None or end > now - timedelta(minutes=30):
                    continue
                intervals.append({"start": start, "end": end,
                                  "consumption_kwh": _finite_float(item.get("consumption_kwh", item.get("consumption")))})
        self.heating_cost_model.ingest(intervals, now)
        rate_state = self.hass.states.get(config[CONF_GAS_RATE])
        rate = None
        if (self._available(rate_state) and rate_state is not None
                and rate_state.attributes.get("unit_of_measurement") == "GBP/kWh"):
            try:
                rate = _finite_float(float(rate_state.state))
            except (ValueError, TypeError, OverflowError):
                pass
        zone = dt_util.get_time_zone(self.hass.config.time_zone)
        start = now.astimezone(zone).replace(hour=0, minute=0, second=0, microsecond=0)
        result = {}
        for index, name in enumerate(("today", "tomorrow")):
            left, right = start + timedelta(days=index), start + timedelta(days=index + 1)
            result[name] = self.heating_cost_model.predict_day(left, right, now, self._cost_forecast, rate)
            result[name].update({"date": left.date().isoformat(), "rate_gbp_per_kwh": rate,
                                 "rate_assumption": "Current unit rate held constant for the whole day",
                                 "calibration": self.heating_cost_model.calibration(),
                                 "excludes": "Standing charge and fitted background gas use",
                                 "attribution": "Statistical estimate; hot water during heating can bias the split"})
        if self._cost_saved_at is None or now - self._cost_saved_at >= timedelta(minutes=5):
            self._copy_learning_state()
            try:
                await self.store.async_save(self.persistent_state)
                self._cost_saved_at = now
            except Exception:
                _LOGGER.warning("Unable to persist heating cost learning", exc_info=True)
        return result

    def _heating_active(self, climate: State | None) -> bool | None:
        """Keep the legacy net-rise learner limited to binary activity sources."""
        demand = self._heating_demand(climate)
        return demand if isinstance(demand, bool) else None

    def _heating_demand(self, climate: State | None) -> bool | float | None:
        """Read binary activity or a percentage demand, never infer it from HVAC mode."""
        entity_id = self.config.get(CONF_HEATING_ACTIVITY)
        if entity_id:
            activity = self.hass.states.get(entity_id)
            if activity is not None and self._available(activity):
                if dt_util.utcnow() - activity.last_reported > timedelta(minutes=30):
                    return None
                value = activity.state.casefold()
                if activity.attributes.get(ATTR_UNIT_OF_MEASUREMENT) == "%":
                    try:
                        percent = float(value)
                    except (TypeError, ValueError, OverflowError):
                        return None
                    return percent / 100 if math.isfinite(percent) and 0 <= percent <= 100 else None
                if value in ("on", "true", "heating"):
                    return True
                if value in ("off", "false", "idle"):
                    return False
            return None
        if not self._available(climate) or climate is None:
            return None
        action = climate.attributes.get(ATTR_HVAC_ACTION)
        if action == "heating":
            return True
        if action in ("idle", "off") or climate.state == "off":
            return False
        return None

    async def _async_temperature_forecast(
        self, now: datetime, state: ControllerState, climate: State | None,
        occupied: bool, schedule_high: bool, journey_home: bool, preheat_ready: bool,
        *, observe: bool = True, issue_accuracy: bool = True, smart_target=None,
    ) -> dict:
        """Publish a conditional forecast without allowing weather failures to stop control."""
        config = self.config
        unit = (state.temperature_capabilities.unit if state.temperature_capabilities
                else self.hass.config.units.temperature_unit)
        indoor = _celsius(state.current_temperature, unit)
        outside_state = self.hass.states.get(config[CONF_OUTDOOR_TEMP])
        outside = (_celsius(outside_state.state, outside_state.attributes.get(ATTR_UNIT_OF_MEASUREMENT))
                   if outside_state else None)
        if climate is not None and now - climate.last_reported > timedelta(hours=2):
            indoor = None
        if outside_state is not None and now - outside_state.last_reported > timedelta(hours=2):
            outside = None
        activity = self._heating_demand(climate)
        # Score the actual sensor report, not a held value re-labelled as a new reading.
        accuracy_changed = observe and self.forecast_accuracy.observe(
            climate.last_reported if climate is not None else now, indoor
        )
        # Learn independently of the forecast service's availability, using the
        # observed thermostat setpoint rather than a command awaiting acknowledgement.
        observed_target = (_celsius(climate.attributes.get(ATTR_TEMPERATURE), unit)
                           if climate is not None and climate.state != "off" else None)
        model_changed = observe and self.thermal_model.observe(
            now, indoor, outside, activity, target_temperature=observed_target
        )
        if model_changed or accuracy_changed:
            self._copy_learning_state()
            try:
                await self.store.async_save(self.persistent_state)
            except Exception:
                _LOGGER.warning("Unable to persist thermal learning", exc_info=True)
        base = {"forecast": [], "temperature_unit": unit, "confidence": "learning",
                "forecast_accuracy": self.forecast_accuracy.metrics()}
        if indoor is None:
            return base | {"reason": "Thermostat temperature is unavailable or has not reported for over two hours"}
        if self.settings.active_hvac_mode not in ("heat", "auto"):
            return base | {"reason": "Temperature forecasting requires a heating HVAC mode"}
        try:
            inputs = await self.forecast_inputs.async_load(
                now, config[CONF_WEATHER], config[CONF_OUTDOOR_TEMP], config[CONF_SCHEDULE]
            )
            if not inputs.available:
                return base | {"reason": inputs.reason}
            self._preview_context = (indoor, inputs, unit)
            settings = self.settings
            blast_until = self.persistent_state.blast_until
            override = self.persistent_state.manual_override_target
            override_valid = True

            def target(at: datetime) -> float | None:
                nonlocal override_valid
                if self.controller_mode == "away":
                    return _celsius(settings.eco_temperature, unit)
                high = inputs.schedule_high(at)
                ready = journey_home and (state.preheat_start_time is None and preheat_ready
                                         or state.preheat_start_time is not None
                                         and at >= state.preheat_start_time)
                home = occupied or (state.arrival_time is not None and at >= state.arrival_time)
                # Manual overrides end at the first policy transition and do not recur.
                if (high, ready, home) != (schedule_high, preheat_ready, occupied):
                    override_valid = False
                result = evaluate_policy(ControlInputs(
                    thermostat_available=True,
                    auto_mode=self.persistent_state.auto_mode,
                    blast_active=blast_until is not None and at < blast_until,
                    manual_override_target=override if override_valid else None,
                    occupied=home, schedule_high=high, journey_home=journey_home,
                    preheat_ready=ready, current_temperature=None,
                    high_temperature=settings.high_temperature,
                    low_temperature=settings.low_temperature,
                    eco_temperature=settings.eco_temperature,
                    active_hvac_mode=settings.active_hvac_mode, evaluated_at=at,
                ))
                if result.hvac_mode == "off":
                    return None
                if result.hvac_mode is None and climate is not None and climate.state == "off":
                    return None
                if smart_target is not None and result.status not in (
                    ControllerStatus.HEAT_BLAST, ControllerStatus.MANUAL_OVERRIDE
                ):
                    # Replan at least every five minutes; hold this policy only
                    # for the supported forecast horizon, never beyond it.
                    return smart_target(at)
                return _celsius(result.target_temperature, unit)

            prediction = self.thermal_model.forecast(now, indoor, inputs.outdoor_temperature, target)
            self._cost_forecast = [{"datetime": point.at.isoformat(), "heating_demand_percent": point.demand * 100}
                                   for point in prediction.points]
            if config.get(CONF_GAS_RATE) and config.get(CONF_GAS_CONSUMPTION):
                # Tomorrow means the complete local calendar day, including DST changes.
                zone = dt_util.get_time_zone(self.hass.config.time_zone)
                local = now.astimezone(zone)
                end = (local + timedelta(days=2)).replace(hour=0, minute=0, second=0, microsecond=0)
                end_utc = end.astimezone(now.tzinfo)
                hours = math.ceil((end_utc - now).total_seconds() / 300) / 12
                override_valid = True
                try:
                    extended = self.thermal_model.forecast(now, indoor, inputs.outdoor_temperature, target, hours=hours)
                    self._cost_forecast = [{"datetime": point.at.isoformat(), "heating_demand_percent": point.demand * 100}
                                           for point in extended.points]
                except ValueError:
                    # Preserve the valid 24h chart; a partial tomorrow must not look like a daily total.
                    pass
            if issue_accuracy and self.forecast_accuracy.issue(now, prediction.points):
                self._copy_learning_state()
                try:
                    await self.store.async_save(self.persistent_state)
                except Exception:
                    _LOGGER.warning("Unable to persist forecast accuracy", exc_info=True)
            def native(value: float | None) -> float | None:
                return (round(TemperatureConverter.convert(value, "°C", unit), 2)
                        if value is not None else None)
            assumptions = [
                "Predicts the thermostat location, not every room.",
                "Current occupancy is held until any known home arrival; future departures are unknown.",
                "Solar gains, open windows and changes in boiler output are not explicitly modelled.",
                *inputs.assumptions,
            ]
            assumptions.append("Heating demand is a reported control signal, not measured boiler power.")
            if not prediction.lag_learned:
                assumptions.append("Heating response delay is still being learnt.")
            if not prediction.demand_model_learned:
                assumptions.append("Future demand uses an initial estimate until enough setpoint changes are observed.")
            if activity is None:
                assumptions.append("Heating activity/demand is missing or stale: learning is paused.")
            return base | {
                "forecast": [{"datetime": point.at.isoformat(),
                              "temperature": native(point.indoor_temperature),
                              "outdoor_temperature": native(point.outdoor_temperature),
                              "target_temperature": native(point.target_temperature),
                              "heating_demand_percent": round(point.demand * 100, 1)}
                             for point in prediction.points],
                "confidence": ("learned" if prediction.confidence >= 0.5
                               and prediction.lag_learned and prediction.demand_model_learned
                               and activity is not None else "learning"),
                "model_score": prediction.confidence,
                "heating_response_minutes": prediction.lag_minutes,
                "heating_response_learned": prediction.lag_learned,
                "demand_model_learned": prediction.demand_model_learned,
                "current_heating_demand_percent": (round(float(activity) * 100, 1)
                                                   if activity is not None else None),
                "forecast_accuracy": self.forecast_accuracy.metrics(),
                "learned_samples": prediction.learned_samples,
                "model_residual_c_per_hour": prediction.residual_rate_c_per_hour,
                "heat_gain_c_per_hour": prediction.heat_gain_c_per_hour,
                "heat_loss_per_hour": prediction.heat_loss_per_hour,
                "assumptions": assumptions,
            }
        except Exception:
            _LOGGER.warning("Indoor temperature forecast unavailable", exc_info=True)
            return base | {"reason": "Forecast inputs could not be processed"}

    async def _async_apply_commands(
        self, climate: State | None, desired_mode: str | None, desired_target: float | None
    ) -> None:
        """Apply policy intent in mode-then-temperature order."""
        if not self.persistent_state.auto_mode and desired_mode is not None:
            desired_mode, desired_target = HVACMode.OFF, None
        priority = self._smart_input_signature(include_climate=False)
        self._command_error = None
        if (
            self._commands_stopped
            or self._shutting_down
            or self.hass.state is not CoreState.running
            or not self._available(climate)
            or desired_mode is None
            or climate is None
        ):
            return
        entity_id = self.config[CONF_CLIMATE]
        if climate.state != desired_mode and not self._command_pending(
            self._hvac_command_records, desired_mode
        ):
            self._prune_command_records(self._hvac_command_records)
            mode_record = self._new_command_record(desired_mode)
            self._hvac_command_records.append(mode_record)
            self._sync_pending_views()
            try:
                await self.hass.services.async_call(
                    CLIMATE_DOMAIN,
                    "set_hvac_mode",
                    {"entity_id": entity_id, "hvac_mode": desired_mode},
                    blocking=True,
                    context=mode_record.context,
                )
            except HomeAssistantError as err:
                self._retire_command_record(self._hvac_command_records, mode_record)
                _LOGGER.error("Failed setting HVAC mode for %s: %s", entity_id, err)
                self._command_error = "The thermostat did not accept its heating mode. The existing schedule will retry."
                return
            except BaseException:
                self._retire_command_record(self._hvac_command_records, mode_record)
                raise

        if (
            self._commands_stopped
            or self._shutting_down
            or desired_mode == HVACMode.OFF
            or desired_target is None
        ):
            return
        # The mode service may have changed the target or device availability.
        live_climate = self._climate_state()
        if priority != self._smart_input_signature(include_climate=False):
            result = self._fallback_result(dt_util.utcnow())
            await self._async_apply_commands(live_climate, result.hvac_mode, result.target_temperature)
            return
        if self._available(live_climate) and live_climate is not None:
            await self._async_apply_target_command(live_climate, desired_target)

    async def _async_apply_target_command(self, climate: State, desired_target: float) -> None:
        """Write one target with bounded provenance when it differs from live state."""
        if (
            self._commands_stopped
            or self._shutting_down
            or self.hass.state is not CoreState.running
        ):
            return
        target = _finite_float(desired_target)
        current_target = _finite_float(climate.attributes.get(ATTR_TEMPERATURE))
        if target is None or _targets_match(climate, target, current_target):
            return
        entity_id = self.config[CONF_CLIMATE]
        self._prune_command_records(self._target_command_records)
        if self._command_pending(self._target_command_records, target):
            return
        target_record = self._new_command_record(target)
        self._target_command_records.append(target_record)
        self._sync_pending_views()
        try:
            await self.hass.services.async_call(
                CLIMATE_DOMAIN,
                "set_temperature",
                {"entity_id": entity_id, ATTR_TEMPERATURE: target},
                blocking=True,
                context=target_record.context,
            )
        except HomeAssistantError as err:
            self._retire_command_record(self._target_command_records, target_record)
            _LOGGER.error("Failed setting target temperature for %s: %s", entity_id, err)
            self._command_error = "The thermostat did not accept its target temperature. The existing schedule will retry."
        except BaseException:
            self._retire_command_record(self._target_command_records, target_record)
            raise

    async def async_set_auto_mode(self, enabled: bool) -> None:
        """Persist strict Auto mode and immediately re-evaluate."""
        if not isinstance(enabled, bool):
            raise TypeError("enabled must be a bool")
        await self.async_set_controller_mode("auto" if enabled else "off")

    async def async_set_controller_mode(self, mode: str) -> None:
        """Set the explicit operating mode, independently of preview planning."""
        if mode not in ("auto", "away", "off"):
            raise ValueError("Unsupported controller mode")
        self.persistent_state.auto_mode = mode != "off"
        self.persistent_state.forced_away = mode == "away"
        self.persistent_state.away_seen_absent = []
        self.persistent_state.away_selected_at = dt_util.utcnow() if mode == "away" else None
        self._set_manual_override(None, None)
        self.persistent_state.blast_until = None
        if mode == "away":
            for person in self.config[CONF_PERSONS]:
                self._away_observe_person(person, self.hass.states.get(person))
        await self.store.async_save(self.persistent_state)
        self._override_dirty = False
        await self.async_refresh()

    async def async_start_blast(self) -> bool:
        """Start or restart the fixed-duration heat blast."""
        if self.controller_mode != "auto":
            return False
        self._set_manual_override(None, None)
        self.persistent_state.blast_until = dt_util.utcnow() + timedelta(minutes=HEAT_BLAST_MINUTES)
        await self.store.async_save(self.persistent_state)
        self._override_dirty = False
        await self.async_refresh()
        return True

    async def async_reset_learning(self) -> None:
        """Clear the learned model and immediately re-evaluate."""
        self.learner.reset()
        self.heating_cost_model = HeatingCostModel()
        self.thermal_model = ThermalForecaster()
        self.forecast_accuracy = ForecastAccuracyTracker()
        self.persistent_state.smart_control_enabled = False
        self._smart_healthy_since = None
        self._preview_cache = None
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
        capabilities, error = _climate_capabilities(climate, self.hass.config.units.temperature_unit)
        if error or capabilities is None:
            raise HomeAssistantError("The configured climate capabilities are invalid")
        candidate = _default_settings(self.hass, capabilities) | self.config
        candidate[key] = value
        errors, normalized = _validate_settings(candidate, capabilities)
        if errors or normalized is None:
            raise HomeAssistantError(f"Invalid setting: {next(iter(errors.values()))}")
        options = dict(self.entry.options) | {
            name: value for name, value in normalized.items()
            if name in _SETTING_KEYS or name in self.config
        }
        self._set_manual_override(None, None)
        await self.store.async_save(self.persistent_state)
        self._override_dirty = False
        await self._async_update_options_in_place(options)
