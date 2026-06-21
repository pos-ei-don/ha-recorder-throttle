"""Repair flows for recorder_throttle — in-app fix instead of an external link."""
from __future__ import annotations

import voluptuous as vol

from homeassistant.components.repairs import RepairsFlow
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import issue_registry as ir

from .const import CONF_SCAN_ENABLED, DOMAIN

SUMMARY_ISSUE = "rt_top_writers_summary"


class TopWritersRepairFlow(RepairsFlow):
    """Let the user stop future heavy-writer reports from within the app."""

    async def async_step_init(self, user_input=None) -> FlowResult:
        return await self.async_step_confirm()

    async def async_step_confirm(self, user_input=None) -> FlowResult:
        if user_input is not None:
            # Turn the scan off in the (single) config entry's options. The entry's
            # update listener reloads it, and setup deletes the summary issue.
            entries = self.hass.config_entries.async_entries(DOMAIN)
            if entries:
                entry = entries[0]
                self.hass.config_entries.async_update_entry(
                    entry, options={**dict(entry.options), CONF_SCAN_ENABLED: False}
                )
            ir.async_delete_issue(self.hass, DOMAIN, SUMMARY_ISSUE)
            return self.async_create_entry(title="", data={})
        return self.async_show_form(step_id="confirm", data_schema=vol.Schema({}))


async def async_create_fix_flow(hass: HomeAssistant, issue_id: str, data) -> RepairsFlow:
    """Return the in-app repair flow for our fixable issues."""
    return TopWritersRepairFlow()
