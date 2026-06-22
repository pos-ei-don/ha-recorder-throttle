"""Config and options flow for recorder_throttle (settings dialog)."""
from __future__ import annotations

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.data_entry_flow import section

from .const import (
    CONF_AUTO_POLICY,
    CONF_AUTO_SCOPE,
    CONF_AUTO_THROTTLE,
    CONF_INTERVAL,
    CONF_SCAN_ENABLED,
    CONF_THRESHOLD,
    CONF_WINDOW,
    DEFAULTS,
    DOMAIN,
)

# UI groups the settings into two collapsible sections ("boxes"). The returned
# user_input is therefore nested per section; _flatten() maps it back to the flat
# option keys the rest of the integration uses, so stored options stay unchanged.
_SECTIONS = ("scan", "auto")


def _schema(opts: dict) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required("scan"): section(
                vol.Schema(
                    {
                        vol.Optional(
                            CONF_SCAN_ENABLED, default=opts.get(CONF_SCAN_ENABLED, DEFAULTS[CONF_SCAN_ENABLED])
                        ): bool,
                        vol.Optional(
                            CONF_THRESHOLD, default=opts.get(CONF_THRESHOLD, DEFAULTS[CONF_THRESHOLD])
                        ): vol.All(vol.Coerce(float), vol.Range(min=0)),
                        vol.Optional(
                            CONF_INTERVAL, default=opts.get(CONF_INTERVAL, DEFAULTS[CONF_INTERVAL])
                        ): vol.All(vol.Coerce(int), vol.Range(min=1)),
                        vol.Optional(
                            CONF_WINDOW, default=opts.get(CONF_WINDOW, DEFAULTS[CONF_WINDOW])
                        ): vol.All(vol.Coerce(float), vol.Range(min=0, min_included=False)),
                    }
                ),
                {"collapsed": False},
            ),
            vol.Required("auto"): section(
                vol.Schema(
                    {
                        vol.Optional(
                            CONF_AUTO_THROTTLE, default=opts.get(CONF_AUTO_THROTTLE, DEFAULTS[CONF_AUTO_THROTTLE])
                        ): bool,
                        vol.Optional(
                            CONF_AUTO_POLICY, default=opts.get(CONF_AUTO_POLICY, DEFAULTS[CONF_AUTO_POLICY])
                        ): vol.In(["1min", "5min", "10min"]),
                        vol.Optional(
                            CONF_AUTO_SCOPE, default=opts.get(CONF_AUTO_SCOPE, DEFAULTS[CONF_AUTO_SCOPE])
                        ): vol.In(["sensor", "all"]),
                    }
                ),
                {"collapsed": False},
            ),
            # Info/help box at the bottom — empty schema, text comes from the section
            # name/description in the translations. Collapsed so it stays unobtrusive.
            vol.Optional("info"): section(vol.Schema({}), {"collapsed": True}),
        }
    )


def _flatten(user_input: dict) -> dict:
    """Collapse the per-section nested input back to flat option keys."""
    flat: dict = {}
    for sec in _SECTIONS:
        val = user_input.get(sec)
        if isinstance(val, dict):
            flat.update(val)
    return flat


class RecorderThrottleConfigFlow(ConfigFlow, domain=DOMAIN):
    """Setup (single instance) + YAML import."""

    VERSION = 1

    async def async_step_user(self, user_input=None) -> ConfigFlowResult:
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")
        if user_input is not None:
            return self.async_create_entry(title="Recorder Throttle", data={}, options=_flatten(user_input))
        return self.async_show_form(step_id="user", data_schema=_schema({}))

    async def async_step_import(self, user_input: dict) -> ConfigFlowResult:
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")
        opts = {k: user_input.get(k, v) for k, v in DEFAULTS.items()}
        return self.async_create_entry(title="Recorder Throttle (YAML)", data={}, options=opts)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return RecorderThrottleOptionsFlow()


class RecorderThrottleOptionsFlow(OptionsFlow):
    """Settings dialog (scan/report + auto-throttle), grouped into two sections."""

    async def async_step_init(self, user_input=None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=_flatten(user_input))
        return self.async_show_form(step_id="init", data_schema=_schema(dict(self.config_entry.options)))
