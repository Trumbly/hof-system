"""Abnahme Schritt 6 gegen den Testhof: jeder Sonderfall richtig zugeordnet oder in
„Zahlungen klären“, unsichere Treffer nie gebucht, Rückstände nach dem Lauf aktuell."""

from __future__ import annotations

import io
import re
from collections.abc import Iterator
from decimal import Decimal
from typing import Any

import pytest
from django.core.management import call_command
from django.db import transaction
from django.test import override_settings

from apps.abgleich import services
from apps.abgleich.models import Guthaben, Handzahlung, Klaerfall, Zahlerregel, Zuordnung
from apps.bank.models import Bankumsatz
from apps.kassenbuch.models import Buchung
from apps.kern.models import Lauf
from apps.soll import services as soll
from apps.soll.models import Sollposten
from apps.stammdaten.models import Vertrag
from testhof import abgleich as testhof_abgleich
from testhof import pruefsumme, register, sonderfaelle
from testhof.aufbau import STICHTAG, Testhof
from tests.abgleich.conftest import laeufe

E = Decimal


#: Stand vor den Läufen (vom Modul-Fixture festgehalten).
VORHER: dict[str, int] = {}


@pytest.fixture(scope="module")
def _abgeglichen_modul(_testhof_sitzung: Testhof, django_db_blocker: Any) -> Iterator[int]:
    """Einordnen und Abgleich einmal je Modul (beide Läufe zusammen dauern im Testhof ein
    paar Dutzend Sekunden), in einem Sicherungspunkt, der danach zurückgerollt wird. Die
    Tests laufen darin wie sonst in eigenen Sicherungspunkten."""
    with (
        django_db_blocker.unblock(),
        pytest.MonkeyPatch.context() as patch,
        override_settings(ABGLEICH_BEGINN=testhof_abgleich.BEGINN),
    ):
        patch.setattr(services, "heute", lambda: STICHTAG)
        punkt = transaction.savepoint()
        try:
            VORHER["mit_rueckstand"] = len(services.rueckstaende().mit_rueckstand)
            _, lauf = laeufe()
            yield lauf.pk
        finally:
            transaction.savepoint_rollback(punkt)


@pytest.fixture
def abgeglichen(_abgeglichen_modul: int, testhof: Testhof, stichtag: object) -> Lauf:
    """Der Testhof nach Einordnen und Abgleich (Stichtag 01.11.2026)."""
    return Lauf.objects.get(pk=_abgeglichen_modul)


def _fall(name: str, testhof: Testhof) -> list[Vertrag]:
    return testhof.abgleich.sonderfaelle[name]  # type: ignore[no-any-return]


def _posten(vertrag: Vertrag, *monate: int) -> list[Sollposten]:
    qs = (
        Sollposten.objects.filter(vertrag=vertrag, von__year=2026, art="miete")
        .exclude(status="storniert")
        .order_by("von")
    )
    return [p for p in qs if not monate or p.von.month in monate]


def _buchungen(vertrag: Vertrag) -> list[Buchung]:
    """Buchungen der Zahlungen ab Juli, deren Zahlerregel diesen Vertrag nennt."""
    regel = Zahlerregel.objects.get(vertrag=vertrag)
    return list(
        Buchung.objects.filter(
            regel=regel.regel, umsatz__buchungsdatum__gte=testhof_abgleich.BEGINN
        )
        .select_related("umsatz")
        .order_by("umsatz__buchungsdatum")
    )


def test_sonderfaelle_angemeldet() -> None:
    module = sonderfaelle.erzeugende_module()
    for schluessel in testhof_abgleich.SONDERFAELLE:
        assert "abgleich" in module[schluessel], schluessel
    assert register.laden()["abgleich"].abhaengig_von == ("bank", "belege", "kassenbuch", "soll")
    for schluessel in testhof_abgleich.AUCH_GENUTZT:
        assert module[schluessel], schluessel
    # Alle Sonderfälle des Schritts 6 haben ein Modul.
    schritt_6 = [f.schluessel for f in sonderfaelle.SONDERFAELLE if 6 in f.schritte]
    assert all(module[s] for s in schritt_6)


def test_lauf_im_testhof(abgeglichen: Lauf) -> None:
    """Abnahme Schritt 6: Der Lauf bucht die sicheren Treffer; im Testhof bleiben genau zwei
    Klärfälle (falscher Zweck, Rückbuchung). Ein zweiter Lauf ändert nichts."""
    assert abgeglichen.status == "erfolgreich"
    assert abgeglichen.gebucht > 150
    assert abgeglichen.arbeitsposten == 2
    assert sorted(Klaerfall.objects.values_list("art", flat=True)) == ["rueckbuchung", "zweck"]
    for wort in ("€", "Beispiel"):
        assert wort not in abgeglichen.meldung

    _, zweiter = laeufe()
    assert (zweiter.gebucht, zweiter.arbeitsposten, zweiter.fehler) == (0, 0, 0)


def test_sonderfall_sammelzahler(testhof: Testhof, abgeglichen: Lauf) -> None:
    """Abnahme Schritt 6, Sonderfall „Sammelzahler“: eine Überweisung für drei Verträge."""
    vertraege = _fall("sammelzahler", testhof)
    regel = Zahlerregel.objects.get(sammelzeilen__vertrag=vertraege[0])
    assert {v.pk for v in regel.vertraege()} == {v.pk for v in vertraege}
    for buchung in Buchung.objects.filter(
        regel=regel.regel, umsatz__buchungsdatum__gte="2026-07-01"
    ):
        assert services.offener_betrag(buchung) == 0
        assert {z.sollposten.vertrag_id for z in buchung.zuordnungen.all()} == {
            v.pk for v in vertraege
        }
    for vertrag in vertraege:
        assert {p.status for p in _posten(vertrag, 7, 8, 9, 10)} == {"bezahlt"}


def test_sonderfall_fremder_zahler(testhof: Testhof, abgeglichen: Lauf) -> None:
    """Abnahme Schritt 6, Sonderfall „Fremder Zahler“: Die Zahlerpartei des Vertrags
    überweist; die Zahlerregel ordnet richtig zu."""
    (vertrag,) = _fall("fremder_zahler", testhof)
    mieter = vertrag.parteien_mit_rolle("mieter")[0]
    zahler = vertrag.parteien_mit_rolle("zahler")[0]
    assert zahler != mieter
    buchungen = _buchungen(vertrag)
    assert buchungen and all(services.offener_betrag(b) == 0 for b in buchungen)
    assert {p.status for p in _posten(vertrag, 7, 8, 9, 10)} == {"bezahlt"}


def test_sonderfall_falscher_zweck(testhof: Testhof, abgeglichen: Lauf) -> None:
    """Abnahme Schritt 6, Sonderfall „Verwendungszweck nennt die falsche Einheit“: nicht
    gebucht, sondern Klärfall mit Vorschlag."""
    vertrag, andere = _fall("falsche_einheit_im_verwendungszweck", testhof)
    (fall,) = Klaerfall.objects.filter(art=Klaerfall.Art.ZWECK)
    assert fall.vorschlag["vertrag"] == vertrag.pk
    assert andere.pk in fall.vorschlag["alternativen"]
    assert not fall.buchung.zuordnungen.exists()
    assert andere.einheiten.get().nw_id in fall.buchung.umsatz.verwendungszweck


def test_sonderfall_ueberzahlung(testhof: Testhof, abgeglichen: Lauf) -> None:
    """Abnahme Schritt 6, Sonderfall „Überzahlung mit Guthaben für den Folgemonat“."""
    (vertrag,) = _fall("ueberzahlung_guthaben", testhof)
    august = _buchungen(vertrag)[1]
    erstes = Guthaben.objects.filter(buchung=august).order_by("pk").first()
    assert erstes is not None and erstes.betrag == testhof_abgleich.MEHR
    september = _posten(vertrag, 9)[0]
    assert erstes.verrechnet_mit == september
    assert {p.status for p in _posten(vertrag, 7, 8, 9, 10)} == {"bezahlt"}
    november = _posten(vertrag, 11)[0]
    assert (november.status, november.bezahlt) == ("teilweise", testhof_abgleich.MEHR)
    assert services.guthaben_summe(vertrag) == 0


def test_sonderfall_barzahlung(testhof: Testhof, abgeglichen: Lauf) -> None:
    """Abnahme Schritt 6, Sonderfall „Barzahlung ohne Bankumsatz“: Handzahlung mit Grund und
    Beleg; der Posten ist bezahlt."""
    (vertrag,) = _fall("barzahlung", testhof)
    handzahlung = Handzahlung.objects.get(vertrag=vertrag)
    assert handzahlung.art == "bar" and handzahlung.grund and handzahlung.beleg
    assert _posten(vertrag, 9)[0].status == "bezahlt"
    assert Zuordnung.objects.filter(handzahlung=handzahlung).count() == 1
    assert {p.status for p in _posten(vertrag, 7, 8, 9, 10)} == {"bezahlt"}


def test_sonderfall_teilzahlung_mit_nachzahlung(testhof: Testhof, abgeglichen: Lauf) -> None:
    """Abnahme Schritt 6, Sonderfall „Teilzahlung mit späterer Nachzahlung“."""
    (vertrag,) = _fall("teilzahlung_nachzahlung", testhof)
    august = _posten(vertrag, 8)[0]
    erste = august.zuordnungen.order_by("datum").first()
    assert erste is not None and erste.betrag == (august.betrag * testhof_abgleich.ANTEIL).quantize(
        E("0.01")
    )
    assert august.zuordnungen.count() == 2  # Rest mit der Nachzahlung im September
    assert {p.status for p in _posten(vertrag, 7, 8, 9, 10)} == {"bezahlt"}


def test_sonderfall_dauerauftrag_alte_miete(testhof: Testhof, abgeglichen: Lauf) -> None:
    """Abnahme Schritt 6, Sonderfall „Dauerauftrag mit alter Miete“: als Teilzahlung gebucht,
    die Differenz ist Rückstand. Review 1, Punkt 1: Dazu kommt der Vortrag aus dem alten
    System (sechs Monate Differenz seit der Staffel); die Zahlungen tilgen ihn zuerst."""
    (vertrag,) = _fall("dauerauftrag_alte_miete", testhof)
    buchungen = _buchungen(vertrag)
    assert len(buchungen) == 4 and all(services.offener_betrag(b) == 0 for b in buchungen)
    alt = buchungen[0].betrag
    neu = _posten(vertrag, 7)[0].betrag
    assert alt < neu
    vortrag = soll.vortrag(vertrag)
    assert vortrag is not None and vortrag.betrag == 6 * (neu - alt)
    vortrag.refresh_from_db()
    assert vortrag.offen == 0  # älteste Forderung zuerst getilgt
    offen = sum((p.offen for p in _posten(vertrag, 7, 8, 9, 10)), E("0"))
    assert offen == 10 * (neu - alt)
    assert soll.offen(vertrag, STICHTAG).summe_offen == offen
    (eintrag,) = [r for r in services.rueckstaende().vertraege if r.vertrag == vertrag]
    assert eintrag.summe == offen


def test_sonderfall_rueckbuchung(testhof: Testhof, abgeglichen: Lauf) -> None:
    """Abnahme Schritt 6, Sonderfall „Rückbuchung“: Klärfall; die Zahlung wird nicht als
    Eingang gezählt."""
    (vertrag,) = _fall("rueckbuchung", testhof)
    (fall,) = Klaerfall.objects.filter(art=Klaerfall.Art.RUECKBUCHUNG)
    assert fall.gegen_umsatz is not None
    assert fall.gegen_umsatz.richtung == Bankumsatz.Richtung.AUSGANG
    assert fall.gegen_umsatz.gegen_iban == fall.buchung.umsatz.gegen_iban
    assert not fall.buchung.zuordnungen.exists()
    assert fall.buchung.regel.zahlerregel.vertrag == vertrag  # type: ignore[union-attr]


def test_sonderfall_jahreszahler(testhof: Testhof, abgeglichen: Lauf) -> None:
    """Abnahme Schritt 6, Sonderfall „Jahreszahler“: Die Jahresmiete deckt den Jahresposten."""
    (vertrag,) = _fall("jahreszahler", testhof)
    (jahr,) = _posten(vertrag)
    assert (jahr.von.month, jahr.bis.month) == (1, 12)
    assert jahr.status == "bezahlt"


def test_sonderfall_wohnraum_nie_im_abgleich(testhof: Testhof, abgeglichen: Lauf) -> None:
    """Abnahme Schritt 6, Sonderfall „Wohnraummiete auf demselben Konto“: im Kassenbuch
    gebucht, nie zugeordnet und kein Klärfall."""
    wohnraum = Buchung.objects.filter(umsatz__gegen_iban=testhof.bank.wohnraum_iban)
    assert wohnraum.count() == 24 and all(b.wohnraum for b in wohnraum)
    assert not Zuordnung.objects.filter(buchung__in=wohnraum).exists()
    assert not Klaerfall.objects.filter(buchung__in=wohnraum).exists()


def test_pacht_ohne_vertrag_nicht_vertragsbezogen(testhof: Testhof, abgeglichen: Lauf) -> None:
    pacht = Zahlerregel.objects.get(regel__bezeichnung=testhof_abgleich.PACHTREGEL)
    assert pacht.nicht_vertragsbezogen
    assert not Klaerfall.objects.filter(buchung__regel=pacht.regel).exists()


def test_rueckstaende_nach_dem_lauf_aktuell(testhof: Testhof, abgeglichen: Lauf) -> None:
    """Abnahme Schritt 6: Die Rückstände sind nach dem Lauf aktuell (Standardablauf 2)."""
    assert VORHER["mit_rueckstand"] > 30  # vor dem Abgleich ist fast alles offen

    nachher = services.rueckstaende()
    erwartet = {
        testhof.abgleich.sonderfaelle[name][0].nummer
        for name in (
            "dauerauftrag_alte_miete",
            "falsche_einheit_im_verwendungszweck",
            "rueckbuchung",
        )
    }
    assert {r.vertrag.nummer for r in nachher.mit_rueckstand} == erwartet
    zweck = testhof.abgleich.sonderfaelle["falsche_einheit_im_verwendungszweck"][0]
    assert next(r for r in nachher.vertraege if r.vertrag == zweck).in_klaerung
    assert nachher.datenstand == testhof.bank.ende
    zahlen = nachher.kennzahlen()
    assert zahlen["vertraege_mit_rueckstand"] == 3
    assert zahlen["summe"] == nachher.summe > 0


def test_rueckrechnung_in_einer_kopie(testhof: Testhof, abgeglichen: Lauf) -> None:
    """Abnahme Schritt 6: Die Rückrechnung Juli bis Dezember 2026 läuft im Probelauf: Sie
    nennt je Vertrag den Stand (bezahlt, offen, in Klärung) mit Zählern und NW-IDs, ohne
    Namen und Beträge; danach ist die Datenbank unverändert (gleiche Prüfsumme)."""
    vorher = pruefsumme.pruefsumme()
    laeufe = Lauf.objects.count()

    ausgabe = io.StringIO()
    call_command("abgleich_rueckrechnung", "--von", "2026-07", "--bis", "2026-12", stdout=ausgabe)
    text = ausgabe.getvalue()

    assert pruefsumme.pruefsumme() == vorher
    assert Lauf.objects.count() == laeufe
    assert "in einer Kopie; nichts gespeichert" in text
    assert re.search(r"in Klärung [1-9]", text)
    # Frisch gerechnet kommt dasselbe heraus wie der Lauf.
    assert "Abweichung zum aktuellen Stand: 0" in text
    zeilen = [z for z in text.splitlines() if z.startswith("  V-")]
    assert len(zeilen) >= 40
    assert all(z.rsplit(": ", 1)[1] in ("bezahlt", "offen", "in Klärung") for z in zeilen)
    assert "NW-IDs offen:" in text and "Abweichung zum aktuellen Stand:" in text
    for vertrag in Vertrag.objects.prefetch_related("vertragsparteien__partei")[:10]:
        for vp in vertrag.vertragsparteien.all():
            if vp.partei.nachname:
                assert vp.partei.nachname not in text
    assert "€" not in text
