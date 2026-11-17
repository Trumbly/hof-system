"""Kündigung (H2.8) und die Andockstellen für Schreiben (Schritt 3c) und Fristen (Schritt 9)."""

from __future__ import annotations

import datetime
from decimal import Decimal

import pytest
from django.contrib.contenttypes.models import ContentType

from apps.kern.models import Arbeitsposten
from apps.stammdaten import fristen, schreiben, services
from apps.stammdaten.models import Checkliste, Kuendigung, Partei, Vertrag

pytestmark = pytest.mark.django_db
D = datetime.date


def _posten(art: str, bezug: object) -> list[Arbeitsposten]:
    return list(
        Arbeitsposten.objects.filter(
            art=art,
            bezug_typ=ContentType.objects.get_for_model(type(bezug)),  # type: ignore[arg-type]
            bezug_id=bezug.pk,  # type: ignore[attr-defined]
        )
    )


def test_kuendigung_erzeugt_fristende_bestaetigung_und_rueckgabetermin(
    aktiver_vertrag: Vertrag,
) -> None:
    """Schritt 4, Aufgabe 5: Eingangsdatum erfassen, Fristende nach Vertragsart rechnen,
    Bestätigung aus Vorlage anfordern, Rückgabetermin als Frist melden."""
    kuendigung = services.kuendigung_erfassen(aktiver_vertrag, D(2026, 11, 3))

    assert kuendigung.fristende == D(2027, 1, 31)
    assert kuendigung.rueckgabetermin == D(2027, 1, 31)
    assert kuendigung.kuendigungsregel == "monat_580a"
    aktiver_vertrag.refresh_from_db()
    assert aktiver_vertrag.status == Vertrag.Status.GEKUENDIGT
    assert aktiver_vertrag.ende == D(2027, 1, 31)  # damit endet ab Schritt 5 das Soll

    (bestaetigung,) = _posten(schreiben.ART, kuendigung)
    assert bestaetigung.hinweis == "Vorlagenart kuendigungsbestaetigung, Versandweg mail"
    (frist,) = _posten(fristen.ART, aktiver_vertrag)
    assert frist.faellig_am == D(2027, 1, 31)
    assert frist.hinweis == f"Rückgabetermin: Vertrag {aktiver_vertrag.nummer}"
    assert aktiver_vertrag.checklisten.filter(phase=Checkliste.Phase.RUECKGABE).exists()


def test_spaeteres_wunschende_und_eigener_rueckgabetermin(aktiver_vertrag: Vertrag) -> None:
    kuendigung = services.kuendigung_erfassen(
        aktiver_vertrag,
        D(2026, 11, 3),
        zum_wunsch=D(2027, 3, 31),
        rueckgabetermin=D(2027, 3, 29),
    )
    assert (kuendigung.fristende, kuendigung.rueckgabetermin) == (D(2027, 3, 31), D(2027, 3, 29))


def test_frueheres_wunschende_gilt_nicht(aktiver_vertrag: Vertrag) -> None:
    kuendigung = services.kuendigung_erfassen(
        aktiver_vertrag, D(2026, 11, 3), zum_wunsch=D(2026, 11, 30)
    )
    assert kuendigung.fristende == D(2027, 1, 31)


def test_kuendigungsregel_am_vertrag_geht_vor(aktiver_vertrag: Vertrag) -> None:
    services.vertrag_aendern(aktiver_vertrag, kuendigungsregel="ein_monat_monatsende")
    kuendigung = services.kuendigung_erfassen(aktiver_vertrag, D(2026, 11, 20))
    assert kuendigung.fristende == D(2026, 12, 31)


def test_nur_aktive_vertraege_kuendbar(entwurf: Vertrag) -> None:
    with pytest.raises(services.StammdatenFehler, match=entwurf.nummer):
        services.kuendigung_erfassen(entwurf, D(2026, 11, 3))
    assert not Kuendigung.objects.exists()


def test_kontext_vertrag_fuer_die_vorlage(aktiver_vertrag: Vertrag, mieter: Partei) -> None:
    """Schritt 4, Aufgabe 3 (H2.3): Der Kontext füllt die Platzhalter des Vertragsdokuments aus
    dem Vertragsstamm: Vertrag, Mieter, Einheiten, Kondition mit Staffel, Kaution."""
    services.kondition_ab(aktiver_vertrag, D(2027, 2, 1), miete=Decimal("95.00"))
    kontext = schreiben.kontext_vertrag(aktiver_vertrag)

    assert kontext["vertrag"]["nummer"] == aktiver_vertrag.nummer
    assert kontext["vertrag"]["art_titel"] == "Garage"
    assert kontext["vertrag"]["beginn"] == D(2026, 2, 1)
    assert "dritten Werktag" in kontext["vertrag"]["kuendigungsfrist"]
    assert kontext["mieter"][0]["name"] == mieter.anzeigename
    assert kontext["mieter"][0]["ort"] == "Beispielstadt"
    assert kontext["einheiten"][0]["nw_id"] == "NW-G-0001"
    assert kontext["kondition"]["miete"] == Decimal("85.00")
    assert kontext["kondition"]["brutto"] == Decimal("85.00")
    assert [s["miete"] for s in kontext["staffel"]] == [Decimal("85.00"), Decimal("95.00")]
    assert kontext["kaution"]["betrag"] == Decimal("180.00")
    assert kontext["mandant"]["bezeichnung"] == aktiver_vertrag.mandant.bezeichnung


def test_vertragsdokument_andockstelle(entwurf: Vertrag, mieter: Partei) -> None:
    """Schritt 4, Aufgabe 3: Das Vertragsdokument wird über die Andockstelle angefordert:
    vorerst ein Arbeitsposten „Schreiben erstellen“ mit Vorlagenart und Bezug, ohne Namen."""
    posten = services.vertragsdokument_anfordern(entwurf)

    assert posten.art == "Schreiben erstellen"
    assert posten.bezug == entwurf
    assert posten.hinweis == "Vorlagenart vertrag_garage, Versandweg vor_ort"
    assert mieter.nachname not in posten.hinweis
    services.vertrag_aendern(entwurf, vertragsart=Vertrag.Art.H)
    assert "vertrag_halle" in services.vertragsdokument_anfordern(entwurf).hinweis


def test_andockstelle_ruft_eine_stelle(monkeypatch: pytest.MonkeyPatch, entwurf: Vertrag) -> None:
    """Alle Schreiben laufen über ``schreiben._anfordern`` – die eine Stelle, die Schritt 3c auf
    ``dokumente.services.schreiben_erzeugen`` umstellt."""
    aufrufe: list[tuple[str, object, dict[str, object], str]] = []
    monkeypatch.setattr(schreiben, "_anfordern", lambda v, b, k, w: aufrufe.append((v, b, k, w)))
    services.vertragsdokument_anfordern(entwurf)

    ((vorlagenart, bezug, kontext, versandweg),) = aufrufe
    assert (vorlagenart, bezug, versandweg) == ("vertrag_garage", entwurf, "vor_ort")
    assert kontext == schreiben.kontext_vertrag(entwurf)


def test_kontext_kuendigung(aktiver_vertrag: Vertrag) -> None:
    kuendigung = services.kuendigung_erfassen(aktiver_vertrag, D(2026, 11, 3))
    kontext = schreiben.kontext_kuendigung(kuendigung)
    assert kontext["kuendigung"]["zugang_am"] == D(2026, 11, 3)
    assert kontext["kuendigung"]["fristende"] == D(2027, 1, 31)
    assert kontext["kuendigung"]["rueckgabetermin"] == D(2027, 1, 31)
    assert kontext["vertrag"]["nummer"] == aktiver_vertrag.nummer


def test_fristen_andockstelle_kennt_ihre_arten(aktiver_vertrag: Vertrag) -> None:
    with pytest.raises(ValueError, match="Unbekannte Fristart"):
        fristen.fristen_anfordern("gibt_es_nicht", D(2026, 1, 1), aktiver_vertrag)
    posten = fristen.fristen_anfordern("vertragsende", D(2027, 1, 1), aktiver_vertrag)
    assert (posten.art, posten.faellig_am) == ("Frist", D(2027, 1, 1))
