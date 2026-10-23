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
