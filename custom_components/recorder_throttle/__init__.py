"""recorder_throttle — per-Entity-Zeit-Throttling der Recorder-DB-Writes (label-gesteuert).

Hängt sich fail-safe in die laufende Recorder-Instanz: ersetzt das Instanz-Attribut
`_process_state_changed_event_into_session` durch einen Wrapper, der State-Changes
gedrosselter Entities verwirft (kein DB-Row). Live-State/Automationen/UI bleiben unberührt.

Konfiguration über HA-Labels (rec-off/rec-1min/rec-5min/rec-10min) + Einstellungsdialog
(config_flow/options: Scan-Schwelle für neue Vielschreiber → Repair-Report). Docs: README.
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

# YAML (Altbestand) wird in einen Config-Entry importiert; Schema nur fürs Import-Mapping.
CONFIG_SCHEMA = vol.Schema(
    {
        DOMAIN: vol.Schema(
            {
                vol.Optional(CONF_SCAN_ENABLED): cv.boolean,
                vol.Optional(CONF_THRESHOLD): vol.Coerce(float),
                vol.Optional(CONF_INTERVAL): vol.Coerce(int),
                vol.Optional(CONF_WINDOW): vol.Coerce(float),
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
ISSUE_PREFIX = "top_writer_"  # Altbestand (frühere per-Entity-Issues) — wird aufgeräumt
SUMMARY_ISSUE = "rt_top_writers_summary"

_TOP_WRITERS_SQL = (
    "SELECT sm.entity_id, COUNT(*) AS c "
    "FROM states s JOIN states_meta sm ON s.metadata_id = sm.metadata_id "
    "WHERE s.last_updated_ts > :since "
    "GROUP BY sm.entity_id ORDER BY c DESC LIMIT :lim"
)


# ---- Daten/Policy ---------------------------------------------------------

def _more_restrictive(a: int, b: int) -> int:
    """0 (= nicht schreiben) am restriktivsten, sonst gewinnt das größere Intervall."""
    if a == 0 or b == 0:
        return 0
    return max(a, b)


@callback
def _rebuild_policies(hass: HomeAssistant) -> None:
    """entity_id -> Intervall aus den rec-* Labels neu berechnen (atomarer Ref-Swap)."""
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
        _LOGGER.debug("recorder_throttle: %d Entities gedrosselt", len(pol))
    except Exception:  # noqa: BLE001 — fail-safe
        _LOGGER.exception("recorder_throttle: Rebuild der Policies fehlgeschlagen")


@callback
def _ensure_labels(hass: HomeAssistant) -> None:
    """rec-* + rec-accepted Labels anlegen, falls sie fehlen."""
    lab_reg = lr.async_get(hass)
    existing = {label.name for label in lab_reg.async_list_labels()}
    defs = dict(LABEL_META)
    defs[ACCEPTED_LABEL] = ACCEPTED_LABEL_META
    for name, (color, icon) in defs.items():
        if name in existing:
            continue
        try:
            lab_reg.async_create(name=name, color=color, icon=icon)
            _LOGGER.info("recorder_throttle: Label '%s' angelegt", name)
        except Exception:  # noqa: BLE001
            _LOGGER.warning("recorder_throttle: Label '%s' konnte nicht angelegt werden", name)


@callback
def _accepted_ids(hass: HomeAssistant) -> set[str]:
    """entity_ids mit rec-accepted Label."""
    ent_reg = er.async_get(hass)
    lab_reg = lr.async_get(hass)
    acc_id = next((l.label_id for l in lab_reg.async_list_labels() if l.name == ACCEPTED_LABEL), None)
    if acc_id is None:
        return set()
    return {e.entity_id for e in ent_reg.entities.values() if acc_id in e.labels}


# ---- Recorder-Hook --------------------------------------------------------

def _install_hook(hass: HomeAssistant) -> bool:
    """Wrapper in die Recorder-Instanz einhängen. True bei Erfolg."""
    data = hass.data[DOMAIN]
    try:
        from homeassistant.components.recorder import get_instance

        rec = get_instance(hass)
        if rec is None:
            _LOGGER.error("recorder_throttle: keine Recorder-Instanz gefunden")
            return False
        orig = rec._process_state_changed_event_into_session  # noqa: SLF001
        if getattr(orig, "_rt_wrapped", False):
            return True
        monotonic = time.monotonic

        def wrapped(event):  # läuft synchron im Recorder-Thread
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
        _LOGGER.info("recorder_throttle: Hook installiert; %d Entities gedrosselt", len(data["policies"]))
        return True
    except Exception:  # noqa: BLE001 — fail-safe
        _LOGGER.exception("recorder_throttle: Hook-Installation fehlgeschlagen")
        return False


@callback
def _restore_hook(hass: HomeAssistant) -> None:
    data = hass.data.get(DOMAIN) or {}
    try:
        if data.get("rec") is not None and data.get("orig") is not None:
            data["rec"]._process_state_changed_event_into_session = data["orig"]  # noqa: SLF001
            data["orig"] = None
    except Exception:  # noqa: BLE001
        pass


# ---- Label-Operationen ----------------------------------------------------

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
            _LOGGER.warning("recorder_throttle: %s nicht in der Entity-Registry", eid)
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
        _LOGGER.warning("recorder_throttle: Label '%s' fehlt", ACCEPTED_LABEL)
        return
    for eid in entity_ids:
        entry = ent_reg.async_get(eid)
        if entry is None:
            continue
        labels = set(entry.labels)
        labels.add(acc_id) if accepted else labels.discard(acc_id)
        ent_reg.async_update_entity(eid, labels=labels)
    if accepted:
        for eid in entity_ids:
            ir.async_delete_issue(hass, DOMAIN, ISSUE_PREFIX + eid)


# ---- Top-Writers + Scan ---------------------------------------------------

async def _top_writers(
    hass: HomeAssistant, hours: float, limit: int, exclude_accepted: bool = False
) -> dict:
    """Top-DB-Schreiber der letzten <hours>h aus der states-Tabelle (über Recorder-Executor)."""
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
    return {"window_hours": hours, "count": len(writers), "writers": writers}


async def _scan_top_writers(hass: HomeAssistant, conf: dict) -> None:
    """Periodischer Scan: ungedrosselte, nicht-akzeptierte Vielschreiber als EIN Sammel-Repair."""
    try:
        window = float(conf.get(CONF_WINDOW, DEFAULTS[CONF_WINDOW]))
        res = await _top_writers(hass, window, 60)
    except Exception:  # noqa: BLE001
        _LOGGER.exception("recorder_throttle: Top-Writer-Scan fehlgeschlagen")
        return
    thr = float(conf.get(CONF_THRESHOLD, DEFAULTS[CONF_THRESHOLD]))
    policies = hass.data[DOMAIN]["policies"]
    accepted = _accepted_ids(hass)
    flagged = [
        w
        for w in res["writers"]
        if w["per_min"] >= thr and w["entity_id"] not in accepted and policies.get(w["entity_id"]) is None
    ]
    reg = ir.async_get(hass)
    for issue in list(reg.issues.values()):
        if issue.domain == DOMAIN and issue.issue_id.startswith(ISSUE_PREFIX):
            ir.async_delete_issue(hass, DOMAIN, issue.issue_id)
    if flagged:
        examples = ", ".join(w["name"] for w in flagged[:5]) + (" …" if len(flagged) > 5 else "")
        ir.async_create_issue(
            hass,
            DOMAIN,
            SUMMARY_ISSUE,
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key="top_writers_summary",
            translation_placeholders={"count": str(len(flagged)), "examples": examples, "threshold": str(int(thr))},
            learn_more_url="https://github.com/pos-ei-don/ha-recorder-throttle",
        )
    else:
        ir.async_delete_issue(hass, DOMAIN, SUMMARY_ISSUE)


# ---- Setup (YAML-Import + Config-Entry) -----------------------------------

async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """YAML (Altbestand) -> Config-Entry importieren."""
    if DOMAIN in config:
        hass.async_create_task(
            hass.config_entries.flow.async_init(
                DOMAIN, context={"source": SOURCE_IMPORT}, data=config.get(DOMAIN) or {}
            )
        )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Eigentliche Einrichtung pro Config-Entry."""
    settings = {**DEFAULTS, **dict(entry.options)}
    data = hass.data.setdefault(
        DOMAIN,
        {"enabled": True, "policies": {}, "last": {}, "orig": None, "rec": None, "stats": {"dropped": 0, "passed": 0}},
    )
    data["settings"] = settings

    _ensure_labels(hass)
    _rebuild_policies(hass)
    if not _install_hook(hass):
        _LOGGER.error("recorder_throttle: Hook NICHT installiert — Recorder läuft ungedrosselt (fail-safe)")
    entry.async_on_unload(lambda: _restore_hook(hass))

    @callback
    def _on_registry_update(_event) -> None:
        _rebuild_policies(hass)

    entry.async_on_unload(hass.bus.async_listen(er.EVENT_ENTITY_REGISTRY_UPDATED, _on_registry_update))
    entry.async_on_unload(hass.bus.async_listen(lr.EVENT_LABEL_REGISTRY_UPDATED, _on_registry_update))

    _register_services(hass)

    # Top-Writer-Scan -> Repair-Issues
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
    """Entladen — alle async_on_unload-Callbacks (Hook-Restore, Listener, Scan) laufen automatisch."""
    return True


async def _async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Options geändert -> Entry neu laden (neue Schwelle/Intervall greifen)."""
    await hass.config_entries.async_reload(entry.entry_id)


@callback
def _register_services(hass: HomeAssistant) -> None:
    """Services einmalig registrieren (überdauern Reload; Handler nutzen hass.data)."""
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
            _LOGGER.exception("recorder_throttle: top_writers fehlgeschlagen")
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
