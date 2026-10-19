/* recorder-throttle-card — management card for the recorder_throttle integration.
 * Localized: shows German when Home Assistant's language is German, English otherwise.
 * Tabs:
 *   Unthrottled — frequent writers (top_writers, policy=full), with live rate/min
 *   Throttled   — all entities with a rec-* policy (1/5/10min/off), from labels
 *   Accepted    — all entities with rec-accepted
 * Each row: name (click = more-info) · rate · statistics badge · policy switch · accept toggle.
 * Throttled/Accepted also get a live sort toggle (entity ID / friendly name) and all three
 * tabs are paginated (page_size, default 50) — Unthrottled keeps its rate-first order.
 * Throttled also gets an All/Unaccepted filter, so newly-throttled entities are easy to
 * find and accept without paging through everything already reviewed.
 * Config:  type: custom:recorder-throttle-card  | title | hours:1 | limit:30 | page_size:50 | sort:"entity_id"|"friendly_name"
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
const RT_POLL_MS = 120000;

const RT_I18N = {
  en: {
    header: "Recorder Throttle",
    tab_unthrottled: "Unthrottled", tab_throttled: "Throttled", tab_accepted: "Accepted",
    pol_full: "Full", pol_1min: "1m", pol_5min: "5m", pol_10min: "10m", pol_off: "Off",
    stats_kept: "stats kept", no_backup: "no backup",
    loading: "Loading …", no_entries: "No entries.",
    more_info: "More info",
    acc_btn: "✓ acc.", acc_title: "Mark as accepted heavy writer (stop reporting)",
    all_to: "All to", bulk_confirm: "Throttle all {n} entities in this list (all pages) to {p}?",
    not_throttleable: "not throttleable",
    not_throttleable_title: "No unique ID: this entity is not in the entity registry, so it can't carry labels. Use the recorder's exclude: configuration instead.",
    sort_label: "Sort:", sort_entity_id: "Entity ID", sort_friendly_name: "Friendly name",
    page_prev: "Previous page", page_next: "Next page", page_info: "Page {page} of {pages}",
    filter_label: "Show:", filter_all: "All", filter_unaccepted: "Unaccepted",
  },
  de: {
    header: "Recorder-Drosselung",
    tab_unthrottled: "Ungedrosselt", tab_throttled: "Gedrosselt", tab_accepted: "Akzeptiert",
    pol_full: "Voll", pol_1min: "1m", pol_5min: "5m", pol_10min: "10m", pol_off: "Aus",
    stats_kept: "5-min bleibt", no_backup: "kein Backup",
    loading: "Lade …", no_entries: "Keine Einträge.",
    more_info: "Mehr Infos",
    acc_btn: "✓ akz.", acc_title: "Als akzeptierten Vielschreiber markieren",
    all_to: "Alle auf", bulk_confirm: "Alle {n} Entities dieser Liste (alle Seiten) auf {p} drosseln?",
    not_throttleable: "nicht drosselbar",
    not_throttleable_title: "Keine eindeutige ID: Die Entity steht nicht in der Entity-Registry und kann daher keine Labels tragen. Stattdessen exclude: in der Recorder-Konfiguration verwenden.",
    sort_label: "Sortieren:", sort_entity_id: "Entity-ID", sort_friendly_name: "Anzeigename",
    page_prev: "Vorherige Seite", page_next: "Nächste Seite", page_info: "Seite {page} von {pages}",
    filter_label: "Anzeigen:", filter_all: "Alle", filter_unaccepted: "Nicht akzeptiert",
  },
};

class RecorderThrottleCard extends HTMLElement {
  constructor() {
    super();
    // Never replace the DOM between pointerdown and click: HA hands out a new `hass` on every
    // state change, and a re-render in that gap swallowed clicks on large installations (#2).
    this._endInteraction = () => {
      clearTimeout(this._interactionTimer);
      // Keep the guard through all synchronous click handlers.
      this._interactionTimer = setTimeout(() => {
        this._interacting = false;
        if (this._renderPending) this._render();
      }, 0);
    };
    this._pointerUp = (e) => {
      const path = e.composedPath();
      if (!path.includes(this) || path.some((node) => node.disabled)) {
        this._endInteraction();
      } else {
        // An inside release normally produces a click; if none follows, don't stay frozen.
        clearTimeout(this._interactionTimer);
        this._interactionTimer = setTimeout(this._endInteraction, 1000);
      }
    };
    this._visibilityChanged = () => this._updatePolling();
    this.addEventListener("pointerdown", () => {
      clearTimeout(this._interactionTimer);
      this._interacting = true;
    }, true);
    this.addEventListener("click", this._endInteraction, true);
    this.addEventListener("dragend", this._endInteraction, true);
  }

  setConfig(config) {
    this._config = config;
    this._hours = config.hours || 1;
    this._limit = RecorderThrottleCard._posInt(config.limit, 30);
    this._pageSize = RecorderThrottleCard._posInt(config.page_size, 50);
    this._sort = config.sort === "friendly_name" ? "friendly_name" : "entity_id";
    this._page = { unthrottled: 1, throttled: 1, accepted: 1 };
    this._hideAccepted = false;
    this._rowH = null;
    this._tab = "unthrottled";
    this._data = null;
    this._built = false;
    this._shownStates = null;
    this.innerHTML = "";
  }

  static _posInt(v, dflt) {
    // YAML may hand over "50" as a string; "50" + 50 would concatenate when paging.
    const n = parseInt(v, 10);
    return Number.isFinite(n) && n > 0 ? n : dflt;
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._built) this._build();
    this._updatePolling();
    // Re-render only if something the card shows changed: the entity registry (labels live
    // there), the language, or a state object of a row on the visible page (#2).
    const localeKey = JSON.stringify([hass.language, hass.locale]);
    if (!this._shownStates || this._renderedEntities !== hass.entities || this._renderedLocale !== localeKey) {
      this._render();
      return;
    }
    for (const [eid, state] of this._shownStates) {
      if (hass.states[eid] !== state) {
        this._render();
        break;
      }
    }
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
    document.addEventListener("visibilitychange", this._visibilityChanged);
    window.addEventListener("pointerup", this._pointerUp, true);
    window.addEventListener("pointercancel", this._endInteraction, true);
    window.addEventListener("blur", this._endInteraction);
    this._intersecting = !("IntersectionObserver" in window);
    if (!this._observer && "IntersectionObserver" in window) {
      this._observer = new IntersectionObserver((entries) => {
        if (!this.isConnected) return;
        this._intersecting = entries[entries.length - 1].isIntersecting;
        this._updatePolling();
      });
      this._observer.observe(this);
    }
    this._updatePolling();
    if (this._renderPending) this._render();
  }
  disconnectedCallback() {
    document.removeEventListener("visibilitychange", this._visibilityChanged);
    window.removeEventListener("pointerup", this._pointerUp, true);
    window.removeEventListener("pointercancel", this._endInteraction, true);
    window.removeEventListener("blur", this._endInteraction);
    if (this._observer) this._observer.disconnect();
    this._observer = null;
    this._intersecting = false;
    clearInterval(this._timer);
    this._timer = null;
    clearTimeout(this._interactionTimer);
    this._interacting = false;
  }

  // top_writers is a heavy recorder query: poll only while the card is connected and visible
  // (tab in the foreground, card on screen); catch up once when it becomes visible again (#2).
  _canPoll() {
    return this.isConnected && document.visibilityState === "visible" && this._intersecting;
  }
  _updatePolling() {
    if (!this._canPoll()) {
      clearInterval(this._timer);
      this._timer = null;
      return;
    }
    if (!this._timer) this._timer = setInterval(() => this._fetch(), RT_POLL_MS);
    if (this._refreshPending || !this._lastFetch || Date.now() - this._lastFetch >= RT_POLL_MS) this._fetch();
  }
  _refetchAfterAction() {
    setTimeout(() => {
      // Kept as pending if the card is hidden or another fetch is running.
      this._refreshPending = true;
      this._fetch();
    }, 1500);
  }

  async _fetch() {
    if (!this._hass || this._fetching || !this._canPoll()) return;
    this._fetching = true;
    this._refreshPending = false;
    // Also rate-limits failed attempts; otherwise every hass update could retry.
    this._lastFetch = Date.now();
    try {
      const call = (extra) => this._hass.callWS({
        type: "call_service",
        domain: "recorder_throttle",
        service: "top_writers",
        service_data: { hours: this._hours, limit: this._limit, exclude_accepted: true, ...extra },
        return_response: true,
      });
      // Unthrottled list: throttled entities excluded BEFORE the limit (else they can fill it);
      // rates for the throttled tabs come from the unfiltered query.
      const [u, all] = await Promise.all([call({ exclude_throttled: true }), call({})]);
      this._data = ((u && u.response) || {}).writers || [];
      this._rates = ((all && all.response) || {}).writers || [];
      this._render();
    } catch (e) {
      /* building / no perms */
    } finally {
      this._fetching = false;
      if (this._refreshPending) this._fetch();
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
    [...(this._rates || []), ...(this._data || [])].forEach((w) => (m[w.entity_id] = w.per_min));
    return m;
  }

  _unthrottledRows() {
    return (this._data || []).filter((w) => (w.policy || "full") === "full").map((w) => ({ ...w }));
  }
  // Label membership only changes with the entity registry: cache it per hass.entities object.
  _labelLists() {
    const entities = this._hass && this._hass.entities;
    if (!this._labelCache || this._labelEntities !== entities) {
      const lists = { throttled: [], accepted: [] };
      const ents = entities || {};
      for (const eid in ents) {
        const labels = ents[eid].labels || [];
        const policy = this._polFromLabels(labels);
        const accepted = labels.includes("rec_accepted");
        const row = { entity_id: eid, policy, accepted };
        if (policy !== "full") lists.throttled.push(row);
        if (accepted) lists.accepted.push(row);
      }
      this._labelEntities = entities;
      this._labelCache = lists;
    }
    return this._labelCache;
  }
  _byLabel(tab) {
    const rm = this._rateMap();
    return this._labelLists()[tab].map((row) => ({
      ...row,
      name: this._name(row.entity_id),
      per_min: rm[row.entity_id],
      has_statistics: this._stat(row.entity_id),
    }));
  }
  _sortRows(rows) {
    const key = this._sort === "friendly_name" ? "name" : "entity_id";
    return rows.slice().sort((a, b) => String(a[key]).localeCompare(String(b[key])));
  }
  _throttledRows(ignoreFilter) {
    const rows = this._byLabel("throttled");
    const filtered = this._hideAccepted && !ignoreFilter ? rows.filter((w) => !w.accepted) : rows;
    return this._sortRows(filtered);
  }
  _acceptedRows() {
    return this._sortRows(this._byLabel("accepted"));
  }
  _rowsFor(tab) {
    if (tab === "throttled") return this._throttledRows();
    if (tab === "accepted") return this._acceptedRows();
    return this._unthrottledRows();
  }
  _paginate(rows, tabId) {
    const size = this._pageSize;
    const pages = Math.max(1, Math.ceil(rows.length / size));
    let page = this._page[tabId] || 1;
    if (page > pages) page = pages;
    if (page < 1) page = 1;
    this._page[tabId] = page;
    const start = (page - 1) * size;
    return { rows: rows.slice(start, start + size), page, pages };
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
      .rt-row:last-child{border-bottom-color:transparent}
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
      .rt-bulk{display:flex;align-items:center;gap:8px;padding:6px 0 8px;border-bottom:1px solid var(--divider-color,#2c333b);margin-bottom:4px;font-size:13px;color:var(--secondary-text-color,#9aa7b4)}
      .rt-bulk button{appearance:none;border:1px solid var(--divider-color,#2c333b);background:transparent;color:var(--primary-text-color);border-radius:8px;padding:4px 12px;font:inherit;font-size:13px;cursor:pointer}
      .rt-bulk button.m1:hover{border-color:#1a7f37;color:#3fb950}.rt-bulk button.m5:hover{border-color:#9e6a03;color:#d29922}
      .rt-sort{display:flex;flex-wrap:wrap;align-items:center;gap:8px;padding:6px 0 8px;border-bottom:1px solid var(--divider-color,#2c333b);margin-bottom:4px;font-size:13px;color:var(--secondary-text-color,#9aa7b4)}
      .rt-sort .rt-seg button.on{background:var(--primary-color,#1f6feb)}
      .rt-na{flex:none;font-size:12px;color:var(--secondary-text-color,#9aa7b4);border:1px dashed var(--divider-color,#2c333b);border-radius:8px;padding:5px 9px;cursor:help}
      .rt-pager{display:flex;align-items:center;justify-content:center;gap:12px;padding:10px 0 2px}
      .rt-pager button{appearance:none;border:1px solid var(--divider-color,#2c333b);background:transparent;color:var(--primary-text-color);border-radius:8px;padding:4px 10px;font:inherit;font-size:14px;cursor:pointer;line-height:1}
      .rt-pager button:disabled{opacity:.35;cursor:default}
      .rt-pager-info{font-size:12px;color:var(--secondary-text-color,#9aa7b4);min-width:70px;text-align:center}
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
      this._refetchAfterAction();
    } else if (el.dataset.act === "accept") {
      this._hass.callService("recorder_throttle", "set_accepted", { entity_id: eid, accepted: el.dataset.acc !== "1" });
      this._refetchAfterAction();
    } else if (el.dataset.act === "bulk") {
      const ids = this._unthrottledRows().filter((w) => w.in_registry !== false).map((w) => w.entity_id);
      if (!ids.length) return;
      const msg = this._t("bulk_confirm").replace("{n}", ids.length).replace("{p}", this._t("pol_" + el.dataset.k));
      if (!window.confirm(msg)) return;
      this._hass.callService("recorder_throttle", "set_policy", { entity_id: ids, policy: el.dataset.k });
      this._refetchAfterAction();
    } else if (el.dataset.act === "sort") {
      if (el.dataset.k !== this._sort) {
        this._sort = el.dataset.k;
        this._page.throttled = 1;
        this._page.accepted = 1;
        this._render();
      }
    } else if (el.dataset.act === "filter") {
      const wantHide = el.dataset.k === "unaccepted";
      if (wantHide !== this._hideAccepted) {
        this._hideAccepted = wantHide;
        this._page.throttled = 1;
        this._render();
      }
    } else if (el.dataset.act === "page") {
      const cur = this._page[this._tab] || 1;
      this._page[this._tab] = el.dataset.dir === "next" ? cur + 1 : Math.max(1, cur - 1);
      this._render();
    }
  }

  _renderSortToggle() {
    const modes = [
      { k: "entity_id", label: this._t("sort_entity_id") },
      { k: "friendly_name", label: this._t("sort_friendly_name") },
    ];
    const btns = modes
      .map((m) => `<button data-act="sort" data-k="${m.k}" class="${m.k === this._sort ? "on" : ""}">${m.label}</button>`)
      .join("");
    return `<span>${this._t("sort_label")}</span><div class="rt-seg">${btns}</div>`;
  }
  _renderAcceptedFilter() {
    const modes = [
      { k: "all", label: this._t("filter_all") },
      { k: "unaccepted", label: this._t("filter_unaccepted") },
    ];
    const cur = this._hideAccepted ? "unaccepted" : "all";
    const btns = modes
      .map((m) => `<button data-act="filter" data-k="${m.k}" class="${m.k === cur ? "on" : ""}">${m.label}</button>`)
      .join("");
    return `<span>${this._t("filter_label")}</span><div class="rt-seg">${btns}</div>`;
  }
  _renderPager(page, pages) {
    return `<div class="rt-pager">
      <button data-act="page" data-dir="prev" ${page <= 1 ? "disabled" : ""} title="${this._t("page_prev")}">‹</button>
      <span class="rt-pager-info">${this._t("page_info").replace("{page}", page).replace("{pages}", pages)}</span>
      <button data-act="page" data-dir="next" ${page >= pages ? "disabled" : ""} title="${this._t("page_next")}">›</button>
    </div>`;
  }

  _render() {
    if (!this._wrap) return;
    if (this._interacting) {
      this._renderPending = true;
      return;
    }
    this._renderPending = false;
    this._renderedEntities = this._hass.entities;
    this._renderedLocale = JSON.stringify([this._hass.language, this._hass.locale]);
    this._shownStates = new Map();
    this.querySelector("ha-card").header = this._config.title || this._t("header");
    const labels = this._labelLists();
    const counts = {
      unthrottled: this._unthrottledRows().length,
      throttled: this._hideAccepted ? labels.throttled.filter((w) => !w.accepted).length : labels.throttled.length,
      accepted: labels.accepted.length,
    };
    // When the Throttled filter is hiding accepted entities, show "shown/total" in the
    // tab label instead of just the filtered count — otherwise it looks like entities
    // went missing rather than just being filtered out of view.
    const throttledLabel = this._hideAccepted ? `${counts.throttled}/${labels.throttled.length}` : counts.throttled;
    this._tabsEl.innerHTML = RT_TAB_IDS.map(
      (id) => `<button data-tab="${id}" class="${id === this._tab ? "on" : ""}">${this._t("tab_" + id)} (${id === "throttled" ? throttledLabel : counts[id]})</button>`
    ).join("");

    const toolbarParts = [];
    if (this._tab !== "unthrottled") toolbarParts.push(this._renderSortToggle());
    if (this._tab === "throttled") toolbarParts.push(this._renderAcceptedFilter());
    const toolbar = toolbarParts.length ? `<div class="rt-sort">${toolbarParts.join("")}</div>` : "";

    const rows = this._rowsFor(this._tab);
    if (!rows.length) {
      // Keep the toolbar visible even with zero rows: with the Throttled tab's filter
      // set to "Unaccepted", zero rows just means everything's been reviewed — losing
      // the control here would trap the user unable to switch back to "All".
      this._wrap.innerHTML = `${toolbar}<div class="rt-empty">${this._data === null && this._tab === "unthrottled" ? this._t("loading") : this._t("no_entries")}</div>`;
      return;
    }
    const { rows: pageRows, page, pages } = this._paginate(rows, this._tab);
    for (const row of pageRows) this._shownStates.set(row.entity_id, this._hass.states[row.entity_id]);
    const pager = pages > 1 ? this._renderPager(page, pages) : "";
    const bulk =
      this._tab === "unthrottled"
        ? `<div class="rt-bulk"><span>${this._t("all_to")}</span><button data-act="bulk" data-k="1min" class="m1">${this._t("pol_1min")}</button><button data-act="bulk" data-k="5min" class="m5">${this._t("pol_5min")}</button></div>`
        : "";
    // Reserve a full page's worth of height on the rows container (from a previously
    // measured row) so a shorter last page doesn't shrink the card and trigger a
    // Lovelace masonry reflow that jumps the card on screen (only matters once paging
    // is actually in effect for this tab; a single-page tab is naturally stable).
    const rowsStyle = pages > 1 && this._rowH ? ` style="min-height:${this._rowH * this._pageSize}px"` : "";
    const rowsHtml = pageRows
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
        // No unique ID -> not in the entity registry -> no labels -> set_policy/set_accepted can't work (#3).
        const actions =
          w.in_registry === false
            ? `<span class="rt-na" title="${this._esc(this._t("not_throttleable_title"))}">${this._t("not_throttleable")}</span>`
            : `<div class="rt-seg">${seg}</div>
          <button class="rt-acc${acc ? " on" : ""}" data-act="accept" data-eid="${eid}" data-acc="${acc ? "1" : "0"}" title="${this._t("acc_title")}">${this._t("acc_btn")}</button>`;
        return `<div class="rt-row">
          <div class="rt-info" data-act="info" data-eid="${eid}" title="${this._t("more_info")}">
            <div class="rt-name">${this._esc(this._name(eid, w.name))}</div>
            <div class="rt-sub"><span class="rt-rate">${rate}</span> · ${eid}${statBadge}</div>
          </div>
          ${actions}
        </div>`;
      })
      .join("");
    this._wrap.innerHTML = `${toolbar}${bulk}<div class="rt-rows"${rowsStyle}>${rowsHtml}</div>${pager}`;
    const firstRow = this._wrap.querySelector(".rt-row");
    if (firstRow) this._rowH = firstRow.offsetHeight;
  }

  getCardSize() {
    const shown = Math.min(this._rowsFor(this._tab).length, this._pageSize) || 6;
    return 2 + Math.ceil(shown * 0.6);
  }
  static getStubConfig() {
    return { hours: 1, limit: 30, page_size: 50, sort: "entity_id" };
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
  console.info("%c recorder-throttle-card %c v1.2 ", "background:#1f6feb;color:#fff", "");
}
