/*
 * Dashboard-Karte „Pakete“ (type: custom:paketbote-card) – kommt mit der Integration Briefankündigung, ohne HACS-Frontend.
 * Optionen: titel, konto (Name oder ID), zugestellt (true/false: zugestellte zeigen), hinzufuegen (true/false: Eingabefeld)
 */
const DIENSTE = {
  dhl: ["DHL", "#FFCC00", "#D40511"], hermes: ["Hermes", "#0091CD", "#fff"], dpd: ["DPD", "#DC0032", "#fff"],
  gls: ["GLS", "#10337A", "#FFD900"], ups: ["UPS", "#40291A", "#FFB500"], amazon: ["Amazon", "#232F3E", "#FF9900"],
};
const DE = (navigator.language || "de").startsWith("de");
const T = (de, en) => (DE ? de : en);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const isoTag = (d) => d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0") + "-" + String(d.getDate()).padStart(2, "0");
const TAGE = DE ? ["So", "Mo", "Di", "Mi", "Do", "Fr", "Sa"] : ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

function standText(b) {
  if (b.stand === "zugestellt") return T("zugestellt", "delivered");
  if (b.erledigt) return T("angekommen", "arrived");
  if (b.stand === "abholbereit") return T("zur Abholung bereit", "ready for pickup");
  const f = b.zeitfenster ? ", " + b.zeitfenster : "";
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(b.erwartet || "");
  if (m) {
    const d = new Date(+m[1], +m[2] - 1, +m[3]), h = new Date();
    const tage = Math.round((d - new Date(h.getFullYear(), h.getMonth(), h.getDate())) / 864e5);
    if (tage === 0) return T("kommt heute", "arrives today") + f;
    if (tage === 1) return T("kommt morgen", "arrives tomorrow") + f;
    if (tage < 0) return b.stand === "heute" ? T("kommt heute", "arrives today") + f : T("überfällig", "overdue");
    return T("voraussichtlich ", "expected ") + (tage < 7 ? TAGE[d.getDay()] + " " : "") + d.getDate() + "." + (d.getMonth() + 1) + "." + f;
  }
  if (b.stand === "heute") return T("kommt heute", "arrives today") + f;
  if (b.problem) return T("Problem bei der Zustellung", "delivery problem");
  return (b.ereignisse || []).length ? T("unterwegs", "on the way") : T("angekündigt", "announced");
}
const stufe = (b) => (b.stand === "zugestellt" ? 4 : b.stand === "heute" || b.stand === "abholbereit" ? 3 : (b.ereignisse || []).length ? 2 : 1);
const heute = (b) => !b.erledigt && b.stand !== "zugestellt" && (b.stand === "heute" || b.stand === "abholbereit" || b.erwartet === isoTag(new Date()));

class PaketboteCard extends HTMLElement {
  setConfig(c) { this.c = Object.assign({ zugestellt: true, hinzufuegen: true }, c || {}); this.offen = this.offen || ""; this.zeichnen(); }
  static getStubConfig() { return { titel: "Pakete" }; }
  getCardSize() { return 4; }

  set hass(h) {
    const erste = !this._hass;
    this._hass = h;
    if (erste) { this.laden(); this.uhr = setInterval(() => this.laden(), 5 * 60e3); }
  }
  disconnectedCallback() { clearInterval(this.uhr); this.uhr = null; this._hass = this._hass; }
  connectedCallback() { if (this._hass && !this.uhr) { this.laden(); this.uhr = setInterval(() => this.laden(), 5 * 60e3); } }

  async laden() {
    try {
      const k = this.c.konto ? "?konto=" + encodeURIComponent(this.c.konto) : "";
      const d = await this._hass.callApi("GET", "briefankuendigung/briefe" + k);
      this.daten = (d.briefe || []).filter((b) => b.art === "paket");
      this.fehler = "";
    } catch (e) {
      this.fehler = T("Keine Verbindung zur Integration", "No connection to the integration") + ": " + (e.message || e.status_message || e);
    }
    this.zeichnen();
  }

  async hinzufuegen(nummer, name) {
    try {
      await this._hass.callApi("POST", "briefankuendigung/sendung", { nummer, name });
      this.meldung = T("Hinzugefügt", "Added");
    } catch (e) {
      this.meldung = T("Sendungsnummer oder Paketdienst nicht erkannt.", "Tracking number or carrier not recognised.");
    }
    await this.laden();
  }

  zeichnen() {
    if (!this.c) return;
    if (!this.shadowRoot) this.attachShadow({ mode: "open" });
    const alle = this.daten || [];
    const g = { heute: [], unterwegs: [], zugestellt: [] };
    for (const b of alle) (b.erledigt || b.stand === "zugestellt" ? g.zugestellt : heute(b) ? g.heute : g.unterwegs).push(b);
    const termin = (a, b) => (a.erwartet || "9999").localeCompare(b.erwartet || "9999") || b.datum - a.datum;
    g.heute.sort(termin); g.unterwegs.sort(termin); g.zugestellt.sort((a, b) => b.datum - a.datum);
    const aktiv = g.heute.length + g.unterwegs.length;
    const titel = this.c.titel || (aktiv ? (aktiv === 1 ? T("1 Paket unterwegs", "1 parcel on the way") : aktiv + T(" Pakete unterwegs", " parcels on the way")) : T("Keine Pakete unterwegs", "No parcels on the way"));
    const zeile = (b) => {
      const [n, f, s] = DIENSTE[b.dienst] || [String(b.dienst || "?").toUpperCase(), "#b8854d", "#fff"];
      const auf = this.offen === b.id;
      const art = b.problem ? "problem" : b.stand === "abholbereit" ? "abholen" : heute(b) ? "heute" : "";
      const ev = (b.ereignisse || []).slice(0, 8).map((e) => `<li><b>${esc(e.text)}</b><span>${esc([e.zeit ? new Date(e.zeit).toLocaleString(undefined, { weekday: "short", day: "numeric", month: "numeric", hour: "2-digit", minute: "2-digit" }) : "", e.ort].filter(Boolean).join(" · "))}</span></li>`).join("");
      return `<div class="sendung ${b.erledigt || b.stand === "zugestellt" ? "fertig" : ""}" data-id="${esc(b.id)}">
        <div class="kopf">
          <div class="logo" style="background:${f};color:${s}"><span class="kiste">▣</span><span class="n ${b.dienst === "dhl" ? "kursiv" : ""}">${esc(n)}</span></div>
          <div class="text"><div class="titel">${esc(b.name || b.absender || b.sendung || T("Paket", "Parcel"))}</div>
            <div class="stand ${art}">${esc(standText(b))}${b.kundenkonto ? " · " + esc(b.kundenkonto) : ""}</div>
            ${b.erledigt || b.stand === "zugestellt" ? "" : `<div class="balken">${[1, 2, 3, 4].map((i) => `<i class="${i <= stufe(b) ? "an" : ""}"></i>`).join("")}</div>`}
          </div>
        </div>
        ${auf ? `<div class="detail">${b.status_text ? `<p>${esc(b.status_text)}</p>` : ""}
          ${ev ? `<ul>${ev}</ul>` : `<p class="leise">${T("Noch keine Stationen bekannt.", "No stops known yet.")}</p>`}
          <div class="knoepfe">${b.verfolgen ? `<a href="${esc(b.verfolgen)}" target="_blank" rel="noopener">${T("Beim Dienst", "Carrier site")}</a>` : ""}
          ${b.sendung ? `<span class="leise">${esc(b.sendung)}</span>` : ""}</div></div>` : ""}
      </div>`;
    };
    const abschnitt = (name, liste) => (liste.length ? `<h3>${name}</h3>${liste.map(zeile).join("")}` : "");
    this.shadowRoot.innerHTML = `<style>
      ha-card{padding:14px 16px}
      .oben{display:flex;align-items:center;gap:8px;font-size:1.15em;font-weight:600;margin-bottom:6px}
      h3{font-size:.75em;text-transform:uppercase;letter-spacing:.05em;color:var(--secondary-text-color);margin:12px 0 4px}
      .sendung{border-radius:12px;padding:6px;cursor:pointer}.sendung:hover{background:var(--secondary-background-color)}
      .fertig{opacity:.6}.kopf{display:flex;gap:12px;align-items:center}
      .logo{width:42px;height:42px;border-radius:11px;display:flex;flex-direction:column;align-items:center;justify-content:center;flex:none;font-weight:900;font-size:10px}
      .kiste{font-size:16px;line-height:16px}.kursiv{font-style:italic}
      .text{flex:1;min-width:0}.titel{font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
      .stand{font-size:.9em;color:var(--secondary-text-color)}.stand.heute{color:var(--primary-color)}.stand.abholen{color:var(--warning-color,#f57c00)}.stand.problem{color:var(--error-color,#db4437)}
      .balken{display:flex;gap:3px;margin-top:4px;max-width:180px}.balken i{flex:1;height:4px;border-radius:2px;background:var(--divider-color)}.balken i.an{background:var(--primary-color)}
      .detail{margin:8px 0 4px 54px;font-size:.92em}.detail ul{list-style:none;padding:0;margin:0;border-left:2px solid var(--divider-color)}
      .detail li{padding:0 0 8px 12px;position:relative}.detail li:before{content:"";position:absolute;left:-6px;top:4px;width:10px;height:10px;border-radius:50%;background:var(--disabled-text-color,#999)}
      .detail li:first-child:before{background:var(--primary-color)}.detail li span{display:block;font-size:.85em;color:var(--secondary-text-color)}
      .knoepfe{display:flex;gap:12px;align-items:center;margin-top:4px}.knoepfe a{color:var(--primary-color);font-weight:600;text-decoration:none}
      .leise{color:var(--secondary-text-color);font-size:.9em}.fehler{color:var(--error-color,#db4437)}
      form{display:flex;gap:6px;margin-top:12px}input{flex:1;min-width:0;padding:8px 10px;border-radius:8px;border:1px solid var(--divider-color);background:var(--card-background-color);color:var(--primary-text-color)}
      button{padding:8px 12px;border-radius:8px;border:0;background:var(--primary-color);color:var(--text-primary-color,#fff);font-weight:600;cursor:pointer}
    </style><ha-card>
      <div class="oben"><ha-icon icon="mdi:package-variant-closed"></ha-icon>${esc(titel)}</div>
      ${this.fehler ? `<p class="fehler">${esc(this.fehler)}</p>` : ""}
      ${this.daten && !alle.length ? `<p class="leise">${T("Keine Pakete unterwegs", "No parcels on the way")}</p>` : ""}
      ${abschnitt(T("Heute", "Today"), g.heute)}${abschnitt(T("Unterwegs", "On the way"), g.unterwegs)}
      ${this.c.zugestellt ? abschnitt(T("Zugestellt", "Delivered"), g.zugestellt.slice(0, 5)) : ""}
      ${this.c.hinzufuegen ? `<form><input name="n" placeholder="${T("Sendungsnummer (Name)", "Tracking number (name)")}" autocomplete="off"><button>＋</button></form>` : ""}
      ${this.meldung ? `<p class="leise">${esc(this.meldung)}</p>` : ""}
    </ha-card>`;
    this.shadowRoot.querySelectorAll(".sendung").forEach((el) => (el.onclick = (e) => {
      if (e.target.closest("a")) return;
      this.offen = this.offen === el.dataset.id ? "" : el.dataset.id;
      this.zeichnen();
    }));
    const f = this.shadowRoot.querySelector("form");
    if (f) f.onsubmit = (e) => {
      e.preventDefault();
      const [nummer, ...rest] = f.n.value.trim().split(/\s+/);
      if (nummer) this.hinzufuegen(nummer, rest.join(" "));
    };
  }
}
customElements.define("paketbote-card", PaketboteCard);
window.customCards = window.customCards || [];
window.customCards.push({ type: "paketbote-card", name: "Pakete (Briefankündigung)", description: "Sendungen mit Stand, Termin und Verlauf", preview: true });
