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
url: /local/central-heating-controller-card.js?v=1.2.0
type: module
```

## Daily Control Card

```yaml
type: custom:central-heating-controller-card
entity: sensor.central_heating_controller_status
mode: control
```

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
