"""Standardablauf 1: Neuvermietung außerhalb von Wohnraum (Prozessliste), mit dem Testhof.

Ergebnis laut Prozessliste: ein aktiver Vertrag mit laufender Sollstellung. Gebaut ist bis
Schritt 4 alles außer PDF (Schritt 3c), Sollstellung (Schritt 5), Bankerkennung der Kaution
(Schritt 6), Fristen-App (Schritt 9) und Meldung an die Website (Schritt 10). Diese Teile
stehen unten als ``xfail`` mit Grund.
"""

from __future__ import annotations

import datetime
import importlib

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse

from apps.stammdaten import schreiben, services
from apps.stammdaten.models import Einheit, Partei, Schluessel, Uebergabe, Vertrag
from testhof.aufbau import STICHTAG, Testhof
from tests.ablaeufe.hilfen import posten, schritt
from tests.stammdaten.conftest import foto

D = datetime.date
BEGINN = D(2026, 12, 1)


def _freie_garage(testhof: Testhof) -> Einheit:
    frei = [e for e in testhof.stammdaten.frei() if e.typ == Einheit.Typ.GARAGE]
    assert frei, "Der Testhof hat keine freie Garage"
    return frei[0]


def test_standardablauf_1_neuvermietung(testhof: Testhof, angemeldet: Client) -> None:
    """Abnahme Schritt 4 (Standardablauf 1): Aus dem Vertragsstamm wird der Vertrag
    angefordert; die Übergabe am Handy setzt ihn aktiv."""
    # 1. System: Die Einheit ist frei.
    einheit = _freie_garage(testhof)
    assert einheit.status_am(STICHTAG).status == "frei"  # type: ignore[union-attr]
    services.schluessel_anlegen(einheit, anzahl=2, inhaber=Schluessel.Inhaber.HOF)

    # 2.–3. Interessent und Prüfung laufen über die Website (Schritt 10, v2).

    # 4. System: legt Partei und Vertrag an und fordert Vertrag und Anlagen aus der Vorlage an.
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
    (anforderung,) = posten(schreiben.ART, vertrag)
    assert anforderung.hinweis == "Vorlagenart vertrag_garage, Versandweg vor_ort"
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
    assert posten(schreiben.ART, uebergabe, hinweis__contains="uebergabeprotokoll")
    seite = angemeldet.get(url).content.decode()
    assert "aktiv" in seite and "Übergabe am 01.12.2026" in seite


@pytest.mark.xfail(reason="Schritt 3c: Die App dokumente erzeugt das Vertrags-PDF noch nicht.")
def test_standardablauf_1_vertrag_entsteht_als_pdf() -> None:
    """Abnahme Schritt 4: Ein neuer Vertrag entsteht aus dem Vertragsstamm als PDF."""
    dokumente = importlib.import_module("apps.dokumente.services")
    assert hasattr(dokumente, "schreiben_erzeugen")
    assert "dokumente" in schreiben._anfordern.__code__.co_names


@pytest.mark.xfail(reason="Schritt 5: Die Sollstellung startet mit dem aktiven Vertrag.")
def test_standardablauf_1_sollstellung_startet() -> None:
    importlib.import_module("apps.soll.services")


@pytest.mark.xfail(reason="Schritt 6: Der Abgleich erkennt Kaution und erste Miete über die Bank.")
def test_standardablauf_1_kaution_aus_bank() -> None:
    importlib.import_module("apps.abgleich.services")


@pytest.mark.xfail(reason="Schritt 9: Fristen des Vertrags stehen in der App fristen.")
def test_standardablauf_1_fristen_angelegt() -> None:
    importlib.import_module("apps.fristen.services")
    from apps.stammdaten import fristen

    assert "fristen" in fristen.fristen_anfordern.__code__.co_names


@pytest.mark.xfail(reason="Schritt 10: Die Belegung geht an die Website.")
def test_standardablauf_1_belegung_an_website() -> None:
    importlib.import_module("apps.anbindungen.services")
