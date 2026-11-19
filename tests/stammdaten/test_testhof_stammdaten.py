"""Testhof-Modul der Stammdaten (Schritt 4, Aufgabe 9)."""

from __future__ import annotations

import datetime

from apps.stammdaten import datenstand, leerstand
from apps.stammdaten.models import Einheit, Kontaktweg, Partei, Vertrag
from testhof import erfunden, register
from testhof.aufbau import STICHTAG, Testhof

D = datetime.date


def test_groessenordnung_60_45_50(testhof: Testhof) -> None:
    """Schritt 4, Aufgabe 9: 60 Einheiten, 45 Verträge, 50 Parteien, deterministisch aus dem
    Seed (die Gleichheit prüft tests/testhof/test_generator.py über alle Tabellen)."""
    assert Einheit.objects.count() == 60
    assert Vertrag.objects.count() == 45
    assert Partei.objects.count() == 50
    assert Einheit.objects.filter(typ=Einheit.Typ.WOHNUNG).count() == 6
    assert not Vertrag.objects.filter(einheiten__typ=Einheit.Typ.WOHNUNG).exists()
    assert len(testhof.stammdaten.einheiten) == 60
    assert set(Vertrag.objects.values_list("status", flat=True)) == {"aktiv", "beendet"}


def test_sonderfaelle_sind_angemeldet_und_vorhanden(testhof: Testhof) -> None:
    """Schritt 4, Aufgabe 9: Sonderfälle „Vertragsbeginn mitten im Monat“, „Staffelmiete“ und
    „Kündigung, Rückgabe und Kautionsabrechnung“."""
    modul = register.laden()["stammdaten"]
    assert set(modul.sonderfaelle) == {
        "vertrag_monatsmitte",
        "staffelmiete",
        "kuendigung_rueckgabe_kaution",
    }
    faelle = testhof.stammdaten.sonderfaelle

    beginn, befristet = faelle["vertrag_monatsmitte"]
    assert beginn.beginn.day == 15
    assert befristet.ende is not None and befristet.ende.day == 15

    (staffel,) = faelle["staffelmiete"]
    stufen = list(staffel.konditionen.order_by("gueltig_ab"))
    assert len(stufen) == 3
    # eine Stufe im Testzeitraum (24 Monate bis zum Stichtag)
    assert any(
        STICHTAG - datetime.timedelta(days=730) < k.gueltig_ab <= STICHTAG for k in stufen[1:]
    )

    (fall,) = faelle["kuendigung_rueckgabe_kaution"]
    fall.refresh_from_db()
    assert fall.status == Vertrag.Status.BEENDET
    kuendigung = fall.kuendigungen.get()
    assert (kuendigung.zugang_am, kuendigung.fristende) == (D(2026, 7, 2), D(2026, 9, 30))
    assert fall.uebergaben.get().art == "rueckgabe"
    assert fall.kaution.abgerechnet_am == D(2026, 10, 20)
    (einheit,) = fall.einheiten.all()
    assert einheit.status_am(STICHTAG).status == "frei"  # type: ignore[union-attr]


def test_nur_erfundene_kontaktdaten(testhof: Testhof) -> None:
    """Abnahme Schritt 0, Punkt 4: Mails auf .invalid, Telefonnummern aus dem fiktiven Bereich."""
    for kontakt in Kontaktweg.objects.all():
        if kontakt.art == Kontaktweg.Art.EMAIL:
            assert erfunden.ist_invalid_mail(kontakt.wert), kontakt.pk
        else:
            assert erfunden.ist_fiktive_telefonnummer(kontakt.wert), kontakt.pk


def test_testhof_hat_leerstand_und_luecken(testhof: Testhof) -> None:
    zeilen = leerstand.berechnen(STICHTAG - datetime.timedelta(days=364), STICHTAG)
    assert len(zeilen) >= 9
    stand = {b.schluessel: b for b in datenstand.ermitteln(STICHTAG)}
    # Review 2, B12: zwei von drei Verträgen haben einen Scan, die übrigen nicht.
    ohne_scan = [lu for lu in stand["vertrag"].luecken if "scan" in dict(lu.felder)]
    assert 10 <= len(ohne_scan) <= 20
    assert stand["vertrag"].anzahl_luecken > len(ohne_scan)  # weitere Lücken als nur Scans
    assert stand["einheit"].anzahl_luecken >= 4
    assert stand["partei"].luecken
