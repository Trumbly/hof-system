"""Kündigung (H2.8), Schreiben über die App dokumente (3c) und Fristen über die App fristen."""

from __future__ import annotations

import datetime
from decimal import Decimal

import pytest
from django.contrib.contenttypes.models import ContentType

from apps.dokumente.models import Schreiben, Vorlage
from apps.fristen import services as fristen
from apps.fristen.models import Frist
from apps.kern.models import Arbeitsposten
from apps.stammdaten import schreiben, services
from apps.stammdaten.models import Checkliste, Kuendigung, Partei, Vertrag
from tests.stammdaten.conftest import braucht_weasyprint

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

    # Ohne freigegebene Vorlage: Arbeitsposten statt Fehler.
    (bestaetigung,) = _posten(schreiben.ART, kuendigung)
    assert bestaetigung.hinweis.startswith("Vorlagenart kuendigungsbestaetigung: Es gibt keine")
    (rueckgabe,) = fristen.fristen_zu(aktiver_vertrag, nur_offen=True)
    assert (rueckgabe.art, rueckgabe.quelle, rueckgabe.datum) == (
        Frist.Art.VERTRAGSENDE,
        Frist.Quelle.VERTRAG,
        D(2027, 1, 31),
    )
    assert rueckgabe.hinweis == f"Rückgabetermin, Vertrag {aktiver_vertrag.nummer}"
    (fristende,) = fristen.fristen_zu(kuendigung)
    assert (fristende.art, fristende.datum) == (Frist.Art.KUENDIGUNG, D(2027, 1, 31))
    assert aktiver_vertrag.checklisten.filter(phase=Checkliste.Phase.RUECKGABE).exists()


@braucht_weasyprint
def test_kuendigungsbestaetigung_als_schreiben(
    aktiver_vertrag: Vertrag, vorlagen: dict[str, Vorlage]
) -> None:
    """Abnahme Schritt 4: Die Kündigung erzeugt die Bestätigung aus der Vorlage als
    Schreiben im Status „bereit“ (per Mail, weil eine Adresse da ist), mit PDF."""
    kuendigung = services.kuendigung_erfassen(aktiver_vertrag, D(2026, 11, 3))

    brief = Schreiben.objects.get(art="kuendigungsbestaetigung")
    assert brief.bezug == kuendigung
    assert (brief.status, brief.versandweg) == (Schreiben.Status.BEREIT, "mail")
    assert brief.pdf and brief.pdf_sha256
    assert "31.01.2027" in brief.inhalt_html
    assert not _posten(schreiben.ART, kuendigung)


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


def test_ohne_vorlage_entsteht_ein_arbeitsposten(entwurf: Vertrag, mieter: Partei) -> None:
    """Fehlt eine freigegebene Vorlage, entsteht ein Arbeitsposten „Schreiben erstellen“ mit
    Vorlagenart und Grund, ohne Namen; kein Fehler."""
    posten = services.vertragsdokument_erzeugen(entwurf)

    assert isinstance(posten, Arbeitsposten)
    assert posten.art == "Schreiben erstellen"
    assert posten.bezug == entwurf
    assert posten.hinweis.startswith("Vorlagenart vertrag: Es gibt keine freigegebene Vorlage")
    assert mieter.nachname not in posten.hinweis


@braucht_weasyprint
def test_neuer_vertrag_entsteht_als_pdf(
    entwurf: Vertrag, mieter: Partei, vorlagen: dict[str, Vorlage]
) -> None:
    """Abnahme Schritt 4: Ein neuer Vertrag entsteht aus dem Vertragsstamm als PDF (Vorlage
    der Art „vertrag“, Versandweg „selbst“: unterschrieben wird vor Ort)."""
    ergebnis = services.vertragsdokument_erzeugen(entwurf)

    assert isinstance(ergebnis, Schreiben)
    assert ergebnis.bezug == entwurf
    assert (ergebnis.art, ergebnis.versandweg, ergebnis.status) == ("vertrag", "selbst", "bereit")
    daten = ergebnis.pdf.read()
    assert daten.startswith(b"%PDF")
    assert entwurf.nummer in ergebnis.inhalt_html
    assert mieter.anzeigename in ergebnis.inhalt_html
    assert "85,00" in ergebnis.inhalt_html
    with pytest.raises(services.StammdatenFehler):
        services.kuendigung_erfassen(entwurf, D(2026, 11, 3))


def test_vorlage_je_vertragsart(monkeypatch: pytest.MonkeyPatch, entwurf: Vertrag) -> None:
    """Die Vorlage heißt je Vertragsart wie in konfiguration.toml; fehlt sie, gilt die
    neueste freigegebene der Art „vertrag“."""
    aufrufe: list[tuple[str, str | None]] = []

    def merken(
        art: str, bezug: object, kontext: object, weg: object = None, *, name: str | None = None
    ) -> None:
        aufrufe.append((art, name))

    monkeypatch.setattr(schreiben, "_erzeugen", merken)
    services.vertragsdokument_erzeugen(entwurf)
    services.vertrag_aendern(entwurf, vertragsart=Vertrag.Art.H)
    services.vertragsdokument_erzeugen(entwurf)
    assert aufrufe == [("vertrag", "Vertrag Garage"), ("vertrag", "Vertrag Halle")]


def test_kontext_nur_einfache_werte(entwurf: Vertrag) -> None:
    from apps.dokumente import platzhalter

    platzhalter.kontext_pruefen(schreiben.kontext_vertrag(entwurf))
    kontext = schreiben.kontext_vertrag(entwurf)
    assert kontext["empfaenger"]["email"] == "beispiel@testhof.invalid"
    assert kontext["empfaenger"]["anschrift"] == "Testweg 1\n12345 Beispielstadt"
    assert kontext["einheit"]["kuerzel"] == "NW-G-0001"


def test_kontext_kuendigung(aktiver_vertrag: Vertrag) -> None:
    kuendigung = services.kuendigung_erfassen(aktiver_vertrag, D(2026, 11, 3))
    kontext = schreiben.kontext_kuendigung(kuendigung)
    assert kontext["kuendigung"]["zugang_am"] == D(2026, 11, 3)
    assert kontext["kuendigung"]["fristende"] == D(2027, 1, 31)
    assert kontext["kuendigung"]["rueckgabetermin"] == D(2027, 1, 31)
    assert kontext["vertrag"]["nummer"] == aktiver_vertrag.nummer


def test_spaeterer_rueckgabetermin_verschiebt_die_frist(aktiver_vertrag: Vertrag) -> None:
    """Eine Frist Vertragsende je Vertrag: Die Kündigung verschiebt die eines befristeten
    Vertrags auf den Rückgabetermin (mit Eintrag in den Verschiebungen)."""
    services.vertrag_aendern(aktiver_vertrag, ende=D(2027, 6, 30))
    fristen.frist_anlegen(
        Frist.Art.VERTRAGSENDE, D(2027, 6, 30), Frist.Quelle.VERTRAG, aktiver_vertrag
    )
    kuendigung = services.kuendigung_erfassen(
        aktiver_vertrag, D(2026, 11, 3), rueckgabetermin=D(2027, 1, 29)
    )
    (frist,) = fristen.fristen_zu(aktiver_vertrag, nur_offen=True)
    assert frist.datum == kuendigung.rueckgabetermin == D(2027, 1, 29)
    assert frist.verschiebungen.count() == 1


def test_posten_der_stammdaten_melden_sich() -> None:
    """Schritt 9a: Posten, die auf Max warten (Schreiben ohne Vorlage, fehlende Schlüssel),
    stehen im Standard der Benachrichtigung; die Kautionsabrechnung meldet sich über ihre
    Frist („Frist fällig“)."""
    from django.conf import settings

    arten = set(settings.HOF_BENACHRICHTIGUNG_ARTEN)
    assert {schreiben.ART, services.ART_SCHLUESSEL_FEHLT, "Frist fällig"} <= arten
