# Central Heating Controller Lovelace Card

The repository includes a dependency-free Lovelace custom card at
`www/central-heating-controller-card.js`.

HACS installs the integration directory only; this optional card must be copied
separately, including after a card update.

Copy it to your Home Assistant config:

```text
/config/www/central-heating-controller-card.js
```

Add the Lovelace resource:

```yaml
url: /local/central-heating-controller-card.js?v=1.4.0
type: module
```

## Daily Control Card

```yaml
type: custom:central-heating-controller-card
entity: sensor.central_heating_controller_status
mode: control
```

The control card includes a **Mode** selector for `auto`, `away`, and `off`.
It uses `select.<base>_controller_mode` and calls Home Assistant's
`select.select_option` service; the existing auto-mode switch action remains
available for compatibility. The collapsible **Schedule and night settings**
editor lets you show or hide the smart schedule preview, add or remove any
number of comfort periods for each day, and edit the night window and
temperatures. Comfort windows are warm-by times; they do not turn the heating
on at the start time. The High setpoint is the comfort temperature. If the
weekly schedule has no windows, the existing Home Assistant schedule is used.
The editor saves the full weekly schedule through the integration service. The Configure link remains
available for the rest of the integration options.

When `sensor.<base>_smart_heating_plan` has state `preview`, the card shows a
separate panel headed **Preview only** and marked as unable to control heating.
Its explanation and optional forecast use the sensor's `forecast` attribute
with the same point fields as the indoor forecast (`datetime`, `temperature`,
`target_temperature`, and `heating_demand_percent`). The panel is omitted for
`disabled` or `unavailable` states. The live forecast remains separate above
the preview.

Override renamed entities with `controller_mode` and `smart_heating_plan` under
`entities`.

## Visual Status Card

```yaml
type: custom:central-heating-controller-card
entity: sensor.central_heating_controller_status
mode: visual
```

The visual card includes a responsive 24-hour indoor temperature forecast when
the integration's `sensor.<base>_indoor_temperature_forecast` entity is
available. It plots predicted indoor temperature and target temperature, shows
the local-time range and unit, and lists forecast assumptions. The control
card also shows the chart. While the controller is learning, any available
estimate remains visible with an uncalibrated label. The card shows current
reported heating demand and either the learned heating response time or a
learning label. The response time is an exponential time constant: it describes
the time to reach about 63% of the effect of a demand change. Forecast mean
errors and sample counts appear for each populated 1-hour, 6-hour, and 24-hour
horizon. To use a renamed entity, add `indoor_temperature_forecast` under
`entities`.

## Settings Card

```yaml
type: custom:central-heating-controller-card
entity: sensor.central_heating_controller_status
mode: settings
```

## Renamed Entities

The card derives related entities from the status sensor by replacing `_status`
with the integration's standard suffixes.

The card also recognizes the old `fallback_warm_up_duration`,
`maximum_warm_up_duration`, and `pre_heat_start_time` suffixes retained in existing
installations after an upgrade. Existing entity IDs do not need to be renamed.

If Home Assistant added suffixes or you renamed entities, override them:

```yaml
type: custom:central-heating-controller-card
entity: sensor.central_heating_controller_status
entities:
  auto_mode: switch.my_heating_auto
  heat_blast: button.my_heating_boost
  high_temperature: number.my_heating_high
  low_temperature: number.my_heating_low
  eco_temperature: number.my_heating_eco
  fallback_warmup_minutes: number.my_heating_fallback_warmup
  maximum_warmup_minutes: number.my_heating_maximum_warmup
  effective_target_temperature: sensor.my_heating_effective_target
  learned_heating_rate: sensor.my_heating_learned_rate
  preheat_start_time: sensor.my_heating_preheat_start
```

## Kiosk Forecast Card

A separate, read-only card with large current and target temperatures, a 24-hour
forecast line, a dashed target line, and amber shading where heating is expected.
It uses the same JavaScript resource as the other cards. Copy the updated file
from the v1.5.0 release into `/config/www/central-heating-controller-card.js`,
update the resource URL above, and refresh the browser.

```yaml
type: custom:central-heating-forecast-card
entity: sensor.central_heating_controller_status
```

Use a full-width section on the tablet for the clearest chart. The chart height
is capped at 260px, with no controls or diagnostic lists. “Early estimate” is
shown while learning. Amber shading means forecast demand above 5%; these are
estimated heating periods, not guaranteed boiler switching times. Five-minute
forecast points limit the precision of the shading. Entity overrides work just
as for the control card.

## Rendering and regression checks

The cards ignore unrelated Home Assistant state updates. Displayed entity
replacements (including attribute-only changes, disappearance and recovery), entity
overrides and configuration changes still update the display. Formatting follows
Home Assistant's language, clock format and local/server time-zone preferences;
equivalent preference objects do not cause redraws. The latest Home Assistant
object is retained even when rendering is skipped, so actions use current callbacks.

The kiosk card reuses its current/target header and chart DOM. Header-only updates
reuse the cached forecast; new forecast data patches SVG attributes, labels and
heating bands. Date formatters are reused until their inputs change. These checks
assume Home Assistant's immutable State objects, rather than objects mutated in place.

Run `node --test tests/js/*.test.mjs` for automated dependency and formatter checks.
For real-DOM verification, serve the repository on localhost, open
`tests/browser/forecast-rendering.html`, and select **Run rendering checks**. It uses
synthetic data only and verifies 80 unrelated updates produce zero renders,
header-only updates do not rebuild the chart, new forecast geometry retains SVG and
line nodes, and source/configuration/unavailable/time-zone changes still work.

The Android profiling experiment identified unnecessary rendering. The checks here
do not establish the cause of earlier low-memory kills or replace a repeat profile
on the tablet after installing the new JavaScript.
