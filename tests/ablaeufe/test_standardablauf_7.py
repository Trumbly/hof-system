"""Standardablauf 7: Monatsabschluss (Prozessliste), durchgehend mit dem Testhof.

Auslöser: Anfang des Folgemonats. Ergebnis: ein gesperrter Monat mit Bericht.

1. System prüft, ob alle Umsätze eingeordnet sind und der Saldo zum Kontostand passt.
   (Belege prüft der Abschluss ab Schritt 3.)
2. System legt die Lücken in die Arbeitsliste.
3. Du klärst sie.
4. System sperrt den Monat und erzeugt den Bericht je Objekt.
5. Hof-Cockpit und Liquiditätsvorschau kommen mit Schritt 9 und v2; hier nicht geprüft.
"""

from __future__ import annotations

import datetime
import io

import pytest
from django.core.management import call_command
from django.test import Client
from django.urls import reverse

from apps.bank.models import Bankumsatz
from apps.kassenbuch import auswertung, pdf, services
from apps.kassenbuch.models import Buchung, Monatsabschluss, Monatsbericht
from apps.kern.models import Arbeitsposten, Mandant
from testhof.aufbau import Testhof

JAN = datetime.date(2026, 1, 1)
FEB = datetime.date(2026, 2, 1)


@pytest.fixture
def fake_pdf(monkeypatch: pytest.MonkeyPatch) -> None:
    """PDF ohne WeasyPrint, damit der Ablauf auch unter macOS durchläuft (echtes PDF:
    tests/kassenbuch/test_pdf.py)."""
    monkeypatch.setattr(pdf, "html_zu_pdf", lambda html: b"%PDF-1.7 Ablauf 7")


def _abschluss(*argumente: str) -> None:
    call_command("kassenbuch_monatsabschluss", "--ab", "2026-01", *argumente, stdout=io.StringIO())


def test_standardablauf_7_monatsabschluss(
    testhof: Testhof, angemeldet: Client, fake_pdf: None
) -> None:
    """Standardablauf 7 und Abnahme Schritt 2: Lücken werden Arbeitsposten, nach dem Klären
    ist der Monat abgeschlossen, gesperrt und hat seinen Bericht je Objekt, ohne Handarbeit.
    Ändern geht danach nur per Stornobuchung im offenen Monat."""
    kassenbuch = testhof.kassenbuch
    mandanten = testhof.kern.mandanten
    objekte = testhof.kern.objekte
    call_command("kassenbuch_einordnen", stdout=io.StringIO())

    # 1. und 2.: Prüfen, Lücken in die Arbeitsliste.
    _abschluss("--bis", "2026-01")
    offen = Arbeitsposten.objects.filter(art=services.ART_ABSCHLUSS, status="offen")
    assert {p.bezug.mandant.art for p in offen} == {Mandant.Art.GBR, Mandant.Art.NACHLASS}
    assert not Monatsabschluss.objects.filter(abgeschlossen_am__isnull=False).exists()
    assert not Monatsbericht.objects.exists()

    # 3.: Du klärst die offenen Umsätze im Januar über die Aktion „Zuordnen“.
    januar = Arbeitsposten.objects.filter(
        art=services.ART_EINORDNEN,
        status="offen",
        bezug_id__in=Bankumsatz.objects.filter(
            buchungsdatum__gte=JAN, buchungsdatum__lt=FEB
        ).values("pk"),
    )
    assert januar.count() == 2
    for posten in januar:
        umsatz = Bankumsatz.objects.select_related("konto").get(pk=posten.bezug_id)
        weiter = angemeldet.post(
            reverse("kern:arbeitsposten_aktion", args=[posten.pk, "umsatz_zuordnen"])
        )
        vr = umsatz.konto == testhof.bank.konten["vr"]
        kategorie = kassenbuch.kategorien["Versicherungen" if vr else "Material"]
        antwort = angemeldet.post(
            weiter["Location"],
            {"kategorie": kategorie.pk, "objekt": objekte["FELD" if vr else "HOF"].pk},
        )
        assert antwort.status_code == 302, antwort.content.decode()[:500]

    # 4.: Erneut prüfen (Aktion am Posten für die GbR, der Lauf für den Nachlass).
    gbr_posten = offen.get(bezug_id__in=Monatsabschluss.objects.filter(mandant=mandanten["gbr"]))
    angemeldet.post(reverse("kern:arbeitsposten_aktion", args=[gbr_posten.pk, "abschluss_pruefen"]))
    assert Monatsabschluss.objects.get(mandant=mandanten["gbr"], monat=JAN).abgeschlossen
    assert not Monatsbericht.objects.exists()  # Nachlass fehlt noch
    _abschluss("--bis", "2026-01")

    for art in ("gbr", "nachlass"):
        abschluss = Monatsabschluss.objects.get(mandant=mandanten[art], monat=JAN)
        assert abschluss.abgeschlossen
        assert abschluss.saldo_bank == abschluss.saldo_buch
    assert not Arbeitsposten.objects.filter(art=services.ART_ABSCHLUSS, status="offen").exists()
    berichte = {b.objekt.kuerzel: b for b in Monatsbericht.objects.filter(monat=JAN)}
    assert set(berichte) == {"HOF", "HALLE", "FELD"}
    assert all(b.datei.name.endswith(f"monatsbericht-{k}-2026-01.pdf") for k, b in berichte.items())
    halle = auswertung.bericht(objekte["HALLE"], JAN)
    assert any(z.name == "Versicherungen" for z in halle.kosten)  # Teil der Sammelrechnung
    hof = auswertung.bericht(objekte["HOF"], JAN)
    assert any(z.name == "Mieteinnahmen Wohnraum" for z in hof.einnahmen)
    assert [e.mandant.kuerzel for e in hof.entnahmen] == ["GBR"]

    # Gesperrt: Im Januar entsteht keine Buchung mehr; der Weg ist das Storno im Februar.
    umsatz, _ = kassenbuch.sammelrechnung
    with pytest.raises(services.MonatGesperrt):
        services.umsatz_umbuchen(
            umsatz,
            [services.Teil(umsatz.betrag, kassenbuch.kategorien["Versicherungen"], objekte["HOF"])],
            datum=datetime.date(2026, 1, 31),
        )
    stornos = services.umsatz_stornieren(umsatz)
    assert {s.datum for s in stornos} == {FEB}
    assert Buchung.objects.filter(datum__lt=FEB, umsatz=umsatz).count() == 2
    assert auswertung.bericht(objekte["HALLE"], JAN).summe_kosten == halle.summe_kosten
