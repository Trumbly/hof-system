"""Rechnung der Sollstellung ohne Datenbank (Schritt 5, Aufgabe 2 und 4).

Alle Erwartungswerte sind fest in Euro und Cent gerechnet; die Regeln stehen im Docstring
von ``apps.soll.rechnung``.
"""

from __future__ import annotations

import datetime
from decimal import Decimal
from fractions import Fraction

import pytest

from apps.soll import rechnung
from apps.soll.rechnung import Faelligkeitsregel, Planposten, Stufe
from apps.stammdaten import fristrechnung

D = datetime.date
E = Decimal
DRITTER_WERKTAG = Faelligkeitsregel(art="werktag", tag=3, land="NW")


def stufe(
    von: datetime.date,
    miete: str,
    *,
    bis: datetime.date | None = None,
    pauschale: str = "0",
    steuersatz: str = "0",
    zahlweise: str = "monatlich",
) -> Stufe:
    return Stufe(
        von=von,
        bis=bis,
        miete=E(miete),
        pauschale=E(pauschale),
        steuersatz=E(steuersatz),
        zahlweise=zahlweise,
    )


def plan(
    stufen: list[Stufe],
    *,
    beginn: datetime.date,
    bis_datum: datetime.date,
    ende: datetime.date | None = None,
    soll_ab: datetime.date | None = None,
    regel: Faelligkeitsregel = DRITTER_WERKTAG,
) -> list[Planposten]:
    return rechnung.plan(
        stufen, beginn=beginn, ende=ende, bis_datum=bis_datum, regel=regel, soll_ab=soll_ab
    )


def kurz(posten: list[Planposten]) -> list[tuple[str, datetime.date, datetime.date, Decimal]]:
    return [(p.art, p.von, p.bis, p.betrag) for p in posten]


# --- Bausteine -----------------------------------------------------------------------


def test_runden_kaufmaennisch() -> None:
    assert rechnung.runden(Fraction(1015 * 3, 3000)) == E("1.02")  # 1,015 -> 1,02
    assert rechnung.runden(Fraction(10144, 10000)) == E("1.01")
    assert rechnung.runden(E("45.4119")) == E("45.41")


def test_perioden_im_kalenderraster() -> None:
    assert rechnung.periode(D(2026, 5, 17), 1) == (D(2026, 5, 1), D(2026, 5, 31))
    assert rechnung.periode(D(2026, 5, 17), 3) == (D(2026, 4, 1), D(2026, 6, 30))
    assert rechnung.periode(D(2026, 8, 2), 6) == (D(2026, 7, 1), D(2026, 12, 31))
    assert rechnung.periode(D(2026, 2, 28), 12) == (D(2026, 1, 1), D(2026, 12, 31))


def test_monatsanteil_taggenau_je_kalendermonat() -> None:
    assert rechnung.monatsanteil(D(2026, 3, 15), D(2026, 3, 31)) == Fraction(17, 31)
    assert rechnung.monatsanteil(D(2026, 2, 1), D(2026, 2, 28)) == 1
    assert rechnung.monatsanteil(D(2026, 2, 15), D(2026, 3, 31)) == Fraction(3, 2)


def test_feiertage_nrw_an_einer_stelle() -> None:
    """Schritt 5, Aufgabe 4: Feiertage NRW (Fronleichnam, Allerheiligen) zusätzlich zu den
    bundesweiten; die Kündigung rechnet weiter nur mit den bundesweiten."""
    nrw = fristrechnung.feiertage(2026, "NW")
    assert D(2026, 6, 4) in nrw  # Fronleichnam (Ostern 05.04.2026 + 60)
    assert D(2026, 11, 1) in nrw  # Allerheiligen
    assert D(2026, 4, 3) in nrw  # Karfreitag (bundesweit)
    assert D(2026, 6, 4) not in fristrechnung.feiertage(2026)
    assert not fristrechnung.ist_werktag(D(2027, 11, 1), land="NW")  # Montag, Allerheiligen
    assert fristrechnung.ist_werktag(D(2027, 11, 1))  # für die Kündigung ein Werktag
    with pytest.raises(ValueError, match="XX"):
        fristrechnung.feiertage(2026, "XX")


@pytest.mark.parametrize(
    ("beginn", "erwartet"),
    [
        (D(2026, 7, 1), D(2026, 7, 3)),  # Mi, Do, Fr
        (D(2026, 8, 1), D(2026, 8, 5)),  # Samstag zählt nicht
        (D(2026, 10, 1), D(2026, 10, 5)),  # 03.10. Tag der Deutschen Einheit (Samstag)
        (D(2026, 11, 1), D(2026, 11, 4)),  # 01.11. Sonntag und Allerheiligen
        (D(2027, 11, 1), D(2027, 11, 4)),  # Allerheiligen am Montag (ohne NRW: 03.11.)
        (D(2026, 4, 1), D(2026, 4, 7)),  # Karfreitag 03.04., Ostermontag 06.04.
        (D(2027, 1, 1), D(2027, 1, 6)),  # Neujahr
        (D(2026, 3, 15), D(2026, 3, 16)),  # Beginn am Sonntag: erster Werktag ab Beginn
        (D(2026, 6, 2), D(2026, 6, 3)),  # Beginn vor dem dritten Werktag: der dritte
    ],
)
def test_faelligkeit_dritter_werktag_nrw(beginn: datetime.date, erwartet: datetime.date) -> None:
    """Schritt 5, Aufgabe 4: Fälligkeit am dritten Werktag, Feiertage NRW, frühestens am
    Beginn des Zeitraums."""
    assert rechnung.faellig_am(beginn, DRITTER_WERKTAG) == erwartet


def test_faelligkeit_fester_kalendertag() -> None:
    regel = Faelligkeitsregel(art="kalendertag", tag=1)
    assert rechnung.faellig_am(D(2027, 1, 1), regel) == D(2027, 1, 1)  # auch am Feiertag
    assert rechnung.faellig_am(D(2027, 1, 15), regel) == D(2027, 1, 15)
    ende = Faelligkeitsregel(art="kalendertag", tag=31)
    assert rechnung.faellig_am(D(2026, 2, 1), ende) == D(2026, 2, 28)


# --- Plan ----------------------------------------------------------------------------


def test_monatlich_volle_monate() -> None:
    """Schritt 5, Aufgabe 2: monatlich; ein voller Monat ist immer die volle Monatsmiete."""
    posten = plan([stufe(D(2026, 1, 1), "85.00")], beginn=D(2026, 1, 1), bis_datum=D(2026, 3, 31))
    assert kurz(posten) == [
        ("miete", D(2026, 1, 1), D(2026, 1, 31), E("85.00")),
        ("miete", D(2026, 2, 1), D(2026, 2, 28), E("85.00")),
        ("miete", D(2026, 3, 1), D(2026, 3, 31), E("85.00")),
    ]
    assert [p.faellig_am for p in posten] == [D(2026, 1, 6), D(2026, 2, 4), D(2026, 3, 4)]


def test_beginn_mitten_im_monat_anteilig() -> None:
    """Schritt 5, Aufgabe 2 (Monatsmitte): Beginn am 15.03.2026 kostet 17/31 der Miete."""
    posten = plan([stufe(D(2026, 3, 15), "100.00")], beginn=D(2026, 3, 15), bis_datum=D(2026, 4, 1))
    assert kurz(posten) == [
        ("miete", D(2026, 3, 15), D(2026, 3, 31), E("54.84")),  # 100 × 17/31 = 54,838…
        ("miete", D(2026, 4, 1), D(2026, 4, 30), E("100.00")),
    ]
    assert posten[0].faellig_am == D(2026, 3, 16)  # 15.03.2026 ist ein Sonntag


def test_ende_mitten_im_monat_anteilig() -> None:
    """Schritt 5, Aufgabe 2 (Monatsmitte): Ende am 15.06.2026 kostet 15/30 der Miete."""
    posten = plan(
        [stufe(D(2026, 1, 1), "72.20")],
        beginn=D(2026, 1, 1),
        ende=D(2026, 6, 15),
        bis_datum=D(2026, 12, 31),
    )
    assert len(posten) == 6
    assert kurz(posten)[-1] == ("miete", D(2026, 6, 1), D(2026, 6, 15), E("36.10"))


def test_beginn_und_ende_im_selben_monat() -> None:
    posten = plan(
        [stufe(D(2026, 2, 10), "100.00")],
        beginn=D(2026, 2, 10),
        ende=D(2026, 2, 20),
        bis_datum=D(2026, 12, 31),
    )
    assert kurz(posten) == [("miete", D(2026, 2, 10), D(2026, 2, 20), E("39.29"))]  # 11/28


def test_staffel_stufe_zum_monatsersten() -> None:
    """Schritt 5, Aufgabe 2 (Staffel): Die Stufe am 01.01.2026 gilt ab dem Januarposten."""
    stufen = [
        stufe(D(2024, 1, 1), "73.08", bis=D(2025, 12, 31)),
        stufe(D(2026, 1, 1), "76.73", bis=D(2026, 12, 31)),
        stufe(D(2027, 1, 1), "80.39"),
    ]
    posten = plan(stufen, beginn=D(2024, 1, 1), bis_datum=D(2027, 1, 31), soll_ab=D(2025, 11, 1))
    assert kurz(posten) == [
        ("miete", D(2025, 11, 1), D(2025, 11, 30), E("73.08")),
        ("miete", D(2025, 12, 1), D(2025, 12, 31), E("73.08")),
        *[
            ("miete", D(2026, m, 1), fristrechnung.monatsende(D(2026, m, 1)), E("76.73"))
            for m in range(1, 13)
        ],
        ("miete", D(2027, 1, 1), D(2027, 1, 31), E("80.39")),
    ]


def test_staffel_mitten_im_monat_wird_geteilt() -> None:
    """Schritt 5, Aufgabe 2: Wechselt die Kondition mitten im Monat, wird anteilig geteilt."""
    stufen = [
        stufe(D(2026, 1, 1), "100.00", bis=D(2026, 3, 15)),
        stufe(D(2026, 3, 16), "110.00"),
    ]
    posten = plan(stufen, beginn=D(2026, 1, 1), bis_datum=D(2026, 4, 30))
    assert kurz(posten)[2:] == [
        ("miete", D(2026, 3, 1), D(2026, 3, 15), E("48.39")),  # 100 × 15/31
        ("miete", D(2026, 3, 16), D(2026, 3, 31), E("56.77")),  # Rest von 105,16
        ("miete", D(2026, 4, 1), D(2026, 4, 30), E("110.00")),
    ]
    assert posten[3].faellig_am == D(2026, 3, 16)


def test_rest_im_letzten_teilposten() -> None:
    """Schritt 5, Aufgabe 2: Rundung auf Cent, Rest im letzten Teilposten. 10,15 € im Juni
    geteilt nach drei Tagen: 1,015 → 1,02 und 9,135 → 9,14 ergäben 10,16; der letzte Teil
    bekommt deshalb den Rest 9,13, zusammen genau 10,15."""
    stufen = [
        stufe(D(2026, 1, 1), "10.15", bis=D(2026, 6, 3)),
        stufe(D(2026, 6, 4), "10.15", steuersatz="19"),
    ]
    posten = plan(stufen, beginn=D(2026, 1, 1), bis_datum=D(2026, 6, 30))
    erster, zweiter = posten[-2:]
    assert (erster.von, erster.bis, erster.netto, erster.steuer) == (
        D(2026, 6, 1),
        D(2026, 6, 3),
        E("1.02"),
        E("0.00"),
    )
    assert (zweiter.von, zweiter.bis, zweiter.netto, zweiter.steuer, zweiter.betrag) == (
        D(2026, 6, 4),
        D(2026, 6, 30),
        E("9.13"),
        E("1.73"),  # 9,13 × 19 % = 1,7347
        E("10.86"),
    )
    assert erster.netto + zweiter.netto == E("10.15")


def test_jahreszahler_ein_posten_je_kalenderjahr() -> None:
    """Schritt 5, Aufgabe 2 (Jahreszahler): jährlich ein Posten über zwölf Monatsmieten,
    das erste Jahr anteilig ab Vertragsbeginn."""
    posten = plan(
        [stufe(D(2023, 4, 1), "76.10", zahlweise="jaehrlich")],
        beginn=D(2023, 4, 1),
        bis_datum=D(2026, 11, 30),
    )
    assert kurz(posten) == [
        ("miete", D(2023, 4, 1), D(2023, 12, 31), E("684.90")),  # 9 × 76,10
        ("miete", D(2024, 1, 1), D(2024, 12, 31), E("913.20")),
        ("miete", D(2025, 1, 1), D(2025, 12, 31), E("913.20")),
        ("miete", D(2026, 1, 1), D(2026, 12, 31), E("913.20")),
    ]
    assert [p.faellig_am for p in posten] == [
        D(2023, 4, 5),  # Mo 03.04., Di 04.04., Mi 05.04.
        D(2024, 1, 4),
        D(2025, 1, 6),
        D(2026, 1, 6),
    ]


def test_jahreszahler_mit_beginn_im_alten_system() -> None:
    """Schritt 5: Perioden, die vor dem Beginn der Sollstellung anfangen, führt das alte
    System; das Jahr 2024 entsteht bei Sollstellung ab 01.11.2024 nicht."""
    posten = plan(
        [stufe(D(2023, 4, 1), "76.10", zahlweise="jaehrlich")],
        beginn=D(2023, 4, 1),
        bis_datum=D(2026, 11, 30),
        soll_ab=D(2024, 11, 1),
    )
    assert [p.von for p in posten] == [D(2025, 1, 1), D(2026, 1, 1)]


def test_vierteljaehrlich_mit_beginn_mitten_im_quartal() -> None:
    """Schritt 5, Aufgabe 2: vierteljährlich; Beginn am 15.02.2026 kostet im ersten Quartal
    einen halben Februar und den März."""
    posten = plan(
        [stufe(D(2026, 2, 15), "90.00", zahlweise="vierteljaehrlich")],
        beginn=D(2026, 2, 15),
        bis_datum=D(2026, 7, 1),
    )
    assert kurz(posten) == [
        ("miete", D(2026, 2, 15), D(2026, 3, 31), E("135.00")),  # 14/28 × 90 + 90
        ("miete", D(2026, 4, 1), D(2026, 6, 30), E("270.00")),
        ("miete", D(2026, 7, 1), D(2026, 9, 30), E("270.00")),
    ]
    assert [p.faellig_am for p in posten] == [D(2026, 2, 16), D(2026, 4, 7), D(2026, 7, 3)]


def test_halbjaehrlich_und_fehlende_zahlweise() -> None:
    halb = plan(
        [stufe(D(2026, 1, 1), "50.00", zahlweise="halbjaehrlich")],
        beginn=D(2026, 1, 1),
        bis_datum=D(2026, 12, 31),
    )
    assert kurz(halb) == [
        ("miete", D(2026, 1, 1), D(2026, 6, 30), E("300.00")),
        ("miete", D(2026, 7, 1), D(2026, 12, 31), E("300.00")),
    ]
    ohne = plan(
        [stufe(D(2026, 1, 1), "50.00", zahlweise="")], beginn=D(2026, 1, 1), bis_datum=D(2026, 2, 1)
    )
    assert len(ohne) == 2  # noch nicht erfasst: monatlich


def test_pauschale_und_steuer_als_eigene_posten() -> None:
    """Schritt 5, Aufgabe 1: Miete und Pauschale getrennt, mit Steuersatz und Steuerbetrag."""
    (miete, pauschale) = plan(
        [stufe(D(2026, 1, 1), "1000.00", pauschale="50.00", steuersatz="19")],
        beginn=D(2026, 1, 1),
        bis_datum=D(2026, 1, 31),
    )
    assert (miete.art, miete.netto, miete.steuer, miete.betrag) == (
        "miete",
        E("1000.00"),
        E("190.00"),
        E("1190.00"),
    )
    assert (pauschale.art, pauschale.netto, pauschale.steuer, pauschale.betrag) == (
        "pauschale",
        E("50.00"),
        E("9.50"),
        E("59.50"),
    )


def test_bis_datum_und_miete_null() -> None:
    """Es entstehen nur Posten, deren Zeitraum bis zum Bis-Datum beginnt; Miete 0 ergibt
    keinen Posten."""
    posten = plan([stufe(D(2026, 1, 1), "85.00")], beginn=D(2026, 1, 1), bis_datum=D(2026, 2, 1))
    assert [p.von for p in posten] == [D(2026, 1, 1), D(2026, 2, 1)]
    assert plan([stufe(D(2026, 1, 1), "0.00")], beginn=D(2026, 1, 1), bis_datum=D(2026, 6, 1)) == []


def test_teilung_haengt_nicht_vom_bis_datum_ab() -> None:
    """Die Rundung eines geteilten Monats ist gleich, ob er auf einmal oder in zwei Läufen
    entsteht (Idempotenz über Läufe hinweg)."""
    stufen = [
        stufe(D(2026, 1, 1), "10.15", bis=D(2026, 6, 3)),
        stufe(D(2026, 6, 4), "10.15", steuersatz="19"),
    ]
    frueh = plan(stufen, beginn=D(2026, 1, 1), bis_datum=D(2026, 6, 2))
    spaet = plan(stufen, beginn=D(2026, 1, 1), bis_datum=D(2026, 6, 30))
    assert frueh == spaet[:-1]
