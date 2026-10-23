# Heating dashboard

The optional Heating dashboard provides three full-width views: **Overview** for
current control, **Schedule** for the smart schedule preview and its editor, and
**Learning** for the learning and forecast details. It uses the existing
`custom:central-heating-controller-card` with `sensor.central_heating_controller_status`.

The schedule starts in preview: the existing Home Assistant schedule controls the
thermostat while the proposal learns. Overview shows readiness; Schedule explains
remaining checks and offers **Enable intelligent heating** only after they pass.
Learning shows the full evidence. The opt-in defaults to off and can always be
removed using **Return to existing schedule**. Auto/Away/Off and overrides remain
live controls. See [readiness and fallback behaviour](../README.md#readiness-and-opt-in-live-control)
for exact thresholds and what happens before activation.

## Install the card resource

HACS installs the integration only. The dashboard card JavaScript is a separate
manual installation:

1. Copy `www/central-heating-controller-card.js` to
   `/config/www/central-heating-controller-card.js`.
2. In Home Assistant, open **Settings → Dashboards → Resources**, add
   `/local/central-heating-controller-card.js?v=1.7.0` as a **JavaScript module**, and save.
   Add this resource once; the three views share it. If it is already registered
   for another controller card or kiosk dashboard, edit that single registration to use `?v=1.7.0`.
3. Refresh the browser after copying or updating the file.

The kiosk dashboard remains unchanged. Its existing forecast card can use the same
JavaScript resource.

## Add the dashboard

Create a new dashboard in Home Assistant and choose **Start with an empty dashboard** (or create a dashboard that accepts YAML), then open its **Raw
configuration editor**. Paste the contents of [`dashboards/heating.yaml`](../dashboards/heating.yaml)
and save. This is a manual dashboard configuration; it is not registered in the
running Home Assistant instance by installing this repository.

Alternatively, register the YAML file in `<config>/configuration.yaml` to show it
in the sidebar:

```yaml
lovelace:
  dashboards:
    heating-dashboard:
      mode: yaml
      title: Heating
      icon: mdi:radiator
      show_in_sidebar: true
      filename: dashboards/heating.yaml
```

Merge this under any existing `lovelace:` key rather than creating a second one.
Restart Home Assistant after changing `configuration.yaml`. With either setup,
ensure the card resource above is registered once.

The preview and controller mode entities require integration v1.5.0 or newer.
Upgrade through HACS and restart Home Assistant if those entities are missing.

Readiness and guarded live-control entities require integration v1.7.0 or newer.
Upgrade through HACS and restart Home Assistant; the update leaves live opt-in off.
