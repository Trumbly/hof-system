"""Konfiguration der Stammdaten (konfiguration.toml, import_spalten.toml)."""

from __future__ import annotations

from apps.stammdaten import datenstand, importe, konfiguration
from apps.stammdaten.models import Checkliste, Einheit, Vertrag


def test_kuendigungsfristen_je_vertragsart_stehen_in_der_konfiguration() -> None:
    """Schritt 4, Aufgabe 5: Kündigungsfristen je Vertragsart stehen als Konfiguration in
    einer Datei (konfiguration.toml), für jede Vertragsart des Modells mit gültiger Regel."""
    k = konfiguration.laden()

    assert set(k.vertragsarten) == set(Vertrag.Art.values)
    for art in k.vertragsarten.values():
        assert art.kuendigungsregel in k.kuendigungsregeln
        assert art.vorlage.startswith("vertrag_")
    assert k.kuendigungsregeln["monat_580a"].karenz_werktage == 3
    assert k.kuendigungsregeln["quartal_580a"].periode == "quartal"
    assert konfiguration.DATEI.name == "konfiguration.toml"


def test_pflichtfelder_je_bereich_wie_im_bauplan() -> None:
    """Schritt 4, Aufgabe 7: Pflichtfelder je Art als Konfiguration, Bereiche und Regeln wie in
    der Tabelle des Bauplans (ohne Zähler, die in Schritt 8 dazukommen)."""
    bereiche = konfiguration.laden().datenstand

    assert list(bereiche) == ["einheit", "partei", "vertrag", "kaution", "schluessel"]
    assert set(bereiche["einheit"].pflicht) == {"typ", "flaeche", "objekt", "status", "zielmiete"}
    assert set(bereiche["partei"].pflicht) == {"anschrift", "kontaktweg"}
    assert set(bereiche["vertrag"].pflicht) == {
        "beginn",
        "miete",
        "zahlweise",
        "kuendigungsfrist",
        "kaution",
        "scan",
    }
    assert set(bereiche["kaution"].pflicht) == {"betrag", "eingang", "konto"}
    assert set(bereiche["schluessel"].pflicht) == {"anzahl", "inhaber"}
    # Wohnraum: nur NW-ID mit Objekt
    assert bereiche["einheit"].pflicht_fuer(Einheit.Typ.WOHNUNG) == ("objekt",)
    assert datenstand.konfiguration_pruefen() == []


def test_checklisten_je_phase() -> None:
    """Schritt 4, Aufgabe 1: Checklisten je Phase der SOP stehen in der Konfiguration."""
    for phase in Checkliste.Phase.values:
        vorlage = konfiguration.checkliste(phase)
        assert vorlage.titel and vorlage.punkte


def test_spaltenzuordnung_an_einer_stelle() -> None:
    """Schritt 4, Aufgabe 8: Die Spaltenzuordnung der Importe steht konfigurierbar an einer
    Stelle (import_spalten.toml)."""
    for name in ("notion_einheiten", "hellohousing_mieter", "hellohousing_mietverlauf"):
        abschnitt = importe.zuordnung(name)
        assert abschnitt["spalten"]
    assert set(importe.zuordnung("notion_einheiten")["typen"].values()) <= set(Einheit.Typ.values)
