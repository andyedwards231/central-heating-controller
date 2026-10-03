"""Exercise real Home Assistant climate services with delayed device reports."""

from unittest.mock import AsyncMock, patch

from homeassistant.components.climate import (
    DATA_COMPONENT,
    ClimateEntity,
    ClimateEntityFeature,
    HVACMode,
)
from homeassistant.const import UnitOfTemperature
from homeassistant.core import Context
from homeassistant.setup import async_setup_component

from custom_components.central_heating_controller.models import ControllerStatus, PersistentState
from tests.test_entities import _entry, _set_states


class ReportingThermostat(ClimateEntity):
    """Simulate Matter-style reports while using HA's real service validation."""

    _attr_name = "Hallway"
    _attr_should_poll = False
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_hvac_modes = [HVACMode.OFF, HVACMode.HEAT]
    _attr_hvac_mode = HVACMode.OFF
    _attr_supported_features = ClimateEntityFeature.TARGET_TEMPERATURE
    _attr_min_temp = 5.0
    _attr_max_temp = 25.0
    _attr_target_temperature = 17.0
    _attr_current_temperature = 16.0

    def __init__(self):
        self.entity_id = "climate.hallway"
        self.commands = []

    async def async_set_hvac_mode(self, hvac_mode):
        self.commands.append(("mode", hvac_mode))
        self._attr_hvac_mode = hvac_mode
        self._attr_target_temperature = 22.0
        self.async_write_ha_state()

    async def async_set_temperature(self, **kwargs):
        # Accept the write; the target arrives in a later device report.
        self.commands.append(("target", kwargs["temperature"]))

    def report_target(self, value):
        self.async_set_context(Context())
        self._attr_target_temperature = value
        self.async_write_ha_state()


async def test_real_climate_services_with_delayed_reports_and_manual_override(hass) -> None:
    """Validate startup, acknowledgement, manual control, schedule and strict off."""
    _set_states(hass, include_climate=False)
    assert await async_setup_component(hass, "climate", {})
    thermostat = ReportingThermostat()
    await hass.data[DATA_COMPONENT].async_add_entities([thermostat])
    entry = _entry()
    entry.add_to_hass(hass)

    with (
        patch(
            "custom_components.central_heating_controller.storage.ControllerStore.async_load",
            AsyncMock(return_value=PersistentState()),
        ),
        patch(
            "custom_components.central_heating_controller.storage.ControllerStore.async_save",
            AsyncMock(),
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        coordinator = entry.runtime_data.coordinator
        assert thermostat.commands == [("mode", HVACMode.HEAT), ("target", 17.0)]
        assert coordinator.pending_target == 17.0
        assert coordinator.persistent_state.manual_override_target is None

        thermostat.report_target(17.0)
        await hass.async_block_till_done()
        assert coordinator.pending_target is None
        assert coordinator.data.status is ControllerStatus.LOW

        thermostat.report_target(18.5)
        await hass.async_block_till_done()
        assert coordinator.data.status is ControllerStatus.MANUAL_OVERRIDE
        assert coordinator.persistent_state.manual_override_target == 18.5

        hass.states.async_set("schedule.heating", "on")
        await hass.async_block_till_done()
        assert coordinator.data.status is ControllerStatus.HIGH
        assert thermostat.commands[-1] == ("target", 20.0)
        thermostat.report_target(20.0)
        await hass.async_block_till_done()

        await coordinator.async_set_auto_mode(False)
        await hass.async_block_till_done()
        assert thermostat.hvac_mode == HVACMode.OFF
        assert thermostat.commands[-1] == ("mode", HVACMode.OFF)
        assert coordinator.data.status is ControllerStatus.OFF
        assert await hass.config_entries.async_unload(entry.entry_id)
