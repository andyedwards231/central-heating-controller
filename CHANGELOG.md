# Changelog

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
