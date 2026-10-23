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
[card installation and examples](docs/lovelace-card.md) and the optional
[three-view Heating dashboard](docs/heating-dashboard.md).

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

## Indoor temperature forecast

In the integration's **Configure** flow, select a weather entity providing hourly
forecasts and a live outdoor temperature sensor. These are optional additions, so
existing installations keep working without reconfiguration. The integration reads
`weather.get_forecasts` and `schedule.get_schedule`; no extra automation or chart
library is needed. Both Celsius and Fahrenheit inputs are supported.

The new **Indoor temperature forecast** sensor contains a 24-hour forecast at
five-minute intervals in its `forecast` attribute; its state is the predicted
indoor temperature in 24 hours. The bundled Lovelace card plots the indoor curve
and the controller's target. Update the card JavaScript as well as the integration
when installing this change, and refresh the browser resource cache.

For Andy's installation, the selected sources are `weather.forecast_home`,
`sensor.hue_outdoor_motion_sensor_temperature`, and
`climate.hallway_hall_tado_x`. Select these in Configure after installing; this
change does not modify the live Home Assistant configuration.

### Learning and accuracy

Select `sensor.hall_heating_power` in **Heating activity or demand** for Andy's
installation. A sensor reporting a `%` unit is interpreted as 0–100% demand:
13% becomes 0.13, not full-power heating. Invalid, unavailable, out-of-range or
more-than-30-minute-old demand pauses learning. Binary sensors and sensors with
`on`/`off`, `True`/`False` or `heating`/`idle` remain supported. Without a selected
source the thermostat's `hvac_action` is used when available.

Reported demand is a control signal, not a measurement of gas use or boiler heat
output. The model learns its relationship to observed temperature changes. The
Hall Tado X does not expose `hvac_action`; its old `sensor.is_central_heating_on`
template points at `climate.room_1` and should not be used for this installation.
The percentage source allows useful learning without a separate boiler relay
sensor, but accuracy must still be checked in the house.

The thermal model learns heat gain, heat loss and how quickly a change in demand
affects the room. It compares response times of 0, 5, 10, 20, 40 and 60 minutes,
using earlier observation windows for fitting and later windows for validation.
A nonzero response time must improve on the immediate-response model before it
is used. This represents heat building up and residual warmth after demand falls;
it is an exponential response time, not a literal period during which nothing
happens. About 63% of a demand change's effect is reached after one response time.

Observations are aggregated into 15-minute windows. The model retains up to
1,000 windows, uses bounded coefficients and reduces the influence of outliers.
Future demand is estimated from the temperature target and the observed demand
behaviour. Before sufficient varied observations exist, the card labels the
forecast uncalibrated rather than presenting assumptions as learnt facts.

Learning and forecast-error history survive restarts. Changing the thermostat,
outdoor sensor or demand/activity source starts fresh learning and accuracy
tracking. The reset-learning option clears both. The new thermal model's storage
format is version 2; previous version-1 thermal samples are discarded because
they contain no response-delay information. This does not discard control
settings or the separate legacy warm-up-rate learner.

The forecast integrates heat flow every minute and publishes points every five
minutes for the next 24 hours. The outdoor forecast is anchored to the live
outdoor reading, with that correction fading over three hours. Weekly schedules
use Home Assistant's local time zone, including clock changes. Occupancy remains
as it is now until a known home arrival; blast expiry, manual override expiry
and preheating follow the existing controller policy. Future departures cannot
be inferred. Forecasting does not change the thermostat's control policy or start
heating early for scheduled high periods. The legacy warm-up estimator only
learns from binary activity; with a percentage source it retains its existing
estimate or configured fallback rather than treating partial demand as full heat.

The card shows reported demand, learned response time, and mean absolute error
for 1-, 6- and 24-hour forecasts once enough time has elapsed to score them. The
sensor's `forecast_accuracy` attribute additionally includes signed bias
(actual minus predicted) and root-mean-square error, all in Celsius, plus sample
counts. One forecast cohort is retained per hour; each horizon retains up to
168 measured errors. Only fresh indoor sensor reports at or up to five minutes
after the predicted timestamp are scored. Reusing an old temperature on a
coordinator refresh does not count as a fresh observation. Sparse sensor reports
may therefore leave some forecasts unscored. Pending forecasts and errors are
persisted; expired scoring windows are dropped on restart.

Measured errors include the effects of later weather, occupancy and manual-control
changes. The model score is a learning-quality heuristic, not a statistical
probability. Model residuals are in °C/hour, not a guarantee of 24-hour accuracy.

This is a single-zone estimate of the thermostat location. Solar gains, opening
windows, changing boiler output and differences between rooms are not explicitly
represented. Assess accuracy over representative heating and cooling days.
Synthetic tests verify learning and simulation, but do not establish real-house
accuracy. Missing weather/schedule data produces a forecast error without
preventing thermostat control. Input requests are cached for 15 minutes with
bounded failure retries; schedule state changes invalidate its cache immediately.
The full curve is excluded from recorder attributes to avoid storing a large
chart payload every minute.


## Predicted heating costs

Optional **Predicted heating cost today** and **Predicted heating cost tomorrow**
sensors estimate the heating portion of your gas bill in GBP. In the integration's
configuration, select a current gas unit rate (`GBP/kWh`), current Octopus gas
consumption (`kWh`, with half-hour `charges` attributes), and optionally the previous
consumption entity so delayed readings can be matched to stored heating demand.
Keep the heating activity source configured, for example `sensor.hall_heating_power`.
A cumulative meter total without timestamped intervals cannot calibrate this model.

For a combi boiler, gas also supplies hot water. The model fits gas consumption
against average heating demand, with a separate background allowance. Only the
fitted demand-linked component is charged to heating. This is a statistical
attribution, not a measurement of heating-only consumption: hot water used at the
same time as heating can bias it. An intermittent camera water meter is not used
as evidence that hot water was or was not running.

Learning requires at least 30 complete half-hour intervals across three dates,
including six with at least 10% demand and six with at most 5% demand. Demand must
vary enough to identify a relationship, and unstable fits are rejected. These
are minimum requirements, not a promise of calibration after three days. Demand
and calibration persist for 14 days so delayed gas readings can be matched by
their interval timestamps. Estimated supplier readings and the most recent
half-hour are excluded; later corrections replace earlier samples.

Today's value estimates the whole local day: observed demand since midnight plus
forecast demand to midnight. Tomorrow covers its whole local calendar day. Both
use the current unit rate, held constant, without a standing charge. Tomorrow
requires weather coverage through the end of tomorrow. Missing demand history,
missing forecast coverage, an invalid rate or insufficient learning leaves the
sensor value unknown; the `reason` and `calibration` attributes remain visible
while the model is learning. Calibration diagnostics include the number and
time span of retained heating-demand observations, even before any gas interval
can be matched. The sensor itself is unavailable only when its coordinator is
unavailable. Restart gaps longer than five minutes can leave today's value
unknown until the next full day.

Attributes expose the fitted coefficient, background gas allowance, sample
counts, median absolute fit error, demand spread diagnostic, forecast confidence
and tariff assumption. Fit errors describe the shared gas model, not independently
verified heating-only cost accuracy. Temperature forecasts continue to have their
separate checks against actual indoor temperatures.


## Smart schedule preview and controller modes

The smart schedule starts in **preview only**. It does not change the targets
sent to your thermostat: the existing Home Assistant schedule remains in
control. The main card shows a separate proposed 24-hour temperature curve and
shades expected heating. Open **Schedule and night settings** to edit weekly
comfort periods, night times, the night minimum and the maximum daytime drop.
Your High temperature is the comfort target; periods mean **warm by** the start,
with comfort maintained until the end. With no weekly periods set, preview uses
your existing schedule helper. Night defaults to 23:00–07:00 and 16°C; the
occupied daytime allowance defaults to 2°C. Comfort windows take precedence
when they overlap the night window.

The planner compares setbacks between comfort periods and earlier recovery
starts, using the learned heating delay, demand response, heat loss, outdoor
forecast and known arrival. Different gaps may use different drops, in 0.5°C
steps up to your configured maximum. It minimises predicted heating demand
across 36 hours when weather coverage permits (otherwise 24 hours), with an
estimated allowance for reheating any remaining temperature deficit at the
end. It uses a bounded search; neither exact optimum nor cash savings are
guaranteed. A flat unit gas rate makes demand a useful cost proxy. Shared
combi-boiler hot water and uncertain boiler efficiency limit monetary accuracy.
The existing heating-cost sensors describe the **live** schedule, not the preview.

The preview remains labelled while the model is learning, or if it predicts
that comfort cannot be reached. Its assumed occupancy continues until any
known home arrival; unknown future departures are not predicted. Actual
forecast accuracy continues to be checked against reported temperatures for
the live policy. A proposed schedule cannot be scored as though it had run.

`select.central_heating_controller_controller_mode` offers these live modes:

- **Auto:** normal control. Smart scheduling remains a preview in this release.
- **Away:** holds the Eco temperature until a fresh homeward destination and
  valid future ETA are seen, or any tracked person returns after a confirmed
  absence. A person already home when Away is selected must leave and return;
  unknown/unavailable tracking does not count as leaving. The latch survives
  restarts. With no ETA entity configured, a fresh homeward destination uses
  the existing immediate-preheat behaviour.
- **Off:** turns the climate entity off and prevents automatic heating or heat
  blasts. Presence and journey updates cannot re-enable it. Select Auto to resume.

The preview plans recovery before a known arrival, even outside comfort
windows, and holds comfort for the first hour after arrival. This proposal
requires a usable ETA. The mode selector acts immediately, independently of
preview. The legacy Auto switch remains compatible: on selects Auto, off
selects Off.

The `central_heating_controller.set_smart_schedule` action saves the complete
preview settings against a controller entity. It is used by the card, so YAML
editing is not necessary. Update the dashboard JavaScript separately from HACS;
see [card installation](docs/lovelace-card.md).
