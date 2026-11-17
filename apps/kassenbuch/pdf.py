"""PDF aus HTML mit WeasyPrint.

WeasyPrint braucht Systembibliotheken (Pango, HarfBuzz). Im Produktions-Image und in der
Pipeline sind sie installiert; unter macOS fehlen sie oft. Dann meldet ``html_zu_pdf`` das
mit ``PdfNichtVerfuegbar``, statt den Aufrufer mit einem ``OSError`` zu überraschen.
"""

from __future__ import annotations

from functools import cache


class PdfNichtVerfuegbar(RuntimeError):
    """WeasyPrint oder seine Systembibliotheken (Pango) fehlen."""


@cache
def _grund() -> str | None:
    try:
        import weasyprint  # noqa: F401
    except (ImportError, OSError) as fehler:
        return f"WeasyPrint lässt sich nicht laden ({type(fehler).__name__}); fehlt Pango?"
    return None


def verfuegbar() -> bool:
    return _grund() is None


def grund() -> str:
    """Warum kein PDF entsteht (leer, wenn WeasyPrint bereitsteht)."""
    return _grund() or ""


def html_zu_pdf(html: str) -> bytes:
    """Setzt eine HTML-Seite als PDF. Bilder oder Schriften von außen gibt es nicht."""
    meldung = _grund()
    if meldung is not None:
        raise PdfNichtVerfuegbar(meldung)
    from weasyprint import HTML

    pdf: bytes = HTML(string=html).write_pdf()
    return pdf
