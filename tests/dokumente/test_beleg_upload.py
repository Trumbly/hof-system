"""Upload am Handy und Seite des Belegs (Schritt 3a, Aufgabe 4; H6.2)."""

from __future__ import annotations

import datetime
from decimal import Decimal

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse

from apps.dokumente import services
from apps.dokumente.models import Beleg, Dokument, Lieferant
from apps.kern.models import Arbeitsposten
from tests.dokumente.conftest import Belegwelt, gueltige_buchung, rechnung_pdf
from tests.dokumente.test_ablage import foto

pytestmark = pytest.mark.django_db


def test_foto_vom_handy_wird_beleg_mit_allen_feldern(
    angemeldet: Client, belegwelt: Belegwelt
) -> None:
    """Abnahme Schritt 3: Ein Foto vom Handy wird zum Beleg mit Objekt, Kostenart,
    Leistungsjahr und umlagefähigem Anteil (Felder von Hand, keine Texterkennung)."""
    seite = angemeldet.get(reverse("dokumente:beleg_hochladen")).content.decode()
    assert 'accept="image/*,application/pdf"' in seite
    assert 'capture="environment"' in seite
    assert 'enctype="multipart/form-data"' in seite

    bild = foto("lightgray")
    antwort = angemeldet.post(
        reverse("dokumente:beleg_hochladen"),
        {
            "datei": SimpleUploadedFile("IMG_0001.jpg", bild, content_type="image/jpeg"),
            "art": "beleg",
            "lieferant": belegwelt.lieferant.pk,
            "datum": "2026-11-12",
            "betrag": "84,50",
            "objekt": belegwelt.halle.pk,
            "kategorie": belegwelt.strom.pk,
            "leistungsjahr": "2026",
            "umlagefaehiger_anteil": "30",
        },
    )

    beleg = Beleg.objects.select_related("dokument").get()
    assert antwort.status_code == 302, antwort.content.decode()[:800]
    assert antwort["Location"] == reverse("dokumente:beleg", args=[beleg.pk])
    assert beleg.status == Beleg.Status.BESTAETIGT
    assert (beleg.lieferant, beleg.betrag, beleg.objekt, beleg.kategorie) == (
        belegwelt.lieferant,
        Decimal("84.50"),
        belegwelt.halle,
        belegwelt.strom,
    )
    assert (beleg.leistungsjahr, beleg.umlagefaehiger_anteil) == (2026, Decimal("30.00"))
    dokument = beleg.dokument
    assert dokument.herkunft == Dokument.Herkunft.UPLOAD
    assert dokument.datum == datetime.date(2026, 11, 12)
    assert dokument.datei.name.endswith(".pdf") and dokument.original.name.endswith(".jpg")
    assert "IMG_0001" not in dokument.datei.name
    assert not Arbeitsposten.objects.filter(art=services.ART_BELEGE_ZUORDNEN).exists()


def test_upload_mit_neuem_lieferanten_und_vorschlag(
    angemeldet: Client, belegwelt: Belegwelt
) -> None:
    """Schritt 3a, Aufgabe 4: Leere Felder füllt der Vorschlag aus früheren Belegen; fehlt der
    Betrag, bleibt der Beleg offen mit Arbeitsposten."""
    angemeldet.post(
        reverse("dokumente:beleg_hochladen"),
        {
            "datei": SimpleUploadedFile("a.pdf", rechnung_pdf("A"), content_type="application/pdf"),
            "art": "beleg",
            "lieferant_neu": "Winterdienst Erfunden",
            "datum": "2026-01-15",
            "betrag": "120.00",
            "objekt": belegwelt.hof.pk,
            "kategorie": belegwelt.versicherung.pk,
            "leistungsjahr": "2025",
        },
    )
    lieferant = Lieferant.objects.get(name="Winterdienst Erfunden")

    antwort = angemeldet.post(
        reverse("dokumente:beleg_hochladen"),
        {
            "datei": SimpleUploadedFile("b.pdf", rechnung_pdf("B"), content_type="application/pdf"),
            "art": "beleg",
            "lieferant": lieferant.pk,
            "datum": "2027-01-20",
        },
    )

    neu = Beleg.objects.order_by("-pk").first()
    assert neu is not None
    assert antwort["Location"] == reverse("dokumente:beleg", args=[neu.pk])
    assert neu.status == Beleg.Status.OFFEN
    assert (neu.objekt, neu.kategorie, neu.leistungsjahr) == (
        belegwelt.hof,
        belegwelt.versicherung,
        2026,
    )
    assert Arbeitsposten.objects.filter(
        art=services.ART_BELEGE_ZUORDNEN, bezug_id=neu.pk, status="offen"
    ).exists()


def test_vorschlag_per_htmx(angemeldet: Client, belegwelt: Belegwelt) -> None:
    """Schritt 3a, Aufgabe 4: Die Wahl des Lieferanten füllt Objekt, Kostenart und
    Leistungsjahr aus seinen früheren Belegen vor."""
    services.beleg_hochladen(
        rechnung_pdf("V"),
        datum=datetime.date(2026, 5, 1),
        lieferant=belegwelt.lieferant,
        betrag=Decimal("10.00"),
        objekt=belegwelt.halle,
        kategorie=belegwelt.versicherung,
        leistungsjahr=2026,
    )
    seite = angemeldet.get(reverse("dokumente:beleg_hochladen")).content.decode()
    assert f'hx-get="{reverse("dokumente:beleg_vorschlag")}"' in seite

    teil = angemeldet.get(
        reverse("dokumente:beleg_vorschlag"),
        {"lieferant": belegwelt.lieferant.pk, "datum": "2026-11-01"},
        headers={"HX-Request": "true"},
    ).content.decode()
    assert 'id="beleg-felder"' in teil
    assert f'<option value="{belegwelt.halle.pk}" selected>' in teil
    assert f'<option value="{belegwelt.versicherung.pk}" selected>' in teil
    assert 'value="2026"' in teil
    assert "Vorschlag aus Beleg" in teil


def test_dublette_beim_upload(angemeldet: Client, belegwelt: Belegwelt) -> None:
    daten = {"art": "beleg", "datum": "2026-11-12"}
    for _ in range(2):
        antwort = angemeldet.post(
            reverse("dokumente:beleg_hochladen"),
            {**daten, "datei": SimpleUploadedFile("x.pdf", rechnung_pdf(), "application/pdf")},
            follow=True,
        )
    assert "liegt schon in der Ablage" in antwort.content.decode()
    assert Dokument.objects.count() == 1


def test_kein_pdf_kein_foto(angemeldet: Client, belegwelt: Belegwelt) -> None:
    antwort = angemeldet.post(
        reverse("dokumente:beleg_hochladen"),
        {
            "art": "beleg",
            "datum": "2026-11-12",
            "datei": SimpleUploadedFile("x.txt", b"Hallo", "text/plain"),
        },
    )
    assert antwort.status_code == 200
    assert "weder ein PDF noch ein lesbares Foto" in antwort.content.decode()
    assert not Dokument.objects.exists()


def test_mahnung_hochladen_legt_am_lieferanten_ab(angemeldet: Client, belegwelt: Belegwelt) -> None:
    """Schritt 3a, Aufgabe 6 (H6.8): Upload als Mahnung: kein Beleg, Dokument am Lieferanten."""
    antwort = angemeldet.post(
        reverse("dokumente:beleg_hochladen"),
        {
            "art": "mahnung_lieferant",
            "lieferant": belegwelt.lieferant.pk,
            "datum": "2026-11-12",
            "datei": SimpleUploadedFile("m.pdf", rechnung_pdf("M"), "application/pdf"),
        },
    )
    dokument = Dokument.objects.get()
    assert antwort["Location"] == reverse("dokumente:dokument", args=[dokument.pk])
    assert dokument.art == "mahnung_lieferant" and not Beleg.objects.exists()
    seite = angemeldet.get(reverse("dokumente:lieferant", args=[belegwelt.lieferant.pk]))
    assert f"Dokument #{dokument.pk}" in seite.content.decode()


def test_arbeitsposten_fuehrt_zur_seite_des_belegs_und_bestaetigen(
    angemeldet: Client, belegwelt: Belegwelt
) -> None:
    """Standardablauf 6, Schritt 3 über die Oberfläche: Arbeitsposten „Belege zuordnen“ →
    Seite des Belegs → Felder bestätigen → passende Buchung wählen."""
    dokument = services.dokument_ablegen(
        rechnung_pdf(), art="beleg", datum=datetime.date(2026, 11, 2), herkunft="postfach"
    ).dokument
    beleg = services.beleg_anlegen(dokument)
    posten = Arbeitsposten.objects.get(art=services.ART_BELEGE_ZUORDNEN)
    buchung = gueltige_buchung(belegwelt.ausgabe(datetime.date(2026, 11, 5), "119.00"))

    weiter = angemeldet.post(
        reverse("kern:arbeitsposten_aktion", args=[posten.pk, "beleg_zuordnen"])
    )
    assert weiter["Location"] == reverse("dokumente:beleg", args=[beleg.pk])
    liste = angemeldet.get(reverse("kern:arbeitslisten")).content.decode()
    assert "Belege zuordnen" in liste and "Beleg ansehen" in liste

    antwort = angemeldet.post(
        weiter["Location"],
        {
            "lieferant": belegwelt.lieferant.pk,
            "datum": "2026-11-02",
            "betrag": "119.00",
            "objekt": belegwelt.hof.pk,
            "kategorie": belegwelt.strom.pk,
            "leistungsjahr": "2026",
            "umlagefaehiger_anteil": "",
        },
    )
    assert antwort.status_code == 302, antwort.content.decode()[:800]
    posten.refresh_from_db()
    assert posten.status == "erledigt"
    seite = angemeldet.get(weiter["Location"]).content.decode()
    assert f"Buchung #{buchung.pk}" in seite

    angemeldet.post(reverse("dokumente:beleg_buchung", args=[beleg.pk]), {"buchung": buchung.pk})
    beleg.refresh_from_db()
    assert beleg.buchung == buchung


def test_pflichtfelder_auf_der_seite_des_belegs(angemeldet: Client, belegwelt: Belegwelt) -> None:
    dokument = services.dokument_ablegen(
        rechnung_pdf(), art="beleg", datum=datetime.date(2026, 11, 2), herkunft="postfach"
    ).dokument
    beleg = services.beleg_anlegen(dokument)
    antwort = angemeldet.post(reverse("dokumente:beleg", args=[beleg.pk]), {"datum": "2026-11-02"})
    assert antwort.status_code == 200
    assert "Der Lieferant fehlt." in antwort.content.decode()
    beleg.refresh_from_db()
    assert beleg.status == Beleg.Status.OFFEN


def test_aufteilen_ueber_die_seite(angemeldet: Client, belegwelt: Belegwelt) -> None:
    beleg = services.beleg_hochladen(
        rechnung_pdf("S"),
        datum=datetime.date(2026, 1, 10),
        lieferant=belegwelt.lieferant,
        betrag=Decimal("1000.00"),
        objekt=belegwelt.hof,
        kategorie=belegwelt.versicherung,
        leistungsjahr=2026,
    ).beleg
    assert beleg is not None
    seite = angemeldet.get(reverse("dokumente:beleg_aufteilen", args=[beleg.pk]))
    assert seite.status_code == 200
    daten = {"teil-TOTAL_FORMS": "2", "teil-INITIAL_FORMS": "2"}
    for nummer, (betrag, objekt) in enumerate(
        [("600.00", belegwelt.hof), ("400.00", belegwelt.halle)]
    ):
        daten |= {
            f"teil-{nummer}-betrag": betrag,
            f"teil-{nummer}-objekt": str(objekt.pk),
            f"teil-{nummer}-kategorie": str(belegwelt.versicherung.pk),
            f"teil-{nummer}-leistungsjahr": "2026",
        }
    antwort = angemeldet.post(reverse("dokumente:beleg_aufteilen", args=[beleg.pk]), daten)
    assert antwort.status_code == 302
    assert sorted(Beleg.objects.values_list("betrag", flat=True)) == [
        Decimal("400.00"),
        Decimal("600.00"),
    ]


def test_seiten_der_ablage_nur_mit_anmeldung(client: Client) -> None:
    for name, argumente in [
        ("dokumente:ablage", []),
        ("dokumente:beleg_hochladen", []),
        ("dokumente:beleg", [1]),
        ("dokumente:belegmappe", []),
        ("dokumente:belegmappe_pdf", []),
        ("dokumente:lieferanten", []),
    ]:
        antwort = client.get(reverse(name, args=argumente))
        assert antwort.status_code == 302, name
        assert antwort["Location"].startswith(reverse("account_login"))


def test_lieferanten_anlegen_und_aendern(angemeldet: Client, belegwelt: Belegwelt) -> None:
    angemeldet.post(
        reverse("dokumente:lieferanten"),
        {"name": "Müllabfuhr Erfunden", "email": "m@x.invalid", "iban": "", "aktiv": "on"},
    )
    neu = Lieferant.objects.get(name="Müllabfuhr Erfunden")
    angemeldet.post(
        reverse("dokumente:lieferant", args=[neu.pk]),
        {"name": "Müllabfuhr Erfunden", "email": "neu@x.invalid", "iban": ""},
    )
    neu.refresh_from_db()
    assert (neu.email, neu.aktiv) == ("neu@x.invalid", False)
