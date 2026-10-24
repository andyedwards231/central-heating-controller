const CARD_NAME = "central-heating-controller-card";
const EMPTY_SETTINGS = Object.freeze({});

const ENTITY_SUFFIXES = {
  autoMode: ["switch", "auto_mode", "auto_mode"],
  controllerMode: ["select", "controller_mode", "controller_mode"],
  heatBlast: ["button", "heat_blast", "heat_blast"],
  highTemperature: ["number", "high_temperature", "high_temperature"],
  lowTemperature: ["number", "low_temperature", "low_temperature"],
  ecoTemperature: ["number", "eco_temperature", "eco_temperature"],
  fallbackWarmup: ["number", "fallback_warmup_minutes", "fallback_warmup_minutes"],
  maximumWarmup: ["number", "maximum_warmup_minutes", "maximum_warmup_minutes"],
  effectiveTarget: ["sensor", "effective_target_temperature", "effective_target_temperature"],
  learnedRate: ["sensor", "learned_heating_rate", "learned_heating_rate"],
  preheatStart: ["sensor", "preheat_start_time", "preheat_start_time"],
  indoorTemperatureForecast: ["sensor", "indoor_temperature_forecast", "indoor_temperature_forecast"],
  smartHeatingPlan: ["sensor", "smart_heating_plan", "smart_heating_plan"],
  smartHeatingReadiness: ["sensor", "smart_heating_readiness", "smart_heating_readiness"],
  smartHeatingControl: ["switch", "smart_heating_control", "smart_heating_control"],
  heatingCostToday: ["sensor", "predicted_heating_cost_today", "predicted_heating_cost_today"],
  heatingCostTomorrow: ["sensor", "predicted_heating_cost_tomorrow", "predicted_heating_cost_tomorrow"],
};

// Existing entity registries retain names created by releases before 1.1.0.
const LEGACY_SUFFIXES = {
  fallbackWarmup: "fallback_warm_up_duration",
  maximumWarmup: "maximum_warm_up_duration",
  preheatStart: "pre_heat_start_time",
};

const STATUS_LABELS = {
  high: "High",
  low: "Low",
  pre_heating: "Pre-heating",
  away: "Away",
  off: "Off",
  heat_blast: "Heat blast",
  manual_override: "Manual override",
  unavailable: "Unavailable",
};

class CentralHeatingControllerCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._config = null;
    this._entities = {};
    this._hass = null;
    this._scheduleDraft = null;
    this._scheduleDraftDirty = false;
    this._scheduleSettingsSource = null;
    this._scheduleEditorOpen = null;
    this._scheduleMessage = "";
    this._smartControlMessage = "";
    this._smartControlPending = false;
    this._numberControlPending = new Set();
    this._numberControlMessage = null;
    this._scheduleSource = null;
    this._scheduleSaving = false;
    this._openDetails = new Set();
    this._scheduleRevision = 0;
  }

  setConfig(config) {
    if (!config || !config.entity) {
      throw new Error("entity is required");
    }

    this._config = {
      title: "Heating",
      mode: "control",
      ...config,
    };
    this._entities = this._deriveEntities(this._config.entity, this._config.entities || {});
    this._requestRender(true);
  }

  set hass(hass) {
    this._hass = hass;
    if (this._config) {
      this._entities = this._deriveEntities(this._config.entity, this._config.entities || {});
    }
    this._requestRender();
  }

  get hass() {
    return this._hass;
  }

  _renderContext() {
    return [
      Boolean(this._hass),
      this._hass?.language,
      JSON.stringify(this._hass?.locale),
      this._hass?.config?.time_zone,
      JSON.stringify(this._hass?.selectedTheme),
      this._hass?.themes?.darkMode,
    ];
  }

  _renderEntityIds() {
    const mode = this._config?.mode;
    if (mode === "overview") return ["status", "effectiveTarget", "controllerMode", "heatBlast", "preheatStart", "indoorTemperatureForecast", "heatingCostToday", "heatingCostTomorrow", "smartHeatingPlan", "smartHeatingReadiness", "smartHeatingControl"].map(key => this._entities[key]);
    if (mode === "schedule") return ["status", "controllerMode", "smartHeatingPlan", "indoorTemperatureForecast", "highTemperature", "smartHeatingReadiness", "smartHeatingControl"].map(key => this._entities[key]);
    if (mode === "learning") return ["status", "controllerMode", "indoorTemperatureForecast", "learnedRate", "heatingCostToday", "smartHeatingReadiness", "smartHeatingControl"].map(key => this._entities[key]);
    const existingEntities = Object.entries(this._entities)
      .filter(([key]) => !["smartHeatingReadiness", "smartHeatingControl"].includes(key))
      .map(([, entityId]) => entityId);
    return mode === "control" ? [...existingEntities, this._entities.smartHeatingReadiness] : existingEntities;
  }

  _renderFormatterInputs() {
    return [this._hass?.formatEntityState, this._hass?.entities];
  }

  _requestRender(force = false) {
    if (!this._config) return;
    // HA replaces changed State objects. Keep a snapshot rather than the old
    // hass object: callers can reuse that object with a new states mapping.
    const inputs = [
      ...this._renderContext(),
      ...this._renderFormatterInputs(),
      ...this._renderEntityIds().flatMap(id => [id, this._state(id)]),
    ];
    if (!force && this._renderInputs?.length === inputs.length
        && inputs.every((value, index) => value === this._renderInputs[index])) return;
    this._render();
    this._renderInputs = inputs;
  }

  _dateFormatters() {
    const locale = this._hass?.locale;
    const language = locale?.language || this._hass?.language || undefined;
    const timeFormat = locale?.time_format;
    const timeZone = locale?.time_zone === "server" ? this._hass?.config?.time_zone : undefined;
    const key = JSON.stringify([language, timeFormat, timeZone]);
    if (this._cachedDateFormatters?.key !== key) {
      const options = { hour: "2-digit", minute: "2-digit", timeZone };
      if (timeFormat === "12") options.hourCycle = "h12";
      if (timeFormat === "24") options.hourCycle = "h23";
      this._cachedDateFormatters = {
        key,
        time: new Intl.DateTimeFormat(timeFormat === "system" ? undefined : language, options),
        language: timeFormat === "system" ? undefined : language,
        timeOptions: options,
        day: new Intl.DateTimeFormat(language, { weekday: "short", timeZone }),
      };
    }
    return this._cachedDateFormatters;
  }

  getCardSize() {
    return 4;
  }

  _deriveEntities(statusEntity, overrides) {
    const [domain, objectId] = statusEntity.split(".");
    const base = domain === "sensor" && objectId?.endsWith("_status")
      ? objectId.slice(0, -"_status".length)
      : objectId;
    const entities = { status: statusEntity };

    for (const [property, [entityDomain, suffix, overrideKey]] of Object.entries(ENTITY_SUFFIXES)) {
      const standardId = `${entityDomain}.${base}_${suffix}`;
      const legacyId = LEGACY_SUFFIXES[property]
        ? `${entityDomain}.${base}_${LEGACY_SUFFIXES[property]}` : null;
      entities[property] = overrides[overrideKey]
        || (!this._state(standardId) && legacyId && this._state(legacyId) ? legacyId : standardId);
    }

    return entities;
  }

  _state(entityId) {
    return this._hass?.states?.[entityId] || null;
  }

  _statusState() {
    return this._state(this._config?.entity);
  }

  _statusLabel(status) {
    return STATUS_LABELS[status] || this._titleCase(status || "unknown");
  }

  _titleCase(value) {
    return String(value)
      .replace(/_/g, " ")
      .replace(/\b\w/g, (letter) => letter.toUpperCase());
  }

  _formatEntity(entityId, fallback = "Unavailable") {
    const stateObj = this._state(entityId);
    if (!stateObj || stateObj.state === "unavailable" || stateObj.state === "unknown") {
      return fallback;
    }
    if (this._hass?.formatEntityState) {
      return this._hass.formatEntityState(stateObj);
    }
    const unit = stateObj.attributes?.unit_of_measurement;
    return unit ? `${stateObj.state} ${unit}` : stateObj.state;
  }

  _formatNumber(value, unit = "") {
    if (value === undefined || value === null || value === "") {
      return "Unavailable";
    }
    const numberValue = Number(value);
    const formatted = Number.isFinite(numberValue) ? numberValue.toFixed(1) : String(value);
    return unit ? `${formatted} ${unit}` : formatted;
  }

  async _toggleAutoMode() {
    const entityId = this._entities.autoMode;
    const auto = this._state(entityId);
    const service = auto?.state === "on" ? "turn_off" : "turn_on";
    await this._callService("switch", service, { entity_id: entityId });
  }

  async _selectControllerMode(mode) {
    if (!["auto", "away", "off"].includes(mode)) return;
    await this._quickAction("select", "select_option", {
      entity_id: this._entities.controllerMode,
      option: mode,
    });
  }

  async _quickAction(domain, service, data) {
    if (this._quickPending) return;
    this._quickPending = true;
    this._quickMessage = "";
    this._requestRender(true);
    try {
      await this._callService(domain, service, data);
    } catch (_error) {
      this._quickMessage = "Could not change the heating. Please try again.";
    } finally {
      this._quickPending = false;
      this._requestRender(true);
    }
  }

  _readiness() {
    const sensor = this._state(this._entities.smartHeatingReadiness);
    const control = this._state(this._entities.smartHeatingControl);
    const attrs = sensor?.attributes || {};
    const state = sensor?.state;
    const unavailable = !sensor || ["unknown", "unavailable"].includes(state);
    const mode = this._state(this._entities.controllerMode)?.state;
    const status = this._statusState();
    const overridden = status?.state === "heat_blast" || status?.state === "manual_override" || status?.attributes?.manual_override === true;
    const pausedReason = mode === "off" ? "Heating mode is Off, so intelligent heating is paused."
      : mode === "away" ? "Heating mode is Away, so intelligent heating is paused."
        : overridden ? "A manual override is in control, so intelligent heating is paused." : "";
    const reportedControlState = attrs.control_state;
    const controlState = pausedReason ? "paused"
      : ["preview", "active", "fallback", "paused"].includes(reportedControlState) ? reportedControlState
        : unavailable ? "unknown" : control?.state === "on" ? "unknown" : "preview";
    const explanation = pausedReason || (controlState === "unknown" ? "The integration has not reported whether intelligent heating is active." : "");
    return { sensor, control, attrs, state, mode, controlState, unavailable, explanation };
  }

  async _setSmartHeatingControl(enabled) {
    const { attrs, control, mode, unavailable, controlState, state } = this._readiness();
    if (this._smartControlPending) return;
    if (enabled && (unavailable || state !== "ready" || attrs.ready !== true || mode !== "auto" || controlState === "paused" || !control || ["unknown", "unavailable"].includes(control.state))) return;
    if (!enabled && (!control || ["unknown", "unavailable"].includes(control.state))) return;
    this._smartControlPending = true;
    this._requestRender(true);
    try {
      await this._callService("switch", enabled ? "turn_on" : "turn_off", { entity_id: this._entities.smartHeatingControl });
      this._smartControlMessage = enabled ? "Automatic timing enabled."
        : this._readiness().controlState === "paused" ? `Automatic timing disabled. ${this._readiness().explanation}`
          : "Returned to the existing schedule.";
    } catch (_error) {
      this._smartControlMessage = enabled
        ? "Could not enable automatic timing. Check Learning and try again."
        : "Could not disable automatic timing. Please try again.";
    } finally {
      this._smartControlPending = false;
      this._requestRender(true);
    }
  }

  _renderReadiness(checklistMode = "none") {
    const { attrs, state, controlState, unavailable, explanation, mode } = this._readiness();
    if (unavailable) return `<section class="chc-readiness is-unavailable" aria-label="Intelligent heating readiness"><strong>Intelligent heating unavailable</strong><p>The installed integration does not provide readiness information yet. Update the integration and restart Home Assistant to enable this option.</p>${explanation ? `<p>${this._escape(explanation)}</p>` : ""}</section>`;
    const heading = controlState === "active" ? "Intelligent heating active"
      : controlState === "fallback" ? "Using existing schedule"
        : controlState === "paused" ? "Intelligent heating paused"
          : controlState === "unknown" ? "Intelligent heating status unavailable"
          : attrs.ready === true ? "Ready to enable" : state === "learning" ? "Still learning" : "Not ready to enable";
    const summaryExplanation = explanation || attrs.fallback_reason || attrs.explanation || (attrs.ready === true && mode !== "auto"
      ? "Set heating mode to Auto before enabling intelligent heating."
      : controlState === "preview" ? "Preview — the existing schedule controls heating."
        : controlState === "paused" ? "Intelligent heating is paused." : "");
    const checks = Array.isArray(attrs.checks) ? attrs.checks : [];
    const remaining = checks.filter(check => check.passed !== true).length;
    const checklist = checklistMode !== "none" ? `<details class="chc-readiness-checks"${checklistMode === "open" ? " open" : ""}><summary>Readiness checks (${remaining} remaining)</summary>${checks.length
      ? `<ul>${checks.map(check => `<li class="${check.passed === true ? "is-passed" : "is-pending"}"><span aria-hidden="true">${check.passed === true ? "✓" : "○"}</span><span><strong>${this._escape(check.label || check.key || "Readiness check")}</strong>${check.detail ? `<small>${this._escape(check.detail)}</small>` : ""}</span></li>`).join("")}</ul>`
      : `<p>${state === "learning" ? "Requirements are still being checked." : "No readiness checks are available."}</p>`}</details>` : "";
    return `<section class="chc-readiness ${controlState === "active" ? "is-active" : controlState === "fallback" ? "is-fallback" : controlState === "paused" ? "is-paused" : ""}" aria-label="Intelligent heating readiness"><div class="chc-readiness-heading"><strong>${heading}</strong>${controlState === "unknown" ? `<span>Status unavailable</span>` : state === "ready" ? `<span>${remaining ? `${remaining} checks remaining` : "All checks passed"}</span>` : state === "learning" ? `<span>In progress</span>` : `<span>${this._escape(state || "Unavailable")}</span>`}</div>${summaryExplanation ? `<p>${this._escape(controlState === "fallback" && attrs.fallback_reason ? `Reason: ${attrs.fallback_reason}` : summaryExplanation)}</p>` : ""}${checklist}</section>`;
  }

  _initialScheduleDraft(settings = {}) {
    const days = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"];
    const weekly = settings.weekly_schedule && typeof settings.weekly_schedule === "object"
      ? settings.weekly_schedule : {};
    return {
      enabled: settings.enabled === true,
      weekly_schedule: Object.fromEntries(days.map(day => [day,
        Array.isArray(weekly[day]) ? weekly[day].map(period => ({
          start: String(period?.start || ""), end: String(period?.end || ""),
        })) : [],
      ])),
      night_start: String(settings.night_start || "23:00"),
      night_end: String(settings.night_end || "07:00"),
      night_temperature: settings.night_temperature ?? 16,
      daytime_drop: settings.daytime_drop ?? 2,
    };
  }

  _renderScheduleEditor(settings = EMPTY_SETTINGS, controlState = "preview") {
    if (this._scheduleEditorOpen === null) this._scheduleEditorOpen = this._config?.mode === "schedule";
    settings ||= EMPTY_SETTINGS;
    if (!this._scheduleDraft || (!this._scheduleDraftDirty && settings !== this._scheduleSettingsSource)) {
      this._scheduleDraft = this._initialScheduleDraft(settings);
      this._scheduleSettingsSource = settings;
      this._scheduleSource = Object.values(this._scheduleDraft.weekly_schedule).some(periods => periods.length) ? "custom" : "existing";
    }
    const draft = this._scheduleDraft;
    const plan = this._state(this._entities.smartHeatingPlan)?.attributes || {};
    const status = this._statusState()?.attributes || {};
    const scheduleEntity = plan.schedule_entity || status.schedule_entity;
    const dayNames = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"];
    const periodText = periods => Array.isArray(periods) && periods.length
      ? periods.map(period => `${period.start}–${period.end}`).join(", ") : "No comfort period";
    const existing = plan.existing_schedule;
    const inherited = existing && typeof existing === "object"
      ? `<div class="chc-inherited-week" aria-label="Existing comfort times">${dayNames.map(day => `<div><strong>${this._titleCase(day)}</strong><span>${this._escape(periodText(existing[day]))}</span></div>`).join("")}</div>`
      : '<p class="chc-schedule-hint">The timetable is not available to display yet. Your existing schedule still works.</p>';
    const rows = dayNames.map(day => {
      const periods = draft.weekly_schedule[day] || [];
      return `<details class="chc-schedule-day" data-detail="day-${day}" ${this._openDetails.has(`day-${day}`) ? "open" : ""}><summary><strong>${this._titleCase(day)}</strong><span>${this._escape(periodText(periods))}</span><span class="chc-day-edit">Change</span></summary><div class="chc-day-fields">
        ${periods.map((period, index) => `<div class="chc-period"><label>Warm from<input type="time" aria-label="${this._titleCase(day)} warm from ${index + 1}" data-draft-path="weekly_schedule.${day}.${index}.start" value="${this._escape(period.start)}"></label><label>Until<input type="time" aria-label="${this._titleCase(day)} until ${index + 1}" data-draft-path="weekly_schedule.${day}.${index}.end" value="${this._escape(period.end)}"></label><button type="button" class="chc-remove" data-action="period-remove:${day}:${index}" aria-label="Remove ${this._titleCase(day)} period ${index + 1}">Remove</button></div>`).join("")}
        <button type="button" class="chc-add" data-action="period-add:${day}" aria-label="Add ${this._titleCase(day)} comfort period">Add a period</button></div></details>`;
    }).join("");
    const nights = `<div class="chc-night-fields"><div class="chc-period"><label>Night starts<input type="time" data-draft-path="night_start" value="${this._escape(draft.night_start)}"></label><label>Night ends<input type="time" data-draft-path="night_end" value="${this._escape(draft.night_end)}"></label></div><div class="chc-period"><label>Night temperature<input type="number" step="0.5" data-draft-path="night_temperature" value="${this._escape(draft.night_temperature)}"></label><label>Maximum daytime drop<input type="number" step="0.5" min="0" data-draft-path="daytime_drop" value="${this._escape(draft.daytime_drop)}"></label></div><p>Between comfort periods, the house may cool by up to this amount. Automatic timing can choose a smaller drop when that uses less heating overall.</p></div>`;
    return `<details class="chc-schedule-editor" ${this._scheduleEditorOpen ? "open" : ""}><summary>When should the house be warm?</summary><form data-schedule-form>
      <fieldset class="chc-source-choice"><legend>Choose your timetable</legend><label><input type="radio" name="schedule-source" value="existing" data-schedule-source ${this._scheduleSource === "existing" ? "checked" : ""}> Use my existing schedule</label><label><input type="radio" name="schedule-source" value="custom" data-schedule-source ${this._scheduleSource === "custom" ? "checked" : ""}> Set comfort times here</label></fieldset>
      ${this._scheduleSource === "existing" ? `<div class="chc-timetable"><p><strong>No need to enter the times again.</strong> The On periods in your Home Assistant schedule are the times you want warmth.</p>${inherited}${scheduleEntity ? `<button type="button" data-more-info="${this._escape(scheduleEntity)}">Open existing schedule</button>` : ""}</div>` : `<div class="chc-timetable"><p>Set the times you want to be warm, such as 07:00–09:00. Automatic timing will work out when to start heating. Days without a period stay outside comfort hours.</p><div class="chc-week-editor">${rows}</div></div>`}
      ${this._detail("night", `Night and daytime allowance <span class="chc-summary-value">${this._escape(draft.night_temperature)}° at night · up to ${this._escape(draft.daytime_drop)}° lower by day</span>`, nights)}
      <div class="chc-schedule-footer"><button type="submit" ${this._scheduleSaving ? "disabled" : ""}>${this._scheduleSaving ? "Saving…" : "Save comfort times"}</button><span role="status">${this._escape(this._scheduleMessage)}</span></div>
      ${controlState !== "active" ? '<p class="chc-schedule-hint">Saved times are for automatic timing. Your existing schedule stays in control until it is enabled.</p>' : ""}
    </form></details>`;
  }

  _setScheduleSource(source) {
    if (!["existing", "custom"].includes(source)) return;
    this._scheduleSource = source;
    this._scheduleDraftDirty = true;
    this._scheduleRevision += 1;
    this._scheduleMessage = "";
    this._requestRender(true);
    this.shadowRoot.querySelector?.('[data-schedule-source]:checked')?.focus();
  }

  _showMoreInfo(entityId) {
    if (!/^(climate|schedule)\.[a-z0-9_]+$/.test(entityId || "")) return;
    this.dispatchEvent(new CustomEvent("hass-more-info", {detail: {entityId}, bubbles: true, composed: true}));
  }

  async _setPlanningEnabled(enabled) {
    const settings = this._state(this._entities.smartHeatingPlan)?.attributes?.settings;
    if (!settings || this._planningPending) return;
    this._planningPending = true;
    try {
      await this._callService("central_heating_controller", "set_smart_schedule", {
        entity_id: this._config.entity, ...this._initialScheduleDraft(settings), enabled,
      });
      this._planningMessage = enabled ? "Planning enabled." : "Planning disabled. Your existing schedule is in control.";
    } catch (_error) {
      this._planningMessage = "Could not change planning. Please try again.";
    } finally {
      this._planningPending = false;
      this._requestRender(true);
    }
  }

  _updateScheduleDraft(path, value) {
    if (!this._scheduleDraft || !path) return;
    const parts = path.split(".");
    let target = this._scheduleDraft;
    for (const part of parts.slice(0, -1)) target = target?.[part];
    const key = parts.at(-1);
    if (!target || !(key in target)) return;
    target[key] = typeof value === "boolean" ? value : value;
    this._scheduleDraftDirty = true;
    this._scheduleRevision += 1;
  }

  async _saveSchedule() {
    const draft = this._scheduleDraft;
    if (!draft || this._scheduleSaving) return;
    if (this._scheduleSource === "custom" && !Object.values(draft.weekly_schedule).some(periods => periods.length)) {
      this._scheduleMessage = "Add a comfort period, or choose your existing schedule.";
      this._requestRender(true);
      return;
    }
    const toMinutes = value => {
      const match = /^(\d{2}):(\d{2})$/.exec(String(value));
      return match && Number(match[1]) < 24 && Number(match[2]) < 60;
    };
    if (!toMinutes(draft.night_start) || !toMinutes(draft.night_end)
        || !Number.isFinite(Number(draft.night_temperature)) || !Number.isFinite(Number(draft.daytime_drop))
        || Number(draft.daytime_drop) < 0
        || this._scheduleSource !== "existing" && Object.values(draft.weekly_schedule).some(periods => periods.some(period =>
          !toMinutes(period.start) || !toMinutes(period.end)))) {
      this._scheduleMessage = "Check the times and temperature values before saving.";
      this._render();
      return;
    }
    const data = {
      entity_id: this._config.entity,
      enabled: this._state(this._entities.smartHeatingPlan)?.attributes?.settings?.enabled ?? draft.enabled,
      weekly_schedule: this._scheduleSource === "existing" ? {} : Object.fromEntries(Object.entries(draft.weekly_schedule)
        .map(([day, periods]) => [day, periods.map(period => ({...period}))])),
      night_start: draft.night_start,
      night_end: draft.night_end,
      night_temperature: Number(draft.night_temperature),
      daytime_drop: Number(draft.daytime_drop),
    };
    const revision = this._scheduleRevision;
    this._scheduleSaving = true;
    this._requestRender(true);
    try {
      await this._callService("central_heating_controller", "set_smart_schedule", data);
      this._scheduleMessage = revision !== this._scheduleRevision ? "Earlier changes saved. Save again to keep your latest edits."
        : this._readiness().controlState === "active" ? "Comfort times saved." : "Saved for automatic timing. Your existing schedule is still running.";
      this._scheduleDraftDirty = revision !== this._scheduleRevision;
    } catch (_error) {
      this._scheduleMessage = "Could not save the comfort times. Please try again.";
    } finally {
      this._scheduleSaving = false;
    }
    this._requestRender(true);
  }

  _setScheduleEditorOpen(open) {
    this._scheduleEditorOpen = Boolean(open);
  }

  _changeSchedulePeriods(action) {
    if (!this._scheduleDraft) return;
    const [, operation, day, indexText] = /^(period-(?:add|remove)):([^:]+)(?::(\d+))?$/.exec(action) || [];
    if (!operation || !this._scheduleDraft.weekly_schedule[day]) return;
    const periods = this._scheduleDraft.weekly_schedule[day];
    if (operation === "period-add") periods.push({ start: "07:00", end: "09:00" });
    else periods.splice(Number(indexText), 1);
    this._scheduleSource = "custom";
    this._openDetails.add(`day-${day}`);
    this._scheduleDraftDirty = true;
    this._scheduleRevision += 1;
    this._render();
  }

  async _pressHeatBlast() {
    await this._quickAction("button", "press", { entity_id: this._entities.heatBlast });
  }

  async _adjustNumber(entityId, direction) {
    if (this._numberControlPending.has(entityId)) return;
    const stateObj = this._state(entityId);
    if (!stateObj || stateObj.state === "unavailable" || stateObj.state === "unknown") {
      return;
    }

    const current = Number(stateObj.state);
    if (!Number.isFinite(current)) {
      return;
    }

    const attrs = stateObj.attributes || {};
    const step = Number(attrs.step) || 1;
    const min = Number(attrs.min);
    const max = Number(attrs.max);
    let value = current + step * direction;
    if (Number.isFinite(min)) {
      value = Math.max(min, value);
    }
    if (Number.isFinite(max)) {
      value = Math.min(max, value);
    }
    value = Number(value.toFixed(this._decimalPlaces(step)));

    this._numberControlPending.add(entityId);
    this._numberControlMessage = null;
    this._requestRender(true);
    try {
      await this._callService("number", "set_value", { entity_id: entityId, value });
    } catch (_error) {
      this._numberControlMessage = {
        entityId,
        text: "Could not save this setting. Please try again.",
      };
    } finally {
      this._numberControlPending.delete(entityId);
      this._requestRender(true);
    }
  }

  async _callService(domain, service, data) {
    if (!this._hass?.callService) {
      return;
    }
    await this._hass.callService(domain, service, data);
  }

  _decimalPlaces(value) {
    const text = String(value);
    return text.includes(".") ? text.split(".")[1].length : 0;
  }

  _render() {
    if (!this.shadowRoot || !this._config) {
      return;
    }
    if (this._hass && !this._statusState()) {
      this._renderStatusEntityError();
      return;
    }

    if (this._config.mode === "visual") {
      this._renderVisual();
      return;
    }
    if (this._config.mode === "settings") {
      this._renderSettings();
      return;
    }

    if (["overview", "schedule", "learning"].includes(this._config.mode)) {
      this._renderDashboardMode(this._config.mode);
      return;
    }

    this._renderControl();
  }

  _dashboardLink(view, label) {
    const root = this._config.dashboard_path || "/dashboard-heating";
    const path = typeof root === "string" && /^\/[a-zA-Z0-9_/-]+$/.test(root) ? root.replace(/\/$/, "") : "/dashboard-heating";
    return `<a class="chc-text-link" href="${path}/${view}">${this._escape(label)}</a>`;
  }

  _policySummary() {
    const status = this._statusState();
    const mode = this._state(this._entities.controllerMode)?.state;
    const control = this._readiness();
    if (["unknown", "unavailable"].includes(status?.state)) return "Waiting for heating readings. Check Learning for details.";
    if (mode === "off" || status?.state === "off") return "Off selected. The heating will stay off until you change mode.";
    if (mode === "away" || status?.state === "away") return "Away temperature is in use.";
    if (status?.state === "heat_blast") return "A one-hour boost is in control.";
    if (status?.state === "manual_override" || status?.attributes?.manual_override) return "Your manual temperature is in control.";
    if (control.controlState === "active") return "Automatic timing is controlling the heating.";
    if (control.controlState === "fallback") return "Your existing schedule is running while automatic timing is unavailable.";
    if (control.controlState === "unknown") return "Heating control status is unavailable. Check Learning for details.";
    return "Your existing schedule is controlling the heating.";
  }

  _activityLabel() {
    const status = this._statusState();
    if (this._state(this._entities.controllerMode)?.state === "off" || status?.state === "off") return "Off selected";
    if (["unknown", "unavailable"].includes(status?.state)) return "Heating activity unavailable";
    const demand = this._finiteNumber(status?.attributes?.current_heating_demand_percent
      ?? this._state(this._entities.indoorTemperatureForecast)?.attributes?.current_heating_demand_percent);
    if (demand !== null && demand >= 0 && demand <= 100) return demand > 0 ? "Heating requested" : "Heating idle";
    return "Heating activity unavailable";
  }

  _forecastRows(entityId = this._entities.indoorTemperatureForecast) {
    const state = this._state(entityId);
    this._forecastRowsCache ||= new Map();
    const cached = this._forecastRowsCache.get(entityId);
    if (cached?.state === state) return cached.rows;
    const rows = Array.isArray(state?.attributes?.forecast) ? state.attributes.forecast
      .filter(point => point && typeof point === "object")
      .map(point => ({ time: new Date(point.datetime).getTime(), temperature: this._finiteNumber(point.temperature),
        target: this._finiteNumber(point.target_temperature), demand: this._finiteNumber(point.heating_demand_percent) }))
      .filter(point => Number.isFinite(point.time) && point.temperature !== null)
      .sort((a, b) => a.time - b.time) : [];
    this._forecastRowsCache.set(entityId, {state, rows});
    return rows;
  }

  _eventTime(value, now = Date.now()) {
    const date = new Date(value);
    const formats = this._dateFormatters();
    const day = formats.day.format(date);
    const today = formats.day.format(new Date(now));
    return `${day === today ? "Today" : day} ${this._timeLabel(date)}`;
  }

  _timeLabel(value) {
    const date = new Date(value);
    const formats = this._dateFormatters();
    const time = formats.time.format(date), day = formats.day.format(date);
    // During the autumn clock change, two different instants share one clock
    // label. Include the zone only for that repeated hour.
    const repeated = [-3600000, 3600000].some(offset => {
      const other = new Date(date.getTime() + offset);
      return formats.day.format(other) === day && formats.time.format(other) === time;
    });
    if (!repeated) return time;
    formats.event ||= new Intl.DateTimeFormat(formats.language, {...formats.timeOptions, timeZoneName: "short"});
    return formats.event.format(date);
  }

  _nextChanges(now = Date.now(), entityId = this._entities.indoorTemperatureForecast) {
    const rows = this._forecastRows(entityId);
    const intervals = [];
    let targetChange = null;
    for (let i = 0; i < rows.length - 1; i++) {
      const point = rows[i], next = rows[i + 1];
      const continuous = next.time > point.time && next.time - point.time <= 10 * 60 * 1000;
      if (continuous && !targetChange && next.time > now && next.target !== null
          && (point.target === null || Math.abs(next.target - point.target) >= 0.05)) {
        const direction = point.target === null ? "is set" : next.target > point.target ? "rises" : "falls";
        targetChange = { time: next.time, text: `Target ${direction} to ${this._formatTemperature(next.target)}` };
      }
      if (!continuous || next.time <= now || point.target === null || point.demand === null
          || point.demand <= 5 || point.demand > 100) continue;
      const previous = intervals.at(-1);
      if (previous?.end === point.time) previous.end = next.time;
      else intervals.push({start: Math.max(now, point.time), end: next.time});
    }
    return {heating: intervals[0] || null, target: targetChange, available: rows.length > 1 && rows.at(-1).time > now};
  }

  _renderNextChanges() {
    const mode = this._state(this._entities.controllerMode)?.state;
    const changes = this._nextChanges();
    const entries = [];
    if (mode === "off") entries.push(`<li><strong>Heating stays off</strong><span>Change mode to resume your schedule.</span></li>`);
    else {
      if (changes.heating) {
        const heat = changes.heating;
        entries.push(`<li><strong>${heat.start <= Date.now() ? "Heating likely until" : "Heating likely around"} ${this._escape(this._eventTime(heat.start <= Date.now() ? heat.end : heat.start))}</strong>${heat.start > Date.now() ? `<span>Until about ${this._escape(this._eventTime(heat.end))}</span>` : ""}</li>`);
      }
      if (changes.target) entries.push(`<li><strong>${this._escape(changes.target.text)}</strong><span>Around ${this._escape(this._eventTime(changes.target.time))}</span></li>`);
      if (!entries.length) entries.push(`<li><strong>${changes.available ? "No further target changes in the next 24 hours" : "Waiting for the temperature forecast"}</strong><span>${changes.available ? "Heating may still cycle to maintain the target." : "Your current heating schedule continues."}</span></li>`);
    }
    return `<section class="chc-next" aria-label="What happens next"><h3>What happens next</h3><ul>${entries.join("")}</ul></section>`;
  }

  _renderControlChoice() {
    const modeEntity = this._state(this._entities.controllerMode);
    const mode = modeEntity?.state;
    const unavailable = !modeEntity || !["auto", "away", "off"].includes(mode);
    return `<div class="chc-quick-controls"><label>Heating mode<select data-mode aria-label="Heating mode" ${unavailable || this._quickPending ? "disabled" : ""}>${unavailable ? "<option selected>Unavailable</option>" : ["auto", "away", "off"].map(value => `<option value="${value}" ${mode === value ? "selected" : ""}>${this._titleCase(value)}</option>`).join("")}</select></label><button type="button" data-action="blast" ${this._state(this._entities.heatBlast) && mode === "auto" && !this._quickPending ? "" : "disabled"}>Boost for 1 hour</button><span class="chc-quick-message" role="status">${this._escape(this._quickMessage || "")}</span></div>`;
  }

  _renderSmartAction() {
    const ready = this._readiness();
    const on = ready.control?.state === "on";
    const available = ready.control && !["unavailable", "unknown"].includes(ready.control.state);
    const disabled = this._smartControlPending || !available || (!on && (ready.unavailable || ready.state !== "ready" || ready.attrs.ready !== true || ready.mode !== "auto" || ready.controlState === "paused"));
    return `<div class="chc-smart-action"><button type="button" data-action="smart-control:${on ? "off" : "on"}" ${disabled ? "disabled" : ""}>${this._smartControlPending ? "Saving…" : on ? ready.controlState === "paused" ? "Turn off automatic timing" : "Use existing schedule instead" : "Enable automatic timing"}</button><span role="status">${this._escape(this._smartControlMessage)}</span></div>`;
  }

  _learningNeeds() {
    const readiness = this._readiness();
    const checks = Array.isArray(readiness.attrs.checks) ? readiness.attrs.checks : [];
    const failed = new Set(checks.filter(check => check && !check.passed).map(check => check.key));
    const needs = [];
    if (["thermal_windows", "model_score"].some(key => failed.has(key))) needs.push("More examples of the house heating up and cooling down.");
    if (failed.has("heating_response")) needs.push("More heating starts and stops to measure the warm-up delay.");
    if (failed.has("demand_model")) needs.push("More examples of how the thermostat responds to a temperature change.");
    if (failed.has("gas_calibration")) needs.push("More gas readings during heating, to improve cost estimates.");
    if (["accuracy_1h", "accuracy_6h", "accuracy_24h"].some(key => failed.has(key))) needs.push("More accurate temperature forecasts, checked across several days.");
    if (failed.has("plan_feasible") && !["thermal_windows", "model_score", "heating_response", "demand_model"].some(key => failed.has(key))) needs.push("A plan that can reach your comfort temperature in time.");
    const dataKeys = ["thermostat", "outdoor", "weather", "demand", "occupancy", "schedule", "arrival", "plan_fresh", "gas_fresh", "rate_available", "commands", "clock"];
    if (dataKeys.some(key => failed.has(key))) needs.push("A missing reading or heating connection needs attention. See detailed checks below.");
    if (failed.has("preview_enabled")) needs.push("Schedule planning needs to be enabled in the settings below.");
    if (!needs.length && failed.has("stable")) needs.push("One continuous hour with all the safety checks passing.");
    if (!needs.length && readiness.attrs.ready !== true) needs.push("Readiness information is not available yet. Check the integration.");
    return needs;
  }

  _detail(key, title, body) {
    return `<details class="chc-disclosure" data-detail="${key}" ${this._openDetails.has(key) ? "open" : ""}><summary>${title}</summary><div class="chc-disclosure-body">${body}</div></details>`;
  }

  _renderDashboardMode(mode) {
    const active = this.shadowRoot.activeElement;
    const activePath = active?.dataset?.draftPath;
    const selection = activePath && typeof active.selectionStart === "number" ? [active.selectionStart, active.selectionEnd] : null;
    const status = this._statusState();
    const attrs = status?.attributes || {};
    const plan = this._state(this._entities.smartHeatingPlan);
    const readiness = this._readiness();
    const title = mode === "overview" ? this._config.title : mode === "schedule" ? "Comfort times" : "Learning";
    const header = `<div class="chc-header"><div class="chc-title">${this._escape(title)}</div>${mode === "overview" ? `<span class="chc-activity">${this._escape(this._activityLabel())}</span>` : ""}</div>`;
    let body;
    if (mode === "overview") {
      const target = this._formatEntity(this._entities.effectiveTarget, this._formatTemperature(attrs.effective_target_temperature));
      const forecast = this._state(this._entities.indoorTemperatureForecast);
      const cost = [this._renderCost("Today", this._entities.heatingCostToday), this._renderCost("Tomorrow", this._entities.heatingCostTomorrow)].join("");
      const historyEntity = attrs.climate_entity;
      const progress = readiness.controlState === "active" ? "Automatic timing is on" : readiness.attrs.ready === true ? "Automatic timing is ready to enable" : "Automatic timing is still learning";
      body = `<p class="chc-policy">${this._escape(this._policySummary())}</p><div class="chc-overview-primary"><div><div class="chc-label">Inside now</div><div class="chc-current-big">${this._escape(this._formatTemperature(attrs.current_temperature))}</div></div><div><div class="chc-label">Target now</div><div class="chc-target">${this._escape(target)}</div></div></div>
        ${this._renderDashboardForecast()}${this._renderNextChanges()}${this._renderControlChoice()}
        <div class="chc-now-links">${this._dashboardLink("schedule", "Change comfort times")}${historyEntity ? `<button type="button" class="chc-link-button" data-more-info="${this._escape(historyEntity)}">Past temperatures</button>` : ""}</div>
        <p class="chc-progress-link">${this._dashboardLink("learning", `${progress} →`)}</p>
        ${this._detail("costs", "Heating costs", `<div class="chc-cost-grid">${cost}</div><p>Heating only; hot water and the standing charge are excluded. Estimates need enough matched gas readings.</p>`)}
        ${forecast?.attributes?.confidence === "learning" ? '<p class="chc-small-note">Temperature estimates are still improving as more readings arrive.</p>' : ""}`;
    } else if (mode === "schedule") {
      const settings = plan?.attributes?.settings;
      const available = settings && typeof settings === "object" && Object.keys(settings).length;
      const paused = readiness.controlState === "paused";
      const note = paused ? `${this._policySummary()} ${readiness.control?.state === "on" ? "These times will be used when automatic timing resumes." : "These times prepare automatic timing; enable it later from Learning."}` : readiness.controlState === "active" ? "Automatic timing uses these comfort times now. It chooses when to start warming the house."
        : readiness.controlState === "fallback" ? "Your existing schedule is running. These times are saved for automatic timing when it can safely resume."
          : "Your existing schedule still runs the heating. These times prepare automatic timing; they will not take over until you enable it.";
      body = `<p class="chc-intro">Choose when you want the house warm. We learn how early the heating needs to start.</p><p class="chc-policy">${this._escape(note)}</p>
        <div class="chc-setpoints">${this._renderNumberControl("Comfort temperature", this._entities.highTemperature)}</div><p class="chc-small-note">This temperature is shared with your existing heating schedule.</p>
        ${available ? this._renderScheduleEditor(settings, readiness.controlState) : '<p class="chc-empty">Comfort-time settings are unavailable. Check the integration.</p>'}
        ${this._detail("preview", readiness.controlState === "active" ? "See the automatic temperature plan" : "See the automatic timing preview", `<p>${readiness.controlState === "active" ? "This plan is controlling heating, unless you select Away, Off or a manual temperature." : "This is a proposed plan. The graph on Now shows what your heating is actually expected to do."}</p>${this._renderDashboardForecast(this._entities.smartHeatingPlan, true)}`)}
        <p class="chc-progress-link">${this._dashboardLink("learning", readiness.attrs.ready === true ? "Ready to enable automatic timing →" : "See what automatic timing still needs to learn →")}</p>`;
    } else {
      const forecast = this._state(this._entities.indoorTemperatureForecast)?.attributes || {};
      const unit = forecast.temperature_unit || "°C";
      const accuracy = ["1h", "6h", "24h"].map(h => {
        const item = readiness.attrs.recent_accuracy?.[h] || forecast.forecast_accuracy?.[h] || {};
        const error = this._finiteNumber(item.mae_c), count = this._finiteNumber(item.count);
        const value = error !== null && error >= 0 && count > 0 ? `${this._formatNumber(unit === "°F" ? error * 1.8 : error, unit)} average error` : "Waiting for readings";
        return this._renderMetric(`${h === "1h" ? "1 hour" : h === "6h" ? "6 hours" : "24 hours"} ahead`, value);
      }).join("");
      const heading = readiness.controlState === "active" ? "Automatic timing is on" : readiness.unavailable ? "Learning information unavailable" : readiness.attrs.ready === true ? "Ready to use automatic timing" : "Still learning your home";
      const needs = this._learningNeeds();
      const response = forecast.heating_response_learned === true ? this._formatNumber(forecast.heating_response_minutes, "min") : "Still learning";
      const cost = this._state(this._entities.heatingCostToday)?.attributes?.calibration;
      const technical = `${this._renderReadiness("open")}<div class="chc-learning-grid">${this._renderMetric("Temperature samples", String(forecast.learned_samples ?? "Waiting"))}${this._renderMetric("Heating response time", response)}${this._renderMetric("Matched gas readings", String(cost?.sample_count ?? 0))}</div>`;
      body = `<section class="chc-learning-summary"><h2>${heading}</h2><p>${this._escape(this._policySummary())}</p>${readiness.controlState === "active" ? '<p>The safety checks have passed. You can return to your existing schedule at any time.</p>' : readiness.attrs.ready === true ? readiness.controlState === "paused" ? '<p>Return to Auto and end any temporary override before enabling automatic timing.</p>' : '<p>The checks have passed. Automatic timing starts only when you enable it.</p>' : `<h3>What is still needed</h3><ul>${needs.map(need => `<li>${this._escape(need)}</li>`).join("")}</ul>`}</section>${this._renderSmartAction()}
        <section class="chc-accuracy-summary"><h3>How close were the forecasts?</h3><p>Compared with measured indoor temperatures. Smaller errors are better.</p><div class="chc-learning-grid">${accuracy}</div></section>
        ${this._detail("checks", "Detailed learning and safety checks", technical)}
        ${this._detail("planning", "Planning settings", `<label class="chc-enable"><input type="checkbox" data-planning-enabled ${this._planningPending ? "disabled" : ""} ${plan?.attributes?.settings?.enabled === true ? "checked" : ""}> Prepare automatic heating plans</label><p>Turning planning off keeps your existing schedule in control.</p><span role="status">${this._escape(this._planningMessage || "")}</span>`)}
        ${readiness.controlState === "fallback" && readiness.attrs.fallback_reason ? `<p class="chc-small-note">${this._escape(readiness.attrs.fallback_reason)}</p>` : ""}`;
    }
    this.shadowRoot.innerHTML = `<style>${this._styles()}</style><ha-card class="chc-card chc-dashboard chc-dashboard-${mode}">${header}${body}</ha-card>`;
    this._bindActions();
    if (activePath) {
      const replacement = [...this.shadowRoot.querySelectorAll("[data-draft-path]")].find(input => input.dataset.draftPath === activePath);
      replacement?.focus();
      if (selection) replacement?.setSelectionRange?.(...selection);
    }
  }

  _renderDashboardForecast(entityId = this._entities.indoorTemperatureForecast, preview = false) {
    return `<section class="chc-forecast chc-simple-forecast" aria-label="${preview ? "Automatic timing preview" : "Live heating forecast"}"><div class="chc-section-title">Next 24 hours</div>${this._renderForecast(true, entityId, "simple")}<p class="chc-forecast-note">Shading shows when heating is likely to be needed.</p></section>`;
  }

  _renderCost(label, entityId) {
    const state = this._state(entityId);
    const number = this._finiteNumber(state?.state);
    const value = number !== null && number >= 0 ? this._formatEntity(entityId) : "Unavailable";
    return `<div class="chc-cost"><div class="chc-label">${this._escape(label)}</div><div class="chc-cost-value">${this._escape(value)}</div></div>`;
  }

  _formatTemperature(value) {
    if (value === undefined || value === null || value === "") return "Unavailable";
    const unit = this._state(this._entities.indoorTemperatureForecast)?.attributes?.temperature_unit || this._state(this._entities.effectiveTarget)?.attributes?.unit_of_measurement || this._hass?.config?.unit_system?.temperature || "°C";
    return this._formatNumber(value, unit === "degC" ? "°C" : unit === "degF" ? "°F" : unit);
  }

  _renderStatusEntityError() {
    this.shadowRoot.innerHTML = `
      <style>${this._styles()}</style>
      <ha-card class="chc-card">
        <div class="chc-header">
          <div>
            <div class="chc-title">${this._escape(this._config.title)}</div>
            <div class="chc-subtitle">Status entity not found</div>
          </div>
          <div class="chc-badge">Error</div>
        </div>
        <div class="chc-error">
          The configured status entity ${this._escape(this._config.entity)} is not available.
        </div>
      </ha-card>
    `;
  }

  _renderControl() {
    const status = this._statusState();
    const attributes = status?.attributes || {};
    const target = this._formatEntity(
      this._entities.effectiveTarget,
      this._formatNumber(attributes.effective_target_temperature),
    );
    const current = this._formatNumber(attributes.current_temperature);
    const statusLabel = this._statusLabel(status?.state);

    const reason = attributes.reason ? `
      <div class="chc-reason">${this._escape(attributes.reason)}</div>
    ` : "";
    const manual = attributes.manual_override ? `
      <div class="chc-alert">Manual override is active</div>
    ` : "";
    const selectedMode = this._state(this._entities.controllerMode)?.state || "auto";
    const previewState = this._state(this._entities.smartHeatingPlan);
    const readiness = this._readiness();
    const planActive = previewState?.state === "active" && previewState.attributes?.preview === false;
    const preview = (previewState?.state === "preview" && previewState.attributes?.preview === true) || planActive ? `
      <section class="chc-preview" aria-label="${planActive ? "Intelligent heating plan" : "Smart heating preview"}">
        <div class="chc-preview-heading"><strong>${planActive ? "Intelligent heating active" : "Preview only"}</strong><span>${planActive ? "Controls heating" : "Does not control heating"}</span></div>
        ${previewState.attributes?.explanation ? `<div class="chc-forecast-note">${this._escape(previewState.attributes.explanation)}</div>` : ""}
        ${this._renderForecast(false, this._entities.smartHeatingPlan, planActive ? "active" : "preview")}
      </section>
    ` : "";
    const previewUnavailable = ["unavailable", "unknown"].includes(previewState?.state) ? `
      <section class="chc-preview" aria-label="Smart heating preview unavailable">
        <div class="chc-preview-heading"><strong>${readiness.controlState === "fallback" ? "Using existing schedule" : "Preview unavailable"}</strong><span>${readiness.controlState === "fallback" ? "Existing schedule controls heating" : "Preview only"}</span></div>
        <div class="chc-forecast-note">${this._escape(previewState.attributes?.explanation || "The smart heating preview is currently unavailable.")}</div>
      </section>
    ` : "";
    const scheduleEditor = this._renderScheduleEditor(previewState?.attributes?.settings || EMPTY_SETTINGS, readiness.controlState);

    this.shadowRoot.innerHTML = `
      <style>${this._styles()}</style>
      <ha-card class="chc-card">
        <div class="chc-header">
          <div>
            <div class="chc-title">${this._escape(this._config.title)}</div>
            <div class="chc-subtitle">Central Heating Controller</div>
          </div>
          <div class="chc-badge">${this._escape(statusLabel)}</div>
        </div>
        <div class="chc-main">
          <div>
            <div class="chc-label">Effective target</div>
            <div class="chc-target">${this._escape(target)}</div>
          </div>
          <div>
            <div class="chc-label">Current</div>
            <div class="chc-current">${this._escape(current)}</div>
          </div>
        </div>
        <label class="chc-mode"><span class="chc-label">Mode</span><select data-mode aria-label="Mode">
          ${["auto", "away", "off"].map(mode => `<option value="${mode}" ${selectedMode === mode ? "selected" : ""}>${this._titleCase(mode)}</option>`).join("")}
        </select></label>
        <div class="chc-actions">
          <button type="button" data-action="auto">${this._autoModeLabel()}</button>
          <button type="button" data-action="blast" class="chc-hot">Heat Blast</button>
        </div>
        <div class="chc-setpoints">
          ${this._renderNumberControl("High", this._entities.highTemperature)}
          ${this._renderNumberControl("Low", this._entities.lowTemperature)}
          ${this._renderNumberControl("Eco", this._entities.ecoTemperature)}
        </div>
        ${this._renderForecast()}
        ${preview}
        ${previewUnavailable}
        ${scheduleEditor}
        <a class="chc-configure" href="/config/integrations/integration/central_heating_controller">Configure schedule and integration settings</a>
        ${reason}
        ${manual}
      </ha-card>
    `;
    this._bindActions();
  }

  _renderSettings() {
    const status = this._statusState();
    const statusLabel = this._statusLabel(status?.state);
    const reason = status?.attributes?.reason;

    this.shadowRoot.innerHTML = `
      <style>${this._styles()}</style>
      <ha-card class="chc-card">
        <div class="chc-header">
          <div>
            <div class="chc-title">${this._escape(this._config.title)}</div>
            <div class="chc-subtitle">Heating settings</div>
          </div>
          <div class="chc-badge">${this._escape(statusLabel)}</div>
        </div>
        <div class="chc-settings">
          ${this._renderNumberControl("High", this._entities.highTemperature)}
          ${this._renderNumberControl("Low", this._entities.lowTemperature)}
          ${this._renderNumberControl("Eco", this._entities.ecoTemperature)}
          ${this._renderNumberControl("Fallback warm-up", this._entities.fallbackWarmup)}
          ${this._renderNumberControl("Maximum warm-up", this._entities.maximumWarmup)}
        </div>
        ${reason ? `<div class="chc-reason">${this._escape(reason)}</div>` : ""}
      </ha-card>
    `;
    this._bindActions();
  }

  _renderVisual() {
    const status = this._statusState();
    const attributes = status?.attributes || {};
    const target = this._formatEntity(
      this._entities.effectiveTarget,
      this._formatNumber(attributes.effective_target_temperature),
    );
    const current = this._formatNumber(attributes.current_temperature);
    const preheat = this._formatTime(
      this._state(this._entities.preheatStart)?.state || attributes.preheat_start_time,
    );
    const arrival = this._formatTime(attributes.arrival_time);
    const learned = this._formatEntity(this._entities.learnedRate);
    const statusLabel = this._statusLabel(status?.state);
    const forecast = this._renderForecast();

    this.shadowRoot.innerHTML = `
      <style>${this._styles()}</style>
      <ha-card class="chc-card">
        <div class="chc-header">
          <div>
            <div class="chc-title">${this._escape(this._config.title)}</div>
            <div class="chc-subtitle">Journey and controller status</div>
          </div>
          <div class="chc-badge">${this._escape(statusLabel)}</div>
        </div>
        <div class="chc-journey">
          <div class="chc-section-title">Journey</div>
          <div class="chc-stages">
            ${this._renderStage("away", status?.state)}
            ${this._renderStage("pre_heating", status?.state)}
            ${this._renderStage("high", status?.state)}
            ${this._renderStage("low", status?.state)}
            ${this._renderStage("heat_blast", status?.state)}
            ${this._renderStage("manual_override", status?.state)}
            ${this._renderStage("off", status?.state)}
            ${this._renderStage("unavailable", status?.state)}
          </div>
        </div>
        <div class="chc-visual-grid">
          ${this._renderMetric("Target", target)}
          ${this._renderMetric("Current", current)}
          ${this._renderMetric("Pre-heat start", preheat)}
          ${this._renderMetric("Arrival", arrival)}
          ${this._renderMetric("Learned rate", learned)}
        </div>
        ${forecast}
        <div class="chc-actions">
          <button type="button" data-action="auto">${this._autoModeLabel()}</button>
          <button type="button" data-action="blast" class="chc-hot">Heat Blast</button>
        </div>
        ${attributes.reason ? `<div class="chc-reason">${this._escape(attributes.reason)}</div>` : ""}
      </ha-card>
    `;
    this._bindActions();
  }

  _renderForecast(compact = false, entityId = this._entities.indoorTemperatureForecast, presentation = "forecast") {
    const state = this._state(entityId);
    const key = this._dateFormatters().key;
    this._forecastCache ||= new Map();
    const cacheKey = `${entityId}:${compact}:${presentation}`;
    const cached = this._forecastCache.get(cacheKey);
    if (cached?.state === state && cached.formatKey === key) return cached.markup;
    const markup = this._buildForecast(compact, entityId, presentation);
    this._forecastCache.set(cacheKey, { state, formatKey: key, markup });
    return markup;
  }

  _buildForecast(compact = false, entityId = this._entities.indoorTemperatureForecast, presentation = "forecast") {
    const state = this._state(entityId);
    const attrs = state?.attributes || {};
    const rows = this._forecastRows(entityId);
    const unit = attrs.temperature_unit || "°C";
    const confidence = attrs.confidence;
    const reason = attrs.reason;
    let content;

    if (rows.length) {
      const width = 600, height = presentation === "simple" ? 260 : 220;
      const left = presentation === "simple" ? 64 : 48, right = 12, top = 12, bottom = presentation === "simple" ? 42 : 34;
      const times = rows.map((p) => p.time);
      const values = rows.flatMap((p) => p.target === null ? [p.temperature] : [p.temperature, p.target]);
      const tMin = times[0], tMax = times[times.length - 1] || tMin + 1;
      let vMin = Math.min(...values), vMax = Math.max(...values);
      const pad = Math.max((vMax - vMin) * 0.15, 0.5);
      vMin -= pad; vMax += pad;
      const x = (time) => left + (time - tMin) / (tMax - tMin || 1) * (width - left - right);
      const y = (value) => top + (vMax - value) / (vMax - vMin) * (height - top - bottom);
      const pathFor = (key, step = false) => {
        const chunks = [];
        let prior = null;
        for (const point of rows) {
          const value = point[key];
          if (value === null) { prior = null; continue; }
          const px = x(point.time).toFixed(1), py = y(value).toFixed(1);
          if (!prior) chunks.push(`M${px},${py}`);
          else if (step) chunks.push(`L${px},${y(prior[key]).toFixed(1)} L${px},${py}`);
          else chunks.push(`L${px},${py}`);
          prior = point;
        }
        return chunks.join(" ");
      };
      const ticks = [0, 1, 2].map((i) => {
        const value = vMin + (vMax - vMin) * i / 2;
        const yy = y(value);
        return `<g><line x1="${left}" y1="${yy}" x2="${width-right}" y2="${yy}" class="chc-chart-grid"/><text x="${left-7}" y="${yy+4}" text-anchor="end">${value.toFixed(1)} ${this._escape(unit)}</text></g>`;
      }).join("");
      const formatAxisTime = (value) => {
        const date = new Date(value);
        const formats = this._dateFormatters();
        return `${formats.day.format(date)} ${formats.time.format(date)}`;
      };
      const heatingBands = rows.slice(0, -1).map((point, index) => {
        const next = rows[index + 1];
        // Demand describes the interval beginning at this point; do not bridge gaps.
        if (point.target === null || point.demand === null || point.demand <= 5 || point.demand > 100
            || next.time <= point.time || next.time - point.time > 10 * 60 * 1000) return "";
        return `<rect class="chc-heating-band" x="${x(point.time)}" y="${top}" width="${x(next.time) - x(point.time)}" height="${height-top-bottom}"/>`;
      }).join("");
      const first = formatAxisTime(tMin);
      const last = formatAxisTime(tMax);
      const timeTicks = [];
      const interval = 6 * 60 * 60 * 1000;
      const axisTick = (time, includeDay = false) => {
        const date = new Date(time);
        const formats = this._dateFormatters();
        const label = this._timeLabel(date);
        const day = formats.day.format(date);
        const previousDay = formats.day.format(new Date(time - interval));
        const tickLabel = includeDay || day !== previousDay ? `${day} ${label}` : label;
        return `<g><line x1="${x(time)}" y1="${top}" x2="${x(time)}" y2="${height-bottom}" class="chc-chart-grid chc-chart-vgrid"/><text x="${x(time)}" y="${height-17}" text-anchor="middle">${this._escape(tickLabel)}</text></g>`;
      };
      if (compact) {
        for (let i = 0; i <= 4; i++) timeTicks.push(axisTick(tMin + (tMax - tMin) * i / 4, i === 0 || i === 4));
      } else {
      timeTicks.push(axisTick(tMin, true));
      for (let time = Math.ceil(tMin / interval) * interval; time < tMax; time += interval) {
        if (time > tMin) timeTicks.push(axisTick(time));
      }
      }
      content = `<div class="chc-chart-wrap">
        <svg class="chc-chart" viewBox="0 0 ${width} ${height}" role="img" aria-labelledby="chc-chart-title chc-chart-desc">
          <title id="chc-chart-title">${presentation === "active" ? "Intelligent heating plan" : presentation === "fallback" ? "Existing schedule forecast" : presentation === "preview" ? "Proposed heating plan" : "Indoor temperature forecast"} for the next 24 hours</title>
          <desc id="chc-chart-desc">Predicted indoor temperature and target temperature, in ${this._escape(unit)}, from ${this._escape(first)} to ${this._escape(last)} local time.</desc>
          <g class="chc-chart-bands">${heatingBands}</g><g class="chc-chart-temperature-ticks">${ticks}</g><g class="chc-chart-time-ticks">${timeTicks.join("")}</g>
          <path d="${pathFor("temperature")}" class="chc-chart-line chc-chart-indoor"/><path d="${pathFor("target", true)}" class="chc-chart-line chc-chart-target"/>
        </svg><div class="chc-chart-legend"><span><i class="chc-legend-indoor"></i>${presentation === "simple" ? "Expected temperature" : presentation === "active" ? "Plan temperature" : presentation === "fallback" ? "Existing schedule" : presentation === "preview" ? "Proposed plan" : compact ? "Forecast" : "Indoor forecast"}</span><span><i class="chc-legend-target"></i>Target</span><span><i class="chc-legend-heating"></i>${presentation === "simple" ? "Heating likely" : "Heating expected"}</span></div>
      </div>`;
    } else if (confidence === "learning") {
      content = `<div class="chc-forecast-state">${this._escape(reason || "Learning your home’s heating response. A forecast will appear when enough data is available.")}</div>`;
    } else {
      content = `<div class="chc-forecast-state">${this._escape(reason || (!state || ["unknown", "unavailable"].includes(state.state) ? "Forecast is unavailable." : "A 24-hour forecast is not available yet."))}</div>`;
    }
    if (compact) return rows.length > 1 ? content : '<div class="chc-kiosk-empty">Forecast unavailable</div>';
    const learning = confidence === "learning" && rows.length
      ? `<div class="chc-forecast-state is-learning">Uncalibrated estimate — learning your home’s heating response.</div>` : "";
    const endpoint = state && Number.isFinite(Number(state.state))
      ? `<div class="chc-forecast-endpoint">In 24 hours <strong>${this._escape(this._formatNumber(state.state, unit))}</strong></div>` : "";
    const assumptions = Array.isArray(attrs.assumptions) && attrs.assumptions.length
      ? `<ul class="chc-forecast-assumptions">${attrs.assumptions.map((item) => `<li>${this._escape(item)}</li>`).join("")}</ul>` : "";
    const accuracyNotes = ["1h", "6h", "24h"].flatMap((horizon) => {
      const accuracy = attrs.forecast_accuracy?.[horizon];
      const errorC = this._finiteNumber(accuracy?.mae_c);
      const count = this._finiteNumber(accuracy?.count);
      if (errorC === null || errorC < 0 || count === null || count <= 0) return [];
      const error = unit === "°F" ? errorC * 9 / 5 : errorC;
      const label = horizon === "1h" ? "1-hour" : horizon === "6h" ? "6-hour" : "24-hour";
      return [`<span>Mean ${label} error: ${this._escape(this._formatNumber(error, unit))} (${this._escape(count)} checks)</span>`];
    }).join('<span aria-hidden="true"> · </span>');
    const accuracyNote = accuracyNotes ? `<div class="chc-forecast-note chc-accuracy">${accuracyNotes}</div>` : "";
    const demand = this._finiteNumber(attrs.current_heating_demand_percent);
    const demandNote = demand !== null && demand >= 0 && demand <= 100
      ? `<span>Current reported demand: ${this._escape(this._formatNumber(demand, "%"))}</span>` : "";
    const responseMinutes = this._finiteNumber(attrs.heating_response_minutes);
    const responseNote = attrs.heating_response_learned === true && responseMinutes === 0
      ? `<span title="Time to reach about 63% of the effect of a demand change">Heating response: immediate</span>`
      : attrs.heating_response_learned === true && responseMinutes !== null && responseMinutes > 0
        ? `<span title="Time to reach about 63% of the effect of a demand change">Heating response: ~${this._escape(this._formatNumber(responseMinutes, "min").replace(/\.0 min$/, " min"))}</span>`
        : attrs.heating_response_learned === false
        ? "<span>Learning heating response</span>" : "";
    const modelNotes = [demandNote, responseNote].filter(Boolean).join('<span aria-hidden="true"> · </span>');
    const modelNote = modelNotes ? `<div class="chc-forecast-note chc-model-note">${modelNotes}</div>` : "";
    return `<section class="chc-forecast" aria-label="Indoor temperature forecast"><div class="chc-section-title">Next 24 hours</div>${learning}${content}${endpoint}${reason && rows.length ? `<div class="chc-forecast-note">${this._escape(reason)}</div>` : ""}${modelNote}${accuracyNote}${assumptions}</section>`;
  }

  _finiteNumber(value) {
    if (value === null || value === undefined || value === "" || typeof value === "boolean") return null;
    const number = Number(value);
    return Number.isFinite(number) ? number : null;
  }

  _autoModeLabel() {
    const auto = this._state(this._entities.autoMode);
    return auto?.state === "on" ? "Auto mode on" : "Auto mode off";
  }

  _renderNumberControl(label, entityId) {
    const stateObj = this._state(entityId);
    const unavailable = !stateObj || stateObj.state === "unavailable" || stateObj.state === "unknown";
    const value = unavailable ? "Unavailable" : this._formatEntity(entityId);
    const pending = this._numberControlPending.has(entityId);
    const disabled = unavailable || pending ? "disabled" : "";
    const message = this._numberControlMessage?.entityId === entityId ? this._numberControlMessage.text : "";

    return `
      <div class="chc-number ${unavailable ? "is-disabled" : ""}">
        <div>
          <div class="chc-label">${this._escape(label)}</div>
          <div class="chc-number-value">${this._escape(value)}</div>
        </div>
        <div class="chc-stepper">
          <button type="button" data-action="decrement:${this._escape(entityId)}" ${disabled} aria-label="Decrease ${this._escape(label)}">-</button>
          <button type="button" data-action="increment:${this._escape(entityId)}" ${disabled} aria-label="Increase ${this._escape(label)}">+</button>
        </div>
        ${pending ? '<span class="chc-number-message" role="status">Saving…</span>' : message ? `<span class="chc-number-message is-error" role="status">${this._escape(message)}</span>` : ""}
      </div>
    `;
  }

  _renderStage(status, activeStatus) {
    const active = status === activeStatus ? "is-active" : "";
    return `
      <div class="chc-stage ${active}">
        <span></span>
        ${this._escape(this._statusLabel(status))}
      </div>
    `;
  }

  _renderMetric(label, value) {
    return `
      <div class="chc-metric">
        <div class="chc-label">${this._escape(label)}</div>
        <div class="chc-metric-value">${this._escape(value)}</div>
      </div>
    `;
  }

  _formatTime(value) {
    if (!value || value === "unknown" || value === "unavailable") {
      return "Unavailable";
    }
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) {
      return String(value);
    }
    return this._dateFormatters().time.format(date);
  }

  _bindActions() {
    if (!this.shadowRoot.querySelectorAll) {
      return;
    }

    for (const button of this.shadowRoot.querySelectorAll("[data-action]")) {
      button.addEventListener("click", () => {
        const action = button.dataset.action;
        if (action === "auto") {
          this._toggleAutoMode();
        } else if (action === "blast") {
          this._pressHeatBlast();
        } else if (action === "smart-control:on") {
          this._setSmartHeatingControl(true);
        } else if (action === "smart-control:off") {
          this._setSmartHeatingControl(false);
        } else if (action?.startsWith("period-")) {
          this._changeSchedulePeriods(action);
        } else if (action?.startsWith("increment:")) {
          this._adjustNumber(action.slice("increment:".length), 1);
        } else if (action?.startsWith("decrement:")) {
          this._adjustNumber(action.slice("decrement:".length), -1);
        }
      });
    }
    const mode = this.shadowRoot.querySelector("[data-mode]");
    if (mode) mode.addEventListener("change", (event) => this._selectControllerMode(event.target.value));
    for (const input of this.shadowRoot.querySelectorAll("[data-schedule-source]")) {
      input.addEventListener("change", event => this._setScheduleSource(event.target.value));
    }
    for (const details of this.shadowRoot.querySelectorAll("[data-detail]")) {
      details.addEventListener("toggle", event => {
        if (!event.target.isConnected) return;
        if (event.target.open) this._openDetails.add(details.dataset.detail);
        else this._openDetails.delete(details.dataset.detail);
      });
    }
    for (const button of this.shadowRoot.querySelectorAll("[data-more-info]")) {
      button.addEventListener("click", () => this._showMoreInfo(button.dataset.moreInfo));
    }
    const planning = this.shadowRoot.querySelector("[data-planning-enabled]");
    if (planning) planning.addEventListener("change", event => this._setPlanningEnabled(event.target.checked));
    for (const input of this.shadowRoot.querySelectorAll("[data-draft-path]")) {
      const update = event => this._updateScheduleDraft(input.dataset.draftPath,
        input.type === "checkbox" ? event.target.checked : event.target.value);
      input.addEventListener(input.type === "checkbox" ? "change" : "input", update);
    }
    const form = this.shadowRoot.querySelector("[data-schedule-form]");
    if (form) form.addEventListener("submit", event => {
      event.preventDefault();
      this._saveSchedule();
    });
    const details = this.shadowRoot.querySelector(".chc-schedule-editor");
    if (details) details.addEventListener("toggle", event => this._setScheduleEditorOpen(event.target.open));
  }

  _styles() {
    return `
      :host { display: block; }
      .chc-card {
        display: block;
        overflow: hidden;
        color: var(--primary-text-color, #1f2933);
        background: var(--ha-card-background, var(--card-background-color, #fff));
      }
      .chc-header {
        display: flex;
        align-items: flex-start;
        justify-content: space-between;
        gap: 12px;
        padding: 18px 18px 12px;
        border-bottom: 1px solid var(--divider-color, rgba(127, 127, 127, 0.2));
      }
      .chc-title {
        font-size: 18px;
        font-weight: 700;
      }
      .chc-subtitle,
      .chc-label {
        color: var(--secondary-text-color, #667085);
        font-size: 12px;
      }
      .chc-badge {
        border-radius: 999px;
        padding: 5px 9px;
        background: color-mix(in srgb, var(--primary-color, #03a9f4) 16%, transparent);
        color: var(--primary-color, #0369a1);
        font-size: 12px;
        font-weight: 700;
        white-space: nowrap;
      }
      .chc-main {
        display: grid;
        grid-template-columns: minmax(0, 1fr) auto;
        gap: 14px;
        padding: 18px;
      }
      .chc-overview-primary { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; padding: 20px 18px 16px; align-items: end; }
      .chc-current-big { margin-top: 4px; font-size: 34px; font-weight: 750; line-height: 1; }
      .chc-overview-primary .chc-target { font-size: 42px; }
      .chc-cost-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; padding: 0 18px 16px; }
      .chc-cost { border: 1px solid var(--divider-color, rgba(127,127,127,.22)); border-radius: 8px; padding: 11px; }
      .chc-cost-value { margin-top: 5px; font-size: 21px; font-weight: 750; }
      .chc-dashboard-hint { padding: 0 18px 18px; }
      .chc-readiness { margin: 0 18px 16px; padding: 13px; border: 1px solid var(--divider-color, rgba(127,127,127,.25)); border-left: 4px solid var(--primary-color, #007ba8); border-radius: 9px; }
      .chc-readiness.is-active { border-left-color: #25865a; }
      .chc-readiness.is-fallback, .chc-readiness.is-unavailable { border-left-color: var(--warning-color, #b7791f); }
      .chc-readiness-heading { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 5px 12px; }
      .chc-readiness-heading > span { color: var(--secondary-text-color, #667085); font-size: 12px; }
      .chc-readiness p { margin: 6px 0 0; color: var(--secondary-text-color, #667085); font-size: 13px; line-height: 1.45; }
      .chc-readiness .chc-fallback-reason { color: var(--primary-text-color, #1f2933); }
      .chc-readiness-checks { margin-top: 12px; padding-top: 10px; border-top: 1px solid var(--divider-color, rgba(127,127,127,.2)); }
      .chc-readiness-checks > summary { cursor: pointer; font-weight: 700; }
      .chc-readiness-checks ul { display: grid; gap: 9px; margin: 9px 0 0; padding: 0; list-style: none; }
      .chc-readiness-checks li { display: grid; grid-template-columns: 18px minmax(0,1fr); gap: 6px; font-size: 13px; }
      .chc-readiness-checks li > span:first-child { font-weight: 700; }
      .chc-readiness-checks li.is-passed > span:first-child { color: #25865a; }
      .chc-readiness-checks li.is-pending > span:first-child { color: var(--warning-color, #9a6700); }
      .chc-readiness-checks small { display: block; margin-top: 2px; color: var(--secondary-text-color, #667085); font-size: 12px; line-height: 1.4; }
      .chc-readiness-blockers { margin-top: 11px; color: var(--secondary-text-color, #667085); font-size: 12px; }
      .chc-readiness-blockers ul { margin: 5px 0 0; padding-left: 18px; line-height: 1.45; }
      .chc-smart-action { display: flex; flex-wrap: wrap; align-items: center; gap: 9px; margin: 0 18px 16px; }
      .chc-smart-action button { min-height: 42px; padding: 9px 14px; border: 0; border-radius: 8px; color: white; background: var(--primary-color, #007ba8); font: inherit; font-weight: 700; cursor: pointer; }
      .chc-smart-action button:disabled { opacity: .55; cursor: not-allowed; }
      .chc-smart-action span { color: var(--secondary-text-color, #667085); font-size: 12px; }
      .chc-learning-grid { display: grid; grid-template-columns: repeat(2,minmax(0,1fr)); gap: 10px; padding: 18px; }
      .chc-dashboard { max-width: 1200px; margin-inline: auto; }
      .chc-dashboard .chc-chart { max-height: 340px; }
      .chc-dashboard-overview .chc-forecast { padding-top: 4px; }
      .chc-dashboard-schedule .chc-setpoints { grid-template-columns: minmax(0, 1fr); max-width: 540px; }
      .chc-dashboard-controls { display: grid; grid-template-columns: minmax(0, 1fr) minmax(170px, .45fr); align-items: center; }
      .chc-dashboard-controls .chc-overview-primary { grid-row: 1 / 3; }
      .chc-dashboard-controls .chc-actions { padding-top: 0; }
      .chc-dashboard-learning .chc-metric-value { font-size: 18px; line-height: 1.5; overflow-wrap: anywhere; }
      @media (min-width: 850px) {
        .chc-dashboard-learning .chc-learning-grid { grid-template-columns: repeat(3,minmax(0,1fr)); }
        .chc-dashboard-schedule .chc-schedule-editor form { grid-template-columns: repeat(2,minmax(0,1fr)); }
        .chc-dashboard-schedule .chc-enable, .chc-dashboard-schedule .chc-schedule-hint,
        .chc-dashboard-schedule .chc-schedule-footer { grid-column: 1 / -1; }
      }
      @media (max-width: 550px) {
        .chc-dashboard-controls { grid-template-columns: minmax(0,1fr); }
        .chc-dashboard-controls .chc-overview-primary { grid-row: auto; }
        .chc-dashboard .chc-target, .chc-dashboard .chc-current-big { font-size: 30px; }
        .chc-dashboard-learning .chc-learning-grid { grid-template-columns: minmax(0,1fr); }
      }
      .chc-mode { display: grid; grid-template-columns: 1fr; gap: 6px; padding: 0 18px 14px; }
      .chc-mode select { width: 100%; min-height: 40px; padding: 7px 10px; border: 1px solid var(--divider-color, #aaa); border-radius: 8px; color: inherit; background: var(--card-background-color, #fff); font: inherit; }
      .chc-preview { margin: 0 18px 18px; padding: 12px; border: 1px solid var(--warning-color, #e59b16); border-radius: 9px; background: color-mix(in srgb, var(--warning-color, #e59b16) 7%, transparent); }
      .chc-preview .chc-forecast { padding: 10px 0 0; }
      .chc-preview-heading { display: flex; flex-wrap: wrap; justify-content: space-between; gap: 5px 12px; color: var(--warning-color, #9a6700); }
      .chc-configure { display: block; padding: 0 18px 18px; color: var(--primary-color, #03a9f4); font-size: 13px; }
      .chc-schedule-editor { margin: 0 18px 18px; border: 1px solid var(--divider-color, rgba(127, 127, 127, .25)); border-radius: 9px; }
      .chc-schedule-editor > summary { padding: 12px; cursor: pointer; font-weight: 700; }
      .chc-schedule-editor form { display: grid; gap: 10px; padding: 0 12px 12px; }
      .chc-enable { display: flex; align-items: center; gap: 8px; font-size: 13px; }
      .chc-schedule-hint { margin: 0; color: var(--secondary-text-color, #667085); font-size: 12px; }
      .chc-schedule-day { min-width: 0; margin: 0; border: 1px solid var(--divider-color, rgba(127, 127, 127, .2)); border-radius: 7px; padding: 9px; }
      .chc-schedule-day legend { padding: 0 5px; font-size: 13px; font-weight: 700; }
      .chc-period { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)) auto; align-items: end; gap: 7px; margin-bottom: 7px; }
      .chc-period label { display: grid; gap: 4px; color: var(--secondary-text-color, #667085); font-size: 11px; }
      .chc-period input { box-sizing: border-box; width: 100%; min-width: 0; min-height: 36px; padding: 5px; border: 1px solid var(--divider-color, #aaa); border-radius: 6px; color: inherit; background: var(--card-background-color, #fff); font: inherit; }
      .chc-period button, .chc-add, .chc-schedule-footer button { min-height: 34px; padding: 6px 9px; border: 1px solid var(--divider-color, #aaa); border-radius: 6px; color: var(--primary-text-color, #1f2933); background: var(--card-background-color, #fff); font: inherit; cursor: pointer; }
      .chc-add { color: var(--primary-color, #03a9f4); }
      .chc-schedule-footer { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; }
      .chc-schedule-footer span { color: var(--secondary-text-color, #667085); font-size: 12px; }
      .chc-target {
        margin-top: 2px;
        font-size: 34px;
        font-weight: 750;
        line-height: 1;
      }
      .chc-current {
        margin-top: 7px;
        font-size: 16px;
        font-weight: 700;
        text-align: right;
      }
      .chc-actions {
        display: grid;
        grid-template-columns: 1fr 1fr;
        gap: 10px;
        padding: 0 18px 18px;
      }
      .chc-setpoints {
        display: grid;
        grid-template-columns: repeat(3, minmax(0, 1fr));
        gap: 10px;
        padding: 0 18px 18px;
      }
      .chc-settings {
        display: grid;
        grid-template-columns: 1fr;
        gap: 10px;
        padding: 18px;
      }
      .chc-journey {
        padding: 18px 18px 10px;
      }
      .chc-section-title {
        margin-bottom: 10px;
        font-size: 13px;
        font-weight: 750;
      }
      .chc-stages {
        display: grid;
        grid-template-columns: repeat(4, minmax(0, 1fr));
        gap: 8px;
      }
      .chc-stage {
        display: flex;
        align-items: center;
        gap: 6px;
        min-width: 0;
        border: 1px solid var(--divider-color, rgba(127, 127, 127, 0.22));
        border-radius: 8px;
        padding: 8px;
        color: var(--secondary-text-color, #667085);
        font-size: 12px;
        font-weight: 700;
      }
      .chc-stage span {
        width: 8px;
        height: 8px;
        flex: 0 0 auto;
        border-radius: 999px;
        background: var(--disabled-text-color, #a8adb5);
      }
      .chc-stage.is-active {
        border-color: color-mix(in srgb, var(--primary-color, #03a9f4) 45%, transparent);
        color: var(--primary-text-color, #1f2933);
      }
      .chc-stage.is-active span {
        background: var(--primary-color, #03a9f4);
      }
      .chc-visual-grid {
        display: grid;
        grid-template-columns: repeat(2, minmax(0, 1fr));
        gap: 10px;
        padding: 8px 18px 18px;
      }
      .chc-forecast { padding: 0 18px 18px; }
      .chc-chart-wrap { min-width: 0; }
      .chc-chart { display: block; width: 100%; height: auto; overflow: visible; color: var(--secondary-text-color, #667085); font-family: inherit; font-size: 11px; }
      .chc-chart text { fill: currentColor; }
      .chc-chart-grid { stroke: var(--divider-color, rgba(127, 127, 127, 0.25)); stroke-width: 1; }
      .chc-chart-vgrid { stroke-dasharray: 2 4; opacity: 0.65; }
      .chc-chart-line { fill: none; stroke-width: 3; stroke-linecap: round; stroke-linejoin: round; }
      .chc-chart-indoor { stroke: var(--primary-color, #03a9f4); }
      .chc-heating-band { fill: #ed962a; opacity: .19; }
      .chc-chart-legend .chc-legend-heating { width: 14px; height: 10px; border: 0; background: #ed962a; opacity: .5; border-radius: 2px; }
      .chc-chart-target { stroke: var(--warning-color, #e59b16); stroke-dasharray: 6 5; }
      .chc-chart-dot { fill: var(--primary-color, #03a9f4); }
      .chc-chart-legend { display: flex; flex-wrap: wrap; gap: 14px; color: var(--secondary-text-color, #667085); font-size: 11px; }
      .chc-chart-legend span { display: inline-flex; align-items: center; gap: 5px; }
      .chc-chart-legend i { display: inline-block; width: 15px; border-top: 3px solid var(--primary-color, #03a9f4); }
      .chc-chart-legend .chc-legend-target { border-color: var(--warning-color, #e59b16); border-top-style: dashed; }
      .chc-forecast-state, .chc-forecast-note { color: var(--secondary-text-color, #667085); font-size: 12px; line-height: 1.45; }
      .chc-accuracy, .chc-model-note { display: flex; flex-wrap: wrap; gap: 3px 7px; margin-top: 6px; }
      .chc-forecast-state.is-learning { margin-bottom: 7px; color: var(--warning-color, #9a6700); font-weight: 700; }
      .chc-forecast-endpoint { margin-top: 8px; color: var(--secondary-text-color, #667085); font-size: 12px; }
      .chc-forecast-endpoint strong { margin-left: 5px; color: var(--primary-text-color, #1f2933); }
      .chc-forecast-assumptions { margin: 8px 0 0; padding-left: 18px; color: var(--secondary-text-color, #667085); font-size: 11px; line-height: 1.45; }
      .chc-metric {
        min-width: 0;
        border: 1px solid var(--divider-color, rgba(127, 127, 127, 0.22));
        border-radius: 8px;
        padding: 10px;
      }
      .chc-metric-value {
        margin-top: 4px;
        font-size: 16px;
        font-weight: 750;
        overflow-wrap: anywhere;
      }
      .chc-number {
        display: grid;
        grid-template-columns: minmax(0, 1fr) auto;
        gap: 8px;
        align-items: center;
        min-width: 0;
        border: 1px solid var(--divider-color, rgba(127, 127, 127, 0.22));
        border-radius: 8px;
        padding: 10px;
      }
      .chc-number.is-disabled {
        opacity: 0.55;
      }
      .chc-number-value {
        margin-top: 3px;
        font-size: 15px;
        font-weight: 750;
        overflow-wrap: anywhere;
      }
      .chc-stepper {
        display: grid;
        grid-template-columns: 28px 28px;
        gap: 4px;
      }
      .chc-stepper button {
        min-height: 28px;
        padding: 0;
      }
      .chc-number-message {
        grid-column: 1 / -1;
        color: var(--secondary-text-color, #667085);
        font-size: 12px;
      }
      .chc-number-message.is-error {
        color: var(--error-color, #b3261e);
      }
      .chc-reason,
      .chc-alert,
      .chc-error {
        margin: 0 18px 18px;
        color: var(--secondary-text-color, #667085);
        font-size: 12px;
        line-height: 1.4;
      }
      .chc-error {
        margin-top: 18px;
        color: var(--error-color, #b3261e);
        font-weight: 700;
      }
      .chc-alert {
        color: var(--warning-color, #b45309);
        font-weight: 700;
      }
      button {
        min-height: 38px;
        border: 1px solid var(--divider-color, rgba(127, 127, 127, 0.28));
        border-radius: 8px;
        background: var(--secondary-background-color, #f7f8fa);
        color: var(--primary-text-color, #1f2933);
        font: inherit;
        font-weight: 700;
        cursor: pointer;
      }
      button.chc-hot {
        background: color-mix(in srgb, var(--warning-color, #f59e0b) 18%, transparent);
        color: var(--primary-text-color, #1f2933);
      }
      button:disabled {
        cursor: not-allowed;
        opacity: 0.45;
      }
      @media (max-width: 360px) {
        .chc-main { grid-template-columns: 1fr; }
        .chc-current { text-align: left; }
        .chc-setpoints { grid-template-columns: 1fr; }
        .chc-stages,
        .chc-visual-grid { grid-template-columns: 1fr; }
      }
      .chc-dashboard { padding-bottom: 12px; }
      .chc-dashboard .chc-header { align-items: center; border: 0; padding: 22px 24px 8px; }
      .chc-dashboard .chc-title { font-size: 23px; font-weight: 650; }
      .chc-activity { color: var(--secondary-text-color, #667085); font-size: 13px; }
      .chc-policy { margin: 0; padding: 4px 24px 14px; color: var(--secondary-text-color, #667085); font-size: 14px; line-height: 1.5; }
      .chc-intro { margin: 4px 24px 12px; font-size: 17px; line-height: 1.5; max-width: 720px; }
      .chc-dashboard .chc-overview-primary { padding: 8px 24px 22px; grid-template-columns: 1fr 1fr; max-width: 650px; }
      .chc-dashboard .chc-current-big { font-size: 44px; font-weight: 650; }
      .chc-dashboard .chc-target { font-size: 36px; font-weight: 550; }
      .chc-dashboard .chc-label { font-size: 13px; }
      .chc-dashboard .chc-simple-forecast { padding: 0 24px 8px; }
      .chc-simple-forecast .chc-section-title { font-size: 14px; font-weight: 600; }
      .chc-dashboard .chc-chart { max-height: 330px; }
      .chc-dashboard .chc-chart-legend { font-size: 12px; gap: 12px; }
      .chc-dashboard .chc-chart-line { stroke-width: 3; }
      .chc-dashboard .chc-chart-line { vector-effect: non-scaling-stroke; stroke-width: 2.5; }
      .chc-dashboard .chc-forecast-note { font-size: 12px; margin-top: 7px; }
      .chc-next { margin: 8px 24px 18px; border-top: 1px solid var(--divider-color, #ddd); padding-top: 16px; }
      .chc-next h3, .chc-accuracy-summary h3 { font-size: 15px; font-weight: 650; margin: 0 0 10px; }
      .chc-next ul { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; margin: 0; padding: 0; list-style: none; }
      .chc-next li { display: grid; gap: 5px; font-size: 14px; line-height: 1.5; }
      .chc-next li strong { font-weight: 600; }
      .chc-next li span { color: var(--secondary-text-color, #667085); font-size: 13px; }
      .chc-quick-controls { display: flex; align-items: end; gap: 12px; flex-wrap: wrap; padding: 0 24px 12px; }
      .chc-quick-controls label { display: grid; gap: 5px; font-size: 12px; color: var(--secondary-text-color, #667085); }
      .chc-quick-controls select, .chc-quick-controls button, .chc-timetable button, .chc-schedule-footer button { min-height: 44px; padding: 9px 14px; border: 1px solid var(--divider-color, #bbb); border-radius: 8px; color: var(--primary-text-color, #1f2933); background: var(--card-background-color, #fff); font: inherit; cursor: pointer; }
      .chc-quick-controls select { min-width: 150px; font-size: 15px; }
      .chc-quick-controls button { font-size: 14px; }
      .chc-quick-message { width: 100%; font-size: 13px; }
      .chc-quick-message:empty { display: none; }
      .chc-now-links { display: flex; flex-wrap: wrap; gap: 8px 22px; margin: 0 24px; align-items: center; }
      .chc-text-link, .chc-link-button { color: var(--primary-color, #007ba8); font-size: 14px; text-decoration: none; line-height: 1.5; }
      .chc-link-button { background: none; border: 0; padding: 10px 0; cursor: pointer; font-family: inherit; min-height: 44px; }
      .chc-text-link:hover, .chc-link-button:hover { text-decoration: underline; }
      .chc-progress-link { margin: 10px 24px 16px; padding-top: 12px; border-top: 1px solid var(--divider-color, #ddd); }
      .chc-small-note { margin: 0 24px 15px; color: var(--secondary-text-color, #667085); font-size: 12px; line-height: 1.5; }
      .chc-disclosure { margin: 0 24px 12px; border-top: 1px solid var(--divider-color, #ddd); }
      .chc-disclosure > summary { padding: 13px 0; cursor: pointer; font-size: 14px; font-weight: 600; min-height: 44px; }
      .chc-disclosure-body { padding-bottom: 12px; }
      .chc-disclosure-body > p, .chc-night-fields > p { margin: 0 0 12px; font-size: 13px; line-height: 1.5; color: var(--secondary-text-color, #667085); }
      .chc-disclosure-body .chc-cost-grid { padding: 0; }
      .chc-disclosure .chc-readiness, .chc-disclosure .chc-learning-grid { margin-inline: 0; }
      .chc-dashboard-schedule .chc-setpoints { padding: 5px 24px 10px; max-width: 650px; }
      .chc-dashboard .chc-stepper button { width: 44px; height: 44px; min-height: 44px; font-size: 20px; }
      .chc-dashboard .chc-number { background: transparent; }
      .chc-dashboard .chc-number-value { font-size: 24px; }
      .chc-dashboard .chc-schedule-editor { margin: 0 24px 20px; border: 0; border-top: 1px solid var(--divider-color, #ddd); border-radius: 0; }
      .chc-dashboard .chc-schedule-editor > summary { padding: 17px 0; font-size: 17px; font-weight: 600; }
      .chc-dashboard .chc-schedule-editor form { display: block; padding: 0; }
      .chc-source-choice { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; border: 0; margin: 0 0 15px; padding: 0; }
      .chc-source-choice legend { position: absolute; width: 1px; height: 1px; overflow: hidden; clip-path: inset(50%); }
      .chc-source-choice label { display: flex; align-items: center; gap: 9px; padding: 12px; min-height: 48px; border: 1px solid var(--divider-color, #bbb); border-radius: 8px; font-size: 14px; cursor: pointer; }
      .chc-source-choice label:has(input:checked) { border-color: var(--primary-color, #007ba8); background: color-mix(in srgb, var(--primary-color, #007ba8) 6%, transparent); }
      .chc-source-choice input { accent-color: var(--primary-color, #007ba8); width: 18px; height: 18px; margin: 0; flex: 0 0 auto; }
      .chc-timetable > p { font-size: 14px; line-height: 1.5; margin: 0 0 14px; }
      .chc-inherited-week { display: grid; gap: 0; margin: 0 0 12px; }
      .chc-inherited-week > div { display: grid; grid-template-columns: 105px 1fr; gap: 10px; padding: 9px 0; border-bottom: 1px solid var(--divider-color, #ddd); font-size: 14px; line-height: 1.5; }
      .chc-inherited-week strong { font-weight: 550; }
      .chc-inherited-week span { color: var(--secondary-text-color, #667085); }
      .chc-schedule-day { border: 0; border-bottom: 1px solid var(--divider-color, #ddd); border-radius: 0; padding: 0; }
      .chc-schedule-day > summary { display: grid; grid-template-columns: 105px 1fr auto; gap: 10px; align-items: center; min-height: 48px; padding: 10px 0; font-size: 14px; cursor: pointer; }
      .chc-schedule-day > summary strong { font-weight: 550; }
      .chc-schedule-day > summary span { color: var(--secondary-text-color, #667085); }
      .chc-schedule-day > summary .chc-day-edit { color: var(--primary-color, #007ba8); font-size: 12px; }
      .chc-day-fields { padding: 8px 0 14px; }
      .chc-period input { min-height: 44px; font-size: 15px; }
      .chc-period button, .chc-add { min-height: 44px; }
      .chc-schedule-editor .chc-disclosure { margin: 16px 0 0; }
      .chc-summary-value { display: block; padding: 5px 0 0 15px; color: var(--secondary-text-color, #667085); font-size: 12px; font-weight: 400; }
      .chc-night-fields .chc-period { grid-template-columns: 1fr 1fr; }
      .chc-schedule-footer { display: flex; align-items: center; flex-wrap: wrap; gap: 12px; margin: 18px 0 10px; }
      .chc-schedule-footer button { background: var(--primary-color, #007ba8); color: white; border: 0; font-weight: 600; }
      .chc-schedule-footer span { color: var(--secondary-text-color, #667085); font-size: 13px; }
      .chc-schedule-hint { font-size: 12px; line-height: 1.5; }
      .chc-learning-summary { padding: 4px 24px 12px; }
      .chc-learning-summary h2 { font-size: 20px; font-weight: 600; margin: 4px 0 12px; }
      .chc-learning-summary h3 { font-size: 15px; margin: 18px 0 8px; }
      .chc-learning-summary p, .chc-learning-summary li { font-size: 14px; line-height: 1.65; }
      .chc-learning-summary p { margin: 0 0 8px; }
      .chc-learning-summary ul { padding-left: 20px; margin: 0 0 4px; }
      .chc-learning-summary li { margin-bottom: 7px; }
      .chc-dashboard-learning .chc-smart-action { margin: 0 24px 22px; }
      .chc-accuracy-summary { margin: 0 24px 20px; }
      .chc-accuracy-summary > p { color: var(--secondary-text-color, #667085); font-size: 13px; line-height: 1.5; }
      .chc-accuracy-summary .chc-learning-grid { padding: 0; grid-template-columns: repeat(3,1fr); }
      .chc-dashboard-learning .chc-metric { padding: 12px; }
      .chc-dashboard-learning .chc-metric-value { font-size: 16px; }
      .chc-empty { margin: 18px 24px; color: var(--secondary-text-color, #667085); line-height: 1.5; }
      .chc-dashboard :is(button,select,input,a,summary):focus-visible { outline: 3px solid var(--primary-color, #007ba8); outline-offset: 3px; }
      @media (min-width: 850px) {
        .chc-dashboard { max-width: 1060px; }
        .chc-dashboard .chc-chart { max-height: 310px; }
        .chc-inherited-week { grid-template-columns: 1fr 1fr; gap: 0 32px; }
      }
      @media (max-width: 550px) {
        .chc-dashboard .chc-header { padding: 18px 18px 8px; }
        .chc-dashboard .chc-title { font-size: 21px; }
        .chc-activity { font-size: 12px; max-width: 130px; text-align: right; }
        .chc-dashboard .chc-policy { padding-inline: 18px; }
        .chc-dashboard .chc-overview-primary { padding: 4px 18px 20px; }
        .chc-dashboard .chc-current-big { font-size: 36px; }
        .chc-dashboard .chc-target { font-size: 30px; }
        .chc-dashboard .chc-simple-forecast { padding-inline: 10px; }
        .chc-dashboard .chc-chart { font-size: 17px; }
        .chc-next { margin: 6px 18px 16px; }
        .chc-next ul { grid-template-columns: 1fr; gap: 12px; }
        .chc-quick-controls { padding-inline: 18px; gap: 9px; }
        .chc-now-links, .chc-progress-link, .chc-disclosure, .chc-small-note, .chc-accuracy-summary { margin-inline: 18px; }
        .chc-intro { margin-inline: 18px; font-size: 16px; }
        .chc-dashboard-schedule .chc-setpoints { padding-inline: 18px; }
        .chc-dashboard .chc-schedule-editor { margin-inline: 18px; }
        .chc-source-choice { grid-template-columns: 1fr; }
        .chc-schedule-day > summary { grid-template-columns: 80px 1fr auto; font-size: 13px; gap: 7px; }
        .chc-schedule-day > summary span { overflow-wrap: anywhere; }
        .chc-learning-summary { padding-inline: 18px; }
        .chc-dashboard-learning .chc-smart-action { margin-inline: 18px; }
        .chc-accuracy-summary .chc-learning-grid { grid-template-columns: 1fr; }
      }
    `;
  }

  _escape(value) {
    return String(value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }
}

if (!customElements.get(CARD_NAME)) {
  customElements.define(CARD_NAME, CentralHeatingControllerCard);
}

window.customCards = window.customCards || [];
window.customCards.push({
  type: CARD_NAME,
  name: "Central Heating Controller Card",
  description: "Control and visualize the Central Heating Controller integration.",
});


class CentralHeatingForecastCard extends CentralHeatingControllerCard {
  getCardSize() { return 4; }

  getGridOptions() { return { columns: 12, rows: 6, min_columns: 6, min_rows: 5 }; }

  _renderEntityIds() {
    return [this._entities.status, this._entities.indoorTemperatureForecast];
  }

  _renderFormatterInputs() {
    return []; // This card formats its own two temperatures, not entity states.
  }

  _syncChartChildren(current, incoming) {
    // Keep the SVG and its layers/paths mounted. Only changed attributes, text
    // and added/removed children are patched when new forecast data arrives.
    for (let index = 0; index < incoming.childNodes.length; index++) {
      const next = incoming.childNodes[index];
      const prior = current.childNodes[index];
      if (!prior) {
        current.appendChild(next.cloneNode(true));
      } else if (prior.nodeType !== next.nodeType || prior.nodeName !== next.nodeName
                 || prior.namespaceURI !== next.namespaceURI) {
        current.replaceChild(next.cloneNode(true), prior);
      } else if (next.nodeType === 3 || next.nodeType === 8) {
        if (prior.nodeValue !== next.nodeValue) prior.nodeValue = next.nodeValue;
      } else {
        for (const attribute of [...prior.attributes]) {
          if (!next.hasAttribute(attribute.name)) prior.removeAttribute(attribute.name);
        }
        for (const attribute of next.attributes) {
          if (prior.getAttribute(attribute.name) !== attribute.value) {
            prior.setAttribute(attribute.name, attribute.value);
          }
        }
        this._syncChartChildren(prior, next);
      }
    }
    while (current.childNodes.length > incoming.childNodes.length) {
      current.removeChild(current.lastChild);
    }
  }

  _render() {
    if (!this.shadowRoot || !this._config) return;
    const status = this._statusState();
    const forecast = this._state(this._entities.indoorTemperatureForecast);
    const attrs = forecast?.attributes || {};
    const unit = attrs.temperature_unit || "°C";
    const available = status && !["unknown", "unavailable"].includes(status.state);
    const current = available ? this._finiteNumber(status.attributes?.current_temperature) : null;
    const target = available ? this._finiteNumber(status.attributes?.effective_target_temperature) : null;
    const display = value => value === null ? "—" : this._formatNumber(value, unit);
    const chart = this._renderForecast(true);
    const caption = attrs.confidence === "learning" ? "Early estimate" : "Estimated heating times";
    if (this.shadowRoot.querySelector(".chc-kiosk")) {
      for (const [selector, value] of [
        [".chc-kiosk-now", display(current)],
        [".chc-kiosk-target-value", display(target)],
        [".chc-kiosk-caption", caption],
      ]) {
        const element = this.shadowRoot.querySelector(selector);
        if (element.textContent !== value) element.textContent = value;
      }
      if (chart !== this._kioskForecastMarkup) {
        const template = document.createElement("template");
        template.innerHTML = chart;
        this._syncChartChildren(
          this.shadowRoot.querySelector(".chc-kiosk-forecast"), template.content,
        );
        this._kioskForecastMarkup = chart;
      }
      return;
    }
    this.shadowRoot.innerHTML = `<style>${this._styles()}
      .chc-kiosk { padding: 18px 20px 14px; overflow: hidden; }
      .chc-kiosk-header { display: flex; justify-content: space-between; gap: 16px; align-items: baseline; margin-bottom: 12px; }
      .chc-kiosk-now { font-size: clamp(32px, 4vw, 46px); font-weight: 700; line-height: 1.1; white-space: nowrap; }
      .chc-kiosk-target { font-size: 20px; white-space: nowrap; }
      .chc-kiosk-label { font-size: 14px; color: var(--secondary-text-color); margin-right: 8px; }
      .chc-kiosk .chc-chart { height: clamp(180px, 25vw, 260px); font-size: 12px; }
      .chc-kiosk .chc-chart-indoor { stroke-width: 4; }
      .chc-kiosk .chc-chart-target { stroke: var(--secondary-text-color, #667085); stroke-width: 2; }
      .chc-heating-band { fill: #ed962a; opacity: .19; }
      .chc-kiosk .chc-chart-legend { font-size: 13px; gap: 10px 18px; }
      .chc-kiosk .chc-legend-target { border-color: var(--secondary-text-color, #667085); }
      .chc-kiosk .chc-legend-heating { width: 14px; height: 10px; border: 0; background: #ed962a; opacity: .5; border-radius: 2px; }
      .chc-kiosk-footer { display: flex; justify-content: space-between; gap: 10px; font-size: 12px; color: var(--secondary-text-color); margin-top: 10px; }
      .chc-kiosk-empty { min-height: 180px; display: grid; place-items: center; color: var(--secondary-text-color); }
      @media(max-width: 400px) { .chc-kiosk { padding: 14px 10px; } .chc-kiosk-target { font-size: 17px; } }
    </style><ha-card class="chc-kiosk" aria-label="House temperature forecast">
      <div class="chc-kiosk-header"><div><span class="chc-kiosk-label">Now</span><strong class="chc-kiosk-now">${this._escape(display(current))}</strong></div>
      <div class="chc-kiosk-target"><span class="chc-kiosk-label">Target</span><span class="chc-kiosk-target-value">${this._escape(display(target))}</span></div></div>
      <div class="chc-kiosk-forecast">${chart}</div>
      <div class="chc-kiosk-footer"><span>Next 24 hours</span><span class="chc-kiosk-caption">${caption}</span></div>
    </ha-card>`;
    this._kioskForecastMarkup = chart;
  }
}

if (!customElements.get("central-heating-forecast-card")) {
  customElements.define("central-heating-forecast-card", CentralHeatingForecastCard);
}
window.customCards.push({
  type: "central-heating-forecast-card",
  name: "House Temperature Forecast",
  description: "A compact 24-hour temperature chart with expected heating periods.",
});
