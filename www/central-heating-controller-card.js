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
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    this._render();
  }

  get hass() {
    return this._hass;
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
      entities[property] = overrides[overrideKey] || `${entityDomain}.${base}_${suffix}`;
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
        <div class="chc-actions">
          <button type="button" data-action="auto">${this._autoModeLabel()}</button>
          <button type="button" data-action="blast" class="chc-hot">Heat Blast</button>
        </div>
        ${attributes.reason ? `<div class="chc-reason">${this._escape(attributes.reason)}</div>` : ""}
      </ha-card>
    `;
    this._bindActions();
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
    return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
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
