# Central Heating Controller Lovelace Card

The repository includes a dependency-free Lovelace custom card at
`www/central-heating-controller-card.js`.

Copy it to your Home Assistant config:

```text
/config/www/central-heating-controller-card.js
```

Add the Lovelace resource:

```yaml
url: /local/central-heating-controller-card.js
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

## Settings Card

```yaml
type: custom:central-heating-controller-card
entity: sensor.central_heating_controller_status
mode: settings
```

## Renamed Entities

The card derives related entities from the status sensor by replacing `_status`
with the integration's standard suffixes. If Home Assistant added suffixes or you
renamed entities, override them:

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
