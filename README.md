# Central Heating Controller

Central Heating Controller is a copy-ready Home Assistant custom integration for one
thermostat. It combines a Home Assistant schedule helper, occupancy, a destination
sensor, optional arrival time, manual thermostat changes, and a small adaptive
learning model to choose a safe target temperature.

This integration does not replace frost protection or manufacturer safety systems.
Keep the thermostat, boiler, radiator valves, and any heating safety equipment
configured according to their manufacturer guidance.

## Installation

1. Copy `custom_components/central_heating_controller` into `<config>/custom_components/`.
2. Restart Home Assistant.
3. Open Settings → Devices & services → Add integration.
4. Search for Central Heating Controller.
5. Complete the setup flow for the thermostat you want this controller to manage.

The integration manifest is intended for direct copy installation: it has an empty
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
- `unavailable`: the thermostat is unavailable or cannot be safely commanded.

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
- If `arrival_time_entity` is configured and the entity exists but has an invalid,
  unavailable, unknown, blank, or past ETA, pre-heating starts immediately.
- If the configured ETA entity was removed or does not exist,
  configured-removed ETA disables preheat.
- In short: unconfigured or existing invalid ETA starts preheat immediately,
  while a configured-removed ETA disables preheat.
- If the ETA is valid and in the future, the controller calculates a pre-heat start
  time from the selected warm-up duration.
- If the destination changes away from home, the pre-heat journey is cancelled.

The adaptive model has a three-sample learning period. Until at least three valid
heating samples are collected, it uses `fallback_warmup_minutes`. After that, it
uses the trusted learned heating rate, current temperature, high target, and
`maximum_warmup_minutes` cap. Use the options flow and choose reset learning if you
change heating hardware, radiator balancing, insulation, or anything else that
would make the old learned rate misleading.

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

## Troubleshooting

For unavailable fallbacks, check the selected thermostat first. If the thermostat is
missing, unavailable, has unusable temperature metadata, or cannot accept a target
temperature, the controller keeps affected entities unavailable rather than issuing
unsafe commands. If the schedule helper is unavailable while someone is home, the
controller falls back to the low target.

Useful troubleshooting places:

- Logs: look for messages from `custom_components.central_heating_controller`.
- Diagnostics: download diagnostics from the integration entry. Diagnostics are
  designed to redact location and journey values while preserving useful status.
- Repairs: the integration creates repairs for missing configured input entities;
  restore the missing entity or reconfigure the integration.

If pre-heating does not start, check destination matching, the configured home zone
friendly name, `destination_home_value`, and whether the ETA entity exists. Remember
that destination changes away from home cancel the pre-heat path.

## Removal

For safe removal:

1. Turn Auto mode off if you want the thermostat left in HVAC off before removal.
2. Remove the Central Heating Controller entry from Settings → Devices & services.
3. Confirm the thermostat is back under your preferred normal control.
4. Restart Home Assistant if you are also deleting files.
5. Delete `<config>/custom_components/central_heating_controller` only after the
   integration entry is removed.

Removal clears the integration's stored auto mode, heat blast, manual override, and
learning state for that entry. It does not delete your thermostat, people, zone,
schedule helper, destination sensor, or ETA sensor.
