"""Seiten des Kassenbuchs und die Aktionen am Arbeitsposten „Umsätze einordnen“."""

from __future__ import annotations

import datetime
from decimal import Decimal

import pytest
from django.core.files.base import ContentFile
from django.test import Client
from django.urls import reverse

from apps.bank.models import Bankumsatz
from apps.kassenbuch import pdf, services
from apps.kassenbuch.models import Buchung, Monatsbericht, Zuordnungsregel
from apps.kern import aktionen
from apps.kern.models import Arbeitsposten
from tests.kassenbuch.conftest import KleinerHof, ganz, umsatz

JAN = datetime.date(2026, 1, 10)


def _posten(klein: KleinerHof, betrag: str = "120.00") -> tuple[Arbeitsposten, Bankumsatz]:
    eintrag = umsatz(klein.konto, JAN, betrag, eingang=False, zweck=f"Rechnung {betrag}")
    services.umsaetze_einordnen()
    return Arbeitsposten.objects.get(art=services.ART_EINORDNEN, bezug_id=eintrag.pk), eintrag


def test_seiten_brauchen_anmeldung(client: Client, klein: KleinerHof) -> None:
    antwort = client.get(reverse("kassenbuch:buchungen"))
    assert antwort.status_code == 302
    assert reverse("account_login") in antwort["Location"]


def test_seiten_laden(angemeldet: Client, klein: KleinerHof) -> None:
    """Seiten: Kassenbuch mit Filter, Regeln, Abschlüsse, Monatsbericht; Menüpunkt."""
    eintrag = umsatz(klein.konto, JAN, "250.00")
    ganz(klein, eintrag)
    seiten = [
        reverse("kassenbuch:buchungen") + f"?von=2026-01-01&bis=2026-01-31&objekt={klein.hof.pk}",
        reverse("kassenbuch:regeln"),
        reverse("kassenbuch:regel_neu"),
        reverse("kassenbuch:abschluesse"),
        reverse("kassenbuch:bericht") + f"?objekt={klein.hof.pk}&monat=2026-01",
        reverse("kassenbuch:umsatz", args=[eintrag.pk]),
        reverse("kassenbuch:zuordnen", args=[eintrag.pk]),
        reverse("kassenbuch:aufteilen", args=[eintrag.pk]),
        reverse("kassenbuch:regel_aus_umsatz", args=[eintrag.pk]),
    ]
    for adresse in seiten:
        antwort = angemeldet.get(adresse)
        assert antwort.status_code == 200, adresse
        assert 'href="/kassenbuch/"' in antwort.content.decode()
    buchungen = angemeldet.get(seiten[0]).content.decode()
    assert f'id="buchung-{Buchung.objects.get().pk}"' in buchungen


def test_aktionen_am_posten(klein: KleinerHof) -> None:
    """Aufgabe 3: Der Posten „Umsätze einordnen“ bietet Zuordnen, Aufteilen, Regel anlegen
    und Zurückstellen; die ersten drei führen auf ihre Seite."""
    posten, _ = _posten(klein)
    titel = [a.titel for a in aktionen.fuer_posten(posten)]
    assert titel == ["Zuordnen", "Aufteilen", "Regel anlegen", "Zurückstellen"]
    assert aktionen.holen("umsatz_zuordnen").seite is not None
    assert aktionen.holen("zurueckstellen").seite is None


def test_aktion_mit_seite_leitet_weiter(angemeldet: Client, klein: KleinerHof) -> None:
    posten, eintrag = _posten(klein)
    adresse = reverse("kern:arbeitsposten_aktion", args=[posten.pk, "umsatz_zuordnen"])

    antwort = angemeldet.post(adresse)
    assert antwort.status_code == 302
    assert antwort["Location"] == reverse("kassenbuch:zuordnen", args=[eintrag.pk])

    htmx = angemeldet.post(adresse, headers={"HX-Request": "true"})
    assert htmx.status_code == 200
    assert htmx["HX-Redirect"] == reverse("kassenbuch:zuordnen", args=[eintrag.pk])
    posten.refresh_from_db()
    assert posten.status == "offen"

    zurueck = angemeldet.post(
        reverse("kern:arbeitsposten_aktion", args=[posten.pk, "zurueckstellen"])
    )
    assert zurueck.status_code == 302
    posten.refresh_from_db()
    assert posten.status == "zurueckgestellt"


def test_arbeitsliste_zeigt_link_zum_umsatz(angemeldet: Client, klein: KleinerHof) -> None:
    _, eintrag = _posten(klein)
    seite = angemeldet.get(reverse("kern:arbeitslisten")).content.decode()
    assert "Umsätze einordnen" in seite
    assert reverse("kassenbuch:umsatz", args=[eintrag.pk]) in seite
    assert "Regel anlegen" in seite


def test_zuordnen_bucht_und_erledigt_den_posten(angemeldet: Client, klein: KleinerHof) -> None:
    posten, eintrag = _posten(klein)
    antwort = angemeldet.post(
        reverse("kassenbuch:zuordnen", args=[eintrag.pk]),
        {"kategorie": klein.strom.pk, "objekt": klein.halle.pk, "leistungsjahr": 2025},
    )
    assert antwort.status_code == 302
    assert antwort["Location"] == reverse("kern:arbeitslisten")
    (buchung,) = Buchung.objects.filter(umsatz=eintrag)
    assert (buchung.objekt, buchung.mandant, buchung.leistungsjahr) == (klein.halle, klein.kg, 2025)
    posten.refresh_from_db()
    assert posten.status == "erledigt"


def _teile(*teile: tuple[str, int, int]) -> dict[str, object]:
    daten: dict[str, object] = {
        "teile-TOTAL_FORMS": len(teile),
        "teile-INITIAL_FORMS": 0,
        "teile-MIN_NUM_FORMS": 2,
        "teile-MAX_NUM_FORMS": 1000,
    }
    for nummer, (betrag, kategorie, objekt) in enumerate(teile):
        daten[f"teile-{nummer}-betrag"] = betrag
        daten[f"teile-{nummer}-kategorie"] = kategorie
        daten[f"teile-{nummer}-objekt"] = objekt
    return daten


def test_aufteilen_ueber_das_formular(angemeldet: Client, klein: KleinerHof) -> None:
    """Aufgabe 2 auf der Seite: Aufteilen mit mehreren Zeilen; die Summe muss stimmen."""
    posten, eintrag = _posten(klein, "100.00")
    adresse = reverse("kassenbuch:aufteilen", args=[eintrag.pk])
    falsch = angemeldet.post(
        adresse,
        _teile(("70.00", klein.strom.pk, klein.hof.pk), ("20.00", klein.strom.pk, klein.halle.pk)),
    )
    assert falsch.status_code == 200
    assert "Summe der Teile" in falsch.content.decode()
    assert not Buchung.objects.exists()

    mehr = angemeldet.post(
        adresse,
        {
            **_teile(
                ("70.00", klein.strom.pk, klein.hof.pk), ("30.00", klein.strom.pk, klein.halle.pk)
            ),
            "mehr": "1",
        },
    )
    assert 'id="teil-2"' in mehr.content.decode()
    assert not Buchung.objects.exists()

    richtig = angemeldet.post(
        adresse,
        _teile(
            ("50.00", klein.strom.pk, klein.hof.pk),
            ("30.00", klein.strom.pk, klein.halle.pk),
            ("20.00", klein.miete.pk, klein.hof.pk),
        ),
    )
    assert richtig.status_code == 302
    assert sorted(Buchung.objects.values_list("betrag", flat=True)) == [
        Decimal("20.00"),
        Decimal("30.00"),
        Decimal("50.00"),
    ]
    posten.refresh_from_db()
    assert posten.status == "erledigt"


def test_htmx_zeile_beim_aufteilen(angemeldet: Client, klein: KleinerHof) -> None:
    _, eintrag = _posten(klein)
    antwort = angemeldet.get(
        reverse("kassenbuch:aufteilen_zeile", args=[eintrag.pk]) + "?nummer=2",
        headers={"HX-Request": "true"},
    )
    inhalt = antwort.content.decode()
    assert 'name="teile-2-betrag"' in inhalt
    assert 'id="id_teile-TOTAL_FORMS" hx-swap-oob="true"' in inhalt
    assert 'value="3"' in inhalt


def test_regel_anlegen_mit_vorschau_und_anwenden(angemeldet: Client, klein: KleinerHof) -> None:
    """Aufgabe 4 auf der Seite: Regel aus dem Umsatz anlegen, Vorschau mit Zählern, dann
    rückwirkend anwenden."""
    _, eintrag = _posten(klein)
    zweiter = umsatz(klein.konto, JAN, "121.00", eingang=False, zweck="Rechnung 2")
    services.umsaetze_einordnen()
    formular = angemeldet.get(reverse("kassenbuch:regel_aus_umsatz", args=[eintrag.pk]))
    assert 'value="DE40012345670000000001"' in formular.content.decode()

    antwort = angemeldet.post(
        reverse("kassenbuch:regel_aus_umsatz", args=[eintrag.pk]),
        {
            "bezeichnung": "Stadtwerke",
            "gegen_iban": "DE40012345670000000001",
            "richtung": "ausgang",
            "kategorie": klein.strom.pk,
            "objekt": klein.hof.pk,
            "prioritaet": 10,
            "aktiv": "on",
        },
    )
    regel = Zuordnungsregel.objects.get()
    assert antwort["Location"] == reverse("kassenbuch:regel_anwenden", args=[regel.pk])
    assert not Buchung.objects.exists()

    vorschau = angemeldet.get(antwort["Location"]).content.decode()
    assert "wird gebucht: <strong>2</strong>" in vorschau

    veraltet = angemeldet.post(antwort["Location"], {"erwartet": "1"}, follow=True)
    assert "Vorschau neu ansehen" in veraltet.content.decode()
    assert not Buchung.objects.exists()

    angewandt = angemeldet.post(antwort["Location"], {"erwartet": "2"})
    assert angewandt.status_code == 302
    assert set(Buchung.objects.values_list("umsatz_id", flat=True)) == {eintrag.pk, zweiter.pk}


def test_stornieren_ueber_die_seite(angemeldet: Client, klein: KleinerHof) -> None:
    eintrag = umsatz(klein.konto, JAN, "250.00")
    ganz(klein, eintrag)
    antwort = angemeldet.post(reverse("kassenbuch:stornieren", args=[eintrag.pk]), follow=True)
    assert "1 Buchungen storniert" in antwort.content.decode()
    assert Buchung.objects.filter(storno_von__isnull=False).count() == 1


def test_bericht_seite_und_pdf(
    angemeldet: Client, klein: KleinerHof, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Aufgabe 6: Monatsbericht je Objekt als Seite und als PDF."""
    eintrag = umsatz(klein.konto, JAN, "250.00")
    ganz(klein, eintrag)
    seite = angemeldet.get(reverse("kassenbuch:bericht") + f"?objekt={klein.hof.pk}&monat=2026-01")
    inhalt = seite.content.decode()
    assert "Mieteinnahmen Garagen" in inhalt
    assert "250,00" in inhalt

    adresse = reverse("kassenbuch:bericht_pdf", args=[klein.hof.pk, 2026, 1])
    monkeypatch.setattr(pdf, "html_zu_pdf", lambda html: b"%PDF-1.7 live")
    live = angemeldet.get(adresse)
    assert (live.status_code, live["Content-Type"]) == (200, "application/pdf")
    assert "monatsbericht-HOF-2026-01.pdf" in live["Content-Disposition"]

    bericht = services.monatsbericht_erzeugen(klein.hof, JAN)
    bericht.datei.save("monatsbericht-HOF-2026-01.pdf", ContentFile(b"%PDF-1.7 gespeichert"))
    gespeichert = angemeldet.get(adresse)
    assert b"".join(gespeichert.streaming_content) == b"%PDF-1.7 gespeichert"
    assert Monatsbericht.objects.count() == 1


def test_pdf_ohne_weasyprint_sagt_warum(
    angemeldet: Client, klein: KleinerHof, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fehlt(html: str) -> bytes:
        raise pdf.PdfNichtVerfuegbar("WeasyPrint fehlt")

    monkeypatch.setattr(pdf, "html_zu_pdf", fehlt)
    antwort = angemeldet.get(reverse("kassenbuch:bericht_pdf", args=[klein.hof.pk, 2026, 1]))
    assert antwort.status_code == 503
    assert "WeasyPrint fehlt" in antwort.content.decode()
