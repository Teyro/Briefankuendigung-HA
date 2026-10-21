// Kleiner MIME-Leser: Kopfzeilen (inkl. =?utf-8?…?= ), mehrteilige Mails, Base64/Quoted-Printable,
// Zeichensätze. Liefert eine flache Liste der Teile.

function dekodiere(buf, zeichensatz) {
  const cs = String(zeichensatz || 'utf-8').toLowerCase().replace(/^(x-|cp)/, '');
  try { return new TextDecoder(cs === 'us-ascii' ? 'utf-8' : cs).decode(buf); } catch (e) { return buf.toString('latin1'); }
}

function quotedPrintable(text) {
  const b = [];
  const s = text.replace(/=\r?\n/g, '');
  for (let i = 0; i < s.length; i++) {
    if (s[i] === '=' && /^[0-9A-Fa-f]{2}$/.test(s.substr(i + 1, 2))) { b.push(parseInt(s.substr(i + 1, 2), 16)); i += 2; }
    else b.push(s.charCodeAt(i) & 255);
  }
  return Buffer.from(b);
}

/** RFC 2047: =?charset?B|Q?text?= in Kopfzeilen */
export function kopfText(s) {
  return String(s || '')
    .replace(/\?=\s+=\?/g, '?==?')
    .replace(/=\?([^?]+)\?([bBqQ])\?([^?]*)\?=/g, (_, cs, art, t) => {
      const buf = art.toUpperCase() === 'B' ? Buffer.from(t, 'base64') : quotedPrintable(t.replace(/_/g, ' '));
      return dekodiere(buf, cs);
    });
}

function kopfzeilen(roh) {
  const kopf = {};
  const zeilen = roh.replace(/\r\n/g, '\n').replace(/\n[ \t]+/g, ' ').split('\n');
  for (const z of zeilen) {
    const i = z.indexOf(':');
    if (i <= 0) continue;
    const k = z.slice(0, i).trim().toLowerCase();
    (kopf[k] = kopf[k] || []).push(z.slice(i + 1).trim());
  }
  return kopf;
}

/** "text/html; charset=utf-8; boundary=…" -> { wert, params } */
function parameter(s) {
  const teile = String(s || '').split(';');
  const params = {};
  for (const t of teile.slice(1)) {
    const m = t.match(/^\s*([^=\s]+)\s*=\s*(?:"([^"]*)"|([^\s;]*))/);
    if (m) params[m[1].toLowerCase().replace(/\*$/, '')] = m[2] !== undefined ? m[2] : m[3];
  }
  return { wert: teile[0].trim().toLowerCase(), params };
}

function trenne(buf) {
  let i = buf.indexOf('\r\n\r\n'), l = 4;
  const j = buf.indexOf('\n\n');
  if (i < 0 || (j >= 0 && j < i)) { i = j; l = 2; }
  if (i < 0) return { kopf: buf.toString('latin1'), koerper: Buffer.alloc(0) };
  return { kopf: buf.subarray(0, i).toString('latin1'), koerper: buf.subarray(i + l) };
}

/** Zerlegt eine Mail. Ergebnis: { kopf, teile: [{ typ, zeichensatz, inhalt (Buffer), text?, dateiname, cid }] } */
export function zerlege(roh, tiefe = 0) {
  const { kopf: kopfRoh, koerper } = trenne(roh);
  const kopf = kopfzeilen(kopfRoh);
  const ct = parameter((kopf['content-type'] || ['text/plain'])[0]);
  const teile = [];
  if (ct.wert.startsWith('multipart/') && ct.params.boundary && tiefe < 8) {
    const grenze = '--' + ct.params.boundary;
    const text = koerper.toString('latin1');
    const stuecke = text.split(new RegExp('\\r?\\n?' + grenze.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '(?:--)?[ \\t]*\\r?\\n?'));
    for (const st of stuecke.slice(1)) {
      if (!st.trim()) continue;
      teile.push(...zerlege(Buffer.from(st, 'latin1'), tiefe + 1).teile);
    }
  } else {
    const kodierung = String((kopf['content-transfer-encoding'] || [''])[0]).toLowerCase();
    let inhalt = koerper;
    if (kodierung === 'base64') inhalt = Buffer.from(koerper.toString('latin1').replace(/[^A-Za-z0-9+/=]/g, ''), 'base64');
    else if (kodierung === 'quoted-printable') inhalt = quotedPrintable(koerper.toString('latin1'));
    const cd = parameter((kopf['content-disposition'] || [''])[0]);
    const teil = {
      typ: ct.wert, zeichensatz: ct.params.charset || '', inhalt,
      dateiname: kopfText(cd.params.filename || ct.params.name || ''),
      cid: String((kopf['content-id'] || [''])[0]).replace(/[<>]/g, ''),
    };
    if (teil.typ.startsWith('text/')) teil.text = dekodiere(inhalt, teil.zeichensatz);
    teile.push(teil);
  }
  return { kopf, teile };
}

const ENT = { nbsp: ' ', amp: '&', lt: '<', gt: '>', quot: '"', apos: "'", auml: 'ä', ouml: 'ö', uuml: 'ü', Auml: 'Ä', Ouml: 'Ö', Uuml: 'Ü', szlig: 'ß', euro: '€', ndash: '–', mdash: '—', shy: '' };
/** Sichtbarer Text aus HTML (für die Suche nach dem Absender des Briefs). */
export function ohneHtml(html) {
  return String(html || '')
    .replace(/<(script|style|head)[\s\S]*?<\/\1>/gi, ' ')
    .replace(/<br\s*\/?>|<\/(p|div|tr|li|h\d)>/gi, '\n')
    .replace(/<[^>]+>/g, ' ')
    .replace(/&(#x?[0-9a-f]+|[a-z]+);/gi, (m, e) => e[0] === '#' ? String.fromCodePoint(e[1].toLowerCase() === 'x' ? parseInt(e.slice(2), 16) : parseInt(e.slice(1), 10)) : (ENT[e] !== undefined ? ENT[e] : m))
    .replace(/[ \t ]+/g, ' ').replace(/\s*\n\s*/g, '\n').trim();
}
