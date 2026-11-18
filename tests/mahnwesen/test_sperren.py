"""Sperren im Mahnwesen (Standardablauf 3, Schritt 2): Rechtsfall, Klärfall, Ratenplan, Hand.
Gesperrte Fälle schreiten nicht fort; im Fall steht, warum."""

from __future__ import annotations

import datetime

import pytest

from apps.dokumente.models import Schreiben
from apps.kern.models import Arbeitsposten, Benutzer
from apps.mahnwesen import services
from apps.mahnwesen.models import Mahnfall, Mahnsperre
from tests.mahnwesen.conftest import (
    FAELLIG_OKT,
    Mahnhof,
    Vorlagen,
    fall_zu,
    lauf,
    letzter,
    mahnposten,
    schritte,
    senden,
)

pytestmark = pytest.mark.django_db
D = datetime.date
TAG = datetime.timedelta(days=1)


def test_sperre_haelt_den_fall_an_und_nennt_den_grund(
    mahnhof: Mahnhof, vorlagen: Vorlagen, benutzer: Benutzer
) -> None:
    """Abnahme Schritt 7: Sperren greifen. Ein Rechtsfall hält den Fall an: Das bereite
    Schreiben wird verworfen, es entsteht keine weitere Stufe, und im Fall steht der Grund."""
    vertrag = mahnhof.vertrag()
    lauf(FAELLIG_OKT + 5 * TAG)
    erinnerung = letzter(vertrag)

    sperre = services.sperre_setzen(
        vertrag, Mahnsperre.Grund.RECHTSFALL, von=FAELLIG_OKT + 6 * TAG, hinweis="Kanzlei"
    )
    fall = fall_zu(vertrag)
    assert fall.status == Mahnfall.Status.GESPERRT
    assert fall.sperrgrund == f"Rechtsfall (Sperre #{sperre.pk})"
    erinnerung.refresh_from_db()
    assert erinnerung.schreiben is not None
    assert erinnerung.schreiben.status == Schreiben.Status.VERWORFEN
    assert erinnerung.abgebrochen_am == FAELLIG_OKT + 6 * TAG
    assert mahnposten()[0].status == Arbeitsposten.Status.ERLEDIGT

    for woche in range(1, 6):
        ergebnis = lauf(FAELLIG_OKT + (5 + 7 * woche) * TAG)
        assert ergebnis.gesperrt == 1 and ergebnis.schreiben == 0
    assert len(schritte(vertrag)) == 1
    assert fall_zu(vertrag).sperrgrund == f"Rechtsfall (Sperre #{sperre.pk})"


def test_ratenplan_mit_ende_und_aufheben(mahnhof: Mahnhof, vorlagen: Vorlagen) -> None:
    """Abnahme Schritt 7: Ein Ratenplan sperrt bis zu seinem Ende; danach schreitet der
    Fall wieder fort (die Stufe kommt nach der Sperrfrist von 14 Tagen neu)."""
    vertrag = mahnhof.vertrag()
    lauf(FAELLIG_OKT + 5 * TAG)
    services.sperre_setzen(
        vertrag, Mahnsperre.Grund.RATENPLAN, von=FAELLIG_OKT + 6 * TAG, bis=D(2026, 10, 31)
    )
    lauf(D(2026, 10, 26))
    assert fall_zu(vertrag).status == Mahnfall.Status.GESPERRT
    assert "bis 31.10.2026" in fall_zu(vertrag).sperrgrund

    lauf(D(2026, 11, 2))
    fall = fall_zu(vertrag)
    assert fall.status == Mahnfall.Status.OFFEN and fall.sperrgrund == ""
    neu = letzter(vertrag)
    assert len(schritte(vertrag)) == 2 and neu.stufe.schluessel == "erinnerung"
    assert neu.schreiben is not None and neu.schreiben.status == Schreiben.Status.BEREIT


def test_von_hand_aufheben(mahnhof: Mahnhof, vorlagen: Vorlagen) -> None:
    vertrag = mahnhof.vertrag()
    sperre = services.sperre_setzen(vertrag, Mahnsperre.Grund.HAND, von=D(2026, 10, 1))
    lauf(FAELLIG_OKT + 5 * TAG)
    assert fall_zu(vertrag).status == Mahnfall.Status.GESPERRT
    assert not schritte(vertrag)

    services.sperre_aufheben(sperre, FAELLIG_OKT + 7 * TAG)
    with pytest.raises(services.MahnwesenFehler):
        services.sperre_aufheben(sperre, FAELLIG_OKT + 8 * TAG)
    lauf(FAELLIG_OKT + 7 * TAG)
    assert fall_zu(vertrag).status == Mahnfall.Status.OFFEN
    assert letzter(vertrag).stufe.schluessel == "erinnerung"


def test_gesendetes_schreiben_bleibt_bei_sperre(
    mahnhof: Mahnhof, vorlagen: Vorlagen, benutzer: Benutzer
) -> None:
    """Ein schon gesendetes Schreiben wird nicht verworfen; es kann noch belegt werden."""
    vertrag = mahnhof.vertrag()
    lauf(FAELLIG_OKT + 5 * TAG)
    senden(letzter(vertrag).schreiben, benutzer, FAELLIG_OKT + 5 * TAG)  # type: ignore[arg-type]
    services.sperre_setzen(vertrag, Mahnsperre.Grund.HAND, von=FAELLIG_OKT + 6 * TAG)
    lauf(FAELLIG_OKT + 14 * TAG)
    erinnerung = letzter(vertrag)
    assert erinnerung.abgebrochen_am is None
    assert erinnerung.belegt_am == FAELLIG_OKT + 5 * TAG
    assert len(schritte(vertrag)) == 1


def test_automatische_sperre_aus_einer_quelle(
    mahnhof: Mahnhof, vorlagen: Vorlagen, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Abnahme Schritt 7: Ein offener Klärfall sperrt automatisch; ist er geklärt, hebt der
    Lauf die Sperre auf. (Die Quelle „Klärfall“ fragt den Abgleich; hier ersetzt.)"""
    vertrag = mahnhof.vertrag()
    in_klaerung = {vertrag.pk}
    monkeypatch.setattr(services.abgleich, "vertraege_in_klaerung", lambda: set(in_klaerung))

    ergebnis = lauf(FAELLIG_OKT + 5 * TAG)
    (sperre,) = Mahnsperre.objects.filter(vertrag=vertrag)
    assert (sperre.grund, sperre.automatisch, ergebnis.sperren_gesetzt) == ("klaerfall", True, 1)
    assert fall_zu(vertrag).status == Mahnfall.Status.GESPERRT
    assert fall_zu(vertrag).sperrgrund.startswith("Klärfall")
    lauf(FAELLIG_OKT + 5 * TAG)
    assert Mahnsperre.objects.filter(vertrag=vertrag).count() == 1  # idempotent

    in_klaerung.clear()
    ergebnis = lauf(FAELLIG_OKT + 12 * TAG)
    sperre.refresh_from_db()
    assert sperre.aufgehoben_am == FAELLIG_OKT + 12 * TAG and ergebnis.sperren_aufgehoben == 1
    assert fall_zu(vertrag).status == Mahnfall.Status.OFFEN
    assert letzter(vertrag).stufe.schluessel == "erinnerung"


def test_sperre_pruefungen(mahnhof: Mahnhof) -> None:
    vertrag = mahnhof.vertrag()
    with pytest.raises(services.MahnwesenFehler):
        services.sperre_setzen(vertrag, "unbekannt", von=D(2026, 10, 1))
    with pytest.raises(services.MahnwesenFehler):
        services.sperre_setzen(
            vertrag, Mahnsperre.Grund.HAND, von=D(2026, 10, 2), bis=D(2026, 10, 1)
        )
    with pytest.raises(ValueError, match="Grund"):
        services.sperrquelle_anmelden("unbekannt", lambda tag: {})
