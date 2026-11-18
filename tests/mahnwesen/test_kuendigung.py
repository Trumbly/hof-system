"""Kündigung im Mahnwesen: Anwaltshinweis und Bestätigung vor dem Senden (H4.3); ordentliche
Kündigung durch den Vermieter mit Fristrechnung je Vertragsart (H4.6)."""

from __future__ import annotations

import datetime
from decimal import Decimal

import pytest
from django.test import Client
from django.urls import reverse

from apps.dokumente import services as dokumente
from apps.dokumente.models import Schreiben
from apps.dokumente.vorlagenarten import Versandweg
from apps.kern.models import Benutzer
from apps.mahnwesen import services
from apps.stammdaten import services as stammdaten
from apps.stammdaten.models import Einheit, EinheitStatus, Vertrag
from tests.mahnwesen.conftest import (
    FAELLIG_NOV,
    FAELLIG_OKT,
    Mahnhof,
    Vorlagen,
    lauf,
    letzter,
    senden,
    zustellen,
)

pytestmark = pytest.mark.django_db
D = datetime.date
TAG = datetime.timedelta(days=1)


def _bis_kuendigung(mahnhof: Mahnhof, vorlagen: Vorlagen, benutzer: Benutzer) -> Schreiben:
    vorlagen.fristlos()
    vertrag = mahnhof.vertrag()
    mahnhof.datenstand(D(2026, 11, 30))
    lauf(FAELLIG_OKT + 5 * TAG)
    senden(letzter(vertrag).schreiben, benutzer, FAELLIG_OKT + 5 * TAG)  # type: ignore[arg-type]
    lauf(FAELLIG_OKT + 14 * TAG)
    zustellen(senden(letzter(vertrag).schreiben, benutzer, FAELLIG_OKT + 14 * TAG), D(2026, 10, 20))  # type: ignore[arg-type]
    lauf(FAELLIG_NOV + TAG)
    zustellen(senden(letzter(vertrag).schreiben, benutzer, FAELLIG_NOV + TAG), FAELLIG_NOV + TAG)  # type: ignore[arg-type]
    lauf(FAELLIG_NOV + 16 * TAG)
    schritt = letzter(vertrag)
    assert schritt.stufe.schluessel == "kuendigung" and schritt.schreiben is not None
    return schritt.schreiben


def test_kuendigung_nur_mit_bestaetigung_des_anwalts(
    mahnhof: Mahnhof, vorlagen: Vorlagen, benutzer: Benutzer
) -> None:
    """Abnahme Schritt 7: Die Kündigung hat den Anwaltshinweis und verlangt vor dem Senden
    die Bestätigung, dass der Anwalt sie gesehen hat. Ohne Bestätigung kein Versand."""
    schreiben = _bis_kuendigung(mahnhof, vorlagen, benutzer)
    assert dokumente.braucht_anwalt(schreiben)

    with pytest.raises(dokumente.AnwaltBestaetigungFehlt, match="vor Versand zum Anwalt"):
        dokumente.schreiben_senden(schreiben, benutzer, pruefsumme=schreiben.pdf_sha256)
    schreiben.refresh_from_db()
    assert schreiben.status == Schreiben.Status.BEREIT

    dokumente.schreiben_senden(
        schreiben, benutzer, pruefsumme=schreiben.pdf_sha256, anwalt_gesehen=True
    )
    schreiben.refresh_from_db()
    assert schreiben.status == Schreiben.Status.GESENDET


def test_senden_knopf_verlangt_den_haken(
    mahnhof: Mahnhof, vorlagen: Vorlagen, benutzer: Benutzer, angemeldet: Client
) -> None:
    """Abnahme Schritt 7: Auf der Seite des Schreibens steht der Hinweis „vor Versand zum
    Anwalt“; der Senden-Knopf geht nur mit dem Haken „Der Anwalt hat sie gesehen“."""
    schreiben = _bis_kuendigung(mahnhof, vorlagen, benutzer)
    seite = angemeldet.get(reverse("dokumente:schreiben", args=[schreiben.pk])).content.decode()
    assert "vor Versand zum Anwalt" in seite
    assert 'name="anwalt_gesehen"' in seite

    url = reverse("dokumente:schreiben_senden", args=[schreiben.pk])
    antwort = angemeldet.post(url, {"pruefsumme": schreiben.pdf_sha256}, follow=True)
    assert "Nicht gesendet" in antwort.content.decode()
    schreiben.refresh_from_db()
    assert schreiben.status == Schreiben.Status.BEREIT

    angemeldet.post(url, {"pruefsumme": schreiben.pdf_sha256, "anwalt_gesehen": "1"})
    schreiben.refresh_from_db()
    assert schreiben.status == Schreiben.Status.GESENDET


def test_andere_schreiben_ohne_haken(
    mahnhof: Mahnhof, vorlagen: Vorlagen, angemeldet: Client
) -> None:
    """Erinnerung und Mahnung brauchen keine Bestätigung des Anwalts."""
    vertrag = mahnhof.vertrag()
    lauf(FAELLIG_OKT + 5 * TAG)
    schreiben = letzter(vertrag).schreiben
    assert schreiben is not None and not dokumente.braucht_anwalt(schreiben)
    seite = angemeldet.get(reverse("dokumente:schreiben", args=[schreiben.pk])).content.decode()
    assert 'name="anwalt_gesehen"' not in seite


# --- H4.6 Ordentliche Kündigung durch den Vermieter ----------------------------------


def _halle(mahnhof: Mahnhof) -> Vertrag:
    halle = stammdaten.einheit_anlegen(
        mahnhof.klein.halle,
        Einheit.Typ.HALLE,
        bezeichnung="Halle Test",
        status=EinheitStatus.Status.FREI,
        status_ab=D(2019, 1, 1),
    )
    return stammdaten.vertrag_anlegen(
        einheiten=[halle],
        mieter=[mahnhof.mieter],
        beginn=D(2026, 1, 1),
        miete=Decimal("400.00"),
        vertragsart=Vertrag.Art.H,
        status=Vertrag.Status.AKTIV,
        kaution=Decimal("0.00"),
    )


@pytest.mark.parametrize(
    ("zugang", "fristende"),
    [
        (D(2026, 11, 3), D(2027, 1, 31)),  # bis zum dritten Werktag: November zählt mit
        (D(2026, 11, 5), D(2027, 2, 28)),  # danach: erst der Dezember
    ],
)
def test_fristrechnung_garage(
    mahnhof: Mahnhof, zugang: datetime.date, fristende: datetime.date
) -> None:
    """H4.6: Das System rechnet die Frist je Vertragsart (Garage: § 580a Abs. 1 Nr. 3 BGB)."""
    frist = services.kuendigungsfrist(mahnhof.vertrag(), zugang)
    assert (frist.regel, frist.fristende) == ("monat_580a", fristende)


def test_fristrechnung_halle(mahnhof: Mahnhof) -> None:
    """H4.6: Halle kündigt zum Quartal (§ 580a Abs. 2 BGB)."""
    frist = services.kuendigungsfrist(_halle(mahnhof), D(2026, 10, 5))
    assert (frist.regel, frist.fristende) == ("quartal_580a", D(2027, 3, 31))


def test_ordentliche_kuendigung_aus_der_vorlage(
    mahnhof: Mahnhof, vorlagen: Vorlagen, benutzer: Benutzer
) -> None:
    """H4.6: Schreiben aus der Vorlage der Vertragsart (sonst der allgemeinen) mit dem
    gerechneten Fristende; Versand als einseitige Erklärung, erst nach dem Anwalt."""
    vorlagen.freigeben("kuendigung")  # „Kündigung durch den Hof“ (allgemein)
    garage = mahnhof.vertrag()
    schreiben = services.ordentliche_kuendigung_erzeugen(garage, D(2026, 11, 3))
    assert schreiben.status == Schreiben.Status.BEREIT
    assert schreiben.einseitige_erklaerung and schreiben.versandweg == Versandweg.SELBST
    assert "31.01.2027" in schreiben.inhalt_html
    assert dokumente.braucht_anwalt(schreiben)
    assert schreiben.bezug == garage

    vorlagen.freigeben(
        "kuendigung",
        "Kündigung Halle",
        "<p>Halle {{ vertrag.nummer }} zum {{ vertrag.ende|datum }}</p>",
    )
    halle = _halle(mahnhof)
    schreiben = services.ordentliche_kuendigung_erzeugen(halle, D(2026, 10, 5))
    assert schreiben.vorlage.name == "Kündigung Halle"
    assert "31.03.2027" in schreiben.inhalt_html


def test_ordentliche_kuendigung_ohne_vorlage(mahnhof: Mahnhof, vorlagen: Vorlagen) -> None:
    """Ohne freigegebene Vorlage entsteht kein Schreiben (Probebetrieb)."""
    with pytest.raises(services.MahnwesenFehler, match="Probebetrieb"):
        services.ordentliche_kuendigung_erzeugen(mahnhof.vertrag(), D(2026, 11, 3))
    assert not Schreiben.objects.exists()


def test_ordentliche_kuendigung_ueber_die_seite(
    mahnhof: Mahnhof, vorlagen: Vorlagen, angemeldet: Client
) -> None:
    vorlagen.freigeben("kuendigung")
    vertrag = mahnhof.vertrag()
    url = reverse("mahnwesen:kuendigen", args=[vertrag.pk])
    seite = angemeldet.get(url, {"zugang_am": "2026-11-05"}).content.decode()
    assert "28.02.2027" in seite and "vor Versand zum Anwalt" in seite

    antwort = angemeldet.post(url, {"zugang_am": "2026-11-05"})
    schreiben = Schreiben.objects.get(art="kuendigung")
    assert antwort.status_code == 302
    assert antwort["Location"] == reverse("dokumente:schreiben", args=[schreiben.pk])
    assert "28.02.2027" in schreiben.inhalt_html
