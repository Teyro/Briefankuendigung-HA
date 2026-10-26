"""Paketdienste: DHL, Hermes, DPD und Amazon – Absender, Sendungsnummern, Stand, Liefertermin.

Ohne Home-Assistant-Abhängigkeiten (einzeln testbar). Die Daten kommen aus den Benachrichtigungs-Mails
der Dienste; für DHL optional zusätzlich aus der offiziellen Sendungsverfolgung (eigener API-Schlüssel).
"""

from __future__ import annotations

import hashlib
import re
from datetime import date, datetime, timedelta
from urllib.parse import quote

DIENSTE: dict[str, dict] = {
    "dhl": {"name": "DHL", "absender": ("dhl.de", "dhl.com")},
    "hermes": {"name": "Hermes", "absender": ("myhermes.de", "hermesworld.com", "hermes-europe.de")},
    "dpd": {"name": "DPD", "absender": ("dpd.de", "dpd.com", "dpdgroup.com")},
    "amazon": {"name": "Amazon", "absender": ("amazon.de", "amazon.com", "amazon.at", "amazon.co.uk")},
}
ALLE = list(DIENSTE)

MONATE = {"januar": 1, "februar": 2, "märz": 3, "maerz": 3, "april": 4, "mai": 5, "juni": 6, "juli": 7, "august": 8,
          "september": 9, "oktober": 10, "november": 11, "dezember": 12}

# Sendungsnummern je Dienst (die erste passende gewinnt)
NUMMERN = {
    "dhl": r"\b(JJD\d{12,20}|00\d{18}|\d{20}|\d{12})\b",
    "hermes": r"\b(H\d{19}|H[0-9A-Z]{19}|\d{14})\b",
    "dpd": r"\b(0\d{13}|\d{14})\b",
    "amazon": r"\b(TBA\d{9,14}|\d{3}-\d{7}-\d{7})\b",
}


def dienst_von_absender(von: str) -> str:
    """„noreply@paketankuendigung.myhermes.de“ → „hermes“ ("" = keiner)"""
    v = (von or "").lower()
    for d, info in DIENSTE.items():
        if any(a in v for a in info["absender"]):
            return d
    return ""


def dienst_von_nummer(nummer: str) -> str:
    """Dienst an der Sendungsnummer erkennen ("" = nicht eindeutig, z. B. 14 Ziffern bei Hermes und DPD)."""
    n = re.sub(r"\s+", "", nummer or "").upper()
    if re.fullmatch(r"TBA\d{9,14}|\d{3}-\d{7}-\d{7}", n):
        return "amazon"
    if re.fullmatch(r"JJD\d{12,20}|00\d{18}|\d{20}|\d{12}", n):
        return "dhl"
    if re.fullmatch(r"H[0-9A-Z]{19}", n):
        return "hermes"
    return ""


def ist_sendung(text: str) -> bool:
    """Betrifft die Mail überhaupt eine Sendung (und ist kein Newsletter, keine Rechnung, keine Bestellbestätigung)?"""
    t = text or ""
    if re.search(r"Newsletter|Angebot|Gutschein|Bewert|Rechnung ist|Ihre Rechnung|Bestellbestätigung|Bestellung bestätigt|Rücksendung erhalten", t, re.I) \
            and not re.search(r"versandt|unterwegs|zugestellt|Zustellung", t, re.I):
        return False
    return bool(re.search(r"Sendung|Paket|Päckchen|Zustellung|zugestellt|versandt|geliefert|Lieferung|unterwegs", t, re.I))


def stand(text: str) -> str:
    """unterwegs | heute | abholbereit | zugestellt"""
    t = text or ""
    if re.search(r"wurde\s.{0,40}zugestellt|ist zugestellt|zugestellt worden|erfolgreich zugestellt|wurde geliefert|Zugestellt:|"
                 r"wurde abgeholt|ist angekommen|delivered", t, re.I):
        return "zugestellt"
    if re.search(r"zur Abholung bereit|abholbereit|liegt .{0,60}zur Abholung|kann .{0,40}abgeholt werden|in der Packstation", t, re.I):
        return "abholbereit"
    if re.search(r"\bheute\b|in Zustellung|wird zugestellt|Zustellfahrzeug|auf dem Weg zu Ihnen und kommt|Heute geliefert|out for delivery", t, re.I):
        return "heute"
    return "unterwegs"


def nummer(text: str, dienst: str) -> str:
    t = re.sub(r"\s+", " ", text or "")
    m = re.search(r"(?:Sendungsnummer|Paketnummer|Trackingnummer|Sendungs-Nr\.?|Tracking-ID)\s*:?\s*([A-Z0-9-]{10,30})", t, re.I)
    if m and (not dienst or dienst_von_nummer(m.group(1)) in ("", dienst)):
        return m.group(1).upper()
    m = re.search(NUMMERN.get(dienst, NUMMERN["dhl"]), t)
    return m.group(1) if m else ""


def absender(text: str, dienst: str) -> str:
    """Wer hat geschickt? Bei Amazon der Artikel („„Kindle Paperwhite“ …“), sonst der Shop."""
    t = re.sub(r"\s+", " ", text or "")
    if dienst == "amazon":
        m = re.search(r"[„\"“](.{3,90}?)[“\"”]", t)
        return m.group(1).strip() if m else ""
    m = re.search(r"(?:Sendung|Paket|Päckchen|Lieferung|Bestellung)\s+(?:von|bei)\s+(.{2,100}?)(?:\s+(?:ist|wird|kommt|wurde|hat|befindet)\s|[,;!](?:\s|$)|\.\s+[A-ZÄÖÜ]|$)",
                  t, re.I) or re.search(r"Absender:\s*(.{2,100}?)(?:\s{2,}|[.,;]|$)", t, re.I)
    return m.group(1).strip(" „“\"'.") if m else ""


def erwartet(text: str, bezug: date) -> tuple[str, str]:
    """Voraussichtliche Zustellung: (ISO-Datum oder "", Zeitfenster wie „10:00–12:00“ oder "")."""
    t = re.sub(r"\s+", " ", text or "")
    fenster = ""
    m = re.search(r"zwischen\s+(\d{1,2})(?::(\d{2}))?\s*(?:Uhr)?\s+und\s+(\d{1,2})(?::(\d{2}))?", t, re.I)
    if m:
        fenster = f"{int(m.group(1)):02d}:{m.group(2) or '00'}–{int(m.group(3)):02d}:{m.group(4) or '00'}"
    tag: date | None = None
    if re.search(r"\b(kommt|zugestellt|geliefert|Zustellung|Lieferung)\b.{0,40}\bheute\b|\bheute\b.{0,40}\b(zugestellt|geliefert|Zustellung)", t, re.I):
        tag = bezug
    elif re.search(r"\bmorgen\b", t, re.I) and re.search(r"zugestellt|geliefert|Zustellung|Lieferung", t, re.I):
        tag = bezug + timedelta(days=1)
    m = re.search(r"(?:voraussichtlich|Lieferung|Zustellung|zugestellt|geliefert|Ankunft)[^.]{0,40}?(\d{1,2})\.\s?(\d{1,2})\.(\d{2,4})?", t, re.I)
    m2 = re.search(r"(?:voraussichtlich|Lieferung|Zustellung|Ankunft)[^.]{0,40}?(\d{1,2})\.\s?(" + "|".join(MONATE) + r")(?:\s+(\d{4}))?", t, re.I)
    try:
        if m:
            j = int(m.group(3)) if m.group(3) else bezug.year
            tag = date(j + 2000 if j < 100 else j, int(m.group(2)), int(m.group(1)))
        elif m2:
            tag = date(int(m2.group(3)) if m2.group(3) else bezug.year, MONATE[m2.group(2).lower()], int(m2.group(1)))
        if tag and not m2 and not (m and m.group(3)) and tag < bezug - timedelta(days=60):
            tag = tag.replace(year=tag.year + 1)  # Dezember-Mail mit Termin im Januar
    except ValueError:
        tag = None
    return (tag.isoformat() if tag else "", fenster)


def infos(text: str, dienst: str, bezug: date | None = None) -> dict:
    """Alles aus einer Paket-Mail: absender, sendung, stand, erwartet, zeitfenster."""
    e, f = erwartet(text, bezug or date.today())
    return {"absender": absender(text, dienst), "sendung": nummer(text, dienst), "stand": stand(text), "erwartet": e, "zeitfenster": f}


def verfolgen(dienst: str, sendung: str) -> str:
    """Seite zur Sendungsverfolgung beim Dienst"""
    if not sendung:
        return "https://www.amazon.de/gp/css/order-history" if dienst == "amazon" else ""
    n = quote(sendung, safe="")
    return {
        "dhl": f"https://www.dhl.de/de/privatkunden/pakete-empfangen/verfolgen.html?piececode={n}",
        "hermes": f"https://www.myhermes.de/empfangen/sendungsverfolgung/sendungsinformation#{n}",
        "dpd": f"https://tracking.dpd.de/status/de_DE/parcel/{n}",
        "amazon": f"https://www.amazon.de/gp/your-account/order-details?orderID={n}" if re.fullmatch(r"\d{3}-\d{7}-\d{7}", sendung)
        else f"https://track.amazon.de/tracking/{n}",
    }.get(dienst, "")


def konten_lesen(text: str) -> dict[str, str]:
    """Kundenkonten aus den Optionen: je Zeile „adresse = Name“ (z. B. zwei Amazon-Konten in einem Postfach)."""
    erg = {}
    for zeile in re.split(r"[\n;]+", text or ""):
        a, sep, n = zeile.partition("=")
        if not sep:
            a, sep, n = zeile.partition(":")
        if sep and "@" in a and n.strip():
            erg[a.strip().lower()] = n.strip()
    return erg


def sendung_id(dienst: str, sendung: str) -> str:
    """Feste ID einer von Hand hinzugefügten Sendung"""
    return hashlib.sha256(f"{dienst}:{sendung.upper()}".encode()).hexdigest()[:20]


def dhl_status(antwort: dict) -> dict | None:
    """Antwort der DHL-Sendungsverfolgung (Shipment Tracking – Unified) → {stand, erwartet, text}"""
    try:
        s = (antwort.get("shipments") or [])[0]
    except (AttributeError, IndexError, TypeError):
        return None
    st = s.get("status") or {}
    code = str(st.get("statusCode") or "").lower()
    text = str(st.get("description") or st.get("status") or "")
    if code == "delivered":
        stufe = "zugestellt"
    elif re.search(r"Zustellung|Zustellfahrzeug|out for delivery", text, re.I):
        stufe = "heute"
    elif re.search(r"Filiale|Packstation|abholbereit|zur Abholung", text, re.I):
        stufe = "abholbereit"
    else:
        stufe = "unterwegs"
    e = ""
    zeit = s.get("estimatedTimeOfDelivery") or ""
    if zeit:
        try:
            e = datetime.fromisoformat(str(zeit).replace("Z", "+00:00")).date().isoformat()
        except ValueError:
            e = ""
    return {"stand": stufe, "erwartet": e, "text": text[:160]}
