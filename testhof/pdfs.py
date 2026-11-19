"""Kleine erfundene PDFs für den Testhof und die Tests, ohne WeasyPrint.

``einfaches_pdf`` schreibt ein einseitiges PDF mit Textzeilen in Helvetica von Hand. Gleiche
Zeilen ergeben byteweise dasselbe PDF (kein Datum, keine Zufallskennung): Die Prüfsumme des
Testhofs bleibt bei zwei Läufen gleich, und die Dublettenprüfung lässt sich gezielt testen.
"""

from __future__ import annotations

import datetime
from collections.abc import Sequence
from decimal import Decimal


def _text(zeile: str) -> bytes:
    roh = zeile.encode("cp1252", errors="replace")
    return roh.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")


def einfaches_pdf(zeilen: Sequence[str]) -> bytes:
    """Ein A4-Blatt mit den Zeilen untereinander (Umlaute über WinAnsi)."""
    inhalt = b"BT /F1 11 Tf 56 780 Td 16 TL\n"
    for nummer, zeile in enumerate(zeilen):
        if nummer:
            inhalt += b"T*\n"
        inhalt += b"(" + _text(zeile) + b") Tj\n"
    inhalt += b"ET\n"
    objekte = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
        b"<< /Length " + str(len(inhalt)).encode() + b" >>\nstream\n" + inhalt + b"endstream",
    ]
    pdf = b"%PDF-1.4\n"
    stellen = []
    for nummer, objekt in enumerate(objekte, start=1):
        stellen.append(len(pdf))
        pdf += f"{nummer} 0 obj\n".encode() + objekt + b"\nendobj\n"
    xref = len(pdf)
    pdf += f"xref\n0 {len(objekte) + 1}\n0000000000 65535 f \n".encode()
    for stelle in stellen:
        pdf += f"{stelle:010d} 00000 n \n".encode()
    pdf += (
        f"trailer\n<< /Size {len(objekte) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n"
    ).encode()
    return pdf


def _euro(betrag: Decimal) -> str:
    """Deutsches Zahlenformat wie auf einer Rechnung: 1.234,56."""
    return f"{betrag:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _iban_gruppen(iban: str) -> str:
    return " ".join(iban[i : i + 4] for i in range(0, len(iban), 4))


def rechnung_pdf(
    *,
    lieferant: str,
    nummer: str,
    datum: datetime.date | None,
    betrag: Decimal,
    leistung: str = "",
    iban: str = "",
    email: str = "",
) -> bytes:
    """Erfundene Rechnung mit Textschicht wie eine echte deutsche Rechnung (Review 2, B9).

    Der Briefkopf steht in der ersten Zeile; darunter Rechnungs-Nr., Rechnungsdatum
    (TT.MM.JJJJ), Leistung, Gesamtbetrag mit Komma und Bankverbindung. Daraus liest
    ``apps.dokumente.textschicht`` die Angaben für das Formular.
    """
    zeilen = [lieferant]
    if email:
        zeilen.append(f"E-Mail: {email}")
    zeilen += ["Rechnung (erfunden, Testhof)", f"Rechnungs-Nr.: {nummer}"]
    if datum is not None:
        zeilen.append(f"Rechnungsdatum: {datum:%d.%m.%Y}")
    if leistung:
        zeilen.append(f"Leistung: {leistung}")
    zeilen.append(f"Gesamtbetrag: {_euro(betrag)} EUR")
    if iban:
        zeilen.append(f"Bankverbindung: IBAN {_iban_gruppen(iban)}")
    return einfaches_pdf(zeilen)
