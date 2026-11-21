"""Anzeigenamen der Läufe (Design-Review nachher, Befund C): Kachel und Seite „Läufe“ zeigen
„Status-MCP aktualisieren“ statt „statusmcp_aktualisieren“; der technische Name bleibt im
Lauf, in seiner Meldung und in den Logs."""

from __future__ import annotations

import pytest
from django.core.management import get_commands, load_command_class
from django.test import Client
from django.urls import reverse
from django.utils.html import strip_tags

from apps.kern import laufnamen, services
from apps.kern.laeufe import LaufBefehl
from apps.kern.models import Lauf


def _lauf_befehle() -> dict[str, str]:
    """Befehlsname → Name des Laufs, für jeden Management-Befehl, der ein Lauf ist."""
    ergebnis = {}
    for befehl, app in get_commands().items():
        if befehl.startswith("_"):
            continue
        klasse = load_command_class(app, befehl)
        if isinstance(klasse, LaufBefehl):
            ergebnis[befehl] = klasse.get_lauf_name()
    return ergebnis


def test_jeder_lauf_hat_einen_anzeigenamen() -> None:
    """Befund C: Jeder angemeldete Lauf (Management-Befehl mit ``LaufBefehl``) hat einen
    Anzeigenamen in ``apps.kern.laufnamen``; keiner ist leer oder gleich dem Befehlsnamen."""
    befehle = _lauf_befehle()
    assert {"tageslauf", "statusmcp_aktualisieren", "bank_abruf"} <= set(befehle)
    fehlend = sorted(n for n in befehle.values() if n not in laufnamen.ANZEIGENAMEN)
    assert not fehlend, f"Anzeigename fehlt in apps/kern/laufnamen.py: {fehlend}"
    for name in befehle.values():
        anzeige = laufnamen.ANZEIGENAMEN[name]
        assert anzeige.strip() and anzeige != name and "_" not in anzeige, name
    # Keine Leichen: Jeder Eintrag gehört zu einem Lauf.
    assert set(laufnamen.ANZEIGENAMEN) <= set(befehle.values())


def test_anzeigename_mit_rueckfall() -> None:
    """Befund C: Die Namen aus dem Auftrag; ein unbekannter Name bleibt stehen."""
    assert laufnamen.anzeigename("statusmcp_aktualisieren") == "Status-MCP aktualisieren"
    assert laufnamen.anzeigename("kassenbuch_einordnen") == "Kassenbuch einordnen"
    assert laufnamen.anzeigename("bank_abruf") == "Bankabruf"
    assert laufnamen.anzeigename("unbekannt_xy") == "unbekannt_xy"
    assert Lauf(name="bank_abruf").anzeigename == "Bankabruf"


@pytest.mark.django_db
def test_kachel_und_seite_zeigen_anzeigenamen(angemeldet: Client) -> None:
    """Befund C: Kachel „Läufe“ und Seite „Läufe“ zeigen den Anzeigenamen; der technische Name
    bleibt im Lauf, in der Meldung und im Filter der Adresse."""
    lauf = services.lauf_starten("statusmcp_aktualisieren")
    services.lauf_beenden(lauf, gebucht=0, arbeitsposten=0, fehler=0, meldung="3 Kennzahlen.")
    lauf.refresh_from_db()
    assert lauf.name == "statusmcp_aktualisieren"

    start = angemeldet.get(reverse("kern:startseite")).content.decode()
    kachel = start.split('id="kachel-laeufe"', 1)[1].split('<li id="kachel-', 1)[0]
    assert "Status-MCP aktualisieren" in kachel
    assert "statusmcp_aktualisieren" not in kachel

    seite = angemeldet.get(reverse("kern:laeufe")).content.decode()
    zeile = seite.split(f'id="lauf-{lauf.pk}"', 1)[1].split("</tr>", 1)[0]
    assert "Status-MCP aktualisieren" in strip_tags(zeile)
    assert "statusmcp_aktualisieren" not in strip_tags(zeile)
    assert "?name=statusmcp_aktualisieren" in zeile
    gefiltert = angemeldet.get(reverse("kern:laeufe"), {"name": "statusmcp_aktualisieren"})
    assert "Nur Läufe" in gefiltert.content.decode()
    assert "Status-MCP aktualisieren</span>" in gefiltert.content.decode()
