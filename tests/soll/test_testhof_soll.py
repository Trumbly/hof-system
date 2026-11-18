"""Sollstellung im Testhof (Schritt 5, Aufgabe 8 und Abnahme).

Die Erwartungswerte sind fest in Euro und Cent; sie folgen aus Seed 42 und den Regeln in
``apps.soll.rechnung``.
"""

from __future__ import annotations

import datetime
from decimal import Decimal

import pytest

from apps.soll.models import Sollposten
from apps.stammdaten.models import Vertrag
from testhof import aufbau, pruefsumme, register
from testhof.aufbau import STICHTAG, Testhof
from testhof.soll import SOLL_AB

D = datetime.date
E = Decimal


def _posten(vertrag: Vertrag) -> list[tuple[datetime.date, datetime.date, Decimal, datetime.date]]:
    return [
        (p.von, p.bis, p.betrag, p.faellig_am)
        for p in Sollposten.objects.filter(vertrag=vertrag)
        .exclude(status=Sollposten.Status.STORNIERT)
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
    """Schritt 5, Aufgabe 8: Soll für alle Testhof-Verträge ab dem Testzeitraum bis zum festen
    Stichtag, alles offen, nichts storniert."""
    assert Sollposten.objects.count() == 1028
    assert Sollposten.objects.filter(von__lt=SOLL_AB).count() == 0
    assert Sollposten.objects.filter(von__gt=STICHTAG).count() == 0
    # Bezahlt ist nur die Barzahlung aus dem Abgleich (testhof/abgleich.py, Schritt 6).
    assert set(Sollposten.objects.values_list("status", flat=True)) == {"offen", "bezahlt"}
    assert Sollposten.objects.filter(status="bezahlt").count() == 1
    ergebnis = testhof.soll.ergebnis
    assert (len(ergebnis.neu), ergebnis.vertraege, ergebnis.fehler) == (1028, 45, [])


def test_abnahme_vertragsbeginn_zur_monatsmitte(testhof: Testhof) -> None:
    """Abnahme Schritt 5: Vertragsbeginn zur Monatsmitte ergibt im Testhof den erwarteten
    Teilposten (Beginn 15.03.2025, Miete 82,81 €: 17/31 = 45,41 €, fällig am Montag)."""
    beginn, _ = testhof.soll.sonderfaelle["vertrag_monatsmitte"]
    posten = _posten(beginn)
    assert posten[:2] == [
        (D(2025, 3, 15), D(2025, 3, 31), E("45.41"), D(2025, 3, 17)),
        (D(2025, 4, 1), D(2025, 4, 30), E("82.81"), D(2025, 4, 3)),
    ]
    assert len(posten) == 21  # März 2025 bis November 2026
    assert posten[-1] == (D(2026, 11, 1), D(2026, 11, 30), E("82.81"), D(2026, 11, 4))


def test_abnahme_vertragsende_zur_monatsmitte(testhof: Testhof) -> None:
    """Abnahme Schritt 5: Ende am 15.06.2026 ergibt den Teilposten 15/30 von 72,20 € und
    danach nichts mehr."""
    _, befristet = testhof.soll.sonderfaelle["vertrag_monatsmitte"]
    posten = _posten(befristet)
    assert posten[0] == (D(2024, 11, 1), D(2024, 11, 30), E("72.20"), D(2024, 11, 6))
    assert posten[-1] == (D(2026, 6, 1), D(2026, 6, 15), E("36.10"), D(2026, 6, 3))
    assert len(posten) == 20  # November 2024 bis Juni 2026


def test_abnahme_staffel(testhof: Testhof) -> None:
    """Abnahme Schritt 5: Die Staffel erhöht die Miete ab dem Januarposten 2026 von 73,08 €
    auf 76,73 €; die Stufe 2027 liegt nach dem Stichtag."""
    (staffel,) = testhof.soll.sonderfaelle["staffelmiete"]
    betraege = {von: betrag for von, _, betrag, _ in _posten(staffel)}
    assert betraege[D(2025, 12, 1)] == E("73.08")
    assert betraege[D(2026, 1, 1)] == E("76.73")
    assert betraege[D(2026, 11, 1)] == E("76.73")
    werte = list(betraege.values())
    assert (len(werte), werte.count(E("73.08")), werte.count(E("76.73"))) == (25, 14, 11)
    assert sum(werte) == E("1867.15")  # 14 × 73,08 + 11 × 76,73


def test_abnahme_jahreszahler(testhof: Testhof) -> None:
    """Abnahme Schritt 5: Der Jahreszahler hat je Kalenderjahr einen Posten über zwölf
    Monatsmieten (76,10 € × 12 = 913,20 €), fällig am dritten Werktag im Januar. Das Jahr
    2024 begann vor dem Testzeitraum und gehört dem alten System."""
    (jahreszahler,) = testhof.soll.sonderfaelle["jahreszahler"]
    assert _posten(jahreszahler) == [
        (D(2025, 1, 1), D(2025, 12, 31), E("913.20"), D(2025, 1, 6)),
        (D(2026, 1, 1), D(2026, 12, 31), E("913.20"), D(2026, 1, 6)),
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
