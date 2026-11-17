"""Standardablauf 1: Neuvermietung außerhalb von Wohnraum (Prozessliste), mit dem Testhof.

Ergebnis laut Prozessliste: ein aktiver Vertrag mit laufender Sollstellung. Gebaut ist
alles außer Bankerkennung der Kaution (Schritt 6) und Meldung an die Website (Schritt 10).
Diese Teile stehen unten als ``xfail`` mit Grund.
"""

from __future__ import annotations

import datetime
import importlib
from decimal import Decimal

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse

from apps.dokumente.models import Schreiben
from apps.fristen import services as fristen
from apps.soll import services as soll
from apps.soll.models import Sollposten
from apps.stammdaten import schreiben, services
from apps.stammdaten.models import Einheit, Partei, Schluessel, Uebergabe, Vertrag
from testhof.aufbau import STICHTAG, Testhof
from tests.ablaeufe.hilfen import posten, schritt
from tests.stammdaten.conftest import braucht_weasyprint, foto

D = datetime.date
BEGINN = D(2026, 12, 1)


def _freie_garage(testhof: Testhof) -> Einheit:
    frei = [e for e in testhof.stammdaten.frei() if e.typ == Einheit.Typ.GARAGE]
    assert frei, "Der Testhof hat keine freie Garage"
    return frei[0]


@braucht_weasyprint
def test_standardablauf_1_neuvermietung(testhof: Testhof, angemeldet: Client) -> None:
    """Abnahme Schritt 4 (Standardablauf 1): Ein neuer Vertrag entsteht aus dem
    Vertragsstamm als PDF; die Übergabe am Handy setzt ihn aktiv."""
    # 1. System: Die Einheit ist frei.
    einheit = _freie_garage(testhof)
    assert einheit.status_am(STICHTAG).status == "frei"  # type: ignore[union-attr]
    services.schluessel_anlegen(einheit, anzahl=2, inhaber=Schluessel.Inhaber.HOF)

    # 2.–3. Interessent und Prüfung laufen über die Website (Schritt 10, v2).

    # 4. System: legt Partei und Vertrag an und füllt den Vertrag aus der Vorlage.
    antwort = angemeldet.post(
        reverse("stammdaten:partei_neu"),
        {
            "art": "person",
            "vorname": "Nora",
            "nachname": "Testmieterin",
            "strasse": "Erfundener Weg 3",
            "plz": "12345",
            "ort": "Beispielstadt",
        },
    )
    partei = Partei.objects.get(nachname="Testmieterin")
    assert antwort["Location"] == reverse("stammdaten:partei", args=[partei.pk])
    antwort = angemeldet.post(
        reverse("stammdaten:vertrag_neu"),
        {
            "einheit": einheit.pk,
            "mieter": partei.pk,
            "beginn": BEGINN.isoformat(),
            "miete": "89.00",
            "zahlweise": "monatlich",
            "kaution": "178.00",
        },
    )
    vertrag = Vertrag.objects.get(vertragseinheiten__einheit=einheit, status="entwurf")
    assert antwort["Location"] == reverse("stammdaten:vertrag", args=[vertrag.pk])
    # Vertrag als PDF aus der freigegebenen Vorlage des Testhofs, bereit zum Drucken.
    dokument = Schreiben.objects.get(art="vertrag", bezug_id=vertrag.pk)
    assert dokument.bezug == vertrag
    assert (dokument.status, dokument.versandweg) == ("bereit", "selbst")
    assert dokument.pdf.read().startswith(b"%PDF")
    assert "Nora Testmieterin" in dokument.inhalt_html
    assert not posten(schreiben.ART, vertrag)
    kontext = schreiben.kontext_vertrag(vertrag)
    assert kontext["mieter"][0]["name"] == "Nora Testmieterin"
    assert kontext["einheiten"][0]["nw_id"] == einheit.nw_id
    assert kontext["kondition"]["miete"] == 89
    assert kontext["kaution"]["betrag"] == 178

    # 5. Vor Ort: Beide unterschreiben; der Scan hängt am Vertrag.
    url = reverse("stammdaten:vertrag", args=[vertrag.pk])
    scan = SimpleUploadedFile("vertrag.pdf", b"%PDF-1.4 erfunden", content_type="application/pdf")
    antwort = angemeldet.post(
        url,
        {
            "aktion": "unterschrift",
            "unterschrift-unterschrieben_am": "2026-11-28",
            "unterschrift-scan": scan,
        },
    )
    assert antwort.status_code == 302

    # 6. Kaution geht ein (ab Schritt 6 erkennt das System sie über die Bank).
    antwort = angemeldet.post(
        url,
        {
            "aktion": "kaution",
            "kaution-betrag": "178.00",
            "kaution-eingang_am": "2026-11-29",
            "kaution-konto": "Vivid Kautionen",
        },
    )
    assert antwort.status_code == 302

    # 7. Vor Ort: Übergabe am Handy mit Protokoll, Fotos und Schlüssel gegen Quittung.
    antwort = angemeldet.post(
        reverse("stammdaten:uebergabe_starten", args=[vertrag.pk, "uebergabe"])
    )
    uebergabe = Uebergabe.objects.get(vertrag=vertrag)
    schluessel = services.schluessel_der_uebergabe(uebergabe)
    assert schluessel
    schritt(angemeldet, uebergabe.pk, 1, {"datum": BEGINN.isoformat()})
    schritt(angemeldet, uebergabe.pk, 2, {"nummer_0": "Z-G-1", "stand_0": "100,5"})
    schritt(angemeldet, uebergabe.pk, 3, {"fotos": [foto("innen.png"), foto("tor.png")]})
    schritt(angemeldet, uebergabe.pk, 4, {f"schluessel_{s.pk}": str(s.anzahl) for s in schluessel})
    schritt(angemeldet, uebergabe.pk, 5, {"zustand": "neuwertig", "unterschrift": foto("q.png")})
    schritt(angemeldet, uebergabe.pk, 6, {})

    # 8. System: Vertrag aktiv, Einheit vermietet, Schlüssel beim Mieter, Protokoll angefordert.
    vertrag.refresh_from_db()
    uebergabe.refresh_from_db()
    assert vertrag.status == Vertrag.Status.AKTIV
    assert vertrag.unterschriebener_scan
    assert einheit.status_am(BEGINN).status == "vermietet"  # type: ignore[union-attr]
    assert {s.partei for s in einheit.schluessel.all()} == {partei}
    assert uebergabe.zaehlerstaende == [{"nummer": "Z-G-1", "stand": "100.5"}]
    assert uebergabe.fotos.count() == 2
    assert Schreiben.objects.filter(art="uebergabeprotokoll", bezug_id=uebergabe.pk).exists()
    # Fristen: Der unbefristete Vertrag hat keine; Vertragsende entsteht mit der Kündigung.
    assert not fristen.fristen_zu(vertrag).exists()
    seite = angemeldet.get(url).content.decode()
    assert "aktiv" in seite and "Übergabe am 01.12.2026" in seite
    # Die Sollstellung startet mit dem aktiven Vertrag (Schritt 5).
    assert _erste_miete(vertrag) == (BEGINN, D(2026, 12, 31), Decimal("89.00"), D(2026, 12, 3))


def _erste_miete(vertrag: Vertrag) -> tuple[datetime.date, datetime.date, Decimal, datetime.date]:
    posten = Sollposten.objects.filter(vertrag=vertrag, art="miete").order_by("von").first()
    assert posten is not None, "Kein Sollposten"
    return (posten.von, posten.bis, posten.betrag, posten.faellig_am)


def test_standardablauf_1_sollstellung_startet(
    testhof: Testhof, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Abnahme Schritt 5 (Standardablauf 1, Schritt 8): Mit der Übergabe wird der Vertrag
    aktiv, und die Sollstellung beginnt mit der ersten Miete (fällig am 3. Werktag)."""
    monkeypatch.setattr(soll, "heute", lambda: D(2026, 11, 28))
    einheit = _freie_garage(testhof)
    mieter = testhof.stammdaten.parteien[-1]
    with schreiben.ohne_schreiben("Test ohne PDF"):
        vertrag = services.vertrag_anlegen(
            einheiten=[einheit],
            mieter=[mieter],
            beginn=BEGINN,
            miete=Decimal("89.00"),
            kaution=Decimal("178.00"),
        )
        assert not Sollposten.objects.filter(vertrag=vertrag).exists()  # Entwurf: kein Soll
        services.vertrag_unterschrieben(vertrag, D(2026, 11, 28))
        services.kaution_eingang_erfassen(vertrag.kaution, D(2026, 11, 29), "Vivid Kautionen")
        uebergabe = services.uebergabe_beginnen(vertrag, "uebergabe", BEGINN)
        services.uebergabe_speichern(uebergabe, unterschrift=foto("q.png"))
        services.uebergabe_abschliessen(uebergabe)

    vertrag.refresh_from_db()
    assert vertrag.status == Vertrag.Status.AKTIV
    assert _erste_miete(vertrag) == (BEGINN, D(2026, 12, 31), Decimal("89.00"), D(2026, 12, 3))
    assert Sollposten.objects.filter(vertrag=vertrag).count() == 1


@pytest.mark.xfail(reason="Schritt 6: Der Abgleich erkennt Kaution und erste Miete über die Bank.")
def test_standardablauf_1_kaution_aus_bank() -> None:
    importlib.import_module("apps.abgleich.services")


@pytest.mark.xfail(reason="Schritt 10: Die Belegung geht an die Website.")
def test_standardablauf_1_belegung_an_website() -> None:
    importlib.import_module("apps.anbindungen.services")
