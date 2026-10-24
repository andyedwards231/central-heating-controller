import assert from "node:assert/strict";
import test from "node:test";
import "./dom-shim.mjs";
import { createHass } from "./fixtures.mjs";
import "../../www/central-heating-controller-card.js";

const Card = customElements.get("central-heating-controller-card");
const STATUS = "sensor.central_heating_controller_status";
const FORECAST = "sensor.central_heating_controller_indoor_temperature_forecast";
const PLAN = "sensor.central_heating_controller_smart_heating_plan";
const READINESS = "sensor.central_heating_controller_smart_heating_readiness";
const CONTROL = "switch.central_heating_controller_smart_heating_control";
const MODE = "select.central_heating_controller_controller_mode";

function makeCard(mode, states = {}, hass = {}) {
  const card = new Card();
  card.setConfig({ entity: STATUS, mode });
  card.hass = createHass({ states, hass });
  return card;
}

function forecast(points, attributes = {}) {
  return { state: "18", attributes: { confidence: "learned", temperature_unit: "°C", ...attributes, forecast: points } };
}

function point(time, target, demand, temperature = 18) {
  return { datetime: new Date(time).toISOString(), temperature, target_temperature: target, heating_demand_percent: demand };
}

test("next-change summary groups only continuous future demand and finds a target change", () => {
  const now = Date.parse("2026-06-29T17:00:00Z");
  const card = makeCard("overview", {
    [FORECAST]: forecast([
      point(now, 18, 25), point(now + 5 * 60_000, 20, 30), point(now + 10 * 60_000, 20, 4),
      point(now + 15 * 60_000, 20, 25), point(now + 20 * 60_000, 20, 0),
    ]),
  });

  const changes = card._nextChanges(now);

  assert.deepEqual(changes.heating, { start: now, end: now + 10 * 60_000 });
  assert.deepEqual(changes.target, { time: now + 5 * 60_000, text: "Target rises to 20.0 °C" });
  assert.equal(changes.available, true);
});

test("target-null periods can resume, while gaps and stale forecasts are not joined", () => {
  const now = Date.parse("2026-06-29T17:00:00Z");
  const card = makeCard("overview", {
    [FORECAST]: forecast([
      point(now, null, 90), point(now + 5 * 60_000, 18, 90),
      point(now + 20 * 60_000, 18, 90), point(now + 25 * 60_000, 18, 0),
    ]),
  });
  const changes = card._nextChanges(now);
  assert.deepEqual(changes.target, { time: now + 5 * 60_000, text: "Target is set to 18.0 °C" });
  assert.deepEqual(changes.heating, { start: now + 20 * 60_000, end: now + 25 * 60_000 });

  card.hass.states[FORECAST] = forecast([
    point(now - 20 * 60_000, 18, 30), point(now - 15 * 60_000, 18, 30),
  ]);
  card.hass = card.hass;
  assert.deepEqual(card._nextChanges(now), { heating: null, target: null, available: false });
});

test("forecast events use the Home Assistant timezone including daylight saving", () => {
  const card = makeCard("overview", {}, {
    config: { time_zone: "Europe/London" },
    locale: { language: "en-GB", time_format: "24", time_zone: "server" },
  });
  assert.equal(card._eventTime("2026-06-29T17:00:00Z", Date.parse("2026-06-29T16:00:00Z")), "Today 18:00");
});

test("repeated autumn clock-hour event labels distinguish BST from GMT", () => {
  const card = makeCard("overview", {}, {
    config: { time_zone: "Europe/London" },
    locale: { language: "en-GB", time_format: "24", time_zone: "server" },
  });
  const now = Date.parse("2026-10-25T00:00:00Z");
  const before = card._eventTime("2026-10-25T00:30:00Z", now);
  const after = card._eventTime("2026-10-25T01:30:00Z", now);

  assert.match(before, /01:30 BST$/);
  assert.match(after, /01:30 GMT$/);
  assert.notEqual(before, after);
});

test("state wording names what currently owns the heating", () => {
  const cases = [
    { name: "off", mode: "off", status: "off", control: "preview", expected: "Off selected" },
    { name: "away", mode: "away", status: "away", control: "preview", expected: "Away temperature is in use" },
    { name: "manual", mode: "auto", status: "manual_override", control: "paused", expected: "Your manual temperature is in control" },
    { name: "active", mode: "auto", status: "high", control: "active", expected: "Automatic timing is controlling the heating" },
    { name: "fallback", mode: "auto", status: "low", control: "fallback", expected: "Your existing schedule is running" },
  ];
  for (const item of cases) {
    const card = makeCard("overview", {
      [STATUS]: { state: item.status, attributes: item.name === "manual" ? { manual_override: true } : {} },
      [MODE]: { state: item.mode, attributes: {} },
      [READINESS]: { state: item.control === "active" ? "ready" : "blocked", attributes: { control_state: item.control, ready: item.control === "active" } },
      [CONTROL]: { state: item.control === "active" ? "on" : "off", attributes: {} },
    });
    assert.ok(card.shadowRoot.innerHTML.includes(item.expected), `${item.name} should name its owner`);
  }
});

test("missing activity stays unavailable rather than being presented as idle", () => {
  const future = Date.now() + 5 * 60_000;
  const card = makeCard("overview", {
    [STATUS]: { state: "low", attributes: { current_temperature: 18, effective_target_temperature: 18 } },
    [FORECAST]: forecast([point(future, 18, null), point(future + 5 * 60_000, 18, null)]),
  });
  assert.match(card.shadowRoot.innerHTML, /Heating activity unavailable/);
  assert.doesNotMatch(card.shadowRoot.innerHTML, /Heating idle/);
});

test("existing schedule metadata is displayed and escaped without interpreting HA data", () => {
  const badTime = "<img src=x onerror=alert(1)>";
  const card = makeCard("schedule", {
    [PLAN]: { state: "preview", attributes: {
      settings: { enabled: false, weekly_schedule: {} },
      schedule_entity: "schedule.heating",
      existing_schedule: { monday: [{ start: badTime, end: "09:00" }] },
    } },
  });
  const html = card.shadowRoot.innerHTML;
  assert.match(html, /Use my existing schedule/);
  assert.match(html, /schedule\.heating/);
  assert.ok(html.includes("&lt;img src=x onerror=alert(1)&gt;"));
  assert.doesNotMatch(html, /<img src=x onerror=alert\(1\)>/);
});

test("opt-in safety requires readiness, Auto mode and an available switch", async () => {
  const makeLearningCard = ({ ready, mode = "auto", controlState = "off" }) => makeCard("learning", {
    [READINESS]: { state: "ready", attributes: { ready, control_state: "preview" } },
    [MODE]: { state: mode, attributes: {} },
    [CONTROL]: { state: controlState, attributes: {} },
  });

  for (const scenario of [
    { ready: false },
    { ready: true, mode: "away" },
    { ready: true, controlState: "unavailable" },
  ]) {
    const card = makeLearningCard(scenario);
    await card._setSmartHeatingControl(true);
    assert.equal(card.hass.calls.length, 0);
  }

  const card = makeLearningCard({ ready: true });
  await card._setSmartHeatingControl(true);
  assert.deepEqual(card.hass.calls.at(-1), { domain: "switch", service: "turn_on", data: { entity_id: CONTROL } });

  const overview = makeCard("overview", {
    [READINESS]: { state: "ready", attributes: { ready: true } },
    [MODE]: { state: "auto", attributes: {} }, [CONTROL]: { state: "off", attributes: {} },
  });
  assert.doesNotMatch(overview.shadowRoot.innerHTML, /data-action="smart-control:on"/);
});

test("unknown readiness cannot enable even when its stale attributes say ready", async () => {
  const card = makeCard("learning", {
    [READINESS]: { state: "unknown", attributes: { ready: true, control_state: "preview" } },
    [MODE]: { state: "auto", attributes: {} },
    [CONTROL]: { state: "off", attributes: {} },
  });
  assert.match(card.shadowRoot.innerHTML, /data-action="smart-control:on" disabled/);
  await card._setSmartHeatingControl(true);
  assert.equal(card.hass.calls.length, 0);
});

test("missing readiness still allows an available enabled controller to turn off", async () => {
  const card = makeCard("learning", {
    [CONTROL]: { state: "on", attributes: {} },
  });
  assert.match(card.shadowRoot.innerHTML, /data-action="smart-control:off"/);
  await card._setSmartHeatingControl(false);
  assert.deepEqual(card.hass.calls.at(-1), { domain: "switch", service: "turn_off", data: { entity_id: CONTROL } });
});

test("manual override and Heat Blast block new opt-in but still allow turning an enabled switch off", async () => {
  for (const status of ["manual_override", "heat_blast"]) {
    const states = {
      [STATUS]: { state: status, attributes: { manual_override: status === "manual_override" } },
      [READINESS]: { state: "ready", attributes: { ready: true, control_state: "active" } },
      [MODE]: { state: "auto", attributes: {} },
      [CONTROL]: { state: "off", attributes: {} },
    };
    const disabled = makeCard("learning", states);
    assert.match(disabled.shadowRoot.innerHTML, /data-action="smart-control:on" disabled/, `${status} should disable opt-in`);
    await disabled._setSmartHeatingControl(true);
    assert.equal(disabled.hass.calls.length, 0, `${status} must not call turn_on`);

    const enabled = makeCard("learning", { ...states, [CONTROL]: { state: "on", attributes: {} } });
    await enabled._setSmartHeatingControl(false);
    assert.deepEqual(enabled.hass.calls.at(-1), {
      domain: "switch", service: "turn_off", data: { entity_id: CONTROL },
    }, `${status} must not prevent an explicit turn_off`);
  }
});

test("Fahrenheit display uses forecast units and converts Celsius accuracy deltas", () => {
  const card = makeCard("overview", {
    [FORECAST]: forecast([], { temperature_unit: "°F" }),
    [STATUS]: { state: "low", attributes: { current_temperature: 68, effective_target_temperature: 70 } },
    "sensor.central_heating_controller_effective_target_temperature": { state: "70", attributes: { unit_of_measurement: "°F" } },
  });
  assert.match(card.shadowRoot.innerHTML, /68\.0 °F/);
  assert.match(card.shadowRoot.innerHTML, /70 °F/);

  card.setConfig({ entity: STATUS, mode: "learning" });
  card.hass.states[FORECAST] = forecast([], {
    temperature_unit: "°F", forecast_accuracy: { "1h": { mae_c: 1, count: 5 } },
  });
  card.hass = card.hass;
  assert.match(card.shadowRoot.innerHTML, /1\.8 °F average error/);
});

test("a rejected schedule save retains source and draft and reports inline feedback", async () => {
  const card = makeCard("schedule", {
    [PLAN]: { state: "preview", attributes: { settings: {
      enabled: true, weekly_schedule: { monday: [{ start: "06:30", end: "08:00" }] },
    } } },
  });
  card._setScheduleSource("custom");
  card._updateScheduleDraft("weekly_schedule.monday.0.start", "06:45");
  card.hass.callService = async () => { throw new Error("service rejected"); };

  await card._saveSchedule();

  assert.equal(card._scheduleSource, "custom");
  assert.equal(card._scheduleDraft.weekly_schedule.monday[0].start, "06:45");
  assert.equal(card._scheduleDraftDirty, true);
  assert.match(card.shadowRoot.innerHTML, /Could not save the comfort times\. Please try again\./);
  assert.match(card.shadowRoot.innerHTML, /value="custom" data-schedule-source checked/);
  assert.match(card.shadowRoot.innerHTML, /data-draft-path="weekly_schedule\.monday\.0\.start" value="06:45"/);
});

test("concurrent schedule save taps issue only one service call", async () => {
  const card = makeCard("schedule", {
    [PLAN]: { state: "preview", attributes: { settings: {
      enabled: true, weekly_schedule: { monday: [{ start: "06:30", end: "08:00" }] },
    } } },
  });
  let resolveService;
  card.hass.callService = (domain, service, data) => {
    card.hass.calls.push({ domain, service, data });
    return new Promise(resolve => { resolveService = resolve; });
  };

  const firstSave = card._saveSchedule();
  assert.equal(card._scheduleSaving, true);
  await card._saveSchedule();
  assert.equal(card.hass.calls.length, 1);
  resolveService();
  await firstSave;
  assert.equal(card._scheduleSaving, false);
});

test("edits made during a successful save stay dirty after HA publishes the saved settings", async () => {
  const initialSettings = {
    enabled: true, weekly_schedule: { monday: [{ start: "06:30", end: "08:00" }] },
  };
  const card = makeCard("schedule", {
    [PLAN]: { state: "preview", attributes: { settings: initialSettings } },
  });
  card._setScheduleSource("custom");
  let resolveService;
  let submitted;
  card.hass.callService = (domain, service, data) => {
    card.hass.calls.push({ domain, service, data });
    submitted = data;
    return new Promise(resolve => { resolveService = resolve; });
  };

  const saving = card._saveSchedule();
  assert.equal(submitted.weekly_schedule.monday[0].start, "06:30");
  card._updateScheduleDraft("weekly_schedule.monday.0.start", "06:45");
  card._setScheduleSource("existing");

  // Home Assistant reflects the service call while the original request is pending.
  card.hass.states[PLAN] = { state: "preview", attributes: { settings: submitted } };
  card.hass = card.hass;
  assert.equal(card._scheduleDraft.weekly_schedule.monday[0].start, "06:45");
  assert.equal(card._scheduleSource, "existing");

  resolveService();
  await saving;

  assert.equal(submitted.weekly_schedule.monday[0].start, "06:30", "the sent payload must be a snapshot, not a live draft reference");
  assert.equal(card._scheduleDraft.weekly_schedule.monday[0].start, "06:45");
  assert.equal(card._scheduleSource, "existing");
  assert.equal(card._scheduleDraftDirty, true);
  assert.match(card.shadowRoot.innerHTML, /Earlier changes saved\. Save again to keep your latest edits\./);
});

test("overview uses the controlling forecast while schedule keeps proposed plan collapsed", () => {
  const card = makeCard("overview", {
    [FORECAST]: forecast([point(Date.now(), 18, 0), point(Date.now() + 5 * 60_000, 18.5, 0)]),
    [PLAN]: { state: "preview", attributes: { preview: true, forecast: [point(Date.now(), 10, 0), point(Date.now() + 5 * 60_000, 10, 0)] } },
  });
  assert.match(card.shadowRoot.innerHTML, /Indoor temperature forecast|Expected temperature/);
  assert.doesNotMatch(card.shadowRoot.innerHTML, /Proposed heating plan/);

  card.setConfig({ entity: STATUS, mode: "schedule" });
  card.hass = card.hass;
  assert.match(card.shadowRoot.innerHTML, /See the automatic timing preview/);
  assert.doesNotMatch(card.shadowRoot.innerHTML, /data-detail="preview" open/);
});
