"""Nachgebauter IMAP-Server (ohne TLS) und Testmails für die Tests."""

from __future__ import annotations

import base64
import os
import re
import socketserver
import threading

BILD_GROSS = os.urandom(48000)
LOGO = os.urandom(900)


def _b64(b: bytes) -> str:
    s = base64.b64encode(b).decode()
    return "\r\n".join(s[i:i + 76] for i in range(0, len(s), 76))


MAIL_EINGEBETTET = "\r\n".join([
    "From: Deutsche Post AG <ankuendigung@brief.deutschepost.de>",
    "Subject: Ein Brief ist unterwegs zu Ihnen",
    "Date: Sat, 01 Aug 2026 01:01:00 +0200",
    "Message-ID: <abc123@brief.deutschepost.de>",
    "MIME-Version: 1.0",
    'Content-Type: multipart/related; boundary="GRENZE1"',
    "",
    "--GRENZE1",
    "Content-Type: text/html; charset=utf-8",
    "Content-Transfer-Encoding: quoted-printable",
    "",
    "<p>Lieber Herr Muster,</p><p>ein Brief von <i>Hansemerkur KV AG Hauptv=",
    "erwaltung</i> ist unterwegs zu Ihnen und wird Ihnen in den n=C3=A4chsten Tagen zugestellt.</p>",
    '<img src=3D"cid:logo"><img src=3D"cid:brief">',
    "--GRENZE1",
    'Content-Type: image/png; name="logo.png"',
    "Content-Transfer-Encoding: base64",
    "Content-ID: <logo>",
    "",
    _b64(LOGO),
    "--GRENZE1",
    'Content-Type: image/jpeg; name="brief.jpg"',
    "Content-Transfer-Encoding: base64",
    "Content-ID: <brief>",
    "",
    _b64(BILD_GROSS),
    "--GRENZE1--",
    "",
]).encode()

MAIL_VERLINKT = "\r\n".join([
    "From: ankuendigung@brief.deutschepost.de",
    "Subject: =?UTF-8?B?RWluIEJyaWVmIGlzdCB1bnRlcndlZ3M=?=",
    "Date: Mon, 19 Oct 2026 01:05:00 +0200",
    "Message-ID: <def456@brief.deutschepost.de>",
    "Content-Type: text/html; charset=utf-8",
    "",
    '<p>ein Brief von <b>Finanzamt Hamburg-Nord</b> ist unterwegs zu Ihnen.</p>'
    '<img src="https://briefankuendigung.deutschepost.de/scan/xyz.jpg"><img src="https://tracker.example.com/pixel.gif">',
    "",
]).encode()

MAIL_LATIN1 = (
    "From: ankuendigung@brief.deutschepost.de\r\n"
    "Subject: =?ISO-8859-1?Q?Ein_Brief_f=FCr_Sie?=\r\n"
    "Date: Tue, 20 Oct 2026 01:07:00 +0200\r\n"
    "Message-ID: <ghi789@brief.deutschepost.de>\r\n"
    "Content-Type: text/plain; charset=iso-8859-1\r\n"
    "Content-Transfer-Encoding: 8bit\r\n\r\n"
    "ein Brief von Bezirksamt Wandsbek - Bürgeramt ist unterwegs zu Ihnen.\r\n"
).encode("latin-1")


MAIL_DHL = "\r\n".join([
    "From: DHL Paket <noreply@dhl.de>",
    "Subject: Ihre DHL Sendung kommt heute",
    "Date: Wed, 21 Oct 2026 07:12:00 +0200",
    "Message-ID: <paket1@dhl.de>",
    "Content-Type: text/html; charset=utf-8",
    "",
    "<p>Guten Tag,</p><p>Ihre Sendung von <b>Thalia Bücher GmbH</b> wird heute zugestellt.</p>"
    "<p>Sendungsnummer: 00340434161094015902</p><img src=\"https://www.dhl.de/werbung.jpg\">",
    "",
]).encode()

MAIL_DHL_NEWSLETTER = "\r\n".join([
    "From: DHL <newsletter@dhl.de>",
    "Subject: Unsere Herbst-Angebote",
    "Date: Wed, 21 Oct 2026 09:00:00 +0200",
    "Message-ID: <news1@dhl.de>",
    "Content-Type: text/plain; charset=utf-8",
    "",
    "Jetzt Briefmarken online kaufen und sparen.",
    "",
]).encode()


class ImapNachbau:
    """Startet einen kleinen IMAP-Server in einem Thread."""

    def __init__(self, mails: dict[int, bytes], passwort: str = "richtig") -> None:
        self.mails = mails
        self.passwort = passwort
        self.befehle: list[str] = []
        self.nicht_gepeekt = False
        nachbau = self

        class Handler(socketserver.StreamRequestHandler):
            def handle(self) -> None:
                self.wfile.write(b"* OK Nachbau bereit\r\n")
                while True:
                    zeile = self.rfile.readline()
                    if not zeile:
                        return
                    z = zeile.decode().rstrip("\r\n")
                    tag, _, rest = z.partition(" ")
                    cmd, _, arg = rest.partition(" ")
                    cmd = cmd.upper()
                    nachbau.befehle.append(cmd + (" " + arg.split(" ")[0] if cmd == "UID" else ""))
                    if cmd == "CAPABILITY":
                        self.wfile.write(b"* CAPABILITY IMAP4rev1\r\n" + tag.encode() + b" OK CAPABILITY completed\r\n")
                    elif cmd == "LOGIN":
                        m = re.match(r'^(\S+|"(?:[^"\\]|\\.)*") ("(?:[^"\\]|\\.)*"|\S+)$', arg)
                        pw = m.group(2).strip('"').replace('\\"', '"').replace("\\\\", "\\") if m else ""
                        ok = pw == nachbau.passwort
                        self.wfile.write(tag.encode() + (b" OK LOGIN completed\r\n" if ok else b" NO [AUTHENTICATIONFAILED] Invalid credentials\r\n"))
                    elif cmd in ("EXAMINE", "SELECT"):
                        self.wfile.write(b"* %d EXISTS\r\n" % len(nachbau.mails) + tag.encode() + b" OK [READ-ONLY] done\r\n")
                    elif cmd == "UID" and arg.upper().startswith("SEARCH"):
                        # FROM "x" wie bei echten Servern als Teiltext des Absenders
                        m = re.search(r'FROM "((?:[^"\\]|\\.)*)"', arg, re.I)
                        von = m.group(1).lower() if m else ""
                        def absender(mail: bytes) -> str:
                            k = re.search(rb"^From:(.*)$", mail, re.M | re.I)
                            return k.group(1).decode("latin-1").lower() if k else ""
                        ids = " ".join(str(k) for k, v in nachbau.mails.items() if von in absender(v))
                        self.wfile.write(f"* SEARCH {ids}\r\n{tag} OK SEARCH completed\r\n".encode())
                    elif cmd == "UID" and arg.upper().startswith("FETCH"):
                        if "PEEK" not in arg.upper():
                            nachbau.nicht_gepeekt = True
                        uid = int(arg.split(" ")[1])
                        m = nachbau.mails[uid]
                        self.wfile.write(f"* 1 FETCH (UID {uid} BODY[] {{{len(m)}}}\r\n".encode() + m + b")\r\n" + tag.encode() + b" OK FETCH completed\r\n")
                    elif cmd == "LOGOUT":
                        self.wfile.write(b"* BYE\r\n" + tag.encode() + b" OK LOGOUT completed\r\n")
                        return
                    else:
                        self.wfile.write(tag.encode() + b" BAD unbekannt\r\n")

        class Server(socketserver.ThreadingTCPServer):
            allow_reuse_address = True
            daemon_threads = True

        self.server = Server(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()


class Antwort:
    """Ersatz für urllib-Antworten beim Nachladen verlinkter Bilder."""

    def __init__(self, daten: bytes, typ: str = "image/jpeg") -> None:
        self.daten, self.headers = daten, {"Content-Type": typ}

    def read(self, n: int = -1) -> bytes:
        return self.daten

    def __enter__(self):
        return self

    def __exit__(self, *a) -> None:
        return None


def oeffnen_nachbau(geladen: list[str]):
    def oeffnen(req, timeout=15):
        geladen.append(req.full_url)
        return Antwort(os.urandom(30000) if "deutschepost" in req.full_url else os.urandom(50))
    return oeffnen


def _mail(von: str, an: str, betreff: str, datum: str, mid: str, text: str) -> bytes:
    return "\r\n".join([f"From: {von}", f"To: {an}", f"Subject: {betreff}", f"Date: {datum}", f"Message-ID: <{mid}>",
                        "Content-Type: text/plain; charset=utf-8", "", text, ""]).encode()


MAIL_HERMES = _mail("Hermes <noreply@paketankuendigung.myhermes.de>", "kim@gmx.de", "Ihre Hermes Sendung ist unterwegs",
                    "Wed, 21 Oct 2026 06:10:00 +0200", "h1@myhermes.de",
                    "Hallo Kim, Ihre Sendung von OTTO GmbH & Co KG ist unterwegs. Voraussichtliche Zustellung: 23.10.2026 "
                    "zwischen 10:00 und 13:00 Uhr. Sendungsnummer: H1002345678901234567")
MAIL_DPD = _mail("DPD <noreply@dpd.de>", "kim@gmx.de", "Ihr DPD Paket kommt heute",
                 "Wed, 21 Oct 2026 07:20:00 +0200", "d1@dpd.de",
                 "Ihr Paket von Zalando SE kommt heute zwischen 9:30 und 10:30. Paketnummer 01234567890123")
MAIL_AMAZON_1 = _mail("Amazon.de <versandbestaetigung@amazon.de>", "kim@gmx.de", "Versandt: „Kindle Paperwhite (16 GB)“",
                      "Tue, 20 Oct 2026 18:00:00 +0200", "a1@amazon.de",
                      "Hallo Kim, Ihr Paket mit „Kindle Paperwhite (16 GB)“ wurde versandt. Lieferung: Donnerstag, 22. Oktober. "
                      "Bestellnr. 302-1234567-7654321. Sendungsnummer: TBA301234567890")
MAIL_AMAZON_2 = _mail("Amazon.de <shipment-tracking@amazon.de>", "johanna.k@gmx.de", "Zugestellt: „Kaffeebohnen 1 kg“",
                      "Wed, 21 Oct 2026 14:00:00 +0200", "a2@amazon.de",
                      "Ihr Paket mit „Kaffeebohnen 1 kg“ wurde zugestellt. Tracking-ID: TBA309999999999")
MAIL_AMAZON_WERBUNG = _mail("Amazon.de <store-news@amazon.de>", "kim@gmx.de", "Angebote der Woche",
                            "Wed, 21 Oct 2026 09:00:00 +0200", "a3@amazon.de", "Entdecken Sie unsere Angebote und Gutscheine.")
