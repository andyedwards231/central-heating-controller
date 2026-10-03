# Central Heating Controller

Central Heating Controller is a copy-ready Home Assistant custom integration for one
thermostat. It combines a Home Assistant schedule helper, occupancy, a destination
sensor, optional arrival time, manual thermostat changes, and a small adaptive
learning model to choose a safe target temperature.

This integration does not replace frost protection or manufacturer safety systems.
Keep the thermostat, boiler, radiator valves, and any heating safety equipment
configured according to their manufacturer guidance.

## Installation

Requires Home Assistant Core 2026.9.3 or newer. The automated tests run against
2026.9.3.

For HACS, add `https://github.com/andyedwards231/central-heating-controller` as a
custom repository of type **Integration**, download Central Heating Controller,
and restart Home Assistant. Then add the integration in Settings → Devices & services.
Updates are published as GitHub releases with matching manifest versions so HACS
can detect them. Restart Home Assistant after installing an update.

For manual installation:

1. Copy `custom_components/central_heating_controller` into `<config>/custom_components/`.
2. Restart Home Assistant.
3. Open Settings → Devices & services → Add integration.
4. Search for Central Heating Controller.
5. Complete the setup flow for the thermostat you want this controller to manage.

The integration manifest supports HACS and direct copy installation: it has an empty
`requirements` list, `config_flow: true`, `integration_type: service`,
`iot_class: local_push`, and a version number.

## Schedule Helper

Create a Home Assistant `schedule.*` helper before setup. The controller reads it as
the normal occupied heating plan:

- On = high
- Off = low

When someone is home and the schedule is on, the controller targets the high
temperature. When someone is home and the schedule is off or unavailable, it targets
the low temperature. When nobody is home and no pre-heat journey is ready, it targets
the eco temperature.

## Setup

The setup flow asks for these entities and settings:

- `climate_entity`: the thermostat this controller will command. A thermostat can
  only be controlled by one Central Heating Controller entry.
- `person_entities`: one or more people used to decide whether home is occupied.
- `home_zone_entity`: the Home Assistant zone used as home.
- `schedule_entity`: the `schedule.*` helper where On = high and Off = low.
- `destination_entity`: a sensor whose state describes the current destination.
- `arrival_time_entity`: optional ETA sensor for timed pre-heating.
- `active_hvac_mode`: the thermostat HVAC mode to use when heating is required,
  such as `heat`.
- `high_temperature`: the warm occupied target, used for schedule-on, heat blast,
  and pre-heating.
- `low_temperature`: the occupied schedule-off target.
- `eco_temperature`: the unoccupied target.
- `fallback_warmup_minutes`: the warm-up duration used until learning is trusted
  or when a current temperature is unavailable.
- `maximum_warmup_minutes`: the cap for adaptive warm-up calculations.
- `destination_home_value`: optional extra destination value that should mean home.

Temperature settings are validated against the selected thermostat capabilities.
The temperature order must be high ≥ low ≥ eco. Warm-up durations must be in
5-minute increments from 5 to 360 minutes, and the fallback cannot exceed the
maximum.

## Statuses

The `status` sensor publishes these stable states:

- `high`: someone is home and the schedule helper is on; target is
  `high_temperature`.
- `low`: someone is home and the schedule helper is off or unavailable; target is
  `low_temperature`.
- `pre_heating`: nobody is home, the destination matches home, and the ETA window
  says pre-heating is ready; target is `high_temperature`.
- `away`: nobody is home and pre-heating is not ready; target is
  `eco_temperature`.
- `off`: Auto mode is off, so the controller commands HVAC off.
- `heat_blast`: a fixed high-temperature heat blast is active.
- `manual_override`: the thermostat target was changed externally and the
  controller is preserving it.
- `unavailable`: the thermostat is missing or unavailable, so the controller does
  not command it.

The status sensor also exposes useful attributes such as the policy reason,
current temperature, effective target temperature, trusted learned heating rate,
manual override flag, arrival time, and pre-heat start time when they apply.

## Heat Blast

Press the Heat blast button to start or restart a 60-minute high-temperature boost.
Heat blast clears any active manual override and takes priority over normal schedule,
occupancy, away, and pre-heating decisions. It only runs while Auto mode is enabled.

## Manual Override

If the thermostat target changes outside a command issued by this integration, the
controller records a manual override and keeps that external target instead of
immediately replacing it.

Manual override clears when one of these override-clearing events happens:

- Auto mode is turned off or back on.
- The Heat blast button is pressed.
- A controller setting number is changed.
- The preserved external target no longer matches the current policy context, for
  example because occupancy, schedule, destination, or pre-heat state changed.
- The integration entry is removed.

This behavior lets a short manual adjustment survive routine coordinator refreshes
without permanently disabling automatic control.

## Adaptive Pre-heating

Destination matching compares the destination sensor with the configured home zone
entity ID, the home zone object ID, the home zone friendly name, and the optional
`destination_home_value`. Matching ignores case, a leading `zone.`, whitespace,
hyphens, and underscores.

When nobody is home and the destination means home, the controller can pre-heat:

- If `arrival_time_entity` is not configured, pre-heating starts immediately for a
  home journey.
- If `arrival_time_entity` is configured, an invalid, unavailable, unknown, blank,
  past, or missing ETA blocks pre-heating. A configured ETA entity that is missing
  disables preheat and creates a repair issue.
- If a previously valid ETA becomes invalid or expires before anyone arrives,
  pre-heating stops and the controller returns to the eco target, unless heat blast
  or a manual override has priority.
- If the ETA is valid and in the future, the controller calculates a pre-heat start
  time from the selected warm-up duration.
- If the destination changes away from home, the pre-heat journey is cancelled.

The adaptive model has a three-sample learning period. Until at least three valid
heating samples are collected, it uses `fallback_warmup_minutes`. After that, it
uses the trusted learned heating rate, current temperature, high target, and
`maximum_warmup_minutes` cap. Use the options flow and choose reset learning if you
change heating hardware, radiator balancing, insulation, or anything else that
would make the old learned rate misleading.

Learning requires the thermostat to report `hvac_action: heating`. If it does not,
the controller continues using the fallback warm-up duration. An unavailable
thermostat breaks the current learning sample, so time spent offline is not
counted as observed heating. ETA values may be ISO timestamps or Unix timestamps
(including fractional seconds); timezone-free ISO values use Home Assistant's timezone.

### Tado X

Use the thermostat's `climate.*` entity exposed by Home Assistant's **Matter**
integration and select `heat` as the active HVAC mode. The standard Tado integration
does not support Tado X; see [Home Assistant's Tado documentation](https://www.home-assistant.io/integrations/tado/).

The controller manages one climate entity per entry. Other Tado rooms or radiator
valves are not automatically included. Matter heating-action reporting depends on
the device, so check whether your entity exposes `hvac_action` before expecting
adaptive learning. Changes from Tado schedules, the Tado app, or other automations
are treated as external target changes and may activate manual override.

The controller waits briefly for command acknowledgements before repeating a write.
Its status describes the selected control policy, not proof that the boiler is firing.

## Dashboard Entities

Add these entities to a dashboard if you want visibility and controls. Home
Assistant may add a suffix if an entity ID already exists, but a typical first
entry exposes:

- `switch.central_heating_controller_auto_mode`
- `button.central_heating_controller_heat_blast`
- `number.central_heating_controller_high_temperature`
- `number.central_heating_controller_low_temperature`
- `number.central_heating_controller_eco_temperature`
- `number.central_heating_controller_fallback_warmup_minutes`
- `number.central_heating_controller_maximum_warmup_minutes`
- `sensor.central_heating_controller_status`
- `sensor.central_heating_controller_effective_target_temperature`
- `sensor.central_heating_controller_learned_heating_rate`
- `sensor.central_heating_controller_preheat_start_time`

The effective target and pre-heat start sensors are unavailable when there is no
meaningful value. The learned heating rate sensor is unavailable until the learning
model is trusted.

## Lovelace Card

This repository also includes a drop-in custom Lovelace card at
`www/central-heating-controller-card.js`. It supports daily control, visual status,
and settings modes from the same status sensor configuration. See
`docs/lovelace-card.md` for installation and YAML examples.

## Troubleshooting

For unavailable fallbacks, check the selected thermostat first. If the thermostat is
missing or unavailable, the controller reports `unavailable` and does not command
the thermostat. If the thermostat has unusable temperature capability metadata,
the high, low, and eco setting controls become unavailable until the metadata is
valid again. If the schedule helper is unavailable while someone is home, the
controller falls back to the low target.

Useful troubleshooting places:

- Logs: look for messages from `custom_components.central_heating_controller`.
- Diagnostics: download diagnostics from the integration entry. Diagnostics are
  designed to redact location and journey values while preserving useful status.
- Repairs: the integration creates repairs for missing configured input entities;
  restore the missing entity or reconfigure the integration.

If pre-heating does not start, check destination matching, the configured home zone
friendly name, `destination_home_value`, and whether the configured ETA entity has
a valid future timestamp. Destination changes away from home cancel the pre-heat path.

## Development

With Python 3.14 and uv installed, run `uv sync --frozen`, `uv run pytest`,
`uv run ruff check .`, and `node --test tests/js/*.test.mjs`.
The test suite includes real Home Assistant climate-service validation with
simulated delayed thermostat reports. Physical Tado X behaviour still needs a
check on the installed system: verify schedule high/low, an external manual target,
Auto off, and invalid ETA handling before relying on unattended control.

## Removal

For safe removal:

1. Turn Auto mode off if you want the thermostat left in HVAC off before removal.
2. Remove the Central Heating Controller entry from Settings → Devices & services.
3. Confirm the thermostat is back under your preferred normal control.
4. Restart Home Assistant if you are also deleting files.
5. Delete `<config>/custom_components/central_heating_controller` only after the
   integration entry is removed.

Removal unloads the controller and clears repair issues for that entry. It does
not delete your thermostat, people, zone, schedule helper, destination sensor, ETA
sensor, or other Home Assistant entities.
