"""Constants for recorder_throttle."""

DOMAIN = "recorder_throttle"

# Label NAME -> throttle interval in seconds. 0 = don't write to the DB at all.
# Matching is done by the label NAME (label_id is resolved at runtime), so it is
# robust regardless of how the slug is generated.
LABEL_INTERVALS = {
    "rec-off": 0,
    "rec-1min": 60,
    "rec-5min": 300,
    "rec-10min": 600,
}

# Label creation (name -> (color, icon)); colors from the HA label palette.
LABEL_META = {
    "rec-off": ("red", "mdi:database-off-outline"),
    "rec-1min": ("blue", "mdi:database-clock-outline"),
    "rec-5min": ("indigo", "mdi:database-clock-outline"),
    "rec-10min": ("deep-purple", "mdi:database-clock-outline"),
}

# Service keyword -> label name (None = "full" = no throttling, remove all rec-* labels).
POLICY_TO_NAME = {
    "full": None,
    "off": "rec-off",
    "1min": "rec-1min",
    "5min": "rec-5min",
    "10min": "rec-10min",
}

# "accepted" label — marks reviewed heavy writers that should NOT be reported again as
# new top writers (no throttling effect, purely a marker).
ACCEPTED_LABEL = "rec-accepted"
ACCEPTED_LABEL_META = ("green", "mdi:check-decagram")

# Scan/repair — options keys + defaults (UI settings dialog or YAML import)
CONF_SCAN_ENABLED = "scan_enabled"
CONF_THRESHOLD = "scan_threshold_per_min"
CONF_INTERVAL = "scan_interval_min"
CONF_WINDOW = "scan_window_hours"
# Auto-throttle: when on, the periodic scan applies a throttle policy to newly
# detected heavy writers automatically instead of only raising a Repairs notice.
CONF_AUTO_THROTTLE = "auto_throttle"
CONF_AUTO_POLICY = "auto_throttle_policy"   # one of "1min" / "5min" / "10min"
CONF_AUTO_SCOPE = "auto_throttle_scope"     # "sensor" = only sensor.* ; "all" = any domain

DEFAULT_SCAN_ENABLED = True
DEFAULT_THRESHOLD_PER_MIN = 5.0
DEFAULT_SCAN_INTERVAL_MIN = 60
DEFAULT_SCAN_WINDOW_HOURS = 1.0
DEFAULT_AUTO_THROTTLE = False
DEFAULT_AUTO_POLICY = "1min"
DEFAULT_AUTO_SCOPE = "sensor"

DEFAULTS = {
    CONF_SCAN_ENABLED: DEFAULT_SCAN_ENABLED,
    CONF_THRESHOLD: DEFAULT_THRESHOLD_PER_MIN,
    CONF_INTERVAL: DEFAULT_SCAN_INTERVAL_MIN,
    CONF_WINDOW: DEFAULT_SCAN_WINDOW_HOURS,
    CONF_AUTO_THROTTLE: DEFAULT_AUTO_THROTTLE,
    CONF_AUTO_POLICY: DEFAULT_AUTO_POLICY,
    CONF_AUTO_SCOPE: DEFAULT_AUTO_SCOPE,
}
