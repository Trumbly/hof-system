"""Services des Kassenbuchs: Kategorien, Regeln, Buchen, Aufteilen, Storno, Rücknahme."""

from __future__ import annotations

import datetime
import itertools
from decimal import Decimal

import pytest
from django.db import DatabaseError, transaction
from pghistory.models import Events

from apps.kassenbuch import auswertung, services
from apps.kassenbuch.models import Buchung, Kategorie, Zuordnungsregel
from apps.kern import services as kern_services
from apps.kern.models import Arbeitsposten
from tests.kassenbuch.conftest import KleinerHof, ganz, umsatz

JAN = datetime.date(2026, 1, 10)


# --- Kategorien ----------------------------------------------------------------------


def test_kategorie_arten_und_entnahme_braucht_mandant(klein: KleinerHof) -> None:
    """Aufgabe 1 und 7: Kategorie mit Art Einnahme/Ausgabe/Entnahme/Umbuchung; eine
    Entnahme-Kategorie gehört genau einem Mandanten (B9)."""
    assert set(Kategorie.Art.values) == {"einnahme", "ausgabe", "entnahme", "umbuchung"}
    with pytest.raises(services.KassenbuchFehler, match="Mandanten"):
        services.kategorie_anlegen("Entnahmen ohne", Kategorie.Art.ENTNAHME)
    with pytest.raises(services.KassenbuchFehler, match="Nur Entnahme"):
        services.kategorie_anlegen("Strom 2", Kategorie.Art.AUSGABE, mandant=klein.gbr)
    with pytest.raises(services.KassenbuchFehler, match="gibt es schon"):
        services.kategorie_anlegen("strom", Kategorie.Art.AUSGABE)
    with pytest.raises(services.KassenbuchFehler, match="zwischen 0 und 100"):
        services.kategorie_anlegen("Wasser", Kategorie.Art.AUSGABE, umlagefaehiger_anteil=120)
    assert klein.entnahme_gbr.mandant == klein.gbr


def test_kategorie_aendern_und_deaktivieren(klein: KleinerHof) -> None:
    services.kategorie_aendern(klein.strom, bezeichnung="Allgemeinstrom", umlagefaehiger_anteil=80)
    klein.strom.refresh_from_db()
    assert (klein.strom.bezeichnung, klein.strom.umlagefaehiger_anteil) == ("Allgemeinstrom", 80)
    services.kategorie_aendern(klein.strom, aktiv=False)
    eintrag = umsatz(klein.konto, JAN, "10.00", eingang=False)
    with pytest.raises(services.KassenbuchFehler, match="nicht aktiv"):
        ganz(klein, eintrag, klein.strom)


# --- Regeln --------------------------------------------------------------------------


_PRIORITAET = itertools.count(100)


def _regel(klein: KleinerHof, **bedingungen: object) -> Zuordnungsregel:
    # Ohne Angabe eine eigene Priorität: Gleichrangige Regeln, die sich überschneiden,
    # lassen sich nicht speichern (Review 1, Punkt 5).
    bedingungen.setdefault("prioritaet", next(_PRIORITAET))
    return services.regel_anlegen(
        "Regel",
        kategorie=klein.miete,
        objekt=klein.hof,
        **bedingungen,  # type: ignore[arg-type]
    )


def test_regel_braucht_eine_gueltige_bedingung(klein: KleinerHof) -> None:
    """Aufgabe 4: Regeln sind Daten mit Bedingungen; eine Regel ohne Bedingung oder mit
    kaputtem Regex wird abgewiesen."""
    with pytest.raises(services.KassenbuchFehler, match="mindestens eine Bedingung"):
        _regel(klein)
    with pytest.raises(services.KassenbuchFehler, match="regulär"):
        _regel(klein, zweck="Miete (", zweck_regex=True)
    with pytest.raises(services.KassenbuchFehler, match="liegt über"):
        _regel(klein, betrag_von=Decimal("10"), betrag_bis=Decimal("5"))
    with pytest.raises(services.KassenbuchFehler, match="Entnahme"):
        services.regel_anlegen(
            "Entnahme", kategorie=klein.entnahme_kg, objekt=klein.hof, zweck="Entnahme"
        )
    regel = _regel(klein, gegen_iban="de40 0123 4567 0000 0000 01")
    assert regel.gegen_iban == "DE40012345670000000001"
    assert regel.mandant == klein.gbr  # Mandant des Objekts


def test_regel_bedingungen(klein: KleinerHof) -> None:
    """H6.3: Bedingungen auf Gegenpartei (Teil, ohne Groß-/Kleinschreibung), IBAN, Zweck
    (Teil oder Regex), Betragsspanne, Richtung und Konto; alle gesetzten müssen passen."""
    eintrag = umsatz(klein.konto, JAN, "250.00", name="Erika Beispiel", zweck="Miete 01/2026 G-07")
    passt = services.regel_passt
    assert passt(_regel(klein, gegenpartei="beispiel"), eintrag)
    assert not passt(_regel(klein, gegenpartei="Muster"), eintrag)
    assert passt(_regel(klein, gegen_iban="DE40012345670000000001"), eintrag)
    assert not passt(_regel(klein, gegen_iban="DE40012345670000000002"), eintrag)
    assert passt(_regel(klein, zweck="g-07"), eintrag)
    assert passt(_regel(klein, zweck=r"^Miete \d{2}/\d{4} G-0\d$", zweck_regex=True), eintrag)
    assert not passt(_regel(klein, zweck=r"G-1\d$", zweck_regex=True), eintrag)
    assert passt(_regel(klein, betrag_von=Decimal("250.00"), betrag_bis=Decimal("300")), eintrag)
    assert not passt(_regel(klein, betrag_bis=Decimal("249.99")), eintrag)
    assert not passt(_regel(klein, zweck="Miete", richtung="ausgang"), eintrag)
    assert passt(_regel(klein, konto=klein.konto), eintrag)
    assert not passt(_regel(klein, gegenpartei="Erika", zweck="Pacht"), eintrag)


def test_eindeutig_heisst_genau_eine_regel_mit_hoechster_prioritaet(klein: KleinerHof) -> None:
    """Aufgabe 3: Eindeutig ist ein Treffer, wenn genau eine Regel mit der höchsten
    Priorität passt; zwei gleichrangige sind mehrdeutig, keine ist kein Treffer."""
    allgemein = _regel(klein, zweck="Miete", prioritaet=10)
    genau = _regel(klein, gegenpartei="Erika", prioritaet=20)
    # Gleichrang entsteht nur, wenn der Umsatz erst nach beiden Regeln kommt (sonst lehnt
    # regel_anlegen die zweite ab, Review 1, Punkt 5).
    zweite = _regel(klein, gegen_iban="DE40012345670000000001", prioritaet=20)
    eintrag = umsatz(klein.konto, JAN, "250.00")

    einordnung = services.einordnen(eintrag, [allgemein, genau])
    assert einordnung.regel == genau
    assert set(einordnung.treffer) == {allgemein, genau}

    einordnung = services.einordnen(eintrag, services.aktive_regeln())
    assert einordnung.regel is None and einordnung.mehrdeutig
    assert set(einordnung.gleichauf) == {genau, zweite}

    services.regel_aendern(zweite, aktiv=False)
    assert services.einordnen(eintrag, services.aktive_regeln()).regel == genau
    assert not services.einordnen(eintrag, []).treffer


# --- Buchen und Aufteilen ------------------------------------------------------------


def test_umsatz_aufteilen_summe_muss_stimmen(klein: KleinerHof) -> None:
    """Aufgabe 2: Ein Umsatz kann in mehrere Buchungen aufgeteilt werden; die Summe muss dem
    Umsatz entsprechen (Prüfung im Service)."""
    eintrag = umsatz(klein.konto, JAN, "100.00", eingang=False, zweck="Sammelrechnung")
    teile = [
        services.Teil(Decimal("60.00"), klein.strom, klein.hof),
        services.Teil(Decimal("39.99"), klein.strom, klein.halle),
    ]
    with pytest.raises(services.KassenbuchFehler, match="Summe der Teile"):
        services.umsatz_buchen(eintrag, teile)
    assert not Buchung.objects.exists()

    teile[1] = services.Teil(Decimal("40.00"), klein.strom, klein.halle)
    buchungen = services.umsatz_buchen(eintrag, teile)

    assert [(b.objekt, b.mandant, b.betrag) for b in buchungen] == [
        (klein.hof, klein.gbr, Decimal("60.00")),
        (klein.halle, klein.kg, Decimal("40.00")),
    ]
    assert all(b.richtung == "ausgang" and b.datum == JAN for b in buchungen)
    assert all(b.leistungsjahr == 2026 and b.umlagefaehiger_anteil == 50 for b in buchungen)
    assert services.ist_gebucht(eintrag)
    with pytest.raises(services.KassenbuchFehler, match="schon gebucht"):
        services.umsatz_buchen(eintrag, teile)


@pytest.mark.parametrize("betrag", ["0.00", "-5.00", "10.001"])
def test_teilbetraege_sind_positiv_mit_zwei_stellen(klein: KleinerHof, betrag: str) -> None:
    """CLAUDE.md 4.2: Beträge positiv mit zwei Stellen, die Richtung kommt vom Umsatz."""
    eintrag = umsatz(klein.konto, JAN, "10.00")
    with pytest.raises(services.KassenbuchFehler):
        services.umsatz_buchen(eintrag, [services.Teil(Decimal(betrag), klein.miete, klein.hof)])


def test_entnahme_nur_fuer_ihren_mandanten(klein: KleinerHof) -> None:
    eintrag = umsatz(klein.konto, JAN, "500.00", eingang=False, zweck="Entnahme")
    with pytest.raises(services.KassenbuchFehler, match="Entnahme-Kategorie"):
        services.umsatz_zuordnen(eintrag, kategorie=klein.entnahme_kg, objekt=klein.hof)
    services.umsatz_zuordnen(eintrag, kategorie=klein.entnahme_gbr, objekt=klein.hof)


def test_entnahmen_getrennt_je_mandant(klein: KleinerHof) -> None:
    """Abnahme Schritt 2: Entnahmen stehen getrennt je Mandant (B9), in einer eigenen
    Kategorie je Mandant und nicht unter den Kosten."""
    for zweck, kategorie, mandant, betrag in (
        ("Entnahme 1", klein.entnahme_gbr, klein.gbr, "500.00"),
        ("Entnahme 2", klein.entnahme_gbr, klein.gbr, "250.00"),
        ("Entnahme 3", klein.entnahme_kg, klein.kg, "100.00"),
    ):
        eintrag = umsatz(klein.konto, JAN, betrag, eingang=False, zweck=zweck)
        services.umsatz_zuordnen(eintrag, kategorie=kategorie, objekt=klein.hof, mandant=mandant)
    strom = umsatz(klein.konto, JAN, "80.00", eingang=False, zweck="Strom")
    ganz(klein, strom, klein.strom)

    je_mandant = auswertung.entnahmen_je_mandant(
        datetime.date(2026, 1, 1), datetime.date(2026, 1, 31)
    )
    assert [(m.kuerzel, b) for m, b in je_mandant] == [
        ("GBR", Decimal("750.00")),
        ("KG", Decimal("100.00")),
    ]
    bericht = auswertung.bericht(klein.hof, JAN)
    assert bericht.summe_kosten == Decimal("80.00")
    assert [(e.mandant.kuerzel, e.betrag) for e in bericht.entnahmen] == [
        ("GBR", Decimal("750.00")),
        ("KG", Decimal("100.00")),
    ]


def test_wohnraum_wird_gebucht_und_markiert(klein: KleinerHof) -> None:
    """Sonderfall „Wohnraummiete auf demselben Konto“: Die Buchung entsteht und trägt die
    Markierung Wohnraum, damit der Abgleich (Schritt 6) sie auslässt."""
    eintrag = umsatz(klein.konto, JAN, "900.00", zweck="Miete Wohnung W-01")
    (buchung,) = services.umsatz_zuordnen(eintrag, kategorie=klein.wohnraum, objekt=klein.hof)
    assert buchung.wohnraum
    assert auswertung.bericht(klein.hof, JAN).summe_einnahmen == Decimal("900.00")


# --- Storno und Rücknahme ------------------------------------------------------------


def test_storno_statt_loeschen(klein: KleinerHof) -> None:
    """CLAUDE.md 4.4: Gebuchtes wird storniert; die Stornobuchung hat die Gegenrichtung und
    zeigt auf das Original, das unverändert bleibt. Danach steht der Umsatz in der Liste."""
    eintrag = umsatz(klein.konto, JAN, "250.00")
    (original,) = services.umsatz_zuordnen(eintrag, kategorie=klein.miete, objekt=klein.hof)

    (storno,) = services.umsatz_stornieren(eintrag)

    original.refresh_from_db()
    assert original.storniert_durch == storno
    assert (storno.richtung, storno.betrag, storno.datum) == ("ausgang", original.betrag, JAN)
    assert not services.ist_gebucht(eintrag)
    assert auswertung.bericht(klein.hof, JAN).summe_einnahmen == 0
    posten = Arbeitsposten.objects.get(art=services.ART_EINORDNEN)
    assert posten.bezug == eintrag
    with pytest.raises(services.KassenbuchFehler, match="keine gültige Buchung"):
        services.umsatz_stornieren(eintrag)


def test_buchung_laesst_sich_in_der_datenbank_nicht_aendern(klein: KleinerHof) -> None:
    """Gebuchtes wird nie überschrieben: Ein Trigger verhindert jede Änderung, auch am
    Service vorbei."""
    eintrag = umsatz(klein.konto, JAN, "250.00")
    (buchung,) = services.umsatz_zuordnen(eintrag, kategorie=klein.miete, objekt=klein.hof)
    buchung.betrag = Decimal("1.00")
    with pytest.raises(DatabaseError), transaction.atomic():
        buchung.save()


def test_zuruecknehmen_im_protokoll_storniert(klein: KleinerHof) -> None:
    """Der Stornoweg ist beim Kern angemeldet: „Zurücknehmen“ im Protokoll storniert eine
    Buchung, statt sie zu löschen."""
    eintrag = umsatz(klein.konto, JAN, "250.00")
    (buchung,) = services.umsatz_zuordnen(eintrag, kategorie=klein.miete, objekt=klein.hof)
    ereignis = Events.objects.get(
        pgh_obj_model="kassenbuch.Buchung", pgh_obj_id=str(buchung.pk), pgh_label="insert"
    )

    kern_services.zuruecknehmen(ereignis.pgh_slug)

    assert Buchung.objects.filter(pk=buchung.pk).exists()
    storno = Buchung.objects.get(storno_von=buchung)
    assert storno.richtung == "ausgang"
    with pytest.raises(kern_services.RuecknahmeNichtMoeglich, match="schon storniert"):
        kern_services.zuruecknehmen(ereignis.pgh_slug)
    storno_ereignis = Events.objects.get(
        pgh_obj_model="kassenbuch.Buchung", pgh_obj_id=str(storno.pk), pgh_label="insert"
    )
    with pytest.raises(kern_services.RuecknahmeNichtMoeglich, match="Stornobuchung"):
        kern_services.zuruecknehmen(storno_ereignis.pgh_slug)


def test_umbuchen_storniert_und_bucht_neu(klein: KleinerHof) -> None:
    eintrag = umsatz(klein.konto, JAN, "100.00", eingang=False, zweck="Strom")
    ganz(klein, eintrag, klein.strom)
    neu = services.umsatz_umbuchen(
        eintrag,
        [
            services.Teil(Decimal("50.00"), klein.strom, klein.hof),
            services.Teil(Decimal("50.00"), klein.strom, klein.halle),
        ],
    )
    assert len(neu) == 2
    assert Buchung.objects.filter(umsatz=eintrag).count() == 4  # Original, Storno, zwei neue
    assert services.ist_gebucht(eintrag)
    assert not Arbeitsposten.objects.filter(art=services.ART_EINORDNEN).exists()


def test_rueckbuchung_wird_erkannt(klein: KleinerHof) -> None:
    """Sonderfall „Rückbuchung“: Ein Ausgang über denselben Betrag an dieselbe IBAN wenige
    Tage nach einem Eingang ist als Rückbuchung erkennbar (Vorschlag, keine Buchung)."""
    eingang = umsatz(klein.konto, JAN, "250.00", zweck="Miete")
    zurueck = umsatz(
        klein.konto, JAN + datetime.timedelta(days=3), "250.00", eingang=False, zweck="Rückbuchung"
    )
    anders = umsatz(klein.konto, JAN, "99.00", eingang=False, zweck="Anders")
    assert services.rueckbuchung_zu(zurueck) == eingang
    assert services.rueckbuchung_zu(eingang) is None
    assert services.rueckbuchung_zu(anders) is None


# --- Einordnen und rückwirkende Regel ------------------------------------------------


def test_einordnen_bucht_eindeutige_und_legt_posten_fuer_den_rest(klein: KleinerHof) -> None:
    """Aufgabe 3: Eindeutige Treffer werden gebucht, mehrdeutige und fehlende werden ein
    Arbeitsposten „Umsätze einordnen“ mit den Aktionen; ein zweiter Lauf ändert nichts."""
    regel = _regel(klein, gegen_iban="DE40012345670000000001", richtung="eingang")
    services.regel_anlegen("Strom A", kategorie=klein.strom, objekt=klein.hof, zweck="Strom")
    services.regel_anlegen("Strom B", kategorie=klein.strom, objekt=klein.halle, zweck="Januar")
    eindeutig = umsatz(klein.konto, JAN, "250.00", iban="DE40012345670000000001")
    mehrdeutig = umsatz(klein.konto, JAN, "80.00", eingang=False, zweck="Strom Januar")
    fehlt = umsatz(klein.konto, JAN, "12.00", eingang=False, zweck="Unbekannt")

    ergebnis = services.umsaetze_einordnen()

    assert (ergebnis.gebucht, ergebnis.arbeitsposten, ergebnis.fehler) == (1, 2, 0)
    (buchung,) = Buchung.objects.filter(umsatz=eindeutig)
    assert buchung.regel == regel
    posten = {p.bezug: p for p in Arbeitsposten.objects.filter(art=services.ART_EINORDNEN)}
    assert set(posten) == {mehrdeutig, fehlt}
    assert posten[mehrdeutig].aktionen == list(services.AKTIONEN_EINORDNEN)
    assert "gleicher Priorität" in posten[mehrdeutig].hinweis
    assert "Keine Regel" in posten[fehlt].hinweis
    # keine Beträge im Hinweis
    assert "80.00" not in posten[mehrdeutig].hinweis
    assert "80,00" not in posten[mehrdeutig].hinweis

    zweiter = services.umsaetze_einordnen()
    assert (zweiter.gebucht, zweiter.arbeitsposten) == (0, 0)


def test_einordnen_bucht_stornierte_umsaetze_nie_selbst(klein: KleinerHof) -> None:
    eintrag = umsatz(klein.konto, JAN, "250.00")
    _regel(klein, gegen_iban=eintrag.gegen_iban)
    services.umsaetze_einordnen()
    services.umsatz_stornieren(eintrag, neu_einordnen=False)

    ergebnis = services.umsaetze_einordnen()

    assert (ergebnis.gebucht, ergebnis.arbeitsposten) == (0, 1)
    assert not services.ist_gebucht(eintrag)


def test_regel_rueckwirkend_mit_vorschau(klein: KleinerHof) -> None:
    """Aufgabe 4: Eine neue Regel lässt sich rückwirkend auf offene Umsätze anwenden; die
    Vorschau zählt zuerst, gebucht wird erst danach. Arbeitsposten sind danach erledigt."""
    a = umsatz(klein.konto, JAN, "40.00", eingang=False, zweck="Strom 1")
    b = umsatz(klein.konto, JAN, "41.00", eingang=False, zweck="Strom 2")
    gebucht = umsatz(klein.konto, JAN, "42.00", eingang=False, zweck="Strom 3")
    ganz(klein, gebucht, klein.strom)
    services.umsaetze_einordnen()
    assert Arbeitsposten.objects.filter(status="offen").count() == 2

    regel = services.regel_anlegen("Strom", kategorie=klein.strom, objekt=klein.hof, zweck="Strom")
    vorschau = services.regel_vorschau(regel)

    assert vorschau.zu_buchen == [a, b]
    assert Buchung.objects.filter(umsatz__in=[a, b]).count() == 0  # Vorschau bucht nichts
    with pytest.raises(services.VorschauVeraltet):
        services.regel_rueckwirkend_anwenden(regel, erwartet=5)
    assert services.regel_rueckwirkend_anwenden(regel, erwartet=2) == [a, b]
    assert services.ist_gebucht(a) and services.ist_gebucht(b)
    assert not Arbeitsposten.objects.filter(status="offen").exists()


def test_vorschau_zaehlt_mehrdeutige_und_hoeherrangige(klein: KleinerHof) -> None:
    services.regel_anlegen(
        "Halle", kategorie=klein.strom, objekt=klein.halle, zweck="Halle", prioritaet=30
    )
    services.regel_anlegen("Hof", kategorie=klein.strom, objekt=klein.hof, zweck="Hof")
    neu = services.regel_anlegen("Strom", kategorie=klein.strom, objekt=klein.hof, zweck="Strom")
    umsatz(klein.konto, JAN, "40.00", eingang=False, zweck="Strom Halle")
    umsatz(klein.konto, JAN, "41.00", eingang=False, zweck="Strom Hof")

    vorschau = services.regel_vorschau(neu)

    assert (len(vorschau.zu_buchen), len(vorschau.mehrdeutig), len(vorschau.andere_regel)) == (
        0,
        1,
        1,
    )
