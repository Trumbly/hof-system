"""Standardablauf 4: Kündigung und Rückgabe (Prozessliste), mit dem Testhof.

Ergebnis laut Prozessliste: Die Einheit ist frei, die Kaution abgerechnet, das Soll endet
zum Fristende (Schritt 5). Offen bis zu seinem Schritt: offene Posten in der
Kautionsabrechnung (6). Die Meldung an die Website (10) prüft
``test_standardablauf_4_website_meldung``.
"""

from __future__ import annotations

import datetime
from decimal import Decimal

import pytest
from django.db import models
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.anbindungen import services as anbindungen
from apps.dokumente.models import Schreiben
from apps.fristen import services as fristen
from apps.fristen.models import Frist
from apps.soll import services as soll
from apps.soll.models import Sollposten
from apps.stammdaten import schreiben, services
from apps.stammdaten.models import Checkliste, Einheit, Kaution, Kuendigung, Uebergabe, Vertrag
from testhof.aufbau import Testhof
from tests.ablaeufe.hilfen import posten, schritt
from tests.stammdaten.conftest import braucht_weasyprint, foto

D = datetime.date
ZUGANG = D(2026, 11, 3)
FRISTENDE = D(2027, 1, 31)


def _garagenvertrag(testhof: Testhof) -> Vertrag:
    for vertrag in testhof.stammdaten.aktive_vertraege():
        kaution = Kaution.objects.get(vertrag=vertrag)
        if (
            vertrag.vertragsart == Vertrag.Art.G
            and vertrag.kuendigungsregel == "monat_580a"
            and vertrag.ende is None
            and kaution.betrag
            and kaution.eingang_am
            and vertrag.einheiten.get().schluessel.filter(inhaber="partei").exists()
            and (mieter := vertrag.parteien_mit_rolle("mieter")[0]).hat_anschrift
            and mieter.kontaktwege.filter(art="email").exists()
        ):
            return vertrag
    raise AssertionError("Kein passender Garagenvertrag im Testhof")


@braucht_weasyprint
def test_standardablauf_4_kuendigung_und_rueckgabe(testhof: Testhof, angemeldet: Client) -> None:
    """Abnahme Schritt 4 (Standardablauf 4): Die Kündigung erzeugt Fristende, Bestätigung und
    Rückgabetermin; die Rückgabe gibt die Einheit frei und stößt die Kautionsabrechnung an."""
    vertrag = _garagenvertrag(testhof)
    einheit: Einheit = vertrag.einheiten.get()
    url = reverse("stammdaten:vertrag", args=[vertrag.pk])

    # 1. Du: erfasst die Kündigung mit Zugangsdatum am Vertrag.
    antwort = angemeldet.post(
        url,
        {
            "aktion": "kuendigung",
            "kuendigung-zugang_am": ZUGANG.isoformat(),
            "kuendigung-von": "mieter",
        },
    )
    assert antwort.status_code == 302

    # 2. System: Fristende, Bestätigung mit Rückgabetermin, Vertragsende (beendet das Soll).
    kuendigung = Kuendigung.objects.get(vertrag=vertrag)
    assert (kuendigung.fristende, kuendigung.rueckgabetermin) == (FRISTENDE, FRISTENDE)
    vertrag.refresh_from_db()
    assert (vertrag.status, vertrag.ende) == (Vertrag.Status.GEKUENDIGT, FRISTENDE)
    bestaetigung = Schreiben.objects.get(art="kuendigungsbestaetigung", bezug_id=kuendigung.pk)
    assert bestaetigung.bezug == kuendigung
    assert (bestaetigung.status, bestaetigung.versandweg) == ("bereit", "mail")
    assert bestaetigung.pdf.read().startswith(b"%PDF")
    assert "31.01.2027" in bestaetigung.inhalt_html  # Fristende und Rückgabetermin
    (frist,) = fristen.fristen_zu(vertrag, nur_offen=True)
    assert (frist.art, frist.quelle, frist.datum) == (
        Frist.Art.VERTRAGSENDE,
        Frist.Quelle.VERTRAG,
        FRISTENDE,
    )
    assert frist.hinweis.startswith("Rückgabetermin")
    (fristende,) = fristen.fristen_zu(kuendigung, nur_offen=True)
    assert (fristende.art, fristende.datum) == (Frist.Art.KUENDIGUNG, FRISTENDE)
    # Das Soll endet zum Fristende (Schritt 5).
    assert not _soll(vertrag).filter(bis__gt=FRISTENDE).exists()

    # 3. Vor Ort: Rückgabe mit Protokoll, Fotos, Zählerstand und allen Schlüsseln.
    angemeldet.post(reverse("stammdaten:uebergabe_starten", args=[vertrag.pk, "rueckgabe"]))
    rueckgabe = Uebergabe.objects.get(vertrag=vertrag, art="rueckgabe")
    schluessel = services.schluessel_der_uebergabe(rueckgabe)
    assert schluessel
    schritt(angemeldet, rueckgabe.pk, 1, {"datum": FRISTENDE.isoformat()})
    schritt(angemeldet, rueckgabe.pk, 2, {"nummer_0": "Z-R-1", "stand_0": "2210"})
    schritt(angemeldet, rueckgabe.pk, 3, {"fotos": [foto("rueckgabe.png")]})
    schritt(angemeldet, rueckgabe.pk, 4, {f"schluessel_{s.pk}": str(s.anzahl) for s in schluessel})
    schritt(angemeldet, rueckgabe.pk, 5, {"zustand": "besenrein", "zurueckgelassen": "nichts"})
    schritt(angemeldet, rueckgabe.pk, 6, {})

    # 4. System: Einheit frei, Frist für die Kautionsabrechnung.
    vertrag.refresh_from_db()
    kaution = Kaution.objects.get(vertrag=vertrag)
    assert vertrag.status == Vertrag.Status.BEENDET
    assert einheit.status_am(FRISTENDE).status == "frei"  # type: ignore[union-attr]
    assert not einheit.schluessel.filter(inhaber="partei").exists()
    assert kaution.abrechnung_faellig_am == D(2027, 7, 31)
    (abrechnen,) = posten(services.ART_KAUTION_ABRECHNEN, kaution)
    assert abrechnen.faellig_am == D(2027, 7, 31)
    (kautionsfrist,) = fristen.fristen_zu(kaution, nur_offen=True)
    assert (kautionsfrist.art, kautionsfrist.quelle, kautionsfrist.datum) == (
        Frist.Art.KAUTIONSABRECHNUNG,
        Frist.Quelle.KAUTION,
        D(2027, 7, 31),
    )
    assert not fristen.fristen_zu(vertrag, nur_offen=True).exists()  # Rückgabe erfolgt
    assert Schreiben.objects.filter(art="rueckgabeprotokoll", bezug_id=rueckgabe.pk).exists()

    # 5. Du: rechnest die Kaution über die Vorlage ab.
    antwort = angemeldet.post(
        url,
        {
            "aktion": "kaution_abrechnen",
            "kaution_abrechnen-datum": "2027-02-15",
            "kaution_abrechnen-einbehalt": "20.00",
        },
    )
    assert antwort.status_code == 302
    kaution.refresh_from_db()
    assert kaution.abgerechnet_am == D(2027, 2, 15)
    assert kaution.ausgezahlt == kaution.betrag - Decimal("20.00")  # type: ignore[operator]
    abrechnung = Schreiben.objects.get(art="kautionsabrechnung", bezug_id=kaution.pk)
    assert (abrechnung.status, abrechnung.versandweg) == ("bereit", "brief")
    assert not fristen.fristen_zu(kaution, nur_offen=True).exists()
    abrechnen.refresh_from_db()
    assert abrechnen.status == "erledigt"

    # 6. System: legt die Checkliste für die Neuvermietung an.
    assert einheit.checklisten.filter(phase=Checkliste.Phase.VORBEREITUNG).exists()


def _soll(vertrag: Vertrag) -> models.QuerySet[Sollposten]:
    return Sollposten.objects.filter(vertrag=vertrag, art="miete").exclude(status="storniert")


def test_standardablauf_4_soll_endet(testhof: Testhof, monkeypatch: pytest.MonkeyPatch) -> None:
    """Abnahme Schritt 5 (Standardablauf 4): Die Kündigung beendet das Soll zum Fristende;
    schon gestellte offene Posten danach werden storniert."""
    monkeypatch.setattr(soll, "heute", lambda: ZUGANG)
    vertrag = _garagenvertrag(testhof)
    soll.sollstellung_erzeugen(vertrag, D(2027, 6, 30))
    assert _soll(vertrag).order_by("von").last().von == D(2027, 6, 1)  # type: ignore[union-attr]

    services.kuendigung_erfassen(vertrag, ZUGANG)

    letzter = _soll(vertrag).order_by("von").last()
    assert letzter is not None and (letzter.von, letzter.bis) == (D(2027, 1, 1), FRISTENDE)
    storniert = Sollposten.objects.filter(vertrag=vertrag, status="storniert")
    assert sorted(p.von for p in storniert) == [D(2027, m, 1) for m in range(2, 7)]
    assert soll.sollstellung_erzeugen(vertrag, D(2027, 12, 31)).neu == []


def test_standardablauf_4_website_meldung(testhof: Testhof) -> None:
    """Abnahme Schritt 10 (Standardablauf 4): Nach Kündigung und Rückgabe steht die Einheit
    ohne Handgriff wieder im Katalog der Website, frei ab dem Rückgabetag."""
    vertrag = _garagenvertrag(testhof)
    einheit: Einheit = vertrag.einheiten.get()
    heute = timezone.localdate()
    im_katalog = {e.nw_id for e in anbindungen.katalog(heute)}
    assert einheit.nw_id not in im_katalog

    with schreiben.ohne_schreiben("Ablauf ohne PDF"):
        services.kuendigung_erfassen(vertrag, ZUGANG)
        assert einheit.nw_id not in {e.nw_id for e in anbindungen.katalog(heute)}
        rueckgabe = services.uebergabe_beginnen(vertrag, Uebergabe.Art.RUECKGABE, FRISTENDE)
        services.uebergabe_speichern(
            rueckgabe,
            schluessel=[
                {"schluessel": s.pk, "anzahl": s.anzahl}
                for s in services.schluessel_der_uebergabe(rueckgabe)
            ],
            zustand="besenrein",
        )
        services.uebergabe_abschliessen(rueckgabe)

    (eintrag,) = [e for e in anbindungen.katalog(heute) if e.nw_id == einheit.nw_id]
    assert eintrag.frei_ab == max(FRISTENDE, heute)
    assert eintrag.preis_monat == einheit.zielmiete_am(eintrag.frei_ab).betrag  # type: ignore[union-attr]
    assert {e.nw_id for e in anbindungen.katalog(heute)} == im_katalog | {einheit.nw_id}
