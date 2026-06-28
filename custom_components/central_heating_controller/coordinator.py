"""Home Assistant coordinator for central heating control."""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
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
from .models import (
    ControlInputs,
    ControllerSettings,
    ControllerState,
    JsonPrimitive,
    PersistentState,
    TemperatureCapabilities,
)
from .policy import evaluate_policy
from .repairs import sync_missing_entity_issues
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
    capabilities, error = _climate_capabilities(climate)
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
            self._retire_command_record(self._target_command_records, target_record)
            manual_target = self.persistent_state.manual_override_target
            if (
                manual_target is not None
                and not _targets_match(new_state, new_target, manual_target)
            ):
                self._stale_own_target_echo = True
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

        occupied = self._occupied(person_states, home_zone)
        schedule_high = schedule is not None and schedule.state == "on"
        journey_home = self._journey_home(destination, home_zone)

        arrival = None
        start = None
        preheat_ready = False
        if journey_home and not arrival_entity_missing:
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
        state = ControllerState(
            result=result,
            current_temperature=current_temperature,
            learned_rate=self.learner.rate,
            learned_trusted=self.learner.trusted,
            arrival_time=arrival,
            preheat_start_time=start,
            warmup_minutes=warmup_minutes,
            temperature_capabilities=temperature_capabilities,
        )
        if state != self.data:
            self.async_set_updated_data(state)
            self._skip_duplicate_listener_update = True
        await self._async_apply_commands(climate, result.hvac_mode, result.target_temperature)
        if (
            preserve_stale_echo
            and climate is not None
            and self.persistent_state.manual_override_target is not None
        ):
            await self._async_apply_target_command(
                climate, self.persistent_state.manual_override_target
            )
        self._stale_own_target_echo = False
        return state

    async def _async_apply_commands(
        self, climate: State | None, desired_mode: str | None, desired_target: float | None
    ) -> None:
        """Apply policy intent in mode-then-temperature order."""
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
        if climate.state != desired_mode:
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
        await self._async_apply_target_command(climate, desired_target)

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
        except BaseException:
            self._retire_command_record(self._target_command_records, target_record)
            raise

    async def async_set_auto_mode(self, enabled: bool) -> None:
        """Persist strict Auto mode and immediately re-evaluate."""
        if not isinstance(enabled, bool):
            raise TypeError("enabled must be a bool")
        self.persistent_state.auto_mode = enabled
        self._set_manual_override(None, None)
        if not enabled:
            self.persistent_state.blast_until = None
        await self.store.async_save(self.persistent_state)
        self._override_dirty = False
        await self.async_refresh()

    async def async_start_blast(self) -> bool:
        """Start or restart the fixed-duration heat blast."""
        if not self.persistent_state.auto_mode:
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
        self._set_manual_override(None, None)
        await self.store.async_save(self.persistent_state)
        self._override_dirty = False
        updated = self.hass.config_entries.async_update_entry(self.entry, options=options)
        if not updated:
            await self.async_refresh()
