# Changelog

## 1.6.0 — 2026-10-23

## Dedicated Heating dashboard

Adds a ready-to-use dashboard with three views:

- **Overview:** current and target temperature, live Auto/Away/Off controls, the next 24 hours with expected heating shaded, estimated heating costs and known arrival timing.
- **Schedule:** the proposed temperature forecast, editable comfort periods, configurable night settings and maximum daytime drop. Clearly marked **Preview only**. Changing the comfort temperature also changes the existing live High setting; schedule edits affect the preview only.
- **Learning:** measured forecast errors at 1, 6 and 24 hours, sample counts, heating response and matched gas readings. Missing data is shown explicitly.

The kiosk card remains supported and its rendering path is unchanged. The dashboard adapts to phone and desktop layouts, retains schedule drafts through sensor updates and ignores unrelated entity updates.

## Install

Update the integration through HACS and restart Home Assistant if upgrading from before v1.5.0. This release does not change the heating policy or enable intelligent schedule control.

Replace `/config/www/central-heating-controller-card.js` with the attached JavaScript. Update the existing resource URL to `/local/central-heating-controller-card.js?v=1.6.0` (JavaScript module), then refresh.

Create an empty **Heating** dashboard and paste the attached `heating.yaml` into its Raw configuration editor. The three views share the existing resource. Your kiosk dashboard can stay as it is.

See [dashboard setup](https://github.com/andyedwards231/central-heating-controller/blob/main/docs/heating-dashboard.md) for UI installation and optional YAML dashboard registration.

Validated with 462 Python tests, 53 JavaScript tests and browser checks at desktop and phone widths.


## 1.5.0 — 2026-10-23

## Smart heating schedule preview

- Adds editable weekly comfort windows, configurable night times (23:00–07:00 by default), a 16°C night minimum and a dynamic daytime allowance up to 2°C.
- Simulates different setbacks and recovery start times using learned heating delay, heat loss, outdoor forecasts and known arrival times. Shows a separate 24-hour preview with expected heating shaded. Preview is enabled by default and **does not apply its targets**: the existing schedule remains in control.
- Adds Auto / Away / Off via `select.central_heating_controller_controller_mode`. These modes take effect immediately. Away holds the Eco temperature until a valid homeward journey or a tracked person returns after being away; someone already home when Away is selected must leave and return. Off turns the climate entity off.
- Adds a schedule editor to the main card. The High setting remains your comfort temperature. With no weekly periods entered, the preview uses your existing Home Assistant schedule.
- Cost optimisation uses predicted demand at a constant gas rate and accounts for estimated remaining recovery at the forecast boundary. It is a bounded search, not a guaranteed financial optimum. Uncalibrated or unreachable plans are labelled. Current heating-cost sensors and accuracy tracking continue to describe the live schedule.

## Install

Update through HACS and restart Home Assistant. Replace `/config/www/central-heating-controller-card.js` with the attached file, set the dashboard resource to `/local/central-heating-controller-card.js?v=1.5.0`, and refresh. Existing card YAML remains valid. Open **Schedule and night settings** on the main card to edit the preview.

Arrival preheating needs the configured homeward destination and a usable ETA. Without an ETA, existing immediate-preheat behaviour remains available, but the preview cannot promise an arrival temperature.


## 1.4.1 — 2026-10-23

Keep heating-cost learning diagnostics visible before a price is available. Home Assistant now shows unknown with a reason and calibration attributes instead of hiding those attributes behind unavailable. Add observation count and first/last observation times so collection can be verified before the first complete gas interval. No change to heating control or stored learning.

Restart Home Assistant after installing. The dashboard JavaScript is unchanged from v1.4.0; if not already copied, install the attached file separately in /config/www and refresh the dashboard.


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
