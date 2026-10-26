"""Paketdienste: DHL, Hermes, DPD, GLS, UPS und Amazon – Absender, Sendungsnummern, Stand, Liefertermin, Verlauf.

Ohne Home-Assistant-Abhängigkeiten (einzeln testbar). Die Daten kommen aus den Benachrichtigungs-Mails
der Dienste; optional zusätzlich aus der offiziellen DHL-Sendungsverfolgung und aus 17TRACK (je eigener Schlüssel).
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
    "gls": {"name": "GLS", "absender": ("gls-pakete.de", "gls-group.eu", "gls-group.com")},
    "ups": {"name": "UPS", "absender": ("ups.com",)},
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
    "gls": r"\b(\d{11,12}|[A-Z0-9]{8})\b",
    "ups": r"\b(1Z[0-9A-Z]{16})\b",
}

# Kennungen der Dienste bei 17TRACK (Amazon-Nummern kennt 17TRACK in Deutschland nicht)
SIEBZEHN = {"dhl": 7041, "hermes": 100031, "dpd": 100007, "gls": 101070, "ups": 100002}


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
    if re.fullmatch(r"1Z[0-9A-Z]{16}", n):
        return "ups"
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
        "gls": f"https://gls-group.com/DE/de/paketverfolgung?match={n}",
        "ups": f"https://www.ups.com/track?loc=de_DE&tracknum={n}",
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
    ereignisse = []
    for ev in s.get("events") or []:
        if not isinstance(ev, dict):
            continue
        ort = ((ev.get("location") or {}).get("address") or {}).get("addressLocality") or ""
        ereignisse.append(_ereignis(ev.get("timestamp"), ev.get("description") or ev.get("status"), ort))
    return {"stand": stufe, "erwartet": _tag(s.get("estimatedTimeOfDelivery")), "text": text[:160],
            "ereignisse": [e for e in ereignisse if e]}


def _tag(zeit) -> str:
    """ISO-Zeit → „2026-10-23“ ("" wenn unbrauchbar)"""
    if not zeit:
        return ""
    try:
        return datetime.fromisoformat(str(zeit).replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        m = re.match(r"\d{4}-\d{2}-\d{2}", str(zeit))
        return m.group(0) if m else ""


def _ereignis(zeit, text, ort="") -> dict | None:
    """Ein Eintrag im Verlauf: {zeit (ISO), text, ort}"""
    if not zeit or not text:
        return None
    return {"zeit": str(zeit)[:32], "text": str(text).strip()[:200], **({"ort": str(ort).strip()[:80]} if ort else {})}


def ereignisse_zusammen(alt: list | None, neu: list | None, hoechstens: int = 40) -> list[dict]:
    """Verlauf zusammenführen: ohne Doppelte, neuestes zuerst"""
    gesehen, erg = set(), []
    for e in (neu or []) + (alt or []):
        k = (e.get("zeit", "")[:16], e.get("text", ""))
        if k not in gesehen:
            gesehen.add(k)
            erg.append(e)
    return sorted(erg, key=lambda e: e.get("zeit", ""), reverse=True)[:hoechstens]


# 17TRACK: Hauptstand → unser Stand
SIEBZEHN_STAND = {"Delivered": "zugestellt", "AvailableForPickup": "abholbereit", "OutForDelivery": "heute"}


def siebzehn_status(eintrag: dict) -> dict | None:
    """Ein Eintrag aus 17TRACK gettrackinfo (data.accepted[]) → {nummer, stand, erwartet, text, ereignisse, ende, problem, dienst_name}"""
    if not isinstance(eintrag, dict):
        return None
    info = eintrag.get("track_info") or {}
    status = (info.get("latest_status") or {}).get("status") or ""
    letztes = info.get("latest_event") or {}
    text = ((letztes.get("description_translation") or {}).get("description") or letztes.get("description") or "")
    ereignisse, name = [], ""
    for p in ((info.get("tracking") or {}).get("providers") or []):
        name = name or ((p.get("provider") or {}).get("name") or "")
        for ev in p.get("events") or []:
            ereignisse.append(_ereignis(ev.get("time_iso") or ev.get("time_utc"),
                                        (ev.get("description_translation") or {}).get("description") or ev.get("description"),
                                        ev.get("location") or ""))
    schaetzung = ((info.get("time_metrics") or {}).get("estimated_delivery_date") or {})
    return {
        "nummer": str(eintrag.get("number") or ""),
        "stand": SIEBZEHN_STAND.get(status, "unterwegs"),
        "erwartet": _tag(schaetzung.get("from") or schaetzung.get("to")),
        "text": str(text)[:160],
        "ereignisse": ereignisse_zusammen([], [e for e in ereignisse if e]),
        "ende": status in ("Delivered", "Expired"),
        "problem": status in ("DeliveryFailure", "Exception"),
        "dienst_name": name,
    }


# --- Ersatz-Anbieter (wenn DHL bzw. 17TRACK nichts liefern): UPS, Ship24, öffentliche Seiten von GLS und DPD ---

def _stand_aus_text(t: str) -> str:
    s = (t or "").lower()
    nicht = any(w in s for w in ("nicht zugestellt", "konnte nicht", "not delivered"))
    if not nicht and any(w in s for w in ("zugestellt", "delivered", "ausgeliefert", "abgeholt", "abholung aus")):
        return "zugestellt"
    if any(w in s for w in ("zur abholung", "abholbereit", "liegt in der packstation", "liegt in der filiale", "pickup", "paketshop")):
        return "abholbereit"
    if nicht:
        return "unterwegs"
    if any(w in s for w in ("in zustellung", "zustellfahrzeug", "out for delivery", "wird heute")):
        return "heute"
    return "unterwegs"


def _ups_zeit(tag: str, zeit: str) -> str:
    if len(tag or "") != 8:
        return ""
    z = (zeit or "000000").ljust(6, "0")
    return f"{tag[:4]}-{tag[4:6]}-{tag[6:]}T{z[:2]}:{z[2:4]}:{z[4:6]}"


def ups_status(paket: dict) -> dict | None:
    """UPS Tracking API: trackResponse.shipment[0].package[0] → {stand, erwartet, zeitfenster, text, ereignisse, ende, problem}"""
    if not isinstance(paket, dict):
        return None
    akt = paket.get("activity") or []
    aktuell = paket.get("currentStatus") or ((akt[0] or {}).get("status") if akt else {}) or {}
    typ, text = aktuell.get("type") or "", aktuell.get("description") or ""
    termine = paket.get("deliveryDate") or []
    termin = next((d for d in termine if d.get("type") in ("SDD", "RDD", "DEL")), termine[0] if termine else {})
    t = (termin or {}).get("date") or ""
    zeit = paket.get("deliveryTime") or {}
    von, bis = zeit.get("startTime") or "", zeit.get("endTime") or ""
    ereignisse = []
    for a in akt:
        st = a.get("status") or {}
        adr = ((a.get("location") or {}).get("address") or {})
        ort = ", ".join(x for x in ((adr.get("city") or "").title(), adr.get("countryCode") or "") if x)
        beschr = st.get("description") or ""
        if beschr.isupper() and len(beschr) > 3:
            beschr = beschr[0] + beschr[1:].lower()
        ereignisse.append(_ereignis(_ups_zeit(a.get("date"), a.get("time")), beschr, ort))
    return {
        "stand": "zugestellt" if typ == "D" else _stand_aus_text(text),
        "erwartet": f"{t[:4]}-{t[4:6]}-{t[6:]}" if len(t) == 8 else "",
        "zeitfenster": f"{von[:2]}:{von[2:4]}–{bis[:2]}:{bis[2:4]}" if len(von) >= 4 and len(bis) >= 4 else "",
        "text": text[:160], "ereignisse": ereignisse_zusammen([], [e for e in ereignisse if e]),
        "ende": typ == "D", "problem": typ == "X",
    }


def ship24_status(tracking: dict) -> dict | None:
    """Ship24: data.trackings[0] → Stand"""
    if not isinstance(tracking, dict):
        return None
    sh = tracking.get("shipment") or {}
    m = sh.get("statusMilestone") or ""
    ereignisse = [_ereignis(e.get("occurrenceDatetime"), e.get("status"), e.get("location") or "") for e in tracking.get("events") or []]
    ereignisse = ereignisse_zusammen([], [e for e in ereignisse if e])
    return {
        "stand": {"delivered": "zugestellt", "available_for_pickup": "abholbereit", "out_for_delivery": "heute"}.get(m, "unterwegs"),
        "erwartet": _tag(((sh.get("delivery") or {}).get("estimatedDeliveryDate"))),
        "text": (ereignisse[0]["text"] if ereignisse else "")[:160], "ereignisse": ereignisse,
        "ende": m == "delivered", "problem": m in ("exception", "failed_attempt"),
    }


def gls_status(antwort: dict) -> dict | None:
    """Öffentliche GLS-Sendungsverfolgung (gls-group.eu …/rstt001?match=…) → Stand"""
    try:
        tu = (antwort.get("tuStatus") or [])[0]
    except (AttributeError, IndexError, TypeError):
        return None
    info = str(((tu.get("progressBar") or {}).get("statusInfo")) or "").upper()
    ereignisse = []
    for h in tu.get("history") or []:
        adr = h.get("address") or {}
        ort = ", ".join(x for x in (adr.get("city") or "", adr.get("countryName") or "") if x)
        ereignisse.append(_ereignis(f"{h.get('date') or ''}T{h.get('time') or '00:00:00'}" if h.get("date") else None, h.get("evtDscr"), ort))
    ereignisse = ereignisse_zusammen([], [e for e in ereignisse if e])
    text = ereignisse[0]["text"] if ereignisse else ""
    return {"stand": "zugestellt" if info == "DELIVERED" else "heute" if info == "INDELIVERY" else _stand_aus_text(text),
            "erwartet": "", "text": text[:160], "ereignisse": ereignisse, "ende": info == "DELIVERED", "problem": False}


def dpd_status(antwort: dict) -> dict | None:
    """Öffentliche DPD-Sendungsverfolgung (tracking.dpd.de/rest/plc/…) → Stand"""
    try:
        d = antwort["parcellifecycleResponse"]["parcelLifeCycleData"]
    except (KeyError, TypeError):
        return None
    infos = d.get("statusInfo") or []
    aktuell = next((i for i in reversed(infos) if i.get("isCurrentStatus")), None) or next((i for i in reversed(infos) if i.get("statusHasBeenReached")), {})
    status, text = aktuell.get("status") or "", aktuell.get("label") or ""
    ereignisse = [_ereignis(s.get("date"), " ".join(c for c in ((s.get("scanDescription") or {}).get("content") or []) if isinstance(c, str)),
                            (s.get("scanData") or {}).get("location") or "") for s in ((d.get("scanInfo") or {}).get("scan") or [])]
    ereignisse = ereignisse_zusammen([], [e for e in ereignisse if e])
    return {"stand": "zugestellt" if status == "DELIVERED" else "heute" if status in ("COURIER", "OUT_FOR_DELIVERY") else
            "abholbereit" if status == "PICKUP" else _stand_aus_text(text),
            "erwartet": "", "text": text[:160], "ereignisse": ereignisse, "ende": status == "DELIVERED", "problem": False}


def dhl_oeffentlich_status(antwort: dict) -> dict | None:
    """Öffentliche DHL-Sendungsverfolgung ohne Schlüssel (dhl.de bzw. deutschepost.de …/int-verfolgen/data/search)"""
    try:
        sd = (antwort.get("sendungen") or [])[0]["sendungsdetails"]
    except (AttributeError, IndexError, KeyError, TypeError):
        return None
    v = sd.get("sendungsverlauf") or {}
    ereignisse = ereignisse_zusammen([], [e for e in (_ereignis(x.get("datum"), " ".join(str(x.get("status") or "").split()), x.get("ort") or "")
                                                     for x in v.get("events") or []) if e])
    text = v.get("status") or (ereignisse[0]["text"] if ereignisse else "")
    zu = bool(sd.get("istZugestellt"))
    stand = "zugestellt" if zu else _stand_aus_text(ereignisse[0]["text"] if ereignisse else text)
    if not zu and stand == "zugestellt":
        stand = _stand_aus_text(text)
    return {"stand": stand, "erwartet": "", "text": str(text)[:160], "ereignisse": ereignisse, "ende": zu, "problem": False}
