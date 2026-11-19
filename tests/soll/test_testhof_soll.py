"""Sollstellung im Testhof (Schritt 5, Aufgabe 8 und Abnahme).

Die Erwartungswerte sind fest in Euro und Cent; sie folgen aus Seed 42 und den Regeln in
``apps.soll.rechnung``.
"""

from __future__ import annotations

import datetime
from collections.abc import Callable
from decimal import Decimal

import pytest

from apps.soll import services
from apps.soll.models import Sollposten
from apps.stammdaten.models import Vertrag
from testhof import aufbau, pruefsumme, register
from testhof.aufbau import STICHTAG, Testhof
from testhof.soll import SOLL_AB, SOLL_AB_JAHRESZAHLER

D = datetime.date
E = Decimal
#: Früherer Beginn für die Abnahmen von Schritt 5: Teilmonate und Staffel liegen vor dem
#: Umzug am 01.07.2026; die Tests stellen das Soll ab dem alten Testzeitraum.
FRUEHER = D(2024, 11, 1)


@pytest.fixture
def ab_frueher(monkeypatch: pytest.MonkeyPatch) -> Callable[[Vertrag], Vertrag]:
    """Stellt das Soll eines Vertrags ab ``FRUEHER`` (wie Max es je Vertrag über die
    Sollregel kann), „heute“ ist der Stichtag des Testhofs."""
    monkeypatch.setattr(services, "heute", lambda: STICHTAG)

    def stellen(vertrag: Vertrag) -> Vertrag:
        services.sollregel_setzen(vertrag, soll_ab=FRUEHER)
        return vertrag

    return stellen


def _posten(vertrag: Vertrag) -> list[tuple[datetime.date, datetime.date, Decimal, datetime.date]]:
    return [
        (p.von, p.bis, p.betrag, p.faellig_am)
        for p in Sollposten.objects.filter(vertrag=vertrag)
        .exclude(status=Sollposten.Status.STORNIERT)
        .exclude(art=Sollposten.Art.VORTRAG)
        .order_by("von")
    ]


def test_sonderfaelle_angemeldet(testhof: Testhof) -> None:
    """Schritt 5, Aufgabe 8: Das Modul soll erzeugt die Sonderfälle Monatsmitte, Staffel und
    Jahreszahler."""
    modul = register.laden()["soll"]
    assert modul.abhaengig_von == ("stammdaten",)
    assert set(modul.sonderfaelle) == {"vertrag_monatsmitte", "staffelmiete", "jahreszahler"}
    faelle = testhof.soll.sonderfaelle
    assert [len(faelle[s]) for s in modul.sonderfaelle] == [2, 1, 1]


def test_soll_aller_vertraege_bis_zum_stichtag(testhof: Testhof) -> None:
    """Schritt 5, Aufgabe 8, und Review 1, Punkt 1: Soll für alle Testhof-Verträge ab dem
    Stichtag des Umzugs bis zum festen Stichtag, alles offen, nichts storniert. Davor nur
    das Jahr 2026 des Jahreszahlers und der Vortrag."""
    assert Sollposten.objects.count() == 215
    vorher = Sollposten.objects.filter(von__lt=SOLL_AB)
    assert list(vorher.values_list("art", "von")) == [("miete", SOLL_AB_JAHRESZAHLER)]
    assert Sollposten.objects.filter(art=Sollposten.Art.VORTRAG).count() == 1
    assert Sollposten.objects.filter(von__gt=STICHTAG).count() == 0
    # Bezahlt ist nur die Barzahlung aus dem Abgleich (testhof/abgleich.py, Schritt 6).
    assert set(Sollposten.objects.values_list("status", flat=True)) == {"offen", "bezahlt"}
    assert Sollposten.objects.filter(status="bezahlt").count() == 1
    ergebnis = testhof.soll.ergebnis
    assert (len(ergebnis.neu), ergebnis.vertraege, ergebnis.fehler) == (214, 45, [])


def test_abnahme_vertragsbeginn_zur_monatsmitte(
    testhof: Testhof, ab_frueher: Callable[[Vertrag], Vertrag]
) -> None:
    """Abnahme Schritt 5: Vertragsbeginn zur Monatsmitte ergibt im Testhof den erwarteten
    Teilposten (Beginn 15.03.2025, Miete 82,81 €: 17/31 = 45,41 €, fällig am Montag)."""
    beginn, _ = testhof.soll.sonderfaelle["vertrag_monatsmitte"]
    posten = _posten(ab_frueher(beginn))
    assert posten[:2] == [
        (D(2025, 3, 15), D(2025, 3, 31), E("45.41"), D(2025, 3, 17)),
        (D(2025, 4, 1), D(2025, 4, 30), E("82.81"), D(2025, 4, 3)),
    ]
    assert len(posten) == 21  # März 2025 bis November 2026
    assert posten[-1] == (D(2026, 11, 1), D(2026, 11, 30), E("82.81"), D(2026, 11, 4))


def test_abnahme_vertragsende_zur_monatsmitte(
    testhof: Testhof, ab_frueher: Callable[[Vertrag], Vertrag]
) -> None:
    """Abnahme Schritt 5: Ende am 15.06.2026 ergibt den Teilposten 15/30 von 72,20 € und
    danach nichts mehr."""
    _, befristet = testhof.soll.sonderfaelle["vertrag_monatsmitte"]
    assert _posten(befristet) == []  # endet vor dem Umzug: kein Soll im Hof-System
    posten = _posten(ab_frueher(befristet))
    assert posten[0] == (D(2024, 11, 1), D(2024, 11, 30), E("72.20"), D(2024, 11, 6))
    assert posten[-1] == (D(2026, 6, 1), D(2026, 6, 15), E("36.10"), D(2026, 6, 3))
    assert len(posten) == 20  # November 2024 bis Juni 2026


def test_abnahme_staffel(testhof: Testhof, ab_frueher: Callable[[Vertrag], Vertrag]) -> None:
    """Abnahme Schritt 5: Die Staffel erhöht die Miete ab dem Januarposten 2026 von 73,08 €
    auf 76,73 €; die Stufe 2027 liegt nach dem Stichtag."""
    (staffel,) = testhof.soll.sonderfaelle["staffelmiete"]
    betraege = {von: betrag for von, _, betrag, _ in _posten(ab_frueher(staffel))}
    assert betraege[D(2025, 12, 1)] == E("73.08")
    assert betraege[D(2026, 1, 1)] == E("76.73")
    assert betraege[D(2026, 11, 1)] == E("76.73")
    werte = list(betraege.values())
    assert (len(werte), werte.count(E("73.08")), werte.count(E("76.73"))) == (25, 14, 11)
    assert sum(werte) == E("1867.15")  # 14 × 73,08 + 11 × 76,73


def test_abnahme_jahreszahler(testhof: Testhof, ab_frueher: Callable[[Vertrag], Vertrag]) -> None:
    """Abnahme Schritt 5: Der Jahreszahler hat je Kalenderjahr einen Posten über zwölf
    Monatsmieten (76,10 € × 12 = 913,20 €), fällig am dritten Werktag im Januar. Im Testhof
    beginnt sein Soll mit dem Jahr, in das der Umzug fällt (Review 1, Punkt 1); ab dem alten
    Testzeitraum gehört das Jahr 2024 dem alten System."""
    (jahreszahler,) = testhof.soll.sonderfaelle["jahreszahler"]
    jahr_2026 = (D(2026, 1, 1), D(2026, 12, 31), E("913.20"), D(2026, 1, 6))
    assert _posten(jahreszahler) == [jahr_2026]
    assert _posten(ab_frueher(jahreszahler)) == [
        (D(2025, 1, 1), D(2025, 12, 31), E("913.20"), D(2025, 1, 6)),
        jahr_2026,
    ]


def test_kuendigung_im_testhof_endet_zum_fristende(testhof: Testhof) -> None:
    """Schritt 5, Aufgabe 5: Im Testhof endet das Soll des gekündigten Vertrags mit dem
    Fristende 30.09.2026."""
    (gekuendigt,) = testhof.stammdaten.sonderfaelle["kuendigung_rueckgabe_kaution"]
    assert _posten(gekuendigt)[-1][:2] == (D(2026, 9, 1), D(2026, 9, 30))


@pytest.mark.django_db
def test_pruefsumme_zweimal_gleich() -> None:
    """Schritt 5, Aufgabe 8: Gleicher Seed ergibt dasselbe Soll (Prüfsumme zweimal gleich)."""
    aufbau.aufbauen(seed=42)
    erster = pruefsumme.tabellen_hashes()
    aufbau.leeren()
    aufbau.aufbauen(seed=42)
    zweiter = pruefsumme.tabellen_hashes()
    assert erster["soll.Sollposten"] == zweiter["soll.Sollposten"]
    assert erster["soll.Sollregel"] == zweiter["soll.Sollregel"]
    assert erster == zweiter
