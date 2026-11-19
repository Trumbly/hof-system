"""Review 2, B13: Schreiben mit lesbarem Titel („Mitteilung · V-0005 · Erika Mailmann“),
Mailtext neben dem großen PDF und Gründen am gesperrten Knopf; Serienmitteilung mit Auswahl
nach Objekt und Typ, Freitext „Mitteilung an Nutzer“ und nur seriengeeigneten Vorlagen."""

from __future__ import annotations

import datetime
import re

import pytest
from django.test import Client
from django.urls import reverse

from apps.dokumente import anzeige, services, vorlagenarten
from apps.dokumente.forms import SerieForm
from apps.dokumente.models import Schreiben, Vorlage
from apps.dokumente.vorlagenarten import Versandweg
from apps.kern.models import Benutzer, Objekt
from apps.stammdaten import schreiben as stammdaten_schreiben
from apps.stammdaten.models import Vertrag
from tests.dokumente.conftest import freigegeben
from tests.dokumente.test_serie_empfaenger import _testhof_mitteilung
from tests.factories import ObjektFabrik
from tests.stammdaten.conftest import garage_anlegen, mieter_anlegen, vertrag_anlegen

pytestmark = pytest.mark.django_db
D = datetime.date
BEZUG = "stammdaten.vertrag"


@pytest.fixture
def nutzermitteilung(briefkopf: Vorlage, benutzer: Benutzer) -> Vorlage:
    betreff, text = _testhof_mitteilung()
    return freigegeben("Mitteilung an Nutzer", "mitteilung", text, benutzer, betreff=betreff)


@pytest.fixture
def vertrag(objekt: Objekt) -> Vertrag:
    return vertrag_anlegen(
        garage_anlegen(objekt), mieter_anlegen("Mailmann"), beginn=D(2026, 2, 1), status="aktiv"
    )


def _schreiben(vorlage: Vorlage, vertrag: Vertrag, text: str = "") -> Schreiben:
    kontext = dict(stammdaten_schreiben.kontext_vertrag(vertrag, D(2026, 11, 1)))
    kontext["mitteilung"] = {"text": text}
    return services.schreiben_erzeugen(vorlage, vertrag, kontext, Versandweg.MAIL)


def test_titel_lesbar_mit_v_nummer_und_name(
    angemeldet: Client, nutzermitteilung: Vorlage, vertrag: Vertrag
) -> None:
    """Review 2, B13: Titel „Mitteilung · V-… · Erika Mailmann“ statt „mitteilung“, Bezug
    als V-Nummer mit Link statt „Vertrag #91“, in der Liste und auf der Seite."""
    schreiben = _schreiben(nutzermitteilung, vertrag)
    titel = f"Mitteilung · {vertrag.nummer} · Erika Mailmann"
    assert anzeige.titel(schreiben) == titel

    liste = angemeldet.get(reverse("dokumente:schreiben_liste")).content.decode()
    assert titel in liste and f"Vertrag #{vertrag.pk}" not in liste
    seite = angemeldet.get(reverse("dokumente:schreiben", args=[schreiben.pk])).content.decode()
    assert f"<h1>{titel}</h1>" in seite
    assert (
        f'<a href="{reverse("stammdaten:vertrag", args=[vertrag.pk])}">{vertrag.nummer}</a>'
        in seite
    )


def test_mailtext_neben_dem_grossen_pdf(
    angemeldet: Client, nutzermitteilung: Vorlage, vertrag: Vertrag
) -> None:
    """Review 2, B13: Neben dem PDF steht der Mailtext; das PDF ist groß (A4-Verhältnis) in
    der geteilten Ansicht."""
    schreiben = _schreiben(nutzermitteilung, vertrag, "Das Tor wird am Montag gestrichen.")
    seite = angemeldet.get(reverse("dokumente:schreiben", args=[schreiben.pk])).content.decode()
    assert 'class="geteilt"' in seite and 'class="pdf-rahmen"' in seite
    assert '<pre class="mailtext">' in seite
    assert "Das Tor wird am Montag gestrichen." in seite
    assert services.mail_text(schreiben).strip().splitlines()[0] in seite


def test_gruende_stehen_am_gesperrten_knopf(
    angemeldet: Client, nutzermitteilung: Vorlage, vertrag: Vertrag, settings: object
) -> None:
    """Review 2, B13 (Review 1, Punkt 4): Ist Senden gesperrt, steht der Grund als Band direkt
    über dem Knopf, und der Knopf verweist darauf."""
    schreiben = _schreiben(nutzermitteilung, vertrag)
    Schreiben.objects.filter(pk=schreiben.pk).update(absender_luecken="Anschrift des Absenders")
    gesperrt = services.senden_gesperrt(Schreiben.objects.get(pk=schreiben.pk))
    assert gesperrt
    seite = angemeldet.get(reverse("dokumente:schreiben", args=[schreiben.pk])).content.decode()
    band = re.search(
        r'<p class="hinweis-band hinweis-warnung" id="senden-gesperrt"[^>]*>(.*?)</p>', seite, re.S
    )
    assert band is not None and gesperrt in band.group(1)
    assert 'disabled aria-describedby="senden-gesperrt">Senden (Mail)' in seite


def test_nur_seriengeeignete_vorlagen_zur_wahl(
    nutzermitteilung: Vorlage, briefkopf: Vorlage, benutzer: Benutzer
) -> None:
    """Review 2, B13: Vorlagen wie Vertrag oder Übergabeprotokoll stehen in der
    Serienmitteilung nicht zur Wahl, nur Arten, die als seriengeeignet markiert sind."""
    vertrag = freigegeben("Vertrag", "vertrag", "<p>{{ vertrag.nummer }}</p>", benutzer)
    protokoll = freigegeben(
        "Übergabe", "uebergabeprotokoll", "<p>{{ vertrag.nummer }}</p>", benutzer
    )
    wahl = list(SerieForm(wahl=[]).fields["vorlage"].queryset)  # type: ignore[attr-defined]
    assert nutzermitteilung in wahl
    assert vertrag not in wahl and protokoll not in wahl and briefkopf not in wahl
    assert set(vorlagenarten.seriengeeignet()) == {"mitteilung", "aushang"}


def test_auswahl_nach_objekt_und_typ_mit_freitext(
    angemeldet: Client, nutzermitteilung: Vorlage
) -> None:
    """Review 2, B13: Auswahl nach Objekt und Typ: Was passt, ist vorgewählt; einzelne lassen
    sich abwählen. Der Freitext „Mitteilung an Nutzer“ steht in jedem erzeugten Schreiben."""
    hof, feld = ObjektFabrik(), ObjektFabrik()
    im_hof = [
        vertrag_anlegen(
            garage_anlegen(hof), mieter_anlegen(f"Hofmann{i}"), beginn=D(2026, 2, 1), status="aktiv"
        )
        for i in range(2)
    ]
    draussen = vertrag_anlegen(
        garage_anlegen(feld), mieter_anlegen("Feldmann"), beginn=D(2026, 2, 1), status="aktiv"
    )
    url = reverse("dokumente:serie_neu")

    seite = angemeldet.get(url, {"bezug": BEZUG, "objekt": hof.pk, "typ": "garage"})
    gewaehlt = set(seite.context["form"].initial["auswahl"])
    assert gewaehlt == {str(v.pk) for v in im_hof}
    html = seite.content.decode()
    assert "Feldmann" not in html and "Hofmann0" in html
    assert 'name="freitext"' in html and "Mitteilung an Nutzer" in html

    daten = {
        "vorlage": nutzermitteilung.pk,
        "versandweg": Versandweg.MAIL,
        "ohne_mail": "brief",
        "auswahl": [str(im_hof[0].pk)],  # einer abgewählt
        "freitext": "Am Samstag wird der Hof gekehrt.",
        "objekt": hof.pk,
        "typ": "garage",
    }
    ziel = f"{url}?bezug={BEZUG}"
    vorschau = angemeldet.post(ziel, {**daten, "schritt": "vorschau"})
    assert len(vorschau.context["plan"]) == 1
    angemeldet.post(ziel, {**daten, "schritt": "erzeugen", "marke": vorschau.context["marke"]})

    (schreiben,) = Schreiben.objects.all()
    assert schreiben.bezug == im_hof[0]
    assert "Am Samstag wird der Hof gekehrt." in schreiben.inhalt_html
    assert draussen.pk not in {s.bezug_id for s in Schreiben.objects.all()}

    # Anderer Freitext als in der Vorschau: Erst neu prüfen.
    anders = angemeldet.post(
        ziel,
        {**daten, "freitext": "anders", "schritt": "erzeugen", "marke": vorschau.context["marke"]},
    )
    assert "Bitte die Empfänger noch einmal prüfen." in anders.content.decode()


def test_ohne_freitext_bleibt_die_stelle_leer(nutzermitteilung: Vorlage, vertrag: Vertrag) -> None:
    """Review 2, B13: Ein Schreiben einer Art mit Freitext entsteht auch ohne Freitext (etwa
    aus der Vertragsseite), die Stelle bleibt leer."""
    kontext = dict(stammdaten_schreiben.kontext_vertrag(vertrag, D(2026, 11, 1)))
    schreiben = services.schreiben_erzeugen(nutzermitteilung, vertrag, kontext, Versandweg.MAIL)
    assert schreiben.status == Schreiben.Status.BEREIT


def test_mailtext_mit_tabelle_bleibt_lesbar() -> None:
    """Review 2, B13: Der Mailtext neben dem PDF zeigte die Aufstellung der offenen Posten
    zusammengeklebt („10/202605.10.202676,73 €“). Jetzt eine Zeile je Tabellenzeile, die
    Zellen mit „ · “ getrennt; so geht er auch an den Mieter."""
    schreiben = Schreiben(
        inhalt_html="<p>Guten Tag,</p><table><thead><tr><th>Posten</th><th>offen</th></tr>"
        "</thead><tbody><tr><td>Miete 10/2026</td><td>36,50 €</td></tr></tbody></table>"
        "<p>Gruß</p>"
    )
    # Absätze und Tabelle durch Leerzeilen getrennt (Review 3, Mailtext).
    assert services.mail_text(schreiben).splitlines() == [
        "Guten Tag,",
        "",
        "Posten · offen",
        "Miete 10/2026 · 36,50 €",
        "",
        "Gruß",
    ]
