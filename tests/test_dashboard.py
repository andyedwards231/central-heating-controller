from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
DASHBOARD = ROOT / "dashboards" / "heating.yaml"


def test_heating_dashboard_has_three_single_card_panel_views():
    config = yaml.safe_load(DASHBOARD.read_text())

    assert [view["path"] for view in config["views"]] == [
        "overview",
        "schedule",
        "learning",
    ]
    assert [view["icon"] for view in config["views"]] == [
        "mdi:radiator",
        "mdi:calendar-clock",
        "mdi:chart-line",
    ]
    assert all(view["type"] == "panel" for view in config["views"])
    assert all(len(view["cards"]) == 1 for view in config["views"])
    assert [view["cards"][0]["mode"] for view in config["views"]] == [
        "overview",
        "schedule",
        "learning",
    ]
    assert all(
        view["cards"][0]["type"] == "custom:central-heating-controller-card"
        and view["cards"][0]["entity"] == "sensor.central_heating_controller_status"
        for view in config["views"]
    )


def test_card_resource_is_registered_outside_dashboard_yaml_once():
    config = yaml.safe_load(DASHBOARD.read_text())

    # Lovelace resources are managed in Settings → Dashboards → Resources.
    # Keeping them out of this per-dashboard YAML avoids duplicate registrations.
    assert "resources" not in config
    assert DASHBOARD.read_text().count("custom:central-heating-controller-card") == 3
