"""Konstanten für recorder_throttle."""

DOMAIN = "recorder_throttle"

# Label-NAME -> Throttle-Intervall in Sekunden. 0 = gar nicht in die DB schreiben.
# Matching erfolgt über den Label-NAMEN (label_id wird zur Laufzeit aufgelöst),
# damit es unabhängig von der Slug-Bildung robust ist.
LABEL_INTERVALS = {
    "rec-off": 0,
    "rec-1min": 60,
    "rec-5min": 300,
    "rec-10min": 600,
}

# Label-Anlage (Name -> (Farbe, Icon)); Farben aus der HA-Label-Palette.
LABEL_META = {
    "rec-off": ("red", "mdi:database-off-outline"),
    "rec-1min": ("blue", "mdi:database-clock-outline"),
    "rec-5min": ("indigo", "mdi:database-clock-outline"),
    "rec-10min": ("deep-purple", "mdi:database-clock-outline"),
}

# Service-Keyword -> Label-Name (None = "full" = keine Drosselung, alle rec-* entfernen).
POLICY_TO_NAME = {
    "full": None,
    "off": "rec-off",
    "1min": "rec-1min",
    "5min": "rec-5min",
    "10min": "rec-10min",
}

# Label „akzeptiert" — markiert geprüfte Vielschreiber, die NICHT erneut als neuer
# Top-Writer gemeldet werden sollen (kein Throttle-Effekt, reine Markierung).
ACCEPTED_LABEL = "rec-accepted"
ACCEPTED_LABEL_META = ("green", "mdi:check-decagram")

# Scan/Repair — Options-Keys + Defaults (UI-Einstellungsdialog bzw. YAML-Import)
CONF_SCAN_ENABLED = "scan_enabled"
CONF_THRESHOLD = "scan_threshold_per_min"
CONF_INTERVAL = "scan_interval_min"
CONF_WINDOW = "scan_window_hours"

DEFAULT_SCAN_ENABLED = True
DEFAULT_THRESHOLD_PER_MIN = 5.0
DEFAULT_SCAN_INTERVAL_MIN = 60
DEFAULT_SCAN_WINDOW_HOURS = 1.0

DEFAULTS = {
    CONF_SCAN_ENABLED: DEFAULT_SCAN_ENABLED,
    CONF_THRESHOLD: DEFAULT_THRESHOLD_PER_MIN,
    CONF_INTERVAL: DEFAULT_SCAN_INTERVAL_MIN,
    CONF_WINDOW: DEFAULT_SCAN_WINDOW_HOURS,
}
