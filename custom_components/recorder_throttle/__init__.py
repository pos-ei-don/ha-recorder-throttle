"""recorder_throttle — per-entity time throttling of recorder DB writes (label-driven).

Hooks fail-safe into the running recorder instance: replaces the instance attribute
`_process_state_changed_event_into_session` with a wrapper that drops state changes
of throttled entities (no DB row). Live state / automations / UI stay untouched.

Configured via HA labels (rec-off/rec-1min/rec-5min/rec-10min) + a settings dialog
(config_flow/options: scan threshold for new heavy writers -> repair report). Docs: README.
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import timedelta

import voluptuous as vol

from homeassistant.config_entries import SOURCE_IMPORT, ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers import label_registry as lr
from homeassistant.helpers.event import async_call_later, async_track_time_interval
from homeassistant.helpers.storage import Store
from homeassistant.helpers.typing import ConfigType

from .const import (
    ACCEPTED_LABEL,
    ACCEPTED_LABEL_META,
    CONF_AUTO_POLICY,
    CONF_AUTO_SCOPE,
    CONF_AUTO_THROTTLE,
    CONF_INTERVAL,
    CONF_SCAN_ENABLED,
    CONF_THRESHOLD,
    CONF_WINDOW,
    DEFAULTS,
    DOMAIN,
    LABEL_INTERVALS,
    LABEL_META,
    POLICY_TO_NAME,
    STORAGE_KEY,
    STORAGE_VERSION,
)

_LOGGER = logging.getLogger(__name__)

# YAML (legacy) is imported into a config entry; this schema is only for the import mapping.
CONFIG_SCHEMA = vol.Schema(
    {
        DOMAIN: vol.Schema(
            {
                vol.Optional(CONF_SCAN_ENABLED): cv.boolean,
                vol.Optional(CONF_THRESHOLD): vol.All(vol.Coerce(float), vol.Range(min=0)),
                vol.Optional(CONF_INTERVAL): vol.All(vol.Coerce(int), vol.Range(min=1)),
                vol.Optional(CONF_WINDOW): vol.All(vol.Coerce(float), vol.Range(min=0.1, max=168)),
            },
            extra=vol.ALLOW_EXTRA,
        )
    },
    extra=vol.ALLOW_EXTRA,
)

SERVICE_SET_POLICY = "set_policy"
SERVICE_SET_ENABLED = "set_enabled"
SERVICE_REBUILD = "rebuild"
SERVICE_TOP_WRITERS = "top_writers"
SERVICE_SET_ACCEPTED = "set_accepted"
SUMMARY_ISSUE = "rt_top_writers_summary"
HOOK_ISSUE = "hook_not_installed"
CARD_FILENAME = "recorder-throttle-card.js"
CARD_URL = "/recorder_throttle/recorder-throttle-card.js"
FIRST_SCAN_DELAY = 300  # s after start: let the recorder work off the startup burst first
SCAN_SKIP_BACKLOG = 1000  # recorder queue length above which a scan round is postponed
WINDOW_MIN, WINDOW_MAX = 0.1, 168.0  # hours; same bounds for options, scanner, service and repair

_TOP_WRITERS_SQL = (
    "SELECT sm.entity_id, COUNT(*) AS c "
    "FROM states s JOIN states_meta sm ON s.metadata_id = sm.metadata_id "
    "WHERE s.last_updated_ts > :since "
    "GROUP BY sm.entity_id ORDER BY c DESC LIMIT :lim"
)


# ---- Data / policy --------------------------------------------------------

def _more_restrictive(a: int, b: int) -> int:
    """0 (= don't write) is the most restrictive; otherwise the larger interval wins."""
    if a == 0 or b == 0:
        return 0
    return max(a, b)


@callback
def _rebuild_policies(hass: HomeAssistant) -> None:
    """Recompute entity_id -> interval from the rec-* labels (atomic ref swap)."""
    data = hass.data[DOMAIN]
    try:
        ent_reg = er.async_get(hass)
        lab_reg = lr.async_get(hass)
        id_to_iv = {
            label.label_id: LABEL_INTERVALS[label.name]
            for label in lab_reg.async_list_labels()
            if label.name in LABEL_INTERVALS
        }
        pol: dict[str, int] = {}
        if id_to_iv:
            for entry in ent_reg.entities.values():
                best: int | None = None
                for lid in entry.labels:
                    iv = id_to_iv.get(lid)
                    if iv is None:
                        continue
                    best = iv if best is None else _more_restrictive(best, iv)
                if best is not None:
                    pol[entry.entity_id] = best
        # The recorder thread reads/writes data["last"] in the hook: publish + prune under the lock.
        with data["lock"]:
            data["policies"] = pol
            last = data["last"]
            for eid in [e for e in last if e not in pol]:
                del last[eid]  # drop timestamps of entities that are no longer throttled
        _LOGGER.debug("recorder_throttle: %d entities throttled", len(pol))
    except Exception:  # noqa: BLE001 — fail-safe
        _LOGGER.exception("recorder_throttle: rebuilding policies failed")


@callback
def _ensure_labels(hass: HomeAssistant) -> None:
    """Create the rec-* + rec-accepted labels if they are missing."""
    lab_reg = lr.async_get(hass)
    existing = {label.name for label in lab_reg.async_list_labels()}
    defs = dict(LABEL_META)
    defs[ACCEPTED_LABEL] = ACCEPTED_LABEL_META
    for name, (color, icon) in defs.items():
        if name in existing:
            continue
        try:
            lab_reg.async_create(name=name, color=color, icon=icon)
            _LOGGER.info("recorder_throttle: created label '%s'", name)
        except Exception:  # noqa: BLE001
            _LOGGER.warning("recorder_throttle: could not create label '%s'", name)


@callback
def _accepted_ids(hass: HomeAssistant) -> set[str]:
    """entity_ids carrying the rec-accepted label."""
    ent_reg = er.async_get(hass)
    lab_reg = lr.async_get(hass)
    acc_id = next((l.label_id for l in lab_reg.async_list_labels() if l.name == ACCEPTED_LABEL), None)
    if acc_id is None:
        return set()
    return {e.entity_id for e in ent_reg.entities.values() if acc_id in e.labels}


# ---- Recorder hook --------------------------------------------------------

def _install_hook(hass: HomeAssistant) -> bool:
    """Install the wrapper into the recorder instance. True on success."""
    data = hass.data[DOMAIN]
    try:
        from homeassistant.components.recorder import get_instance

        rec = get_instance(hass)
        if rec is None:
            _LOGGER.error("recorder_throttle: no recorder instance found")
            return False
        current = rec._process_state_changed_event_into_session  # noqa: SLF001
        prev_state = data.get("wrapper_state")
        if current is data.get("wrapper") and prev_state is not None and prev_state["on"]:
            return True  # our active installation is still in place (identity, not a copyable attribute)
        if prev_state is not None:
            # A previous installation of ours may still sit somewhere below `current` (recorder reloaded,
            # or another integration wrapped it). Switch it to pass-through BEFORE the new filter goes
            # live: at most one installation may filter, or two filters sharing `last` would drop the
            # samples that are meant to be kept.
            prev_state["on"] = False
        orig = current
        lock = data["lock"]
        wall = time.time
        state = {"on": True}  # per installation: switched off on unload, even if someone else wraps us

        def wrapped(event, *args, **kwargs):  # runs synchronously in the recorder thread
            """Throttle one state_changed event, or hand it to the recorder unchanged.

            Read this before drawing conclusions from the ``return None`` branches below:
            an event is dropped **only** when its entity carries an explicit policy. The
            default is *record*, never *discard*.

              * throttling disabled       -> passthrough (``orig(event)``)
              * entity has no policy      -> passthrough  <- the vast majority of events
              * policy is "off" (iv == 0) -> drop
              * still inside the interval -> drop

            Only the last two return ``None``. Reading them in isolation has already led
            to the wrong conclusion once ("it discards everything"), hence this note.
            """
            if not state["on"] or not data["enabled"]:
                return orig(event, *args, **kwargs)
            try:
                eid = event.data.get("entity_id")
                # Interval is measured on the EVENT time, not on processing time: when the recorder
                # works off a backlog (startup burst, slow database) it processes minutes of events in
                # seconds — measured on processing time, a 1-minute entity would keep a single row.
                ts = getattr(event, "time_fired_timestamp", None) or wall()
                with lock:
                    iv = data["policies"].get(eid)
                    if iv is None:
                        drop = False
                    elif iv == 0:
                        drop = True
                    else:
                        last = data["last"]
                        prev = last.get(eid)
                        # ts < prev: clock stepped back or events out of order -> keep and restart the interval
                        drop = prev is not None and 0 <= ts - prev < iv
                        if not drop:
                            last[eid] = ts
                    if iv is not None:
                        data["stats"]["dropped" if drop else "passed"] += 1
            except Exception:  # noqa: BLE001 — our own filter must never stop the recorder: record instead
                drop = False
            if drop:
                return None
            return orig(event, *args, **kwargs)

        wrapped._rt_wrapped = True  # type: ignore[attr-defined]
        rec._process_state_changed_event_into_session = wrapped  # noqa: SLF001
        data["orig"] = orig
        data["rec"] = rec
        data["wrapper"] = wrapped
        data["wrapper_state"] = state
        _LOGGER.info("recorder_throttle: hook installed; %d entities throttled", len(data["policies"]))
        return True
    except Exception:  # noqa: BLE001 — fail-safe
        _LOGGER.exception("recorder_throttle: hook installation failed")
        return False


@callback
def _restore_hook(hass: HomeAssistant) -> None:
    data = hass.data.get(DOMAIN) or {}
    try:
        if data.get("wrapper_state") is not None:
            data["wrapper_state"]["on"] = False  # still nested under a foreign patch -> pure passthrough
        rec, orig, mine = data.get("rec"), data.get("orig"), data.get("wrapper")
        if rec is not None and orig is not None:
            current = getattr(rec, "_process_state_changed_event_into_session", None)
            if current is mine:  # identity, not an attribute: only take back OUR wrapper
                rec._process_state_changed_event_into_session = orig  # noqa: SLF001
        data.update({"orig": None, "rec": None, "wrapper": None, "wrapper_state": None})
    except Exception:  # noqa: BLE001 — fail-safe; a failed restore must not break unload
        _LOGGER.exception("recorder_throttle: restoring the recorder hook failed")


@callback
def _set_hook_issue(hass: HomeAssistant, installed: bool) -> None:
    """Surface a Repairs issue when the hook could not be installed (cleared once it is)."""
    if installed:
        ir.async_delete_issue(hass, DOMAIN, HOOK_ISSUE)
    else:
        ir.async_create_issue(
            hass,
            DOMAIN,
            HOOK_ISSUE,
            is_fixable=False,
            severity=ir.IssueSeverity.ERROR,
            translation_key="hook_not_installed",
        )


# ---- Label operations -----------------------------------------------------

async def _set_policy(hass: HomeAssistant, entity_ids: list[str], policy: str) -> list[str]:
    """Apply a policy; returns the entity_ids actually changed."""
    _ensure_labels(hass)  # a deleted rec-* label must not turn "throttle" into "remove all throttling"
    ent_reg = er.async_get(hass)
    lab_reg = lr.async_get(hass)
    name_to_id = {label.name: label.label_id for label in lab_reg.async_list_labels()}
    rec_label_ids = {name_to_id[n] for n in LABEL_INTERVALS if n in name_to_id}
    target_name = POLICY_TO_NAME[policy]
    target_id = name_to_id.get(target_name) if target_name else None
    if target_name and target_id is None:
        _LOGGER.error("recorder_throttle: label '%s' missing and could not be created — policy not applied", target_name)
        return []
    changed: list[str] = []
    for eid in entity_ids:
        entry = ent_reg.async_get(eid)
        if entry is None:
            _LOGGER.warning("recorder_throttle: %s not in the entity registry", eid)
            continue
        new_labels = set(entry.labels) - rec_label_ids
        if target_id:
            new_labels.add(target_id)
        ent_reg.async_update_entity(eid, labels=new_labels)
        changed.append(eid)
    _rebuild_policies(hass)
    return changed


async def _set_accepted(hass: HomeAssistant, entity_ids: list[str], accepted: bool) -> list[str]:
    """Set/clear the accepted marker; returns the entity_ids actually changed."""
    _ensure_labels(hass)
    ent_reg = er.async_get(hass)
    lab_reg = lr.async_get(hass)
    acc_id = next((l.label_id for l in lab_reg.async_list_labels() if l.name == ACCEPTED_LABEL), None)
    if acc_id is None:
        _LOGGER.warning("recorder_throttle: label '%s' missing", ACCEPTED_LABEL)
        return []
    changed: list[str] = []
    for eid in entity_ids:
        entry = ent_reg.async_get(eid)
        if entry is None:
            continue
        labels = set(entry.labels)
        labels.add(acc_id) if accepted else labels.discard(acc_id)
        ent_reg.async_update_entity(eid, labels=labels)
        changed.append(eid)
    return changed


# ---- Top writers + scan ---------------------------------------------------

async def _top_writers(
    hass: HomeAssistant, hours: float, limit: int, exclude_accepted: bool = False, exclude_throttled: bool = False
) -> dict:
    """Top DB writers of the last <hours>h from the states table (via recorder executor).

    Exclusions are applied BEFORE cutting to <limit>: the SQL fetches enough extra rows to cover every
    excluded entity, so already handled heavy writers cannot crowd new ones out of the result."""
    from sqlalchemy import text

    from homeassistant.components.recorder import get_instance
    from homeassistant.components.recorder.util import session_scope

    since = time.time() - hours * 3600.0
    instance = get_instance(hass)
    policies = hass.data[DOMAIN]["policies"]
    accepted = _accepted_ids(hass)
    extra = (len(accepted) if exclude_accepted else 0) + (len(policies) if exclude_throttled else 0)
    fetch = limit + extra  # bounded by the number of entities; no cap, or exclusions could hide new writers again

    def _query():
        with session_scope(hass=hass, read_only=True) as session:
            res = session.execute(text(_TOP_WRITERS_SQL), {"since": since, "lim": fetch}).all()
            return [(row[0], int(row[1])) for row in res]

    rows = await instance.async_add_executor_job(_query)
    # The snapshot above only sizes the query. Labels may change while it is running;
    # use current policies/exclusions so Repairs does not act on an outdated choice.
    policies = hass.data[DOMAIN]["policies"]
    accepted = _accepted_ids(hass)
    ent_reg = er.async_get(hass)
    writers = []
    for eid, cnt in rows:
        is_acc = eid in accepted
        if exclude_accepted and is_acc:
            continue
        if exclude_throttled and eid in policies:
            continue
        if len(writers) >= limit:
            break
        st = hass.states.get(eid)
        iv = policies.get(eid)
        in_registry = ent_reg.async_get(eid) is not None
        policy = "full" if iv is None else ("off" if iv == 0 else f"{iv // 60}min")
        writers.append(
            {
                "entity_id": eid,
                "name": (st.attributes.get("friendly_name") if st else None) or eid,
                "rows": cnt,
                "per_min": round(cnt / (hours * 60.0), 1),
                "policy": policy,
                "accepted": is_acc,
                "has_statistics": bool(st and st.attributes.get("state_class")),
                # throttling works through registry labels: entities without an entry cannot be throttled
                "in_registry": in_registry,
            }
        )
    stats = hass.data[DOMAIN].get("stats", {})
    return {
        "window_hours": hours,
        "count": len(writers),
        "writers": writers,
        # Running counters since the last restart — for "how many writes were prevented".
        "totals": {"dropped": stats.get("dropped", 0), "passed": stats.get("passed", 0)},
    }


async def _scan_top_writers(hass: HomeAssistant, gen: object) -> None:
    """Periodic scan: unthrottled, non-accepted heavy writers as ONE summary repair issue.

    `gen` identifies the setup that started the scan: after an options reload (new settings) or
    unload, a scan still in flight must not act on the old settings."""
    data = hass.data.get(DOMAIN) or {}
    if not data.get("active", False) or data.get("gen") is not gen:
        return  # entry was unloaded/reloaded while a scan task was still in flight
    try:
        from homeassistant.components.recorder import get_instance

        backlog = getattr(get_instance(hass), "backlog", 0) or 0
    except Exception:  # noqa: BLE001
        backlog = 0
    if backlog > SCAN_SKIP_BACKLOG:
        _LOGGER.debug("recorder_throttle: recorder backlog %s — top-writer scan postponed", backlog)
        return
    try:
        conf = data["settings"]
        window = min(max(float(conf.get(CONF_WINDOW, DEFAULTS[CONF_WINDOW])), WINDOW_MIN), WINDOW_MAX)
        res = await _top_writers(hass, window, 60, exclude_accepted=True, exclude_throttled=True)
    except Exception:  # noqa: BLE001
        _LOGGER.exception("recorder_throttle: top-writer scan failed")
        return
    if not data.get("active", False) or data.get("gen") is not gen:
        return  # unloaded or reloaded (possibly with other settings) while the query ran
    conf = data["settings"]
    thr = float(conf.get(CONF_THRESHOLD, DEFAULTS[CONF_THRESHOLD]))
    policies = hass.data[DOMAIN]["policies"]
    accepted = _accepted_ids(hass)
    flagged = [
        w
        for w in res["writers"]
        if w["per_min"] >= thr and w["entity_id"] not in accepted and policies.get(w["entity_id"]) is None
        and w["in_registry"]  # not throttleable -> no notice that could never be resolved
    ]
    # Auto-throttle: apply a policy to in-scope new heavy writers instead of nagging.
    if flagged and conf.get(CONF_AUTO_THROTTLE, DEFAULTS[CONF_AUTO_THROTTLE]):
        policy = conf.get(CONF_AUTO_POLICY, DEFAULTS[CONF_AUTO_POLICY])
        scope = conf.get(CONF_AUTO_SCOPE, DEFAULTS[CONF_AUTO_SCOPE])
        in_scope = [w for w in flagged if scope == "all" or w["entity_id"].startswith("sensor.")]
        if in_scope:
            ids = await _set_policy(hass, [w["entity_id"] for w in in_scope], policy)
            if ids:
                _LOGGER.info(
                    "recorder_throttle: auto-throttled %d new heavy writer(s) to %s: %s",
                    len(ids), policy, ", ".join(ids[:10]) + (" …" if len(ids) > 10 else ""),
                )
            flagged = [w for w in flagged if w["entity_id"] not in ids]  # only report what was not auto-handled
    # Repair notice only when reporting is enabled (behaviour b: with auto-throttle on,
    # this surfaces just the entities auto-throttle did not cover, e.g. non-sensor writers).
    if flagged and conf.get(CONF_SCAN_ENABLED, True):
        examples = ", ".join(w["name"] for w in flagged[:5]) + (" …" if len(flagged) > 5 else "")
        ir.async_create_issue(
            hass,
            DOMAIN,
            SUMMARY_ISSUE,
            is_fixable=True,
            severity=ir.IssueSeverity.WARNING,
            translation_key="top_writers_summary",
            translation_placeholders={"count": str(len(flagged)), "examples": examples, "threshold": str(int(thr))},
        )
    else:
        ir.async_delete_issue(hass, DOMAIN, SUMMARY_ISSUE)


# ---- Lovelace card registration -------------------------------------------

async def _register_card(hass: HomeAssistant) -> None:
    """Serve the Lovelace card from the integration and make the dashboard load it.

    Serves the JS via a static path, then registers it as a Lovelace *resource*
    (the reliable loader for custom cards) with a fallback to add_extra_js_url. So a
    plain HACS *integration* install delivers the card — no manual resource. Idempotent.
    """
    data = hass.data.setdefault(DOMAIN, {})
    if data.get("card_registered"):
        return
    if not hass.data.get(f"{DOMAIN}_card_static"):  # a static path cannot be unregistered: once per HA run
        try:
            from pathlib import Path

            from homeassistant.components.http import StaticPathConfig

            card = Path(__file__).parent / CARD_FILENAME
            await hass.http.async_register_static_paths(
                [StaticPathConfig(CARD_URL, str(card), False)]
            )
            hass.data[f"{DOMAIN}_card_static"] = True
        except Exception:  # noqa: BLE001 — the card is optional; never block setup
            _LOGGER.warning("recorder_throttle: could not serve the Lovelace card", exc_info=True)
            return

    if await _register_lovelace_resource(hass):
        data["card_registered"] = True
        return
    # Fallback: load it as an app-level module.
    try:
        from homeassistant.components.frontend import add_extra_js_url

        add_extra_js_url(hass, await _card_url(hass))
        data["card_registered"] = True
        _LOGGER.debug("recorder_throttle: card loaded via add_extra_js_url")
    except Exception:  # noqa: BLE001
        _LOGGER.warning("recorder_throttle: could not load the Lovelace card", exc_info=True)


async def _card_url(hass: HomeAssistant) -> str:
    """Card URL with the integration version as query: browsers cache the module by URL, so without it
    they keep running the old card after an update until the user clears the cache."""
    try:
        from homeassistant.loader import async_get_integration

        version = (await async_get_integration(hass, DOMAIN)).version
    except Exception:  # noqa: BLE001 — a missing version must not block the card
        version = None
    return f"{CARD_URL}?v={version}" if version else CARD_URL


async def _register_lovelace_resource(hass: HomeAssistant) -> bool:
    """Add the card as a Lovelace resource (storage mode). True on success/already present."""
    try:
        lovelace = hass.data.get("lovelace")
        resources = getattr(lovelace, "resources", None) if lovelace is not None else None
        if resources is None or not hasattr(resources, "async_create_item"):
            return False  # not available / YAML mode -> caller falls back
        if hasattr(resources, "loaded") and not resources.loaded:
            await resources.async_load()
        url = await _card_url(hass)
        for item in resources.async_items():
            if (item.get("url") or "").split("?")[0] == CARD_URL:
                if item.get("url") != url:  # registered by an older version: point it at this one
                    await resources.async_update_item(item["id"], {"res_type": "module", "url": url})
                    _LOGGER.info("recorder_throttle: updated Lovelace resource to %s", url)
                return True
        await resources.async_create_item({"res_type": "module", "url": url})
        _LOGGER.info("recorder_throttle: registered Lovelace resource %s", url)
        return True
    except Exception:  # noqa: BLE001
        _LOGGER.debug("recorder_throttle: lovelace resource registration unavailable", exc_info=True)
        return False


# ---- Setup (YAML import + config entry) -----------------------------------

async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Import YAML (legacy) into a config entry."""
    if DOMAIN in config:
        hass.async_create_task(
            hass.config_entries.flow.async_init(
                DOMAIN, context={"source": SOURCE_IMPORT}, data=config.get(DOMAIN) or {}
            )
        )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Actual setup per config entry."""
    settings = {**DEFAULTS, **dict(entry.options)}
    data = hass.data.setdefault(
        DOMAIN,
        {"enabled": True, "policies": {}, "last": {}, "orig": None, "rec": None, "stats": {"dropped": 0, "passed": 0}},
    )
    data.setdefault("lock", threading.Lock())
    data["settings"] = settings
    data["active"] = True
    gen = object()  # this setup's identity (see _scan_top_writers)
    data["gen"] = gen
    data.pop("scan_task", None)

    @callback
    def _stop() -> None:
        data.update({"active": False, "gen": None})
        task = data.pop("scan_task", None)
        if task is not None and not task.done():
            task.cancel()  # the executor query itself finishes on its own; its result is then ignored

    entry.async_on_unload(_stop)

    # Restore whether throttling is enabled (see const.STORAGE_KEY for why this is not
    # kept in the entry options). Default is enabled.
    store = data.get("store") or Store(hass, STORAGE_VERSION, STORAGE_KEY)
    data["store"] = store
    saved = await store.async_load() or {}
    data["enabled"] = bool(saved.get("enabled", True))
    if not data["enabled"]:
        _LOGGER.warning(
            "recorder_throttle: throttling is DISABLED — it was switched off earlier and that "
            "setting survives restarts. Everything is being recorded. Call "
            "recorder_throttle.set_enabled with enabled: true to switch throttling back on"
        )

    # Hook first (before the card's awaits), so as few events as possible pass unthrottled at startup.
    _ensure_labels(hass)
    _rebuild_policies(hass)
    if _install_hook(hass):
        _set_hook_issue(hass, True)
    else:
        _set_hook_issue(hass, False)
        _LOGGER.error("recorder_throttle: hook NOT installed — recorder runs unthrottled (fail-safe)")
    entry.async_on_unload(lambda: _restore_hook(hass))
    await _register_card(hass)

    @callback
    def _ensure_hook(_now=None) -> None:
        """Self-heal: re-install the hook if the recorder was reloaded/restarted at runtime.

        Reloading the recorder without restarting HA (common during development) replaces
        or resets its method, dropping our patch. This re-installs it within one tick.
        """
        try:
            from homeassistant.components.recorder import get_instance

            rec = get_instance(hass)
        except Exception:  # noqa: BLE001 — fail-safe
            return
        if rec is None:
            return
        current = getattr(rec, "_process_state_changed_event_into_session", None)
        if current is not None and current is not data.get("wrapper"):
            _LOGGER.info("recorder_throttle: recorder method replaced (reload or another patch) — re-installing hook")
            _set_hook_issue(hass, _install_hook(hass))

    entry.async_on_unload(async_track_time_interval(hass, _ensure_hook, timedelta(seconds=30)))

    @callback
    def _on_registry_update(_event) -> None:
        _rebuild_policies(hass)

    entry.async_on_unload(hass.bus.async_listen(er.EVENT_ENTITY_REGISTRY_UPDATED, _on_registry_update))
    entry.async_on_unload(hass.bus.async_listen(lr.EVENT_LABEL_REGISTRY_UPDATED, _on_registry_update))

    _register_services(hass)

    # Top-writer scan -> auto-throttle and/or repair issues.
    # Runs if reporting is on OR auto-throttle is on (auto-throttle needs the scan).
    if settings.get(CONF_SCAN_ENABLED, True) or settings.get(CONF_AUTO_THROTTLE, False):
        interval = timedelta(minutes=int(settings.get(CONF_INTERVAL, DEFAULTS[CONF_INTERVAL])))

        @callback
        def _run_scan(_now=None) -> None:
            if data.get("gen") is not gen:
                return
            task = data.get("scan_task")
            if task is not None and not task.done():
                return  # previous round still waiting for the database — no overlapping scans
            data["scan_task"] = hass.async_create_task(_scan_top_writers(hass, gen))

        @callback
        def _start_scanning(_now=None) -> None:
            """First scan now, then periodically — the interval timer starts only here, so no
            scan runs before the startup delay is over."""
            if data.get("gen") is not gen:
                return
            _run_scan()
            entry.async_on_unload(async_track_time_interval(hass, _run_scan, interval))

        if hass.is_running:  # reload (options changed): the recorder is warm, scan soon
            entry.async_on_unload(async_call_later(hass, 15, _start_scanning))
        else:  # HA start: give the recorder time to work off the startup burst first

            @callback
            def _later(_event) -> None:
                entry.async_on_unload(async_call_later(hass, FIRST_SCAN_DELAY, _start_scanning))

            entry.async_on_unload(hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, _later))
    else:
        ir.async_delete_issue(hass, DOMAIN, SUMMARY_ISSUE)

    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))
    return True


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Clean up what setup created, so removing the integration leaves no leftovers.

    Removed here: the Lovelace resource for the bundled card (it would otherwise point at
    a URL this integration no longer serves and show up as a broken dashboard resource),
    and the stored enable/disable state.

    Deliberately *kept*: the ``rec-*`` labels. They carry the user's throttling choices,
    not our bookkeeping — silently deleting them would throw away that work. They are
    plain labels and can be removed in Settings -> Areas & Labels if desired.
    """
    try:
        lovelace = hass.data.get("lovelace")
        resources = getattr(lovelace, "resources", None) if lovelace is not None else None
        if resources is not None and hasattr(resources, "async_delete_item"):
            if hasattr(resources, "loaded") and not resources.loaded:
                await resources.async_load()
            for item in list(resources.async_items()):
                if (item.get("url") or "").split("?")[0] == CARD_URL:
                    await resources.async_delete_item(item["id"])
                    _LOGGER.info("recorder_throttle: removed Lovelace resource %s", CARD_URL)
                    break
    except Exception:  # noqa: BLE001 — removal must never fail the uninstall
        _LOGGER.debug("recorder_throttle: could not remove the Lovelace resource", exc_info=True)

    try:
        await Store(hass, STORAGE_VERSION, STORAGE_KEY).async_remove()
    except Exception:  # noqa: BLE001
        _LOGGER.debug("recorder_throttle: could not remove the stored state", exc_info=True)

    # Services and runtime data must not outlive the integration (they would still change labels
    # and re-create the store). The static card path stays registered until restart (HA cannot drop it).
    for svc in (SERVICE_SET_POLICY, SERVICE_SET_ENABLED, SERVICE_REBUILD, SERVICE_TOP_WRITERS, SERVICE_SET_ACCEPTED):
        hass.services.async_remove(DOMAIN, svc)
    for issue in (SUMMARY_ISSUE, HOOK_ISSUE):
        ir.async_delete_issue(hass, DOMAIN, issue)
    hass.data.pop(DOMAIN, None)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload — all async_on_unload callbacks (hook restore, listeners, scan) run automatically."""
    return True


async def _async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Options changed -> reload the entry (new threshold/interval take effect)."""
    await hass.config_entries.async_reload(entry.entry_id)


@callback
def _register_services(hass: HomeAssistant) -> None:
    """Register services once (they survive reloads; handlers read hass.data)."""
    if hass.services.has_service(DOMAIN, SERVICE_SET_POLICY):
        return

    async def _svc_set_policy(call: ServiceCall) -> None:
        if call.data["entity_id"] and not await _set_policy(hass, call.data["entity_id"], call.data["policy"]):
            raise HomeAssistantError("recorder_throttle: policy was not applied to any entity (see log)")

    async def _svc_set_enabled(call: ServiceCall) -> None:
        data = hass.data[DOMAIN]
        data["enabled"] = bool(call.data["enabled"])
        store = data.get("store")
        if store is not None:
            await store.async_save({"enabled": data["enabled"]})
        _LOGGER.info("recorder_throttle: enabled=%s (persisted across restarts)", data["enabled"])

    async def _svc_rebuild(_call: ServiceCall) -> None:
        _rebuild_policies(hass)

    async def _svc_top_writers(call: ServiceCall) -> dict:
        try:
            return await _top_writers(
                hass, float(call.data["hours"]), int(call.data["limit"]), bool(call.data["exclude_accepted"]),
                bool(call.data["exclude_throttled"]),
            )
        except Exception as err:  # noqa: BLE001
            _LOGGER.exception("recorder_throttle: top_writers failed")
            return {"error": str(err), "writers": []}

    async def _svc_set_accepted(call: ServiceCall) -> None:
        if call.data["entity_id"] and not await _set_accepted(hass, call.data["entity_id"], bool(call.data["accepted"])):
            raise HomeAssistantError("recorder_throttle: accepted marker was not applied to any entity (see log)")

    hass.services.async_register(
        DOMAIN, SERVICE_SET_POLICY, _svc_set_policy,
        schema=vol.Schema({vol.Required("entity_id"): cv.entity_ids, vol.Required("policy"): vol.In(list(POLICY_TO_NAME))}),
    )
    hass.services.async_register(
        DOMAIN, SERVICE_SET_ENABLED, _svc_set_enabled, schema=vol.Schema({vol.Required("enabled"): cv.boolean})
    )
    hass.services.async_register(DOMAIN, SERVICE_REBUILD, _svc_rebuild, schema=vol.Schema({}))
    hass.services.async_register(
        DOMAIN, SERVICE_TOP_WRITERS, _svc_top_writers,
        schema=vol.Schema(
            {
                vol.Optional("hours", default=1): vol.All(vol.Coerce(float), vol.Range(min=WINDOW_MIN, max=WINDOW_MAX)),
                vol.Optional("limit", default=30): vol.All(vol.Coerce(int), vol.Range(min=1, max=200)),
                vol.Optional("exclude_accepted", default=False): cv.boolean,
                vol.Optional("exclude_throttled", default=False): cv.boolean,
            }
        ),
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_SET_ACCEPTED, _svc_set_accepted,
        schema=vol.Schema({vol.Required("entity_id"): cv.entity_ids, vol.Optional("accepted", default=True): cv.boolean}),
    )
