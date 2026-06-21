"""recorder_throttle — per-entity time throttling of recorder DB writes (label-driven).

Hooks fail-safe into the running recorder instance: replaces the instance attribute
`_process_state_changed_event_into_session` with a wrapper that drops state changes
of throttled entities (no DB row). Live state / automations / UI stay untouched.

Configured via HA labels (rec-off/rec-1min/rec-5min/rec-10min) + a settings dialog
(config_flow/options: scan threshold for new heavy writers -> repair report). Docs: README.
"""
from __future__ import annotations

import logging
import time
from datetime import timedelta

import voluptuous as vol

from homeassistant.config_entries import SOURCE_IMPORT, ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse, callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers import label_registry as lr
from homeassistant.helpers.event import async_call_later, async_track_time_interval
from homeassistant.helpers.typing import ConfigType

from .const import (
    ACCEPTED_LABEL,
    ACCEPTED_LABEL_META,
    CONF_INTERVAL,
    CONF_SCAN_ENABLED,
    CONF_THRESHOLD,
    CONF_WINDOW,
    DEFAULTS,
    DOMAIN,
    LABEL_INTERVALS,
    LABEL_META,
    POLICY_TO_NAME,
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
                vol.Optional(CONF_WINDOW): vol.All(vol.Coerce(float), vol.Range(min=0, min_included=False)),
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
        data["policies"] = pol
        # Drop stale per-entity timestamps for entities that are no longer throttled.
        data["last"] = {e: t for e, t in data.get("last", {}).items() if e in pol}
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
        orig = rec._process_state_changed_event_into_session  # noqa: SLF001
        if getattr(orig, "_rt_wrapped", False):
            return True
        monotonic = time.monotonic

        def wrapped(event):  # runs synchronously in the recorder thread
            if not data["enabled"]:
                return orig(event)
            eid = event.data.get("entity_id")
            iv = data["policies"].get(eid)
            if iv is None:
                return orig(event)
            stats = data["stats"]
            if iv == 0:
                stats["dropped"] += 1
                return None
            now = monotonic()
            last = data["last"]
            prev = last.get(eid)
            if prev is not None and (now - prev) < iv:
                stats["dropped"] += 1
                return None
            last[eid] = now
            stats["passed"] += 1
            return orig(event)

        wrapped._rt_wrapped = True  # type: ignore[attr-defined]
        rec._process_state_changed_event_into_session = wrapped  # noqa: SLF001
        data["orig"] = orig
        data["rec"] = rec
        _LOGGER.info("recorder_throttle: hook installed; %d entities throttled", len(data["policies"]))
        return True
    except Exception:  # noqa: BLE001 — fail-safe
        _LOGGER.exception("recorder_throttle: hook installation failed")
        return False


@callback
def _restore_hook(hass: HomeAssistant) -> None:
    data = hass.data.get(DOMAIN) or {}
    try:
        if data.get("rec") is not None and data.get("orig") is not None:
            data["rec"]._process_state_changed_event_into_session = data["orig"]  # noqa: SLF001
            data["orig"] = None
            data["rec"] = None
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

async def _set_policy(hass: HomeAssistant, entity_ids: list[str], policy: str) -> None:
    ent_reg = er.async_get(hass)
    lab_reg = lr.async_get(hass)
    name_to_id = {label.name: label.label_id for label in lab_reg.async_list_labels()}
    rec_label_ids = {name_to_id[n] for n in LABEL_INTERVALS if n in name_to_id}
    target_name = POLICY_TO_NAME[policy]
    target_id = name_to_id.get(target_name) if target_name else None
    for eid in entity_ids:
        entry = ent_reg.async_get(eid)
        if entry is None:
            _LOGGER.warning("recorder_throttle: %s not in the entity registry", eid)
            continue
        new_labels = set(entry.labels) - rec_label_ids
        if target_id:
            new_labels.add(target_id)
        ent_reg.async_update_entity(eid, labels=new_labels)
    _rebuild_policies(hass)


async def _set_accepted(hass: HomeAssistant, entity_ids: list[str], accepted: bool) -> None:
    ent_reg = er.async_get(hass)
    lab_reg = lr.async_get(hass)
    acc_id = next((l.label_id for l in lab_reg.async_list_labels() if l.name == ACCEPTED_LABEL), None)
    if acc_id is None:
        _LOGGER.warning("recorder_throttle: label '%s' missing", ACCEPTED_LABEL)
        return
    for eid in entity_ids:
        entry = ent_reg.async_get(eid)
        if entry is None:
            continue
        labels = set(entry.labels)
        labels.add(acc_id) if accepted else labels.discard(acc_id)
        ent_reg.async_update_entity(eid, labels=labels)


# ---- Top writers + scan ---------------------------------------------------

async def _top_writers(
    hass: HomeAssistant, hours: float, limit: int, exclude_accepted: bool = False
) -> dict:
    """Top DB writers of the last <hours>h from the states table (via recorder executor)."""
    from sqlalchemy import text

    from homeassistant.components.recorder import get_instance
    from homeassistant.components.recorder.util import session_scope

    since = time.time() - hours * 3600.0
    instance = get_instance(hass)

    def _query():
        with session_scope(hass=hass, read_only=True) as session:
            res = session.execute(text(_TOP_WRITERS_SQL), {"since": since, "lim": limit}).all()
            return [(row[0], int(row[1])) for row in res]

    rows = await instance.async_add_executor_job(_query)
    policies = hass.data[DOMAIN]["policies"]
    accepted = _accepted_ids(hass)
    writers = []
    for eid, cnt in rows:
        is_acc = eid in accepted
        if exclude_accepted and is_acc:
            continue
        st = hass.states.get(eid)
        iv = policies.get(eid)
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


async def _scan_top_writers(hass: HomeAssistant, conf: dict) -> None:
    """Periodic scan: unthrottled, non-accepted heavy writers as ONE summary repair issue."""
    if not (hass.data.get(DOMAIN) or {}).get("active", False):
        return  # entry was unloaded while a scan task was still in flight
    try:
        window = float(conf.get(CONF_WINDOW, DEFAULTS[CONF_WINDOW]))
        res = await _top_writers(hass, window, 60)
    except Exception:  # noqa: BLE001
        _LOGGER.exception("recorder_throttle: top-writer scan failed")
        return
    thr = float(conf.get(CONF_THRESHOLD, DEFAULTS[CONF_THRESHOLD]))
    policies = hass.data[DOMAIN]["policies"]
    accepted = _accepted_ids(hass)
    flagged = [
        w
        for w in res["writers"]
        if w["per_min"] >= thr and w["entity_id"] not in accepted and policies.get(w["entity_id"]) is None
    ]
    if flagged:
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
    """Serve the Lovelace card from the integration and load it on the frontend.

    This makes the card available with a plain HACS *integration* install — no
    manual dashboard resource needed. Registered once; harmless to leave on unload.
    """
    data = hass.data.setdefault(DOMAIN, {})
    if data.get("card_registered"):
        return
    try:
        from pathlib import Path

        from homeassistant.components.frontend import add_extra_js_url
        from homeassistant.components.http import StaticPathConfig

        card = Path(__file__).parent / CARD_FILENAME
        await hass.http.async_register_static_paths(
            [StaticPathConfig(CARD_URL, str(card), False)]
        )
        add_extra_js_url(hass, CARD_URL)
        data["card_registered"] = True
        _LOGGER.debug("recorder_throttle: registered Lovelace card at %s", CARD_URL)
    except Exception:  # noqa: BLE001 — the card is optional; never block setup
        _LOGGER.warning("recorder_throttle: could not register the Lovelace card", exc_info=True)


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
    data["settings"] = settings
    data["active"] = True
    entry.async_on_unload(lambda: data.update({"active": False}))

    await _register_card(hass)
    _ensure_labels(hass)
    _rebuild_policies(hass)
    if _install_hook(hass):
        _set_hook_issue(hass, True)
    else:
        _set_hook_issue(hass, False)
        _LOGGER.error("recorder_throttle: hook NOT installed — recorder runs unthrottled (fail-safe)")
    entry.async_on_unload(lambda: _restore_hook(hass))

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
        if current is not None and not getattr(current, "_rt_wrapped", False):
            _LOGGER.info("recorder_throttle: recorder reload detected — re-installing hook")
            _set_hook_issue(hass, _install_hook(hass))

    entry.async_on_unload(async_track_time_interval(hass, _ensure_hook, timedelta(seconds=30)))

    @callback
    def _on_registry_update(_event) -> None:
        _rebuild_policies(hass)

    entry.async_on_unload(hass.bus.async_listen(er.EVENT_ENTITY_REGISTRY_UPDATED, _on_registry_update))
    entry.async_on_unload(hass.bus.async_listen(lr.EVENT_LABEL_REGISTRY_UPDATED, _on_registry_update))

    _register_services(hass)

    # Top-writer scan -> repair issues
    if settings.get(CONF_SCAN_ENABLED, True):
        interval = timedelta(minutes=int(settings.get(CONF_INTERVAL, DEFAULTS[CONF_INTERVAL])))

        @callback
        def _run_scan(_now=None) -> None:
            hass.async_create_task(_scan_top_writers(hass, data["settings"]))

        entry.async_on_unload(async_track_time_interval(hass, _run_scan, interval))
        if hass.is_running:
            entry.async_on_unload(async_call_later(hass, 15, _run_scan))
        else:
            entry.async_on_unload(hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, _run_scan))
    else:
        ir.async_delete_issue(hass, DOMAIN, SUMMARY_ISSUE)

    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))
    return True


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
        await _set_policy(hass, call.data["entity_id"], call.data["policy"])

    async def _svc_set_enabled(call: ServiceCall) -> None:
        hass.data[DOMAIN]["enabled"] = bool(call.data["enabled"])
        _LOGGER.info("recorder_throttle: enabled=%s", hass.data[DOMAIN]["enabled"])

    async def _svc_rebuild(_call: ServiceCall) -> None:
        _rebuild_policies(hass)

    async def _svc_top_writers(call: ServiceCall) -> dict:
        try:
            return await _top_writers(
                hass, float(call.data["hours"]), int(call.data["limit"]), bool(call.data["exclude_accepted"])
            )
        except Exception as err:  # noqa: BLE001
            _LOGGER.exception("recorder_throttle: top_writers failed")
            return {"error": str(err), "writers": []}

    async def _svc_set_accepted(call: ServiceCall) -> None:
        await _set_accepted(hass, call.data["entity_id"], bool(call.data["accepted"]))

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
                vol.Optional("hours", default=1): vol.Coerce(float),
                vol.Optional("limit", default=30): vol.Coerce(int),
                vol.Optional("exclude_accepted", default=False): cv.boolean,
            }
        ),
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_SET_ACCEPTED, _svc_set_accepted,
        schema=vol.Schema({vol.Required("entity_id"): cv.entity_ids, vol.Optional("accepted", default=True): cv.boolean}),
    )
