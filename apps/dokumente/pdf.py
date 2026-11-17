"""PDF aus HTML mit WeasyPrint, im Brief nach DIN 5008 (Form B).

Der Rahmen (``templates/dokumente/pdf/brief.html``) ist eine Systemvorlage: Sie legt
Seitenränder, Anschriftfeld, Informationsblock, Falz- und Lochmarken fest. Hinein kommen
der Briefkopf (eine Vorlage der Art „briefkopf“) und der Inhalt des Schreibens, beide
schon mit ``apps.dokumente.platzhalter`` gerendert.

WeasyPrint braucht Systembibliotheken (Pango, HarfBuzz). Fehlen sie, meldet
``verfuegbar()`` den Grund; ``erzeugen()`` wirft dann ``PdfNichtVerfuegbar``.
Unter macOS mit Homebrew: ``brew install pango`` und ``DYLD_FALLBACK_LIBRARY_PATH``
auf ``$(brew --prefix)/lib`` (setzt das Makefile).
"""

from __future__ import annotations

import datetime
import logging
from functools import cache
from typing import Any

from django.template.loader import render_to_string

# WeasyPrint meldet jeden Arbeitsschritt auf INFO; das gehört nicht ins Log.
logging.getLogger("weasyprint.progress").setLevel(logging.WARNING)


class PdfNichtVerfuegbar(RuntimeError):
    pass


HINWEIS = (
    "WeasyPrint findet seine Systembibliotheken nicht (Pango, HarfBuzz). "
    "macOS: brew install pango und DYLD_FALLBACK_LIBRARY_PATH=$(brew --prefix)/lib setzen "
    "(make test macht das). Ubuntu: apt install libpango-1.0-0 libpangoft2-1.0-0 "
    "libharfbuzz0b fonts-dejavu-core."
)


@cache
def _weasyprint() -> Any:
    try:
        import weasyprint
    except (OSError, ImportError) as fehler:
        raise PdfNichtVerfuegbar(f"{HINWEIS} ({type(fehler).__name__})") from fehler
    return weasyprint


def verfuegbar() -> tuple[bool, str]:
    """(True, "") wenn WeasyPrint PDFs erzeugen kann, sonst (False, Grund)."""
    try:
        _weasyprint()
    except PdfNichtVerfuegbar as fehler:
        return False, str(fehler)
    return True, ""


def erzeugen(html: str) -> bytes:
    """Erzeugt ein PDF aus fertigem HTML."""
    weasyprint = _weasyprint()
    pdf: bytes = weasyprint.HTML(string=html, base_url=None).write_pdf()
    return pdf


def brief_html(
    *,
    briefkopf_html: str,
    inhalt_html: str,
    betreff: str,
    empfaenger_anschrift: str,
    datum: datetime.date,
    zeichen: str,
) -> str:
    """Setzt Briefkopf, Anschrift, Informationsblock, Betreff und Inhalt zusammen.

    ``briefkopf_html`` und ``inhalt_html`` sind bereits gerendert und maskiert;
    ``betreff`` und ``empfaenger_anschrift`` sind Text und werden hier maskiert.
    """
    return render_to_string(
        "dokumente/pdf/brief.html",
        {
            "briefkopf_html": briefkopf_html,
            "inhalt_html": inhalt_html,
            "betreff": betreff,
            "anschrift_zeilen": [z for z in empfaenger_anschrift.splitlines() if z.strip()],
            "datum": datum,
            "zeichen": zeichen,
        },
    )
