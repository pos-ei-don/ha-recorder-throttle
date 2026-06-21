# Changelog

## 0.3.5
- Card now ships with the integration and loads automatically — no manual dashboard resource needed.

## 0.3.4
- Card: HTML-escape entity names (prevents XSS via crafted friendly names).
- Use the current Home Assistant options-flow pattern (no deprecation warning).

## 0.3.3
- Show a Repairs issue when the recorder hook can't be installed (was log-only).

## 0.3.2
- Throttle hook self-heals after a recorder reload at runtime (no HA restart needed).

## 0.3.1
- All code/comments/logs in English; settings input validation; minor robustness fixes.

## 0.3.0
- Initial release: per-entity recorder throttling via labels, management card, Repairs report.
