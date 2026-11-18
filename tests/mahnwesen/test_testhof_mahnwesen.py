"""Abnahme Schritt 7 gegen den Testhof: Stufenfolge und Fristen je Stufe, Sperren durch
Klärfall und Rechtsfall, Probebetrieb der Kündigung, nichts mit Wohnraum-Bezug.

Der Testhof gleicht nicht selbst ab: Einordnen und Abgleich laufen einmal je Modul (in einem
Sicherungspunkt, wie in ``tests/abgleich/test_testhof_abgleich.py``), danach der Mahnlauf
über die Montage ab dem 02.11.2026 (Bankdaten bis 31.10.2026)."""

from __future__ import annotations

import datetime
from collections.abc import Iterator
from typing import Any

import pytest
from django.db import transaction
from django.test import override_settings

from apps.abgleich import services as abgleich
from apps.abgleich.models import Klaerfall
from apps.dokumente.models import Schreiben
from apps.dokumente.vorlagenarten import Versandweg
from apps.kassenbuch.models import Buchung
from apps.kern.models import Arbeitsposten, Benutzer
from apps.mahnwesen import services
from apps.mahnwesen.models import Mahnfall, Mahnschritt, Mahnsperre
from apps.stammdaten.models import Einheit, Vertrag
from testhof import abgleich as testhof_abgleich
from testhof import sonderfaelle
from testhof.aufbau import STICHTAG, Testhof
from tests.abgleich.conftest import laeufe
from tests.mahnwesen.conftest import fall_zu, lauf, letzter, mahnposten, schritte, senden, zustellen

D = datetime.date
M1, M2, M3, M4 = D(2026, 11, 2), D(2026, 11, 9), D(2026, 11, 16), D(2026, 11, 23)


@pytest.fixture(scope="module")
def _abgeglichen_modul(_testhof_sitzung: Testhof, django_db_blocker: Any) -> Iterator[None]:
    with (
        django_db_blocker.unblock(),
        pytest.MonkeyPatch.context() as patch,
        override_settings(ABGLEICH_BEGINN=testhof_abgleich.BEGINN),
    ):
        patch.setattr(abgleich, "heute", lambda: STICHTAG)
        punkt = transaction.savepoint()
        try:
            laeufe()
            yield
        finally:
            transaction.savepoint_rollback(punkt)


@pytest.fixture
def hof(_abgeglichen_modul: None, testhof: Testhof) -> Testhof:
    """Der Testhof nach Einordnen und Abgleich (Stichtag 01.11.2026)."""
    return testhof


def _vertrag(hof: Testhof, rolle: str) -> Vertrag:
    return hof.mahnwesen.vertraege[rolle]  # type: ignore[no-any-return]


def _benutzer(hof: Testhof) -> Benutzer:
    return hof.kern.benutzer  # type: ignore[no-any-return]


def _rueckbuchung_september_oktober(vertrag: Vertrag) -> None:
    """Die Bank holt die Zahlungen für September und Oktober zurück: Danach sind zwei
    Monatsmieten Rückstand (Fall „Kündigung“ im Testhof)."""
    buchungen = Buchung.objects.filter(
        zuordnungen__sollposten__vertrag=vertrag,
        zuordnungen__storniert_am__isnull=True,
        umsatz__buchungsdatum__gte=D(2026, 9, 1),
    ).distinct()
    assert buchungen.count() == 2
    for buchung in buchungen:
        abgleich.buchung_loesen(buchung, "Rücklastschrift (Testhof)")


def _bis_abmahnung(hof: Testhof) -> Vertrag:
    vertrag = _vertrag(hof, "stufen")
    benutzer = _benutzer(hof)
    _rueckbuchung_september_oktober(vertrag)
    lauf(M1)
    senden(letzter(vertrag).schreiben, benutzer, M1)  # type: ignore[arg-type]
    lauf(M2)
    zustellen(senden(letzter(vertrag).schreiben, benutzer, M2), M2 + datetime.timedelta(days=1))  # type: ignore[arg-type]
    lauf(M3)
    assert letzter(vertrag).stufe.schluessel == "mahnung"  # Frist der Mahnung läuft bis 20.11.
    lauf(M4)
    return vertrag


def test_testhof_stufe_erinnerung(hof: Testhof) -> None:
    """Abnahme Schritt 7, Stufe Erinnerung im Testhof: Der Dauerauftrag mit alter Miete
    (Rest offen seit Oktober) bekommt am ersten Montag die Erinnerung per Mail; die beiden
    anderen Rückstände sind gesperrt (Klärfall, Rechtsfall)."""
    ergebnis = lauf(M1)
    vertrag = _vertrag(hof, "stufen")
    (schritt,) = schritte(vertrag)
    assert schritt.stufe.schluessel == "erinnerung"
    assert schritt.schreiben is not None and schritt.schreiben.versandweg == Versandweg.MAIL
    assert {f.vertrag.nummer: f.status for f in Mahnfall.objects.select_related("vertrag")} == {
        vertrag.nummer: "offen",
        _vertrag(hof, "klaerfall").nummer: "gesperrt",
        _vertrag(hof, "rechtsfall").nummer: "gesperrt",
    }
    assert (ergebnis.faelle_neu, ergebnis.schreiben, ergebnis.gesperrt) == (3, 1, 2)
    assert len(mahnposten()) == 1


def test_testhof_stufe_mahnung(hof: Testhof) -> None:
    """Abnahme Schritt 7, Stufe Mahnung im Testhof: eine Woche nach der gesendeten
    Erinnerung (Zahlungsziel sieben Tage, mehr als 14 Tage nach Fälligkeit), mit zehn Tagen
    Frist."""
    vertrag = _vertrag(hof, "stufen")
    lauf(M1)
    erinnerung = letzter(vertrag)
    senden(erinnerung.schreiben, _benutzer(hof), M1)  # type: ignore[arg-type]
    lauf(M1 + datetime.timedelta(days=6))
    assert letzter(vertrag).pk == erinnerung.pk

    lauf(M2)
    mahnung = letzter(vertrag)
    assert mahnung.stufe.schluessel == "mahnung"
    assert mahnung.schreiben is not None and mahnung.schreiben.einseitige_erklaerung
    assert "19.11.2026" in mahnung.schreiben.inhalt_html  # zehn Tage Frist
    assert fall_zu(vertrag).stufe_belegt_am is None


def test_testhof_stufe_abmahnung(hof: Testhof) -> None:
    """Abnahme Schritt 7, Stufe Abmahnung im Testhof: bei der zweiten offenen Monatsmiete,
    frühestens zehn Tage nach der zugestellten Mahnung, als Brief."""
    vertrag = _bis_abmahnung(hof)
    abmahnung = letzter(vertrag)
    assert abmahnung.stufe.schluessel == "abmahnung" and abmahnung.stichtag == M4
    assert abmahnung.schreiben is not None
    assert abmahnung.schreiben.versandweg == Versandweg.SELBST
    assert services.lage_von(vertrag, M4).offene_monatsmieten == 3


def test_testhof_stufe_kuendigung_im_probebetrieb(hof: Testhof) -> None:
    """Abnahme Schritt 7, Stufe Kündigung im Testhof: ab zwei Monatsmieten Rückstand,
    frühestens 14 Tage nach der zugestellten Abmahnung. Der Testhof hat absichtlich keine
    Vorlage „Fristlose Kündigung“: Probebetrieb, nur der Arbeitsposten."""
    vertrag = _bis_abmahnung(hof)
    zustellen(senden(letzter(vertrag).schreiben, _benutzer(hof), M4), D(2026, 11, 24))  # type: ignore[arg-type]
    assert services.lage_von(vertrag, M4).rueckstand_in_monatsmieten >= 2

    lauf(D(2026, 12, 7))  # Frist der Abmahnung bis 08.12.
    assert letzter(vertrag).stufe.schluessel == "abmahnung"
    lauf(D(2026, 12, 14))
    kuendigung = letzter(vertrag)
    assert kuendigung.stufe.schluessel == "kuendigung"
    assert kuendigung.probebetrieb and kuendigung.schreiben is None
    posten = Arbeitsposten.objects.get(pk=kuendigung.arbeitsposten_id)
    assert "Probebetrieb: Vorlage fehlt" in posten.hinweis
    assert "vor Versand zum Anwalt" in posten.hinweis


def test_testhof_rechtsfall_mit_mahnsperre(hof: Testhof) -> None:
    """Sonderfall „Rechtsfall mit Mahnsperre“ (Abnahme Schritt 7: Sperren greifen): Für den
    Rechtsfall geht keine Mahnung hinaus, auch wenn sein Klärfall geklärt ist."""
    vertrag = _vertrag(hof, "rechtsfall")
    assert hof.mahnwesen.rechtsfall.grund == Mahnsperre.Grund.RECHTSFALL
    assert sonderfaelle.erzeugende_module()["rechtsfall_mahnsperre"] == ["mahnwesen"]

    lauf(M1)
    fall = fall_zu(vertrag)
    assert fall.status == Mahnfall.Status.GESPERRT
    assert "Rechtsfall" in fall.sperrgrund and "Klärfall" in fall.sperrgrund

    (klaerfall,) = Klaerfall.objects.filter(art=Klaerfall.Art.RUECKBUCHUNG)
    abgleich.rueckbuchung_bestaetigen(klaerfall)
    for montag in (M2, M3, M4):
        lauf(montag)
    fall.refresh_from_db()
    assert fall.status == Mahnfall.Status.GESPERRT
    assert fall.sperrgrund == f"Rechtsfall (Sperre #{hof.mahnwesen.rechtsfall.pk})"
    assert not Mahnschritt.objects.filter(fall__vertrag=vertrag).exists()
    assert services.lage_von(vertrag, M4).rueckstand > 0


def test_testhof_klaerfall_sperrt_und_geklaert_ist_bezahlt(hof: Testhof) -> None:
    """Abnahme Schritt 7: Ein offener Klärfall sperrt automatisch. Ordnet der Benutzer die
    Zahlung zu, ist der Posten bezahlt: Der Fall schließt sich, die Sperre fällt."""
    vertrag = _vertrag(hof, "klaerfall")
    lauf(M1)
    assert fall_zu(vertrag).sperrgrund.startswith("Klärfall")

    (klaerfall,) = [
        k
        for k in Klaerfall.objects.filter(ergebnis="")
        if vertrag.pk
        in {int(i) for i in (k.vorschlag or {}).get("alternativen", [])}
        | ({int(k.vorschlag["vertrag"])} if (k.vorschlag or {}).get("vertrag") else set())
    ]
    abgleich.klaerfall_zuordnen(klaerfall, vertrag)
    lauf(M2)
    fall = Mahnfall.objects.get(vertrag=vertrag)
    assert fall.status == Mahnfall.Status.ERLEDIGT
    assert not services.gueltige_sperren(M2).filter(vertrag=vertrag).exists()
    assert not Mahnschritt.objects.filter(fall=fall).exists()


def test_testhof_teilzahlung_mit_nachzahlung_ohne_fall(hof: Testhof) -> None:
    """Sonderfall „Teilzahlung mit späterer Nachzahlung“ (Schritt 7): nach der Nachzahlung
    kein Rückstand, also kein Mahnfall."""
    (vertrag,) = hof.abgleich.sonderfaelle["teilzahlung_nachzahlung"]
    lauf(M1)
    assert not Mahnfall.objects.filter(vertrag=vertrag).exists()


def test_testhof_nichts_mit_wohnraum_bezug(hof: Testhof) -> None:
    """Das Mahnwesen mahnt im Testhof nie etwas mit Wohnraum-Bezug."""
    lauf(M1)
    assert Mahnfall.objects.exists()
    assert not Mahnfall.objects.filter(vertrag__einheiten__typ=Einheit.Typ.WOHNUNG).exists()
    assert Einheit.objects.filter(typ=Einheit.Typ.WOHNUNG).exists()


def _stand() -> list[list[dict[str, Any]]]:
    """Inhalt der Tabellen, die der Mahnlauf schreibt (ohne Zeitstempel der Uhr)."""
    return [
        list(m.objects.order_by("pk").values(*felder))
        for m, felder in (
            (Mahnfall, ("vertrag", "stufe", "stufe_belegt_am", "status", "sperrgrund")),
            (Mahnschritt, ("fall", "stufe", "stichtag", "schreiben", "hinweis", "abgebrochen_am")),
            (Mahnsperre, ("vertrag", "grund", "von", "bis", "aufgehoben_am")),
            (Schreiben, ("art", "status", "pdf_sha256")),
            (Arbeitsposten, ("art", "status", "hinweis")),
        )
    ]


def test_testhof_lauf_idempotent(hof: Testhof) -> None:
    """Ein zweiter Lauf am selben Montag ändert nichts."""
    lauf(M1)
    vorher = _stand()
    ergebnis = lauf(M1)
    assert ergebnis.faelle_neu == ergebnis.schreiben == ergebnis.arbeitsposten == 0
    assert _stand() == vorher
