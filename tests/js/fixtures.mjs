export function createHass(overrides = {}) {
  const calls = [];
  const states = {
    "sensor.central_heating_controller_status": {
      entity_id: "sensor.central_heating_controller_status",
      state: "pre_heating",
      attributes: {
        friendly_name: "Status",
        current_temperature: 16.8,
        effective_target_temperature: 20,
        reason: "Pre-heating for the journey home",
        arrival_time: "2026-06-29T18:35:00+01:00",
        preheat_start_time: "2026-06-29T17:35:00+01:00",
      },
    },
    "switch.central_heating_controller_auto_mode": {
      entity_id: "switch.central_heating_controller_auto_mode",
      state: "on",
      attributes: { friendly_name: "Auto mode" },
    },
    "button.central_heating_controller_heat_blast": {
      entity_id: "button.central_heating_controller_heat_blast",
      state: "unknown",
      attributes: { friendly_name: "Heat blast" },
    },
    "number.central_heating_controller_high_temperature": {
      entity_id: "number.central_heating_controller_high_temperature",
      state: "20.0",
      attributes: { min: 7, max: 35, step: 0.5, unit_of_measurement: "degC" },
    },
    "number.central_heating_controller_low_temperature": {
      entity_id: "number.central_heating_controller_low_temperature",
      state: "17.0",
      attributes: { min: 7, max: 35, step: 0.5, unit_of_measurement: "degC" },
    },
    "number.central_heating_controller_eco_temperature": {
      entity_id: "number.central_heating_controller_eco_temperature",
      state: "14.0",
      attributes: { min: 7, max: 35, step: 0.5, unit_of_measurement: "degC" },
    },
    "number.central_heating_controller_fallback_warmup_minutes": {
      entity_id: "number.central_heating_controller_fallback_warmup_minutes",
      state: "60",
      attributes: { min: 5, max: 360, step: 5, unit_of_measurement: "min" },
    },
    "number.central_heating_controller_maximum_warmup_minutes": {
      entity_id: "number.central_heating_controller_maximum_warmup_minutes",
      state: "180",
      attributes: { min: 5, max: 360, step: 5, unit_of_measurement: "min" },
    },
    "sensor.central_heating_controller_effective_target_temperature": {
      entity_id: "sensor.central_heating_controller_effective_target_temperature",
      state: "20.0",
      attributes: { unit_of_measurement: "degC" },
    },
    "sensor.central_heating_controller_learned_heating_rate": {
      entity_id: "sensor.central_heating_controller_learned_heating_rate",
      state: "1.8",
      attributes: { unit_of_measurement: "degC/h" },
    },
    "sensor.central_heating_controller_preheat_start_time": {
      entity_id: "sensor.central_heating_controller_preheat_start_time",
      state: "2026-06-29T17:35:00+01:00",
      attributes: {},
    },
    ...overrides.states,
  };

  return {
    calls,
    states,
    localize: (key) => key,
    formatEntityState: (stateObj) => {
      const unit = stateObj.attributes.unit_of_measurement;
      return unit ? `${stateObj.state} ${unit}` : stateObj.state;
    },
    callService(domain, service, data) {
      calls.push({ domain, service, data });
      return Promise.resolve();
    },
    ...overrides.hass,
  };
}
