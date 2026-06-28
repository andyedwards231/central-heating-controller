"""Repairs support for missing controller inputs."""

from collections.abc import Collection

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

from .const import (
    CONF_ARRIVAL_TIME,
    CONF_CLIMATE,
    CONF_DESTINATION,
    CONF_HOME_ZONE,
    CONF_PERSONS,
    CONF_SCHEDULE,
    DOMAIN,
)

_INPUT_LABELS = {
    CONF_CLIMATE: "thermostat",
    CONF_PERSONS: "people",
    CONF_HOME_ZONE: "home zone",
    CONF_SCHEDULE: "heating schedule",
    CONF_DESTINATION: "destination",
    CONF_ARRIVAL_TIME: "arrival time",
}


def delete_entry_issues(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Delete every repair issue owned by a config entry."""
    for config_key in _INPUT_LABELS:
        ir.async_delete_issue(hass, DOMAIN, f"{entry.entry_id}_{config_key}")


def sync_missing_entity_issues(
    hass: HomeAssistant,
    entry: ConfigEntry,
    missing_keys: Collection[str],
) -> None:
    """Create issues for missing configured inputs and delete resolved issues."""
    missing = set(missing_keys)
    for config_key, input_label in _INPUT_LABELS.items():
        issue_id = f"{entry.entry_id}_{config_key}"
        if config_key not in missing:
            ir.async_delete_issue(hass, DOMAIN, issue_id)
            continue
        ir.async_create_issue(
            hass,
            DOMAIN,
            issue_id,
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key="missing_entity",
            translation_placeholders={"input": input_label},
        )
