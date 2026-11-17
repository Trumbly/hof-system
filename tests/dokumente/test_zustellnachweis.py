"""Zustellnachweis bei einseitigen Erklärungen (Schritt 3c, Aufgabe 7; H8.3)."""

from __future__ import annotations

import datetime

import pytest
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.test import Client
from django.urls import reverse

from apps.dokumente import services
from apps.dokumente.models import Schreiben, Vorlage
from apps.dokumente.vorlagenarten import Versandweg
from apps.kern.models import Benutzer, Objekt
from tests.dokumente.conftest import kontext

pytestmark = pytest.mark.django_db

TAG = datetime.date(2026, 11, 20)


@pytest.fixture
def gesendete_kuendigung(kuendigung: Vorlage, objekt: Objekt, benutzer: Benutzer) -> Schreiben:
    schreiben = services.schreiben_erzeugen(kuendigung, objekt, kontext(objekt), Versandweg.SELBST)
    return services.schreiben_senden(schreiben, benutzer, pruefsumme=schreiben.pdf_sha256)


def test_einseitige_erklaerung_ohne_zustellnachweis_nicht_abschliessbar(
    gesendete_kuendigung: Schreiben,
) -> None:
    """Abnahme Schritt 3: Einseitige Erklärung lässt sich ohne Zustellnachweis nicht
    abschließen."""
    schreiben = gesendete_kuendigung
    assert schreiben.einseitige_erklaerung

    with pytest.raises(services.ZustellnachweisFehlt):
        services.schreiben_abschliessen(schreiben, zugestellt_am=TAG)
    with pytest.raises(services.ZustellnachweisFehlt):
        services.schreiben_abschliessen(schreiben, zugestellt_am=TAG, vermerk="   ")

    schreiben.refresh_from_db()
    assert schreiben.status == Schreiben.Status.GESENDET
    assert not schreiben.ist_erledigt


def test_auch_die_datenbank_laesst_es_nicht_zu(gesendete_kuendigung: Schreiben) -> None:
    """Abnahme Schritt 3: Einseitige Erklärung ohne Nachweis – auch am Service vorbei sichert
    eine Prüfbedingung in der Datenbank, dass sie nicht als zugestellt gilt."""
    schreiben = gesendete_kuendigung
    schreiben.status = Schreiben.Status.ZUGESTELLT
    with pytest.raises(IntegrityError), transaction.atomic():
        schreiben.save()


def test_mit_vermerk_oder_datei_ist_sie_erledigt(
    kuendigung: Vorlage, objekt: Objekt, benutzer: Benutzer
) -> None:
    """Schritt 3c, Aufgabe 7: Mit Zustellnachweis (Vermerk oder Datei) ist sie erledigt."""
    erste, zweite = (
        services.schreiben_erzeugen(kuendigung, objekt, kontext(objekt), Versandweg.SELBST)
        for _ in range(2)
    )
    for s in (erste, zweite):
        services.schreiben_senden(s, benutzer, pruefsumme=s.pdf_sha256)

    services.schreiben_abschliessen(
        erste, zugestellt_am=TAG, vermerk="Einwurf durch Boten am 20.11.2026, 9:15 Uhr"
    )
    services.schreiben_abschliessen(
        zweite, zugestellt_am=TAG, nachweis=ContentFile(b"%PDF-1.7 Beleg", name="beleg.pdf")
    )

    for s in (erste, zweite):
        s.refresh_from_db()
        assert s.status == Schreiben.Status.ZUGESTELLT
        assert s.ist_erledigt
    assert zweite.zustellnachweis.name.endswith(f"zustellnachweis_{zweite.pk}.pdf")


def test_andere_schreiben_ohne_nachweis_abschliessbar(
    mitteilung: Vorlage, objekt: Objekt, benutzer: Benutzer
) -> None:
    """Schritt 3c, Aufgabe 7: Die Pflicht gilt nur für einseitige Erklärungen."""
    schreiben = services.schreiben_erzeugen(mitteilung, objekt, kontext(objekt), Versandweg.MAIL)
    services.schreiben_senden(schreiben, benutzer, pruefsumme=schreiben.pdf_sha256)
    services.schreiben_abschliessen(schreiben, zugestellt_am=TAG)
    schreiben.refresh_from_db()
    assert schreiben.ist_erledigt


def test_nicht_gesendetes_laesst_sich_nicht_abschliessen(
    kuendigung: Vorlage, objekt: Objekt
) -> None:
    """Schritt 3c, Aufgabe 7: Abschließen setzt den Versand voraus."""
    schreiben = services.schreiben_erzeugen(kuendigung, objekt, kontext(objekt))
    with pytest.raises(services.SchreibenFehler):
        services.schreiben_abschliessen(schreiben, zugestellt_am=TAG, vermerk="x")


def test_abschliessen_ueber_die_seite(angemeldet: Client, gesendete_kuendigung: Schreiben) -> None:
    """Abnahme Schritt 3: Auf der Seite ohne Nachweis abgelehnt, mit Datei erledigt."""
    url = reverse("dokumente:schreiben_abschliessen", args=[gesendete_kuendigung.pk])

    antwort = angemeldet.post(url, {"zugestellt_am": "2026-11-20"}, follow=True)
    assert "ohne Zustellnachweis nicht abschließbar" in antwort.content.decode()
    gesendete_kuendigung.refresh_from_db()
    assert gesendete_kuendigung.status == Schreiben.Status.GESENDET

    datei = SimpleUploadedFile("foto.jpg", b"\xff\xd8\xff erfundenes Foto", "image/jpeg")
    angemeldet.post(url, {"zugestellt_am": "2026-11-20", "nachweis": datei})
    gesendete_kuendigung.refresh_from_db()
    assert gesendete_kuendigung.ist_erledigt
    nachweis = angemeldet.get(
        reverse("dokumente:schreiben_nachweis", args=[gesendete_kuendigung.pk])
    )
    assert nachweis.status_code == 200
