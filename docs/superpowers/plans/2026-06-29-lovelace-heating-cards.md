# Lovelace Central Heating Cards Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build one drop-in Home Assistant Lovelace custom card JavaScript file that supports three modes: `control`, `visual`, and `settings`.

**Architecture:** Create a single dependency-free Web Component with a shared entity discovery/control core and three presentation renderers. Implement one usable mode at a time so each phase leaves a working card.

**Tech Stack:** Plain JavaScript, Custom Elements, shadow DOM, Home Assistant Lovelace custom card APIs, Node syntax/behavior tests with no package install.

---

## File Structure

- Create: `www/central-heating-controller-card.js`
  - Drop-in Lovelace custom card source. Contains the custom element, config parsing, entity derivation, render helpers, service calls, styles, and all three card modes.
- Create: `tests/js/central-heating-controller-card.test.mjs`
  - Node-based behavioral tests for config parsing, entity derivation, rendering branch selection, and service call payloads.
- Create: `tests/js/dom-shim.mjs`
  - Minimal DOM/custom-elements shim for testing the card without installing browser test packages.
- Create: `tests/js/fixtures.mjs`
  - Mock Home Assistant states and service call recorder used by the card tests.
- Create: `docs/lovelace-card.md`
  - Copy/install instructions and YAML examples for all three card modes.
- Modify: `README.md`
  - Add a short pointer to the Lovelace card documentation.

## Task 1: Shared Card Shell And Entity Discovery

- [ ] **Step 1: Create the test DOM shim**

Create `tests/js/dom-shim.mjs` with:

```js
class TestShadowRoot {
  constructor() {
    this.innerHTML = "";
  }

  querySelector() {
    return null;
  }
}

class TestHTMLElement {
  constructor() {
    this.shadowRoot = null;
  }

  attachShadow() {
    this.shadowRoot = new TestShadowRoot();
    return this.shadowRoot;
  }
}

const registry = new Map();

globalThis.HTMLElement = TestHTMLElement;
globalThis.customElements = {
  define(name, klass) {
    registry.set(name, klass);
  },
  get(name) {
    return registry.get(name);
  },
};

globalThis.Event = class Event {
  constructor(type) {
    this.type = type;
  }
};
```

- [ ] **Step 2: Create fixtures for Home Assistant state**

Create `tests/js/fixtures.mjs` with:

```js
export function createHass(overrides = {}) {
  const calls = [];
  const states = {
    "sensor.central_heating_controller_status": {
      entity_id: "sensor.central_heating_controller_status",
      state: "pre_heating",
      attributes: {
        friendly_name: "Status",
        current_temperature: 16.8,
        effective_target_temperature: 20,
        reason: "Pre-heating for the journey home",
        arrival_time: "2026-06-29T18:35:00+01:00",
        preheat_start_time: "2026-06-29T17:35:00+01:00",
      },
    },
    "switch.central_heating_controller_auto_mode": {
      entity_id: "switch.central_heating_controller_auto_mode",
      state: "on",
      attributes: { friendly_name: "Auto mode" },
    },
    "button.central_heating_controller_heat_blast": {
      entity_id: "button.central_heating_controller_heat_blast",
      state: "unknown",
      attributes: { friendly_name: "Heat blast" },
    },
    "number.central_heating_controller_high_temperature": {
      entity_id: "number.central_heating_controller_high_temperature",
      state: "20.0",
      attributes: { min: 7, max: 35, step: 0.5, unit_of_measurement: "degC" },
    },
    "number.central_heating_controller_low_temperature": {
      entity_id: "number.central_heating_controller_low_temperature",
      state: "17.0",
      attributes: { min: 7, max: 35, step: 0.5, unit_of_measurement: "degC" },
    },
    "number.central_heating_controller_eco_temperature": {
      entity_id: "number.central_heating_controller_eco_temperature",
      state: "14.0",
      attributes: { min: 7, max: 35, step: 0.5, unit_of_measurement: "degC" },
    },
    "number.central_heating_controller_fallback_warmup_minutes": {
      entity_id: "number.central_heating_controller_fallback_warmup_minutes",
      state: "60",
      attributes: { min: 5, max: 360, step: 5, unit_of_measurement: "min" },
    },
    "number.central_heating_controller_maximum_warmup_minutes": {
      entity_id: "number.central_heating_controller_maximum_warmup_minutes",
      state: "180",
      attributes: { min: 5, max: 360, step: 5, unit_of_measurement: "min" },
    },
    "sensor.central_heating_controller_effective_target_temperature": {
      entity_id: "sensor.central_heating_controller_effective_target_temperature",
      state: "20.0",
      attributes: { unit_of_measurement: "degC" },
    },
    "sensor.central_heating_controller_learned_heating_rate": {
      entity_id: "sensor.central_heating_controller_learned_heating_rate",
      state: "1.8",
      attributes: { unit_of_measurement: "degC/h" },
    },
    "sensor.central_heating_controller_preheat_start_time": {
      entity_id: "sensor.central_heating_controller_preheat_start_time",
      state: "2026-06-29T17:35:00+01:00",
      attributes: {},
    },
    ...overrides.states,
  };

  return {
    calls,
    states,
    localize: (key) => key,
    formatEntityState: (stateObj) => {
      const unit = stateObj.attributes.unit_of_measurement;
      return unit ? `${stateObj.state} ${unit}` : stateObj.state;
    },
    callService(domain, service, data) {
      calls.push({ domain, service, data });
      return Promise.resolve();
    },
    ...overrides.hass,
  };
}
```

- [ ] **Step 3: Write failing tests for shared configuration**

Create `tests/js/central-heating-controller-card.test.mjs` with:

```js
import assert from "node:assert/strict";
import test from "node:test";
import "./dom-shim.mjs";
import { createHass } from "./fixtures.mjs";
import "../../www/central-heating-controller-card.js";

const Card = customElements.get("central-heating-controller-card");

test("derives related entities from the status sensor by default", () => {
  const card = new Card();
  card.setConfig({ entity: "sensor.central_heating_controller_status" });

  assert.equal(card._entities.autoMode, "switch.central_heating_controller_auto_mode");
  assert.equal(card._entities.heatBlast, "button.central_heating_controller_heat_blast");
  assert.equal(card._entities.highTemperature, "number.central_heating_controller_high_temperature");
  assert.equal(card._entities.effectiveTarget, "sensor.central_heating_controller_effective_target_temperature");
});

test("allows explicit entity overrides", () => {
  const card = new Card();
  card.setConfig({
    entity: "sensor.central_heating_controller_status",
    entities: {
      auto_mode: "switch.heating_auto_custom",
      heat_blast: "button.heating_boost_custom",
    },
  });

  assert.equal(card._entities.autoMode, "switch.heating_auto_custom");
  assert.equal(card._entities.heatBlast, "button.heating_boost_custom");
});

test("renders a configuration error when entity is omitted", () => {
  const card = new Card();

  assert.throws(() => card.setConfig({}), /entity is required/);
});

test("defaults to control mode and renders with mocked Home Assistant state", () => {
  const card = new Card();
  card.setConfig({ entity: "sensor.central_heating_controller_status" });
  card.hass = createHass();

  assert.equal(card._config.mode, "control");
  assert.match(card.shadowRoot.innerHTML, /Heating/);
  assert.match(card.shadowRoot.innerHTML, /Pre-heating/);
});
```

- [ ] **Step 4: Run the tests and confirm they fail**

Run:

```bash
/Users/andy/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node --test tests/js/central-heating-controller-card.test.mjs
```

Expected: FAIL because `www/central-heating-controller-card.js` does not exist.

- [ ] **Step 5: Implement the minimal shared shell**

Create `www/central-heating-controller-card.js` with a custom element that:

- Validates `config.entity`.
- Defaults `mode` to `control`.
- Derives related entities from a status sensor ending in `_status`.
- Supports explicit overrides in `config.entities`.
- Renders a basic `ha-card` with title, status, target, current temperature, Auto, and Heat Blast placeholders.

- [ ] **Step 6: Run the tests and confirm Task 1 passes**

Run:

```bash
/Users/andy/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node --test tests/js/central-heating-controller-card.test.mjs
```

Expected: PASS.

## Task 2: Control Card Mode

- [ ] **Step 1: Add tests for controls and service calls**

Extend `tests/js/central-heating-controller-card.test.mjs` with tests proving:

- `mode: control` renders Auto and Heat Blast buttons.
- Pressing Auto while off calls `switch.turn_on`.
- Pressing Heat Blast calls `button.press`.
- Incrementing high temperature calls `number.set_value` with the current value plus the entity step.

- [ ] **Step 2: Run tests and confirm they fail**

Run the same Node test command. Expected: FAIL because event handling and number controls are not implemented.

- [ ] **Step 3: Implement the control renderer**

Update `www/central-heating-controller-card.js` to include:

- Status badge with friendly text for all integration statuses.
- Effective target and current temperature display.
- Auto mode action.
- Heat Blast action.
- High, low, and eco number steppers.
- Policy reason/manual override detail.

- [ ] **Step 4: Run tests and visually inspect the control harness**

Run:

```bash
/Users/andy/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node --test tests/js/central-heating-controller-card.test.mjs
```

Expected: PASS.

## Task 3: Visual Card Mode

- [ ] **Step 1: Add tests for visual mode**

Extend the test file to assert that `mode: visual` renders:

- Journey/status stage labels.
- Pre-heat start time.
- Arrival time.
- Learned heating rate.
- Auto and Heat Blast quick actions.

- [ ] **Step 2: Run tests and confirm they fail**

Expected: FAIL because `visual` mode is not implemented.

- [ ] **Step 3: Implement the visual renderer**

Update `www/central-heating-controller-card.js` with a dedicated visual layout using shared helpers:

- `away`, `pre_heating`, `high`, `low`, `heat_blast`, `manual_override`, `off`, `unavailable` stage strip.
- Target/current comparison.
- Time formatting for pre-heat and arrival.
- Learned rate display that gracefully handles unavailable sensors.

- [ ] **Step 4: Run tests**

Expected: PASS.

## Task 4: Settings Card Mode

- [ ] **Step 1: Add tests for settings mode**

Extend the test file to assert that `mode: settings` renders:

- High, low, eco controls.
- Fallback warm-up duration control.
- Maximum warm-up duration control.
- Disabled unavailable controls without throwing.

- [ ] **Step 2: Run tests and confirm they fail**

Expected: FAIL because settings mode is not implemented.

- [ ] **Step 3: Implement the settings renderer**

Update `www/central-heating-controller-card.js` with:

- Compact active status summary.
- Five number controls using the shared stepper renderer.
- Min/max/step clamping from number entity attributes.
- Disabled styling for unavailable/missing number entities.

- [ ] **Step 4: Run tests**

Expected: PASS.

## Task 5: Styling, Documentation, And Final Verification

- [ ] **Step 1: Add installation documentation**

Create `docs/lovelace-card.md` with:

````markdown
# Central Heating Controller Lovelace Card

Copy `www/central-heating-controller-card.js` to `/config/www/central-heating-controller-card.js`.

Add the Lovelace resource:

```yaml
url: /local/central-heating-controller-card.js
type: module
```

Daily control card:

```yaml
type: custom:central-heating-controller-card
entity: sensor.central_heating_controller_status
mode: control
```

Visual status card:

```yaml
type: custom:central-heating-controller-card
entity: sensor.central_heating_controller_status
mode: visual
```

Settings card:

```yaml
type: custom:central-heating-controller-card
entity: sensor.central_heating_controller_status
mode: settings
```

Override renamed entities:

```yaml
type: custom:central-heating-controller-card
entity: sensor.central_heating_controller_status
entities:
  auto_mode: switch.my_heating_auto
  heat_blast: button.my_heating_boost
```
````

- [ ] **Step 2: Link the documentation from README**

Add a short "Lovelace Card" section to `README.md` pointing to `docs/lovelace-card.md`.

- [ ] **Step 3: Run all JavaScript tests**

Run:

```bash
/Users/andy/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node --test tests/js/central-heating-controller-card.test.mjs
```

Expected: PASS.

- [ ] **Step 4: Run existing Python tests**

Run:

```bash
uv run pytest
```

Expected: PASS.

- [ ] **Step 5: Inspect the final worktree**

Run:

```bash
git status --short
```

Expected: only intentional files are modified or added.

## Recommended Build Order

1. Shared shell and discovery.
2. `control` mode.
3. `visual` mode.
4. `settings` mode.
5. Documentation and final verification.

This gives you one working drop-in card after Task 1, the most useful production card after Task 2,
and the two extra dashboard cards as low-risk increments.
