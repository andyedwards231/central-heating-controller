# Central Heating Controller Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a copy-ready Home Assistant custom integration that intelligently controls one thermostat using schedule, occupancy, cancellable travel pre-heating, adaptive heating-rate learning, strict Auto-off, manual overrides, and Heat Blast.

**Architecture:** A config-entry-owned `ControllerCoordinator` is the sole thermostat command writer. Pure policy, matching, arrival parsing, and learning modules keep decisions deterministic and readily testable; thin Home Assistant entity platforms expose controls and status on one device. Persistent runtime state uses Home Assistant `Store`, while config and editable settings live in config-entry data/options.

**Tech Stack:** Python 3.13, Home Assistant custom-component APIs, `pytest`, `pytest-homeassistant-custom-component`, `freezegun`, Ruff, and mypy-compatible type hints.

---

## File Map

- `custom_components/central_heating_controller/manifest.json`: integration metadata and config-flow declaration.
- `custom_components/central_heating_controller/const.py`: domain, config keys, defaults, platforms, and storage constants.
- `custom_components/central_heating_controller/models.py`: immutable policy inputs/results, status enum, settings, and persistent-state dataclasses.
- `custom_components/central_heating_controller/policy.py`: pure priority state machine.
- `custom_components/central_heating_controller/travel.py`: destination normalization, arrival parsing, and pre-heat threshold calculation.
- `custom_components/central_heating_controller/learning.py`: non-overlapping heating observations and EWMA model.
- `custom_components/central_heating_controller/storage.py`: validated Home Assistant `Store` serialization.
- `custom_components/central_heating_controller/coordinator.py`: listeners, snapshots, manual-override recognition, persistence, minute refresh, and climate service calls.
- `custom_components/central_heating_controller/config_flow.py`: multi-step setup and options flow.
- `custom_components/central_heating_controller/entity.py`: common controller entity and device information.
- `custom_components/central_heating_controller/switch.py`: Auto Mode.
- `custom_components/central_heating_controller/button.py`: Heat Blast.
- `custom_components/central_heating_controller/number.py`: five editable settings.
- `custom_components/central_heating_controller/sensor.py`: status, effective target, learned rate, and pre-heat time.
- `custom_components/central_heating_controller/diagnostics.py`: privacy-redacted config/runtime diagnostics.
- `custom_components/central_heating_controller/repairs.py`: missing-input repair issue creation/deletion.
- `custom_components/central_heating_controller/__init__.py`: entry setup, forwarding, reload, and unload.
- `custom_components/central_heating_controller/strings.json`: config/option forms, validation errors, repairs, and entity translations.
- `custom_components/central_heating_controller/translations/en.json`: English translation copy matching `strings.json`.
- `tests/conftest.py`: Home Assistant test fixtures and enable-custom-integrations fixture.
- `tests/test_policy.py`: priority and status tests.
- `tests/test_travel.py`: destination, arrival, and threshold tests.
- `tests/test_learning.py`: sample filtering, trust, smoothing, and warm-up tests.
- `tests/test_config_flow.py`: setup, validation, duplicate, and options tests.
- `tests/test_coordinator.py`: event, service-call, override, unavailable, and restoration tests.
- `tests/test_entities.py`: device/entity registration and control tests.
- `tests/test_diagnostics.py`: privacy redaction tests.
- `pyproject.toml`: development and test tooling.
- `README.md`: installation, setup, behavior, dashboard use, troubleshooting, and safety notice.

## Task 1: Project Skeleton and Test Harness

**Files:**
- Create: `pyproject.toml`
- Create: `custom_components/central_heating_controller/manifest.json`
- Create: `custom_components/central_heating_controller/const.py`
- Create: `custom_components/central_heating_controller/__init__.py`
- Create: `tests/__init__.py`
- Create: `tests/conftest.py`
- Test: `tests/test_manifest.py`

- [ ] **Step 1: Write the failing manifest test**

```python
import json
from pathlib import Path


def test_manifest_declares_copy_ready_config_flow() -> None:
    manifest = json.loads(
        Path("custom_components/central_heating_controller/manifest.json").read_text()
    )
    assert manifest["domain"] == "central_heating_controller"
    assert manifest["name"] == "Central Heating Controller"
    assert manifest["config_flow"] is True
    assert manifest["version"] == "0.1.0"
    assert manifest["iot_class"] == "local_push"
```

- [ ] **Step 2: Run the test and verify RED**

Run: `python3 -m pytest tests/test_manifest.py -q`

Expected: FAIL because `manifest.json` does not exist.

- [ ] **Step 3: Add the minimal package metadata and constants**

Create a manifest with domain/name/config-flow/version/iot-class, empty requirements/codeowners, and `integration_type: service`. Add `pyproject.toml` with pytest paths, asyncio auto mode, Ruff line length 100, and development dependencies for Home Assistant test support. Define these stable constants in `const.py`:

```python
DOMAIN = "central_heating_controller"
NAME = "Central Heating Controller"
PLATFORMS = ("switch", "button", "number", "sensor")
STORAGE_VERSION = 1
STORAGE_KEY = f"{DOMAIN}.{{entry_id}}"

CONF_CLIMATE = "climate_entity"
CONF_PERSONS = "person_entities"
CONF_HOME_ZONE = "home_zone_entity"
CONF_SCHEDULE = "schedule_entity"
CONF_DESTINATION = "destination_entity"
CONF_ARRIVAL_TIME = "arrival_time_entity"
CONF_DESTINATION_HOME_VALUE = "destination_home_value"
CONF_ACTIVE_HVAC_MODE = "active_hvac_mode"
CONF_HIGH_TEMP = "high_temperature"
CONF_LOW_TEMP = "low_temperature"
CONF_ECO_TEMP = "eco_temperature"
CONF_FALLBACK_MINUTES = "fallback_warmup_minutes"
CONF_MAX_WARMUP_MINUTES = "maximum_warmup_minutes"

DEFAULT_HIGH_C = 20.0
DEFAULT_LOW_C = 17.0
DEFAULT_ECO_C = 14.0
DEFAULT_HIGH_F = 68.0
DEFAULT_LOW_F = 63.0
DEFAULT_ECO_F = 57.0
DEFAULT_FALLBACK_MINUTES = 60
DEFAULT_MAX_WARMUP_MINUTES = 180
HEAT_BLAST_MINUTES = 60
```

Keep `__init__.py` importable with only `DOMAIN` exported until entry setup is added later. Add `tests/conftest.py` with `pytest_plugins = "pytest_homeassistant_custom_component"` and an autouse `enable_custom_integrations` fixture.

- [ ] **Step 4: Run the test and verify GREEN**

Run: `python3 -m pytest tests/test_manifest.py -q`

Expected: `1 passed`.

- [ ] **Step 5: Commit the skeleton**

```bash
git add pyproject.toml custom_components tests
git commit -m "chore: scaffold central heating integration"
```

## Task 2: Pure Control Policy

**Files:**
- Create: `custom_components/central_heating_controller/models.py`
- Create: `custom_components/central_heating_controller/policy.py`
- Test: `tests/test_policy.py`

- [ ] **Step 1: Write one parametrized failing test for the complete priority table**

```python
from datetime import datetime, timezone

import pytest

from custom_components.central_heating_controller.models import (
    ControlInputs,
    ControllerStatus,
)
from custom_components.central_heating_controller.policy import evaluate_policy


BASE = dict(
    thermostat_available=True,
    auto_mode=True,
    blast_active=False,
    manual_override_target=None,
    occupied=True,
    schedule_high=False,
    journey_home=False,
    preheat_ready=False,
    current_temperature=16.0,
    high_temperature=20.0,
    low_temperature=17.0,
    eco_temperature=14.0,
    active_hvac_mode="heat",
    evaluated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
)


@pytest.mark.parametrize(
    ("changes", "status", "target", "mode"),
    [
        ({"thermostat_available": False}, ControllerStatus.UNAVAILABLE, None, None),
        ({"auto_mode": False}, ControllerStatus.OFF, None, "off"),
        ({"blast_active": True}, ControllerStatus.HEAT_BLAST, 20.0, "heat"),
        ({"manual_override_target": 18.5}, ControllerStatus.MANUAL_OVERRIDE, 18.5, None),
        (
            {"occupied": False, "journey_home": True, "preheat_ready": True},
            ControllerStatus.PREHEATING,
            20.0,
            "heat",
        ),
        ({"occupied": False}, ControllerStatus.AWAY, 14.0, "heat"),
        ({"schedule_high": True}, ControllerStatus.HIGH, 20.0, "heat"),
        ({}, ControllerStatus.LOW, 17.0, "heat"),
    ],
)
def test_policy_priority(changes, status, target, mode) -> None:
    result = evaluate_policy(ControlInputs(**(BASE | changes)))
    assert (result.status, result.target_temperature, result.hvac_mode) == (
        status,
        target,
        mode,
    )
```

- [ ] **Step 2: Run the test and verify RED**

Run: `python3 -m pytest tests/test_policy.py -q`

Expected: collection FAIL because the models and policy modules do not exist.

- [ ] **Step 3: Implement immutable models and the strict policy**

Define `ControllerStatus(StrEnum)` with values `high`, `low`, `pre_heating`, `away`, `off`, `heat_blast`, `manual_override`, and `unavailable`. Define frozen dataclasses `ControllerSettings`, `ControlInputs`, and `ControlResult`. Implement `evaluate_policy(inputs)` as one ordered sequence matching the test and include a concise machine-readable `reason` for each result. Manual override returns its preserved target as the effective target but `hvac_mode=None`, explicitly meaning “do not command.”

```python
def evaluate_policy(inputs: ControlInputs) -> ControlResult:
    if not inputs.thermostat_available:
        return ControlResult(ControllerStatus.UNAVAILABLE, None, None, "thermostat_unavailable")
    if not inputs.auto_mode:
        return ControlResult(ControllerStatus.OFF, None, "off", "auto_mode_off")
    if inputs.blast_active:
        return ControlResult(ControllerStatus.HEAT_BLAST, inputs.high_temperature, inputs.active_hvac_mode, "blast_active")
    if inputs.manual_override_target is not None:
        return ControlResult(ControllerStatus.MANUAL_OVERRIDE, inputs.manual_override_target, None, "external_target_preserved")
    if not inputs.occupied and inputs.journey_home and inputs.preheat_ready:
        return ControlResult(ControllerStatus.PREHEATING, inputs.high_temperature, inputs.active_hvac_mode, "arrival_within_warmup")
    if not inputs.occupied:
        return ControlResult(ControllerStatus.AWAY, inputs.eco_temperature, inputs.active_hvac_mode, "home_unoccupied")
    if inputs.schedule_high:
        return ControlResult(ControllerStatus.HIGH, inputs.high_temperature, inputs.active_hvac_mode, "schedule_on")
    return ControlResult(ControllerStatus.LOW, inputs.low_temperature, inputs.active_hvac_mode, "schedule_off_or_unavailable")
```

- [ ] **Step 4: Run policy tests and verify GREEN**

Run: `python3 -m pytest tests/test_policy.py -q`

Expected: all eight parameter cases pass.

- [ ] **Step 5: Commit policy**

```bash
git add custom_components/central_heating_controller/models.py custom_components/central_heating_controller/policy.py tests/test_policy.py
git commit -m "feat: add heating control policy"
```

## Task 3: Travel Matching and Arrival Thresholds

**Files:**
- Create: `custom_components/central_heating_controller/travel.py`
- Test: `tests/test_travel.py`

- [ ] **Step 1: Write failing tests for normalized destinations and ETA formats**

```python
from datetime import datetime, timezone

import pytest

from custom_components.central_heating_controller.travel import (
    destination_is_home,
    parse_arrival_time,
    preheat_timing,
)


@pytest.mark.parametrize("destination", ["home", "Home", "zone.home", "My_Home", "my home"])
def test_destination_matches_zone_forms(destination) -> None:
    assert destination_is_home(destination, "zone.home", "My Home", None)


def test_optional_exact_destination_value_is_additive() -> None:
    assert destination_is_home("HOME_ADDRESS", "zone.home", "My Home", "HOME_ADDRESS")


@pytest.mark.parametrize(
    "raw",
    ["2026-01-01T12:30:00+00:00", 1767270600, "1767270600"],
)
def test_arrival_formats(raw) -> None:
    assert parse_arrival_time(raw, timezone.utc) == datetime(2026, 1, 1, 12, 30, tzinfo=timezone.utc)


def test_bad_or_past_arrival_means_immediate_preheat() -> None:
    now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    timing = preheat_timing("unavailable", now, warmup_minutes=60, local_tz=timezone.utc)
    assert timing.ready is True
    assert timing.arrival is None
    assert timing.start is None


def test_future_arrival_waits_until_calculated_start() -> None:
    now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    timing = preheat_timing("2026-01-01T14:00:00+00:00", now, 60, timezone.utc)
    assert timing.ready is False
    assert timing.start == datetime(2026, 1, 1, 13, 0, tzinfo=timezone.utc)
```

- [ ] **Step 2: Run travel tests and verify RED**

Run: `python3 -m pytest tests/test_travel.py -q`

Expected: collection FAIL because `travel.py` does not exist.

- [ ] **Step 3: Implement normalization, parsing, and timing**

Implement `_normalize_destination` by trimming, case-folding, removing a leading `zone.`, and converting runs of spaces/underscores/hyphens to one underscore. `destination_is_home` compares the normalized state to normalized zone entity ID, entity object ID, friendly name, and optional override. Treat `unknown`, `unavailable`, empty, and `None` as no match.

Implement `parse_arrival_time(raw, local_tz)` with Home Assistant datetime helpers: numeric values and all-digit strings are Unix timestamps; other strings use `dt_util.parse_datetime`; naive parsed values receive `local_tz`; results are converted to UTC. Return `None` on invalid values. Define frozen `PreheatTiming(ready, arrival, start)`. `preheat_timing` returns immediate readiness for invalid/past arrival values and otherwise compares `now >= arrival - timedelta(minutes=warmup_minutes)`.

- [ ] **Step 4: Run travel tests and verify GREEN**

Run: `python3 -m pytest tests/test_travel.py -q`

Expected: all travel tests pass.

- [ ] **Step 5: Commit travel helpers**

```bash
git add custom_components/central_heating_controller/travel.py tests/test_travel.py
git commit -m "feat: add journey preheating calculations"
```

## Task 4: Adaptive Heating-Rate Learning

**Files:**
- Create: `custom_components/central_heating_controller/learning.py`
- Test: `tests/test_learning.py`

- [ ] **Step 1: Write failing tests for samples, EWMA, trust, and warm-up**

```python
from datetime import datetime, timedelta, timezone

import pytest

from custom_components.central_heating_controller.learning import HeatingRateLearner


NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def accepted_sample(learner, start_temp, end_temp, offset=0) -> None:
    start = NOW + timedelta(hours=offset)
    learner.observe(start, start_temp, 21.0, heating=True)
    learner.observe(start + timedelta(minutes=30), end_temp, 21.0, heating=True)


def test_requires_three_non_overlapping_samples_and_smooths_at_point_three() -> None:
    learner = HeatingRateLearner()
    accepted_sample(learner, 18.0, 19.0, 0)  # 2.0 C/h
    accepted_sample(learner, 18.0, 18.5, 1)  # 1.0 C/h => 1.7
    accepted_sample(learner, 18.0, 19.5, 2)  # 3.0 C/h => 2.09
    assert learner.sample_count == 3
    assert learner.trusted is True
    assert learner.rate == pytest.approx(2.09)


def test_rejects_short_falling_target_reached_and_implausible_windows() -> None:
    learner = HeatingRateLearner()
    learner.observe(NOW, 18.0, 20.0, heating=True)
    learner.observe(NOW + timedelta(minutes=10), 19.0, 20.0, heating=True)
    learner.observe(NOW + timedelta(minutes=30), 17.0, 20.0, heating=True)
    learner.observe(NOW + timedelta(hours=1), 20.0, 20.0, heating=True)
    assert learner.sample_count == 0


def test_warmup_uses_fallback_until_trusted_then_clamps_model() -> None:
    learner = HeatingRateLearner(rate=1.0, sample_count=2)
    assert learner.warmup_minutes(16.0, 20.0, 60, 180) == 60
    learner.sample_count = 3
    assert learner.warmup_minutes(16.0, 20.0, 60, 180) == 180
    assert learner.warmup_minutes(20.0, 20.0, 60, 180) == 0
```

- [ ] **Step 2: Run learning tests and verify RED**

Run: `python3 -m pytest tests/test_learning.py -q`

Expected: collection FAIL because `learning.py` does not exist.

- [ ] **Step 3: Implement the learner**

Create a learner with persisted `rate` and `sample_count` plus transient window start/time. `observe` resets its window when heating stops, values are missing, target is reached, elapsed time is under 15 minutes, the temperature does not rise, or the implied rate exceeds 10 degrees/hour. Once 15 minutes elapse, accept a rate in `(0, 10]`, update `rate = sample` for the first sample or `0.3 * sample + 0.7 * rate`, increment count, and reset the window so samples never overlap. `warmup_minutes` returns zero at/above high, fallback before three samples, and `ceil((high-current)/rate*60)` thereafter, clamped to `[0, maximum]`.

- [ ] **Step 4: Run learning tests and verify GREEN**

Run: `python3 -m pytest tests/test_learning.py -q`

Expected: all learner tests pass.

- [ ] **Step 5: Commit learner**

```bash
git add custom_components/central_heating_controller/learning.py tests/test_learning.py
git commit -m "feat: learn adaptive heating rate"
```

## Task 5: Config Flow and Validation

**Files:**
- Create: `custom_components/central_heating_controller/config_flow.py`
- Create: `custom_components/central_heating_controller/strings.json`
- Create: `custom_components/central_heating_controller/translations/en.json`
- Test: `tests/test_config_flow.py`

- [ ] **Step 1: Write failing flow tests**

Use Home Assistant flow tests to assert:

```python
async def test_user_flow_collects_entities_then_settings(hass, climate_state) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_CLIMATE: "climate.hallway",
            CONF_PERSONS: ["person.andy"],
            CONF_HOME_ZONE: "zone.home",
            CONF_SCHEDULE: "schedule.heating",
            CONF_DESTINATION: "sensor.car_destination",
            CONF_ARRIVAL_TIME: "sensor.car_arrival",
        },
    )
    assert result["step_id"] == "settings"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_ACTIVE_HVAC_MODE: "heat",
            CONF_HIGH_TEMP: 20.0,
            CONF_LOW_TEMP: 17.0,
            CONF_ECO_TEMP: 14.0,
            CONF_FALLBACK_MINUTES: 60,
            CONF_MAX_WARMUP_MINUTES: 180,
            CONF_DESTINATION_HOME_VALUE: "",
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Central Heating Controller"
```

Add separate tests asserting rejection of: duplicate climate entries, a climate without target-temperature support, an unsupported active HVAC mode, values outside thermostat min/max, `high < low`, `low < eco`, and fallback greater than maximum. Add a Fahrenheit-default test. Add an options-flow test that preserves entity selections, updates settings, and emits a reset-learning flag only for the runtime action.

- [ ] **Step 2: Run config-flow tests and verify RED**

Run: `python3 -m pytest tests/test_config_flow.py -q`

Expected: collection or flow setup FAIL because `config_flow.py` and translations do not exist.

- [ ] **Step 3: Implement the multi-step flow**

Implement `CentralHeatingControllerConfigFlow(ConfigFlow, domain=DOMAIN)` with `VERSION = 1`. The `user` form uses entity selectors constrained to `climate`, `person` with `multiple=True`, `zone`, and `schedule`; destination accepts any entity; arrival accepts any entity and is optional. After the first submission:

- Verify all required states exist.
- Verify `supported_features` contains `ClimateEntityFeature.TARGET_TEMPERATURE`.
- Abort with `already_configured` when another entry uses the climate entity.
- Read `hvac_modes`, remove `off`, and default to `heat` or the first remaining mode.
- Read `min_temp`/`max_temp`; clamp unit-specific defaults.

The settings step uses select/number/text selectors and validates all inequalities. Store entity IDs in `entry.data` and editable values in `entry.options`. Implement `async_get_options_flow`; its form allows every selection/setting to change, re-runs the same validation, updates options, and calls `runtime_data.coordinator.async_reset_learning()` only when the reset checkbox is submitted. Use translation keys for all labels and errors.

- [ ] **Step 4: Run config-flow tests and verify GREEN**

Run: `python3 -m pytest tests/test_config_flow.py -q`

Expected: all setup, validation, duplicate, unit, and options cases pass.

- [ ] **Step 5: Commit the config flow**

```bash
git add custom_components/central_heating_controller/config_flow.py custom_components/central_heating_controller/strings.json custom_components/central_heating_controller/translations/en.json tests/test_config_flow.py
git commit -m "feat: add guided integration setup"
```

## Task 6: Persistence and Coordinator Core

**Files:**
- Create: `custom_components/central_heating_controller/storage.py`
- Create: `custom_components/central_heating_controller/coordinator.py`
- Modify: `custom_components/central_heating_controller/models.py`
- Modify: `custom_components/central_heating_controller/__init__.py`
- Test: `tests/test_coordinator.py`

- [ ] **Step 1: Write failing coordinator tests for baseline control and strict Off**

Create a configured entry and states for climate/person/zone/schedule/destination. Patch `hass.services.async_call` and assert:

```python
async def test_occupied_schedule_on_commands_heat_and_high(hass, setup_entry, service_calls):
    coordinator = setup_entry.runtime_data.coordinator
    await coordinator.async_refresh()
    assert coordinator.data.status is ControllerStatus.HIGH
    assert ("climate", "set_hvac_mode", {"entity_id": "climate.hallway", "hvac_mode": "heat"}) in service_calls
    assert ("climate", "set_temperature", {"entity_id": "climate.hallway", "temperature": 20.0}) in service_calls


async def test_auto_off_cancels_blast_and_only_commands_off(hass, setup_entry):
    coordinator = setup_entry.runtime_data.coordinator
    await coordinator.async_start_blast()
    await coordinator.async_set_auto_mode(False)
    assert coordinator.data.status is ControllerStatus.OFF
    assert coordinator.persistent_state.blast_until is None
```

Add tests for Away, Low, Pre-heating, destination cancellation, invalid ETA immediate pre-heating, thermostat unavailable, all-people-unknown, schedule unavailable, redundant command suppression, service failure retry, and config-entry unload removing listeners.

- [ ] **Step 2: Run coordinator tests and verify RED**

Run: `python3 -m pytest tests/test_coordinator.py -q`

Expected: FAIL because coordinator and storage do not exist and entry setup has no runtime data.

- [ ] **Step 3: Implement validated storage and runtime models**

Add `PersistentState` with `auto_mode=True`, optional UTC `blast_until`, optional `manual_override_target`, optional `manual_override_fingerprint`, optional learned rate, and sample count. `ControllerState` holds current result plus arrival/start/rate/current temperature. `ControllerRuntimeData` holds coordinator.

Wrap `Store[dict[str, Any]]` in `ControllerStore`. `async_load` accepts only correctly typed finite numeric values, non-negative sample counts, parseable UTC datetimes, and a fingerprint list of JSON primitives; invalid fields fall back independently. `async_save` writes schema version 1 fields. The key is `central_heating_controller.<entry_id>`.

- [ ] **Step 4: Implement coordinator snapshot and command flow**

Subclass `DataUpdateCoordinator[ControllerState]` with a one-minute `update_interval`. On `async_setup`, load persistent state, construct `HeatingRateLearner`, subscribe with `async_track_state_change_event` to climate/person/zone/schedule/destination/optional ETA, register one cleanup callback per subscription, and call `async_config_entry_first_refresh()`.

Build each snapshot as follows:

- Thermostat available unless missing/`unknown`/`unavailable`.
- Occupancy uses `zone.in_zone` when coordinates are present, then normalized state fallback; all unknown means occupied.
- Schedule is high only for state `on`.
- Destination match uses `travel.destination_is_home`.
- Warm-up comes from the learner and `preheat_timing`.
- Blast is active only for a future stored deadline; clear expired deadlines.
- Policy fingerprint is `(occupied, schedule_high, journey_home, preheat_ready, high, low, eco, active_hvac_mode)`.
- Keep a restored manual override only while its stored target matches the live thermostat target and its stored fingerprint matches the live fingerprint.

After `evaluate_policy`, publish data before commands. When a command is needed, set `pending_hvac_mode`/`pending_target`, call `climate.set_hvac_mode` first when required, then `climate.set_temperature`, and suppress equal values using thermostat target step or a 0.01 fallback tolerance. Catch `HomeAssistantError`, log the service and entity once per failed evaluation, set neither integration unavailable nor manual override, and allow the next refresh to retry.

- [ ] **Step 5: Implement lifecycle and runtime actions**

In `__init__.py`, define a typed config entry alias, create/setup coordinator, assign runtime data, forward `switch/button/number/sensor`, and add an update listener that reloads the entry. Unload platforms first, then coordinator listeners.

Implement coordinator methods:

- `async_set_auto_mode(enabled)`: cancel blast and manual override when disabling, persist, refresh.
- `async_start_blast()`: no-op when Auto is off; otherwise clear manual override, store `utcnow()+60 minutes`, persist, refresh.
- `async_update_setting(key, value)`: validate via shared config validator, update entry options, clear manual override, and refresh/reload.
- `async_reset_learning()`: reset learner and persisted learning fields, save, refresh.

- [ ] **Step 6: Run coordinator tests and verify GREEN**

Run: `python3 -m pytest tests/test_coordinator.py -q`

Expected: all core, failure, transition, and unload tests pass.

- [ ] **Step 7: Commit coordinator core**

```bash
git add custom_components/central_heating_controller custom_components/central_heating_controller/__init__.py tests/test_coordinator.py
git commit -m "feat: coordinate thermostat control"
```

## Task 7: Manual Override and Restart Timers

**Files:**
- Modify: `custom_components/central_heating_controller/coordinator.py`
- Modify: `custom_components/central_heating_controller/storage.py`
- Test: `tests/test_coordinator.py`

- [ ] **Step 1: Add failing override and restoration tests**

Test these independently:

```python
async def test_external_target_is_preserved_until_policy_fingerprint_changes(...):
    # Coordinator acknowledgement to 17 must not create an override.
    # External climate target 18.5 must create MANUAL_OVERRIDE.
    # Current-temperature and minute refreshes must retain it.
    # Schedule off->on must clear it and command high.


async def test_destination_change_clears_override(...):
    # An external target is preserved while away.
    # Destination changing to home clears override only when journey/preheat policy changes.


async def test_blast_deadline_survives_restart_and_expires(...):
    # Stored future deadline restores HEAT_BLAST.
    # Advancing time beyond deadline recalculates ordinary policy.


async def test_restored_override_requires_same_target_and_fingerprint(...):
    # Mismatch discards stale override; exact match restores it.
```

- [ ] **Step 2: Run focused tests and verify RED**

Run: `python3 -m pytest tests/test_coordinator.py -k 'override or blast_deadline' -q`

Expected: FAIL because acknowledgement and fingerprint semantics are not complete.

- [ ] **Step 3: Implement acknowledgement and fingerprint semantics**

In the climate state-change listener, compare changed HVAC mode/target against pending command values. Clear a matching pending value and do not activate override. If the target differs from pending while Auto is on and no blast is active, store the external finite target and current policy fingerprint, persist, and refresh. HVAC-mode-only changes do not become target overrides and are corrected on the next normal evaluation.

Before policy evaluation, compare a live fingerprint with the override fingerprint. Clear/persist the override only when it differs; temperature readings and raw ETA changes that do not change `preheat_ready` are intentionally absent from the fingerprint. Auto changes, blast presses, and setting changes explicitly clear override through their runtime methods.

On restoration, validate blast deadline against current UTC time. Validate manual target against current thermostat target using target-step tolerance and validate the stored fingerprint against current policy. Discard only the stale field.

- [ ] **Step 4: Run focused and full coordinator tests and verify GREEN**

Run: `python3 -m pytest tests/test_coordinator.py -q`

Expected: all coordinator tests pass.

- [ ] **Step 5: Commit override semantics**

```bash
git add custom_components/central_heating_controller/coordinator.py custom_components/central_heating_controller/storage.py tests/test_coordinator.py
git commit -m "feat: preserve manual thermostat overrides"
```

## Task 8: Device Entities and Controls

**Files:**
- Create: `custom_components/central_heating_controller/entity.py`
- Create: `custom_components/central_heating_controller/switch.py`
- Create: `custom_components/central_heating_controller/button.py`
- Create: `custom_components/central_heating_controller/number.py`
- Create: `custom_components/central_heating_controller/sensor.py`
- Modify: `custom_components/central_heating_controller/strings.json`
- Modify: `custom_components/central_heating_controller/translations/en.json`
- Test: `tests/test_entities.py`

- [ ] **Step 1: Write failing entity registration/control tests**

After setup, assert one device with identifiers `{(DOMAIN, entry.entry_id)}` and exact name `Central Heating Controller`. Assert unique IDs and values for:

```text
<entry_id>_auto_mode
<entry_id>_heat_blast
<entry_id>_high_temperature
<entry_id>_low_temperature
<entry_id>_eco_temperature
<entry_id>_fallback_warmup_minutes
<entry_id>_maximum_warmup_minutes
<entry_id>_status
<entry_id>_effective_target_temperature
<entry_id>_learned_heating_rate
<entry_id>_preheat_start_time
```

Add service tests: turning Auto off invokes the coordinator and results in Off; turning on re-evaluates; pressing Heat Blast while off is a no-op and while on reports Heat blast; setting each number updates config options; invalid cross-number relationships raise `HomeAssistantError`. Assert status is `SensorDeviceClass.ENUM`, target/rate are measurement sensors with correct units, and pre-heat start is `SensorDeviceClass.TIMESTAMP`.

- [ ] **Step 2: Run entity tests and verify RED**

Run: `python3 -m pytest tests/test_entities.py -q`

Expected: FAIL because the platform modules do not exist.

- [ ] **Step 3: Implement shared device entity and platforms**

`ControllerEntity` subclasses `CoordinatorEntity[ControllerCoordinator]`, sets `_attr_has_entity_name = True`, and returns `DeviceInfo(identifiers={(DOMAIN, entry_id)}, name=NAME, entry_type=DeviceEntryType.SERVICE)`.

Use entity descriptions with translation keys. The switch reads `persistent_state.auto_mode` and delegates turn-on/off. The button delegates Heat Blast and remains available while off so a press can safely no-op. Number entities use climate native temperature unit/range/step for temperatures and minutes/5–360/step 5 for durations. Each number delegates through validated `async_update_setting`.

The enum status sensor exposes options in stable enum order and adds only non-`None` attributes: reason, current temperature, effective target, learned rate, manual override flag, arrival, and pre-heat start. The effective-target sensor is unavailable only when result target is `None`; learned rate is unavailable until trusted; pre-heat start is unavailable unless a valid timed home journey exists.

- [ ] **Step 4: Run entity tests and verify GREEN**

Run: `python3 -m pytest tests/test_entities.py -q`

Expected: all device, state, unit, and service tests pass.

- [ ] **Step 5: Commit entities**

```bash
git add custom_components/central_heating_controller tests/test_entities.py
git commit -m "feat: expose central heating device controls"
```

## Task 9: Repairs and Redacted Diagnostics

**Files:**
- Create: `custom_components/central_heating_controller/repairs.py`
- Create: `custom_components/central_heating_controller/diagnostics.py`
- Modify: `custom_components/central_heating_controller/coordinator.py`
- Modify: `custom_components/central_heating_controller/strings.json`
- Modify: `custom_components/central_heating_controller/translations/en.json`
- Test: `tests/test_diagnostics.py`
- Test: `tests/test_coordinator.py`

- [ ] **Step 1: Write failing privacy and repair tests**

Assert diagnostics retain non-sensitive settings/status but replace person IDs, destination/arrival IDs and values, zone coordinates, arrival timestamps, and manual override fingerprint with `REDACTED`. Assert a missing configured input creates issue ID `<entry_id>_<config_key>`, severity warning, fixable false; restoring/reconfiguring the entity deletes that issue. Assert missing thermostat commands nothing, missing zone conservatively follows schedule, missing schedule uses low, and missing travel inputs disable pre-heating.

- [ ] **Step 2: Run tests and verify RED**

Run: `python3 -m pytest tests/test_diagnostics.py tests/test_coordinator.py -k 'diagnostic or missing or repair' -q`

Expected: FAIL because diagnostics and repair helpers do not exist.

- [ ] **Step 3: Implement explicit redaction and repair lifecycle**

`async_get_config_entry_diagnostics` returns redacted entry data/options, controller status/reason, Auto state, blast-active boolean, learned sample count/rate, and required entity availability booleans. It never returns live coordinates, destination/arrival states, person or vehicle entity IDs, stored arrival/deadline timestamps, or fingerprint content.

Add `sync_missing_entity_issues(hass, entry, missing_keys)` using `issue_registry.async_create_issue`/`async_delete_issue`. Call it after each snapshot; generate translation placeholders containing only the input label, never the sensitive entity ID. Apply precisely the approved fallbacks before policy evaluation.

- [ ] **Step 4: Run diagnostics/repair and full tests and verify GREEN**

Run: `python3 -m pytest tests/test_diagnostics.py tests/test_coordinator.py -q`

Expected: all diagnostics, repairs, and fallback tests pass.

- [ ] **Step 5: Commit diagnostics and repairs**

```bash
git add custom_components/central_heating_controller tests/test_diagnostics.py tests/test_coordinator.py
git commit -m "feat: add diagnostics and missing entity repairs"
```

## Task 10: Documentation, Packaging, and Full Verification

**Files:**
- Create: `README.md`
- Modify: `custom_components/central_heating_controller/manifest.json`
- Modify: `custom_components/central_heating_controller/strings.json`
- Modify: `custom_components/central_heating_controller/translations/en.json`
- Test: all tests

- [ ] **Step 1: Write the README acceptance checklist as failing documentation tests**

Extend `tests/test_manifest.py` to assert the README contains headings/text for Installation, Schedule Helper, Setup, Statuses, Heat Blast, Manual Override, Adaptive Pre-heating, Troubleshooting, Removal, and the exact safety idea that the integration does not replace frost protection or manufacturer safety systems. Assert `strings.json == translations/en.json` for all shared keys and both parse as JSON.

- [ ] **Step 2: Run documentation tests and verify RED**

Run: `python3 -m pytest tests/test_manifest.py -q`

Expected: FAIL because README does not exist and translation completeness is not finalized.

- [ ] **Step 3: Write user-facing documentation and finalize metadata**

Document exact copy installation:

1. Copy `custom_components/central_heating_controller` into `<config>/custom_components/`.
2. Restart Home Assistant.
3. Open Settings → Devices & services → Add integration.
4. Search for Central Heating Controller.

Explain creating a `schedule.*` helper where On means high and Off means low; every setup field; status meanings; dashboard entities; destination matching; optional ETA behavior; the three-sample learning period; reset learning; cancellation when destination changes; override-clearing events; unavailable fallbacks; logs/diagnostics/repairs; safe removal; and the safety disclaimer. Keep manifest requirements empty and verify every key against the current Home Assistant custom integration manifest format.

- [ ] **Step 4: Run formatting and static checks**

Run: `python3 -m ruff check .`

Expected: no errors.

Run: `python3 -m ruff format --check .`

Expected: all files already formatted.

- [ ] **Step 5: Run the complete test suite**

Run: `python3 -m pytest -q`

Expected: all tests pass with no warnings originating from this integration.

- [ ] **Step 6: Verify the copy-ready tree and clean Git state**

Run: `find custom_components/central_heating_controller -maxdepth 2 -type f | sort`

Expected: all integration Python, manifest, strings, and English translation files from the file map are present, with no cache/generated files.

Run: `git status --short`

Expected before final commit: only intended Task 10 files are modified/untracked.

- [ ] **Step 7: Commit documentation and release-ready package**

```bash
git add README.md custom_components tests pyproject.toml
git commit -m "docs: finish copy-ready heating controller"
```

- [ ] **Step 8: Final verification from committed state**

Run: `python3 -m pytest -q && python3 -m ruff check . && git status --short`

Expected: tests pass, Ruff passes, and Git status is empty.

## Plan Self-Review

- Every approved state and priority is implemented in Tasks 2, 6, and 7.
- Schedule, occupancy, destination cancellation, optional ETA, and adaptive learning are covered in Tasks 3, 4, and 6.
- Auto-off, Heat Blast, manual override, persistence, and restart behavior are covered in Tasks 6–8.
- Every exposed entity and its status semantics are covered in Task 8.
- Failure fallbacks, repairs, and privacy are covered in Task 9.
- Copy installation and safety documentation are covered in Task 10.
- All production behavior follows explicit RED/GREEN steps before implementation.
