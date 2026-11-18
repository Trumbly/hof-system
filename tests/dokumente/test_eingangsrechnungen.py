"""Eingangsrechnungen: Posten, Frist, Erinnerung, Überfällig, Abgleich im Kassenbuch
(Schritt 3b, H6.1)."""

from __future__ import annotations

import datetime
import io
from decimal import Decimal

import pytest
from django.core.management import call_command
from django.test import Client
from django.urls import reverse

from apps.dokumente import services
from apps.dokumente.models import Beleg, Eingangsrechnung
from apps.fristen import services as fristen_services
from apps.fristen.models import Frist
from apps.kassenbuch import services as kassenbuch_services
from apps.kassenbuch.models import Zuordnungsregel
from apps.kern import aufgaben
from apps.kern.models import Arbeitsposten, Lauf
from tests.dokumente.conftest import Belegwelt, gueltige_buchung, rechnung_pdf

pytestmark = pytest.mark.django_db

RECHNUNGSDATUM = datetime.date(2026, 11, 2)
FAELLIG = datetime.date(2026, 11, 16)


@pytest.fixture
def gemeldet(monkeypatch: pytest.MonkeyPatch) -> list[tuple[int, str]]:
    """Was an das Hof-Backlog ginge (``kern.aufgaben.melden``): Posten-ID und Status."""
    liste: list[tuple[int, str]] = []
    monkeypatch.setattr(aufgaben, "melden", lambda p: liste.append((p.pk, p.status)))
    return liste


def rechnung(
    belegwelt: Belegwelt,
    betrag: str = "238.00",
    nummer: str = "RE-4711",
    pdf: str = "R-1",
    faellig_am: datetime.date = FAELLIG,
) -> Eingangsrechnung:
    beleg = services.beleg_hochladen(
        rechnung_pdf(pdf, betrag),
        datum=RECHNUNGSDATUM,
        lieferant=belegwelt.lieferant,
        betrag=Decimal(betrag),
        objekt=belegwelt.hof,
        kategorie=belegwelt.strom,
        leistungsjahr=2026,
        faellig_am=faellig_am,
        rechnungsnummer=nummer,
    ).beleg
    assert beleg is not None
    return Eingangsrechnung.objects.get(beleg=beleg)


def regel_fuer_lieferant(belegwelt: Belegwelt) -> Zuordnungsregel:
    return kassenbuch_services.regel_anlegen(
        "Lieferant Strom",
        gegen_iban=belegwelt.lieferant.iban,
        richtung="ausgang",
        kategorie=belegwelt.strom,
        objekt=belegwelt.hof,
    )


def einordnen() -> Lauf:
    call_command("kassenbuch_einordnen", stdout=io.StringIO())
    return Lauf.objects.filter(name="kassenbuch_einordnen").first()  # type: ignore[return-value]


def test_beleg_mit_faelligkeit_wird_rechnung_mit_posten_und_frist(
    belegwelt: Belegwelt, gemeldet: list[tuple[int, str]]
) -> None:
    """Schritt 3b, Aufgabe 2 und Abnahme Schritt 3: Beim Bestätigen eines Belegs mit
    Fälligkeit entstehen die Eingangsrechnung, ein Arbeitsposten „Rechnung zahlen“ (als
    Aufgabe ans Hof-Backlog gemeldet) und eine Frist mit drei Tagen Vorlauf."""
    eintrag = rechnung(belegwelt)

    assert (eintrag.betrag, eintrag.rechnungsnummer, eintrag.faellig_am, eintrag.status) == (
        Decimal("238.00"),
        "RE-4711",
        FAELLIG,
        Eingangsrechnung.Status.OFFEN,
    )
    posten = Arbeitsposten.objects.get(art=services.ART_RECHNUNG_ZAHLEN)
    assert posten.bezug == eintrag and posten.faellig_am == FAELLIG
    assert "16.11.2026" in posten.hinweis
    assert "238" not in posten.hinweis and belegwelt.lieferant.name not in posten.hinweis
    assert gemeldet == [(posten.pk, "offen")]
    frist = Frist.objects.get(quelle=Frist.Quelle.EINGANGSRECHNUNG)
    assert (frist.art, frist.datum, frist.vorlauf_tage, frist.bezug) == (
        Frist.Art.ZAHLUNGSZIEL,
        FAELLIG,
        3,
        eintrag,
    )


def test_erinnerung_drei_tage_vor_faelligkeit_und_danach_ueberfaellig(
    belegwelt: Belegwelt,
) -> None:
    """Schritt 3b, Aufgabe 3: Drei Tage vor Fälligkeit eine Erinnerung (Lauf fristen,
    Posten „Frist fällig“, gemeldet von der Benachrichtigung); nach der Fälligkeit ist der
    Posten „Rechnung zahlen“ als überfällig markiert."""
    eintrag = rechnung(belegwelt)
    frist = Frist.objects.get(quelle=Frist.Quelle.EINGANGSRECHNUNG)

    call_command("fristen", "--stichtag", "2026-11-12", stdout=io.StringIO())
    assert not Arbeitsposten.objects.filter(art=fristen_services.ART_FRIST_FAELLIG).exists()

    call_command("fristen", "--stichtag", "2026-11-13", stdout=io.StringIO())
    erinnerung = Arbeitsposten.objects.get(art=fristen_services.ART_FRIST_FAELLIG)
    assert erinnerung.bezug == frist
    posten = Arbeitsposten.objects.get(art=services.ART_RECHNUNG_ZAHLEN)
    assert not posten.hinweis.startswith("Überfällig")

    call_command("fristen", "--stichtag", "2026-11-16", stdout=io.StringIO())
    posten.refresh_from_db()
    assert not posten.hinweis.startswith("Überfällig")  # am Tag der Fälligkeit noch nicht

    call_command("fristen", "--stichtag", "2026-11-17", stdout=io.StringIO())
    posten.refresh_from_db()
    eintrag.refresh_from_db()
    assert posten.hinweis.startswith("Überfällig seit 17.11.2026.")
    assert eintrag.ueberfaellig_seit == datetime.date(2026, 11, 17)
    lauf = Lauf.objects.filter(name="fristen").first()
    assert lauf is not None and "Rechnungen überfällig: 1." in lauf.meldung

    call_command("fristen", "--stichtag", "2026-11-18", stdout=io.StringIO())
    posten.refresh_from_db()
    assert posten.hinweis.startswith("Überfällig seit 17.11.2026.")  # nur einmal markiert


def test_abbuchung_mit_iban_schliesst_rechnung_posten_und_frist(
    belegwelt: Belegwelt, gemeldet: list[tuple[int, str]]
) -> None:
    """Schritt 3b, Aufgabe 4 und Abnahme Schritt 3: Der Kassenbuch-Lauf erkennt die
    Abbuchung (Betrag und Gegen-IBAN), verbindet Buchung und Rechnung, schließt Posten und
    Frist und hängt den Beleg an die Buchung."""
    regel_fuer_lieferant(belegwelt)
    eintrag = rechnung(belegwelt)
    posten = Arbeitsposten.objects.get(art=services.ART_RECHNUNG_ZAHLEN)
    umsatz = belegwelt.ausgabe(datetime.date(2026, 11, 14), "238.00", zweck="Strom", buchen=False)

    lauf = einordnen()

    eintrag.refresh_from_db()
    buchung = gueltige_buchung(umsatz)
    assert eintrag.status == Eingangsrechnung.Status.BEZAHLT
    assert (eintrag.bezahlt_durch, eintrag.bezahlt_am) == (buchung, umsatz.buchungsdatum)
    posten.refresh_from_db()
    assert posten.status == Arbeitsposten.Status.ERLEDIGT
    assert gemeldet[-1] == (posten.pk, "erledigt")
    assert not fristen_services.fristen_zu(eintrag, nur_offen=True).exists()
    beleg = Beleg.objects.get(pk=eintrag.beleg_id)
    assert beleg.buchung == buchung
    assert list(services.dokumente_zu(buchung)) == [beleg.dokument]
    assert "Eingangsrechnungen: 1 verbunden, 0 Arbeitsposten" in lauf.meldung
    assert (
        services.buchungen_ohne_beleg(
            kassenbuch_services.gueltige_buchungen().filter(pk=buchung.pk)
        )
        == []
    )


def test_rechnungsnummer_im_zweck_genuegt(belegwelt: Belegwelt) -> None:
    """Schritt 3b, Aufgabe 4: Zahlt jemand von einem anderen Konto, erkennt die
    Rechnungsnummer im Verwendungszweck die Abbuchung (auch mit anderen Leerzeichen)."""
    regel = kassenbuch_services.regel_anlegen(
        "Sammelkonto",
        zweck="Rechnung",
        richtung="ausgang",
        kategorie=belegwelt.strom,
        objekt=belegwelt.hof,
    )
    assert regel.pk
    eintrag = rechnung(belegwelt)
    belegwelt.ausgabe(
        datetime.date(2026, 11, 14),
        "238.00",
        zweck="Rechnung re 4711 vom 02.11.",
        iban="DE02120300000000202051",
        buchen=False,
    )
    einordnen()
    eintrag.refresh_from_db()
    assert eintrag.status == Eingangsrechnung.Status.OFFEN  # „re 4711“ ist nicht „RE-4711“

    belegwelt.ausgabe(
        datetime.date(2026, 11, 15),
        "238.00",
        zweck="Rechnung RE- 4711",
        iban="DE02120300000000202051",
        buchen=False,
    )
    einordnen()
    eintrag.refresh_from_db()
    assert eintrag.status == Eingangsrechnung.Status.BEZAHLT
    assert "RE- 4711" in eintrag.bezahlt_durch.umsatz.verwendungszweck  # type: ignore[union-attr]


def test_gleicher_betrag_ohne_iban_und_nummer_ist_kein_treffer(belegwelt: Belegwelt) -> None:
    """Nur der Betrag reicht nicht: Es braucht Gegen-IBAN oder Rechnungsnummer."""
    kassenbuch_services.regel_anlegen(
        "Alles", zweck="Strom", richtung="ausgang", kategorie=belegwelt.strom, objekt=belegwelt.hof
    )
    eintrag = rechnung(belegwelt)
    belegwelt.ausgabe(
        datetime.date(2026, 11, 14),
        "238.00",
        zweck="Strom",
        iban="DE02120300000000202051",
        buchen=False,
    )
    einordnen()
    eintrag.refresh_from_db()
    assert eintrag.status == Eingangsrechnung.Status.OFFEN
    assert not Arbeitsposten.objects.filter(art=services.ART_RECHNUNG_ZUORDNEN).exists()


def test_zwei_rechnungen_gleichen_betrags_werden_arbeitsposten(belegwelt: Belegwelt) -> None:
    """Schritt 3b: Unsichere Treffer, etwa zwei Rechnungen mit gleichem Betrag beim selben
    Lieferanten, werden ein Arbeitsposten „Rechnung zuordnen“, nie automatisch verbunden."""
    regel_fuer_lieferant(belegwelt)
    erste = rechnung(belegwelt, nummer="A-1", pdf="A")
    zweite = rechnung(belegwelt, nummer="A-2", pdf="B")
    umsatz = belegwelt.ausgabe(datetime.date(2026, 11, 14), "238.00", zweck="Strom", buchen=False)

    lauf = einordnen()
    einordnen()  # idempotent: kein zweiter Posten

    for eintrag in (erste, zweite):
        eintrag.refresh_from_db()
        assert eintrag.status == Eingangsrechnung.Status.OFFEN
    posten = Arbeitsposten.objects.filter(art=services.ART_RECHNUNG_ZUORDNEN, status="offen")
    assert {p.bezug for p in posten} == {erste, zweite}
    assert all(f"Umsatz #{umsatz.pk}" in p.hinweis for p in posten)
    assert "Eingangsrechnungen: 0 verbunden, 2 Arbeitsposten" in lauf.meldung
    assert Beleg.objects.filter(buchung__isnull=False).count() == 0


def test_nummer_im_zweck_entscheidet_zwischen_zwei_rechnungen(belegwelt: Belegwelt) -> None:
    regel_fuer_lieferant(belegwelt)
    erste = rechnung(belegwelt, nummer="A-1", pdf="A")
    zweite = rechnung(belegwelt, nummer="A-2", pdf="B")
    belegwelt.ausgabe(datetime.date(2026, 11, 14), "238.00", zweck="Rechnung A-2", buchen=False)
    einordnen()
    erste.refresh_from_db()
    zweite.refresh_from_db()
    assert (erste.status, zweite.status) == ("offen", "bezahlt")


def test_unsicheren_treffer_auf_der_seite_der_rechnung_zuordnen(
    angemeldet: Client, belegwelt: Belegwelt
) -> None:
    """Schritt 3b: Max wählt die Abbuchung; der Posten „Rechnung zuordnen“ schließt sich."""
    regel_fuer_lieferant(belegwelt)
    erste = rechnung(belegwelt, nummer="A-1", pdf="A")
    rechnung(belegwelt, nummer="A-2", pdf="B")
    umsatz = belegwelt.ausgabe(datetime.date(2026, 11, 14), "238.00", zweck="Strom", buchen=False)
    einordnen()
    posten = Arbeitsposten.objects.get(
        art=services.ART_RECHNUNG_ZUORDNEN, bezug_id=erste.pk, status="offen"
    )

    weiter = angemeldet.post(
        reverse("kern:arbeitsposten_aktion", args=[posten.pk, "rechnung_ansehen"])
    )
    assert weiter["Location"] == reverse("dokumente:rechnung", args=[erste.pk])
    seite = angemeldet.get(weiter["Location"]).content.decode()
    assert f"Umsatz #{umsatz.pk}" in seite
    angemeldet.post(reverse("dokumente:rechnung_verbinden", args=[erste.pk]), {"umsatz": umsatz.pk})

    erste.refresh_from_db()
    posten.refresh_from_db()
    assert erste.status == Eingangsrechnung.Status.BEZAHLT
    assert posten.status == Arbeitsposten.Status.ERLEDIGT
    assert angemeldet.get(reverse("dokumente:rechnungen")).status_code == 200


def test_sammelrechnung_haengt_jeden_teil_an_seine_buchung(belegwelt: Belegwelt) -> None:
    """H6.4 mit 3b: Eine aufgeteilte Rechnung, bezahlt mit einer aufgeteilten Abbuchung."""
    eintrag = rechnung(belegwelt, betrag="1000.00")
    teile = services.beleg_aufteilen(
        eintrag.beleg,
        [
            services.Belegteil(Decimal("600.00"), belegwelt.hof, belegwelt.versicherung, 2026),
            services.Belegteil(Decimal("400.00"), belegwelt.halle, belegwelt.versicherung, 2026),
        ],
    )
    umsatz = belegwelt.ausgabe(
        datetime.date(2026, 11, 14), "1000.00", zweck="Rechnung RE-4711", buchen=False
    )
    kassenbuch_services.umsatz_buchen(
        umsatz,
        [
            kassenbuch_services.Teil(Decimal("600.00"), belegwelt.versicherung, belegwelt.hof),
            kassenbuch_services.Teil(Decimal("400.00"), belegwelt.versicherung, belegwelt.halle),
        ],
    )
    einordnen()
    eintrag.refresh_from_db()
    assert eintrag.status == Eingangsrechnung.Status.BEZAHLT
    for teil in teile:
        teil.refresh_from_db()
        assert teil.buchung is not None
        assert (teil.buchung.objekt, teil.buchung.betrag) == (teil.objekt, teil.betrag)


def test_strittig_wird_nie_automatisch_verbunden(belegwelt: Belegwelt) -> None:
    regel_fuer_lieferant(belegwelt)
    eintrag = services.rechnung_strittig(rechnung(belegwelt))
    belegwelt.ausgabe(datetime.date(2026, 11, 14), "238.00", zweck="RE-4711", buchen=False)
    einordnen()
    eintrag.refresh_from_db()
    assert eintrag.status == Eingangsrechnung.Status.STRITTIG
    services.rechnung_strittig(eintrag, strittig=False)
    einordnen()
    eintrag.refresh_from_db()
    assert eintrag.status == Eingangsrechnung.Status.BEZAHLT
    with pytest.raises(services.RechnungFehler):
        services.rechnung_strittig(eintrag)


def test_faelligkeit_aendern_verschiebt_die_frist(belegwelt: Belegwelt) -> None:
    eintrag = rechnung(belegwelt)
    services.eingangsrechnung_anlegen(
        eintrag.beleg, faellig_am=datetime.date(2026, 11, 30), rechnungsnummer="RE-4711"
    )
    frist = Frist.objects.get(quelle=Frist.Quelle.EINGANGSRECHNUNG, erledigt_am=None)
    assert frist.datum == datetime.date(2026, 11, 30)
    assert Eingangsrechnung.objects.count() == 1
    assert Arbeitsposten.objects.filter(art=services.ART_RECHNUNG_ZAHLEN).count() == 1


def test_faelligkeit_ueber_die_seite_des_belegs(angemeldet: Client, belegwelt: Belegwelt) -> None:
    """Schritt 3b über die Oberfläche: Felder bestätigen mit Fälligkeit und Nummer."""
    beleg = services.beleg_anlegen(
        services.dokument_ablegen(
            rechnung_pdf(), art="beleg", datum=RECHNUNGSDATUM, herkunft="postfach"
        ).dokument
    )
    daten = {
        "lieferant": belegwelt.lieferant.pk,
        "datum": "2026-11-02",
        "betrag": "238,00",
        "objekt": belegwelt.hof.pk,
        "kategorie": belegwelt.strom.pk,
        "leistungsjahr": "2026",
        "faellig_am": "2026-11-16",
        "rechnungsnummer": "RE-4711",
    }
    antwort = angemeldet.post(reverse("dokumente:beleg", args=[beleg.pk]), daten)
    assert antwort.status_code == 302, antwort.content.decode()[:500]
    eintrag = Eingangsrechnung.objects.get(beleg=beleg)
    assert eintrag.rechnungsnummer == "RE-4711"
    seite = angemeldet.get(reverse("dokumente:beleg", args=[beleg.pk])).content.decode()
    assert (
        f'Eingangsrechnung</dt><dd><a href="{reverse("dokumente:rechnung", args=[eintrag.pk])}"'
        in seite
    )
    # Nochmals speichern legt nichts doppelt an.
    angemeldet.post(reverse("dokumente:beleg", args=[beleg.pk]), daten)
    assert Arbeitsposten.objects.filter(art=services.ART_RECHNUNG_ZAHLEN).count() == 1
    liste = angemeldet.get(reverse("kern:arbeitslisten")).content.decode()
    assert "Rechnung zahlen" in liste and "Zum Beleg" in liste


def test_bezahlter_beleg_bekommt_keine_rechnung(belegwelt: Belegwelt) -> None:
    beleg = services.beleg_hochladen(
        rechnung_pdf(),
        datum=RECHNUNGSDATUM,
        lieferant=belegwelt.lieferant,
        betrag=Decimal("10.00"),
        objekt=belegwelt.hof,
        kategorie=belegwelt.strom,
        leistungsjahr=2026,
    ).beleg
    assert beleg is not None
    services.beleg_an_buchung(
        beleg, gueltige_buchung(belegwelt.ausgabe(RECHNUNGSDATUM, "10.00", zweck="bar"))
    )
    with pytest.raises(services.RechnungFehler):
        services.beleg_bestaetigen(
            beleg,
            lieferant=belegwelt.lieferant,
            betrag=Decimal("10.00"),
            objekt=belegwelt.hof,
            kategorie=belegwelt.strom,
            leistungsjahr=2026,
            faellig_am=FAELLIG,
        )


def test_aufgaben_melden_tut_vorerst_nichts() -> None:
    """Schritt 3b, Aufgabe 5: Die Schnittstelle zum Hof-Backlog gibt es, sie tut noch nichts."""
    from apps.kern import aufgaben as echt

    assert echt.melden(Arbeitsposten(art="Rechnung zahlen")) is None
