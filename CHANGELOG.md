# Changelog

## 1.3
- Card: much less work on large installations (#2, thanks to @cerebrate for the analysis). Home Assistant hands the card a new state object on every state change anywhere; the card now re-renders only when something it shows has changed — the entity registry (labels), the language, or an entity on the visible page. With 800 throttled entities, 1000 unrelated state changes went from about 33 s to about 1 s of card work.
- Card: clicks are no longer lost. The card used to replace its buttons whenever a state changed, which could happen between pressing and releasing the mouse; it now waits until the click has been handled.
- Card: the heavy-writer query (`top_writers`) only runs while the card is actually visible — browser tab in the foreground and card on screen. When it becomes visible again after more than two minutes, it refreshes once. This saves recorder database reads for every dashboard left open in the background.
- Card: after throttling from the *Unthrottled* tab, a short notice confirms it ("30 entities set to 1m → now in the Throttled tab"). The list refills with the next heavy writers, which previously made it look as if nothing had happened.
- Card: the *Unthrottled* tab only lists entities that write more than once a minute. The mildest level keeps one value per minute and doesn't slow slower entities down at all; they are not heavy writers, and offering them (including "All to …") mostly led to pointless throttling.
- Card: the *Unthrottled* tab says when its list is cut at `limit`: the count reads "30+" and the hint "the 30 heaviest … (there may be more)"; a shorter list is the complete one ("All 12 …"). Previously "Unthrottled (30)" suggested there were only 30.
- The card is now loaded as `recorder-throttle-card.js?v=<version>` (an existing dashboard resource is updated on start). Browsers cache the card by its address, so after an update they kept running the old card until the cache was cleared.
- Card: the *Unthrottled* tab no longer highlights "Full" (everything there is unthrottled by definition, so it read like a selection) and explains in one line what the list shows.

## 1.2
- Card: entities without a unique ID (not in the entity registry, e.g. many YAML-defined entities) are marked "not throttleable" in the *Unthrottled* list instead of offering policy/accept buttons that could never work, and the "All to …" bulk action skips them (#3).
- Card: Throttled/Accepted tabs get a live sort toggle (entity ID or friendly name, shared between the two tabs); the initial default can be set via the new `sort` option.
- Card: all three tabs are now paginated (new `page_size` option, default 50) instead of rendering as one long list — makes the card usable on installations with large numbers of entities. The card's height stays constant while paging, so a shorter last page doesn't shift it around the dashboard.
- Card: the Throttled tab gets a live All/Unaccepted filter, so an entity you've just throttled is easy to find and accept without paging through everything already reviewed. The tab's count badge shows "shown/total" (e.g. `Throttled (88/767)`) while the filter is narrowing the view. Sorting, pagination and the filter were contributed by @cerebrate (#1).
- Card: the sort/filter toolbar wraps on narrow cards; the reserved page height no longer shrinks after a one-row last page; `page_size` and `limit` given as strings (`"50"`) are read as numbers.

## 1.1
- Throttle intervals are now measured on the time a state change happened, not on when the recorder got round to it. Previously, when the recorder worked off a backlog (right after a restart, or with a slow database), a throttled entity could keep a single value for the whole backlog; now it keeps one per interval, as configured.
- The throttle hook is installed earlier during setup, so fewer state changes slip through unthrottled at startup.
- The first heavy-writer scan now waits 5 minutes after Home Assistant starts (the periodic scan only starts after that), is skipped while the recorder has a large backlog, and never runs twice at the same time. A scan still running while the settings are changed no longer acts on the old settings.
- Entities without an entity-registry entry (which cannot carry labels and therefore cannot be throttled) are no longer reported as heavy writers.
- Heavy-writer detection no longer misses new heavy writers when already throttled or accepted entities fill the top of the list (exclusions are applied before the limit). New `exclude_throttled` option for `recorder_throttle.top_writers`.
- If a `rec-*` label was deleted, setting that policy recreates it instead of silently removing the entity's throttling.
- The Repairs fix no longer closes the notice when the heavy-writer query or the chosen action failed — it shows an error and leaves the notice open.
- The card's "Unthrottled" list is no longer emptied when throttled entities fill the top of the ranking.
- `recorder_throttle.top_writers` validates its inputs (window 0.1–168 h, limit 1–200).
- Removing the integration also removes its services and open notices.
- More robust against Home Assistant internals: extra arguments are passed through to the recorder, a failure in the throttle filter falls back to recording, and unloading only restores the recorder method if it is still ours; if another integration has wrapped it in the meantime, our filter switches to pass-through instead of being left active.
- The measurement window is limited to 0.1–168 hours everywhere (settings, scan, service, Repairs).

## 1.0
- Disabling throttling (`recorder_throttle.set_enabled` with `enabled: false`) now survives a restart: it stays off until you switch it back on. While throttling is disabled, a warning is logged on every start so it does not stay off unnoticed.
- Removing the integration now cleans up after itself — the dashboard resource for the bundled card and the stored state are removed. Your `rec-*` labels are kept.
- Documented the services with their parameters, how to disable throttling, and verified database support — SQLite, PostgreSQL and MariaDB/MySQL all tested with Home Assistant 2026.8.

## 0.8.3
- Fix the card's version number shown in the browser console — it had drifted behind the integration version; now it always stays in sync.

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
