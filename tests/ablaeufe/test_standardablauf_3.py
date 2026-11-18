"""Standardablauf 3: Mahnlauf (Prozessliste). Auslöser: wöchentlich, nach dem Abgleich.
Ergebnis: je Rückstand eine belegte Stufe. Gilt nur für Verträge im Hof-System.

1. System rechnet den Rückstand je Vertrag aus dem laufenden Konto.
2. System lässt Verträge mit Sperre aus: Rechtsfall, Ratenplan, offener Klärfall, Datenlücke.
3. System bestimmt die Stufe nach der SOP: Erinnerung ab 5 Tagen, Mahnung ab 14 Tagen,
   Abmahnung bei der zweiten offenen Monatsmiete, Kündigung ab zwei Monatsmieten Rückstand.
4. System füllt die Vorlage der Stufe mit der Aufstellung je Monat und legt den Fall in die
   Arbeitsliste.
5. Du prüfst die Vorschau und klickst auf Senden (Erinnerung und Mahnung als Mail;
   Abmahnung und Kündigung druckst du und wirfst sie als Einschreiben ein).
6. Du erfasst den Zustellnachweis. Erst danach kann die nächste Stufe folgen.
7. Anwalt prüft jede Kündigung vor dem Versand.

Kleiner Hof mit vier Garagen ab Oktober 2026: A zahlt nicht, B zahlt unklar (Klärfall),
C ist ein Rechtsfall, D zahlt erst zum Teil und dann nach. Die Wochen sind gespielt: Das
Senden gilt am Stichtag als geschehen (``senden``-Hilfe der Tests des Mahnwesens).
"""

from __future__ import annotations

import datetime
import io
from datetime import date as D
from zoneinfo import ZoneInfo

import pytest
from django.core import mail
from django.core.management import call_command
from django.test import Client
from django.urls import reverse

from apps.abgleich import services as abgleich
from apps.dokumente.models import Schreiben
from apps.kern.models import Arbeitsposten, Benutzer, Lauf
from apps.mahnwesen import services
from apps.mahnwesen.models import Mahnfall, Mahnsperre
from tests.abgleich.conftest import Abgleichhof
from tests.kassenbuch.conftest import klein, neue_iban
from tests.mahnwesen.conftest import (
    Mahnhof,
    Vorlagen,
    _fakes_leeren,
    _pdf_ohne_weasyprint,
    fall_zu,
    letzter,
    mahnhof,
    stufen,
    vorlagen,
)

__all__ = ["_fakes_leeren", "_pdf_ohne_weasyprint", "klein", "mahnhof", "stufen", "vorlagen"]

pytestmark = pytest.mark.django_db


def _lauf(name: str, *argumente: str) -> Lauf:
    call_command(name, *argumente, stdout=io.StringIO())
    lauf = Lauf.objects.filter(name=name).first()
    assert lauf is not None and lauf.status == "erfolgreich", name
    return lauf


def _abgleich(stichtag: D) -> None:
    _lauf("kassenbuch_einordnen")
    _lauf("abgleich", "--stichtag", stichtag.isoformat())


def _mahnlauf(stichtag: D) -> Lauf:
    return _lauf("mahnlauf", "--stichtag", stichtag.isoformat())


def _als_gesendet_am(schreiben: Schreiben, tag: D) -> None:
    zeit = datetime.datetime.combine(tag, datetime.time(9), tzinfo=ZoneInfo("Europe/Berlin"))
    Schreiben.objects.filter(pk=schreiben.pk).update(gesendet_am=zeit)


def _senden_ueber_die_seite(client: Client, schreiben: Schreiben, tag: D, **extra: str) -> str:
    """5. Vorschau prüfen und senden: Die Seite zeigt das PDF und trägt dessen Prüfsumme."""
    seite = client.get(reverse("dokumente:schreiben", args=[schreiben.pk])).content.decode()
    assert reverse("dokumente:schreiben_pdf", args=[schreiben.pk]) in seite
    assert schreiben.pdf_sha256 in seite
    client.post(
        reverse("dokumente:schreiben_senden", args=[schreiben.pk]),
        {"pruefsumme": schreiben.pdf_sha256, **extra},
    )
    schreiben.refresh_from_db()
    if schreiben.status == Schreiben.Status.GESENDET:
        _als_gesendet_am(schreiben, tag)
    return seite


def _zustellen_ueber_die_seite(client: Client, schreiben: Schreiben, tag: D) -> None:
    """6. Zustellnachweis erfassen."""
    antwort = client.post(
        reverse("dokumente:schreiben_abschliessen", args=[schreiben.pk]),
        {"zugestellt_am": tag.isoformat(), "vermerk": f"Einwurf durch Boten am {tag:%d.%m.%Y}"},
    )
    assert antwort.status_code == 302
    schreiben.refresh_from_db()
    assert schreiben.ist_erledigt


def test_standardablauf_3_mahnlauf(
    mahnhof: Mahnhof, vorlagen: Vorlagen, benutzer: Benutzer, angemeldet: Client
) -> None:
    """Standardablauf 3 und Abnahme Schritt 7: Stufenfolge mit Fristen, keine Stufe ohne
    belegte Vorstufe, Sperren, Probebetrieb und Anwalt vor der Kündigung."""
    hof = Abgleichhof(klein=mahnhof.klein, mieter=mahnhof.mieter)
    a, b, c, d = (mahnhof.vertrag(miete="85.00") for _ in range(4))
    nw = {v: v.einheiten.get().nw_id for v in (a, b, c, d)}

    # Zahlungen im Oktober: D zahlt per Dauerauftrag zu wenig, B von einem fremden Konto
    # mit einer NW-ID im Zweck (Klärfall). A zahlt nichts. C ist ein Rechtsfall.
    iban_d = neue_iban()
    hof.regel(iban_d, vertrag=d)
    hof.zahlung(D(2026, 10, 6), "50.00", iban=iban_d, zweck=f"Miete {nw[d]}")
    hof.zahlung(D(2026, 10, 7), "85.00", iban=neue_iban(), zweck=f"Miete {nw[b]}")
    _abgleich(D(2026, 10, 7))
    assert b.pk in abgleich.vertraege_in_klaerung()
    angemeldet.post(
        reverse("mahnwesen:sperren"),
        {"vertrag": c.pk, "grund": "rechtsfall", "von": "2026-10-01", "hinweis": "Kanzlei"},
    )
    assert Mahnsperre.objects.get(vertrag=c).grund == Mahnsperre.Grund.RECHTSFALL

    # Montag 12.10.: 1.–4. Rückstand aus dem Konto, Sperren, Stufe Erinnerung, Arbeitsliste.
    lauf = _mahnlauf(D(2026, 10, 12))
    assert lauf.gebucht == 2 and lauf.arbeitsposten == 2
    assert fall_zu(a).stufe.schluessel == fall_zu(d).stufe.schluessel == "erinnerung"  # type: ignore[union-attr]
    assert fall_zu(b).status == fall_zu(c).status == Mahnfall.Status.GESPERRT
    assert fall_zu(b).sperrgrund.startswith("Klärfall")
    assert fall_zu(c).sperrgrund.startswith("Rechtsfall")
    erinnerung = letzter(a).schreiben
    assert erinnerung is not None and "10/2026" in erinnerung.inhalt_html  # Aufstellung je Monat

    seite = angemeldet.get(reverse("kern:arbeitslisten")).content.decode()
    assert "Mahnungen senden" in seite and nw[a] in seite
    posten_a = Arbeitsposten.objects.get(pk=letzter(a).arbeitsposten_id)
    weiter = angemeldet.post(
        reverse("kern:arbeitsposten_aktion", args=[posten_a.pk, "mahnfall_ansehen"])
    )
    assert weiter["Location"] == reverse("mahnwesen:fall", args=[fall_zu(a).pk])
    fall_seite = angemeldet.get(weiter["Location"]).content.decode()
    assert reverse("dokumente:schreiben", args=[erinnerung.pk]) in fall_seite

    # 5. Vorschau und Senden: Die Erinnerung geht als Mail hinaus.
    _senden_ueber_die_seite(angemeldet, erinnerung, D(2026, 10, 12))
    assert erinnerung.status == Schreiben.Status.GESENDET
    assert len(mail.outbox) == 1 and mail.outbox[0].attachments

    # D zahlt nach: Der Fall schließt sich, seine Erinnerung wird verworfen.
    hof.zahlung(D(2026, 10, 14), "35.00", iban=iban_d, zweck=f"Rest Miete {nw[d]}")
    _abgleich(D(2026, 10, 14))

    # Montag 19.10.: Mahnung (14 Tage nach Fälligkeit, Erinnerung belegt) mit 10 Tagen Frist.
    _mahnlauf(D(2026, 10, 19))
    assert Mahnfall.objects.get(vertrag=d).status == Mahnfall.Status.ERLEDIGT
    assert Schreiben.objects.get(pk=letzter(d).schreiben_id).status == "verworfen"
    posten_a.refresh_from_db()
    assert posten_a.status == Arbeitsposten.Status.ERLEDIGT
    mahnung = letzter(a).schreiben
    assert mahnung is not None and mahnung.art == "mahnung"
    assert "29.10.2026" in mahnung.inhalt_html
    _senden_ueber_die_seite(angemeldet, mahnung, D(2026, 10, 19))

    # 6. Ohne Zustellnachweis folgt keine Stufe.
    mahnhof.datenstand(D(2026, 11, 30))
    _mahnlauf(D(2026, 11, 5))
    assert letzter(a).stufe.schluessel == "mahnung"
    _zustellen_ueber_die_seite(angemeldet, mahnung, D(2026, 10, 20))

    # Abmahnung bei der zweiten offenen Monatsmiete, als Brief zum Drucken.
    _mahnlauf(D(2026, 11, 5))
    abmahnung = letzter(a).schreiben
    assert abmahnung is not None and abmahnung.art == "abmahnung"
    assert abmahnung.versandweg == "selbst"
    _senden_ueber_die_seite(angemeldet, abmahnung, D(2026, 11, 5))
    _zustellen_ueber_die_seite(angemeldet, abmahnung, D(2026, 11, 6))

    # Kündigung ab zwei Monatsmieten Rückstand: ohne Vorlage im Probebetrieb.
    _mahnlauf(D(2026, 11, 20))
    kuendigung = letzter(a)
    assert kuendigung.stufe.schluessel == "kuendigung" and kuendigung.probebetrieb
    hinweis = Arbeitsposten.objects.get(pk=kuendigung.arbeitsposten_id).hinweis
    assert "Probebetrieb: Vorlage fehlt" in hinweis and "vor Versand zum Anwalt" in hinweis

    # Mit freigegebener Vorlage wird die Stufe scharf; 7. der Anwalt prüft vor dem Versand.
    vorlagen.fristlos()
    _mahnlauf(D(2026, 11, 23))
    kuendigung.refresh_from_db()
    schreiben = kuendigung.schreiben
    assert schreiben is not None and schreiben.art == "kuendigung"
    seite = _senden_ueber_die_seite(angemeldet, schreiben, D(2026, 11, 23))
    assert "vor Versand zum Anwalt" in seite
    assert schreiben.status == Schreiben.Status.BEREIT  # ohne Haken nicht gesendet
    _senden_ueber_die_seite(angemeldet, schreiben, D(2026, 11, 23), anwalt_gesehen="1")
    assert schreiben.status == Schreiben.Status.GESENDET
    _zustellen_ueber_die_seite(angemeldet, schreiben, D(2026, 11, 24))
    _mahnlauf(D(2026, 11, 30))
    fall = fall_zu(a)
    assert fall.stufe_belegt_am == D(2026, 11, 24)

    # Ergebnis: je Rückstand eine belegte Stufe; B und C sind nie gemahnt worden.
    for gesperrt in (b, c):
        assert not fall_zu(gesperrt).schritte.exists()
    assert services.kachel_mahnfaelle()["gesperrt"] == 2
    assert len(mail.outbox) == 2  # Erinnerung und Mahnung als Mail, der Rest als Brief
