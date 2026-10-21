// Test des Add-ons gegen einen nachgebauten IMAP-Server (ohne TLS) und nachgebauten Supervisor.
import net from 'node:net';
import http from 'node:http';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import crypto from 'node:crypto';

const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'brief-'));
process.env.DATEN_ORDNER = path.join(tmp, 'data');
process.env.MEDIEN_ORDNER = path.join(tmp, 'media', 'briefankuendigung');
process.env.KEIN_START = '1';
process.env.NUR_INGRESS = '0';
fs.mkdirSync(process.env.DATEN_ORDNER, { recursive: true });

// --- nachgebauter Supervisor (Sensor) ---
const sensoren = [];
const sup = http.createServer((q, s) => { let b = ''; q.on('data', (d) => (b += d)); q.on('end', () => { sensoren.push({ url: q.url, auth: q.headers.authorization, body: JSON.parse(b) }); s.end('{}'); }); });
await new Promise((r) => sup.listen(9302, r));
process.env.SUPERVISOR_URL = 'http://127.0.0.1:9302';
process.env.SUPERVISOR_TOKEN = 'sup-token';

// --- Testmails ---
const bildGross = crypto.randomBytes(48000); // „Briefumschlag“
const logo = crypto.randomBytes(900);
const b64 = (b) => b.toString('base64').replace(/.{76}/g, '$&\r\n');
const mail1 = [
  'From: Deutsche Post AG <ankuendigung@brief.deutschepost.de>',
  'To: christian@gmx.de',
  'Subject: Ein Brief ist unterwegs zu Ihnen',
  'Date: Sat, 01 Aug 2026 01:01:00 +0200',
  'Message-ID: <abc123@brief.deutschepost.de>',
  'MIME-Version: 1.0',
  'Content-Type: multipart/related; boundary="GRENZE1"',
  '',
  '--GRENZE1',
  'Content-Type: text/html; charset=utf-8',
  'Content-Transfer-Encoding: quoted-printable',
  '',
  '<p>Lieber Herr Christian Stake,</p><p>ein Brief von <i>Hansemerkur KV AG Hauptv=',
  'erwaltung</i> ist unterwegs zu Ihnen und wird Ihnen in den n=C3=A4chsten Tagen zugestellt.</p>',
  '<img src=3D"cid:logo"><img src=3D"cid:brief">',
  '--GRENZE1',
  'Content-Type: image/png; name="logo.png"',
  'Content-Transfer-Encoding: base64',
  'Content-ID: <logo>',
  '',
  b64(logo),
  '--GRENZE1',
  'Content-Type: image/jpeg; name="brief.jpg"',
  'Content-Transfer-Encoding: base64',
  'Content-ID: <brief>',
  '',
  b64(bildGross),
  '--GRENZE1--',
  '',
].join('\r\n');
const mail2 = [
  'From: ankuendigung@brief.deutschepost.de',
  'Subject: =?UTF-8?B?RWluIEJyaWVmIGlzdCB1bnRlcndlZ3M=?=',
  'Date: Mon, 19 Oct 2026 01:05:00 +0200',
  'Message-ID: <def456@brief.deutschepost.de>',
  'Content-Type: text/html; charset=utf-8',
  '',
  '<p>ein Brief von <b>Finanzamt Hamburg-Nord</b> ist unterwegs zu Ihnen.</p><img src="https://briefankuendigung.deutschepost.de/scan/xyz.jpg"><img src="https://tracker.example.com/pixel.gif">',
  '',
].join('\r\n');
const mail3 = Buffer.concat([Buffer.from([
  'From: ankuendigung@brief.deutschepost.de',
  'Subject: =?ISO-8859-1?Q?Ein_Brief_f=FCr_Sie?=',
  'Date: Tue, 20 Oct 2026 01:07:00 +0200',
  'Message-ID: <ghi789@brief.deutschepost.de>',
  'Content-Type: text/plain; charset=iso-8859-1',
  'Content-Transfer-Encoding: 8bit',
  '', ''].join('\r\n'), 'latin1'), Buffer.from('ein Brief von Bezirksamt Wandsbek – Bürgeramt ist unterwegs zu Ihnen.\r\n', 'latin1')]);
const mails = { 101: Buffer.from(mail1), 102: Buffer.from(mail2), 103: mail3 };

// --- nachgebauter IMAP-Server ---
let passwort = 'richtig', gesehenGesetzt = false;
const imapLog = [];
const imap = net.createServer((s) => {
  s.write('* OK Nachbau bereit\r\n');
  let puffer = '';
  s.on('data', (d) => {
    puffer += d.toString('utf8');
    let i;
    while ((i = puffer.indexOf('\r\n')) >= 0) {
      const z = puffer.slice(0, i); puffer = puffer.slice(i + 2);
      const [tag, cmd, ...rest] = z.split(' ');
      imapLog.push(cmd + ' ' + rest.join(' ').replace(/"[^"]*" "[^"]*"$/, '"…" "…"'));
      const r = rest.join(' ');
      if (cmd === 'LOGIN') { const m = r.match(/^"([^"]*)" "((?:[^"\\]|\\.)*)"$/); if (m && m[2].replace(/\\(.)/g, '$1') === passwort) s.write(tag + ' OK LOGIN completed\r\n'); else s.write(tag + ' NO [AUTHENTICATIONFAILED] Invalid credentials\r\n'); }
      else if (cmd === 'EXAMINE') s.write('* 3 EXISTS\r\n* OK [READ-ONLY]\r\n' + tag + ' OK [READ-ONLY] EXAMINE completed\r\n');
      else if (cmd === 'UID' && rest[0] === 'SEARCH') s.write('* SEARCH ' + Object.keys(mails).join(' ') + '\r\n' + tag + ' OK SEARCH completed\r\n');
      else if (cmd === 'UID' && rest[0] === 'FETCH') {
        if (!/BODY\.PEEK/.test(r)) gesehenGesetzt = true;
        const uid = rest[1]; const m = mails[uid];
        s.write('* 1 FETCH (UID ' + uid + ' BODY[] {' + m.length + '}\r\n'); s.write(m); s.write(')\r\n' + tag + ' OK FETCH completed\r\n');
      }
      else if (cmd === 'LOGOUT') { s.write('* BYE\r\n' + tag + ' OK LOGOUT completed\r\n'); s.end(); }
      else s.write(tag + ' BAD unbekannt\r\n');
    }
  });
});
await new Promise((r) => imap.listen(9301, r));

const o = { imap_server: '127.0.0.1', imap_port: 9301, benutzer: 'christian@gmx.de', passwort: 'richtig', absender: 'ankuendigung@brief.deutschepost.de', ordner: 'INBOX', abrufzeiten: '02:00,05:00', tage: 400 };
fs.writeFileSync(path.join(process.env.DATEN_ORDNER, 'options.json'), JSON.stringify(o));

const { abrufen, server, briefAbsender } = await import(new URL('../briefankuendigung/app/server.mjs', import.meta.url).href);
const { Imap } = await import(new URL('../briefankuendigung/app/imap.mjs', import.meta.url).href);
const fabrik = (x) => new Imap({ ...x, sicher: false });
const geladen = [];
const holen = async (url) => {
  geladen.push(url);
  const bild = url.includes('deutschepost') ? crypto.randomBytes(30000) : crypto.randomBytes(50);
  return { ok: true, headers: { get: () => 'image/jpeg' }, arrayBuffer: async () => bild };
};

// 1. Abruf
let st = await abrufen({ imapFabrik: fabrik, holen });
const idx = JSON.parse(fs.readFileSync(path.join(process.env.MEDIEN_ORDNER, 'index.json'), 'utf8'));
console.log('1. Fehler:', st.fehler || 'keiner', '| neu:', st.neu, '| IMAP:', imapLog.join(' ; '));
for (const b of idx.briefe) console.log('   ', new Date(b.datum).toISOString().slice(0, 10), '|', b.absender, '|', b.betreff, '|', b.bild, b.bild ? fs.statSync(path.join(tmp, 'media', b.bild)).size + ' B' : '');
console.log('   Bild 1 identisch:', fs.readFileSync(path.join(tmp, 'media', idx.briefe.find((b) => b.absender.startsWith('Hansemerkur')).bild)).equals(bildGross));
console.log('   nachgeladen:', geladen.join(', '), '| als gelesen markiert (soll false):', gesehenGesetzt);
console.log('   Sensor:', JSON.stringify(sensoren.at(-1)));

// 2. Nochmal: nichts Neues, keine Mail erneut geholt
imapLog.length = 0;
st = await abrufen({ imapFabrik: fabrik, holen });
console.log('2. neu:', st.neu, '| FETCH-Befehle (soll 0):', imapLog.filter((x) => x.startsWith('UID FETCH')).length);

// 3. Falsches Passwort
passwort = 'anders';
fs.writeFileSync(path.join(process.env.DATEN_ORDNER, 'options.json'), JSON.stringify({ ...o, passwort: 'alt"pw\\x' }));
st = await abrufen({ imapFabrik: fabrik, holen });
console.log('3. Fehler:', st.fehler, '| Briefe bleiben:', JSON.parse(fs.readFileSync(path.join(process.env.MEDIEN_ORDNER, 'index.json'), 'utf8')).briefe.length);

// 4. Ingress-Seite und Status
await new Promise((r) => server.listen(9303, r));
const s = await (await fetch('http://127.0.0.1:9303/api/status')).json();
console.log('4. Status:', s.briefe.length, 'Briefe, Fehler:', s.fehler.slice(0, 60), '| Seite:', (await (await fetch('http://127.0.0.1:9303/')).text()).includes('Briefankündigung'));
const bildAntwort = await fetch('http://127.0.0.1:9303/bild/' + s.briefe.find((b) => b.bild).id);
console.log('   Bild über Seite:', bildAntwort.status, bildAntwort.headers.get('content-type'), '| Pfad-Trick:', (await fetch('http://127.0.0.1:9303/bild/..%2F..%2Fdata%2Foptions.json')).status);
console.log('   POST ohne Kopfzeile:', (await fetch('http://127.0.0.1:9303/api/abrufen', { method: 'POST' })).status);

// 5. Absender-Erkennung, Varianten
for (const t of ['Ein Brief von Sparkasse Holstein wird Ihnen in Kürze zugestellt', 'Ein Brief ist unterwegs zu Ihnen.', 'eine Briefsendung von AOK Nordwest ist auf dem Weg zu Ihnen'])
  console.log('5.', JSON.stringify(t.slice(0, 40)), '→', JSON.stringify(briefAbsender(t)));
server.close(); imap.close(); sup.close();
