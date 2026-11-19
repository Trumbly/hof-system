"""Review 2, A8: Serienmitteilung mit Empfängervorschau. Wer keine E-Mail-Adresse hat,
bekommt einen Brief (umstellbar) oder wird ausgelassen und namentlich genannt; die Serie
bricht nicht ab; Fehlermeldungen sind deutsche Sätze statt Feldnamen."""

from __future__ import annotations

import datetime
from typing import Any

import pytest
from django.core import mail
from django.test import Client
from django.urls import reverse
from pghistory.models import Events

from apps.dokumente import services
from apps.dokumente.models import Schreiben, Vorlage
from apps.dokumente.vorlagenarten import Versandweg
from apps.kern import vorgaenge
from apps.kern.models import Benutzer, Objekt
from apps.stammdaten import services as stammdaten
from apps.stammdaten.models import Partei, Vertrag
from testhof import dokumente as testhof_dokumente
from tests.dokumente.conftest import freigegeben, kontext
from tests.stammdaten.conftest import garage_anlegen, mieter_anlegen, vertrag_anlegen

pytestmark = pytest.mark.django_db
D = datetime.date
BEZUG = "stammdaten.vertrag"


def _testhof_mitteilung() -> tuple[str, str]:
    for _name, art, betreff, text in testhof_dokumente.VORLAGEN:
        if art == "mitteilung":
            return betreff, text
    raise KeyError("mitteilung")


@pytest.fixture
def nutzermitteilung(briefkopf: Vorlage, benutzer: Benutzer) -> Vorlage:
    betreff, text = _testhof_mitteilung()
    return freigegeben("Mitteilung an Nutzer", "mitteilung", text, benutzer, betreff=betreff)


def _ohne_mail(nachname: str, *, anschrift: bool = True) -> Partei:
    return stammdaten.partei_anlegen(
        Partei.Art.PERSON,
        vorname="Erika",
        nachname=nachname,
        strasse="Testweg 2" if anschrift else "",
        plz="12345" if anschrift else "",
        ort="Beispielstadt" if anschrift else "",
    )


@pytest.fixture
def drei_vertraege(objekt: Objekt) -> list[Vertrag]:
    """Mit E-Mail (Mailmann), ohne E-Mail mit Anschrift (Briefmann), ohne beides (Leermann)."""
    mieter = [
        mieter_anlegen("Mailmann"),
        _ohne_mail("Briefmann"),
        _ohne_mail("Leermann", anschrift=False),
    ]
    return [
        vertrag_anlegen(garage_anlegen(objekt), m, beginn=D(2026, 2, 1), status="aktiv")
        for m in mieter
    ]


def test_planen_ohne_mail_brief_oder_auslassen(mitteilung: Vorlage, objekt: Objekt) -> None:
    """Review 2, A8: Wer keine E-Mail-Adresse hat, bekommt bei einer Mail-Serie einen Brief
    oder wird ausgelassen; der Grund ist ein deutscher Satz ohne Feldnamen."""
    mit = kontext(objekt)
    ohne = kontext(objekt, email="")
    leer = kontext(objekt, email="")
    leer["empfaenger"]["anschrift"] = ""
    bezuege = [(objekt, mit), (objekt, ohne), (objekt, leer)]

    plan = services.serie_planen(mitteilung, bezuege, Versandweg.MAIL)

    assert [z.versandweg for z in plan] == [Versandweg.MAIL, Versandweg.BRIEF, services.AUSLASSEN]
    assert plan[1].hinweis == "Keine E-Mail-Adresse, deshalb als Brief."
    assert "E-Mail-Adresse des Empfängers" in plan[2].hinweis
    assert "Anschrift des Empfängers" in plan[2].hinweis
    assert "empfaenger" not in plan[2].hinweis

    plan = services.serie_planen(mitteilung, bezuege, Versandweg.MAIL, ohne_mail=services.AUSLASSEN)
    assert [z.versandweg for z in plan] == [Versandweg.MAIL, services.AUSLASSEN, services.AUSLASSEN]


def test_serie_bricht_nicht_ab(mitteilung: Vorlage, objekt: Objekt) -> None:
    """Review 2, A8: Scheitert ein einzelnes Schreiben (fehlender Wert), entstehen die anderen
    trotzdem; der Empfänger steht mit Grund unter „ausgelassen“."""
    kaputt = kontext(objekt)
    del kaputt["betrag"]
    plan = services.serie_planen(
        mitteilung, [(objekt, kontext(objekt)), (objekt, kaputt)], Versandweg.MAIL
    )

    serie = services.serie_aus_plan(mitteilung, plan)

    assert len(serie.schreiben) == 1
    (ausgelassen,) = serie.ausgelassen
    assert "„betrag.offen“ (der offene Betrag)" in ausgelassen.hinweis
    assert Schreiben.objects.filter(serie=serie.kennung).count() == 1
    vid = Events.objects.tracks(serie.schreiben[0]).order_by("pgh_created_at")[0].pgh_context_id
    assert vorgaenge.titel_von(vid) == f"Serie: 1 Schreiben aus Vorlage #{mitteilung.pk} erzeugt"


def _formular(vorlage: Vorlage, vertraege: list[Vertrag], **mehr: Any) -> dict[str, Any]:
    return {
        "vorlage": vorlage.pk,
        "versandweg": Versandweg.MAIL,
        "ohne_mail": "brief",
        "auswahl": [str(v.pk) for v in vertraege],
        **mehr,
    }


def test_vorschau_dann_erzeugen_mit_ausgelassenen_namentlich(
    angemeldet: Client, nutzermitteilung: Vorlage, drei_vertraege: list[Vertrag]
) -> None:
    """Review 2, A8: Vor dem Erzeugen kommt die Empfängervorschau (Einheit, Name,
    Versandweg). Ohne E-Mail automatisch Brief, ohne beides ausgelassen und namentlich
    genannt; kein Abbruch der Serie, keine Feldnamen in den Meldungen."""
    url = reverse("dokumente:serie_neu") + f"?bezug={BEZUG}"
    daten = _formular(nutzermitteilung, drei_vertraege)

    vorschau = angemeldet.post(url, {**daten, "schritt": "vorschau"})

    assert vorschau.status_code == 200
    assert not Schreiben.objects.exists()
    plan = vorschau.context["plan"]
    assert [z.versandweg for z in plan] == [Versandweg.MAIL, Versandweg.BRIEF, services.AUSLASSEN]
    html = vorschau.content.decode()
    for vertrag, name in zip(drei_vertraege, ("Mailmann", "Briefmann", "Leermann"), strict=True):
        assert f"Erika {name}" in html
        assert vertrag.einheiten.get().nw_id in html
    assert "2 Schreiben erzeugen" in html
    assert "empfaenger.email" not in html

    antwort = angemeldet.post(
        url, {**daten, "schritt": "erzeugen", "marke": vorschau.context["marke"]}, follow=True
    )

    schreiben = list(Schreiben.objects.order_by("pk"))
    assert [s.versandweg for s in schreiben] == [Versandweg.MAIL, Versandweg.BRIEF]
    assert len({s.serie for s in schreiben}) == 1
    html = antwort.content.decode()
    assert "2 Schreiben erzeugt und bereit" in html
    assert "Ausgelassen:" in html and "Erika Leermann" in html
    assert "empfaenger" not in html.split("<main", 1)[1].split("</ul>", 1)[0]
    assert mail.outbox == []  # erzeugt, nicht gesendet


def test_versandweg_je_empfaenger_umstellbar(
    angemeldet: Client, nutzermitteilung: Vorlage, drei_vertraege: list[Vertrag]
) -> None:
    """Review 2, A8: Der Versandweg lässt sich in der Vorschau je Empfänger umstellen; wer
    auf „auslassen“ steht, bekommt nichts. Ein Weg ohne die nötigen Angaben lässt aus."""
    url = reverse("dokumente:serie_neu") + f"?bezug={BEZUG}"
    daten = _formular(nutzermitteilung, drei_vertraege)
    marke = angemeldet.post(url, {**daten, "schritt": "vorschau"}).context["marke"]
    mailmann, briefmann, leermann = drei_vertraege

    angemeldet.post(
        url,
        {
            **daten,
            "schritt": "erzeugen",
            "marke": marke,
            f"weg_{briefmann.pk}": services.AUSLASSEN,
            f"weg_{leermann.pk}": Versandweg.BRIEF,  # keine Anschrift: bleibt ausgelassen
        },
    )

    assert [s.bezug for s in Schreiben.objects.order_by("pk")] == [mailmann]


def test_erzeugen_nur_nach_passender_vorschau(
    angemeldet: Client, nutzermitteilung: Vorlage, drei_vertraege: list[Vertrag]
) -> None:
    """Review 2, A8: Ohne (passende) Vorschau entsteht nichts; die Seite bittet in einem Satz,
    die Empfänger noch einmal zu prüfen."""
    url = reverse("dokumente:serie_neu") + f"?bezug={BEZUG}"
    daten = _formular(nutzermitteilung, drei_vertraege)

    antwort = angemeldet.post(url, {**daten, "schritt": "erzeugen", "marke": "alt"})

    assert not Schreiben.objects.exists()
    assert "Bitte die Empfänger noch einmal prüfen." in antwort.content.decode()
    assert antwort.context["plan"]
