"""Postfach per IMAP lesen und Briefankündigungen der Deutschen Post auswerten.

Ohne Home-Assistant-Abhängigkeiten (läuft im Executor und ist so einzeln testbar).
Nur lesend: Ordner schreibgeschützt öffnen (EXAMINE), Mails mit BODY.PEEK holen –
nichts wird als gelesen markiert, verschoben oder gelöscht.
"""

from __future__ import annotations

import email
import email.policy
import hashlib
import html
import imaplib
import re
import ssl
import urllib.request
from datetime import datetime, timedelta, timezone
from email.utils import getaddresses, parsedate_to_datetime
from urllib.parse import urlparse

try:
    from . import pakete
except ImportError:  # einzeln geladen (Tests ohne Home Assistant)
    import importlib.util as _iu
    import pathlib as _pl
    _spec = _iu.spec_from_file_location("pakete", _pl.Path(__file__).with_name("pakete.py"))
    pakete = _iu.module_from_spec(_spec)
    _spec.loader.exec_module(pakete)

BILD_MIN = 4000  # kleinere Bilder sind Logos und Zählpixel
ERLAUBTE_BILD_HOSTS = ("deutschepost.de", "dhl.de", "deutschepost.com", "dpdhl.com")
MONATE = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


class AnmeldeFehler(Exception):
    """Postfach lehnt Benutzer oder Passwort ab."""


class VerbindungsFehler(Exception):
    """Server nicht erreichbar oder unerwartete Antwort."""


def ohne_html(text: str) -> str:
    """Sichtbarer Text aus HTML."""
    t = re.sub(r"<(script|style|head)[\s\S]*?</\1>", " ", text or "", flags=re.I)
    t = re.sub(r"<br\s*/?>|</(p|div|tr|li|h\d)>", "\n", t, flags=re.I)
    t = re.sub(r"<[^>]+>", " ", t)
    t = html.unescape(t)
    t = re.sub(r"[ \t ]+", " ", t)
    return re.sub(r"\s*\n\s*", "\n", t).strip()


def brief_absender(text: str) -> str:
    """Absender des Briefs aus „ein Brief von <Name> ist unterwegs …“."""
    t = re.sub(r"\s+", " ", text or "")
    m = re.search(r"Brief(?:sendung)? von\s+(.{2,140}?)\s+(?:ist|wird|kommt|wurde)\s", t, re.I) or re.search(
        r"Absender:\s*(.{2,140}?)(?:\s{2,}|$)", t, re.I
    )
    return m.group(1).strip(" „“\"'") if m else ""


def paket_infos(text: str) -> dict:
    """Absender, Sendungsnummer und Stand aus einer DHL-Paketankündigung (so gut es geht)."""
    t = re.sub(r"\s+", " ", text or "")
    m = re.search(r"(?:Sendung|Paket|Päckchen|Lieferung)\s+von\s+(.{2,100}?)(?:\s+(?:ist|wird|kommt|wurde|hat|befindet)\s|[,;!](?:\s|$)|\.\s+[A-ZÄÖÜ]|$)", t, re.I) or re.search(
        r"Absender:\s*(.{2,100}?)(?:\s{2,}|[.,;]|$)", t, re.I
    )
    nummer = re.search(r"\b(JJD\d{12,20}|00\d{18}|\d{14}|\d{12}|[A-Z]{2}\d{9}[A-Z]{2})\b", t)
    if re.search(r"wurde\s.{0,40}zugestellt|ist zugestellt|zugestellt worden|erfolgreich zugestellt|abgeholt", t, re.I):
        stand = "zugestellt"
    elif re.search(r"\bheute\b", t, re.I):
        stand = "heute"
    else:
        stand = "unterwegs"
    return {"absender": m.group(1).strip(" „“\"'.") if m else "", "sendung": nummer.group(1) if nummer else "", "stand": stand}


def ist_paket(text: str) -> bool:
    """Ist es überhaupt eine Sendungsmail (und kein Newsletter)?"""
    return bool(re.search(r"Sendung|Paket|Päckchen|Zustellung", text or "", re.I))


def wichtig(absender: str, stichworte: str) -> bool:
    """Steht eines der Stichwörter (z. B. „Finanzamt“) im Absender?"""
    a = (absender or "").lower()
    return bool(a) and any(w and w in a for w in (x.strip().lower() for x in re.split(r"[,;\n]+", stichworte or "")))


def _bild_host_erlaubt(url: str) -> bool:
    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        return False
    return urlparse(url).scheme == "https" and any(host == h or host.endswith("." + h) for h in ERLAUBTE_BILD_HOSTS)


def _lade_bild(url: str, oeffnen=urllib.request.urlopen) -> tuple[bytes, str] | None:
    try:
        with oeffnen(urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=15) as r:
            typ = (r.headers.get("Content-Type") or "").split(";")[0].strip()
            if not typ.startswith("image/"):
                return None
            daten = r.read(6_000_001)
            return (daten, typ) if len(daten) <= 6_000_000 else None
    except Exception:  # noqa: BLE001 – ein fehlendes Bild ist kein Fehler
        return None


def werte_aus(roh: bytes, oeffnen=urllib.request.urlopen, art: str = "brief", dienst: str = "") -> dict | None:
    """Eine Mail auswerten: id, datum (ms), art, absender, betreff, bild (bytes|None), bild_typ (+ sendung, stand bei Paketen).
    Gibt None zurück, wenn eine Paket-Mail gar keine Sendung betrifft (z. B. Newsletter)."""
    msg = email.message_from_bytes(roh, policy=email.policy.default)
    message_id = str(msg.get("Message-ID") or "") or hashlib.sha1(roh).hexdigest()
    bid = hashlib.sha256(message_id.encode()).hexdigest()[:20]
    try:
        datum = int(parsedate_to_datetime(str(msg.get("Date"))).timestamp() * 1000)
    except (TypeError, ValueError):
        datum = int(datetime.now(timezone.utc).timestamp() * 1000)

    html_text, plain_text = "", ""
    bilder: list[tuple[bytes, str]] = []
    for teil in msg.walk():
        if teil.is_multipart():
            continue
        typ = teil.get_content_type()
        if typ == "text/html" and not html_text:
            html_text = teil.get_content()
        elif typ == "text/plain" and not plain_text:
            plain_text = teil.get_content()
        elif teil.get_content_maintype() == "image":
            daten = teil.get_payload(decode=True) or b""
            if len(daten) >= BILD_MIN:
                bilder.append((daten, typ))

    if art == "paket":
        betreff = str(msg.get("Subject") or "")
        text = plain_text or ohne_html(html_text)
        if not pakete.ist_sendung(betreff + " " + text):
            return None
        dienst = dienst or pakete.dienst_von_absender(str(msg.get("From") or "")) or "dhl"
        tag = datetime.fromtimestamp(datum / 1000).date()
        infos = pakete.infos(betreff + ". " + text, dienst, tag)
        if not infos["absender"]:
            infos["absender"] = pakete.absender(ohne_html(html_text), dienst)
        # Empfänger (für mehrere Kundenkonten, z. B. zwei Amazon-Konten in einem Postfach)
        an = [a.lower() for _, a in getaddresses([str(msg.get("To") or ""), str(msg.get("Delivered-To") or "")]) if "@" in a]
        # Bilder in Paket-Mails sind Werbung/Logos – keine Vorschau
        return {"id": bid, "datum": datum, "art": "paket", "absender": infos["absender"], "betreff": betreff,
                "bild": None, "bild_typ": None, "sendung": infos["sendung"], "stand": infos["stand"], "dienst": dienst,
                "erwartet": infos["erwartet"], "zeitfenster": infos["zeitfenster"], "empfaenger": an[0] if an else "",
                **{k: infos[k] for k in ("abholcode", "abholort", "abholadresse", "abholfrist") if infos.get(k)}}

    absender = brief_absender(plain_text) or brief_absender(ohne_html(html_text))
    bild = max(bilder, key=lambda b: len(b[0])) if bilder else None
    if not bild and html_text:
        # Bild nur verlinkt: von der Post nachladen (keine Zählpixel Dritter)
        urls = []
        for u in re.findall(r"""<img[^>]+src=["']([^"']+)["']""", html_text, re.I):
            u = html.unescape(u)
            if _bild_host_erlaubt(u) and u not in urls:
                urls.append(u)
        geladen = [b for b in (_lade_bild(u, oeffnen) for u in urls[:6]) if b and len(b[0]) >= BILD_MIN]
        bild = max(geladen, key=lambda b: len(b[0])) if geladen else None

    return {
        "id": bid,
        "datum": datum,
        "art": "brief",
        "absender": absender,
        "betreff": str(msg.get("Subject") or ""),
        "bild": bild[0] if bild else None,
        "bild_typ": bild[1] if bild else None,
    }


def verbinde(server: str, port: int, benutzer: str, passwort: str, sicher: bool = True) -> imaplib.IMAP4:
    """Verbinden und anmelden. Wirft AnmeldeFehler bzw. VerbindungsFehler."""
    try:
        if sicher:
            imap = imaplib.IMAP4_SSL(server, port, ssl_context=ssl.create_default_context(), timeout=30)
        else:
            imap = imaplib.IMAP4(server, port, timeout=30)
    except (OSError, imaplib.IMAP4.error) as e:
        raise VerbindungsFehler(f"Server {server}:{port} nicht erreichbar ({e})") from e
    try:
        imap.login(benutzer, passwort)
    except imaplib.IMAP4.error as e:
        try:
            imap.logout()
        except Exception:  # noqa: BLE001
            pass
        raise AnmeldeFehler(str(e)) from e
    return imap


def abrufen(
    cfg: dict, erledigte_uids: set[str], bekannte_ids: set[str], sicher: bool = True, oeffnen=urllib.request.urlopen
) -> tuple[list[dict], set[str]]:
    """Neue Briefankündigungen holen. Gibt (neue Briefe, aktualisierte erledigte UIDs) zurück."""
    imap = verbinde(cfg["server"], int(cfg["port"]), cfg["benutzer"], cfg["passwort"], sicher)
    neu: list[dict] = []
    erledigt = set(erledigte_uids)
    try:
        ordner = cfg.get("ordner") or "INBOX"
        typ, _ = imap.select(_zitat(ordner), readonly=True)
        if typ != "OK":
            raise VerbindungsFehler(f"Ordner {ordner} nicht gefunden")
        seit = datetime.now() - timedelta(days=int(cfg.get("tage", 14)))
        datum = f"{seit.day:02d}-{MONATE[seit.month - 1]}-{seit.year}"
        # Briefe von der Post; Pakete von den gewählten Diensten (DHL, Hermes, DPD, Amazon) und eigenen Absendern
        quellen = [(cfg["absender"], "brief", "")] + paket_quellen(cfg)
        for absender, art, dienst in quellen:
            typ, daten = imap.uid("SEARCH", None, "FROM", _zitat(absender), "SINCE", datum)
            if typ != "OK":
                raise VerbindungsFehler("Suche im Postfach fehlgeschlagen")
            uids = (daten[0] or b"").decode().split() if daten else []
            for uid in uids[-40:]:
                schluessel = f"{ordner}:{uid}"
                if schluessel in erledigt:
                    continue
                typ, teile = imap.uid("FETCH", uid, "(BODY.PEEK[])")
                roh = next((t[1] for t in teile or [] if isinstance(t, tuple) and isinstance(t[1], bytes)), None)
                if typ != "OK" or not roh:
                    continue
                brief = werte_aus(roh, oeffnen, art, dienst)
                erledigt.add(schluessel)
                if brief and brief["id"] not in bekannte_ids:
                    neu.append(brief)
                    bekannte_ids.add(brief["id"])
    finally:
        try:
            imap.logout()
        except Exception:  # noqa: BLE001
            pass
    return neu, erledigt


def _zitat(s: str) -> str:
    return '"' + str(s).replace("\\", "\\\\").replace('"', '\\"') + '"'


def paket_zusammenfuehren(alt: dict, neu: dict) -> None:
    """Weitere Mail zu einer schon bekannten Sendung: Stand und Verlauf aktualisieren statt eines zweiten Eintrags.
    Die neueste Mail bestimmt den Stand; „datum“ wird zur Zeit der letzten Meldung (sortiert die Sendung nach oben)."""
    verlauf = alt.get("verlauf") or [{"stand": alt.get("stand") or "unterwegs", "datum": alt["datum"]}]
    if not any(v["datum"] == neu["datum"] and v["stand"] == neu.get("stand") for v in verlauf):
        verlauf.append({"stand": neu.get("stand") or "unterwegs", "datum": neu["datum"]})
    verlauf.sort(key=lambda v: v["datum"])
    alt["verlauf"] = verlauf[-10:]
    alt["erste"] = min(alt.get("erste") or alt["datum"], verlauf[0]["datum"])
    alt["stand"] = verlauf[-1]["stand"]
    alt["datum"] = max(alt["datum"], neu["datum"])
    if not alt.get("absender") and neu.get("absender"):
        alt["absender"] = neu["absender"]
    for k in ("erwartet", "zeitfenster", "dienst", "empfaenger", "abholcode", "abholort", "abholadresse", "abholfrist"):
        if neu.get(k) and (k != "dienst" or not alt.get(k)):
            alt[k] = neu[k]


def paket_quellen(cfg: dict) -> list[tuple[str, str, str]]:
    """(Absender, "paket", Dienst) für die IMAP-Suche. Ohne Einstellung „paketdienste“ (bis 1.3): alle Dienste,
    außer der alte Paket-Absender war leer (= keine Pakete)."""
    dienste = cfg.get("paketdienste")
    alt = [a.strip() for a in re.split(r"[,;]+", cfg.get("paket_absender") or "") if a.strip()]
    if dienste is None:
        dienste = pakete.ALLE if alt else []
    quellen = [(a, "paket", d) for d in dienste if d in pakete.DIENSTE for a in pakete.DIENSTE[d]["absender"]]
    bekannt = {q[0] for q in quellen}
    # eigene Absender (z. B. ein Shop), die zu keinem Dienst gehören
    quellen += [(a, "paket", pakete.dienst_von_absender(a)) for a in alt if a not in bekannt and not pakete.dienst_von_absender(a)]
    return quellen


def naechster_abruf(zeiten: list[tuple[int, int]], jetzt: datetime) -> datetime:
    """Nächster eingestellter Abrufzeitpunkt nach jetzt (gleiche Zeitzone wie jetzt)."""
    kandidaten = []
    for tag in (0, 1):
        for h, m in zeiten:
            t = (jetzt + timedelta(days=tag)).replace(hour=h, minute=m, second=0, microsecond=0)
            if t > jetzt:
                kandidaten.append(t)
    return min(kandidaten)
