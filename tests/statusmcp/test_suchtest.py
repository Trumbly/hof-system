"""Suchtest (Abnahme Schritt 11): Keine Antwort des Status-MCP enthält Personendaten.

Jedes Werkzeug wird gegen den Testhof nach einem Betriebstag aufgerufen, über den
MCP-Client des SDK per Streamable HTTP mit Token, und jede Antwort wird nach allen Werten
registrierter Personenfelder des Testhofs durchsucht: Namen, IBANs, Anschriften,
Verwendungszwecke, Hinweise, Notizen. Dazu die erfundenen Namen und Orte (Muster aus
``tests/dokumente/test_vorlagen_ohne_namen.py``). Ein Fund lässt den Test fehlschlagen.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from typing import Any

import pytest

from apps.kern import personenfelder
from apps.kern import services as kern
from tests.dokumente.test_vorlagen_ohne_namen import funde as namensfunde
from tests.dokumente.test_vorlagen_ohne_namen import namen_aus_dem_testhof
from tests.statusmcp.conftest import ALLE_WERKZEUGE, LaufenderServer, antworttext, aufrufen

pytestmark = pytest.mark.django_db

#: Kürzere Werte sind kein sinnvoller Suchbegriff (Kürzel, Ziffern, „ja“).
MINDESTLAENGE = 5
WORTZEICHEN = r"\wÄÖÜäöüß"


def _texte(wert: Any) -> Iterable[str]:
    """Alle Texte eines Feldwerts, auch aus JSON (Rohdaten der Bank)."""
    if wert is None:
        return
    if isinstance(wert, dict):
        for schluessel, inhalt in wert.items():
            yield from _texte(schluessel)
            yield from _texte(inhalt)
    elif isinstance(wert, list | tuple):
        for inhalt in wert:
            yield from _texte(inhalt)
    else:
        yield str(wert)


def personenwerte() -> set[str]:
    """Jeder Wert jedes registrierten Personenfelds im Testhof, je Zeile, ab fünf Zeichen.

    IBANs zusätzlich ohne Leerzeichen. Passwort-Hashes zählen nicht (sie sind keine
    Personendaten aus dem Testhof, sondern zufällig je Lauf).
    """
    werte: set[str] = set()
    for modell in personenfelder.eigene_modelle():
        felder = personenfelder.personenfelder(modell) - {"password"}
        if not felder or hasattr(modell, "pgh_tracked_model"):
            continue
        for zeile in modell._default_manager.values_list(*sorted(felder)):
            for wert in zeile:
                for text in _texte(wert):
                    for teil in [text, *text.splitlines()]:
                        teil = " ".join(teil.split())
                        if len(teil) >= MINDESTLAENGE:
                            werte.add(teil)
                            if re.fullmatch(r"[A-Z]{2}\d{2}[ \d]+", teil):
                                werte.add(teil.replace(" ", ""))
    return werte


def treffer(antwort: str, werte: set[str]) -> list[str]:
    """Werte, die wörtlich in der Antwort stehen (an Wortgrenzen; Zahlen nicht als Teil
    einer anderen Zahl)."""
    gefunden = []
    for wert in sorted(werte):
        if wert not in antwort:
            continue
        rand = r"\d.," if re.fullmatch(r"[\d .,+/-]+", wert) else WORTZEICHEN
        if re.search(rf"(?<![{rand}]){re.escape(wert)}(?![{rand}])", antwort):
            gefunden.append(wert)
    return gefunden


def suchen(antworten: dict[str, str]) -> list[str]:
    werte = personenwerte()
    namen = namen_aus_dem_testhof()
    assert len(werte) > 200, "Der Testhof sollte viele Personenwerte haben."
    assert len(namen) >= 5
    ergebnis = []
    for quelle, text in antworten.items():
        ergebnis += [f"{quelle}: {wert}" for wert in treffer(text, werte)]
    ergebnis += namensfunde(antworten.items(), namen)
    return ergebnis


def alle_antworten(server: LaufenderServer) -> dict[str, str]:
    ergebnisse = aufrufen(server, ALLE_WERKZEUGE)
    antworten = {}
    for (name, argumente), ergebnis in zip(ALLE_WERKZEUGE, ergebnisse, strict=True):
        assert not ergebnis.is_error, (name, antworttext(ergebnis))
        antworten[f"{name}({json.dumps(argumente)})"] = antworttext(ergebnis)
    return antworten


def test_keine_personendaten_in_den_antworten(betrieb: Any, server: LaufenderServer) -> None:
    """Abnahme Schritt 11: Claude bekommt Zähler, Summen, Fristen und Einheit-IDs, sonst
    nichts. Der Suchtest ruft jedes Werkzeug gegen den Testhof auf und findet in keiner
    Antwort einen Namen, eine IBAN, eine Anschrift oder einen Verwendungszweck."""
    antworten = alle_antworten(server)

    # Die Antworten sind nicht leer: Es gibt etwas, worin ein Name stehen könnte.
    daten = {k: json.loads(v.splitlines()[-1]) for k, v in antworten.items()}
    assert daten["ueberblick({})"]["arbeitsposten"]["offen"] > 0
    assert daten["ueberblick({})"]["laeufe"]["letzte"]
    rueck = daten["rueckstaende({})"]
    assert rueck["gesamt"]["vertraege"] > 0 and rueck["einheiten"] and rueck["je_objekt"]
    assert daten["datenstand({})"]["bereiche"]
    assert daten["fristen(" + json.dumps(ALLE_WERKZEUGE[-1][1]) + ")"]["anzahl"] > 0
    assert daten['kassenbuch_monat({"monat": "2026-10"})']["je_objekt"]

    assert suchen(antworten) == []


def test_der_suchtest_schlaegt_bei_einem_namen_an(betrieb: Any, server: LaufenderServer) -> None:
    """Abnahme Schritt 11: Gegenprobe. Steht ein Name aus dem Testhof absichtlich in einem
    Feld, das eine Sicht liest (hier die Art eines Arbeitspostens), findet der Suchtest ihn
    in der Antwort von ``ueberblick``."""
    name = sorted(namen_aus_dem_testhof())[0]
    kern.arbeitsposten_anlegen(f"Rückruf {name}")

    (ergebnis,) = aufrufen(server, [("ueberblick", {})])
    funde = suchen({"ueberblick": antworttext(ergebnis)})

    assert f"ueberblick: {name}" in funde


def test_der_suchtest_findet_ganze_werte(betrieb: Any) -> None:
    """Gegenprobe zur Wertesuche: Ein Verwendungszweck oder eine IBAN aus dem Testhof in
    einer Antwort wird gefunden, eine Zahl als Teil eines Betrags nicht."""
    from apps.bank.models import Bankumsatz

    umsatz = Bankumsatz.objects.exclude(verwendungszweck="").exclude(gegen_iban="").first()
    assert umsatz is not None
    werte = personenwerte()
    text = json.dumps({"x": umsatz.verwendungszweck, "y": umsatz.gegen_iban.replace(" ", "")})
    gefunden = treffer(text, werte)
    assert " ".join(umsatz.verwendungszweck.split()) in gefunden
    assert treffer('{"summe": "12345.00"}', {"12345"}) == []
