import assert from "node:assert/strict";
import test from "node:test";
import "./dom-shim.mjs";
import { createHass } from "./fixtures.mjs";
import "../../www/central-heating-controller-card.js";

const Card = customElements.get("central-heating-controller-card");
const STATUS = "sensor.central_heating_controller_status";

function makeCard(mode, extraStates = {}) {
  const card = new Card();
  card.setConfig({ entity: STATUS, mode });
  card.hass = createHass({ states: extraStates });
  return card;
}

test("overview shows the main forecast, live controls and explicit missing costs", () => {
  const card = makeCard("overview");
  assert.match(card.shadowRoot.innerHTML, /Next 24 hours/);
  assert.match(card.shadowRoot.innerHTML, /Current target/);
  assert.match(card.shadowRoot.innerHTML, /Heating mode[\s\S]*disabled/);
  assert.doesNotMatch(card.shadowRoot.innerHTML, /data-action="auto"/);
  assert.match(card.shadowRoot.innerHTML, /Estimated heating cost today[\s\S]*Unavailable/);
  assert.match(card.shadowRoot.innerHTML, /preview is for planning only/i);
});

test("overview formats current temperature with its unit and displays a real zero cost", () => {
  const today = "sensor.central_heating_controller_predicted_heating_cost_today";
  const card = makeCard("overview", {
    [today]: { state: "0", attributes: { unit_of_measurement: "GBP" } },
  });
  assert.match(card.shadowRoot.innerHTML, /16\.8 °C/);
  assert.match(card.shadowRoot.innerHTML, /0 GBP/);
  assert.match(card.shadowRoot.innerHTML, /Estimated heating cost tomorrow[\s\S]*Unavailable/);
});

test("schedule view states preview-only and exposes the editable live High setpoint", () => {
  const planId = "sensor.central_heating_controller_smart_heating_plan";
  const card = makeCard("schedule", {
    [planId]: { state: "preview", attributes: { preview: true, settings: { enabled: false, weekly_schedule: {} }, forecast: [] } },
  });
  assert.match(card.shadowRoot.innerHTML, /Preview only/);
  assert.match(card.shadowRoot.innerHTML, /Comfort temperature also changes the High setting/);
  assert.match(card.shadowRoot.innerHTML, /data-schedule-form/);
  assert.match(card.shadowRoot.innerHTML, /<details class="chc-schedule-editor" open>/);
  assert.doesNotMatch(card.shadowRoot.innerHTML, /Estimated heating cost/);
});

test("schedule distinguishes disabled and unavailable previews and never edits absent settings", () => {
  const disabled = makeCard("schedule", {
    "sensor.central_heating_controller_smart_heating_plan": { state: "off", attributes: { settings: {} } },
  });
  assert.match(disabled.shadowRoot.innerHTML, /disabled in the integration options/);
  assert.doesNotMatch(disabled.shadowRoot.innerHTML, /data-schedule-form/);

  const unavailable = makeCard("schedule", {
    "sensor.central_heating_controller_smart_heating_plan": { state: "unavailable", attributes: {} },
  });
  assert.match(unavailable.shadowRoot.innerHTML, /preview is unavailable/i);
  assert.doesNotMatch(unavailable.shadowRoot.innerHTML, /data-schedule-form/);
});

test("learning view reports forecast horizons and available sample and model measures", () => {
  const forecastId = "sensor.central_heating_controller_indoor_temperature_forecast";
  const card = makeCard("learning", {
    [forecastId]: { state: "18", attributes: {
      forecast_accuracy: { "1h": { mae_c: 0.4, count: 6 }, "6h": { mae_c: 0.8, count: 3 } },
      learned_samples: 12, heating_response_learned: true, heating_response_minutes: 18,
    } },
  });
  assert.match(card.shadowRoot.innerHTML, /1h forecast accuracy/);
  assert.match(card.shadowRoot.innerHTML, /6h forecast accuracy/);
  assert.match(card.shadowRoot.innerHTML, /24h forecast accuracy/);
  assert.match(card.shadowRoot.innerHTML, /12/);
  assert.match(card.shadowRoot.innerHTML, /Heating response delay/);
  assert.match(card.shadowRoot.innerHTML, /mean absolute error · 6 readings/);
  assert.doesNotMatch(card.shadowRoot.innerHTML, /Next 24 hours/);
});

test("dashboard views track only their own entities", () => {
  const card = makeCard("overview");
  let renders = 0;
  const render = card._render.bind(card);
  card._render = () => { renders += 1; render(); };
  card.hass.states["sensor.central_heating_controller_learned_heating_rate"] = { state: "2", attributes: {} };
  card.hass = card.hass;
  assert.equal(renders, 0);
  card.hass.states["sensor.central_heating_controller_predicted_heating_cost_today"] = { state: "0", attributes: { unit_of_measurement: "GBP" } };
  card.hass = card.hass;
  assert.equal(renders, 1);
});

test("overview control actions call the existing Home Assistant services", async () => {
  const card = makeCard("overview", {
    "select.central_heating_controller_controller_mode": { state: "auto", attributes: {} },
  });
  await card._selectControllerMode("away");
  await card._pressHeatBlast();
  assert.deepEqual(card.hass.calls.slice(-2).map(call => [call.domain, call.service, call.data.option]), [
    ["select", "select_option", "away"], ["button", "press", undefined],
  ]);
});

test("schedule draft values survive a Home Assistant state refresh", () => {
  const plan = "sensor.central_heating_controller_smart_heating_plan";
  const card = makeCard("schedule", {
    [plan]: { state: "preview", attributes: { preview: true, settings: { enabled: true } } },
  });
  card._updateScheduleDraft("night_start", "22:15");
  card.hass.states["sensor.central_heating_controller_status"] = {
    state: "high", attributes: { current_temperature: 17 },
  };
  card.hass = card.hass;
  assert.match(card.shadowRoot.innerHTML, /data-draft-path="night_start" value="22:15"/);
});

test("schedule draft survives the status entity disappearing during an integration reload", () => {
  const plan = "sensor.central_heating_controller_smart_heating_plan";
  const status = "sensor.central_heating_controller_status";
  const card = makeCard("schedule", {
    [plan]: { state: "preview", attributes: { preview: true, settings: { enabled: true } } },
  });
  card._updateScheduleDraft("night_start", "22:15");

  card.hass = { ...card.hass, states: { ...card.hass.states, [status]: undefined } };
  assert.match(card.shadowRoot.innerHTML, /Status entity not found/);

  card.hass = createHass({ states: {
    [plan]: { state: "preview", attributes: { preview: true, settings: { enabled: true } } },
  } });
  assert.match(card.shadowRoot.innerHTML, /data-draft-path="night_start" value="22:15"/);
  assert.equal(card._scheduleDraftDirty, true);
});

test("native forecast units take precedence over Home Assistant units", () => {
  const card = makeCard("overview", {
    'sensor.central_heating_controller_indoor_temperature_forecast': {state:'20', attributes:{temperature_unit:'°C'}},
  });
  card.hass = {...card.hass, config:{unit_system:{temperature:'°F'}}};
  assert.match(card.shadowRoot.innerHTML, /16\.8 °C/);
});

test("preview input failure leaves saved schedule settings editable", () => {
  const card = makeCard('schedule', {
    'sensor.central_heating_controller_smart_heating_plan': {state:'unavailable', attributes:{explanation:'Waiting for weather', settings:{enabled:true,night_start:'23:00',night_end:'07:00',night_temperature:16,daytime_drop:2,weekly_schedule:{}}}},
  });
  assert.match(card.shadowRoot.innerHTML, /Waiting for weather/);
  assert.match(card.shadowRoot.innerHTML, /data-schedule-form/);
});

test("learning converts temperature differences to Fahrenheit and retains actual counts", () => {
  const card = makeCard('learning', {
    'sensor.central_heating_controller_indoor_temperature_forecast': {state:'68',attributes:{temperature_unit:'°F',forecast_accuracy:{'1h':{mae_c:1,count:5}}}},
  });
  assert.match(card.shadowRoot.innerHTML, /1\.8 °F mean absolute error · 5 readings/);
});

const readinessId = "sensor.central_heating_controller_smart_heating_readiness";
const controlId = "switch.central_heating_controller_smart_heating_control";

test("schedule readiness explains progress and enables explicit opt-in only when ready", async () => {
  const card = makeCard("schedule", {
    "select.central_heating_controller_controller_mode": { state: "auto", attributes: { options: ["auto", "away", "off"] } },
    [readinessId]: { state: "learning", attributes: { ready: false, control_state: "preview", explanation: "Collecting more readings.", checks: [{ key: "samples", label: "Heating history", passed: false, detail: "74 of 100 required samples." }] } },
    [controlId]: { state: "off", attributes: {} },
    "sensor.central_heating_controller_smart_heating_plan": { state: "preview", attributes: { preview: true, settings: { enabled: false } } },
  });
  assert.match(card.shadowRoot.innerHTML, /Still learning/);
  assert.match(card.shadowRoot.innerHTML, /Heating history/);
  assert.match(card.shadowRoot.innerHTML, /74 of 100 required samples/);
  assert.match(card.shadowRoot.innerHTML, /data-action="smart-control:on" disabled/);
  assert.match(card.shadowRoot.innerHTML, /Enable intelligent heating/);
  await card._setSmartHeatingControl(true);
  assert.equal(card.hass.calls.length, 0);

  card.hass.states[readinessId] = { state: "ready", attributes: { ready: true, control_state: "preview", checks: [] } };
  card.hass = card.hass;
  assert.match(card.shadowRoot.innerHTML, /Ready to enable/);
  assert.match(card.shadowRoot.innerHTML, /data-action="smart-control:on"(?! disabled)/);
  await card._setSmartHeatingControl(true);
  assert.deepEqual(card.hass.calls.at(-1), { domain: "switch", service: "turn_on", data: { entity_id: controlId } });
});

test("missing readiness is safe, while an enabled switch can always be turned off", async () => {
  const card = makeCard("schedule", {
    [controlId]: { state: "on", attributes: {} },
    "sensor.central_heating_controller_smart_heating_plan": { state: "unavailable", attributes: {} },
  });
  assert.match(card.shadowRoot.innerHTML, /Intelligent heating unavailable/);
  assert.match(card.shadowRoot.innerHTML, /data-action="smart-control:off"/);
  assert.match(card.shadowRoot.innerHTML, /Return to existing schedule/);
  await card._setSmartHeatingControl(true);
  assert.equal(card.hass.calls.length, 0);
  await card._setSmartHeatingControl(false);
  assert.deepEqual(card.hass.calls.at(-1), { domain: "switch", service: "turn_off", data: { entity_id: controlId } });
});

test("unknown readiness disables opt-in and never calls turn_on", async () => {
  const card = makeCard("schedule", {
    "select.central_heating_controller_controller_mode": { state: "auto", attributes: {} },
    [readinessId]: { state: "unknown", attributes: { ready: true } },
    [controlId]: { state: "off", attributes: {} },
  });
  assert.match(card.shadowRoot.innerHTML, /Intelligent heating unavailable/);
  assert.match(card.shadowRoot.innerHTML, /data-action="smart-control:on" disabled/);
  await card._setSmartHeatingControl(true);
  assert.equal(card.hass.calls.length, 0);
});

test("readiness summary describes active and fallback states without preview claims", () => {
  const active = makeCard("schedule", {
    [readinessId]: { state: "ready", attributes: { ready: true, control_state: "active", explanation: "The learned plan is running." } },
    [controlId]: { state: "on", attributes: {} },
    "sensor.central_heating_controller_smart_heating_plan": { state: "active", attributes: { preview: false, forecast: [
      { datetime: "2026-06-29T12:00:00Z", temperature: 19, target_temperature: 20, heating_demand_percent: 30 },
      { datetime: "2026-06-29T13:00:00Z", temperature: 19.5, target_temperature: 20, heating_demand_percent: 20 },
    ] } },
  });
  assert.match(active.shadowRoot.innerHTML, /Intelligent heating active/);
  assert.match(active.shadowRoot.innerHTML, /Controls heating/);
  assert.match(active.shadowRoot.innerHTML, /Intelligent heating plan for the next 24 hours/);
  assert.doesNotMatch(active.shadowRoot.innerHTML, /Preview only/);

  const fallback = makeCard("schedule", {
    [readinessId]: { state: "blocked", attributes: { ready: false, control_state: "fallback", fallback_reason: "Forecast input is unavailable." } },
    [controlId]: { state: "off", attributes: {} },
  });
  assert.match(fallback.shadowRoot.innerHTML, /Using existing schedule/);
  assert.match(fallback.shadowRoot.innerHTML, /Forecast input is unavailable/);
});

test("schedule checklist is collapsed while learning checklist starts open", () => {
  const states = {
    [readinessId]: { state: "learning", attributes: { ready: false, control_state: "preview", checks: [{ label: "History", passed: false, detail: "12 of 20 samples" }] } },
    [controlId]: { state: "off", attributes: {} },
  };
  const schedule = makeCard("schedule", states);
  assert.match(schedule.shadowRoot.innerHTML, /<details class="chc-readiness-checks"><summary>Readiness checks \(1 remaining\)<\/summary>/);
  assert.doesNotMatch(schedule.shadowRoot.innerHTML, /class="chc-readiness-checks" open/);
  assert.doesNotMatch(schedule.shadowRoot.innerHTML, /What still needs attention/);
  const learning = makeCard("learning", states);
  assert.match(learning.shadowRoot.innerHTML, /<details class="chc-readiness-checks" open><summary>Readiness checks \(1 remaining\)<\/summary>/);
});

test("paused mode and overrides explain control without claiming the existing schedule is active", () => {
  const card = makeCard("schedule", {
    "select.central_heating_controller_controller_mode": { state: "off", attributes: {} },
    [readinessId]: { state: "ready", attributes: { ready: true, control_state: "active" } },
    [controlId]: { state: "on", attributes: {} },
    "sensor.central_heating_controller_smart_heating_plan": { state: "active", attributes: { preview: false } },
  });
  assert.match(card.shadowRoot.innerHTML, /Intelligent heating paused/);
  assert.match(card.shadowRoot.innerHTML, /Heating mode is Off/);
  assert.match(card.shadowRoot.innerHTML, /Heating mode or override is in control/);
  assert.doesNotMatch(card.shadowRoot.innerHTML, /existing schedule controls heating/i);
  assert.doesNotMatch(card.shadowRoot.innerHTML, /Intelligent heating active/);
  assert.match(card.shadowRoot.innerHTML, /Disable intelligent heating/);
});

test("opt-in requires Auto mode, and pending service calls cannot be duplicated", async () => {
  const card = makeCard("schedule", {
    "select.central_heating_controller_controller_mode": { state: "away", attributes: {} },
    [readinessId]: { state: "ready", attributes: { ready: true, control_state: "preview" } },
    [controlId]: { state: "off", attributes: {} },
  });
  assert.match(card.shadowRoot.innerHTML, /Heating mode is Away/);
  assert.match(card.shadowRoot.innerHTML, /data-action="smart-control:on" disabled/);
  await card._setSmartHeatingControl(true);
  assert.equal(card.hass.calls.length, 0);

  card.hass.states["select.central_heating_controller_controller_mode"] = { state: "auto", attributes: {} };
  card.hass = card.hass;
  let release;
  card.hass.callService = (...args) => {
    card.hass.calls.push({ domain: args[0], service: args[1], data: args[2] });
    return new Promise(resolve => { release = resolve; });
  };
  const pending = card._setSmartHeatingControl(true);
  assert.equal(card._smartControlPending, true);
  assert.match(card.shadowRoot.innerHTML, /Enabling…/);
  assert.match(card.shadowRoot.innerHTML, /data-action="smart-control:on" disabled/);
  await card._setSmartHeatingControl(true);
  assert.equal(card.hass.calls.length, 1);
  release();
  await pending;
  assert.equal(card._smartControlPending, false);
});

test("a switch-on state without a reported control mode is never shown as active", () => {
  const card = makeCard("schedule", {
    "select.central_heating_controller_controller_mode": { state: "auto", attributes: {} },
    [readinessId]: { state: "ready", attributes: { ready: true } },
    [controlId]: { state: "on", attributes: {} },
  });
  assert.match(card.shadowRoot.innerHTML, /Intelligent heating status unavailable/);
  assert.doesNotMatch(card.shadowRoot.innerHTML, /Intelligent heating active/);
  assert.doesNotMatch(card.shadowRoot.innerHTML, /Preview — the existing schedule controls heating/);
});

test("dashboard views subscribe to readiness only where it is shown", () => {
  const card = makeCard("overview");
  let renders = 0;
  const render = card._render.bind(card);
  card._render = () => { renders += 1; render(); };
  card.hass.states[readinessId] = { state: "learning", attributes: { ready: false } };
  card.hass = card.hass;
  assert.equal(renders, 1);
  renders = 0;
  card.hass.states["sensor.central_heating_controller_learned_heating_rate"] = { state: "3", attributes: {} };
  card.hass = card.hass;
  assert.equal(renders, 0);
  card.setConfig({ entity: STATUS, mode: "schedule" });
  renders = 0;
  card.hass.states["sensor.central_heating_controller_predicted_heating_cost_today"] = { state: "2", attributes: {} };
  card.hass = card.hass;
  assert.equal(renders, 0);
  card.hass.states[readinessId] = { state: "ready", attributes: { ready: true } };
  card.hass = card.hass;
  assert.equal(renders, 1);
});
