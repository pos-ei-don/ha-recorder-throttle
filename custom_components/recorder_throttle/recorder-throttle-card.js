/* recorder-throttle-card — management card for the recorder_throttle integration.
 * Localized: shows German when Home Assistant's language is German, English otherwise.
 * Tabs:
 *   Unthrottled — frequent writers (top_writers, policy=full), with live rate/min
 *   Throttled   — all entities with a rec-* policy (1/5/10min/off), from labels
 *   Accepted    — all entities with rec-accepted
 * Each row: name (click = more-info) · rate · statistics badge · policy switch · accept toggle.
 * Config:  type: custom:recorder-throttle-card  | title | hours:1 | limit:30
 */
const RT_POL = [
  { k: "full", c: "voll" },
  { k: "1min", c: "m1" },
  { k: "5min", c: "m5" },
  { k: "10min", c: "m10" },
  { k: "off", c: "aus" },
];
const RT_LABEL_POL = { rec_off: "off", rec_10min: "10min", rec_5min: "5min", rec_1min: "1min" };
const RT_TAB_IDS = ["unthrottled", "throttled", "accepted"];

const RT_I18N = {
  en: {
    header: "Recorder Throttle",
    tab_unthrottled: "Unthrottled", tab_throttled: "Throttled", tab_accepted: "Accepted",
    pol_full: "Full", pol_1min: "1m", pol_5min: "5m", pol_10min: "10m", pol_off: "Off",
    stats_kept: "stats kept", no_backup: "no backup",
    loading: "Loading …", no_entries: "No entries.",
    more_info: "More info",
    acc_btn: "✓ acc.", acc_title: "Mark as accepted heavy writer (stop reporting)",
  },
  de: {
    header: "Recorder-Drosselung",
    tab_unthrottled: "Ungedrosselt", tab_throttled: "Gedrosselt", tab_accepted: "Akzeptiert",
    pol_full: "Voll", pol_1min: "1m", pol_5min: "5m", pol_10min: "10m", pol_off: "Aus",
    stats_kept: "5-min bleibt", no_backup: "kein Backup",
    loading: "Lade …", no_entries: "Keine Einträge.",
    more_info: "Mehr Infos",
    acc_btn: "✓ akz.", acc_title: "Als akzeptierten Vielschreiber markieren",
  },
};

class RecorderThrottleCard extends HTMLElement {
  setConfig(config) {
    this._config = config;
    this._hours = config.hours || 1;
    this._limit = config.limit || 30;
    this._tab = "unthrottled";
    this._data = null;
    this._built = false;
    this.innerHTML = "";
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._built) this._build();
    if (!this._fetched) {
      this._fetched = true;
      this._fetch();
    }
    this._render();
  }

  _lang() {
    const h = this._hass || {};
    const l = (h.locale && h.locale.language) || h.language || "en";
    return String(l).toLowerCase().split("-")[0];
  }
  _t(key) {
    const t = RT_I18N[this._lang()] || RT_I18N.en;
    return t[key] != null ? t[key] : RT_I18N.en[key];
  }

  connectedCallback() {
    if (!this._timer) this._timer = setInterval(() => this._fetch(), 120000);
  }
  disconnectedCallback() {
    if (this._timer) {
      clearInterval(this._timer);
      this._timer = null;
    }
  }

  async _fetch() {
    if (!this._hass || this._fetching) return;
    this._fetching = true;
    try {
      const r = await this._hass.callWS({
        type: "call_service",
        domain: "recorder_throttle",
        service: "top_writers",
        service_data: { hours: this._hours, limit: this._limit, exclude_accepted: true },
        return_response: true,
      });
      this._data = ((r && r.response) || {}).writers || [];
      this._render();
    } catch (e) {
      /* building / no perms */
    } finally {
      this._fetching = false;
    }
  }

  _stat(eid) {
    const st = this._hass.states[eid];
    return !!(st && st.attributes && st.attributes.state_class);
  }
  _esc(s) {
    return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }
  _name(eid, fallback) {
    const st = this._hass.states[eid];
    return (st && st.attributes && st.attributes.friendly_name) || fallback || eid;
  }
  _polFromLabels(labels) {
    for (const lid of labels || []) if (RT_LABEL_POL[lid]) return RT_LABEL_POL[lid];
    return "full";
  }
  _rateMap() {
    const m = {};
    (this._data || []).forEach((w) => (m[w.entity_id] = w.per_min));
    return m;
  }

  _unthrottledRows() {
    return (this._data || []).filter((w) => (w.policy || "full") === "full").map((w) => ({ ...w }));
  }
  _byLabel(predicate) {
    const ents = this._hass.entities || {};
    const rm = this._rateMap();
    const out = [];
    for (const eid in ents) {
      const labels = ents[eid].labels || [];
      const pol = this._polFromLabels(labels);
      const acc = labels.includes("rec_accepted");
      if (!predicate(pol, acc)) continue;
      out.push({ entity_id: eid, name: this._name(eid), policy: pol, accepted: acc, per_min: rm[eid], has_statistics: this._stat(eid) });
    }
    return out.sort((a, b) => a.entity_id.localeCompare(b.entity_id));
  }
  _throttledRows() {
    return this._byLabel((pol) => pol !== "full");
  }
  _acceptedRows() {
    return this._byLabel((pol, acc) => acc);
  }
  _rowsFor(tab) {
    if (tab === "throttled") return this._throttledRows();
    if (tab === "accepted") return this._acceptedRows();
    return this._unthrottledRows();
  }

  _build() {
    const card = document.createElement("ha-card");
    card.header = this._config.title || this._t("header");
    const style = document.createElement("style");
    style.textContent = `
      .rt-tabs{display:flex;gap:6px;padding:6px 12px 4px;flex-wrap:wrap}
      .rt-tabs button{appearance:none;border:1px solid var(--divider-color,#2c333b);background:transparent;color:var(--secondary-text-color,#9aa7b4);border-radius:18px;padding:5px 12px;font:inherit;font-size:13px;cursor:pointer}
      .rt-tabs button.on{background:var(--primary-color,#1f6feb);color:#fff;border-color:transparent;font-weight:600}
      .rt-wrap{padding:2px 12px 12px}
      .rt-row{display:flex;align-items:center;gap:10px;padding:8px 0;border-bottom:1px solid var(--divider-color,#2c333b)}
      .rt-row:last-child{border-bottom:none}
      .rt-info{flex:1;min-width:0;cursor:pointer}
      .rt-info:hover .rt-name{color:var(--primary-color,#1f6feb);text-decoration:underline}
      .rt-name{font-weight:500;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
      .rt-sub{font-size:12px;color:var(--secondary-text-color,#9aa7b4);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
      .rt-rate{font-variant-numeric:tabular-nums;color:var(--primary-text-color)}
      .rt-stat{font-size:11px;border-radius:10px;padding:1px 6px;margin-left:4px}
      .rt-stat.y{background:#0d2818;color:#3fb950}.rt-stat.n{background:#2a2d31;color:#9aa7b4}
      .rt-seg{display:inline-flex;border:1px solid var(--divider-color,#2c333b);border-radius:8px;overflow:hidden;flex:none}
      .rt-seg button{appearance:none;border:none;background:transparent;color:var(--secondary-text-color,#9aa7b4);font:inherit;font-size:13px;padding:5px 9px;cursor:pointer;border-right:1px solid var(--divider-color,#2c333b)}
      .rt-seg button:last-child{border-right:none}
      .rt-seg button.on{color:#fff;font-weight:600}
      .rt-seg button.on.voll{background:#1f6feb}.rt-seg button.on.m1{background:#1a7f37}
      .rt-seg button.on.m5{background:#9e6a03}.rt-seg button.on.m10{background:#6e40c9}.rt-seg button.on.aus{background:#8b1a1a}
      .rt-acc{appearance:none;border:1px solid var(--divider-color,#2c333b);background:transparent;color:var(--secondary-text-color,#9aa7b4);border-radius:8px;padding:5px 8px;cursor:pointer;flex:none}
      .rt-acc.on{background:#143b1f;color:#3fb950;border-color:#1c4a2c}
      .rt-empty{padding:14px 4px;color:var(--secondary-text-color,#9aa7b4)}
    `;
    const tabs = document.createElement("div");
    tabs.className = "rt-tabs";
    tabs.addEventListener("click", (e) => {
      const b = e.target.closest("[data-tab]");
      if (b) {
        this._tab = b.dataset.tab;
        this._render();
      }
    });
    const wrap = document.createElement("div");
    wrap.className = "rt-wrap";
    wrap.addEventListener("click", (e) => this._onClick(e));
    card.appendChild(style);
    card.appendChild(tabs);
    card.appendChild(wrap);
    this.appendChild(card);
    this._tabsEl = tabs;
    this._wrap = wrap;
    this._built = true;
  }

  _onClick(e) {
    const el = e.target.closest("[data-act]");
    if (!el) return;
    const eid = el.dataset.eid;
    if (el.dataset.act === "info") {
      this.dispatchEvent(new CustomEvent("hass-more-info", { detail: { entityId: eid }, bubbles: true, composed: true }));
    } else if (el.dataset.act === "policy") {
      this._hass.callService("recorder_throttle", "set_policy", { entity_id: eid, policy: el.dataset.k });
      setTimeout(() => this._fetch(), 1500);
    } else if (el.dataset.act === "accept") {
      this._hass.callService("recorder_throttle", "set_accepted", { entity_id: eid, accepted: el.dataset.acc !== "1" });
      setTimeout(() => this._fetch(), 1500);
    }
  }

  _render() {
    if (!this._wrap) return;
    const counts = {
      unthrottled: this._unthrottledRows().length,
      throttled: this._throttledRows().length,
      accepted: this._acceptedRows().length,
    };
    this._tabsEl.innerHTML = RT_TAB_IDS.map(
      (id) => `<button data-tab="${id}" class="${id === this._tab ? "on" : ""}">${this._t("tab_" + id)} (${counts[id]})</button>`
    ).join("");

    const rows = this._rowsFor(this._tab);
    if (!rows.length) {
      this._wrap.innerHTML = `<div class="rt-empty">${this._data === null && this._tab === "unthrottled" ? this._t("loading") : this._t("no_entries")}</div>`;
      return;
    }
    this._wrap.innerHTML = rows
      .map((w) => {
        const eid = w.entity_id;
        const pol = w.policy || "full";
        const rate = w.per_min != null ? `${w.per_min}/min` : "—";
        const stat = w.has_statistics;
        const statBadge = stat === undefined ? "" : `<span class="rt-stat ${stat ? "y" : "n"}">${stat ? this._t("stats_kept") : this._t("no_backup")}</span>`;
        const seg = RT_POL.map(
          (p) => `<button data-act="policy" data-eid="${eid}" data-k="${p.k}" class="${p.c}${p.k === pol ? " on" : ""}">${this._t("pol_" + p.k)}</button>`
        ).join("");
        const acc = !!w.accepted;
        return `<div class="rt-row">
          <div class="rt-info" data-act="info" data-eid="${eid}" title="${this._t("more_info")}">
            <div class="rt-name">${this._esc(this._name(eid, w.name))}</div>
            <div class="rt-sub"><span class="rt-rate">${rate}</span> · ${eid}${statBadge}</div>
          </div>
          <div class="rt-seg">${seg}</div>
          <button class="rt-acc${acc ? " on" : ""}" data-act="accept" data-eid="${eid}" data-acc="${acc ? "1" : "0"}" title="${this._t("acc_title")}">${this._t("acc_btn")}</button>
        </div>`;
      })
      .join("");
  }

  getCardSize() {
    return 2 + Math.ceil((this._rowsFor(this._tab).length || 6) * 0.6);
  }
  static getStubConfig() {
    return { hours: 1, limit: 30 };
  }
}

if (!customElements.get("recorder-throttle-card")) {
  customElements.define("recorder-throttle-card", RecorderThrottleCard);
  window.customCards = window.customCards || [];
  window.customCards.push({
    type: "recorder-throttle-card",
    name: "Recorder Throttle",
    description: "Throttle per-entity recorder DB writes (EN/DE)",
    documentationURL: "https://github.com/pos-ei-don/ha-recorder-throttle",
  });
  console.info("%c recorder-throttle-card %c v0.4.0 ", "background:#1f6feb;color:#fff", "");
}
