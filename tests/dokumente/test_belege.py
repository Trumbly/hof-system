"""Belege: Vorschlag, Bestätigen, Aufteilen, Buchung, Umwidmen, Lieferanten (Schritt 3a)."""

from __future__ import annotations

import datetime
from decimal import Decimal

import pytest
from django.db import IntegrityError, transaction

from apps.dokumente import dokumentarten, services
from apps.dokumente.models import Beleg, Dokument
from apps.kern.models import Arbeitsposten
from tests.dokumente.conftest import Belegwelt, gueltige_buchung, rechnung_pdf

pytestmark = pytest.mark.django_db

TAG = datetime.date(2026, 11, 3)


def offener_beleg(belegwelt: Belegwelt, nummer: str = "R-1", lieferant: bool = True) -> Beleg:
    dokument = services.dokument_ablegen(
        rechnung_pdf(nummer),
        art=dokumentarten.BELEG,
        datum=TAG,
        herkunft=Dokument.Herkunft.POSTFACH,
    ).dokument
    return services.beleg_anlegen(dokument, lieferant=belegwelt.lieferant if lieferant else None)


def bestaetigt(belegwelt: Belegwelt, betrag: str = "119.00", nummer: str = "R-1") -> Beleg:
    return services.beleg_bestaetigen(
        offener_beleg(belegwelt, nummer),
        lieferant=belegwelt.lieferant,
        betrag=Decimal(betrag),
        objekt=belegwelt.hof,
        kategorie=belegwelt.strom,
        leistungsjahr=2026,
    )


# --- Lieferanten ---------------------------------------------------------------------


def test_lieferant_anlegen_normalisiert_und_verhindert_doppelte(belegwelt: Belegwelt) -> None:
    neu = services.lieferant_anlegen(
        "  Stadtwerke Beispiel ", iban="de44 5001 0517 5407 3249 31", email="Rechnung@X.invalid"
    )
    assert (neu.name, neu.iban, neu.email) == (
        "Stadtwerke Beispiel",
        "DE44500105175407324931",
        "rechnung@x.invalid",
    )
    with pytest.raises(services.AblageFehler):
        services.lieferant_anlegen("stadtwerke beispiel")
    assert services.lieferant_zur_mail("RECHNUNG@x.invalid") == neu
    assert services.lieferant_zur_mail("") is None
    services.lieferant_aendern(neu, aktiv=False)
    assert services.lieferant_zur_mail("rechnung@x.invalid") is None


# --- Vorschlag -----------------------------------------------------------------------


def test_vorschlag_aus_frueheren_belegen_desselben_lieferanten(belegwelt: Belegwelt) -> None:
    """Schritt 3a, Aufgabe 4: Vorschlag für Objekt, Kostenart und Leistungsjahr aus früheren
    Belegen desselben Lieferanten; das Leistungsjahr hält den Abstand zum Belegdatum."""
    assert services.vorschlag(belegwelt.lieferant, TAG) is None
    jahresrechnung = services.beleg_bestaetigen(
        offener_beleg(belegwelt, "J-2025"),
        lieferant=belegwelt.lieferant,
        betrag=Decimal("500.00"),
        objekt=belegwelt.halle,
        kategorie=belegwelt.versicherung,
        leistungsjahr=TAG.year - 1,  # Rechnung im Folgejahr für das Vorjahr
        umlagefaehiger_anteil=Decimal("25"),
    )
    treffer = services.vorschlag(belegwelt.lieferant, datetime.date(2027, 2, 1))
    assert treffer is not None
    assert (treffer.objekt, treffer.kategorie, treffer.leistungsjahr) == (
        belegwelt.halle,
        belegwelt.versicherung,
        2026,
    )
    assert treffer.umlagefaehiger_anteil == Decimal("25.00")
    assert treffer.aus_beleg == jahresrechnung

    neuer = offener_beleg(belegwelt, "J-2026")
    assert (neuer.objekt, neuer.kategorie, neuer.leistungsjahr) == (
        belegwelt.halle,
        belegwelt.versicherung,
        TAG.year - 1,
    )


# --- Bestätigen ----------------------------------------------------------------------


def test_beleg_bestaetigen_mit_allen_feldern_schliesst_den_posten(belegwelt: Belegwelt) -> None:
    """Standardablauf 6, Schritt 3: Du bestätigst die Felder und nennst den umlagefähigen
    Anteil; ohne Angabe gilt die Vorgabe der Kostenart. Der Posten ist danach erledigt."""
    beleg = offener_beleg(belegwelt)
    posten = Arbeitsposten.objects.get(art=services.ART_BELEGE_ZUORDNEN, bezug_id=beleg.pk)
    beleg = services.beleg_bestaetigen(
        beleg,
        lieferant=belegwelt.lieferant,
        betrag=Decimal("119.00"),
        objekt=belegwelt.hof,
        kategorie=belegwelt.strom,
        leistungsjahr=2026,
        datum=datetime.date(2026, 11, 1),
    )
    assert beleg.status == Beleg.Status.BESTAETIGT and beleg.bestaetigt_am is not None
    assert beleg.umlagefaehiger_anteil == Decimal("50.00")  # Vorgabe der Kostenart Strom
    assert beleg.dokument.datum == datetime.date(2026, 11, 1)
    assert beleg.dokument.datei.name.endswith(f"beleg_2026-11-01_{beleg.dokument.pk}.pdf")
    posten.refresh_from_db()
    assert posten.status == Arbeitsposten.Status.ERLEDIGT
    assert list(services.dokumente_zu(belegwelt.lieferant)) == [beleg.dokument]


@pytest.mark.parametrize(
    ("feld", "wert", "meldung"),
    [
        ("betrag", None, "Betrag fehlt"),
        ("betrag", Decimal("-1"), "positiv"),
        ("betrag", Decimal("1.234"), "Nachkommastellen"),
        ("objekt", None, "Objekt, Kostenart und Leistungsjahr"),
        ("leistungsjahr", 1800, "unplausibel"),
        ("lieferant", None, "Lieferant fehlt"),
    ],
)
def test_bestaetigen_prueft_die_felder(
    belegwelt: Belegwelt, feld: str, wert: object, meldung: str
) -> None:
    werte: dict[str, object] = {
        "lieferant": belegwelt.lieferant,
        "betrag": Decimal("10.00"),
        "objekt": belegwelt.hof,
        "kategorie": belegwelt.strom,
        "leistungsjahr": 2026,
        feld: wert,
    }
    beleg = offener_beleg(belegwelt)
    with pytest.raises(services.BelegFehler, match=meldung):
        services.beleg_bestaetigen(beleg, **werte)  # type: ignore[arg-type]
    beleg.refresh_from_db()
    assert beleg.status == Beleg.Status.OFFEN


def test_kostenart_muss_eine_ausgabe_sein(belegwelt: Belegwelt) -> None:
    with pytest.raises(services.BelegFehler, match="keine Ausgabe"):
        services.beleg_bestaetigen(
            offener_beleg(belegwelt),
            lieferant=belegwelt.lieferant,
            betrag=Decimal("10.00"),
            objekt=belegwelt.hof,
            kategorie=belegwelt.miete,
            leistungsjahr=2026,
        )


def test_datenbank_sichert_bestaetigt_nur_vollstaendig(belegwelt: Belegwelt) -> None:
    """Abnahme Schritt 3 (Beleg mit allen Feldern): auch an den Services vorbei."""
    beleg = offener_beleg(belegwelt, lieferant=False)
    beleg.status = Beleg.Status.BESTAETIGT
    with pytest.raises(IntegrityError), transaction.atomic():
        beleg.save()


# --- Sammelrechnung (H6.4) -----------------------------------------------------------


def test_sammelrechnung_aufteilen(belegwelt: Belegwelt) -> None:
    """Standardablauf 6, Schritt 3 (H6.4): Sammelrechnungen aufteilen: ein Beleg je Objekt,
    alle auf demselben Dokument, zusammen genau der Gesamtbetrag."""
    beleg = bestaetigt(belegwelt, "1000.00")
    teile = services.beleg_aufteilen(
        beleg,
        [
            services.Belegteil(Decimal("600.00"), belegwelt.hof, belegwelt.versicherung, 2026),
            services.Belegteil(
                Decimal("400.00"), belegwelt.halle, belegwelt.versicherung, 2026, Decimal("0")
            ),
        ],
    )
    assert teile[0].pk == beleg.pk
    assert [(t.objekt, t.betrag) for t in teile] == [
        (belegwelt.hof, Decimal("600.00")),
        (belegwelt.halle, Decimal("400.00")),
    ]
    assert {t.dokument_id for t in teile} == {beleg.dokument_id}
    assert [t.umlagefaehiger_anteil for t in teile] == [Decimal("100.00"), Decimal("0.00")]
    assert all(t.status == Beleg.Status.BESTAETIGT for t in teile)

    with pytest.raises(services.BelegFehler, match="Gesamtbetrag"):
        services.beleg_aufteilen(
            teile[0],
            [
                services.Belegteil(Decimal("500.00"), belegwelt.hof, belegwelt.strom, 2026),
                services.Belegteil(Decimal("200.00"), belegwelt.hof, belegwelt.strom, 2026),
            ],
        )


# --- Buchung -------------------------------------------------------------------------


def test_beleg_an_buchung_und_kandidaten(belegwelt: Belegwelt) -> None:
    """Schritt 3a (H6.4): Der Beleg hängt an seiner Buchung; die Buchung bleibt unverändert,
    das Dokument hängt zusätzlich über einen Dokumentbezug an ihr."""
    beleg = bestaetigt(belegwelt, "119.00")
    passt = gueltige_buchung(belegwelt.ausgabe(datetime.date(2026, 11, 10), "119.00"))
    belegwelt.ausgabe(datetime.date(2026, 11, 11), "50.00", zweck="anderes")
    weit_weg = gueltige_buchung(belegwelt.ausgabe(datetime.date(2026, 3, 1), "119.00", zweck="alt"))

    assert services.buchung_kandidaten(beleg) == [passt]
    services.beleg_an_buchung(beleg, passt)
    beleg.refresh_from_db()
    assert beleg.buchung == passt
    assert list(services.dokumente_zu(passt)) == [beleg.dokument]
    assert services.buchung_kandidaten(bestaetigt(belegwelt, "119.00", "R-2")) == []
    assert weit_weg.pk not in [b.pk for b in services.buchung_kandidaten(beleg)]


def test_offener_beleg_haengt_nicht_an_buchung(belegwelt: Belegwelt) -> None:
    buchung = gueltige_buchung(belegwelt.ausgabe(TAG, "10.00"))
    with pytest.raises(services.BelegFehler, match="nicht bestätigt"):
        services.beleg_an_buchung(offener_beleg(belegwelt), buchung)


# --- Kein Beleg ----------------------------------------------------------------------


def test_mahnung_wird_am_lieferanten_abgelegt_ohne_buchung(belegwelt: Belegwelt) -> None:
    """Schritt 3a, Aufgabe 6 (H6.8): Mahnungen von Lieferanten und Ähnliches als eigene
    Dokumentart am Lieferanten, ohne Buchung und ohne Beleg."""
    ablage = services.lieferantendokument_ablegen(
        rechnung_pdf("M-1"),
        art=dokumentarten.MAHNUNG_LIEFERANT,
        lieferant=belegwelt.lieferant,
        datum=TAG,
    )
    assert ablage.dokument.art == "mahnung_lieferant"
    assert not ablage.dokument.belege.exists()
    assert list(services.dokumente_zu(belegwelt.lieferant)) == [ablage.dokument]
    with pytest.raises(services.AblageFehler):
        services.lieferantendokument_ablegen(
            rechnung_pdf("M-2"), art="beleg", lieferant=belegwelt.lieferant, datum=TAG
        )
    with pytest.raises(services.BelegFehler):
        services.beleg_anlegen(ablage.dokument)


def test_eingang_aus_dem_postfach_als_mahnung_umwidmen(belegwelt: Belegwelt) -> None:
    """Schritt 3a, Aufgabe 6 (H6.8): Ist ein Eingang eine Mahnung, wird der Beleg verworfen
    und das Dokument liegt als Mahnung am Lieferanten."""
    beleg = offener_beleg(belegwelt, lieferant=False)
    dokument = services.beleg_umwidmen(
        beleg, art=dokumentarten.MAHNUNG_LIEFERANT, lieferant=belegwelt.lieferant
    )
    beleg.refresh_from_db()
    assert beleg.status == Beleg.Status.VERWORFEN
    assert dokument.art == "mahnung_lieferant"
    assert dokument.datei.name.endswith(f"mahnung_lieferant_2026-11-03_{dokument.pk}.pdf")
    assert dokument.loeschfrist == datetime.date(2032, 12, 31)
    assert not Arbeitsposten.objects.filter(
        art=services.ART_BELEGE_ZUORDNEN, status="offen"
    ).exists()


def test_verwerfen_statt_loeschen(belegwelt: Belegwelt) -> None:
    beleg = offener_beleg(belegwelt)
    services.beleg_verwerfen(beleg)
    beleg.refresh_from_db()
    assert beleg.status == Beleg.Status.VERWORFEN
    assert Dokument.objects.filter(pk=beleg.dokument_id).exists()
    with pytest.raises(services.BelegFehler):
        services.beleg_bestaetigen(
            beleg,
            lieferant=belegwelt.lieferant,
            betrag=Decimal("1.00"),
            objekt=belegwelt.hof,
            kategorie=belegwelt.strom,
            leistungsjahr=2026,
        )
