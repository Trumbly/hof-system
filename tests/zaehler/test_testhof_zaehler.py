"""Testhof-Modul der Zähler (Schritt 8, Aufgabe 8)."""

from __future__ import annotations

import datetime

from django.test import Client
from django.urls import reverse

from apps.stammdaten import datenstand
from apps.zaehler import services
from apps.zaehler.models import Zaehler, Zaehlerstand
from testhof import register
from testhof.aufbau import STICHTAG, Testhof

D = datetime.date


def test_zwanzig_zaehler_mit_staenden_ueber_zwei_jahre(testhof: Testhof) -> None:
    """Schritt 8, Aufgabe 8: 20 Zähler, Stände über zwei Jahre, ein Wechsel, ein fehlender
    Stichtagsstand. Deterministisch (die Gleichheit der Prüfsumme bei gleichem Seed prüft
    tests/testhof/test_generator.py über alle Tabellen)."""
    assert "zaehler" in register.laden()
    daten = testhof.zaehler
    assert Zaehler.objects.count() == len(daten.zaehler) == 20
    assert services.eingebaute_zaehler().count() == 19
    assert Zaehler.objects.filter(objekt__isnull=False).count() == 5
    assert not Zaehler.objects.filter(einheit__typ="wohnung").exists()

    erster = Zaehlerstand.objects.order_by("datum").filter(art="abgelesen").first()
    letzter = Zaehlerstand.objects.order_by("-datum").first()
    assert erster is not None and letzter is not None
    assert erster.datum <= D(2024, 12, 31) and letzter.datum >= D(2026, 6, 30)
    assert letzter.datum <= STICHTAG
    # Je eingebautem Zähler steigen die Stände.
    for zaehler in services.eingebaute_zaehler():
        staende = [s.stand for s in zaehler.staende.order_by("datum")]
        assert staende == sorted(staende), zaehler.nummer

    alter, neuer = daten.wechsel.alter, daten.wechsel.neuer
    assert alter.nachfolger == neuer and alter.ausgebaut_am == D(2025, 6, 15)
    assert neuer.staende.get(art="einbau").stand == 0
    assert neuer.letzter_stand().stand < alter.letzter_stand().stand  # type: ignore[union-attr]

    stichtag = services.letzter_stichtag(STICHTAG)
    assert stichtag == D(2025, 12, 31)
    ohne = [
        z.nummer
        for z in services.eingebaute_zaehler()
        if services.stand_zum_stichtag(z, stichtag) is None
    ]
    assert ohne == [daten.ohne_stichtagsstand.nummer]
    assert daten.geschaetzt.ist_geschaetzt and daten.geschaetzt.vermerk
    # Rückgaben aus dem Stammdaten-Modul haben ihre Zählerstände.
    assert daten.bei_rueckgabe and all(s.bei_uebergabe and s.uebergabe for s in daten.bei_rueckgabe)


def test_testhof_hat_luecken_im_bereich_zaehler(testhof: Testhof) -> None:
    stand = {b.schluessel: b for b in datenstand.ermitteln(STICHTAG)}["zaehler"]
    luecken = {luecke.kennung: {f for f, _ in luecke.felder} for luecke in stand.luecken}
    daten = testhof.zaehler
    assert luecken[f"Zähler {daten.ohne_stichtagsstand.nummer}"] >= {"stand_stichtag"}
    assert luecken[f"Zähler {daten.ohne_foto.nummer}"] >= {"foto_stichtag"}
    assert sum("eichfrist" in felder for felder in luecken.values()) == 2
    assert sum("einbaudatum" in felder for felder in luecken.values()) == 1


def test_seiten_mit_dem_testhof(testhof: Testhof, angemeldet: Client) -> None:
    """Die Seiten der Zähler laden mit den Daten des Testhofs; die Besuchsliste nennt den
    Zähler ohne Stichtagsstand, nicht aber den ausgebauten."""
    daten = testhof.zaehler
    seiten = [
        reverse("zaehler:uebersicht"),
        reverse("zaehler:besuch"),
        reverse("zaehler:zaehler", args=[daten.wechsel.alter.pk]),
        reverse("zaehler:zaehler", args=[daten.wechsel.neuer.pk]),
        *(reverse("zaehler:ablesung", args=[o.pk]) for o in testhof.kern.objekte.values()),
    ]
    for url in seiten:
        antwort = angemeldet.get(url)
        assert antwort.status_code == 200, url
    besuch = angemeldet.get(reverse("zaehler:besuch")).content.decode()
    assert daten.ohne_stichtagsstand.nummer in besuch
    assert daten.wechsel.alter.nummer not in besuch  # ausgebaut: nicht mehr zu besuchen
