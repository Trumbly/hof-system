"""Seiten des Abgleichs: Rückstände, Mietkonto, Handzahlung, Klärfall mit seinen Aktionen,
Zahlerregeln. Alles nur angemeldet."""

from __future__ import annotations

import datetime
from decimal import Decimal

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse

from apps.abgleich import services
from apps.abgleich.models import Handzahlung, Klaerfall, Zahlerregel, Zuordnung
from testhof import pdfs
from tests.abgleich.conftest import Abgleichhof, klaerfaelle, neue_iban, posten

D = datetime.date


def _klaerfall(hof: Abgleichhof, betrag: str = "85.00", zweck: str = "Miete") -> Klaerfall:
    buchung = hof.zahlung(D(2026, 7, 2), betrag, iban=neue_iban(), zweck=zweck)
    services.abgleichen()
    (fall,) = klaerfaelle(buchung)
    return fall


def test_seiten_nur_angemeldet(client: Client, db: None) -> None:
    antwort = client.get(reverse("abgleich:rueckstaende"))
    assert antwort.status_code == 302 and "login" in antwort["Location"]


def test_startseite_verlinkt_rueckstaende(hof: Abgleichhof, angemeldet: Client) -> None:
    """H3.11: Die Rückstände sind von der Startseite verlinkt."""
    seite = angemeldet.get(reverse("kern:startseite")).content.decode()
    assert reverse("abgleich:rueckstaende") in seite


def test_rueckstaende_und_mietkonto(hof: Abgleichhof, angemeldet: Client) -> None:
    vertrag = hof.vertrag()
    hof.zahlung(D(2026, 11, 18), "1.00", iban=neue_iban(), zweck="Stand")
    seite = angemeldet.get(reverse("abgleich:rueckstaende")).content.decode()
    assert vertrag.nummer in seite and "über 60" in seite
    seite = angemeldet.get(reverse("abgleich:mietkonto", args=[vertrag.pk])).content.decode()
    assert "Saldo" in seite and "Handzahlung erfassen" in seite


def test_klaerfall_zuordnen_ueber_die_arbeitsliste(hof: Abgleichhof, angemeldet: Client) -> None:
    """H3.6: „Zuordnen“ führt auf die Seite mit dem Vorschlag; danach ist der Posten erledigt."""
    vertrag = hof.vertrag()
    fall = _klaerfall(hof, zweck=f"Miete {vertrag.nummer}")
    arbeit = services.klaerfall_posten(fall)
    assert arbeit is not None
    liste = angemeldet.get(reverse("kern:arbeitslisten")).content.decode()
    assert "Zahlungen klären" in liste and "Zahlung ansehen" in liste

    weiter = angemeldet.post(
        reverse("kern:arbeitsposten_aktion", args=[arbeit.pk, "zahlung_zuordnen"])
    )
    assert weiter["Location"] == reverse("abgleich:zuordnen", args=[fall.pk])
    seite = angemeldet.get(weiter["Location"]).content.decode()
    assert "Vorschlag" in seite and "80 %" in seite
    antwort = angemeldet.post(weiter["Location"], {"vertrag": vertrag.pk})
    assert antwort.status_code == 302

    arbeit.refresh_from_db()
    assert arbeit.status == "erledigt"
    assert posten(vertrag)[0].status == "bezahlt"
    seite = angemeldet.get(reverse("abgleich:klaerfall", args=[fall.pk])).content.decode()
    assert "zugeordnet" in seite


def test_klaerfall_aufteilen(hof: Abgleichhof, angemeldet: Client) -> None:
    a, b = hof.vertrag(), hof.vertrag("40.00")
    fall = _klaerfall(hof, "125.00")
    url = reverse("abgleich:aufteilen", args=[fall.pk])
    assert angemeldet.get(url).status_code == 200
    daten = {
        "anteile-TOTAL_FORMS": "3",
        "anteile-INITIAL_FORMS": "0",
        "anteile-0-posten": posten(a)[0].pk,
        "anteile-0-betrag": "85.00",
        "anteile-1-posten": posten(b)[0].pk,
        "anteile-1-betrag": "40.00",
        "guthaben": "0",
    }
    antwort = angemeldet.post(url, daten)
    assert antwort.status_code == 302, antwort.content.decode()[:800]
    assert Zuordnung.objects.filter(buchung=fall.buchung).count() == 2


def test_klaerfall_regel_anlegen_und_nicht_vertragsbezogen(
    hof: Abgleichhof, angemeldet: Client
) -> None:
    vertrag = hof.vertrag()
    fall = _klaerfall(hof)
    antwort = angemeldet.post(
        reverse("abgleich:regel_aus_klaerfall", args=[fall.pk]), {"vertrag": vertrag.pk}
    )
    assert antwort.status_code == 302
    assert (
        Zahlerregel.objects.get(vertrag=vertrag).regel.gegen_iban == fall.buchung.umsatz.gegen_iban
    )

    zins = hof.zahlung(D(2026, 8, 31), "0.42", iban=neue_iban(), zweck="Zinsen")
    services.abgleichen()
    (zweiter,) = klaerfaelle(zins)
    arbeit = services.klaerfall_posten(zweiter)
    assert arbeit is not None
    angemeldet.post(reverse("kern:arbeitsposten_aktion", args=[arbeit.pk, "nicht_vertragsbezogen"]))
    zweiter.refresh_from_db()
    assert zweiter.ergebnis == "nicht_vertragsbezogen"


def test_handzahlung_mit_beleg_und_storno(hof: Abgleichhof, angemeldet: Client) -> None:
    """H3.7: Handbuchung mit Grund und Beleg über die Seite; Storno im Mietkonto."""
    vertrag = hof.vertrag()
    url = reverse("abgleich:handzahlung", args=[vertrag.pk])
    ohne = angemeldet.post(url, {"art": "bar", "datum": "2026-07-03", "betrag": "85.00"})
    assert ohne.status_code == 200 and not Handzahlung.objects.exists()
    beleg = SimpleUploadedFile(
        "quittung.pdf", pdfs.einfaches_pdf(["Quittung (erfunden)"]), content_type="application/pdf"
    )
    antwort = angemeldet.post(
        url,
        {
            "art": "bar",
            "datum": "2026-07-03",
            "betrag": "85.00",
            "grund": "Quittung",
            "beleg": beleg,
        },
    )
    assert antwort.status_code == 302
    handzahlung = Handzahlung.objects.get()
    assert handzahlung.beleg.art == "beleg"
    assert posten(vertrag)[0].status == "bezahlt"

    konto = reverse("abgleich:mietkonto", args=[vertrag.pk])
    angemeldet.post(konto, {"handzahlung": handzahlung.pk, "grund": "doppelt"})
    handzahlung.refresh_from_db()
    assert handzahlung.storniert_am is not None
    assert posten(vertrag)[0].status == "offen"


def test_zuordnung_im_mietkonto_stornieren(hof: Abgleichhof, angemeldet: Client) -> None:
    vertrag = hof.vertrag()
    iban = neue_iban()
    hof.regel(iban, vertrag=vertrag)
    hof.zahlung(D(2026, 7, 2), "85.00", iban=iban)
    services.abgleichen()
    zuordnung = Zuordnung.objects.get()

    angemeldet.post(
        reverse("abgleich:mietkonto", args=[vertrag.pk]),
        {"zuordnung": zuordnung.pk, "grund": "falsch"},
    )

    zuordnung.refresh_from_db()
    assert zuordnung.storniert_am is not None and posten(vertrag)[0].bezahlt == Decimal("0")


def test_zahlerregeln_pflegen(hof: Abgleichhof, angemeldet: Client) -> None:
    """H1.4: Zahlerregeln liegen in der Datenbank und lassen sich pflegen."""
    a, b = hof.vertrag(), hof.vertrag()
    regel = hof.regel(neue_iban())
    liste = angemeldet.get(reverse("abgleich:zahlerregeln")).content.decode()
    assert f"#{regel.pk}" in liste
    url = reverse("abgleich:zahlerregel", args=[regel.pk])
    antwort = angemeldet.post(url, {"vertraege": [a.pk, b.pk], "betrag": "170.00"})
    assert antwort.status_code == 302
    zahlerregel = Zahlerregel.objects.get(regel=regel)
    assert {v.pk for v in zahlerregel.vertraege()} == {a.pk, b.pk}
    antwort = angemeldet.post(url, {"vertrag": a.pk, "nicht_vertragsbezogen": "on"})
    assert antwort.status_code == 200 and "genau eins" in antwort.content.decode()


def test_klaerfall_bietet_an_die_zahlerregel_zu_korrigieren(
    hof: Abgleichhof, angemeldet: Client
) -> None:
    """Review 2, A11: Zeigt die Zahlerregel auf einen anderen Vertrag als den gewählten,
    bietet „Zuordnen“ an, sie zu korrigieren: Checkbox „Zahlerregel auf diesen Vertrag
    ändern“, vorangehakt, wenn sie vom Vorschlag abweicht. Danach zeigt sie auf den Vertrag."""
    falsch, richtig = hof.vertrag(), hof.vertrag("40.00")
    iban = neue_iban()
    regel = hof.regel(iban, vertrag=falsch)
    buchung = hof.zahlung(D(2026, 7, 2), "40.00", iban=iban, zweck=f"Miete {richtig.nummer}")
    services.abgleichen()
    (fall,) = klaerfaelle(buchung)
    url = reverse("abgleich:zuordnen", args=[fall.pk])

    seite = angemeldet.get(url).content.decode()
    assert "Zahlerregel auf diesen Vertrag ändern" in seite
    assert falsch.nummer in seite
    vorschlag = (fall.vorschlag or {}).get("vertrag")
    if vorschlag and int(vorschlag) != falsch.pk:
        assert 'name="zahlerregel_aendern" id="id_zahlerregel_aendern" checked' in seite

    antwort = angemeldet.post(url, {"vertrag": richtig.pk, "zahlerregel_aendern": "on"})
    assert antwort.status_code == 302, antwort.content.decode()[:800]
    zahlerregel = Zahlerregel.objects.get(regel=regel)
    assert zahlerregel.vertrag == richtig
    assert posten(richtig)[0].status == "bezahlt"


def test_klaerfall_ohne_haken_laesst_die_zahlerregel(hof: Abgleichhof, angemeldet: Client) -> None:
    """Review 2, A11: Ohne Haken bleibt die Zahlerregel, wie sie ist."""
    falsch, richtig = hof.vertrag(), hof.vertrag("40.00")
    iban = neue_iban()
    regel = hof.regel(iban, vertrag=falsch)
    buchung = hof.zahlung(D(2026, 7, 2), "40.00", iban=iban, zweck=f"Miete {richtig.nummer}")
    services.abgleichen()
    (fall,) = klaerfaelle(buchung)
    angemeldet.post(reverse("abgleich:zuordnen", args=[fall.pk]), {"vertrag": richtig.pk})
    assert Zahlerregel.objects.get(regel=regel).vertrag == falsch
