"""Eingegangene Dateien prüfen und Fotos in PDF umwandeln (H6.2).

Entscheidung: Ein Foto vom Handy wird mit Pillow zu einem einseitigen PDF; das Foto selbst
bleibt als Original am Dokument („ein Dokument ein PDF, Originalfotos bleiben“). So hat jedes
Dokument der Ablage ein PDF, die Belegmappe hängt alle gleich an (pypdf), und das Original
geht nicht verloren. WeasyPrint braucht es dafür nicht: Pillow bettet das Bild direkt ein,
ohne Systembibliotheken.

Die Drehung aus den EXIF-Angaben wird vor der Umwandlung angewandt; das PDF enthält keine
EXIF-Angaben (auch keinen Aufnahmeort). Sehr große Fotos werden auf A4 mit 300 dpi verkleinert.
"""

from __future__ import annotations

import io
from dataclasses import dataclass

from PIL import Image, ImageOps, UnidentifiedImageError
from pypdf import PdfReader
from pypdf.errors import PyPdfError

PDF = "application/pdf"
#: Bildformate von Pillow -> Dateityp. HEIC (iPhone) liefert der Browser beim Hochladen als JPEG.
BILDTYPEN = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp", "MPO": "image/jpeg"}
ENDUNGEN = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp", PDF: "pdf"}
#: Längste Kante in Pixeln (A4 mit 300 dpi)
GROESSTE_KANTE = 3508
AUFLOESUNG = 300.0
#: Größte Datei, die angenommen wird
HOECHSTENS_BYTES = 25 * 1024 * 1024


class DateiUngueltig(ValueError):
    """Weder PDF noch lesbares Foto. Die Meldung nennt keine Inhalte."""


@dataclass(frozen=True)
class Eingang:
    """Eine geprüfte Datei: PDF für die Ablage und, bei Fotos, das Original."""

    pdf: bytes
    dateityp: str
    original: bytes | None = None

    @property
    def endung(self) -> str:
        return ENDUNGEN.get(self.dateityp, "bin")


def ist_pdf(inhalt: bytes) -> bool:
    return inhalt[:1024].lstrip().startswith(b"%PDF-")


def pdf_pruefen(inhalt: bytes) -> None:
    """Lässt sich das PDF lesen und hat es mindestens eine Seite? Verschlüsselte PDFs (etwa
    mit Schutz gegen Ändern) nimmt die Ablage an, ohne hineinzusehen."""
    try:
        leser = PdfReader(io.BytesIO(inhalt))
        if leser.is_encrypted:
            return
        if len(leser.pages) < 1:
            raise DateiUngueltig("Das PDF hat keine Seite.")
    except (PyPdfError, ValueError, KeyError, OSError) as fehler:
        raise DateiUngueltig("Das PDF lässt sich nicht lesen.") from fehler


def foto_zu_pdf(inhalt: bytes) -> tuple[bytes, str]:
    """Wandelt ein Foto in ein einseitiges PDF. Rückgabe: (PDF, Dateityp des Fotos)."""
    try:
        with Image.open(io.BytesIO(inhalt)) as bild:
            dateityp = BILDTYPEN.get(bild.format or "")
            if dateityp is None:
                raise DateiUngueltig(f"Bildformat {bild.format} wird nicht angenommen.")
            gedreht = ImageOps.exif_transpose(bild)
            rgb = gedreht.convert("RGB")
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as fehler:
        raise DateiUngueltig("Die Datei ist weder ein PDF noch ein lesbares Foto.") from fehler
    rgb.thumbnail((GROESSTE_KANTE, GROESSTE_KANTE))
    puffer = io.BytesIO()
    rgb.save(puffer, format="PDF", resolution=AUFLOESUNG)
    return puffer.getvalue(), dateityp


def pruefen(inhalt: bytes) -> Eingang:
    """Nimmt ein PDF oder ein Foto an. Fotos werden zu PDF, das Foto bleibt als Original."""
    if not inhalt:
        raise DateiUngueltig("Die Datei ist leer.")
    if len(inhalt) > HOECHSTENS_BYTES:
        raise DateiUngueltig("Die Datei ist größer als 25 MB.")
    if ist_pdf(inhalt):
        pdf_pruefen(inhalt)
        return Eingang(pdf=inhalt, dateityp=PDF)
    pdf, dateityp = foto_zu_pdf(inhalt)
    return Eingang(pdf=pdf, dateityp=dateityp, original=inhalt)
