"""Protokoll jeder Änderung und Rücknahme (Schritt 0b, Aufgaben 3 und 4; H11.6)."""

from __future__ import annotations

from typing import Any

import pghistory
import pytest
from django.core.management import call_command
from django.db import DatabaseError, models, transaction
from django.test import Client
from django.urls import reverse
from pghistory.models import Events

from apps.kern import services
from apps.kern.models import Arbeitsposten, Benutzer, Lauf, Mandant, Objekt
from apps.kern.personenfelder import eigene_modelle
from apps.kern.protokoll import ist_protokolliert, protokollierte_modelle
from tests.factories import MandantFabrik, ObjektFabrik

pytestmark = pytest.mark.django_db

# Modelle, die bewusst nicht im Protokoll stehen, mit Grund.
OHNE_PROTOKOLL = {
    Benutzer: "Passwort-Hash und letzte Anmeldung gehören nicht in die Historie.",
    Lauf: "Der Lauf ist selbst ein Protokoll; seine Änderungen tragen ihn als Kontext.",
}


def ereignisse(objekt: models.Model) -> list[Events]:
    return list(Events.objects.tracks(objekt).order_by("pgh_created_at", "pgh_id"))


def letzte_ereignis_id(objekt: models.Model) -> str:
    return str(ereignisse(objekt)[-1].pgh_slug)


def test_alle_eigenen_modelle_sind_protokolliert() -> None:
    """Schritt 0b, Aufgabe 3: Alle Modelle bekommen das Protokoll über die Basisklasse;
    Ausnahmen stehen mit Grund in OHNE_PROTOKOLL."""
    fachmodelle = [m for m in eigene_modelle() if getattr(m, "pgh_tracked_model", None) is None]
    fehlend = [
        m._meta.label for m in fachmodelle if not ist_protokolliert(m) and m not in OHNE_PROTOKOLL
    ]
    assert fehlend == []
    assert set(protokollierte_modelle()) >= {Mandant, Objekt, Arbeitsposten}


def test_anlage_aenderung_und_loeschung_stehen_im_protokoll() -> None:
    """Schritt 0b, Aufgabe 3: Anlage, Änderung und Löschung stehen im Protokoll."""
    mandant = MandantFabrik()
    objekt = ObjektFabrik(mandant=mandant, bezeichnung="Halle Nord")
    services.objekt_aendern(objekt, bezeichnung="Halle Süd")
    objekt_id = objekt.pk
    objekt.delete()

    arten = list(
        Events.objects.filter(pgh_obj_model="kern.Objekt", pgh_obj_id=str(objekt_id))
        .order_by("pgh_id")
        .values_list("pgh_label", flat=True)
    )
    assert arten == ["insert", "update", "delete"]


def test_aenderung_aus_anfrage_traegt_den_benutzer(
    angemeldet: Client, benutzer_mit_totp: Benutzer
) -> None:
    """Schritt 0b, Aufgabe 3: Eine Änderung aus der Oberfläche trägt den Benutzer als Kontext."""
    call_command("kern_selbsttest")
    posten = Arbeitsposten.objects.get()

    angemeldet.post(reverse("kern:arbeitsposten_aktion", args=[posten.pk, "erledigen"]))

    kontext = ereignisse(posten)[-1].pgh_context
    assert kontext["user"] == benutzer_mit_totp.pk


def test_aenderung_aus_lauf_traegt_den_lauf() -> None:
    """Schritt 0b, Aufgabe 3: Eine Änderung aus einem Lauf trägt den Lauf als Kontext."""
    call_command("kern_selbsttest")
    lauf = Lauf.objects.get()
    posten = Arbeitsposten.objects.get()

    kontext = ereignisse(posten)[0].pgh_context
    assert kontext["lauf"] == lauf.pk
    assert kontext["lauf_name"] == "kern_selbsttest"


def test_testaenderung_steht_im_protokoll_und_laesst_sich_zuruecknehmen() -> None:
    """Abnahme Schritt 0, Punkt 2: Eine Teständerung steht im Protokoll und lässt sich
    zurücknehmen. Die Rücknahme ist selbst eine neue, protokollierte Änderung."""
    objekt = ObjektFabrik(bezeichnung="Halle Nord")
    with pghistory.context(user=4711):
        services.objekt_aendern(objekt, bezeichnung="Halle Süd")
    aenderung = ereignisse(objekt)[-1]
    assert aenderung.pgh_label == "update"
    assert aenderung.pgh_diff == {"bezeichnung": ["Halle Nord", "Halle Süd"]}

    services.zuruecknehmen(aenderung.pgh_slug)

    objekt.refresh_from_db()
    assert objekt.bezeichnung == "Halle Nord"
    ruecknahme = ereignisse(objekt)[-1]
    assert ruecknahme.pgh_label == "update"
    assert ruecknahme.pgh_diff == {"bezeichnung": ["Halle Süd", "Halle Nord"]}
    assert ruecknahme.pgh_context["ruecknahme_von"] == aenderung.pgh_slug
    assert len(ereignisse(objekt)) == 3


def test_ruecknahme_ueber_die_oberflaeche(angemeldet: Client) -> None:
    """Abnahme Schritt 0, Punkt 2: Über den Knopf „Zurücknehmen“ im Protokoll."""
    objekt = ObjektFabrik(bezeichnung="Halle Nord")
    services.objekt_aendern(objekt, bezeichnung="Halle Süd")
    ereignis_id = letzte_ereignis_id(objekt)

    seite = angemeldet.get(reverse("kern:protokoll"), {"modell": "kern.Objekt"})
    assert ereignis_id in seite.content.decode()
    assert "Zurücknehmen" in seite.content.decode()

    antwort = angemeldet.post(
        reverse("kern:zuruecknehmen"),
        {"ereignis": ereignis_id, "modell": "kern.Objekt"},
        follow=True,
    )

    objekt.refresh_from_db()
    assert objekt.bezeichnung == "Halle Nord"
    html = antwort.content.decode()
    assert "Zurückgenommen" in html
    assert "zurückgenommen" in html  # Kennzeichen am Eintrag, ohne zweiten Knopf


def test_ruecknahme_nimmt_nur_die_felder_des_ereignisses_zurueck() -> None:
    """Schritt 0b, Aufgabe 4: Spätere Änderungen an anderen Feldern bleiben stehen."""
    objekt = ObjektFabrik(bezeichnung="Halle Nord", anschrift="Alte Anschrift")
    services.objekt_aendern(objekt, bezeichnung="Halle Süd")
    ereignis_id = letzte_ereignis_id(objekt)
    services.objekt_aendern(objekt, anschrift="Neue Anschrift")

    services.zuruecknehmen(ereignis_id)

    objekt.refresh_from_db()
    assert objekt.bezeichnung == "Halle Nord"
    assert objekt.anschrift == "Neue Anschrift"


def test_ruecknahme_bricht_ab_wenn_das_feld_spaeter_erneut_geaendert_wurde() -> None:
    """Schritt 0b, Aufgabe 4: Keine Rücknahme über eine spätere Änderung hinweg."""
    objekt = ObjektFabrik(bezeichnung="Halle Nord")
    services.objekt_aendern(objekt, bezeichnung="Halle Süd")
    ereignis_id = letzte_ereignis_id(objekt)
    services.objekt_aendern(objekt, bezeichnung="Halle West")

    with pytest.raises(services.RuecknahmeNichtMoeglich):
        services.zuruecknehmen(ereignis_id)

    objekt.refresh_from_db()
    assert objekt.bezeichnung == "Halle West"


def test_ruecknahme_einer_anlage_loescht_das_objekt() -> None:
    """Schritt 0b, Aufgabe 4: Eine Anlage zurücknehmen heißt, das Objekt wieder zu entfernen."""
    mandant = MandantFabrik()
    ereignis_id = letzte_ereignis_id(mandant)

    services.zuruecknehmen(ereignis_id)

    assert not Mandant.objects.filter(pk=mandant.pk).exists()
    assert Events.objects.filter(pgh_obj_model="kern.Mandant", pgh_label="delete").count() == 1


def test_ruecknahme_einer_anlage_mit_bezuegen_wird_abgelehnt() -> None:
    """Schritt 0b, Aufgabe 4: Ein Mandant mit Objekten bleibt stehen."""
    objekt = ObjektFabrik()
    ereignis_id = ereignisse(objekt.mandant)[0].pgh_slug

    with pytest.raises(services.RuecknahmeNichtMoeglich):
        services.zuruecknehmen(ereignis_id)
    assert Mandant.objects.filter(pk=objekt.mandant.pk).exists()


def test_ruecknahme_einer_loeschung_stellt_das_objekt_wieder_her() -> None:
    """Schritt 0b, Aufgabe 4: Eine Löschung zurücknehmen stellt das Objekt mit derselben ID her."""
    objekt = ObjektFabrik(bezeichnung="Halle Nord")
    objekt_id, mandant_id = objekt.pk, objekt.mandant_id
    objekt.delete()
    loeschung = Events.objects.get(
        pgh_obj_model="kern.Objekt", pgh_obj_id=str(objekt_id), pgh_label="delete"
    )

    services.zuruecknehmen(loeschung.pgh_slug)

    wieder = Objekt.objects.get(pk=objekt_id)
    assert wieder.bezeichnung == "Halle Nord"
    assert wieder.mandant_id == mandant_id
    with pytest.raises(services.RuecknahmeNichtMoeglich):
        services.zuruecknehmen(loeschung.pgh_slug)


def test_doppelte_ruecknahme_wird_abgelehnt() -> None:
    """Schritt 0b, Aufgabe 4: Dieselbe Änderung lässt sich nicht zweimal zurücknehmen."""
    objekt = ObjektFabrik(bezeichnung="Halle Nord")
    services.objekt_aendern(objekt, bezeichnung="Halle Süd")
    ereignis_id = letzte_ereignis_id(objekt)
    services.zuruecknehmen(ereignis_id)

    with pytest.raises(services.RuecknahmeNichtMoeglich):
        services.zuruecknehmen(ereignis_id)


@pytest.mark.parametrize("ereignis_id", ["", "quatsch", "kern.ObjektEvent:999999", "kern.Lauf:1"])
def test_unbekanntes_ereignis(ereignis_id: str) -> None:
    """Schritt 0b, Aufgabe 4: Unbekannte Ereignisse ergeben eine klare Meldung, keinen Absturz."""
    with pytest.raises(services.RuecknahmeNichtMoeglich):
        services.zuruecknehmen(ereignis_id)


def test_stornoweg_ersetzt_die_ruecknahme(monkeypatch: pytest.MonkeyPatch) -> None:
    """Schritt 0b, Aufgabe 4: Erweiterungsstelle für gebuchte Werte. Ist für ein Modell ein
    Stornoweg angemeldet, storniert er, statt den Wert zurückzudrehen."""
    monkeypatch.setattr(services, "_STORNOWEGE", {})
    aufrufe: list[Any] = []
    services.stornoweg_registrieren(Objekt, aufrufe.append)
    objekt = ObjektFabrik(bezeichnung="Halle Nord")
    services.objekt_aendern(objekt, bezeichnung="Halle Süd")
    ereignis_id = letzte_ereignis_id(objekt)

    ereignis = services.zuruecknehmen(ereignis_id)

    assert aufrufe == [ereignis]
    assert ereignis.art == "update"
    assert ereignis.vorher is not None and ereignis.vorher["bezeichnung"] == "Halle Nord"
    objekt.refresh_from_db()
    assert objekt.bezeichnung == "Halle Süd"


def test_protokoll_ist_unveraenderlich() -> None:
    """Schritt 0b, Aufgabe 3: Einträge im Protokoll lassen sich nicht ändern oder löschen."""
    objekt = ObjektFabrik()
    ereignismodell = Objekt.pgh_event_model  # type: ignore[attr-defined]

    with pytest.raises(DatabaseError), transaction.atomic():
        ereignismodell.objects.filter(pgh_obj_id=objekt.pk).update(bezeichnung="x")
    with pytest.raises(DatabaseError), transaction.atomic():
        ereignismodell.objects.filter(pgh_obj_id=objekt.pk).delete()
