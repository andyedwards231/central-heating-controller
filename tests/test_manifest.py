import json
from pathlib import Path


INTEGRATION_DIR = Path("custom_components/central_heating_controller")
BRAND_DIR = INTEGRATION_DIR / "brand"


def test_manifest_declares_copy_ready_config_flow() -> None:
    manifest = json.loads((INTEGRATION_DIR / "manifest.json").read_text())
    assert manifest["domain"] == "central_heating_controller"
    assert manifest["name"] == "Central Heating Controller"
    assert manifest["config_flow"] is True
    assert manifest["documentation"] == (
        "https://github.com/andyedwards231/central-heating-controller"
    )
    assert manifest["issue_tracker"] == (
        "https://github.com/andyedwards231/central-heating-controller/issues"
    )
    assert manifest["codeowners"] == ["@andyedwards231"]
    assert manifest["version"] == "0.1.1"
    assert manifest["iot_class"] == "local_push"
    assert manifest["integration_type"] == "service"
    assert manifest["requirements"] == []


def test_hacs_manifest_declares_display_name() -> None:
    hacs_manifest = json.loads(Path("hacs.json").read_text())
    assert hacs_manifest["name"] == "Central Heating Controller"


def test_brand_assets_are_valid_pngs_with_expected_dimensions() -> None:
    expected = {
        "icon.png": (256, 256),
        "icon@2x.png": (512, 512),
        "logo.png": (768, 256),
        "logo@2x.png": (1536, 512),
    }

    for filename, dimensions in expected.items():
        data = (BRAND_DIR / filename).read_bytes()
        assert data.startswith(b"\x89PNG\r\n\x1a\n")
        assert _png_size_and_color_type(data) == (*dimensions, 6)


def _png_size_and_color_type(data: bytes) -> tuple[int, int, int]:
    assert data[12:16] == b"IHDR"
    width = int.from_bytes(data[16:20], "big")
    height = int.from_bytes(data[20:24], "big")
    color_type = data[25]
    return width, height, color_type


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
        "configured ETA entity that is missing disables preheat",
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
