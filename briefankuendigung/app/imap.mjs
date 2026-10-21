// Kleiner IMAP-Client (nur Lesen) – gerade genug für: anmelden, Ordner schreibgeschützt öffnen,
// nach Absender suchen, Mails holen, ohne sie als gelesen zu markieren (BODY.PEEK).
import tls from 'node:tls';
import net from 'node:net';

const MONATE = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
/** IMAP-Datum für SEARCH SINCE, z. B. 07-Oct-2026 */
export function imapDatum(d) { return String(d.getDate()).padStart(2, '0') + '-' + MONATE[d.getMonth()] + '-' + d.getFullYear(); }

/** Zeichenkette für IMAP: in Anführungszeichen, Backslash und " maskiert (nur ASCII-taugliche Werte). */
function zitat(s) { return '"' + String(s).replace(/\\/g, '\\\\').replace(/"/g, '\\"') + '"'; }

export class Imap {
  constructor({ host, port = 993, sicher = true, zeitLimitMs = 30000 }) {
    Object.assign(this, { host, port, sicher, zeitLimitMs });
    this.puffer = Buffer.alloc(0);
    this.nr = 0;
    this.warten = null;
    this.fehler = null;
  }

  async verbinden() {
    await new Promise((ok, fehl) => {
      const optionen = { host: this.host, port: this.port, servername: this.host };
      this.sock = this.sicher ? tls.connect(optionen, ok) : net.connect(optionen, ok);
      this.sock.once('error', fehl);
    });
    this.sock.setTimeout(this.zeitLimitMs, () => this.sock.destroy(new Error('IMAP: Zeitüberschreitung')));
    this.sock.on('data', (d) => { this.puffer = Buffer.concat([this.puffer, d]); if (this.warten) this.warten(); });
    this.sock.on('error', (e) => { this.fehler = e; if (this.warten) this.warten(); });
    this.sock.on('close', () => { this.fehler = this.fehler || new Error('IMAP: Verbindung geschlossen'); if (this.warten) this.warten(); });
    const gruss = await this.zeile();
    if (!/^\* (OK|PREAUTH)/i.test(gruss)) throw new Error('IMAP: unerwartete Begrüßung');
  }

  async mehr() {
    if (this.fehler) throw this.fehler;
    await new Promise((ok) => { this.warten = ok; });
    this.warten = null;
    if (this.fehler && !this.puffer.length) throw this.fehler;
  }

  async zeile() {
    for (;;) {
      const i = this.puffer.indexOf('\r\n');
      if (i >= 0) {
        const z = this.puffer.subarray(0, i).toString('utf8');
        this.puffer = this.puffer.subarray(i + 2);
        return z;
      }
      await this.mehr();
    }
  }

  async bytes(n) {
    while (this.puffer.length < n) await this.mehr();
    const b = Buffer.from(this.puffer.subarray(0, n));
    this.puffer = this.puffer.subarray(n);
    return b;
  }

  /** Befehl senden; liefert die ungetaggten Antworten als { text, literale[] } und wirft bei NO/BAD. */
  async befehl(text) {
    const tag = 'B' + (++this.nr);
    this.sock.write(tag + ' ' + text + '\r\n');
    const antworten = [];
    for (;;) {
      let z = await this.zeile();
      const literale = [];
      // Zeile mit Literal: "… {123}" – dann folgen 123 Bytes und der Rest der Antwort
      let m;
      while ((m = z.match(/\{(\d+)\}$/))) {
        literale.push(await this.bytes(Number(m[1])));
        z = z.slice(0, -m[0].length) + '\u0000' + (await this.zeile());
      }
      if (z.startsWith(tag + ' ')) {
        const rest = z.slice(tag.length + 1);
        if (!/^OK/i.test(rest)) {
          const e = new Error('IMAP: ' + rest.replace(/^(NO|BAD)\s*/i, '').slice(0, 200));
          e.imap = rest.slice(0, 3).toUpperCase();
          throw e;
        }
        return antworten;
      }
      antworten.push({ text: z, literale });
    }
  }

  async anmelden(benutzer, passwort) {
    try {
      await this.befehl('LOGIN ' + zitat(benutzer) + ' ' + zitat(passwort));
    } catch (e) {
      const f = new Error('Anmeldung am Postfach abgelehnt – Benutzer/Passwort prüfen und bei GMX „POP3/IMAP-Abruf“ erlauben (' + e.message.replace(/^IMAP: /, '') + ')');
      f.anmeldung = true;
      throw f;
    }
  }

  /** Ordner nur lesend öffnen (nichts wird verändert). */
  async ordner(name) { await this.befehl('EXAMINE ' + zitat(name)); }

  async sucheVon(absender, seit) {
    const a = await this.befehl('UID SEARCH FROM ' + zitat(absender) + ' SINCE ' + imapDatum(seit));
    const ids = [];
    for (const x of a) { const m = x.text.match(/^\* SEARCH\s*(.*)$/i); if (m) ids.push(...m[1].trim().split(/\s+/).filter(Boolean).map(Number)); }
    return ids;
  }

  /** Ganze Mail (Rohdaten) holen, ohne sie als gelesen zu markieren. */
  async hole(uid) {
    const a = await this.befehl('UID FETCH ' + uid + ' (UID BODY.PEEK[])');
    for (const x of a) if (/FETCH/i.test(x.text) && x.literale.length) return x.literale[0];
    return null;
  }

  async abmelden() {
    try { await this.befehl('LOGOUT'); } catch (e) { /* egal */ }
    try { this.sock.end(); } catch (e) { /* egal */ }
  }
}
