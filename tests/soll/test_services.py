"""Services der Sollstellung (Schritt 5, Aufgaben 1 bis 5)."""

from __future__ import annotations

import datetime
import io
from decimal import Decimal

import pytest
from django.contrib.contenttypes.models import ContentType
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import override_settings
from pghistory.models import Events

from apps.kern import services as kern
from apps.kern.models import Arbeitsposten, Lauf
from apps.soll import services as soll
from apps.soll.models import Sollposten
from apps.stammdaten import services
from apps.stammdaten.models import Vertrag
from tests.soll.conftest import HEUTE, VertragBauer, geltend

D = datetime.date
E = Decimal


def _posten_pruefen(posten: Sollposten) -> list[Arbeitsposten]:
    return list(
        Arbeitsposten.objects.filter(
            art=soll.ART_PRUEFEN,
            bezug_typ=ContentType.objects.get_for_model(Sollposten),
            bezug_id=posten.pk,
        )
    )


# --- Erzeugen ------------------------------------------------------------------------


def test_aktiver_vertrag_bekommt_soll_bis_zum_laufenden_monat(neuer_vertrag: VertragBauer) -> None:
    """Schritt 5, Aufgabe 3: Die Anlage eines aktiven Vertrags stellt sein Soll bis Ende des
    laufenden Monats (Haken in den Stammdaten-Services)."""
    vertrag = neuer_vertrag(D(2026, 9, 1))
    assert [(p.von, p.bis, p.betrag, p.faellig_am, p.status) for p in geltend(vertrag)] == [
        (D(2026, 9, 1), D(2026, 9, 30), E("85.00"), D(2026, 9, 3), "offen"),
        (D(2026, 10, 1), D(2026, 10, 31), E("85.00"), D(2026, 10, 5), "offen"),
        (D(2026, 11, 1), D(2026, 11, 30), E("85.00"), D(2026, 11, 4), "offen"),
    ]
    assert {p.quelle for p in geltend(vertrag)} == {"vertrag"}


def test_entwurf_hat_kein_soll(neuer_vertrag: VertragBauer) -> None:
    vertrag = neuer_vertrag(D(2026, 9, 1), status=Vertrag.Status.ENTWURF)
    assert soll.sollstellung_erzeugen(vertrag, D(2026, 12, 31)).neu == []
    assert not Sollposten.objects.filter(vertrag=vertrag).exists()


def test_sollstellung_ist_idempotent(neuer_vertrag: VertragBauer) -> None:
    """Schritt 5, Aufgabe 2: Ein zweiter Aufruf ändert nichts."""
    vertrag = neuer_vertrag(D(2026, 1, 1))
    erstes = soll.sollstellung_erzeugen(vertrag, D(2026, 12, 31))
    assert len(erstes.neu) == 1  # Dezember; Januar bis November stellte schon die Anlage
    zweites = soll.sollstellung_erzeugen(vertrag, D(2026, 12, 31))
    assert (zweites.neu, zweites.storniert, zweites.unveraendert) == ([], [], 12)
    # Ein früheres Bis-Datum schrumpft den Bestand nicht.
    assert soll.sollstellung_erzeugen(vertrag, D(2026, 3, 31)).storniert == []
    assert len(geltend(vertrag)) == 12


def test_unique_je_vertrag_art_und_zeitraum(neuer_vertrag: VertragBauer) -> None:
    """Schritt 5, Aufgabe 2: Die Datenbank lässt keinen zweiten geltenden Posten für
    denselben Zeitraum zu; ein stornierter steht nicht im Weg."""
    vertrag = neuer_vertrag(D(2026, 11, 1))
    (posten,) = geltend(vertrag)
    doppelt = Sollposten(
        vertrag=vertrag,
        art=posten.art,
        von=posten.von,
        bis=posten.bis,
        netto=posten.netto,
        betrag=posten.betrag,
        faellig_am=posten.faellig_am,
    )
    with pytest.raises(IntegrityError), transaction.atomic():
        doppelt.save()
    soll.sollposten_stornieren(posten, "Test")
    doppelt.save()


# --- Änderungen am Vertrag -----------------------------------------------------------


def test_rueckwirkende_mieterhoehung_storniert_offene_posten(neuer_vertrag: VertragBauer) -> None:
    """Schritt 5, Aufgabe 3: Eine rückwirkende Änderung storniert betroffene offene Posten
    und erzeugt neue; der neue zeigt auf den alten."""
    vertrag = neuer_vertrag(D(2026, 1, 1))
    alt = {p.von: p for p in geltend(vertrag)}

    services.kondition_ab(vertrag, D(2026, 10, 16), miete=E("95.00"), grund="Erhöhung")

    neu = geltend(vertrag)
    assert [(p.von, p.bis, p.betrag) for p in neu[-3:]] == [
        (D(2026, 10, 1), D(2026, 10, 15), E("41.13")),  # 85 × 15/31 = 41,129…
        (D(2026, 10, 16), D(2026, 10, 31), E("49.03")),  # Rest von 90,16 (95 × 16/31 = 49,03)
        (D(2026, 11, 1), D(2026, 11, 30), E("95.00")),
    ]
    oktober, november = alt[D(2026, 10, 1)], alt[D(2026, 11, 1)]
    for posten in (oktober, november):
        posten.refresh_from_db()
        assert (posten.status, posten.storno_grund, posten.storniert_am) == (
            "storniert",
            soll.GRUND_AENDERUNG,
            HEUTE,
        )
    assert neu[-3].ersetzt == oktober and neu[-1].ersetzt == november
    alt[D(2026, 9, 1)].refresh_from_db()
    assert alt[D(2026, 9, 1)].status == "offen"  # nicht betroffen


def test_bezahlter_posten_wird_nicht_still_geaendert(neuer_vertrag: VertragBauer) -> None:
    """Schritt 5, Aufgabe 3: Bezahlte und teilweise bezahlte Posten bleiben; es entsteht ein
    Arbeitsposten, und im selben Zeitraum nichts Neues."""
    vertrag = neuer_vertrag(D(2026, 1, 1))
    posten = {p.von: p for p in geltend(vertrag)}
    soll.zahlungsstand_setzen(posten[D(2026, 10, 1)], E("85.00"))
    soll.zahlungsstand_setzen(posten[D(2026, 11, 1)], E("40.00"))

    ergebnis = soll.sollstellung_erzeugen(vertrag, D(2026, 11, 30))
    assert ergebnis.abweichend == []  # noch passt alles

    services.kondition_ab(vertrag, D(2026, 10, 1), miete=E("90.00"))

    for von, status in ((D(2026, 10, 1), "bezahlt"), (D(2026, 11, 1), "teilweise")):
        p = posten[von]
        p.refresh_from_db()
        assert (p.status, p.betrag) == (status, E("85.00"))
        (gemeldet,) = _posten_pruefen(p)
        assert gemeldet.status == "offen"
        assert f"Vertrag {vertrag.nummer}" in gemeldet.hinweis
        assert "85,00" not in gemeldet.hinweis and "85.00" not in gemeldet.hinweis
    assert [p.von for p in geltend(vertrag)][-2:] == [D(2026, 10, 1), D(2026, 11, 1)]
    assert len(geltend(vertrag)) == 11

    # Ein zweiter Lauf meldet nicht noch einmal, auch nicht nach „erledigt“.
    soll.sollstellung_erzeugen(vertrag, D(2026, 11, 30))
    kern.arbeitsposten_erledigen(_posten_pruefen(posten[D(2026, 10, 1)])[0])
    soll.sollstellung_erzeugen(vertrag, D(2026, 11, 30))
    assert len(_posten_pruefen(posten[D(2026, 10, 1)])) == 1


def test_kuendigung_beendet_soll_zum_fristende(neuer_vertrag: VertragBauer) -> None:
    """Abnahme Schritt 5: Eine Kündigung beendet das Soll zum Fristende. Zugang am
    03.11.2026, § 580a Abs. 1 Nr. 3 BGB: Fristende 31.01.2027. Offene Posten danach werden
    storniert."""
    vertrag = neuer_vertrag(D(2026, 2, 1))
    soll.sollstellung_erzeugen(vertrag, D(2027, 6, 30))
    assert geltend(vertrag)[-1].von == D(2027, 6, 1)

    kuendigung = services.kuendigung_erfassen(vertrag, D(2026, 11, 3))

    assert kuendigung.fristende == D(2027, 1, 31)
    posten = geltend(vertrag)
    assert posten[-1].bis == D(2027, 1, 31)
    assert [p.von for p in posten[-3:]] == [D(2026, 11, 1), D(2026, 12, 1), D(2027, 1, 1)]
    storniert = Sollposten.objects.filter(vertrag=vertrag, status="storniert")
    assert sorted(p.von for p in storniert) == [D(2027, m, 1) for m in range(2, 7)]
    # Spätere Läufe stellen nach dem Fristende nichts mehr.
    assert soll.sollstellung_erzeugen(vertrag, D(2027, 12, 31)).neu == []


def test_kuendigung_mit_wunschende_mitten_im_monat(neuer_vertrag: VertragBauer) -> None:
    """Schritt 5, Aufgabe 5: Ein späteres Wunschende zur Monatsmitte gibt einen Teilmonat."""
    vertrag = neuer_vertrag(D(2026, 2, 1), "90.00")
    services.kuendigung_erfassen(vertrag, D(2026, 11, 3), zum_wunsch=D(2027, 2, 14))
    soll.sollstellung_erzeugen(vertrag, D(2027, 6, 30))
    letzter = geltend(vertrag)[-1]
    assert (letzter.von, letzter.bis, letzter.betrag) == (
        D(2027, 2, 1),
        D(2027, 2, 14),
        E("45.00"),  # 90 × 14/28
    )


def test_ende_und_beginn_aendern(neuer_vertrag: VertragBauer) -> None:
    """Schritt 5, Aufgabe 3: Auch eine geänderte Laufzeit stellt das Soll neu."""
    vertrag = neuer_vertrag(D(2026, 9, 1))
    services.vertrag_aendern(vertrag, beginn=D(2026, 9, 15), ende=D(2026, 10, 20))
    assert [(p.von, p.bis, p.betrag) for p in geltend(vertrag)] == [
        (D(2026, 9, 15), D(2026, 9, 30), E("45.33")),  # 85 × 16/30
        (D(2026, 10, 1), D(2026, 10, 20), E("54.84")),  # 85 × 20/31
    ]


def test_rueckgabe_ohne_kuendigung_beendet_soll(neuer_vertrag: VertragBauer) -> None:
    vertrag = neuer_vertrag(D(2026, 1, 1))
    rueckgabe = services.uebergabe_beginnen(vertrag, "rueckgabe", D(2026, 10, 10))
    services.uebergabe_abschliessen(rueckgabe)
    assert geltend(vertrag)[-1].bis == D(2026, 10, 10)


def test_ohne_sollstellung_unterdrueckt_den_haken(neuer_vertrag: VertragBauer) -> None:
    with soll.ohne_sollstellung("Test"):
        vertrag = neuer_vertrag(D(2026, 1, 1))
    assert not Sollposten.objects.filter(vertrag=vertrag).exists()


# --- Regeln je Vertrag ---------------------------------------------------------------


def test_faelligkeit_nach_vertrag(neuer_vertrag: VertragBauer) -> None:
    """Schritt 5, Aufgabe 4: Fälligkeit nach Vertrag (hier: am 1.); offene Posten werden neu
    gestellt."""
    vertrag = neuer_vertrag(D(2026, 10, 1))
    soll.sollregel_setzen(vertrag, faelligkeit="kalendertag", faelligkeit_tag=1)
    assert [p.faellig_am for p in geltend(vertrag)] == [D(2026, 10, 1), D(2026, 11, 1)]
    soll.sollregel_setzen(vertrag, faelligkeit="")
    assert [p.faellig_am for p in geltend(vertrag)] == [D(2026, 10, 5), D(2026, 11, 4)]
    with pytest.raises(soll.SollFehler, match=vertrag.nummer):
        soll.sollregel_setzen(vertrag, faelligkeit="werktag", faelligkeit_tag=12)


@override_settings(SOLL_BEGINN=D(2026, 7, 1))
def test_soll_beginn_aus_der_einstellung_und_je_vertrag(neuer_vertrag: VertragBauer) -> None:
    """Schritt 5: Vor HOF_SOLL_BEGINN führt das alte System das Soll."""
    vertrag = neuer_vertrag(D(2025, 5, 1))
    assert geltend(vertrag)[0].von == D(2026, 7, 1)
    soll.sollregel_setzen(vertrag, soll_ab=D(2026, 10, 1))
    assert geltend(vertrag)[0].von == D(2026, 7, 1)  # Früheres bleibt stehen
    assert soll.plan(vertrag, D(2026, 11, 30))[0].von == D(2026, 10, 1)


# --- Einzelne Posten -----------------------------------------------------------------


def test_posten_von_hand(neuer_vertrag: VertragBauer) -> None:
    vertrag = neuer_vertrag(D(2026, 11, 1))
    posten = soll.sollposten_anlegen(
        vertrag,
        Sollposten.Art.SONSTIGES,
        von=D(2026, 11, 10),
        netto=E("25.00"),
        steuersatz=E("19"),
        bezeichnung="Ersatz Handsender",
    )
    assert (posten.quelle, posten.betrag, posten.steuer, posten.faellig_am) == (
        "hand",
        E("29.75"),
        E("4.75"),
        D(2026, 11, 10),
    )
    # Die Sollstellung lässt Posten von Hand in Ruhe.
    soll.sollstellung_erzeugen(vertrag, D(2026, 12, 31))
    posten.refresh_from_db()
    assert posten.status == "offen"
    with pytest.raises(soll.SollFehler, match="Konditionen"):
        soll.sollposten_anlegen(vertrag, Sollposten.Art.MIETE, von=D(2026, 11, 1), netto=E("1"))
    with pytest.raises(soll.SollFehler, match="schon"):
        soll.sollposten_anlegen(
            vertrag, Sollposten.Art.SONSTIGES, von=D(2026, 11, 10), netto=E("1")
        )


def test_zahlungsstand_und_storno(neuer_vertrag: VertragBauer) -> None:
    """Erweiterungsstelle Schritt 6: Zahlungsstand setzt den Status; Bezahltes lässt sich
    nicht stornieren."""
    vertrag = neuer_vertrag(D(2026, 11, 1))
    (posten,) = geltend(vertrag)
    assert soll.zahlungsstand_setzen(posten, E("10.00")).status == "teilweise"
    assert posten.offen == E("75.00")
    with pytest.raises(soll.SollFehler, match="bezahlt"):
        soll.sollposten_stornieren(posten, "falsch")
    assert soll.zahlungsstand_setzen(posten, E("85.00")).status == "bezahlt"
    with pytest.raises(soll.SollFehler, match="außerhalb"):
        soll.zahlungsstand_setzen(posten, E("85.01"))
    assert soll.zahlungsstand_setzen(posten, E("0")).status == "offen"
    soll.sollposten_stornieren(posten, "doppelt")
    assert posten.offen == E("0.00")
    with pytest.raises(soll.SollFehler, match="storniert"):
        soll.zahlungsstand_setzen(posten, E("1.00"))
    with pytest.raises(soll.SollFehler, match="schon"):
        soll.sollposten_stornieren(posten, "nochmal")


def test_zuruecknehmen_im_protokoll_storniert(neuer_vertrag: VertragBauer) -> None:
    """CLAUDE.md, Regel 4.4: Die Rücknahme eines Sollpostens ist ein Storno."""
    vertrag = neuer_vertrag(D(2026, 11, 1))
    (posten,) = geltend(vertrag)
    (anlage,) = Events.objects.tracks(posten).filter(pgh_label="insert")
    kern.zuruecknehmen(anlage.pgh_slug)
    posten.refresh_from_db()
    assert posten.status == "storniert"
    assert Sollposten.objects.filter(pk=posten.pk).exists()


# --- Lauf ----------------------------------------------------------------------------


def test_lauf_soll_monat(neuer_vertrag: VertragBauer) -> None:
    """Schritt 5, Aufgabe 3: Der Lauf soll_monat stellt am Monatsersten das Soll für alle
    Verträge und schreibt einen Eintrag ins Laufprotokoll."""
    a = neuer_vertrag(D(2026, 1, 1))
    b = neuer_vertrag(D(2026, 6, 1), "40.00", zahlweise="vierteljaehrlich")
    ausgabe = io.StringIO()
    call_command("soll_monat", "--stichtag", "2026-12-01", stdout=ausgabe)

    lauf = Lauf.objects.get(name="soll_monat")
    assert (lauf.status, lauf.gebucht, lauf.fehler) == ("erfolgreich", 1, 0)
    assert "2 Verträge" in lauf.meldung and "1 Posten neu" in lauf.meldung
    assert geltend(a)[-1].von == D(2026, 12, 1)
    assert [(p.von, p.betrag) for p in geltend(b)] == [
        (D(2026, 6, 1), E("40.00")),  # Teilquartal Juni
        (D(2026, 7, 1), E("120.00")),
        (D(2026, 10, 1), E("120.00")),
    ]
    call_command("soll_monat", "--stichtag", "2026-12-01", stdout=io.StringIO())
    assert Lauf.objects.filter(name="soll_monat").first().gebucht == 0  # type: ignore[union-attr]


def test_sollposten_pruefen_meldet_sich() -> None:
    """Die neue Art „Sollposten prüfen“ steht im Standard von HOF_BENACHRICHTIGUNG_ARTEN."""
    from django.conf import settings

    assert soll.ART_PRUEFEN in settings.HOF_BENACHRICHTIGUNG_ARTEN
