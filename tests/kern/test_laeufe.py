"""Lauf-Gerüst und Beispiel-Lauf kern_selbsttest (Schritt 0b, Aufgabe 6)."""

from __future__ import annotations

from typing import Any

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import Client
from django.urls import reverse

from apps.kern.laeufe import LaufBefehl
from apps.kern.models import Arbeitsposten, Lauf, Mandant
from tests.factories import MandantFabrik

pytestmark = pytest.mark.django_db


class GemischterLauf(LaufBefehl):
    lauf_name = "testlauf"

    def ausfuehren(self, **optionen: Any) -> str:
        self.gebucht(3)
        self.arbeitsposten_anlegen("Testposten")
        self.fehler()
        return "3 gebucht, 1 Posten, 1 Fehler"


class AbbrechenderLauf(LaufBefehl):
    lauf_name = "absturz"

    def ausfuehren(self, **optionen: Any) -> None:
        MandantFabrik()
        self.arbeitsposten_anlegen("Testposten")
        self.gebucht()
        raise RuntimeError("Erfundene Straße 1 ist kaputt")


def test_kern_selbsttest_erzeugt_lauf_und_arbeitsposten() -> None:
    """Fertig-Kriterium Schritt 0b: kern_selbsttest erzeugt einen Lauf und einen Arbeitsposten."""
    call_command("kern_selbsttest")

    lauf = Lauf.objects.get()
    assert lauf.name == "kern_selbsttest"
    assert lauf.status == Lauf.Status.ERFOLGREICH
    assert (lauf.gebucht, lauf.arbeitsposten, lauf.fehler) == (0, 1, 0)
    assert lauf.beendet_am is not None and lauf.beendet_am >= lauf.gestartet_am

    posten = Arbeitsposten.objects.get()
    assert posten.status == Arbeitsposten.Status.OFFEN
    assert posten.lauf == lauf
    assert posten.bezug == lauf
    assert f"#{posten.pk}" in lauf.meldung


def test_kern_selbsttest_ist_in_der_oberflaeche_sichtbar(angemeldet: Client) -> None:
    """Fertig-Kriterium Schritt 0b: Lauf und Arbeitsposten aus kern_selbsttest sind in der
    Oberfläche sichtbar (Seiten „Läufe“ und „Arbeitslisten“)."""
    call_command("kern_selbsttest")
    lauf = Lauf.objects.get()
    posten = Arbeitsposten.objects.get()

    laeufe = angemeldet.get(reverse("kern:laeufe")).content.decode()
    assert f'id="lauf-{lauf.pk}"' in laeufe
    assert "kern_selbsttest" in laeufe
    assert "erfolgreich" in laeufe

    liste = angemeldet.get(reverse("kern:arbeitslisten")).content.decode()
    assert f'id="posten-{posten.pk}"' in liste
    assert "Selbsttest" in liste
    assert reverse("kern:arbeitsposten_aktion", args=[posten.pk, "erledigen"]) in liste


def test_basisklasse_zaehlt_die_drei_koerbe() -> None:
    """Schritt 0b, Aufgabe 6: Die Basisklasse zählt gebucht, Arbeitsposten und Fehler."""
    call_command(GemischterLauf())

    lauf = Lauf.objects.get()
    assert (lauf.gebucht, lauf.arbeitsposten, lauf.fehler) == (3, 1, 1)
    assert lauf.status == Lauf.Status.MIT_FEHLERN
    assert lauf.meldung == "3 gebucht, 1 Posten, 1 Fehler"


def test_ausnahme_wird_als_fehler_festgehalten_und_zurueckgerollt() -> None:
    """Schritt 0b, Aufgabe 6: Eine Ausnahme schließt den Lauf als abgebrochen mit Fehler,
    rollt seine Änderungen zurück und endet mit Fehlercode. Die Meldung nennt keine
    Personendaten aus der Ausnahme."""
    with pytest.raises(CommandError):
        call_command(AbbrechenderLauf())

    lauf = Lauf.objects.get()
    assert lauf.status == Lauf.Status.ABGEBROCHEN
    assert lauf.beendet_am is not None
    assert (lauf.gebucht, lauf.arbeitsposten, lauf.fehler) == (0, 0, 1)
    assert "RuntimeError" in lauf.meldung
    assert "Erfundene Straße" not in lauf.meldung
    assert not Mandant.objects.exists()
    assert not Arbeitsposten.objects.exists()


def test_lauf_name_kommt_aus_dem_befehl() -> None:
    """Schritt 0b, Aufgabe 6: Ohne lauf_name heißt der Lauf wie sein Befehl."""
    from apps.kern.management.commands.kern_selbsttest import Command

    assert Command().get_lauf_name() == "kern_selbsttest"
