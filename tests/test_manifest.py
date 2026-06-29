import json
from pathlib import Path


INTEGRATION_DIR = Path("custom_components/central_heating_controller")


def test_manifest_declares_copy_ready_config_flow() -> None:
    manifest = json.loads((INTEGRATION_DIR / "manifest.json").read_text())
    assert manifest["domain"] == "central_heating_controller"
    assert manifest["name"] == "Central Heating Controller"
    assert manifest["config_flow"] is True
    assert manifest["version"] == "0.1.0"
    assert manifest["iot_class"] == "local_push"
    assert manifest["integration_type"] == "service"
    assert manifest["requirements"] == []


def test_localization_files_are_complete_and_equal() -> None:
    strings = json.loads((INTEGRATION_DIR / "strings.json").read_text())
    english = json.loads((INTEGRATION_DIR / "translations/en.json").read_text())

    assert strings == english


def test_readme_documents_copy_ready_operation_and_safety() -> None:
    readme = Path("README.md").read_text()
    normalized = readme.casefold()

    for heading in (
        "Installation",
        "Schedule Helper",
        "Setup",
        "Statuses",
        "Heat Blast",
        "Manual Override",
        "Adaptive Pre-heating",
        "Troubleshooting",
        "Removal",
    ):
        assert f"## {heading}".casefold() in normalized

    for required_text in (
        "Copy `custom_components/central_heating_controller` into `<config>/custom_components/`.",
        "Restart Home Assistant.",
        "Settings → Devices & services → Add integration",
        "Search for Central Heating Controller.",
        "On = high",
        "Off = low",
        "climate_entity",
        "person_entities",
        "home_zone_entity",
        "schedule_entity",
        "destination_entity",
        "arrival_time_entity",
        "active_hvac_mode",
        "high_temperature",
        "low_temperature",
        "eco_temperature",
        "fallback_warmup_minutes",
        "maximum_warmup_minutes",
        "destination_home_value",
        "high",
        "low",
        "pre_heating",
        "away",
        "off",
        "heat_blast",
        "manual_override",
        "unavailable",
        "switch.central_heating_controller_auto_mode",
        "button.central_heating_controller_heat_blast",
        "sensor.central_heating_controller_status",
        "sensor.central_heating_controller_effective_target_temperature",
        "sensor.central_heating_controller_learned_heating_rate",
        "sensor.central_heating_controller_preheat_start_time",
        "destination matching",
        "configured-removed ETA disables preheat",
        "unconfigured or existing invalid ETA starts preheat immediately",
        "three-sample learning period",
        "reset learning",
        "destination changes",
        "manual override clears",
        "unavailable fallbacks",
        "logs",
        "diagnostics",
        "repairs",
        "safe removal",
        "does not replace frost protection or manufacturer safety systems",
    ):
        assert required_text.casefold() in normalized
