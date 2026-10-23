import assert from "node:assert/strict";
import test from "node:test";
import "./dom-shim.mjs";
import { createHass } from "./fixtures.mjs";
import "../../www/central-heating-controller-card.js";

const Card = customElements.get("central-heating-controller-card");
const KioskCard = customElements.get("central-heating-forecast-card");
const STATUS = "sensor.central_heating_controller_status";
const FORECAST = "sensor.central_heating_controller_indoor_temperature_forecast";

function trackedCard(CardType = Card, config = {}) {
  const card = new CardType();
  let renders = 0;
  const render = card._render.bind(card);
  card._render = (...args) => { renders += 1; return render(...args); };
  card.setConfig({ entity: STATUS, ...config });
  card.hass = createHass();
  renders = 0;
  return { card, count: () => renders, reset: () => { renders = 0; } };
}

function status(attrs = {}, state = "pre_heating") {
  return { entity_id: STATUS, state, attributes: { current_temperature: 16.8, ...attrs } };
}

function forecast(attrs = {}, state = "0") {
  return { entity_id: FORECAST, state, attributes: { confidence: "learning", ...attrs } };
}

test("control mode skips unrelated state changes and renders relevant replacements", () => {
  const { card, count } = trackedCard();
  const hass = card.hass;
  hass.states["sensor.unrelated"] = { state: "1", attributes: {} };
  card.hass = hass;
  assert.equal(count(), 0);

  hass.states[STATUS] = status({ current_temperature: 17.1 });
  card.hass = hass;
  assert.equal(count(), 1);

  hass.states[STATUS] = status({ current_temperature: 17.1 });
  card.hass = hass;
  assert.equal(count(), 2, "a new state reference should render even when its values match");
});

test("control mode notices attribute-only changes and relevant source appearance or removal", () => {
  const { card, count } = trackedCard();
  const hass = card.hass;
  hass.states[STATUS] = status({ reason: "Updated reason" });
  card.hass = hass;
  assert.equal(count(), 1);

  hass.states["number.central_heating_controller_low_temperature"] = undefined;
  card.hass = hass;
  assert.equal(count(), 2);
  hass.states["number.central_heating_controller_low_temperature"] = {
    state: "17.0", attributes: { min: 7, max: 35, step: 0.5, unit_of_measurement: "degC" },
  };
  card.hass = hass;
  assert.equal(count(), 3);
});

test("control mode tracks the mode selector and smart plan preview entity", () => {
  const { card, count } = trackedCard();
  const hass = card.hass;
  hass.states["select.central_heating_controller_controller_mode"] = { state: "away", attributes: {} };
  card.hass = hass;
  assert.equal(count(), 1);
  assert.match(card.shadowRoot.innerHTML, /value="away" selected/);
  hass.states["sensor.central_heating_controller_smart_heating_plan"] = {
    state: "preview", attributes: { preview: true, forecast: [], explanation: "Plan updated" },
  };
  card.hass = hass;
  assert.equal(count(), 2);
  assert.match(card.shadowRoot.innerHTML, /Plan updated/);
});

test("configuration changes force rendering; equivalent metadata does not", () => {
  const { card, count } = trackedCard();
  card.setConfig({ entity: STATUS, title: "New title" });
  assert.equal(count(), 1);
  card.hass = { ...card.hass, themes: { darkMode: false } };
  assert.equal(count(), 2);
  card.hass = { ...card.hass, themes: { darkMode: false } };
  assert.equal(count(), 2);
  card.hass = { ...card.hass, selectedTheme: "dark" };
  assert.equal(count(), 3);
  card.hass = { ...card.hass, selectedTheme: { primaryColor: "#123456" } };
  assert.equal(count(), 4);
  card.hass = { ...card.hass, selectedTheme: { primaryColor: "#123456" } };
  assert.equal(count(), 4, "equivalent new theme metadata objects should be ignored");
  card.hass = { ...card.hass, themes: { darkMode: true } };
  assert.equal(count(), 5, "dark mode changes should render");
});

test("base mode tracks formatter and entity registry changes; kiosk ignores them", () => {
  const standard = trackedCard();
  standard.card.hass = { ...standard.card.hass, formatEntityState: state => `formatted ${state.state}` };
  assert.equal(standard.count(), 1);
  standard.card.hass = { ...standard.card.hass, entities: { "sensor.some_entity": {} } };
  assert.equal(standard.count(), 2);

  const kiosk = trackedCard(KioskCard, { mode: "kiosk" });
  kiosk.card.hass = { ...kiosk.card.hass, formatEntityState: state => `formatted ${state.state}` };
  assert.equal(kiosk.count(), 0);
  kiosk.card.hass = { ...kiosk.card.hass, entities: { "sensor.some_entity": {} } };
  assert.equal(kiosk.count(), 0);
});

test("locale and time preference changes render with stable state references", () => {
  const { card, count, reset } = trackedCard();
  const hass = card.hass;
  hass.locale = { language: "en", time_format: "system", date_format: "language", number_format: "language", time_zone: "server" };
  card.hass = hass;
  reset();
  for (const [key, value] of Object.entries({
    language: "fr", time_format: "24", date_format: "DMY", number_format: "comma_decimal", time_zone: "local",
  })) {
    hass.locale = { ...hass.locale, [key]: value };
    card.hass = hass;
  }
  hass.config = { time_zone: "Europe/Paris" };
  card.hass = hass;
  assert.equal(count(), 6, "five locale changes plus the Home Assistant config time zone should render");
});

test("latest hass callbacks are retained when a state update is skipped", async () => {
  const { card, count } = trackedCard();
  const original = card.hass;
  let oldCalls = 0;
  let newCalls = 0;
  original.callService = () => { oldCalls += 1; };
  card.hass = original;
  count();

  const latest = {
    ...original,
    states: { ...original.states, "sensor.unrelated": { state: "2", attributes: {} } },
    callService: () => { newCalls += 1; },
  };
  card.hass = latest;
  assert.equal(count(), 0, "the unrelated state should not render");
  await card._toggleAutoMode();
  assert.equal(oldCalls, 0);
  assert.equal(newCalls, 1, "actions should use the most recently assigned hass object");
});

test("kiosk depends on status and forecast but ignores control-only sources", () => {
  const { card, count } = trackedCard(KioskCard, { mode: "kiosk" });
  const hass = card.hass;
  hass.states["switch.central_heating_controller_auto_mode"] = { state: "off", attributes: {} };
  card.hass = hass;
  assert.equal(count(), 0);

  hass.states[FORECAST] = forecast({ confidence: "settled" });
  card.hass = hass;
  assert.equal(count(), 1);
  hass.states[STATUS] = status({ current_temperature: 17.3 });
  card.hass = hass;
  assert.equal(count(), 2);
});

test("kiosk tracks the configured forecast entity and ignores the derived default", () => {
  const configured = "sensor.custom_indoor_forecast";
  const { card, count } = trackedCard(KioskCard, {
    mode: "kiosk",
    entities: { indoor_temperature_forecast: configured },
  });
  const hass = card.hass;
  hass.states[FORECAST] = forecast({ confidence: "settled" });
  card.hass = hass;
  assert.equal(count(), 0);
  hass.states[configured] = forecast({ confidence: "settled" });
  card.hass = hass;
  assert.equal(count(), 1);
});

test("legacy state fallback changes when the standard or renamed override appears", () => {
  const legacy = "sensor.central_heating_controller_pre_heat_start_time";
  const standard = "sensor.central_heating_controller_preheat_start_time";
  const { card, count } = trackedCard();
  const hass = card.hass;
  delete hass.states[standard];
  hass.states[legacy] = { state: "2026-06-29T17:40:00+01:00", attributes: {} };
  card.hass = hass;
  assert.equal(count(), 1);
  assert.equal(card._entities.preheatStart, legacy);
  hass.states[standard] = { state: "2026-06-29T17:45:00+01:00", attributes: {} };
  card.hass = hass;
  assert.equal(count(), 2);
  assert.equal(card._entities.preheatStart, standard);
});

test("forecast computation is reused for status-only renders and rebuilt for forecast changes", () => {
  const { card } = trackedCard();
  let builds = 0;
  const build = card._buildForecast.bind(card);
  card._buildForecast = (...args) => { builds += 1; return build(...args); };
  card.hass = createHass();
  const initial = builds;
  card.hass.states[STATUS] = status({ current_temperature: 18 });
  card.hass = card.hass;
  assert.equal(builds, initial, "status-only change should reuse derived forecast rows");
  card.hass.states[FORECAST] = forecast({ confidence: "settled" });
  card.hass = card.hass;
  assert.equal(builds, initial + 1, "new forecast state reference should rebuild derived rows");
});

test("date formatters are cached until locale inputs change and honour server time zone", () => {
  const OriginalDateTimeFormat = Intl.DateTimeFormat;
  let constructions = 0;
  Intl.DateTimeFormat = class extends OriginalDateTimeFormat {
    constructor(...args) { constructions += 1; super(...args); }
  };
  try {
    const { card } = trackedCard();
    const hass = card.hass;
    hass.locale = {
      language: "en-GB", time_format: "24", date_format: "language",
      number_format: "language", time_zone: "server",
    };
    hass.config = { time_zone: "Europe/London" };
    card.hass = hass;
    const london = card._formatTime("2026-01-01T12:00:00Z");
    assert.equal(london, "12:00");
    const firstCount = constructions;
    hass.states[STATUS] = status({ current_temperature: 17.9 });
    card.hass = hass;
    assert.equal(constructions, firstCount, "status-only changes should reuse formatter instances");
    hass.states[FORECAST] = forecast({ confidence: "settled" });
    card.hass = hass;
    assert.equal(constructions, firstCount, "forecast changes should reuse formatters while locale is stable");

    hass.config = { time_zone: "America/New_York" };
    card.hass = hass;
    const newYork = card._formatTime("2026-01-01T12:00:00Z");
    assert.equal(newYork, "07:00");
    assert.equal(constructions, firstCount + 2, "server time zone changes should rebuild both formatters");

    hass.locale = { ...hass.locale, language: "fr-FR" };
    card.hass = hass;
    card._formatTime("2026-01-01T12:00:00Z");
    assert.equal(constructions, firstCount + 4, "language changes should rebuild both formatters");
    hass.locale = { ...hass.locale, time_format: "12" };
    card.hass = hass;
    card._formatTime("2026-01-01T12:00:00Z");
    assert.equal(constructions, firstCount + 6, "time format changes should rebuild both formatters");
  } finally {
    Intl.DateTimeFormat = OriginalDateTimeFormat;
  }
});
