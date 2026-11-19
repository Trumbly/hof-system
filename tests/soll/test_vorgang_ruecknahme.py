"""Rücknahme ganzer Vorgänge an Verträgen: Kündigung und Konditionsänderung (Review 2, A4).

Ausgangslage aus dem Durchgang: Nach dem Zurücknehmen von „Verträge #47 geändert“
(Kündigung V-0002) war der Vertrag wieder aktiv, aber Kündigung, Bestätigung (weiter
„bereit“) und die Fristen blieben stehen.
"""

from __future__ import annotations

import datetime
from decimal import Decimal

import pytest
from django.contrib.contenttypes.models import ContentType
from django.test import Client
from django.urls import reverse
from django.utils.html import strip_tags
from pghistory.models import Events

from apps.dokumente import services as dokumente
from apps.dokumente.models import Schreiben, Vorlage
from apps.fristen.models import Frist
from apps.kern import services as kern
from apps.kern.models import Arbeitsposten
from apps.soll.models import Sollposten
from apps.stammdaten import schreiben as stammdaten_schreiben
from apps.stammdaten import services as stammdaten
from apps.stammdaten.models import Kuendigung, Vertrag
from tests.factories import BenutzerFabrik
from tests.soll.conftest import VertragBauer
from tests.stammdaten.conftest import braucht_weasyprint, vorlagen  # noqa: F401

pytestmark = pytest.mark.django_db
D = datetime.date
#: Zugang im Juni: Fristende 31.08.2026 liegt vor „heute“ (20.11.2026); die Kündigung
#: storniert also die Miete für September bis November.
ZUGANG = D(2026, 6, 1)


def soll(vertrag: Vertrag) -> list[tuple[str, datetime.date, datetime.date, Decimal]]:
    """Das geltende Soll eines Vertrags (ohne Stornos), unabhängig von den Posten-IDs."""
    return sorted(
        (p.art, p.von, p.bis, p.betrag)
        for p in Sollposten.objects.filter(vertrag=vertrag).exclude(
            status=Sollposten.Status.STORNIERT
        )
    )


def vorgang_von(objekt: object) -> object:
    """Die Vorgangs-ID der letzten Änderung an ``objekt``."""
    return Events.objects.tracks(objekt).order_by("-pgh_created_at")[0].pgh_context_id


def fristen_zu(*bezuege: object) -> list[Frist]:
    return [
        f
        for b in bezuege
        for f in Frist.objects.filter(
            bezug_typ=ContentType.objects.get_for_model(type(b)),  # type: ignore[arg-type]
            bezug_id=b.pk,  # type: ignore[attr-defined]
        )
    ]


@braucht_weasyprint
def test_kuendigung_als_vorgang_zuruecknehmen(
    neuer_vertrag: VertragBauer,
    vorlagen: dict[str, Vorlage],  # noqa: F811
) -> None:
    """Review 2, A4: Kündigung erfassen und als Vorgang zurücknehmen: Vertrag aktiv, keine
    offene Kündigung, Bestätigung verworfen, Fristen weg, Soll wie vorher."""
    vertrag = neuer_vertrag()
    vorher = soll(vertrag)

    kuendigung = stammdaten.kuendigung_erfassen(vertrag, ZUGANG)
    vertrag.refresh_from_db()
    bestaetigung = Schreiben.objects.get(art="kuendigungsbestaetigung")
    fristen = fristen_zu(kuendigung, vertrag)
    assert vertrag.status == Vertrag.Status.GEKUENDIGT and vertrag.ende == D(2026, 8, 31)
    assert bestaetigung.status == Schreiben.Status.BEREIT and len(fristen) == 2
    assert soll(vertrag) != vorher  # September bis November storniert
    vid = vorgang_von(kuendigung)

    bericht = kern.vorgang_zuruecknehmen(vid)  # type: ignore[arg-type]

    vertrag.refresh_from_db()
    bestaetigung.refresh_from_db()
    assert (vertrag.status, vertrag.ende) == (Vertrag.Status.AKTIV, None)
    assert not Kuendigung.objects.filter(vertrag=vertrag).exists()
    assert bestaetigung.status == Schreiben.Status.VERWORFEN
    assert not Frist.objects.filter(pk__in=[f.pk for f in fristen]).exists()
    assert not vertrag.checklisten.exists()
    assert soll(vertrag) == vorher
    assert bericht.titel == f"Kündigung {vertrag.nummer} erfasst"
    texte = [s.text for s in bericht.schritte]
    assert f"Schreiben #{bestaetigung.pk} (Kündigungsbestätigung) verworfen" in texte
    assert f"Vertrag {vertrag.nummer}: Ende 31.08.2026 → –, Status gekündigt → aktiv" in texte
    assert "Frist #" in " ".join(texte) and f"Kündigung #{kuendigung.pk} entfernt" in texte
    assert any(t.startswith(f"Soll {vertrag.nummer} neu gestellt: 3 Posten neu") for t in texte)


def test_kuendigung_ohne_vorlage_erledigt_den_arbeitsposten(neuer_vertrag: VertragBauer) -> None:
    """Review 2, A4: Ohne Vorlage legt die Kündigung den Arbeitsposten „Schreiben erstellen“
    an. Die Rücknahme erledigt ihn (gelöscht wird er nicht, damit die Aufgabe im Hof-Backlog
    schließt)."""
    vertrag = neuer_vertrag()
    kuendigung = stammdaten.kuendigung_erfassen(vertrag, ZUGANG)
    posten = Arbeitsposten.objects.get(art=stammdaten_schreiben.ART)

    kern.vorgang_zuruecknehmen(vorgang_von(kuendigung))  # type: ignore[arg-type]

    posten.refresh_from_db()
    assert posten.status == Arbeitsposten.Status.ERLEDIGT
    assert not Kuendigung.objects.exists()


@braucht_weasyprint
def test_kuendigung_mit_versandter_bestaetigung_ist_gesperrt(
    neuer_vertrag: VertragBauer,
    vorlagen: dict[str, Vorlage],  # noqa: F811
    angemeldet: Client,
) -> None:
    """Review 2, A4: Ist ein Schreiben aus dem Vorgang schon versandt, ist die Rücknahme
    gesperrt. Die Seite sagt warum, mit Link zum Schreiben; nichts wird geändert."""
    vertrag = neuer_vertrag()
    kuendigung = stammdaten.kuendigung_erfassen(vertrag, ZUGANG)
    vid = vorgang_von(kuendigung)
    bestaetigung = Schreiben.objects.get(art="kuendigungsbestaetigung")
    dokumente.schreiben_senden(
        bestaetigung, BenutzerFabrik(), pruefsumme=bestaetigung.pdf_sha256, anwalt_gesehen=True
    )

    with pytest.raises(kern.RuecknahmeGesperrt) as fehler:
        kern.vorgang_zuruecknehmen(vid)  # type: ignore[arg-type]
    (sperre,) = fehler.value.sperren
    assert sperre.grund.startswith(f"Schreiben #{bestaetigung.pk} (Kündigungsbestätigung) ist")
    assert "gesendet" in sperre.grund
    assert sperre.url == reverse("dokumente:schreiben", args=[bestaetigung.pk])

    seite = angemeldet.get(reverse("kern:vorgang", args=[vid])).content.decode()
    assert "lässt sich nicht zurücknehmen" in seite
    assert f'href="{sperre.url}"' in seite
    assert "Ganzen Vorgang zurücknehmen" not in seite
    antwort = angemeldet.post(reverse("kern:vorgang_zuruecknehmen", args=[vid]), follow=True)
    assert "Nicht zurückgenommen: Schreiben #" in strip_tags(antwort.content.decode())
    vertrag.refresh_from_db()
    assert vertrag.status == Vertrag.Status.GEKUENDIGT
    assert Kuendigung.objects.filter(pk=kuendigung.pk).exists()


def test_konditionsaenderung_zuruecknehmen_soll_stimmt_sofort(
    neuer_vertrag: VertragBauer,
) -> None:
    """Review 2, A4 (Lücke aus Schritt 5, „Konditionsänderung über Zurücknehmen“): Nach der
    Rücknahme einer rückwirkenden Mietänderung stimmt das Soll sofort, ohne Lauf."""
    vertrag = neuer_vertrag(miete="85.00")
    vorher = soll(vertrag)

    kondition = stammdaten.kondition_ab(vertrag, D(2026, 10, 1), miete=Decimal("95.00"))
    assert {p[3] for p in soll(vertrag) if p[1] >= D(2026, 10, 1)} == {Decimal("95.00")}
    vid = vorgang_von(kondition)

    bericht = kern.vorgang_zuruecknehmen(vid)  # type: ignore[arg-type]

    assert soll(vertrag) == vorher
    assert not vertrag.konditionen.filter(pk=kondition.pk).exists()
    assert vertrag.konditionen.get().gueltig_bis is None
    assert bericht.titel == f"Miete {vertrag.nummer} ab 01.10.2026 geändert"


def test_spaeter_geaenderter_vertrag_bricht_mit_datum_ab(neuer_vertrag: VertragBauer) -> None:
    """Review 2, A4: Wurde der Vertrag nach der Kündigung erneut geändert, bricht die Rücknahme
    ab: „V-… wurde danach am … erneut geändert“. Nichts ist zurückgenommen."""
    vertrag = neuer_vertrag()
    kuendigung = stammdaten.kuendigung_erfassen(vertrag, ZUGANG)
    vid = vorgang_von(kuendigung)
    vertrag.refresh_from_db()
    stammdaten.vertrag_aendern(vertrag, ende=D(2026, 9, 30))

    with pytest.raises(kern.RuecknahmeNichtMoeglich) as fehler:
        kern.vorgang_zuruecknehmen(vid)  # type: ignore[arg-type]

    meldung = str(fehler.value)
    assert meldung.startswith(f"Vertrag {vertrag.nummer} wurde danach am ")
    assert f"im Vorgang „Vertrag {vertrag.nummer} geändert“ erneut geändert (Ende)" in meldung
    assert Kuendigung.objects.filter(pk=kuendigung.pk).exists()


@braucht_weasyprint
def test_protokoll_zeigt_den_kuendigungsvorgang_mit_deutschen_feldnamen(
    neuer_vertrag: VertragBauer,
    vorlagen: dict[str, Vorlage],  # noqa: F811
    angemeldet: Client,
) -> None:
    """Review 2, A4 und B14: „Kündigung V-… erfasst · n Änderungen“, Feldnamen und Werte auf
    Deutsch („Status: aktiv → gekündigt“); inhalt_html und pdf_sha256 nur in den technischen
    Angaben."""
    vertrag = neuer_vertrag()
    stammdaten.kuendigung_erfassen(vertrag, ZUGANG)

    html = angemeldet.get(reverse("kern:protokoll")).content.decode()

    assert f"<strong>Kündigung {vertrag.nummer} erfasst</strong>" in html
    assert "Status: aktiv → gekündigt" in html
    assert "Ende: – → 31.08.2026" in html
    assert "gekuendigt" not in html and "inhalt_html" not in html and "pdf_sha256" not in html
    vorne, _technik = html.split("<summary>technische Angaben</summary>", 1)
    assert "Prüfsumme des PDFs" not in vorne and "Inhalt (gerendert)" not in vorne
    assert "Prüfsumme des PDFs" in html


def test_protokoll_filter_nach_vertrag_einheit_und_partei(
    neuer_vertrag: VertragBauer, angemeldet: Client
) -> None:
    """Review 2, B14: Datenbasis der Filter nach Vertrag/Einheit und Person: Die Kündigung
    erscheint beim Vertrag, bei seiner Einheit und bei seinem Mieter, nicht bei einem
    anderen Vertrag."""
    vertrag = neuer_vertrag()
    anderer = neuer_vertrag()
    stammdaten.kuendigung_erfassen(vertrag, ZUGANG)
    einheit = vertrag.einheiten.get()
    partei = vertrag.vertragsparteien.get().partei
    titel = f"Kündigung {vertrag.nummer} erfasst"

    def seite(objekt: object) -> str:
        bezug = f"{objekt._meta.label_lower}:{objekt.pk}"  # type: ignore[attr-defined]
        return angemeldet.get(reverse("kern:protokoll"), {"bezug": bezug}).content.decode()

    assert titel in seite(vertrag)
    assert titel in seite(einheit)
    assert titel in seite(partei)
    assert titel not in seite(anderer)
    assert f"Nur Vorgänge zu Vertrag {vertrag.nummer}" in seite(vertrag)
