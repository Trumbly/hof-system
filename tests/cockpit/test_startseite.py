"""Hof-Cockpit (Schritt 9, Aufgabe 4): Startseite mit Kacheln direkt aus der Datenbank."""

from __future__ import annotations

import datetime
import io
from typing import Any

import pytest
from django.core.management import call_command
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.cockpit import kacheln
from apps.fristen import services as fristen
from apps.kern import services as kern
from tests.cockpit.conftest import Statusdatei
from tests.factories import MandantFabrik, ObjektFabrik

pytestmark = pytest.mark.django_db

KACHELN = (
    "freigaben",
    "belege",
    "fristen",
    "rueckstaende",
    "offene_posten",
    "eingangsrechnungen",
    "leerstand",
    "datenstand",
    "wohnraum",
    "laeufe",
    "sicherung",
)


@pytest.fixture
def eigene_kacheln(monkeypatch: pytest.MonkeyPatch) -> dict[str, kacheln.Kachel]:
    kopie = dict(kacheln._KACHELN)
    monkeypatch.setattr(kacheln, "_KACHELN", kopie)
    return kopie


def _kachel(html: str, name: str) -> str:
    return html.split(f'id="kachel-{name}"', 1)[1].split("</li>", 1)[0]


def _startseite(client: Client) -> str:
    antwort = client.get(reverse("kern:startseite"))
    assert antwort.status_code == 200
    return antwort.content.decode()


def test_startseite_zeigt_alle_kacheln_in_reihenfolge(angemeldet: Client) -> None:
    """Schritt 9, Aufgabe 4: Kacheln für Freigaben, Fristen, Rückstände, offene Posten,
    Leerstand, Datenstand, Wohnraum, Läufe und Sicherung, jede mit Link auf ihre Liste."""
    html = _startseite(angemeldet)
    positionen = [html.index(f'id="kachel-{name}"') for name in KACHELN]
    assert positionen == sorted(positionen)
    for name, ziel in (
        ("freigaben", reverse("kern:arbeitslisten")),
        ("fristen", reverse("fristen:liste") + "?zeitraum=14"),
        ("offene_posten", reverse("soll:liste")),
        ("belege", reverse("dokumente:ablage")),
        ("eingangsrechnungen", reverse("dokumente:rechnungen")),
        ("leerstand", reverse("stammdaten:leerstand")),
        ("datenstand", reverse("stammdaten:datenstand")),
        ("wohnraum", reverse("anbindungen:kennzahlen")),
        ("laeufe", reverse("kern:laeufe")),
    ):
        assert f'href="{ziel}"' in _kachel(html, name), name
    # Seit Schritt 6 die echte Kachel des Abgleichs statt des Platzhalters.
    assert "Verträge mit Rückstand" in _kachel(html, "rueckstaende")


def test_startseite_liest_direkt_aus_der_datenbank(
    angemeldet: Client, statusdatei: Statusdatei
) -> None:
    """Abnahme Schritt 9: Die Startseite zeigt alles direkt aus der Datenbank. Eine Änderung
    erscheint beim nächsten Aufruf, ohne Lauf und ohne Kopie."""
    html = _startseite(angemeldet)
    assert "Offene Freigaben: 0" in _kachel(html, "freigaben")
    assert "Fristen bis in 14 Tagen: 0" in _kachel(html, "fristen")
    assert "Noch keine Läufe" in _kachel(html, "laeufe")
    assert "Statusdatei fehlt" in _kachel(html, "sicherung")

    heute = timezone.localdate()
    objekt = ObjektFabrik(kuerzel="HOF", mandant=MandantFabrik())
    fristen.frist_anlegen("sonstige", heute + datetime.timedelta(days=3), "hand", objekt)
    fristen.frist_anlegen("sonstige", heute - datetime.timedelta(days=1), "vertrag", objekt)
    fristen.frist_anlegen("kuendigung", heute + datetime.timedelta(days=40), "hand", objekt)
    kern.arbeitsposten_anlegen("Frist fällig", bezug=objekt)
    kern.arbeitsposten_anlegen("Frist fällig", bezug=objekt)
    kern.arbeitsposten_anlegen("Umsätze einordnen")
    call_command("kern_selbsttest", stdout=io.StringIO())
    statusdatei(alter_stunden=2)

    html = _startseite(angemeldet)
    freigaben = _kachel(html, "freigaben")
    assert "Offene Freigaben: 4" in freigaben
    assert "Frist fällig: <strong>2</strong>" in freigaben
    assert "Selbsttest: <strong>1</strong>" in freigaben
    frist_kachel = _kachel(html, "fristen")
    assert "Fristen bis in 14 Tagen: 2" in frist_kachel
    assert "1 überfällig" in frist_kachel
    laeufe = _kachel(html, "laeufe")
    assert "kern_selbsttest" in laeufe and "erfolgreich" in laeufe
    assert "in Ordnung" in _kachel(html, "sicherung")


def test_laeufe_kachel_zeigt_letzten_lauf_je_name(angemeldet: Client) -> None:
    alt = kern.lauf_starten("bank_abruf")
    kern.lauf_beenden(alt, gebucht=0, arbeitsposten=0, fehler=1, abgebrochen=True)
    neu = kern.lauf_starten("bank_abruf")
    kern.lauf_beenden(neu, gebucht=3, arbeitsposten=0, fehler=0)
    kaputt = kern.lauf_starten("fristen")
    kern.lauf_beenden(kaputt, gebucht=0, arbeitsposten=0, fehler=1, abgebrochen=True)

    kachel = _kachel(_startseite(angemeldet), "laeufe")
    assert kachel.count("bank_abruf") == 1
    assert "1 gescheitert" in kachel
    assert kachel.index("fristen") < kachel.index("bank_abruf")  # Gescheiterte zuerst


def test_registrierte_kachel_ersetzt_den_platzhalter(
    angemeldet: Client, eigene_kacheln: dict[str, kacheln.Kachel]
) -> None:
    """Rückstände: Der Platzhalter füllt sich, sobald der Abgleich seine Kachel anmeldet; ein
    späterer Platzhalter überschreibt die echte Kachel nicht."""
    kacheln.kachel_anmelden(
        "rueckstaende",
        "cockpit/_kachel_fehler.html",
        lambda: {"kachel": type("K", (), {"name": "rueckstaende"})()},
        kacheln.RUECKSTAENDE,
    )
    kacheln.kachel_anmelden(
        "rueckstaende",
        "cockpit/_kachel_rueckstaende_platzhalter.html",
        dict,
        kacheln.RUECKSTAENDE,
        platzhalter=True,
    )
    kachel = _kachel(_startseite(angemeldet), "rueckstaende")
    assert "Kommen mit dem Abgleich" not in kachel


def test_kaputte_kachel_nimmt_die_seite_nicht_mit(
    angemeldet: Client, eigene_kacheln: dict[str, kacheln.Kachel]
) -> None:
    def kaputt() -> dict[str, Any]:
        raise RuntimeError("kaputt")

    kacheln.kachel_anmelden("probe", "cockpit/_kachel_freigaben.html", kaputt, 1)
    html = _startseite(angemeldet)
    assert "gerade nicht verfügbar" in _kachel(html, "probe")
    assert 'id="kachel-freigaben"' in html


def test_arbeitsliste_nach_art(angemeldet: Client) -> None:
    """Die Links aus Cockpit und Hof-Backlog zeigen nur die Posten einer Art."""
    kern.arbeitsposten_anlegen("Frist fällig")
    kern.arbeitsposten_anlegen("Umsätze einordnen")
    html = angemeldet.get(reverse("kern:arbeitslisten"), {"art": "Frist fällig"}).content.decode()
    assert "Frist fällig" in html and "Umsätze einordnen" not in html


def test_kacheln_am_handy_einspaltig() -> None:
    """Am Handy stehen die Kacheln untereinander (eine Spalte, erst ab Tablet drei)."""
    from pathlib import Path

    css = (Path(__file__).resolve().parents[2] / "static" / "css" / "hof.css").read_text()
    regel = css.split(".kacheln {", 1)[1].split("}", 1)[0]
    assert "display: grid" in regel and "grid-template-columns" not in regel
    assert "@media (min-width: 48rem) {\n  .kacheln {\n    grid-template-columns" in css
