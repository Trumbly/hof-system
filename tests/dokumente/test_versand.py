"""Senden nur per Klick nach der Vorschau; Mail mit Kopie in „Gesendet“; Brief über
LetterXpress (Schritt 3c, Aufgaben 4 bis 6; CLAUDE.md Regel 8)."""

from __future__ import annotations

import base64
import datetime
import email
import hashlib
import json
from typing import Any

import pytest
from django.core import mail
from django.core.management import call_command
from django.test import Client
from django.urls import reverse

from apps.dokumente import letterxpress, services, versand
from apps.dokumente.letterxpress import FakeLetterXpressClient, LetterXpressClient
from apps.dokumente.models import Schreiben, Vorlage
from apps.dokumente.versand import (
    FakeImapClient,
    ImaplibClient,
    VersandNichtEingerichtet,
    header_wert,
)
from apps.dokumente.vorlagenarten import Versandweg
from apps.kern.models import Arbeitsposten, Benutzer, Lauf, Objekt
from tests.dokumente.conftest import kontext

pytestmark = pytest.mark.django_db


def _nichts_hinausgegangen() -> None:
    assert mail.outbox == []
    assert FakeImapClient.gesendet == []
    assert FakeLetterXpressClient.auftraege == {}


def test_kein_schreiben_verlaesst_das_system_ohne_klick_auf_senden(
    angemeldet: Client, mitteilung: Vorlage, objekt: Objekt
) -> None:
    """Abnahme Schritt 3: Kein Schreiben verlässt das System ohne Klick auf Senden.

    Erzeugen, Ansehen der Seite und des PDFs, ein Senden ohne Prüfsumme der Vorschau oder
    ohne Anmeldung: Nichts geht hinaus. Erst der Klick auf „Senden“ auf der Seite mit
    dem PDF sendet – genau einmal.
    """
    schreiben = services.schreiben_erzeugen(mitteilung, objekt, kontext(objekt), Versandweg.MAIL)
    brief = services.schreiben_erzeugen(
        mitteilung, objekt, kontext(objekt), Versandweg.EINWURF_EINSCHREIBEN
    )
    _nichts_hinausgegangen()

    seite = angemeldet.get(reverse("dokumente:schreiben", args=[schreiben.pk]))
    assert seite.status_code == 200
    angemeldet.get(reverse("dokumente:schreiben_pdf", args=[schreiben.pk]))
    angemeldet.get(reverse("dokumente:schreiben_liste"))
    _nichts_hinausgegangen()

    senden = reverse("dokumente:schreiben_senden", args=[schreiben.pk])
    assert angemeldet.get(senden).status_code == 405
    angemeldet.post(senden)  # ohne Prüfsumme: keine Vorschau gesehen
    angemeldet.post(senden, {"pruefsumme": "0" * 64})  # anderes PDF
    Client().post(senden, {"pruefsumme": schreiben.pdf_sha256})  # nicht angemeldet
    _nichts_hinausgegangen()
    schreiben.refresh_from_db()
    assert schreiben.status == Schreiben.Status.BEREIT

    # Die Seite trägt die Prüfsumme des gezeigten PDFs im Formular „Senden“.
    html = seite.content.decode()
    assert reverse("dokumente:schreiben_pdf", args=[schreiben.pk]) in html
    assert f'name="pruefsumme" value="{schreiben.pdf_sha256}"' in html

    antwort = angemeldet.post(senden, {"pruefsumme": schreiben.pdf_sha256})
    assert antwort.status_code == 302
    assert len(mail.outbox) == 1
    assert len(FakeImapClient.gesendet) == 1
    assert FakeLetterXpressClient.auftraege == {}  # der Brief wartet weiter auf seinen Klick
    schreiben.refresh_from_db()
    assert schreiben.status == Schreiben.Status.GESENDET
    assert schreiben.gesendet_von is not None

    angemeldet.post(senden, {"pruefsumme": schreiben.pdf_sha256})  # zweiter Klick
    assert len(mail.outbox) == 1
    brief.refresh_from_db()
    assert brief.status == Schreiben.Status.BEREIT


def test_senden_im_service_nur_mit_pruefsumme_der_vorschau(
    mitteilung: Vorlage, objekt: Objekt, benutzer: Benutzer
) -> None:
    """Schritt 3c, Aufgabe 6: Auch der Service sendet nur mit der Prüfsumme des PDFs."""
    schreiben = services.schreiben_erzeugen(mitteilung, objekt, kontext(objekt), Versandweg.MAIL)
    with pytest.raises(services.VorschauFehlt):
        services.schreiben_senden(schreiben, benutzer, pruefsumme="")
    _nichts_hinausgegangen()


def test_mail_von_kontakt_mit_pdf_und_kopie_in_gesendet(
    mitteilung: Vorlage, objekt: Objekt, benutzer: Benutzer, settings: Any
) -> None:
    """Abnahme Schritt 3 (Bauplan): Es geht erst nach dem Klick hinaus, mit Kopie im
    Postfach. Schritt 3c, Aufgabe 4: SMTP von kontakt@, Kopie per IMAP-Append."""
    schreiben = services.schreiben_erzeugen(mitteilung, objekt, kontext(objekt), Versandweg.MAIL)

    services.schreiben_senden(schreiben, benutzer, pruefsumme=schreiben.pdf_sha256)

    (nachricht,) = mail.outbox
    assert nachricht.from_email == settings.DOKUMENTE["MAIL_ABSENDER"]
    assert nachricht.to == ["empfaenger@testhof.invalid"]
    assert nachricht.subject == "Hinweis GAR1"
    assert "Guten Tag Frau Beispiel" in nachricht.body
    ((dateiname, inhalt, art),) = nachricht.attachments
    assert art == "application/pdf"
    assert dateiname == schreiben.pdf.name.rsplit("/", 1)[-1]
    assert inhalt.startswith(b"%PDF")

    (kopie,) = FakeImapClient.gesendet
    kopie_mail = email.message_from_bytes(kopie)
    schreiben.refresh_from_db()
    assert header_wert(kopie_mail["Message-ID"]) == schreiben.dienst_auftrag
    assert header_wert(nachricht.message()["Message-ID"]) == schreiben.dienst_auftrag
    assert schreiben.dienst_auftrag.endswith("@hof.invalid>")
    assert schreiben.einlieferungsdatum is not None


def test_message_id_ohne_hostnamen_und_robust_gegen_umbruch(
    mitteilung: Vorlage,
    objekt: Objekt,
    benutzer: Benutzer,
    settings: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Schritt 3c, Aufgabe 4: Die Message-ID trägt die Domain des Absenders, nie den
    Hostnamen des Rechners. Auch wenn der Header sehr lang wird und beim Serialisieren
    umbricht (wie auf dem GitHub-Runner), passen Schreiben, Mail und Kopie zusammen."""
    from django.core.mail import message as django_mail

    langer_host = "runner-" + "x" * 90 + ".internal.cloudapp.net"
    monkeypatch.setattr(django_mail, "DNS_NAME", langer_host)
    lange_domain = "post." + "sehr-lange-erfundene-domain-" * 3 + "hof.invalid"
    settings.DOKUMENTE = {**settings.DOKUMENTE, "MAIL_ABSENDER": f"kontakt@{lange_domain}"}

    schreiben = services.schreiben_erzeugen(mitteilung, objekt, kontext(objekt), Versandweg.MAIL)
    services.schreiben_senden(schreiben, benutzer, pruefsumme=schreiben.pdf_sha256)

    schreiben.refresh_from_db()
    (kopie,) = FakeImapClient.gesendet
    zeilen = kopie.decode().split("\r\n" if b"\r\n" in kopie else "\n")
    beginn = next(i for i, z in enumerate(zeilen) if z.lower().startswith("message-id:"))
    assert zeilen[beginn + 1].startswith((" ", "\t"))  # der Header ist wirklich umbrochen
    assert header_wert(email.message_from_bytes(kopie)["Message-ID"]) == schreiben.dienst_auftrag
    assert header_wert(mail.outbox[0].message()["Message-ID"]) == schreiben.dienst_auftrag
    assert schreiben.dienst_auftrag.endswith(f"@{lange_domain}>")
    assert "runner-" not in kopie.decode()


def test_message_id_ohne_domain_im_absender(settings: Any) -> None:
    """Schritt 3c, Aufgabe 4: Ohne Domain im Absender gilt eine feste Domain."""
    settings.DOKUMENTE = {**settings.DOKUMENTE, "MAIL_ABSENDER": ""}
    assert versand.message_id_erzeugen().endswith("@hof-system.invalid>")


def test_fehlt_die_kopie_ist_die_mail_trotzdem_gesendet_mit_arbeitsposten(
    mitteilung: Vorlage, objekt: Objekt, benutzer: Benutzer, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Schritt 3c, Aufgabe 4: Scheitert nur die Kopie in „Gesendet“, gilt die Mail als
    gesendet (sie ist draußen) und ein Arbeitsposten meldet die fehlende Kopie."""

    def scheitern(self: FakeImapClient, roh: bytes) -> None:
        raise versand.VersandFehler("IMAP: TimeoutError")

    monkeypatch.setattr(FakeImapClient, "in_gesendet_ablegen", scheitern)
    schreiben = services.schreiben_erzeugen(mitteilung, objekt, kontext(objekt), Versandweg.MAIL)
    services.schreiben_senden(schreiben, benutzer, pruefsumme=schreiben.pdf_sha256)

    assert len(mail.outbox) == 1
    schreiben.refresh_from_db()
    assert schreiben.status == Schreiben.Status.GESENDET
    posten = Arbeitsposten.objects.get(art=services.ART_KOPIE_FEHLT)
    assert posten.bezug == schreiben


def test_scheitert_smtp_bleibt_das_schreiben_bereit(
    angemeldet: Client, mitteilung: Vorlage, objekt: Objekt, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Schritt 3c, Aufgabe 4: Scheitert der Versand, bleibt das Schreiben „bereit“."""

    def scheitern(self: versand.DjangoMailClient, nachricht: Any) -> None:
        raise versand.VersandFehler("SMTP: ConnectionRefusedError")

    monkeypatch.setattr(versand.DjangoMailClient, "senden", scheitern)
    schreiben = services.schreiben_erzeugen(mitteilung, objekt, kontext(objekt), Versandweg.MAIL)

    antwort = angemeldet.post(
        reverse("dokumente:schreiben_senden", args=[schreiben.pk]),
        {"pruefsumme": schreiben.pdf_sha256},
        follow=True,
    )

    assert "Nicht gesendet: SMTP: ConnectionRefusedError" in antwort.content.decode()
    schreiben.refresh_from_db()
    assert schreiben.status == Schreiben.Status.BEREIT
    assert FakeImapClient.gesendet == []


def test_brief_als_einwurf_einschreiben_ueber_letterxpress_fake(
    kuendigung: Vorlage, objekt: Objekt, benutzer: Benutzer
) -> None:
    """Abnahme Schritt 3: Brief über den LetterXpress-Fake als Einwurf-Einschreiben;
    Sendungsnummer (und Einlieferungsdatum) hängen am Schreiben."""
    schreiben = services.schreiben_erzeugen(kuendigung, objekt, kontext(objekt))
    assert schreiben.versandweg == Versandweg.EINWURF_EINSCHREIBEN  # Standard der Art

    services.schreiben_senden(schreiben, benutzer, pruefsumme=schreiben.pdf_sha256)

    schreiben.refresh_from_db()
    (auftrag,) = FakeLetterXpressClient.auftraege.values()
    assert auftrag.letter["registered"] == letterxpress.EINWURF_EINSCHREIBEN
    assert auftrag.letter["filename_original"] == schreiben.pdf.name.rsplit("/", 1)[-1]
    assert auftrag.letter["notice"] == f"Schreiben {schreiben.pk}"
    assert schreiben.status == Schreiben.Status.GESENDET
    assert schreiben.dienst_auftrag == str(auftrag.daten["id"])
    assert mail.outbox == []

    FakeLetterXpressClient.einliefern(schreiben.dienst_auftrag, datetime.date(2026, 11, 18))
    call_command("versandstand_abrufen")

    schreiben.refresh_from_db()
    assert schreiben.sendungsnummer == f"RR{int(schreiben.dienst_auftrag):09d}DE"
    assert schreiben.einlieferungsdatum == datetime.date(2026, 11, 18)
    assert schreiben.status == Schreiben.Status.GESENDET  # noch nicht zugestellt
    lauf = Lauf.objects.get(name="versandstand_abrufen")
    assert (lauf.status, lauf.gebucht, lauf.fehler) == (Lauf.Status.ERFOLGREICH, 1, 0)

    FakeLetterXpressClient.zustellen(schreiben.dienst_auftrag, datetime.date(2026, 11, 19))
    call_command("versandstand_abrufen")

    schreiben.refresh_from_db()
    assert schreiben.status == Schreiben.Status.ZUGESTELLT
    assert schreiben.ist_erledigt
    assert schreiben.zugestellt_am == datetime.date(2026, 11, 19)
    assert schreiben.sendungsnummer in schreiben.zustellnachweis_vermerk


def test_einfacher_brief_ohne_einschreiben(
    mitteilung: Vorlage, objekt: Objekt, benutzer: Benutzer
) -> None:
    """Schritt 3c, Aufgabe 5: Einwurf-Einschreiben ist eine Option, kein Muss."""
    schreiben = services.schreiben_erzeugen(mitteilung, objekt, kontext(objekt), Versandweg.BRIEF)
    services.schreiben_senden(schreiben, benutzer, pruefsumme=schreiben.pdf_sha256)
    (auftrag,) = FakeLetterXpressClient.auftraege.values()
    assert "registered" not in auftrag.letter


def test_letterxpress_client_spricht_api_v3(monkeypatch: pytest.MonkeyPatch) -> None:
    """Schritt 3c, Aufgabe 5: Der echte Client baut die Anfragen nach der API v3 (auth in
    jedem Körper, letter mit Base64 und MD5, registered r1) – geprüft ohne Netz."""
    anfragen: list[Any] = []

    class Antwort:
        def __init__(self, daten: dict[str, Any]) -> None:
            self.daten = daten

        def __enter__(self) -> Antwort:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def read(self) -> bytes:
            return json.dumps(self.daten).encode()

    def urlopen(anfrage: Any, timeout: float) -> Antwort:
        anfragen.append(anfrage)
        daten = {
            "id": 6035138,
            "status": "done",
            "registered": "r1",
            "dispatch_date": None,
            "updated_at": "2026-11-18 10:00:04",
            "items": [
                {
                    "status": "sent",
                    "tracking_code": "RC123456789DE",
                    "tracking_status": "Zugestellt: Die Sendung wurde am 19.11.26 zugestellt.",
                }
            ],
        }
        return Antwort({"status": 200, "message": "OK", "data": daten})

    monkeypatch.setattr(letterxpress.urllib.request, "urlopen", urlopen)
    client = LetterXpressClient(benutzer="testkonto", apikey="kein-echter-schluessel", modus="test")

    auftrag = client.auftrag_senden(
        b"%PDF-1.7 Test", einschreiben="r1", dateiname="kuendigung_2026-11-17_1.pdf", notiz="S 1"
    )
    client.auftrag_abrufen(auftrag.auftrag_id)

    senden, abrufen = anfragen
    assert (senden.get_method(), senden.full_url) == (
        "POST",
        "https://api.letterxpress.de/v3/printjobs",
    )
    koerper = json.loads(senden.data)
    assert koerper["auth"] == {
        "username": "testkonto",
        "apikey": "kein-echter-schluessel",
        "mode": "test",
    }
    brief = koerper["letter"]
    assert brief["registered"] == "r1"
    assert brief["specification"] == {"color": "1", "mode": "simplex", "shipping": "national"}
    assert base64.b64decode(brief["base64_file"]) == b"%PDF-1.7 Test"
    assert brief["base64_file_checksum"] == hashlib.md5(brief["base64_file"].encode()).hexdigest()  # noqa: S324
    assert (abrufen.get_method(), abrufen.full_url) == (
        "GET",
        "https://api.letterxpress.de/v3/printjobs/6035138",
    )
    assert json.loads(abrufen.data)["auth"]["mode"] == "test"
    assert auftrag.sendungsnummer == "RC123456789DE"
    assert auftrag.einlieferungsdatum == datetime.date(2026, 11, 18)
    assert auftrag.zugestellt


def test_ohne_zugangsdaten_ist_der_versand_nicht_eingerichtet(settings: Any) -> None:
    """Schritt 3c: Echte Clients ohne Zugangsdaten melden „nicht eingerichtet“, bevor
    etwas hinausgeht."""
    with pytest.raises(VersandNichtEingerichtet):
        LetterXpressClient()
    with pytest.raises(VersandNichtEingerichtet):
        LetterXpressClient(benutzer="a", apikey="b", modus="echt")
    with pytest.raises(VersandNichtEingerichtet):
        ImaplibClient()


def test_fakes_verweigern_sich_in_der_produktion(monkeypatch: pytest.MonkeyPatch) -> None:
    """Schritt 3c: Die Fakes laufen nie in der Produktion (dort gälte sonst als versendet,
    was nirgends ankam)."""
    monkeypatch.setenv("DJANGO_ENV", "prod")
    with pytest.raises(VersandNichtEingerichtet):
        FakeImapClient()
    with pytest.raises(VersandNichtEingerichtet):
        FakeLetterXpressClient()


def test_versandweg_selbst_haelt_nur_den_klick_fest(
    mitteilung: Vorlage, objekt: Objekt, benutzer: Benutzer
) -> None:
    """Schritt 3c, Aufgabe 2: Versandweg „selbst“ (drucken und übergeben) geht nirgends
    hin; der Klick hält fest, dass das Schreiben übergeben wird."""
    schreiben = services.schreiben_erzeugen(mitteilung, objekt, kontext(objekt), Versandweg.SELBST)
    services.schreiben_senden(schreiben, benutzer, pruefsumme=schreiben.pdf_sha256)
    _nichts_hinausgegangen()
    schreiben.refresh_from_db()
    assert schreiben.status == Schreiben.Status.GESENDET


def test_verwerfen_statt_loeschen(mitteilung: Vorlage, objekt: Objekt, benutzer: Benutzer) -> None:
    """Schritt 3c (CLAUDE.md Regel 4.4): Ein falsches Schreiben wird verworfen, nicht gelöscht."""
    schreiben = services.schreiben_erzeugen(mitteilung, objekt, kontext(objekt), Versandweg.MAIL)
    services.schreiben_verwerfen(schreiben)
    with pytest.raises(services.SchreibenFehler):
        services.schreiben_senden(schreiben, benutzer, pruefsumme=schreiben.pdf_sha256)
    _nichts_hinausgegangen()
    assert Schreiben.objects.filter(pk=schreiben.pk, status="verworfen").exists()
