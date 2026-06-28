import json
from pathlib import Path


def test_manifest_declares_copy_ready_config_flow() -> None:
    manifest = json.loads(
        Path("custom_components/central_heating_controller/manifest.json").read_text()
    )
    assert manifest["domain"] == "central_heating_controller"
    assert manifest["name"] == "Central Heating Controller"
    assert manifest["config_flow"] is True
    assert manifest["version"] == "0.1.0"
    assert manifest["iot_class"] == "local_push"
