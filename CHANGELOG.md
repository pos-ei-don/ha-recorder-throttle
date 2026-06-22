# Changelog

## 0.8.2
- Fix integration validation: translation strings must not contain URLs — the "Info & help" box now points to the documentation link in the dialog header instead.

## 0.8.1
- Settings dialog: added an "Info & help" box with a short explanation and a link to the documentation.

## 0.8.0
- Settings are now grouped into two sections — "Detection & reporting" and "Auto-throttle" — and every field has a short description.
- With auto-throttle on, Repairs notices now only surface heavy writers that auto-throttle did not cover (e.g. non-sensor entities), and auto-throttle keeps working even when the Repairs notice is turned off.

## 0.7.0
- New option: auto-throttle newly detected heavy writers (checkbox + interval 1/5/10 min) — the integration applies the throttle itself instead of only raising a Repairs notice.
- Off by default: nothing is auto-throttled until you tick the box in the integration settings. Limited to sensor.* entities (configurable to all) and never touches entities you marked "accepted".

## 0.6.0
- Repairs "Fix" now actually resolves the issue: throttle all reported entities to 1 or 5 minutes, mark as accepted, or stop reporting (menu).
- The card registers itself as a Lovelace resource for reliable loading.

## 0.5.0
- Card: bulk action to throttle all listed heavy writers to 1 or 5 minutes at once.
- Clearer label for the Repairs toggle ("Create a Repairs notice for new heavy writers").

## 0.4.1
- Fix the integration options dialog failing to load (500 error, regression in 0.4.0).

## 0.4.0
- Repairs notice is now fixable in-app (stop future reports) instead of linking out to a website.

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
