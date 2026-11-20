"""Review 3, Punkt 8: Arbeitslisten – Titel ohne Dopplung, Pfade und Nummern als Links.

Titel: „Belege zuordnen · Belege zuordnen“ war doppelt. Der Titel nennt jetzt den Gegenstand
(„Zänker AG · Rechnung 4779“, „NW-G-0004 · Jochem Stumpf“); die Namen kommen beim Anzeigen
aus dem Bezug (``verweise``), nie aus dem Hinweistext.
"""

from __future__ import annotations

import re

import pytest
from django.contrib.messages.storage.base import Message
from django.template.loader import render_to_string
from django.test import Client
from django.urls import reverse
from django.utils.html import format_html, strip_tags

from apps.kern import arbeitslisten, meldungen, services, verweise
from apps.kern.models import Arbeitsposten

pytestmark = pytest.mark.django_db


def _titel_html(html: str, posten: Arbeitsposten) -> str:
    treffer = re.search(rf'id="posten-{posten.pk}-titel">(.*?)</p>', html, flags=re.DOTALL)
    assert treffer, f"Posten #{posten.pk} fehlt in der Liste"
    return strip_tags(treffer.group(1)).strip()


def test_jeder_bezug_hat_eine_beschreibung() -> None:
    """Review 3, Punkt 8: Jede Art von Bezug, die ein Posten haben kann, nennt ihren
    Gegenstand in Worten. Ohne Beschreibung stünde in der Liste wieder die Art als Titel."""
    ohne = [m.__name__ for m, (_t, _u, b) in verweise._VERWEISE.items() if b is None]
    assert ohne == []


def test_titel_je_art_ohne_dopplung(testhof: object, angemeldet: Client) -> None:
    """Review 3, Punkt 8: Für jede Art von Bezug im Testhof zeigt die Arbeitsliste als Titel
    den Gegenstand, nie die Art ein zweites Mal („Belege zuordnen · Belege zuordnen“)."""
    angelegt: list[Arbeitsposten] = []
    for modell in verweise._VERWEISE:
        bezug = modell.objects.order_by("pk").first()  # type: ignore[attr-defined]
        if bezug is None:
            continue
        angelegt.append(
            services.arbeitsposten_anlegen(f"Prüfen {modell.__name__}", bezug=bezug, hinweis="")
        )
    assert len(angelegt) >= 15  # der Testhof hat die meisten Arten von Bezug

    html = angemeldet.get(reverse("kern:arbeitslisten")).content.decode()

    for posten in angelegt:
        titel = _titel_html(html, posten)
        assert posten.art in titel
        gegenstand = titel.removeprefix(posten.art).strip()
        assert gegenstand, f"{posten.art}: kein Gegenstand"
        assert posten.art not in gegenstand, f"{posten.art}: doppelt"


def test_titel_der_beispiele_aus_dem_review(testhof: object) -> None:
    """Review 3, Punkt 8: Beleg bzw. Rechnung mit Lieferant und Nummer, Buchungsanfrage mit
    NW-ID und Name aus dem Bezug, nicht aus dem Hinweistext."""
    from apps.anbindungen.models import Buchungsanfrage
    from apps.dokumente.models import Eingangsrechnung

    rechnung = Eingangsrechnung.objects.select_related("beleg__lieferant").first()
    anfrage = Buchungsanfrage.objects.select_related("einheit").exclude(einheit=None).first()
    assert rechnung is not None and anfrage is not None
    posten_r = services.arbeitsposten_anlegen("Rechnung zahlen", bezug=rechnung, hinweis="")
    posten_a = services.arbeitsposten_anlegen("Buchungsanfrage prüfen", bezug=anfrage)

    (zeile_r, zeile_a) = arbeitslisten.zeilen([posten_r, posten_a])

    nummer = rechnung.rechnungsnummer or rechnung.beleg.rechnungsnummer
    assert zeile_r.titel == f"{rechnung.beleg.lieferant.name} · Rechnung {nummer}"
    assert zeile_a.titel.startswith(f"{anfrage.einheit.nw_id} · {anfrage.kontakt_name}")
    assert anfrage.kontakt_name not in posten_a.hinweis


def test_posten_ohne_bezug_nennt_den_anfang_des_hinweises() -> None:
    """Review 3, Punkt 8: Ohne Bezug (etwa „Sicherung prüfen“) steht der Anfang des Hinweises
    als Titel, nicht noch einmal die Art; ohne Hinweis die Nummer des Postens."""
    mit = services.arbeitsposten_anlegen(
        "Sicherung prüfen", hinweis="Sicherung nicht in Ordnung: Statusdatei fehlt."
    )
    ohne = services.arbeitsposten_anlegen("Sicherung prüfen", hinweis="")

    titel = {z.posten.pk: z.titel for z in arbeitslisten.zeilen([mit, ohne])}

    assert titel[mit.pk] == "Sicherung nicht in Ordnung"
    assert titel[ohne.pk] == f"Posten #{ohne.pk}"


# --- Pfade und Nummern als Links -------------------------------------------------------------


def test_pfade_als_links() -> None:
    """Review 3, Punkt 8 (Review 1, Punkt 8): Pfade der Anwendung in Meldungen werden Links,
    unbekannte Pfade und Dateinamen nicht; der übrige Text bleibt maskiert."""
    html = str(meldungen.verlinken("Erneuern: /bank/konten/9/freigabe/. <b>x</b>"))
    assert '<a href="/bank/konten/9/freigabe/">/bank/konten/9/freigabe/</a>.' in html
    assert "&lt;b&gt;x&lt;/b&gt;" in html
    assert "<a" not in str(meldungen.verlinken("Siehe docs/betrieb.md und /gibt/es/nicht/."))
    assert '<a href="/kassenbuch/regeln/3/">Regel #3</a>' in str(
        meldungen.verlinken("Für diese Gegenpartei gibt es schon die Regel #3.")
    )


def test_fertiges_html_bleibt_unberuehrt() -> None:
    """Review 3, Punkt 8: Hat eine Meldung schon einen Link (format_html), entsteht kein Link
    im Link, und das Attribut bleibt, wie es ist."""
    meldung = Message(
        25, format_html('{} · <a href="{}">ansehen</a>', "Lauf #4 fertig", "/bank/konten/9/")
    )
    html = str(meldungen.verlinken(meldung))
    assert html.count("<a ") == 2
    assert '<a href="/bank/konten/9/">ansehen</a>' in html
    assert f'<a href="{reverse("kern:laeufe")}#lauf-4">Lauf #4</a> fertig' in html


def test_pfade_als_links_im_hinweis_toast_und_lauf(angemeldet: Client) -> None:
    """Review 3, Punkt 8: Hinweistexte am Posten, Toasts und Lauf-Meldungen nutzen dieselbe
    Stelle (``|verlinkt``): Pfade und Nummern sind überall Links."""
    pfad = "/bank/"
    posten = services.arbeitsposten_anlegen(
        "Bankfreigabe läuft ab", hinweis=f"Bitte erneuern: {pfad}"
    )
    lauf = services.lauf_starten("selbsttest")
    services.lauf_beenden(
        lauf,
        gebucht=0,
        arbeitsposten=1,
        fehler=0,
        meldung=f"Arbeitsposten #{posten.pk} angelegt, siehe {pfad}",
    )

    liste = angemeldet.get(reverse("kern:arbeitslisten")).content.decode()
    assert f'Bitte erneuern: <a href="{pfad}">{pfad}</a>' in liste

    toast = render_to_string("bausteine/toast.html", {"text": f"Erledigt, siehe {pfad}."})
    assert f'<a href="{pfad}">{pfad}</a>.' in toast

    laeufe = angemeldet.get(reverse("kern:laeufe")).content.decode()
    assert f'<a href="{pfad}">{pfad}</a>' in laeufe
    assert f"#posten-{posten.pk}" in laeufe


def test_toast_mit_link_bleibt_stehen() -> None:
    """Review 3, Punkt 3: Toasts mit Link bleiben stehen, bis man sie schließt; ohne Link
    verschwinden sie weiter (zentral in ``bausteine.js``)."""
    from pathlib import Path

    from django.conf import settings

    skript = (Path(settings.BASE_DIR) / "static" / "js" / "bausteine.js").read_text()
    assert '!t.classList.contains("toast-fehler") && !t.querySelector("a[href]")' in skript
