"""Vorgänge im Protokoll und ihre Rücknahme, Teil Kern (Review 2, A4 und B14)."""

from __future__ import annotations

import datetime
from typing import Any

import pytest
from django.core.management import call_command
from django.test import Client
from django.urls import reverse
from pghistory.models import Context, Events

from apps.kern import services, vorgaenge
from apps.kern.laeufe import LaufBefehl
from apps.kern.models import Arbeitsposten, Lauf, Objekt
from apps.kern.vorgaenge import als_vorgang, vorgang
from tests.factories import ObjektFabrik

pytestmark = pytest.mark.django_db


def kontext_von(objekt: Objekt) -> list[Any]:
    return list(
        Events.objects.tracks(objekt)
        .order_by("pgh_created_at")
        .values_list("pgh_context_id", flat=True)
    )


# --- Vorgangs-ID ---------------------------------------------------------------------


def test_eine_aktion_ist_ein_vorgang_mit_einer_id() -> None:
    """Review 2, A4: Jede Nutzeraktion bekommt eine Vorgangs-ID; alle ihre Änderungen
    tragen sie."""
    objekt = ObjektFabrik(bezeichnung="Halle Nord")
    with vorgang("Hallen umbenannt") as v:
        services.objekt_aendern(objekt, bezeichnung="Halle Süd")
        services.objekt_aendern(objekt, anschrift="Testweg 2")

    assert kontext_von(objekt)[1:] == [v.id, v.id]
    assert Context.objects.get(pk=v.id).metadata["titel"] == "Hallen umbenannt"


def test_anfrage_ist_ein_vorgang(angemeldet: Client) -> None:
    """Review 2, A4: Eine Aktion in der Oberfläche ist ein Vorgang mit lesbarem Titel und dem
    Benutzer."""
    posten = services.arbeitsposten_anlegen("Testposten")

    angemeldet.post(reverse("kern:arbeitsposten_aktion", args=[posten.pk, "erledigen"]))

    zeile = vorgaenge.vorgaenge_suchen(modell="kern.Arbeitsposten")[0]  # der neueste
    assert zeile.titel == f"Arbeitsposten #{posten.pk}: Erledigt"
    assert zeile.art == vorgaenge.Art.NUTZER
    assert "@" in zeile.wer  # der angemeldete Benutzer


def test_service_titel_aus_argumenten_und_ergebnis() -> None:
    """Review 2, A4: Der Titel kommt aus dem Service (``@als_vorgang``); im selben Vorgang
    gewinnt der erste, auch wenn er erst mit dem Ergebnis feststeht."""

    @als_vorgang("Objekt {objekt.kuerzel} umbenannt")
    def umbenennen(objekt: Objekt, name: str) -> Objekt:
        return services.objekt_aendern(objekt, bezeichnung=name)

    @als_vorgang("Objekt {ergebnis.kuerzel} doppelt umbenannt")
    def doppelt(objekt: Objekt) -> Objekt:
        umbenennen(objekt, "eins")
        return umbenennen(objekt, "zwei")

    objekt = ObjektFabrik(kuerzel="HOF")
    umbenennen(objekt, "neu")
    doppelt(objekt)

    titel = [vorgaenge.titel_von(k) for k in dict.fromkeys(kontext_von(objekt)[1:])]
    assert titel == ["Objekt HOF umbenannt", "Objekt HOF doppelt umbenannt"]


def test_titel_ohne_service_aus_der_ersten_aenderung() -> None:
    """Review 2, B14: Fehlt ein Titel, bildet das Protokoll einen aus der ersten Änderung."""
    objekt = ObjektFabrik(kuerzel="HOF")
    with vorgang() as v:
        services.objekt_aendern(objekt, bezeichnung="Halle Ost")

    assert vorgaenge.titel_von(v.id) == "Objekt HOF geändert"


# --- Lauf als Vorgang ----------------------------------------------------------------


def test_lauf_ist_ein_vorgang_und_laesst_sich_zuruecknehmen() -> None:
    """Review 2, A4: Jeder Lauf ist ein Vorgang („Lauf … #ID“). Zurückgenommen wird er als
    Ganzes; ein angelegter Arbeitsposten wird erledigt (nicht gelöscht), damit auch die
    Aufgabe im Hof-Backlog schließt."""
    call_command("kern_selbsttest")
    lauf = Lauf.objects.get()
    posten = Arbeitsposten.objects.get()
    (ereignis,) = Events.objects.tracks(posten)
    meta = Context.objects.get(pk=ereignis.pgh_context_id).metadata
    assert meta["titel"] == f"Lauf kern_selbsttest #{lauf.pk}"
    assert meta["art"] == vorgaenge.Art.LAUF
    assert meta["lauf"] == lauf.pk

    bericht = services.vorgang_zuruecknehmen(ereignis.pgh_context_id)

    posten.refresh_from_db()
    assert posten.status == Arbeitsposten.Status.ERLEDIGT
    assert [s.text for s in bericht.schritte] == [
        f"Arbeitsposten #{posten.pk} (Selbsttest) erledigt"
    ]


class _InnererLauf(LaufBefehl):
    lauf_name = "innen"

    def ausfuehren(self, **optionen: Any) -> None:
        self.arbeitsposten_anlegen("Testposten")


def test_lauf_in_einem_anderen_vorgang_ist_ein_eigener_vorgang() -> None:
    """Review 2, A4: Ein Lauf, den ein anderer Lauf (Tageslauf) oder eine Anfrage startet,
    ist ein eigener Vorgang; Benutzer und Adresse übernimmt er."""
    with vorgang("Außen", user=7, url="/laeufe/") as aussen:
        objekt = ObjektFabrik()
        _InnererLauf().handle()
        services.objekt_aendern(objekt, bezeichnung="danach")

    posten = Arbeitsposten.objects.get()
    (innen,) = Events.objects.tracks(posten).values_list("pgh_context_id", flat=True)
    assert innen != aussen.id
    assert Context.objects.get(pk=innen).metadata["user"] == 7
    assert kontext_von(objekt) == [aussen.id, aussen.id]  # danach wieder der äußere


# --- Rücknahme: Sperren und Meldungen ------------------------------------------------


def test_ruecknahme_einer_ruecknahme_ist_gesperrt() -> None:
    """Review 2, A4 (Entscheidung): Eine Rücknahme lässt sich nicht zurücknehmen. Sie hat
    Schreiben verworfen und das Soll neu gestellt; das zurückzudrehen wäre kein sauberer
    Stand. Stattdessen die Aktion neu ausführen."""
    objekt = ObjektFabrik(bezeichnung="Halle Nord")
    with vorgang("umbenannt") as v:
        services.objekt_aendern(objekt, bezeichnung="Halle Süd")
    bericht = services.vorgang_zuruecknehmen(v.id)
    assert bericht.ruecknahme_id is not None

    with pytest.raises(services.RuecknahmeGesperrt, match="Rücknahme lässt sich nicht"):
        services.vorgang_zuruecknehmen(bericht.ruecknahme_id)


def test_doppelte_ruecknahme_ist_gesperrt_mit_link() -> None:
    """Review 2, A4: Ein schon zurückgenommener Vorgang verweist auf seine Rücknahme."""
    objekt = ObjektFabrik(bezeichnung="Halle Nord")
    with vorgang("umbenannt") as v:
        services.objekt_aendern(objekt, bezeichnung="Halle Süd")
    bericht = services.vorgang_zuruecknehmen(v.id)

    with pytest.raises(services.RuecknahmeGesperrt) as fehler:
        services.vorgang_zuruecknehmen(v.id)

    (sperre,) = fehler.value.sperren
    assert sperre.url == reverse("kern:vorgang", args=[bericht.ruecknahme_id])


def test_spaetere_aenderung_bricht_ab_mit_verstaendlicher_meldung() -> None:
    """Review 2, A4: Wurde ein Feld danach in einem anderen Vorgang geändert, bricht die
    Rücknahme ab und sagt, wann und in welchem Vorgang. Nichts ist geändert."""
    objekt = ObjektFabrik(kuerzel="HOF", bezeichnung="Halle Nord")
    with vorgang("erste Umbenennung") as erste:
        services.objekt_aendern(objekt, bezeichnung="Halle Süd")
    with vorgang("zweite Umbenennung"):
        services.objekt_aendern(objekt, bezeichnung="Halle West")

    with pytest.raises(services.RuecknahmeNichtMoeglich) as fehler:
        services.vorgang_zuruecknehmen(erste.id)

    meldung = str(fehler.value)
    heute = datetime.date.today().strftime("%d.%m.%Y")
    assert meldung.startswith(f"Objekt HOF wurde danach am {heute} um ")
    assert "im Vorgang „zweite Umbenennung“ erneut geändert (Bezeichnung)" in meldung
    objekt.refresh_from_db()
    assert objekt.bezeichnung == "Halle West"


def test_angelegtes_wird_entfernt_aber_nicht_mit_spaeteren_abhaengigen() -> None:
    """Review 2, A4: Was der Vorgang angelegt hat, wird entfernt; hängt inzwischen etwas
    daran, bricht die Rücknahme mit Meldung ab, statt es mitzulöschen."""
    with vorgang("Mandant angelegt") as v:
        mandant = services.mandant_anlegen("gbr", "T1", "Test")
    with vorgang("Objekt angelegt"):
        services.objekt_anlegen(mandant, "HOF", "Hof")

    with pytest.raises(services.RuecknahmeNichtMoeglich, match="wird noch verwendet"):
        services.vorgang_zuruecknehmen(v.id)


def test_testhof_aufbau_ist_gesperrt() -> None:
    """Review 2, A4: Den Aufbau des Testhofs nimmt man nicht zurück."""
    with vorgang(testhof_seed=1) as v:
        ObjektFabrik()

    with pytest.raises(services.RuecknahmeGesperrt, match="Testhof"):
        services.vorgang_zuruecknehmen(v.id)


def test_vorschau_aendert_nichts() -> None:
    """Review 2, A4: Die Seite zeigt vor dem Zurücknehmen, was passieren wird (Probelauf)."""
    objekt = ObjektFabrik(kuerzel="HOF", bezeichnung="Halle Nord")
    with vorgang("umbenannt") as v:
        services.objekt_aendern(objekt, bezeichnung="Halle Süd")

    bericht = services.ruecknahme_vorschau(v.id)

    assert [s.text for s in bericht.schritte] == ["Objekt HOF: Bezeichnung Halle Süd → Halle Nord"]
    objekt.refresh_from_db()
    assert objekt.bezeichnung == "Halle Süd"
    assert not Context.objects.filter(metadata__ruecknahme_von=str(v.id)).exists()
    services.vorgang_zuruecknehmen(v.id)  # danach geht die echte Rücknahme


# --- Protokoll-Seite -----------------------------------------------------------------


def test_protokoll_zeigt_vorgaenge_mit_anzahl_und_aufklappbaren_einzelheiten(
    angemeldet: Client,
) -> None:
    """Review 2, A4 und B14: Das Protokoll zeigt Vorgänge („Titel · n Änderungen“), die
    Einzelheiten in ``<details>``, Feldnamen auf Deutsch, Event-IDs nur in den technischen
    Angaben."""
    objekt = ObjektFabrik(kuerzel="HOF", bezeichnung="Halle Nord")
    with vorgang("Halle umgebaut"):
        services.objekt_aendern(objekt, bezeichnung="Halle Süd")
        services.objekt_aendern(objekt, aktiv=False)

    html = angemeldet.get(reverse("kern:protokoll")).content.decode()

    assert "<strong>Halle umgebaut</strong> · 2 Änderungen" in html
    assert "Bezeichnung: Halle Nord → Halle Süd" in html
    assert "Aktiv: ja → nein" in html
    vorne, technik = html.split('<details class="technik">', 1)
    assert "kern.ObjektEvent:" not in vorne
    assert "kern.ObjektEvent:" in technik


def test_protokoll_filter_nach_zeitraum(angemeldet: Client) -> None:
    """Review 2, B14: Filter nach Zeitraum (Datenbasis)."""
    with vorgang("heute geändert"):
        ObjektFabrik()
    morgen = datetime.date.today() + datetime.timedelta(days=1)

    assert "heute geändert" in angemeldet.get(reverse("kern:protokoll")).content.decode()
    leer = angemeldet.get(reverse("kern:protokoll"), {"von": morgen.isoformat()})
    assert "Noch keine Vorgänge" in leer.content.decode()
    assert vorgaenge.vorgaenge_suchen(bis=morgen)[0].titel == "heute geändert"


def test_meldung_buendelt_gleichartige_schritte() -> None:
    """Review 2, B (Leitlinie Rückmeldungen): Die Meldung nach der Rücknahme sagt knapp,
    was passiert ist; Gleichartiges steht gebündelt hinter dem Wichtigen."""
    texte = [
        "Kündigung #3 entfernt",
        "Checkpunkt #2 entfernt",
        "Checkpunkt #1 entfernt",
        "Vertrag V-0001: Status gekündigt → aktiv",
        "Soll V-0001 neu gestellt: 3 Posten neu, 0 storniert",
    ]

    assert vorgaenge.zusammenfassen(texte) == [
        "Kündigung #3 entfernt",
        "Vertrag V-0001: Status gekündigt → aktiv",
        "Soll V-0001 neu gestellt: 3 Posten neu, 0 storniert",
        "Checkpunkt ×2 entfernt",
    ]
