"""Seiten für Vorlagen und Schreiben (Schritt 3c, Aufgaben 1, 6 und 8)."""

from __future__ import annotations

import pytest
from django.test import Client
from django.urls import reverse

from apps.dokumente import services
from apps.dokumente.models import Schreiben, Vorlage
from apps.dokumente.vorlagenarten import Versandweg
from apps.kern.models import Objekt
from tests.dokumente.conftest import MITTEILUNG, kontext

pytestmark = pytest.mark.django_db


def test_seiten_nur_mit_anmeldung(client: Client) -> None:
    """Schritt 3c: Ohne Anmeldung keine Vorlagen, keine Schreiben, keine PDFs."""
    for name, argumente in [
        ("dokumente:vorlagen", []),
        ("dokumente:schreiben_liste", []),
        ("dokumente:schreiben", [1]),
        ("dokumente:schreiben_pdf", [1]),
        ("dokumente:serie_neu", []),
    ]:
        antwort = client.get(reverse(name, args=argumente))
        assert antwort.status_code == 302, name
        assert antwort["Location"].startswith(reverse("account_login"))


def test_vorlage_anlegen_bearbeiten_freigeben_ueber_die_seiten(angemeldet: Client) -> None:
    """Schritt 3c, Aufgabe 1 (H8.1): Vorlagen pflegen und freigeben über die Oberfläche."""
    antwort = angemeldet.post(
        reverse("dokumente:vorlage_neu"),
        {"name": "Hinweis", "art": "mitteilung", "betreff": "", "text": "{{ partei.name }}"},
    )
    assert "für diese Vorlagenart nicht gibt: „partei.name“" in antwort.content.decode()
    assert not Vorlage.objects.exists()

    antwort = angemeldet.post(
        reverse("dokumente:vorlage_neu"),
        {"name": "Hinweis", "art": "mitteilung", "betreff": "Hinweis", "text": MITTEILUNG},
    )
    vorlage = Vorlage.objects.get()
    assert antwort["Location"] == reverse("dokumente:vorlage", args=[vorlage.pk])

    angemeldet.post(
        reverse("dokumente:vorlage_bearbeiten", args=[vorlage.pk]),
        {"art": "mitteilung", "betreff": "Neu", "text": "<p>{{ empfaenger.anrede }}</p>"},
    )
    vorlage.refresh_from_db()
    assert vorlage.betreff == "Neu"

    seite = angemeldet.get(reverse("dokumente:vorlage", args=[vorlage.pk])).content.decode()
    assert "Freigeben" in seite
    assert "{{ empfaenger.name }}" in seite  # Liste der erlaubten Platzhalter

    angemeldet.post(
        reverse("dokumente:vorlage_freigeben", args=[vorlage.pk]), {"freigegeben_von": "Max"}
    )
    vorlage.refresh_from_db()
    assert vorlage.status == Vorlage.Status.FREIGEGEBEN
    assert vorlage.freigegeben_von is not None
    assert vorlage.freigabe_durch == "Max"

    angemeldet.post(reverse("dokumente:vorlage_neue_version", args=[vorlage.pk]))
    assert Vorlage.objects.filter(name="Hinweis", version=2, status="entwurf").exists()
    assert "Hinweis" in angemeldet.get(reverse("dokumente:vorlagen")).content.decode()


def test_schreiben_seite_zeigt_pdf_und_senden(
    angemeldet: Client, mitteilung: Vorlage, objekt: Objekt
) -> None:
    """Schritt 3c, Aufgabe 6: Die Seite eines Schreibens zeigt das fertige PDF und darunter
    „Senden“; das PDF kommt nur mit Anmeldung und darf im Rahmen derselben Seite stehen."""
    schreiben = services.schreiben_erzeugen(
        mitteilung, objekt, kontext(objekt), Versandweg.EINWURF_EINSCHREIBEN
    )

    liste = angemeldet.get(reverse("dokumente:schreiben_liste")).content.decode()
    # Review 2, B13: lesbarer Titel statt „Schreiben #5 · mitteilung“
    assert reverse("dokumente:schreiben", args=[schreiben.pk]) in liste
    assert f"Mitteilung · Objekt {objekt.kuerzel}" in liste
    seite = angemeldet.get(reverse("dokumente:schreiben", args=[schreiben.pk])).content.decode()
    assert '<iframe class="pdf-rahmen"' in seite
    assert "Senden (Einwurf-Einschreiben)" in seite

    pdf = angemeldet.get(reverse("dokumente:schreiben_pdf", args=[schreiben.pk]))
    assert pdf["Content-Type"] == "application/pdf"
    assert pdf["X-Frame-Options"] == "SAMEORIGIN"
    assert b"".join(pdf.streaming_content).startswith(b"%PDF")  # type: ignore[attr-defined]


def test_nach_dem_senden_versandstand_und_abschluss(
    angemeldet: Client, kuendigung: Vorlage, objekt: Objekt
) -> None:
    """Schritt 3c, Aufgaben 5 und 7: Nach dem Senden zeigt die Seite den Abruf des
    Versandstands und das Formular für den Zustellnachweis."""
    schreiben = services.schreiben_erzeugen(kuendigung, objekt, kontext(objekt))
    angemeldet.post(
        reverse("dokumente:schreiben_senden", args=[schreiben.pk]),
        {"pruefsumme": schreiben.pdf_sha256, "anwalt_gesehen": "1"},
    )
    seite = angemeldet.get(reverse("dokumente:schreiben", args=[schreiben.pk])).content.decode()
    assert "Versandstand bei LetterXpress abrufen" in seite
    assert "Ohne Zustellnachweis" in seite
    assert 'name="pruefsumme"' not in seite

    angemeldet.post(reverse("dokumente:schreiben_versandstand", args=[schreiben.pk]))
    schreiben.refresh_from_db()
    assert schreiben.status == Schreiben.Status.GESENDET


def test_vorschau_einer_vorlage_als_pdf(angemeldet: Client, mitteilung: Vorlage) -> None:
    """Schritt 3c, Aufgabe 6: Vorschau einer Vorlage mit Beispielwerten als PDF."""
    antwort = angemeldet.get(reverse("dokumente:vorlage_vorschau", args=[mitteilung.pk]))
    assert antwort.status_code == 200
    assert antwort["Content-Type"] == "application/pdf"
    assert antwort.content.startswith(b"%PDF")
    assert not Schreiben.objects.exists()


def test_navigation_hat_schreiben(angemeldet: Client) -> None:
    """Schritt 3c: Die Hauptnavigation führt zu den Schreiben."""
    html = angemeldet.get(reverse("kern:startseite")).content.decode()
    assert reverse("dokumente:schreiben_liste") in html
