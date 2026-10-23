# Changelog

## 1.4.0 — 2026-10-23

- Shade expected heating periods on the main control and visual cards, retaining
  the rendering and caching improvements from 1.3.1.
- Add optional predicted heating cost today and tomorrow sensors in GBP. Learn
  the demand-linked share of gas consumption from timestamped Octopus half-hour
  readings, using a fitted background allowance for shared-boiler hot water.
- Require varied observations across at least three dates, reject unstable fits,
  and expose calibration diagnostics. These are statistical heating estimates;
  hot-water use correlated with heating cannot be separated reliably.
- Ignore estimated, future and incomplete gas intervals. Persist learning for
  delayed readings, accept corrected intervals, and require full calendar-day
  coverage, including daylight-saving changes. No water meter is used.
- Hold the current GBP/kWh rate constant and exclude standing charges. Missing
  data or insufficient calibration leaves cost unavailable, never a partial total.

After updating the integration, restart Home Assistant and select the optional
gas sources in its configuration. Replace the dashboard JavaScript separately
and use `/local/central-heating-controller-card.js?v=1.4.0`, then fully refresh.


## 1.3.1 — 2026-10-22

- Skip dashboard renders when displayed entities and formatting preferences have
  not changed. The kiosk card tracks its configured status and forecast sources.
- Reuse date formatters and cached forecast markup. Keep the kiosk card, SVG and
  chart paths mounted while updating temperatures, labels and forecast geometry.
- Add update-dependency, formatter-cache and real-browser DOM regression checks.

Replace the optional JavaScript separately and change its resource URL to
`/local/central-heating-controller-card.js?v=1.3.1`, then fully refresh the browser.
HACS does not copy the card into `/config/www`. This patch changes no heating policy
or learned data. The fix removes avoidable rendering; it does not establish the
cause of Android low-memory kills or a measured long-term tablet improvement.

## 1.3.0 — 2026-10-22

- Add a separate compact kiosk forecast card with large current and target
  temperatures, a 24-hour forecast chart, and shaded expected heating periods.
- Keep kiosk wording minimal and chart height bounded; existing control and
  settings cards remain available in the same JavaScript resource.
- Update the optional card JavaScript separately after installing through HACS.

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
