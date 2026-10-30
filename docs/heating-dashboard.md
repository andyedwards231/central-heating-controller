# Heating dashboard

The Heating dashboard has three views:

- **Now** shows the indoor temperature, current target and expected temperature over
  the next 24 hours. The dashed line is the target; shading marks estimated heating
  periods. The next expected target change and heating period appear below it.
- **Comfort times** lets you choose when you want warmth and the comfort temperature.
- **Learning** explains what is still needed before automatic timing can take control.
  Detailed temperature measurements and safety checks are collapsed by default.
  **Optional gas cost learning** has its own status; unreliable or missing cost
  estimates do not prevent automatic timing from becoming ready.

## Do I need to enter comfort times?

**You choose when you want the house warm. The integration learns how early to
start heating; it does not guess your daily routine.**

Choose **Use my existing schedule** to reuse the On periods in your configured
Home Assistant Schedule helper. Its weekly timetable is displayed, and **Open
existing schedule** opens that helper. There is no need to enter the times twice.
This is the default when no custom comfort periods have been saved.

Alternatively, choose **Set comfort times here**, expand a day and add periods.
For example, 07:00–09:00 means you want warmth from 07:00, so automatic timing may
start heating before 07:00. Once any custom period is saved, these periods replace
the inherited timetable for the whole week; an empty day has no comfort period.
The editor prevents an empty custom timetable from silently reverting to the
existing schedule. Choose the existing-schedule option explicitly to switch back.

Night temperature, night times and the maximum daytime drop are under **Night and
daytime allowance**. Automatic timing can choose a smaller daytime drop when that
uses less heating overall. Temperatures can be adjusted in half-degree steps when
the thermostat supports them.

## What controls heating before it is ready?

Your existing schedule continues to control heating while automatic timing is in
preview. New comfort periods and night settings prepare the future plan; saving
them does not enable it. **The comfort temperature is shared with the current
schedule**, so changing it also changes that schedule's warm temperature.

The graph on **Now** always follows the policy actually selected for heating.
The proposed automatic plan is separately available under **See the automatic
timing preview** in Comfort times. It is never presented as the live schedule.
Heating shading and times are estimates of thermostat demand, not measurements of
whether the boiler is firing. A reported idle/heat-request status appears at the
top. **Past temperatures** opens the thermostat's Home Assistant history.

When the checks pass, open **Learning** and select **Enable automatic timing**.
It never enables itself for the first time. **Use existing schedule instead**
removes that opt-in. Auto/Away/Off, manual changes and boost retain priority.
See [readiness and fallback behaviour](../README.md#readiness-and-opt-in-live-control)
for the safety requirements. The kiosk forecast card remains supported.

## Install the card resource

HACS installs the integration only. The dashboard card JavaScript is a separate
manual installation:

1. Copy `www/central-heating-controller-card.js` to
   `/config/www/central-heating-controller-card.js`.
2. In Home Assistant, open **Settings → Dashboards → Resources**, add
   `/local/central-heating-controller-card.js?v=1.9.0` as a **JavaScript module**, and save.
   Add this resource once; the three views share it. If it is already registered
   for another controller card or kiosk dashboard, edit that single registration to use `?v=1.9.0`.
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

The inherited timetable display and history shortcut require integration v1.8.0
or newer. Update through HACS and restart Home Assistant to load that metadata.
The redesigned card handles older versions by showing an explicit timetable-unavailable
message. No heating policy or learning data is changed by this dashboard update.

If your Heating dashboard uses a different URL, set `dashboard_path` on the
Overview and Comfort times cards, for example `dashboard_path: /my-heating`.
Existing `overview`, `schedule` and `learning` mode names and route paths remain
compatible even though the visible tabs are now Now, Comfort times and Learning.
