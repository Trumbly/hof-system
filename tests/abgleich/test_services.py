"""Services des Abgleichs (Schritt 6): Stufen, Über- und Teilzahlung, Klärfälle, Storno,
Handzahlungen, Kaution, Summenprüfung in der Datenbank."""

from __future__ import annotations

import datetime
from decimal import Decimal

import pytest
from django.db import IntegrityError, transaction

from apps.abgleich import services
from apps.abgleich.models import Guthaben, Handzahlung, Klaerfall, Zuordnung
from apps.dokumente import services as dokumente
from apps.dokumente.models import Dokument
from apps.kassenbuch import services as kassenbuch
from apps.kern import services as kern
from apps.kern.models import Arbeitsposten
from apps.stammdaten import services as stammdaten
from testhof import pdfs
from tests.abgleich.conftest import Abgleichhof, klaerfaelle, neue_iban, posten

D = datetime.date
E = Decimal


def _beleg(text: str = "Quittung (erfunden)") -> Dokument:
    return services.beleg_ablegen(pdfs.einfaches_pdf([text]), datum=D(2026, 7, 3))


def test_stufe_1_regel_mit_vertrag_passt_genau_auf_aeltesten_posten(hof: Abgleichhof) -> None:
    """Abnahme Schritt 6: Eine Regel mit Vertrag und genau passendem Betrag bucht den
    ältesten offenen Posten; der Status ist danach „bezahlt“."""
    vertrag = hof.vertrag()
    iban = neue_iban()
    hof.regel(iban, vertrag=vertrag)
    buchung = hof.zahlung(D(2026, 7, 2), "85.00", iban=iban, zweck="Miete Juli")

    ergebnis = services.abgleichen()

    juli = posten(vertrag)[0]
    juli.refresh_from_db()
    assert (ergebnis.gebucht, ergebnis.arbeitsposten, ergebnis.je_stufe["regel"]) == (1, 0, 1)
    assert juli.status == "bezahlt" and juli.von == D(2026, 7, 1)
    assert services.offener_betrag(buchung) == 0
    # idempotent
    zweiter = services.abgleichen()
    assert (zweiter.gebucht, zweiter.arbeitsposten) == (0, 0)


def test_mehrere_monate_auf_einmal_sind_aelteste_posten(hof: Abgleichhof) -> None:
    vertrag = hof.vertrag()
    iban = neue_iban()
    hof.regel(iban, vertrag=vertrag)
    hof.zahlung(D(2026, 8, 3), "170.00", iban=iban)

    services.abgleichen()

    assert [p.status for p in posten(vertrag)][:3] == ["bezahlt", "bezahlt", "offen"]


def test_zweck_widerspricht_der_regel_wird_klaerfall(hof: Abgleichhof) -> None:
    """Abnahme Schritt 6 (Standardablauf 2): Der Zweck wird immer geprüft; nennt er eine
    andere Einheit, entsteht ein Klärfall, auch wenn der Betrag stimmt."""
    vertrag = hof.vertrag()
    anderer = hof.vertrag()
    iban = neue_iban()
    hof.regel(iban, vertrag=vertrag)
    fremde = anderer.einheiten.get().nw_id
    buchung = hof.zahlung(D(2026, 7, 2), "85.00", iban=iban, zweck=f"Miete {fremde}")

    ergebnis = services.abgleichen()

    assert ergebnis.gebucht == 0
    (fall,) = klaerfaelle(buchung)
    assert fall.art == Klaerfall.Art.ZWECK
    assert fall.vorschlag["vertrag"] == vertrag.pk
    assert anderer.pk in fall.vorschlag["alternativen"]
    assert not Zuordnung.objects.exists()
    posten_arbeit = services.klaerfall_posten(fall)
    assert posten_arbeit is not None and posten_arbeit.art == "Zahlungen klären"
    assert fremde in posten_arbeit.hinweis and "85" not in posten_arbeit.hinweis


def test_zweck_mit_eigener_einheit_bestaetigt(hof: Abgleichhof) -> None:
    vertrag = hof.vertrag()
    iban = neue_iban()
    hof.regel(iban, vertrag=vertrag)
    hof.zahlung(D(2026, 7, 2), "85.00", iban=iban, zweck=f"Miete {vertrag.einheiten.get().nw_id}")

    assert services.abgleichen().gebucht == 1


def test_mehrdeutig_zwei_gleich_gute_kandidaten_nie_gebucht(hof: Abgleichhof) -> None:
    """Abnahme Schritt 6: Unsichere Treffer werden nie von selbst gebucht. Absichtlich
    mehrdeutig: Jahreszahler mit zwei offenen Jahren gleicher Höhe."""
    vertrag = hof.vertrag(beginn=D(2025, 1, 1), zahlweise="jaehrlich", miete="50.00")
    # Ohne Beginn des Abgleichs liegen beide Jahresposten (2025 und 2026) im Abgleich.
    with services.beginn_setzen(None):
        jahre = [p for p in posten(vertrag) if p.art == "miete"]
        assert [p.betrag for p in jahre] == [E("600.00"), E("600.00")]
        iban = neue_iban()
        hof.regel(iban, vertrag=vertrag)
        buchung = hof.zahlung(D(2026, 7, 2), "600.00", iban=iban, zweck="Jahresmiete")
        # Stufe 1 trifft 2025 (ältester), Stufe 3 trifft 2025 und 2026: zwei Gruppen.
        ergebnis = services.abgleichen()

    assert ergebnis.gebucht == 0
    (fall,) = klaerfaelle(buchung)
    assert fall.art == Klaerfall.Art.MEHRDEUTIG
    assert not Zuordnung.objects.exists()
    assert all(p.status == "offen" for p in posten(vertrag))


def test_stufe_3_jahreszahler(hof: Abgleichhof) -> None:
    """Abnahme Schritt 6: Jahreszahler; der Betrag passt auf die Posten eines Jahres."""
    vertrag = hof.vertrag(beginn=D(2026, 1, 1), zahlweise="jaehrlich", miete="40.00")
    iban = neue_iban()
    hof.regel(iban, vertrag=vertrag)
    hof.zahlung(D(2026, 7, 2), "480.00", iban=iban)

    ergebnis = services.abgleichen()

    assert ergebnis.gebucht == 1
    assert {p.status for p in posten(vertrag)} == {"bezahlt"}


def test_stufe_3_jahr_aus_monatsposten(hof: Abgleichhof) -> None:
    """Stufe 3 mit Monatsposten: Die Zahlung deckt genau die offenen Posten eines Jahres."""
    vertrag = hof.vertrag(beginn=D(2026, 7, 1))
    jahr = services.offene_posten(vertrag)
    assert len(jahr) == 5
    iban = neue_iban()
    hof.regel(iban, vertrag=vertrag)
    hof.zahlung(D(2026, 11, 2), "425.00", iban=iban)

    services.abgleichen()

    assert {p.status for p in posten(vertrag)} == {"bezahlt"}


def test_stufe_2_sammelzahler(hof: Abgleichhof) -> None:
    """Abnahme Schritt 6: Sammelzahler; eine Überweisung für drei Verträge wird aufgeteilt."""
    a, b, c = hof.vertrag("85.00"), hof.vertrag("40.00"), hof.vertrag("120.00")
    iban = neue_iban()
    hof.regel(iban, vertraege=[a, b, c])
    buchung = hof.zahlung(D(2026, 7, 2), "245.00", iban=iban)

    ergebnis = services.abgleichen()

    assert ergebnis.je_stufe["sammel"] == 1
    for vertrag in (a, b, c):
        assert posten(vertrag)[0].status == "bezahlt"
    assert services.offener_betrag(buchung) == 0
    assert Zuordnung.objects.filter(buchung=buchung).count() == 3


def test_sammelzahler_mit_falscher_summe_wird_klaerfall(hof: Abgleichhof) -> None:
    a, b = hof.vertrag("85.00"), hof.vertrag("40.00")
    iban = neue_iban()
    hof.regel(iban, vertraege=[a, b])
    buchung = hof.zahlung(D(2026, 7, 2), "100.00", iban=iban)

    assert services.abgleichen().gebucht == 0
    assert klaerfaelle(buchung)[0].art == Klaerfall.Art.BETRAG


def test_ueberzahlung_wird_guthaben_und_mit_folgemonat_verrechnet(hof: Abgleichhof) -> None:
    """Abnahme Schritt 6: Überzahlung wird Guthaben; der Lauf verrechnet es mit dem nächsten
    fälligen Posten."""
    vertrag = hof.vertrag(beginn=D(2026, 10, 1))
    iban = neue_iban()
    hof.regel(iban, vertrag=vertrag)
    buchung = hof.zahlung(D(2026, 10, 2), "105.00", iban=iban)

    ergebnis = services.abgleichen()

    okt, nov = posten(vertrag)
    assert ergebnis.je_stufe["ueberzahlung"] == 1 and ergebnis.verrechnet == 1
    assert okt.status == "bezahlt"
    assert (nov.status, nov.bezahlt) == ("teilweise", E("20.00"))
    guthaben = Guthaben.objects.get(buchung=buchung)
    assert guthaben.verrechnet_mit == nov and not guthaben.offen
    assert services.guthaben_summe(vertrag) == 0
    assert services.offener_betrag(buchung) == 0


def test_teilzahlung_bei_eindeutiger_regel_laesst_rest_offen(hof: Abgleichhof) -> None:
    """Abnahme Schritt 6: Teilzahlung mit eindeutiger Regel wird gebucht, der Rest bleibt
    offen; die Nachzahlung deckt Rest und Folgemonat genau."""
    vertrag = hof.vertrag()
    iban = neue_iban()
    hof.regel(iban, vertrag=vertrag)
    hof.zahlung(D(2026, 7, 2), "50.00", iban=iban)

    assert services.abgleichen().je_stufe["teilzahlung"] == 1
    juli = posten(vertrag)[0]
    assert (juli.status, juli.offen) == ("teilweise", E("35.00"))

    hof.zahlung(D(2026, 8, 3), "120.00", iban=iban)
    assert services.abgleichen().je_stufe["regel"] == 1
    assert [p.status for p in posten(vertrag)][:2] == ["bezahlt", "bezahlt"]


def test_unklarer_betrag_wird_klaerfall_mit_vorschlag(hof: Abgleichhof) -> None:
    """Teilzahlung mit unklarem Betrag (mehr als der älteste Posten, aber keine Gruppe)
    wird nicht still gebucht: Klärfall mit Vorschlag."""
    vertrag = hof.vertrag()
    iban = neue_iban()
    hof.regel(iban, vertrag=vertrag)
    buchung = hof.zahlung(D(2026, 8, 3), "100.00", iban=iban)

    assert services.abgleichen().gebucht == 0
    (fall,) = klaerfaelle(buchung)
    assert fall.art == Klaerfall.Art.BETRAG
    assert fall.konfidenz == E("0.60")
    assert fall.vorschlag["posten"] == [
        [posten(vertrag)[0].pk, "85.00"],
        [posten(vertrag)[1].pk, "15.00"],
    ]


def test_erwarteter_betrag_der_regel_macht_teilzahlung_klar(hof: Abgleichhof) -> None:
    vertrag = hof.vertrag()
    iban = neue_iban()
    hof.regel(iban, vertrag=vertrag, betrag=E("100.00"))
    hof.zahlung(D(2026, 8, 3), "100.00", iban=iban)

    assert services.abgleichen().je_stufe["teilzahlung"] == 1


def test_dauerauftrag_mit_alter_miete(hof: Abgleichhof) -> None:
    """Abnahme Schritt 6: Dauerauftrag mit alter Miete nach einer Erhöhung: als Teilzahlung
    gebucht, die Differenz bleibt offen (Rückstand für das Mahnwesen)."""
    vertrag = hof.vertrag(beginn=D(2026, 6, 1), miete="80.00")
    stammdaten.kondition_ab(vertrag, D(2026, 7, 1), miete=E("90.00"), grund="Erhöhung")
    iban = neue_iban()
    hof.regel(iban, vertrag=vertrag)
    for monat in (7, 8):
        hof.zahlung(D(2026, monat, 2), "80.00", iban=iban, zweck=f"Dauerauftrag {monat}")

    ergebnis = services.abgleichen()

    assert ergebnis.je_stufe["teilzahlung"] == 2 and ergebnis.arbeitsposten == 0
    offen = sum(
        (p.offen for p in posten(vertrag) if D(2026, 7, 1) <= p.von < D(2026, 9, 1)), E("0")
    )
    assert offen == E("20.00")


def test_ohne_zahlerregel_nie_gebucht_vorschlag_aus_zweck(hof: Abgleichhof) -> None:
    """Abnahme Schritt 6: Fremder Zahler ohne Regel landet in „Zahlungen klären“; der
    Vorschlag kommt aus dem Zweck (hohe Konfidenz), gebucht wird nicht."""
    vertrag = hof.vertrag()
    buchung = hof.zahlung(D(2026, 7, 2), "85.00", iban=neue_iban(), zweck=f"Miete {vertrag.nummer}")

    assert services.abgleichen().gebucht == 0
    (fall,) = klaerfaelle(buchung)
    assert fall.art == Klaerfall.Art.UNBEKANNT
    assert (fall.vorschlag["vertrag"], fall.konfidenz) == (vertrag.pk, E("0.80"))
    assert not Zuordnung.objects.exists()


def test_klaerfall_aktionen(hof: Abgleichhof) -> None:
    """H3.6: Zuordnen, Aufteilen, Regel anlegen, nicht vertragsbezogen; danach erledigt."""
    vertrag = hof.vertrag()
    iban = neue_iban()
    erste = hof.zahlung(D(2026, 7, 2), "85.00", iban=iban, zweck="Miete")
    zweite = hof.zahlung(D(2026, 8, 3), "85.00", iban=iban, zweck="Miete August")
    services.abgleichen()
    fall_1, fall_2 = klaerfaelle()

    services.klaerfall_zuordnen(fall_1, vertrag)
    fall_1.refresh_from_db()
    assert fall_1.ergebnis == "zugeordnet"
    assert services.klaerfall_posten(fall_1).status == "erledigt"  # type: ignore[union-attr]
    assert posten(vertrag)[0].status == "bezahlt"

    # Regel anlegen: Die Buchung ist nach einer Regel ohne Zahlerregel gebucht (keine).
    ergebnis = services.klaerfall_regel_anlegen(fall_2, vertrag=vertrag)
    assert ergebnis.gebucht and ergebnis.neue_regel is not None
    assert posten(vertrag)[1].status == "bezahlt"
    assert services.offener_betrag(zweite) == 0 and services.offener_betrag(erste) == 0

    # Die nächste Zahlung bucht der Lauf über die neue Regel selbst.
    hof.zahlung(D(2026, 9, 2), "85.00", iban=iban, zweck="Miete September")
    assert services.abgleichen().gebucht == 1

    zins = hof.zahlung(D(2026, 9, 30), "1.23", iban=neue_iban(), zweck="Zinsen")
    services.abgleichen()
    (fall_3,) = klaerfaelle(zins)
    services.klaerfall_nicht_vertragsbezogen(fall_3)
    assert services.abgleichen().arbeitsposten == 0
    with pytest.raises(services.AbgleichFehler, match="geklärt"):
        services.klaerfall_zuordnen(fall_3, vertrag)


def test_aufteilen_muss_genau_aufgehen(hof: Abgleichhof) -> None:
    a, b = hof.vertrag(), hof.vertrag("40.00")
    buchung = hof.zahlung(D(2026, 7, 2), "130.00", iban=neue_iban())
    services.abgleichen()
    (fall,) = klaerfaelle(buchung)
    pa, pb = posten(a)[0], posten(b)[0]

    with pytest.raises(services.AbgleichFehler, match="genau"):
        services.klaerfall_aufteilen(fall, [services.Anteil(pa, E("85.00"))])
    services.klaerfall_aufteilen(
        fall,
        [services.Anteil(pa, E("85.00")), services.Anteil(pb, E("40.00"))],
        guthaben=E("5.00"),
        guthaben_vertrag=b,
    )
    assert services.guthaben_summe(b) == E("5.00")
    assert services.offener_betrag(buchung) == 0


def test_summe_je_buchung_und_je_posten_hoechstens_betrag(hof: Abgleichhof) -> None:
    """Datenbank-Prüfung: Die Summe der Zuordnungen je Buchung ist höchstens der
    Buchungsbetrag, je Posten höchstens der Postenbetrag (Service und Trigger)."""
    vertrag = hof.vertrag()
    buchung = hof.zahlung(D(2026, 7, 2), "100.00", iban=neue_iban())
    juli, august = posten(vertrag)[:2]

    with pytest.raises(services.AbgleichFehler, match="offenen Betrag"):
        services.zuordnen(buchung, [services.Anteil(juli, E("90.00"))])
    with pytest.raises(services.AbgleichFehler, match="übersteigen"):
        services.zuordnen(
            buchung, [services.Anteil(juli, E("85.00")), services.Anteil(august, E("20.00"))]
        )
    # Am Service vorbei greift der Trigger der Datenbank.
    with pytest.raises(IntegrityError), transaction.atomic():
        for _ in range(2):
            Zuordnung(
                buchung=buchung, sollposten=juli, betrag=E("60.00"), datum=D(2026, 7, 2)
            ).save()
    with pytest.raises(IntegrityError), transaction.atomic():
        Zuordnung(buchung=buchung, sollposten=juli, betrag=E("100.00"), datum=D(2026, 7, 2)).save()


def test_zuordnung_wird_nie_geaendert_nur_storniert(hof: Abgleichhof) -> None:
    vertrag = hof.vertrag()
    iban = neue_iban()
    hof.regel(iban, vertrag=vertrag)
    buchung = hof.zahlung(D(2026, 7, 2), "85.00", iban=iban)
    services.abgleichen()
    zuordnung = Zuordnung.objects.get(buchung=buchung)

    with pytest.raises(Exception), transaction.atomic():  # noqa: B017 (Trigger)
        Zuordnung.objects.filter(pk=zuordnung.pk).update(betrag=E("1.00"))
    services.zuordnung_stornieren(zuordnung, "falsch zugeordnet")
    juli = posten(vertrag)[0]
    assert juli.status == "offen" and juli.bezahlt == 0
    with pytest.raises(services.AbgleichFehler, match="schon storniert"):
        services.zuordnung_stornieren(zuordnung, "nochmal")
    # Einmal zugeordnet, jetzt wieder offen: nie von selbst, sondern Klärfall.
    ergebnis = services.abgleichen()
    assert ergebnis.gebucht == 0 and klaerfaelle(buchung)[0].art == Klaerfall.Art.ERNEUT


def test_ruecknahme_im_protokoll_storniert_zuordnung(hof: Abgleichhof) -> None:
    from pghistory.models import Events

    vertrag = hof.vertrag()
    iban = neue_iban()
    hof.regel(iban, vertrag=vertrag)
    buchung = hof.zahlung(D(2026, 7, 2), "85.00", iban=iban)
    services.abgleichen()
    zuordnung = Zuordnung.objects.get(buchung=buchung)
    ereignis = Events.objects.get(
        pgh_obj_model="abgleich.Zuordnung", pgh_obj_id=str(zuordnung.pk), pgh_label="insert"
    )

    kern.zuruecknehmen(ereignis.pgh_slug)

    zuordnung.refresh_from_db()
    assert zuordnung.storniert_am is not None
    assert Zuordnung.objects.filter(pk=zuordnung.pk).exists()  # nicht gelöscht
    assert posten(vertrag)[0].status == "offen"


def test_storno_im_kassenbuch_loest_die_zuordnung(hof: Abgleichhof) -> None:
    vertrag = hof.vertrag()
    iban = neue_iban()
    hof.regel(iban, vertrag=vertrag)
    buchung = hof.zahlung(D(2026, 7, 2), "85.00", iban=iban)
    services.abgleichen()
    assert posten(vertrag)[0].status == "bezahlt"

    kassenbuch.umsatz_stornieren(buchung.umsatz, neu_einordnen=False)
    ergebnis = services.abgleichen()

    assert ergebnis.geloest == 1
    assert posten(vertrag)[0].status == "offen"


def test_rueckbuchung_wird_klaerfall_und_loest_nach_bestaetigung(hof: Abgleichhof) -> None:
    """Abnahme Schritt 6 (Sonderfall Rückbuchung): Die Rückbuchung einer zugeordneten
    Zahlung wird ein Klärfall; erst die Bestätigung löst die Zuordnung."""
    vertrag = hof.vertrag()
    iban = neue_iban()
    hof.regel(iban, vertrag=vertrag)
    buchung = hof.zahlung(D(2026, 7, 2), "85.00", iban=iban)
    services.abgleichen()
    from tests.kassenbuch.conftest import umsatz

    umsatz(hof.klein.konto, D(2026, 7, 5), "85.00", eingang=False, iban=iban, zweck="Retoure")

    ergebnis = services.abgleichen()

    (fall,) = klaerfaelle(buchung)
    assert fall.art == Klaerfall.Art.RUECKBUCHUNG and ergebnis.arbeitsposten == 1
    assert posten(vertrag)[0].status == "bezahlt"  # noch nicht gelöst
    services.rueckbuchung_bestaetigen(fall)
    assert posten(vertrag)[0].status == "offen"
    assert services.abgleichen().arbeitsposten == 0


def test_wohnraum_geht_nie_in_den_abgleich(hof: Abgleichhof) -> None:
    """Abnahme Schritt 6: Buchungen mit Wohnraum-Merkmal gehen nie in den Abgleich."""
    hof.vertrag()
    buchung = hof.zahlung(D(2026, 7, 2), "85.00", iban=neue_iban(), kategorie=hof.klein.wohnraum)
    assert buchung.wohnraum

    ergebnis = services.abgleichen()

    assert (ergebnis.gebucht, ergebnis.arbeitsposten) == (0, 0)
    with pytest.raises(services.AbgleichFehler, match="Wohnraum"):
        services.zuordnen(buchung, [], guthaben=E("1.00"))


def test_barzahlung_mit_grund_und_beleg(hof: Abgleichhof) -> None:
    """Abnahme Schritt 6 (H3.7): Barzahlung ohne Bankumsatz, Grund und Beleg Pflicht."""
    vertrag = hof.vertrag()
    with pytest.raises(services.AbgleichFehler, match="Grund"):
        services.handzahlung_erfassen(
            vertrag, "bar", datum=D(2026, 7, 3), betrag=E("85.00"), grund=" ", beleg=_beleg()
        )
    with pytest.raises(services.AbgleichFehler, match="Beleg"):
        services.handzahlung_erfassen(
            vertrag,
            "bar",
            datum=D(2026, 7, 3),
            betrag=E("85.00"),
            grund="bar",
            beleg=None,  # type: ignore[arg-type]
        )
    handzahlung = services.handzahlung_erfassen(
        vertrag, "bar", datum=D(2026, 7, 3), betrag=E("90.00"), grund="Quittung", beleg=_beleg()
    )
    assert handzahlung.beleg.art == "beleg"
    assert list(dokumente.dokumente_zu(handzahlung)) == [handzahlung.beleg]
    with pytest.raises(services.AbgleichFehler, match="ablegen"):
        services.beleg_ablegen(b"kein PDF", datum=D(2026, 7, 3))
    juli, august = posten(vertrag)[:2]
    assert juli.status == "bezahlt"
    assert (august.status, august.bezahlt) == ("teilweise", E("5.00"))

    services.handzahlung_stornieren(handzahlung, "Doppelt erfasst")
    assert {p.status for p in posten(vertrag)} == {"offen"}


def test_erstattung_zahlt_guthaben_aus(hof: Abgleichhof) -> None:
    """H3.7: Erstattung eines Guthabens; der Betrag darf das Guthaben nicht übersteigen."""
    vertrag = hof.vertrag(beginn=D(2026, 11, 1))
    services.handzahlung_erfassen(
        vertrag,
        "fremdes_konto",
        datum=D(2026, 11, 2),
        betrag=E("100.00"),
        grund="Privatkonto",
        beleg=_beleg(),
    )
    assert services.guthaben_summe(vertrag) == E("15.00")
    with pytest.raises(services.AbgleichFehler, match="übersteigt"):
        services.handzahlung_erfassen(
            vertrag,
            "erstattung",
            datum=D(2026, 11, 5),
            betrag=E("20.00"),
            grund="zurück",
            beleg=_beleg(),
        )
    erstattung = services.handzahlung_erfassen(
        vertrag,
        "erstattung",
        datum=D(2026, 11, 5),
        betrag=E("10.00"),
        grund="zurück",
        beleg=_beleg(),
    )
    assert services.guthaben_summe(vertrag) == E("5.00")
    services.handzahlung_stornieren(erstattung, "nicht überwiesen")
    assert services.guthaben_summe(vertrag) == E("15.00")
    assert Handzahlung.objects.filter(storniert_am__isnull=False).count() == 1


def test_kaution_eines_entwurfs_wird_ueber_die_bank_erkannt(hof: Abgleichhof) -> None:
    """Standardablauf 1, Schritt 6: Die Kaution eines neuen Vertrags wird Posten; ihr
    Eingang über die Bank trägt den Eingang an der Kaution ein."""
    vertrag = hof.vertrag(beginn=D(2026, 12, 1), status="entwurf", kaution=E("170.00"))
    iban = neue_iban()
    hof.regel(iban, vertrag=vertrag)
    hof.zahlung(D(2026, 11, 25), "170.00", iban=iban, zweck=f"Kaution {vertrag.nummer}")

    ergebnis = services.abgleichen()

    assert ergebnis.kautionsposten == 1 and ergebnis.gebucht == 1
    (kaution,) = posten(vertrag)
    assert (kaution.art, kaution.status) == ("kaution", "bezahlt")
    vertrag.kaution.refresh_from_db()
    assert vertrag.kaution.eingang_am == D(2026, 11, 25)
    assert services.abgleichen().kautionsposten == 0


def test_rueckstaende_nach_alter(hof: Abgleichhof) -> None:
    """H3.11: Rückstände je Vertrag und Summe, nach Alter 0–30, 31–60, über 60 Tage; ohne
    Bankdaten sind fällige Posten nicht prüfbar."""
    vertrag = hof.vertrag()
    iban = neue_iban()
    hof.zahlung(D(2026, 11, 18), "0.01", iban=iban, zweck="Test")  # Bankdaten bis 18.11.

    stand = services.rueckstaende(D(2026, 11, 20))

    (eintrag,) = stand.vertraege
    # Juli bis November fällig: Nov (≤30), Okt (31–60), Jul–Sep (über 60).
    assert eintrag.bis_30 == E("85.00")
    assert eintrag.bis_60 == E("85.00")
    assert eintrag.ueber_60 == E("255.00")
    zahlen = stand.kennzahlen()
    assert zahlen["vertraege_mit_rueckstand"] == 1
    assert zahlen["nw_ids"] == [vertrag.einheiten.get().nw_id]
    assert "Beispiel" not in str(zahlen)


def test_rueckstand_nicht_pruefbar_nach_bankluecke(hof: Abgleichhof) -> None:
    """Standardablauf 2, Schritt 6: Eine Lücke in den Bankdaten gilt nie als Rückstand."""
    vertrag = hof.vertrag(beginn=D(2026, 11, 1))
    hof.zahlung(D(2026, 10, 30), "1.00", iban=neue_iban(), zweck="alt")  # Daten bis 30.10.

    (eintrag,) = services.rueckstaende(D(2026, 11, 20)).vertraege

    assert eintrag.summe == 0 and eintrag.nicht_pruefbar == E("85.00")
    assert eintrag.vertrag == vertrag


def test_zahlerregel_braucht_genau_ein_ziel(hof: Abgleichhof) -> None:
    vertrag = hof.vertrag()
    regel = hof.regel(neue_iban())
    with pytest.raises(services.AbgleichFehler, match="genau eins"):
        services.zahlerregel_setzen(regel)
    with pytest.raises(services.AbgleichFehler, match="genau eins"):
        services.zahlerregel_setzen(regel, vertrag=vertrag, nicht_vertragsbezogen=True)
    zahlerregel = services.zahlerregel_setzen(regel, vertraege=[vertrag])
    assert zahlerregel.vertrag == vertrag  # eine Liste mit einem Vertrag ist ein Vertrag


def test_arbeitsposten_hinweis_ohne_betraege_und_namen(hof: Abgleichhof) -> None:
    """KI-Grenze: Hinweise nennen IDs und Daten, keine Beträge, Namen oder Zwecke."""
    hof.vertrag()
    hof.zahlung(D(2026, 7, 2), "987.65", iban=neue_iban(), zweck="Geheimer Zweck")
    services.abgleichen()
    hinweis = Arbeitsposten.objects.get(art=services.ART_KLAEREN).hinweis
    for verboten in ("987.65", "987,65", "Geheimer", "Erika", "Beispiel"):
        assert verboten not in hinweis


def test_eingang_eines_lieferanten_nie_im_mieterabgleich(hof: Abgleichhof) -> None:
    """Erstattungen und Rücklastschriften von Lieferanten (Eingänge) werden ein Klärfall
    „Eingang eines Lieferanten“, nie gegen Sollposten gebucht: auf einer Ausgabe-Kategorie
    oder von einer IBAN, an die das Hof-System Ausgaben zahlt."""
    from tests.kassenbuch.conftest import umsatz

    vertrag = hof.vertrag()
    erstattung = hof.zahlung(
        D(2026, 7, 2),
        "85.00",
        iban=neue_iban(),
        zweck=f"Gutschrift {vertrag.nummer}",
        kategorie=hof.klein.strom,
    )
    lieferant = neue_iban()
    ausgabe = umsatz(hof.klein.konto, D(2026, 7, 1), "40.00", eingang=False, iban=lieferant)
    kassenbuch.umsatz_zuordnen(ausgabe, kategorie=hof.klein.strom, objekt=hof.klein.hof)
    ruecklastschrift = hof.zahlung(D(2026, 7, 9), "85.00", iban=lieferant, zweck="Rückgabe")

    ergebnis = services.abgleichen()

    assert ergebnis.gebucht == 0 and ergebnis.je_art["lieferant"] == 2
    for buchung in (erstattung, ruecklastschrift):
        (fall,) = klaerfaelle(buchung)
        assert fall.art == Klaerfall.Art.LIEFERANT and not fall.vorschlag
        assert services.klaerfall_posten(fall).aktionen == list(services.AKTIONEN_LIEFERANT)  # type: ignore[union-attr]
    assert posten(vertrag)[0].status == "offen"


def test_kachel_rueckstaende_nur_zaehler_und_summen(hof: Abgleichhof) -> None:
    """Service für das Hof-Cockpit (Schritt 9b): Zähler und Summen, keine Namen."""
    from django.template.loader import render_to_string

    hof.vertrag()
    hof.zahlung(D(2026, 11, 18), "0.01", iban=neue_iban(), zweck="Stand")
    kontext = services.kachel_rueckstaende()
    assert kontext["anzahl"] == 1 and kontext["summe"] == E("425.00")
    html = render_to_string("abgleich/_kachel_rueckstaende.html", kontext)
    assert html.count('<li id="kachel-rueckstaende">') == 1 and "Erika" not in html
