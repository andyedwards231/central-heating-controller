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

test("overview leads with actual-policy forecast and plain current values", () => {
  const forecast = "sensor.central_heating_controller_indoor_temperature_forecast";
  const plan = "sensor.central_heating_controller_smart_heating_plan";
  const card = makeCard("overview", {
    [forecast]: { state: "18.1", attributes: { confidence: "learned", temperature_unit: "°C", forecast: [
      { datetime: "2026-06-29T12:00:00Z", temperature: 18, target_temperature: 20, heating_demand_percent: 30 },
      { datetime: "2026-06-29T12:05:00Z", temperature: 18.1, target_temperature: 20, heating_demand_percent: 20 },
    ] } },
    [plan]: { state: "preview", attributes: { preview: true, forecast: [
      { datetime: "2026-06-29T12:00:00Z", temperature: 10, target_temperature: 12 },
      { datetime: "2026-06-29T12:05:00Z", temperature: 10, target_temperature: 12 },
    ] } },
    [STATUS]: { state: "low", attributes: { current_temperature: 18, effective_target_temperature: 20, reason: "schedule_low", climate_entity: "climate.hallway", schedule_entity: "schedule.heating", current_heating_demand_percent: 0 } },
    "sensor.central_heating_controller_smart_heating_readiness": { state: "learning", attributes: { control_state: "preview", ready: false } },
  });
  const html = card.shadowRoot.innerHTML;
  assert.match(html, /Target now/);
  assert.match(html, /Inside now/);
  assert.match(html, /18\.0 °C/);
  assert.match(html, /Your existing schedule is controlling the heating/);
  assert.match(html, /Expected temperature/);
  assert.match(html, /Heating idle/);
  assert.match(html, /Past temperatures/);
  assert.doesNotMatch(html, /Proposed heating plan/);
  assert.doesNotMatch(html, /Readiness checks/);
  assert.doesNotMatch(html, /What is still needed/);
});

test("schedule editor defaults from saved periods, shows inherited timetable, and keeps details collapsed", () => {
  const plan = "sensor.central_heating_controller_smart_heating_plan";
  const card = makeCard("schedule", {
    [plan]: { state: "preview", attributes: {
      preview: true,
      settings: { enabled: false, weekly_schedule: { monday: [{ start: "06:30", end: "08:00" }] }, night_start: "23:00", night_end: "07:00", night_temperature: 16, daytime_drop: 2 },
      schedule_entity: "schedule.heating",
      existing_schedule: { monday: [{ start: "07:00", end: "09:00" }], tuesday: [], wednesday: [], thursday: [], friday: [], saturday: [], sunday: [] },
    } },
  });
  const html = card.shadowRoot.innerHTML;
  assert.match(html, /value="custom" data-schedule-source checked/);
  assert.match(html, /Set comfort times here/);
  assert.match(html, /type="number" step="0\.5" data-draft-path="night_temperature"/);
  assert.match(html, /type="number"[^>]*step="0\.5"[^>]*data-draft-path="daytime_drop"/);
  assert.match(html, /See the automatic timing preview/);
  assert.doesNotMatch(html, /data-detail="preview" open/);
  assert.doesNotMatch(html, /data-detail="night" open/);
  assert.match(html, /Your existing schedule still runs the heating[\s\S]*will not take over until you enable it/i);

  card._setScheduleSource("existing");
  assert.match(card.shadowRoot.innerHTML, /value="existing" data-schedule-source checked/);
  assert.match(card.shadowRoot.innerHTML, /Monday[\s\S]*07:00–09:00/);
  assert.match(card.shadowRoot.innerHTML, /schedule\.heating/);
});

test("choosing the inherited source saves no implicit custom periods", async () => {
  const plan = "sensor.central_heating_controller_smart_heating_plan";
  const card = makeCard("schedule", {
    [plan]: { state: "preview", attributes: { settings: {
      enabled: true, weekly_schedule: { monday: [{ start: "06:30", end: "08:00" }] },
    } } },
  });
  card._setScheduleSource("existing");
  await card._saveSchedule();
  assert.deepEqual(card.hass.calls.at(-1), {
    domain: "central_heating_controller", service: "set_smart_schedule", data: {
      entity_id: STATUS, enabled: true, weekly_schedule: {}, night_start: "23:00", night_end: "07:00", night_temperature: 16, daytime_drop: 2,
    },
  });
});

test("custom source cannot save until at least one comfort period exists", async () => {
  const plan = "sensor.central_heating_controller_smart_heating_plan";
  const card = makeCard("schedule", {
    [plan]: { state: "preview", attributes: { settings: { enabled: true, weekly_schedule: {} } } },
  });
  card._setScheduleSource("custom");
  await card._saveSchedule();
  assert.equal(card.hass.calls.length, 0);
  assert.match(card.shadowRoot.innerHTML, /Add a comfort period, or choose your existing schedule/);
});

test("schedule drafts remain intact through Home Assistant refresh and missing status reload", () => {
  const plan = "sensor.central_heating_controller_smart_heating_plan";
  const card = makeCard("schedule", { [plan]: { state: "preview", attributes: { settings: { enabled: true } } } });
  card._updateScheduleDraft("night_start", "22:15");
  card.hass.states[STATUS] = { state: "high", attributes: { current_temperature: 17 } };
  card.hass = card.hass;
  assert.match(card.shadowRoot.innerHTML, /data-draft-path="night_start" value="22:15"/);
  card.hass = createHass({ states: { [plan]: { state: "preview", attributes: { settings: { enabled: true } } } } });
  assert.equal(card._scheduleDraftDirty, true);
  assert.match(card.shadowRoot.innerHTML, /data-draft-path="night_start" value="22:15"/);
});

test("learning owns automatic timing opt-in and refuses unsafe turn-on requests", async () => {
  const readiness = "sensor.central_heating_controller_smart_heating_readiness";
  const control = "switch.central_heating_controller_smart_heating_control";
  const mode = "select.central_heating_controller_controller_mode";
  const card = makeCard("learning", {
    [readiness]: { state: "learning", attributes: { ready: false, control_state: "preview", checks: [{ label: "Heating history", passed: false, detail: "74 readings" }] } },
    [control]: { state: "off", attributes: {} },
    [mode]: { state: "auto", attributes: {} },
  });
  assert.match(card.shadowRoot.innerHTML, /Enable automatic timing/);
  assert.match(card.shadowRoot.innerHTML, /data-action="smart-control:on" disabled/);
  await card._setSmartHeatingControl(true);
  assert.equal(card.hass.calls.length, 0);

  card.hass.states[readiness] = { state: "ready", attributes: { ready: true, control_state: "preview", checks: [] } };
  card.hass.states[mode] = { state: "away", attributes: {} };
  card.hass = card.hass;
  await card._setSmartHeatingControl(true);
  assert.equal(card.hass.calls.length, 0);

  card.hass.states[mode] = { state: "auto", attributes: {} };
  card.hass = card.hass;
  await card._setSmartHeatingControl(true);
  assert.deepEqual(card.hass.calls.at(-1), { domain: "switch", service: "turn_on", data: { entity_id: control } });

  const overview = makeCard("overview", { [readiness]: { state: "ready", attributes: { ready: true } } });
  assert.doesNotMatch(overview.shadowRoot.innerHTML, /Enable automatic timing/);
});

test("diagnostics start collapsed and escape untrusted readiness text", () => {
  const readiness = "sensor.central_heating_controller_smart_heating_readiness";
  const attack = "<img src=x onerror=alert(1)>";
  const card = makeCard("learning", {
    [readiness]: { state: "blocked", attributes: { ready: false, control_state: "preview", explanation: attack, checks: [{ label: attack, detail: attack, passed: false }] } },
    "switch.central_heating_controller_smart_heating_control": { state: "off", attributes: {} },
  });
  const html = card.shadowRoot.innerHTML;
  assert.match(html, /<details class="chc-disclosure" data-detail="checks"/);
  assert.doesNotMatch(html, /data-detail="checks" open/);
  assert.ok(html.includes("&lt;img src=x onerror=alert(1)&gt;"));
  assert.doesNotMatch(html, /<img src=x onerror=alert\(1\)>/);
});

test("view subscriptions include only entities used by that view", () => {
  const card = makeCard("overview");
  let renders = 0;
  const render = card._render.bind(card);
  card._render = () => { renders += 1; render(); };
  card.hass.states["sensor.central_heating_controller_learned_heating_rate"] = { state: "3", attributes: {} };
  card.hass = card.hass;
  assert.equal(renders, 0);
  card.hass.states["sensor.central_heating_controller_smart_heating_readiness"] = { state: "learning", attributes: { ready: false } };
  card.hass = card.hass;
  assert.equal(renders, 1);
  card.setConfig({ entity: STATUS, mode: "schedule" });
  renders = 0;
  card.hass.states["sensor.central_heating_controller_predicted_heating_cost_today"] = { state: "2", attributes: {} };
  card.hass = card.hass;
  assert.equal(renders, 0);
});
