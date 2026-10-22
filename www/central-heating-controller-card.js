const CARD_NAME = "central-heating-controller-card";

const ENTITY_SUFFIXES = {
  autoMode: ["switch", "auto_mode", "auto_mode"],
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
    return Object.values(this._entities);
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

  async _pressHeatBlast() {
    await this._callService("button", "press", { entity_id: this._entities.heatBlast });
  }

  async _adjustNumber(entityId, direction) {
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

    await this._callService("number", "set_value", { entity_id: entityId, value });
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

    this._renderControl();
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

  _renderForecast(compact = false) {
    const state = this._state(this._entities.indoorTemperatureForecast);
    const key = this._dateFormatters().key;
    if (this._forecastCache?.state === state && this._forecastCache.compact === compact
        && this._forecastCache.formatKey === key) return this._forecastCache.markup;
    const markup = this._buildForecast(compact);
    this._forecastCache = { state, compact, formatKey: key, markup };
    return markup;
  }

  _buildForecast(compact = false) {
    const state = this._state(this._entities.indoorTemperatureForecast);
    const attrs = state?.attributes || {};
    const rows = Array.isArray(attrs.forecast) ? attrs.forecast
      .filter((point) => point && typeof point === "object")
      .map((point) => ({
        time: new Date(point.datetime).getTime(),
        temperature: this._finiteNumber(point.temperature),
        target: this._finiteNumber(point.target_temperature),
        demand: this._finiteNumber(point.heating_demand_percent),
      }))
      .filter((point) => Number.isFinite(point.time) && point.temperature !== null)
      .sort((a, b) => a.time - b.time) : [];
    const unit = attrs.temperature_unit || "°C";
    const confidence = attrs.confidence;
    const reason = attrs.reason;
    let content;

    if (rows.length) {
      const width = 600, height = 220, left = 48, right = 12, top = 12, bottom = 34;
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
      const heatingBands = compact ? rows.slice(0, -1).map((point, index) => {
        const next = rows[index + 1];
        // Demand describes the interval beginning at this point; do not bridge gaps.
        if (point.target === null || point.demand === null || point.demand <= 5 || point.demand > 100
            || next.time <= point.time || next.time - point.time > 10 * 60 * 1000) return "";
        return `<rect class="chc-heating-band" x="${x(point.time)}" y="${top}" width="${x(next.time) - x(point.time)}" height="${height-top-bottom}"/>`;
      }).join("") : "";
      const first = formatAxisTime(tMin);
      const last = formatAxisTime(tMax);
      const timeTicks = [];
      const interval = 6 * 60 * 60 * 1000;
      const axisTick = (time, includeDay = false) => {
        const date = new Date(time);
        const formats = this._dateFormatters();
        const label = formats.time.format(date);
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
          <title id="chc-chart-title">Indoor temperature forecast for the next 24 hours</title>
          <desc id="chc-chart-desc">Predicted indoor temperature and target temperature, in ${this._escape(unit)}, from ${this._escape(first)} to ${this._escape(last)} local time.</desc>
          <g class="chc-chart-bands">${heatingBands}</g><g class="chc-chart-temperature-ticks">${ticks}</g><g class="chc-chart-time-ticks">${timeTicks.join("")}</g>
          <path d="${pathFor("temperature")}" class="chc-chart-line chc-chart-indoor"/><path d="${pathFor("target", true)}" class="chc-chart-line chc-chart-target"/>
        </svg><div class="chc-chart-legend"><span><i class="chc-legend-indoor"></i>${compact ? "Forecast" : "Indoor forecast"}</span><span><i class="chc-legend-target"></i>Target</span>${compact ? '<span><i class="chc-legend-heating"></i>Heating expected</span>' : ""}</div>
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
    const disabled = unavailable ? "disabled" : "";

    return `
      <div class="chc-number ${unavailable ? "is-disabled" : ""}">
        <div>
          <div class="chc-label">${this._escape(label)}</div>
          <div class="chc-number-value">${this._escape(value)}</div>
        </div>
        <div class="chc-stepper">
          <button type="button" data-action="decrement:${this._escape(entityId)}" ${disabled}>-</button>
          <button type="button" data-action="increment:${this._escape(entityId)}" ${disabled}>+</button>
        </div>
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
        } else if (action?.startsWith("increment:")) {
          this._adjustNumber(action.slice("increment:".length), 1);
        } else if (action?.startsWith("decrement:")) {
          this._adjustNumber(action.slice("decrement:".length), -1);
        }
      });
    }
  }

  _styles() {
    return `
      :host { display: block; }
      .chc-card {
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
