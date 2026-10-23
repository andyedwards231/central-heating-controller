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
  assert.match(card.shadowRoot.innerHTML, /Mode/);
  assert.match(card.shadowRoot.innerHTML, /Configure schedule and integration settings/);
});

test("renders the smart plan as a clearly labelled preview with its own forecast", () => {
  const card = new Card();
  const hass = createHass({ states: {
    "select.central_heating_controller_controller_mode": { state: "away", attributes: {} },
    "sensor.central_heating_controller_smart_heating_plan": {
      entity_id: "sensor.central_heating_controller_smart_heating_plan",
      state: "preview",
      attributes: {
        explanation: "Heating would start at 17:30.", temperature_unit: "°C", preview: true,
        forecast: [{ datetime: "2026-06-29T12:00:00+01:00", temperature: 18, target_temperature: 19, heating_demand_percent: 30 }],
      },
    },
  } });
  card.setConfig({ entity: "sensor.central_heating_controller_status" });
  card.hass = hass;

  assert.match(card.shadowRoot.innerHTML, /value="away" selected/);
  assert.match(card.shadowRoot.innerHTML, /Preview only/);
  assert.match(card.shadowRoot.innerHTML, /Does not control heating/);
  assert.match(card.shadowRoot.innerHTML, /Heating would start at 17:30/);
  assert.match(card.shadowRoot.innerHTML, /chc-chart-indoor/);
});

test("shows the smart plan explanation when preview is unavailable", () => {
  const card = new Card();
  card.setConfig({ entity: "sensor.central_heating_controller_status" });
  card.hass = createHass({ states: {
    "sensor.central_heating_controller_smart_heating_plan": {
      state: "unavailable", attributes: { explanation: "Weather data is missing." },
    },
  } });

  assert.match(card.shadowRoot.innerHTML, /Preview unavailable/);
  assert.match(card.shadowRoot.innerHTML, /Weather data is missing/);
  const unavailablePanel = card.shadowRoot.innerHTML.match(
    /aria-label="Smart heating preview unavailable">([\s\S]*?)<\/section>/,
  )?.[1] || "";
  assert.doesNotMatch(unavailablePanel, /chc-chart-indoor/);
});

test("controller mode selection calls select.select_option", async () => {
  const card = new Card();
  const hass = createHass();
  card.setConfig({ entity: "sensor.central_heating_controller_status" });
  card.hass = hass;

  await card._selectControllerMode("away");

  assert.deepEqual(hass.calls.at(-1), {
    domain: "select",
    service: "select_option",
    data: {
      entity_id: "select.central_heating_controller_controller_mode",
      option: "away",
    },
  });
});

test("schedule editor loads multiple periods, preserves drafts, and saves the full schedule", async () => {
  const card = new Card();
  const hass = createHass({ states: {
    "sensor.central_heating_controller_smart_heating_plan": {
      state: "disabled",
      attributes: { settings: {
        enabled: true,
        weekly_schedule: { monday: [
          { start: "06:30", end: "08:00" },
          { start: "12:00", end: "13:00" },
          { start: "17:00", end: "22:00" },
        ] },
        night_start: "23:00", night_end: "06:00", night_temperature: 16.5, daytime_drop: 1,
      } },
    },
  } });
  card.setConfig({ entity: "sensor.central_heating_controller_status" });
  card.hass = hass;

  assert.equal(card._scheduleDraft.weekly_schedule.monday.length, 3);
  assert.match(card.shadowRoot.innerHTML, /Schedule and night settings/);
  assert.match(card.shadowRoot.innerHTML, /Enable schedule planning/);
  assert.match(card.shadowRoot.innerHTML, /warm by these times/);
  card._setScheduleEditorOpen(true);
  card._updateScheduleDraft("weekly_schedule.monday.1.start", "12:30");
  hass.states["sensor.central_heating_controller_smart_heating_plan"] = {
    state: "disabled", attributes: { settings: { weekly_schedule: {} } },
  };
  card.hass = hass;
  assert.equal(card._scheduleDraft.weekly_schedule.monday[1].start, "12:30");
  assert.match(card.shadowRoot.innerHTML, /class="chc-schedule-editor" open/);

  await card._saveSchedule();
  assert.deepEqual(hass.calls.at(-1), {
    domain: "central_heating_controller",
    service: "set_smart_schedule",
    data: {
      entity_id: "sensor.central_heating_controller_status",
      enabled: true,
      weekly_schedule: {
        monday: [
          { start: "06:30", end: "08:00" },
          { start: "12:30", end: "13:00" },
          { start: "17:00", end: "22:00" },
        ],
        tuesday: [], wednesday: [], thursday: [], friday: [], saturday: [], sunday: [],
      },
      night_start: "23:00", night_end: "06:00", night_temperature: 16.5, daytime_drop: 1,
    },
  });
  assert.match(card.shadowRoot.innerHTML, /Schedule saved/);
  assert.equal(card._scheduleDraftDirty, false);
  assert.match(card.shadowRoot.innerHTML, /class="chc-schedule-editor" open/);
});

test("schedule editor uses the requested night defaults", () => {
  const card = new Card();
  const draft = card._initialScheduleDraft();
  assert.equal(draft.night_start, "23:00");
  assert.equal(draft.night_end, "07:00");
  assert.equal(draft.daytime_drop, 2);
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

test("finds legacy warm-up and preheat entities retained after an upgrade", () => {
  const card = new Card();
  card.setConfig({ entity: "sensor.central_heating_controller_status", mode: "settings" });
  const hass = createHass();
  for (const [current, legacy] of [
    ["number.central_heating_controller_fallback_warmup_minutes", "number.central_heating_controller_fallback_warm_up_duration"],
    ["number.central_heating_controller_maximum_warmup_minutes", "number.central_heating_controller_maximum_warm_up_duration"],
    ["sensor.central_heating_controller_preheat_start_time", "sensor.central_heating_controller_pre_heat_start_time"],
  ]) {
    hass.states[legacy] = { ...hass.states[current], entity_id: legacy };
    delete hass.states[current];
  }
  card.hass = hass;

  assert.equal(card._entities.fallbackWarmup, "number.central_heating_controller_fallback_warm_up_duration");
  assert.equal(card._entities.maximumWarmup, "number.central_heating_controller_maximum_warm_up_duration");
  assert.equal(card._entities.preheatStart, "sensor.central_heating_controller_pre_heat_start_time");
  assert.match(card.shadowRoot.innerHTML, /60 min/);
  assert.match(card.shadowRoot.innerHTML, /180 min/);
});

test("explicit entity overrides take priority over legacy detection", () => {
  const card = new Card();
  card.setConfig({
    entity: "sensor.central_heating_controller_status",
    entities: { fallback_warmup_minutes: "number.custom_fallback" },
  });
  card.hass = createHass({ states: {
    "number.central_heating_controller_fallback_warmup_minutes": undefined,
    "number.central_heating_controller_fallback_warm_up_duration": { state: "60", attributes: {} },
  } });
  assert.equal(card._entities.fallbackWarmup, "number.custom_fallback");
});

test("visual mode renders a responsive indoor and target forecast chart", () => {
  const card = new Card();
  const hass = createHass({ states: {
    "sensor.central_heating_controller_indoor_temperature_forecast": {
      entity_id: "sensor.central_heating_controller_indoor_temperature_forecast",
      state: "19.2",
      attributes: {
        confidence: "learned", temperature_unit: "°C", reason: "Weather data included",
        assumptions: ["Heating remains enabled"],
        forecast: [
          { datetime: "2026-06-29T12:00:00+01:00", temperature: 18, target_temperature: 19, outdoor_temperature: 12 },
          { datetime: "2026-06-30T12:00:00+01:00", temperature: 19.2, target_temperature: 20, outdoor_temperature: 10 },
        ],
      },
    },
  } });
  card.setConfig({ entity: "sensor.central_heating_controller_status", mode: "visual" });
  card.hass = hass;

  assert.match(card.shadowRoot.innerHTML, /Indoor temperature forecast for the next 24 hours/);
  assert.match(card.shadowRoot.innerHTML, /chc-chart-indoor/);
  assert.match(card.shadowRoot.innerHTML, /chc-chart-target/);
  assert.match(card.shadowRoot.innerHTML, /In 24 hours/);
  assert.match(card.shadowRoot.innerHTML, /Heating remains enabled/);
  assert.match(card.shadowRoot.innerHTML, /local time/);
});

test("forecast entity can be overridden and learning state is explained", () => {
  const card = new Card();
  card.setConfig({ entity: "sensor.central_heating_controller_status", mode: "visual", entities: {
    indoor_temperature_forecast: "sensor.custom_forecast",
  } });
  card.hass = createHass({ states: { "sensor.custom_forecast": {
    entity_id: "sensor.custom_forecast", state: "unknown", attributes: { confidence: "learning" },
  } } });
  assert.equal(card._entities.indoorTemperatureForecast, "sensor.custom_forecast");
  assert.match(card.shadowRoot.innerHTML, /Learning your home’s heating response/);
});

test("keeps a learning estimate visible with local date ticks and recent accuracy", () => {
  const card = new Card();
  const hass = createHass({ states: {
    "sensor.central_heating_controller_indoor_temperature_forecast": {
      entity_id: "sensor.central_heating_controller_indoor_temperature_forecast",
      state: "19",
      attributes: {
        confidence: "learning", temperature_unit: "°C", reason: "Model is still calibrating",
        forecast_accuracy: { "1h": { mae_c: 0.4, count: 3 } },
        forecast: [
          { datetime: "2026-06-29T12:00:00+01:00", temperature: 18, target_temperature: 19, outdoor_temperature: 10 },
          { datetime: "2026-06-29T18:00:00+01:00", temperature: 18.5, target_temperature: null, outdoor_temperature: 9 },
          { datetime: "2026-06-30T12:00:00+01:00", temperature: 19, target_temperature: 20, outdoor_temperature: 8 },
          null,
          { datetime: "2026-06-30T18:00:00+01:00", temperature: "bad", target_temperature: 20 },
        ],
      },
    },
  } });
  card.setConfig({ entity: "sensor.central_heating_controller_status", mode: "control" });
  card.hass = hass;

  assert.match(card.shadowRoot.innerHTML, /Uncalibrated estimate/);
  assert.match(card.shadowRoot.innerHTML, /chc-chart-indoor/);
  assert.match(card.shadowRoot.innerHTML, /chc-chart-vgrid/);
  assert.match(card.shadowRoot.innerHTML, /Mean 1-hour error: 0\.4 °C \(3 checks\)/);
  assert.match(card.shadowRoot.innerHTML, /line x1="48" y1="12" x2="48"/);
  assert.match(card.shadowRoot.innerHTML, /Next 24 hours/);
  assert.doesNotMatch(card.shadowRoot.innerHTML, /NaN/);
});

test("shows reported demand, learned response time, and all populated accuracy horizons", () => {
  const card = new Card();
  const hass = createHass({ states: {
    "sensor.central_heating_controller_status": {
      entity_id: "sensor.central_heating_controller_status", state: "pre_heating",
      attributes: {},
    },
    "sensor.central_heating_controller_indoor_temperature_forecast": {
      entity_id: "sensor.central_heating_controller_indoor_temperature_forecast", state: "unknown",
      attributes: {
        temperature_unit: "°F",
        current_heating_demand_percent: 0, heating_response_minutes: 20,
        heating_response_learned: true, demand_model_learned: true,
        forecast_accuracy: {
          "1h": { mae_c: 0.5, count: 2 },
          "6h": { mae_c: 1, count: 4 },
          "24h": { mae_c: 1.5, count: 6 },
        },
      },
    },
  } });
  card.setConfig({ entity: "sensor.central_heating_controller_status", mode: "control" });
  card.hass = hass;

  assert.match(card.shadowRoot.innerHTML, /Current reported demand: 0\.0 %/);
  assert.match(card.shadowRoot.innerHTML, /Heating response: ~20 min/);
  assert.match(card.shadowRoot.innerHTML, /title="Time to reach about 63% of the effect of a demand change"/);
  assert.match(card.shadowRoot.innerHTML, /Mean 1-hour error: 0\.9 °F \(2 checks\)/);
  assert.match(card.shadowRoot.innerHTML, /Mean 6-hour error: 1\.8 °F \(4 checks\)/);
  assert.match(card.shadowRoot.innerHTML, /Mean 24-hour error: 2\.7 °F \(6 checks\)/);
});

test("shows heating response learning state and skips missing or invalid model and accuracy values", () => {
  const card = new Card();
  const hass = createHass({ states: {
    "sensor.central_heating_controller_status": {
      entity_id: "sensor.central_heating_controller_status", state: "pre_heating",
      attributes: {},
    },
    "sensor.central_heating_controller_indoor_temperature_forecast": {
      entity_id: "sensor.central_heating_controller_indoor_temperature_forecast", state: "unknown",
      attributes: {
        current_heating_demand_percent: null, heating_response_minutes: "NaN",
        heating_response_learned: false, demand_model_learned: false,
        forecast_accuracy: {
          "1h": { mae_c: 0.4, count: 0 },
          "6h": { mae_c: "NaN", count: 3 },
          "24h": { mae_c: null, count: 2 },
        },
      },
    },
  } });
  card.setConfig({ entity: "sensor.central_heating_controller_status", mode: "control" });
  card.hass = hass;

  assert.match(card.shadowRoot.innerHTML, /Learning heating response/);
  assert.doesNotMatch(card.shadowRoot.innerHTML, /Current reported demand|Heating response: ~|Mean (1-hour|6-hour|24-hour) error|NaN/);
});

test("shows an immediate response when a learned response time is zero", () => {
  const card = new Card();
  card.setConfig({ entity: "sensor.central_heating_controller_status", mode: "control" });
  card.hass = createHass({ states: {
    "sensor.central_heating_controller_indoor_temperature_forecast": {
      entity_id: "sensor.central_heating_controller_indoor_temperature_forecast", state: "unknown",
      attributes: { heating_response_minutes: 0, heating_response_learned: true },
    },
  } });

  assert.match(card.shadowRoot.innerHTML, /Heating response: immediate/);
});

const ForecastCard = customElements.get("central-heating-forecast-card");
function kiosk(points, extra = {}) {
  const card = new ForecastCard();
  card.setConfig({ entity: "sensor.central_heating_controller_status" });
  card.hass = createHass({ states: {
    "sensor.central_heating_controller_indoor_temperature_forecast": {
      state: "19", attributes: { forecast: points, confidence: "learning", ...extra },
    },
  } });
  return card;
}
const forecastPoint = (minute, demand, target = 20) => ({
  datetime: new Date(Date.UTC(2026, 9, 24, 23, minute)).toISOString(),
  temperature: 18 + minute / 100, target_temperature: target, heating_demand_percent: demand,
});

test("kiosk shows live current/target and chart without controls or technical diagnostics", () => {
  const card = kiosk([forecastPoint(0, 13), forecastPoint(5, 0)]);
  const html = card.shadowRoot.innerHTML;
  assert.match(html, /16.8 °C/);
  assert.match(html, /20.0 °C/);
  assert.match(html, /Early estimate/);
  assert.match(html, /<svg/);
  assert.match(html, /Heating expected/);
  assert.doesNotMatch(html, /<button|Uncalibrated|model_score|Mean 1-hour|Solar gains/);
});

test("heating shading follows demand intervals and excludes off, tiny, invalid and gap periods", () => {
  const card = kiosk([
    forecastPoint(0, 13), forecastPoint(5, 5), forecastPoint(10, 0),
    forecastPoint(15, 100, null), forecastPoint(20, null), forecastPoint(25, 101),
    forecastPoint(30, 100), forecastPoint(60, 100), forecastPoint(65, 0),
  ]);
  const bands = card.shadowRoot.innerHTML.match(/<rect class="chc-heating-band"[^>]+/g);
  assert.equal(bands.length, 2);
  assert.doesNotMatch(card.shadowRoot.innerHTML, /NaN/);
});

test("missing or malformed forecasts show a concise fallback", () => {
  for (const points of [[], [null, { datetime: "invalid", temperature: 20 }], [forecastPoint(0, 0)]]) {
    const html = kiosk(points).shadowRoot.innerHTML;
    assert.match(html, /Forecast unavailable/);
    assert.doesNotMatch(html, /<svg|NaN/);
  }
});

test("kiosk supports Fahrenheit and missing live status without inventing readings", () => {
  const card = kiosk([forecastPoint(0, 0), forecastPoint(5, 0)], { temperature_unit: "°F" });
  assert.match(card.shadowRoot.innerHTML, /°F/);
  card.hass.states["sensor.central_heating_controller_status"] = undefined;
  card.hass = card.hass;
  assert.match(card.shadowRoot.innerHTML, /chc-kiosk-now">—/);
  assert.match(card.shadowRoot.innerHTML, /<svg/);
});

test("main control and visual cards shade forecast heating with the same cached renderer", () => {
  for (const mode of ["control", "visual"]) {
    const card = new Card();
    card.setConfig({ entity: "sensor.central_heating_controller_status", mode });
    card.hass = kiosk([forecastPoint(0, 13), forecastPoint(5, 0), forecastPoint(10, 0)]).hass;
    assert.equal((card.shadowRoot.innerHTML.match(/<rect class="chc-heating-band"/g) || []).length, 1);
    assert.match(card.shadowRoot.innerHTML, /Heating expected/);
  }
});
