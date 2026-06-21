"""Repair flows for recorder_throttle — in-app fix that actually solves the problem."""
from __future__ import annotations

from homeassistant.components.repairs import RepairsFlow
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import issue_registry as ir

from .const import CONF_THRESHOLD, CONF_WINDOW, CONF_SCAN_ENABLED, DEFAULTS, DOMAIN

SUMMARY_ISSUE = "rt_top_writers_summary"


class TopWritersRepairFlow(RepairsFlow):
    """Resolve the heavy-writers notice in-app: throttle / accept / mute."""

    async def async_step_init(self, user_input=None) -> FlowResult:
        return self.async_show_menu(
            step_id="init",
            menu_options=["throttle_1min", "throttle_5min", "accept_all", "stop_reporting"],
        )

    async def _flagged(self) -> list[str]:
        """Recompute the currently reported (unthrottled, over-threshold) entities."""
        settings = (self.hass.data.get(DOMAIN) or {}).get("settings", {})
        window = float(settings.get(CONF_WINDOW, DEFAULTS[CONF_WINDOW]))
        thr = float(settings.get(CONF_THRESHOLD, DEFAULTS[CONF_THRESHOLD]))
        try:
            resp = await self.hass.services.async_call(
                DOMAIN, "top_writers",
                {"hours": window, "limit": 100, "exclude_accepted": True},
                blocking=True, return_response=True,
            )
        except Exception:  # noqa: BLE001
            return []
        writers = (resp or {}).get("writers", []) or []
        return [
            w["entity_id"] for w in writers
            if (w.get("policy") or "full") == "full" and (w.get("per_min") or 0) >= thr
        ]

    async def _finish(self) -> FlowResult:
        ir.async_delete_issue(self.hass, DOMAIN, SUMMARY_ISSUE)
        return self.async_create_entry(title="", data={})

    async def _set_policy(self, policy: str) -> FlowResult:
        ids = await self._flagged()
        if ids:
            await self.hass.services.async_call(
                DOMAIN, "set_policy", {"entity_id": ids, "policy": policy}, blocking=True
            )
        return await self._finish()

    async def async_step_throttle_1min(self, user_input=None) -> FlowResult:
        return await self._set_policy("1min")

    async def async_step_throttle_5min(self, user_input=None) -> FlowResult:
        return await self._set_policy("5min")

    async def async_step_accept_all(self, user_input=None) -> FlowResult:
        ids = await self._flagged()
        if ids:
            await self.hass.services.async_call(
                DOMAIN, "set_accepted", {"entity_id": ids, "accepted": True}, blocking=True
            )
        return await self._finish()

    async def async_step_stop_reporting(self, user_input=None) -> FlowResult:
        entries = self.hass.config_entries.async_entries(DOMAIN)
        if entries:
            entry = entries[0]
            self.hass.config_entries.async_update_entry(
                entry, options={**dict(entry.options), CONF_SCAN_ENABLED: False}
            )
        return await self._finish()


async def async_create_fix_flow(hass: HomeAssistant, issue_id: str, data) -> RepairsFlow:
    return TopWritersRepairFlow()
