"""Testhof-Modul des Kassenbuchs (Aufgabe 9) und die Abnahme gegen den Testhof."""

from __future__ import annotations

import io
from decimal import Decimal
from typing import Any

import pytest
from django.core.management import call_command
from django.db.models import Sum

from apps.bank.models import Bankumsatz
from apps.kassenbuch import auswertung, services
from apps.kassenbuch.models import Buchung, Kategorie, Zuordnungsregel
from apps.kern.models import Arbeitsposten, Lauf
from testhof import aufbau, register, sonderfaelle
from testhof import kassenbuch as testhof_kassenbuch
from testhof.aufbau import Testhof


def test_35_kategorien_und_60_regeln(testhof: Testhof) -> None:
    """Aufgabe 9: 35 Kategorien (Entnahmen je Mandant) und 60 Regeln im Testhof."""
    assert Kategorie.objects.count() == 35
    assert Zuordnungsregel.objects.count() == 60
    entnahmen = Kategorie.objects.filter(art=Kategorie.Art.ENTNAHME)
    assert sorted(entnahmen.values_list("mandant__art", flat=True)) == sorted(
        ["gbr", "nachlass", "kg_linie", "privat"]
    )
    assert set(Kategorie.objects.values_list("art", flat=True)) == set(Kategorie.Art.values)
    assert len(testhof.kassenbuch.regeln) == 60
    assert testhof.kassenbuch.kategorien[testhof_kassenbuch.WOHNRAUM].wohnraum


def test_daten_entstehen_ueber_die_services(monkeypatch: pytest.MonkeyPatch, db: None) -> None:
    """Aufgabe 9: Das Testhof-Modul schreibt nur über apps.kassenbuch.services."""
    aufrufe: list[str] = []
    for name in ("kategorie_anlegen", "regel_anlegen", "umsatz_buchen"):
        original = getattr(services, name)

        def mitschreiben(*args: Any, _n: str = name, _f: Any = original, **kw: Any) -> Any:
            aufrufe.append(_n)
            return _f(*args, **kw)

        monkeypatch.setattr(services, name, mitschreiben)

    aufbau.aufbauen(seed=42)

    assert aufrufe.count("kategorie_anlegen") == 35
    assert aufrufe.count("regel_anlegen") == 60
    assert aufrufe.count("umsatz_buchen") == 1


def test_sonderfaelle_angemeldet() -> None:
    module = sonderfaelle.erzeugende_module()
    assert module["sammelrechnung_zwei_objekte"] == ["kassenbuch"]
    assert module["wohnraummiete_selbes_konto"] == ["kassenbuch"]
    assert register.laden()["kassenbuch"].abhaengig_von == ("kern", "bank")


def test_sonderfall_sammelrechnung_zwei_objekte(testhof: Testhof) -> None:
    """Sonderfall „Sammelrechnung für zwei Objekte“: ein Umsatz, zwei Buchungen auf HOF und
    HALLE, zusammen genau der Betrag des Umsatzes."""
    umsatz, buchungen = testhof.kassenbuch.sammelrechnung
    assert [b.objekt.kuerzel for b in buchungen] == ["HOF", "HALLE"]
    assert {b.mandant.kuerzel for b in buchungen} == {"GBR", "KG"}
    assert sum(b.betrag for b in buchungen) == umsatz.betrag
    assert services.ist_gebucht(umsatz)


def _einordnen() -> Lauf:
    call_command("kassenbuch_einordnen", stdout=io.StringIO())
    lauf = Lauf.objects.filter(name="kassenbuch_einordnen").first()
    assert lauf is not None
    return lauf


def test_nach_dem_lauf_ist_jeder_umsatz_gebucht_oder_arbeitsposten(testhof: Testhof) -> None:
    """Abnahme Schritt 2: Im Testhof hat nach dem Lauf jeder Umsatz Kategorie, Objekt und
    Mandant oder steht als Arbeitsposten. Ein zweiter Lauf ändert nichts (idempotent)."""
    lauf = _einordnen()

    gesamt = Bankumsatz.objects.count()
    assert lauf.status == "erfolgreich"
    assert (lauf.gebucht, lauf.arbeitsposten, lauf.fehler) == (gesamt - 1 - 49, 49, 0)

    mit_posten = services.offene_posten_umsatz_ids()
    summen = dict(
        services.gueltige_buchungen()
        .values("umsatz_id")
        .annotate(s=Sum("betrag"))
        .values_list("umsatz_id", "s")
    )
    for umsatz_id, betrag in Bankumsatz.objects.values_list("pk", "betrag"):
        gebucht = summen.get(umsatz_id) == betrag
        assert gebucht != (umsatz_id in mit_posten), f"Umsatz #{umsatz_id}"
    unvollstaendig = (
        Buchung.objects.filter(kategorie__isnull=True)
        | Buchung.objects.filter(objekt__isnull=True)
        | Buchung.objects.filter(mandant__isnull=True)
    )
    assert not unvollstaendig.exists()

    zweiter = _einordnen()
    assert (zweiter.gebucht, zweiter.arbeitsposten, zweiter.fehler) == (0, 0, 0)


def test_testhof_arbeitsposten_und_sonderfaelle_nach_dem_lauf(testhof: Testhof) -> None:
    """Die erwarteten Lücken: Material auf Vivid (keine Regel), Versicherung auf VR
    (mehrdeutig) und die Rückbuchung (mit Hinweis auf den Eingang). Wohnraum ist gebucht
    und markiert, Entnahmen stehen bei der GbR."""
    _einordnen()
    posten = Arbeitsposten.objects.filter(art=services.ART_EINORDNEN).order_by("pk")
    umsaetze = {p.bezug_id: p for p in posten}
    zwecke = list(
        Bankumsatz.objects.filter(pk__in=umsaetze).values_list("verwendungszweck", flat=True)
    )
    assert sum(z.startswith("Material Baumarkt") for z in zwecke) == 24
    assert sum(z.startswith("Versicherungsbeitrag") for z in zwecke) == 24
    eingang, zurueck = testhof.bank.rueckbuchung
    assert f"Rückbuchung zu Umsatz #{eingang.pk}" in umsaetze[zurueck.pk].hinweis
    mehrdeutig = next(p for p in posten if "gleicher Priorität" in p.hinweis)
    assert mehrdeutig.aktionen == list(services.AKTIONEN_EINORDNEN)

    wohnraum = Buchung.objects.filter(umsatz__gegen_iban=testhof.bank.wohnraum_iban)
    assert wohnraum.count() == 24
    assert all(b.wohnraum for b in wohnraum)

    beginn, ende = testhof.bank.beginn, testhof.bank.ende
    (mandant, summe), *rest = auswertung.entnahmen_je_mandant(beginn, ende)
    assert mandant.kuerzel == "GBR" and not rest
    erwartet = Bankumsatz.objects.filter(gegen_iban=testhof.bank.entnahme_iban).aggregate(
        s=Sum("betrag")
    )["s"]
    assert summe == erwartet and summe > Decimal("0")
