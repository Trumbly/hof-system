"""Serienmitteilung: eine Vorlage, viele Bezüge, ein Klick (Schritt 3c, Aufgabe 8; H8.4).

Verträge gibt es erst mit Schritt 4; Bezug sind hier Objekte des Kerns. Der Bezug ist
generisch, jedes Modell geht.
"""

from __future__ import annotations

import pytest
from django.core import mail
from django.test import Client
from django.urls import reverse

from apps.dokumente import services
from apps.dokumente.models import Schreiben, Vorlage
from apps.dokumente.vorlagenarten import Versandweg
from apps.kern.models import Benutzer
from tests.dokumente.conftest import freigegeben, kontext
from tests.factories import ObjektFabrik

pytestmark = pytest.mark.django_db


def test_serie_ein_klick_fuer_alle_jedes_schreiben_einzeln(
    angemeldet: Client, mitteilung: Vorlage
) -> None:
    """Schritt 3c, Aufgabe 8 (H8.4): Auswahl von Bezügen, eine Vorlage, ein Senden-Klick
    für alle, jedes Schreiben einzeln abgelegt."""
    objekte = [ObjektFabrik() for _ in range(3)]
    serie = services.serie_erzeugen(
        mitteilung,
        [(o, kontext(o, email=f"nutzer{n}@testhof.invalid")) for n, o in enumerate(objekte)],
        Versandweg.MAIL,
    )

    assert len(serie) == 3
    kennung = serie[0].serie
    assert all(s.serie == kennung for s in serie)
    assert [s.bezug for s in serie] == objekte
    assert len({s.pdf.name for s in serie}) == 3
    assert mail.outbox == []

    seite = angemeldet.get(reverse("dokumente:serie", args=[kennung]))
    pruefsumme = seite.context["pruefsumme"]
    assert pruefsumme == services.serie_pruefsumme(serie)
    for s in serie:
        assert reverse("dokumente:schreiben_pdf", args=[s.pk]) in seite.content.decode()

    senden = reverse("dokumente:serie_senden", args=[kennung])
    angemeldet.post(senden, {"pruefsumme": "falsch"})
    assert mail.outbox == []

    angemeldet.post(senden, {"pruefsumme": pruefsumme})

    assert sorted(m.to[0] for m in mail.outbox) == [
        "nutzer0@testhof.invalid",
        "nutzer1@testhof.invalid",
        "nutzer2@testhof.invalid",
    ]
    assert set(Schreiben.objects.filter(serie=kennung).values_list("status", flat=True)) == {
        Schreiben.Status.GESENDET
    }


def test_serie_prueft_die_gezeigten_schreiben(mitteilung: Vorlage, benutzer: Benutzer) -> None:
    """Schritt 3c, Aufgabe 8: Kommt nach der Vorschau ein Schreiben dazu oder wird eines
    verworfen, passt die Prüfsumme nicht mehr; gesendet wird nichts."""
    objekte = [ObjektFabrik() for _ in range(2)]
    serie = services.serie_erzeugen(mitteilung, [(o, kontext(o)) for o in objekte])
    gesehen = services.serie_pruefsumme(serie)
    services.schreiben_verwerfen(serie[0])

    with pytest.raises(services.VorschauFehlt):
        services.serie_senden(serie[0].serie, benutzer, pruefsumme=gesehen)
    assert mail.outbox == []


def test_serie_ueber_die_seite_mit_objekten_als_bezug(
    angemeldet: Client, benutzer: Benutzer
) -> None:
    """Schritt 3c, Aufgabe 8: Auf der Seite „Serienmitteilung“ Bezüge wählen (hier Objekte,
    später Verträge), Vorlage wählen, Schreiben erzeugen; gesendet ist danach nichts."""
    freigegeben("Briefkopf", "briefkopf", "<div class='kopf'>{{ absender.name }}</div>", benutzer)
    aushang = freigegeben(
        "Aushang", "aushang", "<p>{{ empfaenger.anrede }}: {{ objekt.bezeichnung }}</p>", benutzer
    )
    objekte = [ObjektFabrik() for _ in range(3)]

    seite = angemeldet.get(reverse("dokumente:serie_neu") + "?bezug=kern.objekt")
    assert seite.status_code == 200
    url = reverse("dokumente:serie_neu") + "?bezug=kern.objekt"
    daten = {
        "vorlage": aushang.pk,
        "versandweg": "",
        "ohne_mail": "brief",
        "auswahl": [objekte[0].pk, objekte[2].pk],
    }
    vorschau = angemeldet.post(url, {**daten, "schritt": "vorschau"})
    assert not Schreiben.objects.exists()  # erst die Empfängervorschau (Review 2, A8)
    antwort = angemeldet.post(
        url, {**daten, "schritt": "erzeugen", "marke": vorschau.context["marke"]}
    )

    assert antwort.status_code == 302
    erzeugt = list(Schreiben.objects.order_by("pk"))
    assert [s.bezug for s in erzeugt] == [objekte[0], objekte[2]]
    assert {s.versandweg for s in erzeugt} == {Versandweg.SELBST}
    assert {s.status for s in erzeugt} == {Schreiben.Status.BEREIT}
    assert antwort["Location"] == reverse("dokumente:serie", args=[erzeugt[0].serie])
