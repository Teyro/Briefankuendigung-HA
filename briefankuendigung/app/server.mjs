// Briefankündigung – Home-Assistant-Add-on
// Liest zu festen Zeiten (Standard 02:00 und 05:00) per IMAP die Briefankündigungen der Deutschen Post,
// zieht Absender und Briefbild heraus und legt sie in /media/briefankuendigung ab:
//   index.json  – Liste der Briefe (für das iPhone-Widget, abrufbar über /media/local/… mit HA-Token)
//   <id>.jpg    – Bild des Briefumschlags
// Dazu ein Sensor (nur die Anzahl, keine Namen – der Recorder speichert Zustände dauerhaft) und eine
// Seite in der Seitenleiste (Ingress) mit Vorschau und „Jetzt abrufen“.
import fs from 'node:fs';
import path from 'node:path';
import http from 'node:http';
import crypto from 'node:crypto';
import { Imap } from './imap.mjs';
import { zerlege, kopfText, ohneHtml } from './mime.mjs';

const DATEN = process.env.DATEN_ORDNER || '/data';
const MEDIEN = process.env.MEDIEN_ORDNER || '/media/briefankuendigung';
const PORT = Number(process.env.PORT || 8099);
const NUR_INGRESS = process.env.NUR_INGRESS !== '0';
const SUPERVISOR = process.env.SUPERVISOR_URL || 'http://supervisor';

const STANDARD = { imap_server: 'imap.gmx.net', imap_port: 993, benutzer: '', passwort: '', absender: 'ankuendigung@brief.deutschepost.de', ordner: 'INBOX', abrufzeiten: '02:00,05:00', tage: 14 };
function optionen() {
  try { return Object.assign({}, STANDARD, JSON.parse(fs.readFileSync(path.join(DATEN, 'options.json'), 'utf8'))); } catch (e) { return { ...STANDARD }; }
}

fs.mkdirSync(MEDIEN, { recursive: true });
const INDEX = path.join(MEDIEN, 'index.json');
function liesIndex() { try { return JSON.parse(fs.readFileSync(INDEX, 'utf8')); } catch (e) { return { stand: 0, briefe: [] }; } }
function schreibIndex(i) { fs.writeFileSync(INDEX + '.neu', JSON.stringify(i, null, 1)); fs.renameSync(INDEX + '.neu', INDEX); }

const status = { laeuft: false, letzterAbruf: 0, fehler: '', neu: 0 };
const log = (...a) => console.log(new Date().toISOString().slice(0, 19), ...a);

// ---------------------------------------------------------------------------
//  Auswertung einer Mail
// ---------------------------------------------------------------------------
const BILD_MIN = 4000; // kleinere Bilder sind Logos/Zählpixel

/** Absender des Briefs aus dem Text: „ein Brief von <Name> ist unterwegs …“ */
export function briefAbsender(text) {
  const t = String(text || '').replace(/\s+/g, ' ');
  const m = t.match(/Brief(?:sendung)? von\s+(.{2,140}?)\s+(?:ist|wird|kommt|wurde)\s/i)
    || t.match(/Absender:\s*(.{2,140}?)(?:\s{2,}|\n|$)/i);
  return m ? m[1].replace(/^[„"']|[“"']$/g, '').trim() : '';
}

/** Erlaubte Hosts für nachgeladene Bilder (nur die Post selbst, keine Zählpixel Dritter). */
function erlaubterBildHost(url) {
  try { const h = new URL(url).hostname.toLowerCase(); return /(^|\.)(deutschepost\.de|dhl\.de|deutschepost\.com|dpdhl\.com)$/.test(h); } catch (e) { return false; }
}

async function ladeBild(url, holen = fetch) {
  const ctl = new AbortController();
  const t = setTimeout(() => ctl.abort(), 15000);
  try {
    const r = await holen(url, { signal: ctl.signal, redirect: 'follow' });
    const typ = String(r.headers.get('content-type') || '');
    if (!r.ok || !/^image\//.test(typ)) return null;
    const b = Buffer.from(await r.arrayBuffer());
    return b.length > 6e6 ? null : { typ: typ.split(';')[0], inhalt: b };
  } catch (e) { return null; } finally { clearTimeout(t); }
}

/** Mail -> { id, datum, absender, betreff, bild: {typ, inhalt} | null } */
export async function werteAus(roh, { holen = fetch } = {}) {
  const mail = zerlege(roh);
  const k = (n) => kopfText((mail.kopf[n] || [''])[0]);
  const messageId = k('message-id') || crypto.createHash('sha1').update(roh).digest('hex');
  const id = crypto.createHash('sha256').update(messageId).digest('hex').slice(0, 20);
  const datum = Date.parse(k('date')) || Date.now();
  const html = mail.teile.find((t) => t.typ === 'text/html');
  const plain = mail.teile.find((t) => t.typ === 'text/plain');
  const text = plain && plain.text && plain.text.trim() ? plain.text : ohneHtml(html ? html.text : '');
  let absender = briefAbsender(text) || briefAbsender(ohneHtml(html ? html.text : ''));
  // Eingebettete Bilder: das größte (der Briefumschlag), Logos und Pixel sind klein
  const bilder = mail.teile.filter((t) => /^image\/(jpe?g|png|gif|webp)/.test(t.typ) && t.inhalt.length >= BILD_MIN);
  let bild = bilder.sort((a, b) => b.inhalt.length - a.inhalt.length)[0] || null;
  // Sonst: Bilder aus dem HTML nachladen (nur von der Post)
  if (!bild && html) {
    const urls = [...new Set([...html.text.matchAll(/<img[^>]+src=["']([^"']+)["']/gi)].map((m) => m[1].replace(/&amp;/g, '&')))].filter(erlaubterBildHost);
    const geladen = [];
    for (const u of urls.slice(0, 6)) { const b = await ladeBild(u, holen); if (b && b.inhalt.length >= BILD_MIN) geladen.push(b); }
    bild = geladen.sort((a, b) => b.inhalt.length - a.inhalt.length)[0] || null;
  }
  return { id, datum, absender, betreff: k('subject'), bild: bild ? { typ: bild.typ, inhalt: bild.inhalt } : null };
}

// ---------------------------------------------------------------------------
//  Abruf
// ---------------------------------------------------------------------------
export async function abrufen({ imapFabrik = (o) => new Imap(o), holen = fetch } = {}) {
  if (status.laeuft) return status;
  status.laeuft = true;
  const o = optionen();
  const index = liesIndex();
  const bekannt = new Set(index.briefe.map((b) => b.id));
  let neu = 0;
  try {
    if (!o.benutzer || !o.passwort) throw new Error('Noch nicht eingerichtet: in der Konfiguration Benutzer (E-Mail-Adresse) und Passwort eintragen.');
    const imap = imapFabrik({ host: o.imap_server, port: Number(o.imap_port) || 993 });
    await imap.verbinden();
    try {
      await imap.anmelden(o.benutzer, o.passwort);
      await imap.ordner(o.ordner || 'INBOX');
      const seit = new Date(Date.now() - (Number(o.tage) || 14) * 86400000);
      const uids = await imap.sucheVon(o.absender, seit);
      log(`${uids.length} Mail(s) von ${o.absender} seit ${seit.toISOString().slice(0, 10)}`);
      const erledigt = new Set(index.uids || []);
      for (const uid of uids.slice(-40)) {
        const schluessel = (o.ordner || 'INBOX') + ':' + uid;
        if (erledigt.has(schluessel)) continue;
        const roh = await imap.hole(uid);
        if (!roh) continue;
        const b = await werteAus(roh, { holen });
        erledigt.add(schluessel);
        if (bekannt.has(b.id)) continue;
        const eintrag = { id: b.id, datum: b.datum, absender: b.absender, betreff: b.betreff, bild: null, gefunden: Date.now() };
        if (b.bild) {
          const endung = /png/.test(b.bild.typ) ? 'png' : /gif/.test(b.bild.typ) ? 'gif' : /webp/.test(b.bild.typ) ? 'webp' : 'jpg';
          fs.writeFileSync(path.join(MEDIEN, b.id + '.' + endung), b.bild.inhalt);
          eintrag.bild = 'briefankuendigung/' + b.id + '.' + endung;
        }
        index.briefe.push(eintrag);
        bekannt.add(b.id);
        neu++;
      }
      index.uids = [...erledigt].slice(-400);
    } finally {
      await imap.abmelden();
    }
    // Alte Einträge (und ihre Bilder) entfernen
    const grenze = Date.now() - (Number(o.tage) || 14) * 86400000;
    for (const b of index.briefe.filter((x) => x.datum < grenze)) if (b.bild) fs.rmSync(path.join(path.dirname(MEDIEN), b.bild), { force: true });
    index.briefe = index.briefe.filter((x) => x.datum >= grenze).sort((a, b) => b.datum - a.datum);
    index.stand = Date.now();
    index.fehler = '';
    status.fehler = '';
    log(`Abruf fertig: ${neu} neu, ${index.briefe.length} insgesamt`);
  } catch (e) {
    status.fehler = String(e.message || e);
    index.fehler = status.fehler;
    index.fehlerZeit = Date.now();
    log('Fehler:', status.fehler);
  } finally {
    schreibIndex(index);
    status.laeuft = false;
    status.letzterAbruf = Date.now();
    status.neu = neu;
  }
  await sensorMelden(index).catch((e) => log('Sensor:', e.message));
  return status;
}

/** Sensor: nur Zahlen und Zeitpunkt (keine Absender – der Recorder speichert Zustände dauerhaft). */
async function sensorMelden(index) {
  const token = process.env.SUPERVISOR_TOKEN;
  if (!token) return;
  const tage3 = Date.now() - 3 * 86400000;
  const body = {
    state: String(index.briefe.filter((b) => b.datum >= tage3).length),
    attributes: {
      friendly_name: 'Briefankündigungen', icon: 'mdi:email-newsletter', unit_of_measurement: 'Briefe',
      gesamt: index.briefe.length, letzte_ankuendigung: index.briefe[0] ? new Date(index.briefe[0].datum).toISOString() : null,
      letzter_abruf: new Date().toISOString(), fehler: !!index.fehler,
    },
  };
  const r = await fetch(SUPERVISOR + '/core/api/states/sensor.briefankuendigungen', {
    method: 'POST', headers: { Authorization: 'Bearer ' + token, 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error('HTTP ' + r.status);
}

// ---------------------------------------------------------------------------
//  Zeitplan: zu den eingestellten Uhrzeiten (Zeitzone von Home Assistant)
// ---------------------------------------------------------------------------
function zeiten() {
  return String(optionen().abrufzeiten || '02:00,05:00').split(/[,; ]+/).map((z) => z.trim()).filter((z) => /^\d{1,2}:\d{2}$/.test(z)).map((z) => z.padStart(5, '0'));
}
let letzteMinute = '';
function zeitplan() {
  const jetzt = new Date();
  const hm = String(jetzt.getHours()).padStart(2, '0') + ':' + String(jetzt.getMinutes()).padStart(2, '0');
  const schluessel = jetzt.toDateString() + ' ' + hm;
  if (zeiten().includes(hm) && letzteMinute !== schluessel) {
    letzteMinute = schluessel;
    abrufen();
  }
}

// ---------------------------------------------------------------------------
//  Seite in der Seitenleiste (Ingress)
// ---------------------------------------------------------------------------
const SEITE = fs.readFileSync(new URL('./seite.html', import.meta.url), 'utf8');
function sende(res, code, typ, inhalt) { res.writeHead(code, { 'Content-Type': typ, 'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff' }); res.end(inhalt); }

export const server = http.createServer(async (req, res) => {
  const adresse = (req.socket.remoteAddress || '').replace(/^::ffff:/, '');
  if (NUR_INGRESS && adresse !== '172.30.32.2') return sende(res, 403, 'text/plain', 'nur über Home Assistant');
  const url = new URL(req.url, 'http://x');
  const p = url.pathname.replace(/^.*?\/(api\/|bild\/)/, '/$1');
  if (req.method === 'GET' && (p === '/' || p.endsWith('/'))) return sende(res, 200, 'text/html; charset=utf-8', SEITE);
  if (req.method === 'GET' && p === '/api/status') {
    const i = liesIndex();
    return sende(res, 200, 'application/json', JSON.stringify({ ...status, zeiten: zeiten(), eingerichtet: !!(optionen().benutzer && optionen().passwort), stand: i.stand, fehler: status.fehler || i.fehler || '', briefe: i.briefe.map(({ id, datum, absender, betreff, bild }) => ({ id, datum, absender, betreff, bild: !!bild })) }));
  }
  if (req.method === 'POST' && p === '/api/abrufen') {
    if (req.headers['x-brief'] !== '1') return sende(res, 400, 'text/plain', 'fehlende Kopfzeile');
    await abrufen();
    return sende(res, 200, 'application/json', JSON.stringify(status));
  }
  const m = p.match(/^\/bild\/([0-9a-f]{20})$/);
  if (req.method === 'GET' && m) {
    const b = liesIndex().briefe.find((x) => x.id === m[1]);
    if (!b || !b.bild) return sende(res, 404, 'text/plain', 'nicht gefunden');
    const datei = path.join(path.dirname(MEDIEN), b.bild);
    const typ = datei.endsWith('png') ? 'image/png' : datei.endsWith('gif') ? 'image/gif' : datei.endsWith('webp') ? 'image/webp' : 'image/jpeg';
    try { return sende(res, 200, typ, fs.readFileSync(datei)); } catch (e) { return sende(res, 404, 'text/plain', 'nicht gefunden'); }
  }
  sende(res, 404, 'text/plain', 'nicht gefunden');
});

if (process.env.KEIN_START !== '1') {
  server.listen(PORT, () => log(`Briefankündigung läuft, Abruf um ${zeiten().join(' und ')} Uhr`));
  setInterval(zeitplan, 20000);
  // Beim Start einmal abrufen, damit gleich etwas da ist
  setTimeout(() => abrufen(), 5000);
}
