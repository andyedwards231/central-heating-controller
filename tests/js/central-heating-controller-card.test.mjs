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
  assert.equal(
    card._entities.effectiveTarget,
    "sensor.central_heating_controller_effective_target_temperature",
  );
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

test("renders a clear error when the configured status entity is missing", () => {
  const card = new Card();
  card.setConfig({ entity: "sensor.central_heating_controller_status" });
  card.hass = createHass({
    states: {
      "sensor.central_heating_controller_status": undefined,
    },
  });

  assert.match(card.shadowRoot.innerHTML, /Status entity not found/);
  assert.match(card.shadowRoot.innerHTML, /sensor.central_heating_controller_status/);
});

test("defaults to control mode and renders with mocked Home Assistant state", () => {
  const card = new Card();
  card.setConfig({ entity: "sensor.central_heating_controller_status" });
  card.hass = createHass();

  assert.equal(card._config.mode, "control");
  assert.match(card.shadowRoot.innerHTML, /Heating/);
  assert.match(card.shadowRoot.innerHTML, /Pre-heating/);
});

test("control mode renders primary actions and setpoint controls", () => {
  const card = new Card();
  card.setConfig({
    entity: "sensor.central_heating_controller_status",
    mode: "control",
  });
  card.hass = createHass();

  assert.match(card.shadowRoot.innerHTML, /Auto mode/);
  assert.match(card.shadowRoot.innerHTML, /Heat Blast/);
  assert.match(card.shadowRoot.innerHTML, /High/);
  assert.match(card.shadowRoot.innerHTML, /Low/);
  assert.match(card.shadowRoot.innerHTML, /Eco/);
});

test("turns auto mode on when the auto switch is off", async () => {
  const card = new Card();
  const hass = createHass({
    states: {
      "switch.central_heating_controller_auto_mode": {
        entity_id: "switch.central_heating_controller_auto_mode",
        state: "off",
        attributes: { friendly_name: "Auto mode" },
      },
    },
  });
  card.setConfig({ entity: "sensor.central_heating_controller_status" });
  card.hass = hass;

  await card._toggleAutoMode();

  assert.deepEqual(hass.calls.at(-1), {
    domain: "switch",
    service: "turn_on",
    data: { entity_id: "switch.central_heating_controller_auto_mode" },
  });
});

test("presses the heat blast button", async () => {
  const card = new Card();
  const hass = createHass();
  card.setConfig({ entity: "sensor.central_heating_controller_status" });
  card.hass = hass;

  await card._pressHeatBlast();

  assert.deepEqual(hass.calls.at(-1), {
    domain: "button",
    service: "press",
    data: { entity_id: "button.central_heating_controller_heat_blast" },
  });
});

test("increments high temperature using the number entity step", async () => {
  const card = new Card();
  const hass = createHass();
  card.setConfig({ entity: "sensor.central_heating_controller_status" });
  card.hass = hass;

  await card._adjustNumber(card._entities.highTemperature, 1);

  assert.deepEqual(hass.calls.at(-1), {
    domain: "number",
    service: "set_value",
    data: {
      entity_id: "number.central_heating_controller_high_temperature",
      value: 20.5,
    },
  });
});

test("visual mode renders status journey details and quick actions", () => {
  const card = new Card();
  card.setConfig({
    entity: "sensor.central_heating_controller_status",
    mode: "visual",
  });
  card.hass = createHass();

  assert.match(card.shadowRoot.innerHTML, /Journey/);
  assert.match(card.shadowRoot.innerHTML, /Away/);
  assert.match(card.shadowRoot.innerHTML, /Pre-heating/);
  assert.match(card.shadowRoot.innerHTML, /Heat blast/);
  assert.match(card.shadowRoot.innerHTML, /Pre-heat start/);
  assert.match(card.shadowRoot.innerHTML, /Arrival/);
  assert.match(card.shadowRoot.innerHTML, /Learned rate/);
  assert.match(card.shadowRoot.innerHTML, /1.8 degC\/h/);
  assert.match(card.shadowRoot.innerHTML, /Auto mode on/);
  assert.match(card.shadowRoot.innerHTML, /Heat Blast/);
});

test("settings mode renders all tuning controls", () => {
  const card = new Card();
  card.setConfig({
    entity: "sensor.central_heating_controller_status",
    mode: "settings",
  });
  card.hass = createHass();

  assert.match(card.shadowRoot.innerHTML, /Heating settings/);
  assert.match(card.shadowRoot.innerHTML, /High/);
  assert.match(card.shadowRoot.innerHTML, /Low/);
  assert.match(card.shadowRoot.innerHTML, /Eco/);
  assert.match(card.shadowRoot.innerHTML, /Fallback warm-up/);
  assert.match(card.shadowRoot.innerHTML, /Maximum warm-up/);
  assert.match(card.shadowRoot.innerHTML, /60 min/);
  assert.match(card.shadowRoot.innerHTML, /180 min/);
});

test("settings mode disables unavailable number controls without throwing", () => {
  const card = new Card();
  const hass = createHass({
    states: {
      "number.central_heating_controller_fallback_warmup_minutes": {
        entity_id: "number.central_heating_controller_fallback_warmup_minutes",
        state: "unavailable",
        attributes: { min: 5, max: 360, step: 5, unit_of_measurement: "min" },
      },
    },
  });
  card.setConfig({
    entity: "sensor.central_heating_controller_status",
    mode: "settings",
  });
  card.hass = hass;

  assert.match(card.shadowRoot.innerHTML, /Fallback warm-up/);
  assert.match(card.shadowRoot.innerHTML, /Unavailable/);
  assert.match(card.shadowRoot.innerHTML, /is-disabled/);
  assert.match(card.shadowRoot.innerHTML, /disabled/);
});
