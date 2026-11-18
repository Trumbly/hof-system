"""Standardablauf 2: Sollstellung und Zahlungsabgleich (Prozessliste), mit dem Testhof.

Auslöser: Monatsbeginn und jeder Bankabruf. Ergebnis: Jeder Posten ist bezahlt, offen oder
in Klärung.

1. System erzeugt zum Monatsbeginn die Posten (Lauf ``soll_monat``).
2. System holt die Umsätze und ordnet sie über Zahlerregel und Verwendungszweck zu; der
   Zweck wird immer geprüft (``kassenbuch_einordnen``, dann ``abgleich``).
3. System bucht sichere Treffer: bezahlt, teilweise bezahlt oder überzahlt (Guthaben).
4. System legt den Rest in die Arbeitsliste „Zahlungen klären“.
5. Du ordnest zu, legst eine Regel an oder stellst zurück.
6. System führt je Vertrag das Konto mit Saldo; eine Lücke in den Bankdaten gilt nie als
   Rückstand.
"""

from __future__ import annotations

import datetime
import io
from decimal import Decimal

import pytest
from django.core.management import call_command
from django.test import Client
from django.urls import reverse

from apps.abgleich import services
from apps.abgleich.models import Klaerfall
from apps.kern.models import Arbeitsposten, Lauf
from apps.soll import services as soll
from apps.soll.models import Sollposten
from testhof import abgleich as testhof_abgleich
from testhof.aufbau import Testhof

D = datetime.date
NOV = D(2026, 11, 1)


@pytest.fixture(autouse=True)
def _beginn(settings: object) -> None:
    settings.ABGLEICH_BEGINN = testhof_abgleich.BEGINN  # type: ignore[attr-defined]


def _lauf(name: str, *argumente: str) -> Lauf:
    call_command(name, *argumente, stdout=io.StringIO())
    lauf = Lauf.objects.filter(name=name).first()
    assert lauf is not None and lauf.status == "erfolgreich", name
    return lauf


def test_standardablauf_2_sollstellung_und_zahlungsabgleich(
    testhof: Testhof, angemeldet: Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Standardablauf 2 und Abnahme Schritt 6: Nach Sollstellung, Bankabruf, Einordnen und
    Abgleich ist jeder Posten bezahlt, offen oder in Klärung; Klärfälle löst die Arbeitsliste;
    die Rückstände sind danach aktuell."""
    monkeypatch.setattr(services, "heute", lambda: NOV)
    monkeypatch.setattr(soll, "heute", lambda: NOV)
    vertraege = testhof.abgleich.sonderfaelle

    # 1. Sollstellung zum Monatsbeginn: idempotent, die Posten für November stehen.
    _lauf("soll_monat", "--stichtag", NOV.isoformat())
    assert Sollposten.objects.filter(von=NOV, art="miete").exists()

    # 2. und 3. Einordnen und Abgleich: sichere Treffer gebucht.
    _lauf("kassenbuch_einordnen")
    abgleich = _lauf("abgleich")
    assert abgleich.gebucht > 150
    posten = Sollposten.objects.filter(
        bis__gte=testhof_abgleich.BEGINN, faellig_am__lt=NOV
    ).exclude(status="storniert")
    klaerung = services.vertraege_in_klaerung()
    for p in posten:
        assert p.status in ("bezahlt", "teilweise", "offen")
        if p.status != "bezahlt":
            # offen nur bei Teilzahlung (Dauerauftrag) oder in Klärung
            assert p.vertrag_id in klaerung or p.vertrag in vertraege["dauerauftrag_alte_miete"]
    ueberzahlt = vertraege["ueberzahlung_guthaben"][0]
    assert Sollposten.objects.get(vertrag=ueberzahlt, von=NOV, art="miete").status == "teilweise"

    # 4. Der Rest liegt in der Arbeitsliste: Zweck widerspricht der Regel, Rückbuchung.
    seite = angemeldet.get(reverse("kern:arbeitslisten")).content.decode()
    assert "Zahlungen klären" in seite
    zweck = Klaerfall.objects.get(art=Klaerfall.Art.ZWECK)
    posten_zweck = services.klaerfall_posten(zweck)
    assert posten_zweck is not None and posten_zweck.status == "offen"

    # 5. Du ordnest zu: Aktion „Zuordnen“ führt auf die Seite mit dem Vorschlag.
    weiter = angemeldet.post(
        reverse("kern:arbeitsposten_aktion", args=[posten_zweck.pk, "zahlung_zuordnen"])
    )
    assert weiter.status_code == 302
    seite = angemeldet.get(weiter["Location"]).content.decode()
    assert "Vorschlag" in seite
    antwort = angemeldet.post(
        weiter["Location"], {"vertrag": zweck.vorschlag["vertrag"], "rest_als_guthaben": "on"}
    )
    assert antwort.status_code == 302, antwort.content.decode()[:500]
    posten_zweck.refresh_from_db()
    assert posten_zweck.status == "erledigt"

    #    … und bestätigst die Rückbuchung (der Posten ist wieder offen).
    rueck = services.klaerfall_posten(Klaerfall.objects.get(art=Klaerfall.Art.RUECKBUCHUNG))
    assert rueck is not None
    angemeldet.post(
        reverse("kern:arbeitsposten_aktion", args=[rueck.pk, "rueckbuchung_bestaetigen"])
    )
    rueck.refresh_from_db()
    assert rueck.status == "erledigt"
    assert not Arbeitsposten.objects.filter(art=services.ART_KLAEREN, status="offen").exists()

    # 6. Konto je Vertrag mit Saldo; Rückstände aktuell. Der falsche Zweck ist geklärt.
    zweck_vertrag = vertraege["falsche_einheit_im_verwendungszweck"][0]
    stand = services.rueckstaende()
    assert zweck_vertrag.pk not in {r.vertrag.pk for r in stand.mit_rueckstand}
    assert not any(r.in_klaerung for r in stand.vertraege)
    seite = angemeldet.get(reverse("abgleich:mietkonto", args=[zweck_vertrag.pk])).content
    assert b"Saldo" in seite
    seite = angemeldet.get(reverse("abgleich:rueckstaende")).content.decode()
    assert vertraege["dauerauftrag_alte_miete"][0].nummer in seite

    # Eine Lücke in den Bankdaten gilt nie als Rückstand: Nach dem 31.10. fehlen Daten, die
    # Novembermiete ist am 15.11. „nicht prüfbar“, nicht rückständig.
    spaeter = services.rueckstaende(D(2026, 11, 15))
    assert spaeter.datenstand == testhof.bank.ende
    assert spaeter.summe_nach("nicht_pruefbar") > Decimal("0")
    nov_offen = Sollposten.objects.filter(von=NOV, art="miete", status="offen").count()
    assert nov_offen > 0
