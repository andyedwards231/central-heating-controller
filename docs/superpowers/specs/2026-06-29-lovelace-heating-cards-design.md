# Lovelace Central Heating Cards Design

## Goal

Create one dependency-free Home Assistant Lovelace custom card JavaScript file that can be
dropped into `/config/www/` and used as any of three useful central-heating cards:

- `control`: daily control-first card.
- `visual`: status, journey, pre-heat, and learning visualization card.
- `settings`: tuning card for temperature and warm-up numbers.

All three modes use the same configuration shape and shared entity discovery, so the user can
start with the control card and add the other two cards without duplicating entity wiring.

## Card Configuration

The required configuration is:

```yaml
type: custom:central-heating-controller-card
entity: sensor.central_heating_controller_status
mode: control
```

`entity` must point to the integration status sensor. The card derives related entities from the
status sensor object ID by replacing `_status` with the integration's known suffixes:

- `switch.<base>_auto_mode`
- `button.<base>_heat_blast`
- `number.<base>_high_temperature`
- `number.<base>_low_temperature`
- `number.<base>_eco_temperature`
- `number.<base>_fallback_warmup_minutes`
- `number.<base>_maximum_warmup_minutes`
- `sensor.<base>_effective_target_temperature`
- `sensor.<base>_learned_heating_rate`
- `sensor.<base>_preheat_start_time`

Every derived entity can be overridden through an `entities` object for installations where Home
Assistant adds suffixes or the user has renamed entities.

## Shared Runtime

The card is a standard Web Component registered as `central-heating-controller-card`.
It uses native browser APIs only:

- `customElements.define`
- shadow DOM
- Home Assistant's `this.hass.states`
- Home Assistant services: `switch.turn_on`, `switch.turn_off`, `button.press`,
  and `number.set_value`
- `hass.formatEntityState` when available, with local fallbacks

The shared runtime resolves entities, normalizes status metadata, formats temperatures and times,
and exposes small render helpers used by all three modes. Missing optional entities degrade to
disabled controls or muted "Unavailable" values instead of breaking the whole card.

## Mode 1: Control

The control card is the default and is optimized for daily use:

- Header with title, current controller status, and an icon-like status badge.
- Large effective target temperature.
- Current temperature from the status sensor's `current_temperature` attribute.
- Auto mode button/toggle using `switch.turn_on` and `switch.turn_off`.
- Heat Blast button using `button.press`.
- Compact high, low, and eco number controls with decrement/increment buttons and value display.
- A small detail row showing the policy reason and manual override when present.

## Mode 2: Visual

The visual card is optimized for explanation and confidence:

- Status stage strip for `away`, `pre_heating`, `high`, `low`, `heat_blast`, `manual_override`,
  `off`, and `unavailable`.
- Target/current temperature comparison.
- Pre-heat start and arrival time when available from sensor state or status attributes.
- Learned heating rate when trusted.
- Policy reason summary.

The visual card does not expose all tuning controls; Auto and Heat Blast remain available because
they are high-frequency dashboard actions.

## Mode 3: Settings

The settings card is optimized for tuning:

- High, low, eco temperature controls.
- Fallback and maximum warm-up duration controls.
- Validation-aware disabled states using entity availability and number min/max/step attributes.
- Compact status summary at the top so the user can tune settings while seeing the active mode.

## Styling

The cards should look native in Home Assistant:

- Use `ha-card` as the host container.
- Respect Home Assistant CSS variables such as `--ha-card-background`,
  `--primary-text-color`, `--secondary-text-color`, `--primary-color`,
  `--error-color`, and `--warning-color`.
- Use restrained temperature/status accent colors instead of a one-color palette.
- Fit mobile dashboard columns without overflow.
- Avoid external fonts, libraries, images, or build tooling.

## Error Handling

The card displays a clear configuration error when `entity` is missing or the status entity does
not exist. Related entity failures remain localized: one missing number disables that control while
the rest of the card continues rendering.

## Verification

Verification should cover syntax and core behavior with a small Node-based test harness using mocked
Home Assistant state and service calls. Browser visual QA should load a static harness for all three
modes and inspect desktop and narrow mobile widths.
