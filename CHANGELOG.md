# Changelog

## 1.2.0 — 2026-10-21

- Learn from percentage heating demand (including Tado Hall Heating Power) without
  treating partial demand as continuously active full heating.
- Learn thermal response time and retain residual heat in temperature forecasts;
  estimate future demand from observed thermostat behaviour.
- Preserve forecast-error history across restarts and score fresh temperature
  reports, with MAE, bias and RMSE for 1-, 6- and 24-hour predictions.

- Add a 24-hour indoor temperature forecast using hourly weather, live outdoor
  temperature and the heating schedule, with persisted heating and cooling learning.
- Add an optional actual-heating sensor for thermostats without HVAC activity.
- Plot indoor predictions and targets in the control and visual Lovelace cards,
  distinguishing uncalibrated estimates and showing measured forecast errors.
- Respect occupancy, known arrivals, overrides, blast expiry and temperature units;
  keep forecast failures separate from heating control.
- Reject long telemetry gaps in the existing heating-rate learner.

After updating through HACS, restart Home Assistant and configure the weather,
outdoor temperature and heating activity/demand inputs. For the Hall Tado setup,
select `sensor.hall_heating_power` as the activity/demand source. The initial
forecast is uncalibrated until sufficient heating and cooling data is collected.

Also replace `/config/www/central-heating-controller-card.js` with the JavaScript
asset from this release and use `/local/central-heating-controller-card.js?v=1.2.0`
as the dashboard resource URL. HACS integration updates do not copy this optional
card into `/config/www` automatically.

## 1.1.0 — 2026-10-03

Requires Home Assistant Core 2026.9.3 or newer.

- Configured ETA sensors now require a valid future timestamp before preheating.
  Invalid, unavailable, unknown, blank, expired, or missing ETA values block it.
  Leaving the ETA sensor unconfigured still allows immediate preheating for a
  home destination.
- Prevent thermostat setpoints restored by our HVAC-mode command from becoming
  unintended manual overrides; re-read the target after mode changes.
- Retain command provenance through intermediate device reports and suppress
  repeated identical writes during the ten-second acknowledgement window.
- Reset the current heating-rate sample when the thermostat becomes unavailable.
- Update the Auto switch correctly while the thermostat is unavailable.
- Align new entity IDs with the documented dashboard configuration. The card
  also recognizes the older warm-up and preheat entity IDs.
- Accept fractional Unix timestamps in ETA sensor states.
- Add regression coverage, a test using real Home Assistant climate services,
  pinned Core 2026.9.3 test dependencies, and automated Python, JavaScript,
  hassfest, and HACS checks.

For Tado X, select its Matter climate entity and the `heat` mode. Adaptive learning
requires the entity to report `hvac_action: heating`; otherwise fallback timing
continues to work. Testing uses a simulated thermostat, not physical Tado hardware.

After updating in HACS, restart Home Assistant. If using the optional Lovelace
card, also copy the updated `www/central-heating-controller-card.js` to your
configuration's `www` directory and update its resource URL to
`/local/central-heating-controller-card.js?v=1.1.0`.
