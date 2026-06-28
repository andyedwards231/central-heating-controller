# Central Heating Controller Integration Design

## Purpose

Build a copy-ready Home Assistant custom integration that creates one device named **Central Heating Controller**. The integration manages one existing climate entity to balance comfort and energy use through a schedule, household occupancy, travel-to-home pre-heating, adaptive heating-rate learning, and explicit user overrides.

The integration is installed by copying `custom_components/central_heating_controller` into a Home Assistant configuration directory and restarting Home Assistant. It is configured entirely through the Home Assistant UI.

## Scope

The first release supports:

- One existing climate entity.
- One or more `person` entities for occupancy.
- One Home Assistant `schedule` helper.
- One home `zone` entity.
- One vehicle destination entity.
- One optional arrival-time entity.
- One configurable active HVAC mode selected from the climate entity's supported modes, defaulting to `heat` when available.

Multiple vehicles, YAML configuration, and a proxy climate entity are outside the first-release scope.

## Architecture

The integration uses a native, coordinator-driven Home Assistant architecture. One coordinator owns all control decisions and is the only component permitted to command the selected thermostat. Entity platforms expose controls and status on the Central Heating Controller device; they do not contain policy logic.

The implementation is split into focused units:

- `config_flow.py` provides initial setup, validation, options, and reset actions.
- `coordinator.py` subscribes to input changes, performs the once-per-minute evaluation, restores persistent state, distinguishes external thermostat changes from its own commands, and invokes climate services.
- `policy.py` is a pure decision engine that maps a complete input snapshot to one control result.
- `learning.py` filters temperature observations, maintains the learned heating rate, and calculates required warm-up time.
- `storage.py` persists learning data, manual overrides, and timer deadlines.
- `switch.py`, `button.py`, `number.py`, and `sensor.py` expose the device entities.
- `diagnostics.py` provides diagnostic data with person IDs, vehicle-related entity IDs and states, zone coordinates, and stored arrival values redacted.

No virtual climate entity is created, avoiding two competing thermostat controls and feedback loops.

## Setup and Configuration

The UI config flow asks for:

1. The climate entity to control.
2. One or more person entities.
3. The home zone.
4. The schedule helper.
5. The vehicle destination entity.
6. An optional arrival-time entity.
7. The HVAC mode to use while active, chosen from the selected climate entity's supported modes and defaulting to `heat` when supported.
8. High, low, and eco temperatures.
9. A fallback warm-up duration and a maximum adaptive warm-up duration.
10. An optional exact destination value that means home for unusual vehicle integrations.

Temperatures use Home Assistant's configured temperature unit. They must be within the selected climate entity's supported target range and satisfy `high >= low >= eco`. Validation errors are shown in the setup form.

For Celsius systems, initial defaults are high 20 °C, low 17 °C, and eco 14 °C. For Fahrenheit systems, initial defaults are high 68 °F, low 63 °F, and eco 57 °F. Defaults are clamped to the selected thermostat's range before the form is shown. Fallback warm-up defaults to 60 minutes and maximum warm-up defaults to 180 minutes; each accepts 5–360 minutes in five-minute steps, and fallback may not exceed maximum.

All settings remain editable through the integration's Configure flow. High, low, eco, fallback warm-up duration, and maximum warm-up duration are also exposed as device controls for convenient dashboard adjustment. Changing a setting triggers immediate re-evaluation.

Only one config entry may control a given climate entity. A second attempt is rejected to prevent competing commands.

## Device Entities

The integration creates one device named **Central Heating Controller** with these entities:

- **Auto Mode** switch: enables or strictly disables all automatic and blast heating.
- **Heat Blast** button: while Auto Mode is on, applies the high target for one hour.
- **High Temperature** number.
- **Low Temperature** number.
- **Eco Temperature** number.
- **Fallback Warm-up Duration** number.
- **Maximum Warm-up Duration** number.
- **Status** enum sensor with the states `High`, `Low`, `Pre-heating`, `Away`, `Off`, `Heat blast`, `Manual override`, and `Unavailable`.
- **Effective Target Temperature** sensor.
- **Learned Heating Rate** sensor in degrees per hour. It is unavailable until the model has enough reliable observations.
- **Pre-heat Start Time** timestamp sensor. It is unavailable when no qualifying timed journey exists.

The Status sensor includes attributes for the decision reason, current temperature, effective target, learned heating rate, manual-override state, qualifying arrival time, and calculated pre-heat start time. Attributes omit unavailable values rather than publishing misleading defaults.

## Occupancy, Schedule, and Travel Inputs

The home is occupied when any configured person is within the configured home zone. Zone containment uses person latitude/longitude and zone latitude/longitude/radius when available, with normalized person-state matching as a fallback.

If all selected person entities are unavailable or unknown, the controller assumes the home may be occupied. If at least one person has a valid state and none is home, the home is unoccupied.

The selected schedule is binary:

- `on` means the occupied target is high.
- `off` means the occupied target is low.
- An unavailable or unknown schedule uses low when the home is occupied.

A journey qualifies for home pre-heating only while the home is unoccupied and the vehicle destination matches the configured home zone. Matching is case-insensitive after trimming whitespace and normalizing spaces, underscores, and the optional `zone.` prefix. Accepted automatic values are the zone entity ID, object ID, friendly name, and normalized forms of those values. If an exact destination override is configured, that value is used in addition to the automatic values.

If the destination stops matching home, travel pre-heating is cancelled immediately. The controller then returns to the normal occupied or unoccupied policy. If the destination entity is unavailable or unknown, travel pre-heating is disabled.

## Decision Priority and State Machine

The coordinator evaluates policy in this strict order:

1. **Unavailable**: if the thermostat is unavailable, issue no command and report `Unavailable`.
2. **Off**: if Auto Mode is off, set the thermostat HVAC mode to `off` when needed and report `Off`. Heat Blast button presses are ignored.
3. **Heat blast**: if a blast deadline is active, use the configured active HVAC mode and high temperature until the one-hour deadline.
4. **Manual override**: if an external thermostat target change is being preserved, issue no target-changing command and report `Manual override`.
5. **Pre-heating**: if nobody is home, the destination is home, and the journey is ready for pre-heating, use the active HVAC mode and high temperature.
6. **Away**: if nobody is home otherwise, use the active HVAC mode and eco temperature.
7. **High**: if somebody is home and the schedule is on, use the active HVAC mode and high temperature.
8. **Low**: if somebody is home and the schedule is off or unavailable, use the active HVAC mode and low temperature.

The coordinator avoids redundant service calls when the thermostat already has the required HVAC mode and target.

## Heat Blast

Pressing Heat Blast while Auto Mode is on creates a deadline exactly one hour after the press, commands the high target, and reports `Heat blast`. Pressing it again restarts the one-hour deadline.

The blast takes priority over schedule, occupancy, travel, and manual override. Turning Auto Mode off cancels the blast immediately and turns the thermostat off. The stored deadline survives a Home Assistant restart; an already expired deadline is discarded during restoration. At expiry, policy is recalculated from current inputs rather than restoring a stale prior target.

## Manual Thermostat Override

The coordinator records the target and HVAC mode it intends to command. Matching thermostat updates are treated as acknowledgements, not external changes.

While Auto Mode is on and no Heat Blast is active, an external target-temperature change creates a temporary manual override. The controller leaves the externally selected target untouched and reports `Manual override`.

The override ends on the next meaningful control event:

- Schedule state changes.
- Occupancy state changes.
- Destination-match state changes.
- A qualifying journey crosses its calculated pre-heat threshold.
- Auto Mode changes.
- Heat Blast is pressed.
- A relevant temperature setting changes.

Minute ticks and ordinary current-temperature updates do not end an override. A restored manual override survives Home Assistant restart but is discarded if its stored thermostat target no longer matches the thermostat's restored target.

## Adaptive Pre-heating

### Arrival-Time Handling

The optional arrival-time entity may provide an ISO 8601 datetime string, a Home Assistant timestamp, or a numeric Unix timestamp. Naive datetime values are interpreted in Home Assistant's local time zone. Past or malformed values are unusable.

When the destination is home and nobody is home:

- With a usable future arrival time, the controller remains at eco until time-to-arrival is less than or equal to the calculated required warm-up duration.
- Without a usable arrival time, pre-heating starts immediately.

When a person arrives, ordinary occupied schedule control takes over immediately.

### Learning Model

The learner observes current temperature only while the selected climate entity reports active heating through `hvac_action == heating`. A sample becomes eligible after at least 15 minutes of continuous valid observation. Samples are rejected if states become unavailable, the target is reached, the temperature falls, or the implied rate is outside a conservative valid range of greater than 0 and at most 10 degrees per hour.

Accepted rates update an exponentially weighted moving average with smoothing factor 0.3: the first accepted sample becomes the initial rate, then each new value is `0.3 * sample + 0.7 * previous rate`. After accepting a sample, the learner begins a new observation window so overlapping samples do not inflate confidence. The model becomes trusted after three accepted samples. The learned rate and accepted-sample count are persisted.

Required warm-up duration is:

`(high temperature - current temperature) / learned degrees per hour`

The result is zero when the current temperature is already at or above high. Once the model is trusted, the result is clamped to the configured maximum warm-up duration. Before the model is trusted, the configured fallback duration is used, also capped by the configured maximum.

The calculated pre-heat start time is arrival time minus required warm-up duration. The once-per-minute evaluation ensures the threshold is crossed without requiring an input entity change.

The Configure flow provides an explicit action to reset learned data. Resetting returns the learner to the fallback duration until it accumulates three new accepted samples.

## Persistence and Restart Behaviour

The integration uses Home Assistant storage and restore-capable entity state to retain:

- Auto Mode.
- Heat Blast deadline.
- Manual override target and activation state.
- Learned heating rate and accepted-sample count.

On startup, the coordinator loads stored state, validates it against current entity states, subscribes to inputs, and evaluates policy once. It does not issue a command until Home Assistant is running and the target climate entity has a usable state.

## Failure Handling

- Thermostat unavailable: do not call climate services; report `Unavailable`; automatically resume on recovery.
- Climate service failure: log one contextual error, retain the desired policy result, and retry on the next input event or minute evaluation.
- All people unknown: assume possibly occupied and follow the schedule.
- Schedule unknown: use low for an occupied home.
- Destination unknown: do not pre-heat for travel.
- ETA unknown or malformed while destination is confirmed home: begin pre-heating immediately.
- Invalid restored data: discard only the invalid field, log at debug or warning level as appropriate, and continue with safe defaults.
- Entity removal: a missing thermostat prevents all commands; a missing home zone makes occupancy unknown and therefore conservatively occupied; missing people follow the existing unknown-person rule; a missing schedule uses low; and missing travel entities disable travel pre-heating. The integration raises a repair issue identifying the missing entity and continues only with these defined fallbacks until it is restored or reconfigured.

## Event and Command Flow

The coordinator subscribes to the climate, people, zone, schedule, destination, and optional ETA entities. It also runs a one-minute aligned timer. Each trigger produces an immutable snapshot, detects whether a meaningful transition clears a manual override, asks the pure policy engine for a result, updates exposed entities, and conditionally commands the climate entity.

Config-entry unload removes all listeners and timers. Reload applies configuration changes without requiring a Home Assistant restart.

## Testing Strategy

Pure unit tests cover:

- Every decision-priority branch and boundary.
- Occupancy and normalized destination matching.
- Arrival-time parsing and threshold calculations.
- Heating sample acceptance/rejection, smoothing, trust threshold, and clamps.
- Manual-override clearing events.

Home Assistant integration tests cover:

- Successful and invalid config flows.
- Duplicate-climate rejection and supported-HVAC-mode selection.
- Device and entity registration.
- Schedule and occupancy transitions.
- Destination cancellation and missing/malformed ETA behaviour.
- Heat Blast start, restart, expiry, Auto-off rejection, and restart restoration.
- External target changes versus coordinator command acknowledgements.
- Auto Mode restoration and strict thermostat-off behaviour.
- Unavailable inputs and climate service failures.
- Options updates, config-entry reload, and learned-data reset.

Tests use `pytest`, `pytest-homeassistant-custom-component`, time freezing, and Home Assistant service-call assertions. Production behaviour is implemented test-first.

## Packaging and Documentation

The repository includes:

- The copy-ready `custom_components/central_heating_controller/` integration.
- English strings and translations for setup, options, errors, entity names, and entity states.
- A valid integration manifest and icon-safe metadata.
- A README with installation, schedule-helper setup, configuration, dashboard examples, state meanings, adaptive-learning notes, troubleshooting, and removal instructions.
- A test suite and development dependency configuration.

The README explicitly states that this controller is convenience automation, not a safety control, and must not replace frost protection, boiler limits, or other manufacturer safety systems.
