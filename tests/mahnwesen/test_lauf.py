"""Der Mahnlauf im kleinen Hof: Stufen und Fristen auf den Tag, keine Stufe ohne belegte
Vorstufe, 14 Tage Sperrfrist je Stufe, bezahlt schließt den Fall, idempotent."""

from __future__ import annotations

import datetime
import io
from decimal import Decimal

import pytest
from django.core.management import call_command

from apps.dokumente.models import Schreiben
from apps.dokumente.vorlagenarten import Versandweg
from apps.kern.models import Arbeitsposten, Benutzer, Lauf
from apps.mahnwesen import services
from apps.mahnwesen.models import Mahnfall, Mahnschritt
from apps.soll import services as soll
from apps.stammdaten.models import Einheit, Vertragseinheit
from tests.mahnwesen.conftest import (
    FAELLIG_NOV,
    FAELLIG_OKT,
    Mahnhof,
    Vorlagen,
    fall_zu,
    lauf,
    letzter,
    mahnposten,
    schritte,
    senden,
    zustellen,
)

pytestmark = pytest.mark.django_db
D = datetime.date
TAG = datetime.timedelta(days=1)


def _bis_mahnung(mahnhof: Mahnhof, benutzer: Benutzer) -> tuple[object, Mahnschritt]:
    vertrag = mahnhof.vertrag()
    lauf(FAELLIG_OKT + 5 * TAG)
    erinnerung = letzter(vertrag)
    assert erinnerung.schreiben is not None
    senden(erinnerung.schreiben, benutzer, FAELLIG_OKT + 5 * TAG)
    lauf(FAELLIG_OKT + 14 * TAG)
    return vertrag, letzter(vertrag)


def test_erinnerung_fuenf_tage_nach_faelligkeit(mahnhof: Mahnhof, vorlagen: Vorlagen) -> None:
    """Abnahme Schritt 7, Stufe Erinnerung: fünf Tage nach Fälligkeit, nicht früher; als
    Schreiben „bereit“ per Mail mit einem Arbeitsposten „Mahnungen senden“."""
    vertrag = mahnhof.vertrag()

    lauf(FAELLIG_OKT + 4 * TAG)
    assert not Mahnfall.objects.exists()

    ergebnis = lauf(FAELLIG_OKT + 5 * TAG)
    fall = fall_zu(vertrag)
    (schritt,) = schritte(vertrag)
    assert ergebnis.faelle_neu == 1 and ergebnis.schreiben == 1
    assert schritt.stufe.schluessel == "erinnerung" and fall.stufe == schritt.stufe
    assert fall.stufe_belegt_am is None
    assert schritt.schreiben is not None
    assert schritt.schreiben.status == Schreiben.Status.BEREIT
    assert schritt.schreiben.versandweg == Versandweg.MAIL
    assert schritt.schreiben.art == "zahlungserinnerung"
    (posten,) = mahnposten(fall)
    assert posten.status == Arbeitsposten.Status.OFFEN
    assert vertrag.nummer in posten.hinweis and "bereit" in posten.hinweis
    # Nur die Oktobermiete: die Novembermiete liegt nach dem Datenstand der Bank.
    assert list(fall.offene_posten.values_list("faellig_am", flat=True)) == [FAELLIG_OKT]


def test_mahnung_nach_14_tagen_mit_zehn_tagen_frist(
    mahnhof: Mahnhof, vorlagen: Vorlagen, benutzer: Benutzer
) -> None:
    """Abnahme Schritt 7, Stufe Mahnung: nach 14 Tagen mit zehn Tagen Frist; die nächste
    Stufe frühestens zehn Tage, nachdem die Mahnung belegt ist."""
    vertrag = mahnhof.vertrag()
    lauf(FAELLIG_OKT + 5 * TAG)
    senden(letzter(vertrag).schreiben, benutzer, FAELLIG_OKT + 5 * TAG)  # type: ignore[arg-type]

    lauf(FAELLIG_OKT + 13 * TAG)
    assert letzter(vertrag).stufe.schluessel == "erinnerung"
    fall = fall_zu(vertrag)
    assert fall.stufe_belegt_am == FAELLIG_OKT + 5 * TAG
    assert fall.naechste_stufe_fruehestens == FAELLIG_OKT + 12 * TAG  # Zahlungsziel 7 Tage

    stichtag = FAELLIG_OKT + 14 * TAG
    lauf(stichtag)
    mahnung = letzter(vertrag)
    assert mahnung.stufe.schluessel == "mahnung"
    assert mahnung.stufe.frist_tage == 10
    assert mahnung.schreiben is not None and mahnung.schreiben.einseitige_erklaerung
    assert f"{stichtag + 10 * TAG:%d.%m.%Y}" in mahnung.schreiben.inhalt_html  # Frist im Text

    zustellen(senden(mahnung.schreiben, benutzer, stichtag), stichtag + TAG)
    lauf(stichtag + 7 * TAG)
    fall.refresh_from_db()
    assert fall.stufe_belegt_am == stichtag + TAG
    assert fall.naechste_stufe_fruehestens == stichtag + 11 * TAG


def test_abmahnung_bei_der_zweiten_offenen_monatsmiete(
    mahnhof: Mahnhof, vorlagen: Vorlagen, benutzer: Benutzer
) -> None:
    """Abnahme Schritt 7, Stufe Abmahnung: erst bei der zweiten offenen Monatsmiete; sie
    entsteht als Brief."""
    vertrag, mahnung = _bis_mahnung(mahnhof, benutzer)
    zustellen(senden(mahnung.schreiben, benutzer, FAELLIG_OKT + 14 * TAG), D(2026, 10, 20))  # type: ignore[arg-type]

    lauf(D(2026, 11, 2))  # Frist abgelaufen, aber nur eine Monatsmiete offen
    assert letzter(vertrag).stufe.schluessel == "mahnung"

    mahnhof.datenstand(D(2026, 11, 30))
    lauf(FAELLIG_NOV)  # die Novembermiete ist am Stichtag erst fällig, nicht überfällig
    assert letzter(vertrag).stufe.schluessel == "mahnung"

    lauf(FAELLIG_NOV + TAG)
    abmahnung = letzter(vertrag)
    assert abmahnung.stufe.schluessel == "abmahnung"
    assert abmahnung.schreiben is not None
    assert abmahnung.schreiben.art == "abmahnung"
    assert abmahnung.schreiben.versandweg == Versandweg.SELBST  # drucken, unterschreiben
    assert abmahnung.schreiben.einseitige_erklaerung


def test_kuendigung_ab_zwei_monatsmieten_rueckstand(
    mahnhof: Mahnhof, vorlagen: Vorlagen, benutzer: Benutzer
) -> None:
    """Abnahme Schritt 7, Stufe Kündigung: fristlos, hilfsweise ordentlich, ab zwei
    Monatsmieten Rückstand; als Brief mit dem Hinweis „vor Versand zum Anwalt“."""
    vorlagen.fristlos()
    vertrag, mahnung = _bis_mahnung(mahnhof, benutzer)
    zustellen(senden(mahnung.schreiben, benutzer, FAELLIG_OKT + 14 * TAG), D(2026, 10, 20))  # type: ignore[arg-type]
    mahnhof.datenstand(D(2026, 11, 30))
    lauf(FAELLIG_NOV + TAG)
    abmahnung = letzter(vertrag)
    zustellen(senden(abmahnung.schreiben, benutzer, FAELLIG_NOV + TAG), FAELLIG_NOV + 2 * TAG)  # type: ignore[arg-type]

    lauf(FAELLIG_NOV + 15 * TAG)  # Frist der Abmahnung (14 Tage) läuft noch
    assert letzter(vertrag).stufe.schluessel == "abmahnung"

    lauf(FAELLIG_NOV + 16 * TAG)
    kuendigung = letzter(vertrag)
    assert kuendigung.stufe.schluessel == "kuendigung"
    assert kuendigung.schreiben is not None
    assert kuendigung.schreiben.art == "kuendigung"
    assert kuendigung.schreiben.versandweg == Versandweg.SELBST
    assert "fristlos, hilfsweise ordentlich" in kuendigung.schreiben.inhalt_html
    (posten,) = [p for p in mahnposten() if p.pk == kuendigung.arbeitsposten_id]
    assert "vor Versand zum Anwalt" in posten.hinweis


def test_kuendigung_nicht_unter_zwei_monatsmieten(
    mahnhof: Mahnhof, vorlagen: Vorlagen, benutzer: Benutzer
) -> None:
    """Abnahme Schritt 7: Ist von zwei offenen Monatsmieten eine zum Teil bezahlt, liegt der
    Rückstand unter zwei Monatsmieten: Abmahnung ja, Kündigung nein."""
    vorlagen.fristlos()
    vertrag, mahnung = _bis_mahnung(mahnhof, benutzer)
    zustellen(senden(mahnung.schreiben, benutzer, FAELLIG_OKT + 14 * TAG), D(2026, 10, 20))  # type: ignore[arg-type]
    mahnhof.datenstand(D(2026, 11, 30))
    lauf(FAELLIG_NOV + TAG)
    abmahnung = letzter(vertrag)
    zustellen(senden(abmahnung.schreiben, benutzer, FAELLIG_NOV + TAG), FAELLIG_NOV + 2 * TAG)  # type: ignore[arg-type]
    november = vertrag.sollposten.get(faellig_am=FAELLIG_NOV, art="miete")
    soll.zahlungsstand_setzen(november, Decimal("10.00"))

    lauf(FAELLIG_NOV + 21 * TAG)
    assert letzter(vertrag).stufe.schluessel == "abmahnung"


def test_keine_stufe_ohne_belegte_vorstufe(
    mahnhof: Mahnhof, vorlagen: Vorlagen, benutzer: Benutzer
) -> None:
    """Abnahme Schritt 7: Keine Stufe folgt, bevor die vorige belegt ist. Die Erinnerung
    zählt erst gesendet; die Mahnung als einseitige Erklärung erst zugestellt mit
    Zustellnachweis."""
    vertrag = mahnhof.vertrag()
    lauf(FAELLIG_OKT + 5 * TAG)
    erinnerung = letzter(vertrag)

    for tag in (FAELLIG_OKT + 14 * TAG, FAELLIG_OKT + 28 * TAG):
        lauf(tag)  # Erinnerung noch „bereit“: keine Mahnung, kein zweiter Posten
        assert [s.pk for s in schritte(vertrag)] == [erinnerung.pk]
    assert len(mahnposten()) == 1

    senden(erinnerung.schreiben, benutzer, FAELLIG_OKT + 28 * TAG)  # type: ignore[arg-type]
    lauf(FAELLIG_OKT + 35 * TAG)
    mahnung = letzter(vertrag)
    assert mahnung.stufe.schluessel == "mahnung"

    mahnhof.datenstand(D(2026, 11, 30))
    senden(mahnung.schreiben, benutzer, FAELLIG_OKT + 35 * TAG)  # type: ignore[arg-type]
    lauf(D(2026, 11, 23))  # zwei Monatsmieten offen, Mahnung gesendet, aber nicht zugestellt
    assert letzter(vertrag).pk == mahnung.pk
    assert fall_zu(vertrag).stufe_belegt_am is None

    zustellen(mahnung.schreiben, D(2026, 11, 10))  # type: ignore[arg-type]
    lauf(D(2026, 11, 23))
    assert letzter(vertrag).stufe.schluessel == "abmahnung"
    (erinnerung_posten, mahnung_posten, _) = mahnposten()
    assert erinnerung_posten.status == mahnung_posten.status == Arbeitsposten.Status.ERLEDIGT


def test_kein_zweites_schreiben_derselben_stufe_binnen_14_tagen(
    mahnhof: Mahnhof, vorlagen: Vorlagen
) -> None:
    """H4.1: Verwirft der Benutzer das Schreiben, kommt dieselbe Stufe frühestens 14 Tage
    später wieder."""
    from apps.dokumente import services as dokumente

    vertrag = mahnhof.vertrag()
    erster = FAELLIG_OKT + 5 * TAG
    lauf(erster)
    dokumente.schreiben_verwerfen(letzter(vertrag).schreiben)  # type: ignore[arg-type]

    lauf(erster + 7 * TAG)
    assert len(schritte(vertrag)) == 1
    assert letzter(vertrag).abgebrochen_am == erster + 7 * TAG
    assert mahnposten()[0].status == Arbeitsposten.Status.ERLEDIGT
    lauf(erster + 13 * TAG)
    assert len(schritte(vertrag)) == 1

    lauf(erster + 14 * TAG)
    zweiter = letzter(vertrag)
    assert len(schritte(vertrag)) == 2 and zweiter.stufe.schluessel == "erinnerung"
    assert zweiter.schreiben is not None and zweiter.schreiben.status == Schreiben.Status.BEREIT


def test_bezahlt_schliesst_den_fall(mahnhof: Mahnhof, vorlagen: Vorlagen) -> None:
    """Standardablauf 3: Zahlt der Mieter (kein Posten mehr offen), schließt sich der Fall;
    das bereite Schreiben wird verworfen, der Arbeitsposten erledigt."""
    vertrag = mahnhof.vertrag()
    lauf(FAELLIG_OKT + 5 * TAG)
    schritt = letzter(vertrag)
    oktober = vertrag.sollposten.get(faellig_am=FAELLIG_OKT, art="miete")
    soll.zahlungsstand_setzen(oktober, oktober.betrag)

    ergebnis = lauf(FAELLIG_OKT + 12 * TAG)
    fall = Mahnfall.objects.get(vertrag=vertrag)
    assert ergebnis.erledigt == 1
    assert fall.status == Mahnfall.Status.ERLEDIGT
    assert fall.geschlossen_am == FAELLIG_OKT + 12 * TAG
    schritt.refresh_from_db()
    assert schritt.schreiben is not None
    assert schritt.schreiben.status == Schreiben.Status.VERWORFEN
    assert mahnposten()[0].status == Arbeitsposten.Status.ERLEDIGT


def test_lauf_ist_idempotent(mahnhof: Mahnhof, vorlagen: Vorlagen) -> None:
    """Standardablauf 3: Ein zweiter Lauf am selben Stichtag legt nichts doppelt an."""
    mahnhof.vertrag()
    mahnhof.vertrag(miete="40.00")
    erster = lauf(FAELLIG_OKT + 6 * TAG)
    zahlen = (
        Mahnfall.objects.count(),
        Mahnschritt.objects.count(),
        Schreiben.objects.count(),
        Arbeitsposten.objects.filter(art=services.ART).count(),
    )
    zweiter = lauf(FAELLIG_OKT + 6 * TAG)
    assert erster.schreiben == 2 and zweiter.schreiben == 0 and zweiter.faelle_neu == 0
    assert (
        zahlen
        == (
            Mahnfall.objects.count(),
            Mahnschritt.objects.count(),
            Schreiben.objects.count(),
            Arbeitsposten.objects.filter(art=services.ART).count(),
        )
        == (2, 2, 2, 2)
    )


def test_datenluecke_ist_kein_rueckstand(mahnhof: Mahnhof, vorlagen: Vorlagen) -> None:
    """Standardablauf 3, Schritt 2: Posten nach dem Datenstand der Bank zählen nicht."""
    mahnhof.vertrag(beginn=D(2026, 11, 1))
    lauf(D(2026, 11, 30))
    assert not Mahnfall.objects.exists()


def test_nichts_mit_wohnraum_bezug_wird_gemahnt(mahnhof: Mahnhof, vorlagen: Vorlagen) -> None:
    """Das Mahnwesen gilt nur für Verträge im Hof-System; den Wohnraum mahnt Verwaltio. Auch
    ein Vertrag, der (etwa aus einem alten Import) eine Wohnung umfasst, wird nie gemahnt."""
    vertrag = mahnhof.vertrag()
    wohnung = Einheit.objects.create(  # am Service vorbei: so etwas legt kein Service an
        nw_id="NW-W-9999", typ=Einheit.Typ.WOHNUNG, objekt=mahnhof.klein.hof
    )
    Vertragseinheit.objects.create(vertrag=vertrag, einheit=wohnung)

    lauf(FAELLIG_OKT + 20 * TAG)
    assert not Mahnfall.objects.exists()
    assert not Schreiben.objects.exists()
    with pytest.raises(services.MahnwesenFehler, match="Wohnraum"):
        services.ordentliche_kuendigung_erzeugen(vertrag, D(2026, 11, 3))


def test_lauf_als_befehl(mahnhof: Mahnhof, vorlagen: Vorlagen) -> None:
    """CLAUDE.md, Regel 4.7: Der Lauf schreibt einen Eintrag im Laufprotokoll mit Zählern."""
    mahnhof.vertrag()
    ausgabe = io.StringIO()
    call_command("mahnlauf", "--stichtag", f"{FAELLIG_OKT + 5 * TAG}", stdout=ausgabe)
    eintrag = Lauf.objects.get(name="mahnlauf")
    assert eintrag.status == Lauf.Status.ERFOLGREICH
    assert (eintrag.gebucht, eintrag.arbeitsposten, eintrag.fehler) == (1, 1, 0)
    assert "1 Schreiben bereit" in eintrag.meldung
    assert mahnhof.mieter.nachname not in eintrag.meldung
