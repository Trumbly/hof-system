"""Review 3, Punkt 4: Neue Partei mit Name und Anschrift (oder „Anschrift folgt“), Anrede.

Alle Namen und Adressen sind erfunden (``.invalid``).
"""

from __future__ import annotations

import datetime

import pytest
from django.test import Client
from django.urls import reverse
from faker.providers.person.de_DE import Provider as DeutscheNamen

from apps.dokumente import services as dokumente
from apps.dokumente.models import Schreiben, Vorlage
from apps.dokumente.vorlagenarten import Versandweg
from apps.kern.models import Benutzer, Objekt
from apps.stammdaten import datenstand, schreiben, services
from apps.stammdaten.models import Partei, Vertrag
from testhof.aufbau import Testhof
from tests.stammdaten.conftest import garage_anlegen, vertrag_anlegen

pytestmark = pytest.mark.django_db
D = datetime.date
HTMX = {"HTTP_HX_REQUEST": "true"}
DIALOG = {**HTMX, "HTTP_HX_TARGET": "dialog-inhalt"}


def _vertrag_neu(einheit_pk: int, **neu: str) -> dict[str, object]:
    return {
        "einheit": einheit_pk,
        "mieter": "neu",
        "beginn": "2026-12-01",
        "miete": "80,00",
        "zahlweise": "monatlich",
        **{f"neu_{k}": v for k, v in neu.items()},
    }


# --- Name ist Pflicht ------------------------------------------------------------------


def test_service_name_ist_pflicht() -> None:
    """Review 3, Punkt 4: Eine neue Partei braucht Vor- und Nachnamen oder den Firmennamen;
    der Service sagt, an welchem Feld es fehlt."""
    for felder, feld in (
        ({}, "vorname"),
        ({"vorname": "Tom"}, "nachname"),
        ({"nachname": "Bohnbach"}, "vorname"),
    ):
        with pytest.raises(services.ParteiUnvollstaendig) as fehler:
            services.partei_mit_kontakt_anlegen(anschrift_folgt=True, **felder)
        assert fehler.value.feld == feld
        assert str(fehler.value) == services.NAME_FEHLT_PERSON
    with pytest.raises(services.ParteiUnvollstaendig) as fehler:
        services.neue_partei_pruefen(art=Partei.Art.FIRMA, anschrift_folgt=True)
    assert (fehler.value.feld, str(fehler.value)) == ("firma", services.NAME_FEHLT_FIRMA)
    assert not Partei.objects.exists()

    partei = services.partei_mit_kontakt_anlegen(
        vorname="Tom", nachname="Bohnbach", anschrift_folgt=True
    )
    assert (partei.vorname, partei.nachname, partei.art) == ("Tom", "Bohnbach", "person")


def test_dialog_neue_partei_name_pflicht_am_feld(objekt: Objekt, angemeldet: Client) -> None:
    """Review 3, Punkt 4: Im Dialog „Neuer Vertrag“ mit „Neue Partei“ (auch beim „Annehmen
    und Vertrag anlegen“) steht der fehlende Name als Satz am Feld; es entsteht nichts."""
    einheit = garage_anlegen(objekt)
    url = reverse("stammdaten:vertrag_neu")
    antwort = angemeldet.post(
        url, _vertrag_neu(einheit.pk, nachname="Bohnbach", anschrift_folgt="on"), **DIALOG
    )
    assert antwort.status_code == 200
    form = antwort.context["form"]
    assert form.errors["neu_vorname"] == [services.NAME_FEHLT_PERSON]
    assert 'id="id_neu_vorname-fehler"' in antwort.content.decode()
    assert not Vertrag.objects.exists() and not Partei.objects.exists()


def test_seite_neue_partei_name_pflicht(angemeldet: Client) -> None:
    """Review 3, Punkt 4: Auch die Seite „Neue Partei“ verlangt Vor- und Nachnamen bzw. den
    Firmennamen, mit Satz am Feld."""
    url = reverse("stammdaten:partei_neu")
    seite = angemeldet.get(url).content.decode()
    for name in ("anrede", "strasse", "plz", "ort", "anschrift_folgt"):
        assert f'name="{name}"' in seite
    antwort = angemeldet.post(url, {"art": "person", "vorname": "Tom", "anschrift_folgt": "on"})
    assert antwort.status_code == 200
    assert antwort.context["form"].errors["nachname"] == [services.NAME_FEHLT_PERSON]
    antwort = angemeldet.post(url, {"art": "firma", "anschrift_folgt": "on"})
    assert antwort.context["form"].errors["firma"] == [services.NAME_FEHLT_FIRMA]
    assert not Partei.objects.exists()

    antwort = angemeldet.post(
        url, {"art": "firma", "firma": "Erfunden GmbH", "anschrift_folgt": "on"}
    )
    partei = Partei.objects.get()
    assert antwort["Location"] == reverse("stammdaten:partei", args=[partei.pk])
    assert partei.anrede == Partei.Anrede.FIRMA  # ohne Wahl: Firma


# --- Anschrift oder „Anschrift folgt“ ---------------------------------------------------


def test_anschrift_oder_anschrift_folgt(objekt: Objekt, angemeldet: Client) -> None:
    """Review 3, Punkt 4: Ohne Anschrift und ohne „Anschrift folgt“ entsteht keine Partei;
    der Satz steht an der Straße. Mit Anschrift oder dem Haken geht es."""
    einheit = garage_anlegen(objekt)
    url = reverse("stammdaten:vertrag_neu")
    antwort = angemeldet.post(
        url, _vertrag_neu(einheit.pk, vorname="Tom", nachname="Bohnbach"), **DIALOG
    )
    assert antwort.context["form"].errors["neu_strasse"] == [services.ANSCHRIFT_FEHLT]
    with pytest.raises(services.ParteiUnvollstaendig) as fehler:
        services.partei_mit_kontakt_anlegen(vorname="Tom", nachname="Bohnbach", plz="12345")
    assert fehler.value.feld == "anschrift"
    assert not Partei.objects.exists()

    antwort = angemeldet.post(
        url,
        _vertrag_neu(
            einheit.pk,
            anrede="herr",
            vorname="Tom",
            nachname="Bohnbach",
            strasse="Erfundener Weg 3",
            plz="12345",
            ort="Beispielstadt",
        ),
        **DIALOG,
    )
    assert antwort.status_code == 204
    partei = Vertrag.objects.get().vertragsparteien.get().partei
    assert (partei.strasse, partei.plz, partei.ort) == (
        "Erfundener Weg 3",
        "12345",
        "Beispielstadt",
    )
    assert (partei.anrede, partei.anschrift_folgt) == (Partei.Anrede.HERR, False)


def test_anschrift_folgt_im_datenstand(angemeldet: Client) -> None:
    """Review 3, Punkt 4: Mit „Anschrift folgt“ steht die Lücke im Datenstand (Bereich
    Partei), mit dem Vermerk. Ist die Anschrift ergänzt, ist die Lücke weg und der Haken
    zurückgesetzt."""
    partei = services.partei_mit_kontakt_anlegen(
        vorname="Tom", nachname="Bohnbach", email="tom@testhof.invalid", anschrift_folgt=True
    )
    (stand,) = [b for b in datenstand.ermitteln(D(2026, 11, 1)) if b.schluessel == "partei"]
    (luecke,) = stand.luecken
    assert luecke.kennung == f"Partei #{partei.pk}"
    assert luecke.felder == (("anschrift", "Anschrift"),)
    assert luecke.vermerk == "Anschrift folgt"
    seite = angemeldet.get(reverse("stammdaten:datenstand")).content.decode()
    assert "Anschrift folgt" in seite and "Briefe gesperrt, bis sie ergänzt ist" in seite

    services.partei_aendern(partei, strasse="Erfundener Weg 3", plz="12345", ort="Beispielstadt")
    partei.refresh_from_db()
    assert not partei.anschrift_folgt
    (stand,) = [b for b in datenstand.ermitteln(D(2026, 11, 1)) if b.schluessel == "partei"]
    assert not stand.luecken


def test_brief_an_partei_mit_anschrift_folgt_gesperrt(
    objekt: Objekt, vorlagen: dict[str, Vorlage], angemeldet: Client, benutzer: Benutzer
) -> None:
    """Review 3, Punkt 4: Ein Brief an eine Partei mit „Anschrift folgt“ entsteht zur
    Vorschau, das Senden ist gesperrt, mit Grund am Knopf. Eine Mail an dieselbe Partei geht.
    Nach dem Ergänzen der Anschrift ist ein neu erzeugter Brief nicht mehr gesperrt."""
    partei = services.partei_mit_kontakt_anlegen(
        vorname="Tom", nachname="Bohnbach", email="tom@testhof.invalid", anschrift_folgt=True
    )
    vertrag = vertrag_anlegen(garage_anlegen(objekt), partei, beginn=D(2026, 2, 1))
    kontext = schreiben.kontext_vertrag(vertrag)
    brief = dokumente.schreiben_erzeugen(vorlagen["mitteilung"], vertrag, kontext, Versandweg.BRIEF)
    assert brief.status == Schreiben.Status.BEREIT
    assert brief.empfaenger_luecken == "die Anschrift des Empfängers"
    grund = dokumente.senden_gesperrt(brief)
    assert "Für den Versand als Brief fehlt die Anschrift des Empfängers" in grund
    assert "Anschrift an der Partei ergänzen" in grund
    with pytest.raises(dokumente.BriefkopfUnvollstaendig, match="Anschrift des Empfängers"):
        dokumente.schreiben_senden(brief, benutzer, pruefsumme=brief.pdf_sha256)

    seite = angemeldet.get(reverse("dokumente:schreiben", args=[brief.pk])).content.decode()
    assert 'id="senden-gesperrt"' in seite and "Anschrift des Empfängers" in seite
    assert 'disabled aria-describedby="senden-gesperrt"' in seite

    einschreiben = dokumente.schreiben_erzeugen(
        vorlagen["mitteilung"], vertrag, kontext, Versandweg.EINWURF_EINSCHREIBEN
    )
    assert "als Einwurf-Einschreiben fehlt" in dokumente.senden_gesperrt(einschreiben)
    mail = dokumente.schreiben_erzeugen(vorlagen["mitteilung"], vertrag, kontext, Versandweg.MAIL)
    assert not mail.empfaenger_luecken and not dokumente.empfaenger_gesperrt(mail)

    services.partei_aendern(partei, strasse="Erfundener Weg 3", plz="12345", ort="Beispielstadt")
    assert dokumente.empfaenger_gesperrt(brief)  # das alte PDF hat keine Anschrift
    neu = dokumente.schreiben_erzeugen(
        vorlagen["mitteilung"], vertrag, schreiben.kontext_vertrag(vertrag), Versandweg.BRIEF
    )
    assert not neu.empfaenger_luecken and not dokumente.empfaenger_gesperrt(neu)


def test_ohne_anschrift_folgt_bleibt_der_brief_ein_fehler(objekt: Objekt) -> None:
    """Review 3, Punkt 4: Fehlt die Anschrift ohne „Anschrift folgt“ (Bestand, Import),
    entsteht wie bisher kein Brief (Fehler statt gesperrtem Schreiben)."""
    partei = services.partei_anlegen(Partei.Art.PERSON, vorname="Tom", nachname="Bohnbach")
    vertrag = vertrag_anlegen(garage_anlegen(objekt), partei, beginn=D(2026, 2, 1))
    kontext = schreiben.kontext_vertrag(vertrag)
    assert kontext["empfaenger"]["anschrift_folgt"] is False
    assert "Anschrift des Empfängers" in dokumente.versandweg_fehlt(
        Versandweg.BRIEF, kontext["empfaenger"]
    )


# --- Anrede ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("art", "anrede", "erwartet"),
    [
        ("person", "frau", "Sehr geehrte Frau Bohnbach,"),
        ("person", "herr", "Sehr geehrter Herr Bohnbach,"),
        ("person", "neutral", "Guten Tag Tom Bohnbach,"),
        ("firma", "firma", "Sehr geehrte Damen und Herren,"),
        ("firma", "neutral", "Sehr geehrte Damen und Herren,"),
    ],
)
def test_anredesatz_je_art(art: str, anrede: str, erwartet: str) -> None:
    """Review 3, Punkt 4: ``empfaenger.anrede_brief`` je Anrede: Frau, Herr, neutral
    („Guten Tag Vorname Nachname,“) und Firma („Sehr geehrte Damen und Herren,“)."""
    partei = services.partei_anlegen(
        art,
        anrede=anrede,
        vorname="Tom",
        nachname="Bohnbach",
        firma="Erfunden GmbH" if art == "firma" else "",
    )
    assert schreiben.briefanrede(partei) + "," == erwartet
    assert schreiben._empfaenger(partei)["anrede_brief"] == erwartet


def test_tom_bohnbach_bekommt_sehr_geehrter_herr(
    objekt: Objekt, vorlagen: dict[str, Vorlage]
) -> None:
    """Review 3, Punkt 4: „Tom Bohnbach“ mit Anrede Herr bekommt mit der Vorlage des
    Testhofs „Sehr geehrter Herr Bohnbach,“ (vorher: „Sehr geehrte Frau Bohnbach“)."""
    partei = services.partei_mit_kontakt_anlegen(
        anrede="herr",
        vorname="Tom",
        nachname="Bohnbach",
        email="tom@testhof.invalid",
        anschrift_folgt=True,
    )
    vertrag = vertrag_anlegen(garage_anlegen(objekt), partei, beginn=D(2026, 2, 1))
    brief = dokumente.schreiben_erzeugen(
        vorlagen["mitteilung"], vertrag, schreiben.kontext_vertrag(vertrag), Versandweg.MAIL
    )
    assert "Sehr geehrter Herr Bohnbach," in brief.inhalt_html
    assert "Frau" not in brief.inhalt_html


def test_anrede_platzhalter_in_allen_vorlagenarten() -> None:
    """Review 3, Punkt 4: ``empfaenger.anrede_brief`` steht jeder Vorlagenart (außer dem
    Briefkopf) zur Verfügung, und die Vorlagen des Testhofs nutzen ihn."""
    from apps.dokumente import vorlagenarten
    from testhof.dokumente import VORLAGEN
    from testhof.mahnwesen import VORLAGEN as MAHNVORLAGEN

    for art in vorlagenarten.alle():
        if not art.briefkopf:
            assert "empfaenger.anrede_brief" in art.erlaubte_platzhalter(), art.schluessel
    briefe = [t for _, a, _, t in (*VORLAGEN, *MAHNVORLAGEN) if "empfaenger.anrede" in t]
    assert briefe
    for text in briefe:
        assert "{{ empfaenger.anrede_brief }}" in text
        assert "{{ empfaenger.anrede }}" not in text


def test_anrede_aus_import() -> None:
    """Review 3, Punkt 4: Freitext aus einem Import wird zur Auswahl; leer ist neutral,
    bei einer Firma „Firma“."""
    aus = Partei.Anrede.aus_text
    assert [aus("Frau"), aus("Herr"), aus("Hr."), aus(""), aus("", firma=True)] == [
        "frau",
        "herr",
        "herr",
        "neutral",
        "firma",
    ]


def test_testhof_anreden_passen_zum_vornamen(testhof: Testhof) -> None:
    """Review 3, Punkt 4: Der Testhof vergibt Anreden passend zum Vornamen: Frau nur mit
    weiblichem, Herr nur mit männlichem Vornamen; Firmen haben die Anrede Firma. Eine Partei
    wartet auf ihre Anschrift („Anschrift folgt“)."""
    personen = Partei.objects.filter(art=Partei.Art.PERSON)
    weiblich, maennlich = set(DeutscheNamen.first_names_female), set(DeutscheNamen.first_names_male)
    anreden = set()
    for p in personen:
        anreden.add(p.anrede)
        if p.anrede == Partei.Anrede.FRAU:
            assert p.vorname in weiblich, p.pk
        elif p.anrede == Partei.Anrede.HERR:
            assert p.vorname in maennlich, p.pk
    assert anreden == {"frau", "herr", "neutral"}
    assert set(Partei.objects.filter(art=Partei.Art.FIRMA).values_list("anrede", flat=True)) == {
        "firma"
    }
    assert Partei.objects.filter(anschrift_folgt=True).count() == 1
