"""Übergabe und Rückgabe (H2.4, H2.9, O1), Schlüssel (H1.5), Kaution (H2.10), Checklisten."""

from __future__ import annotations

import datetime
from decimal import Decimal

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse

from apps.fristen import services as fristen
from apps.fristen.models import Frist
from apps.kern.models import Arbeitsposten
from apps.stammdaten import schreiben, services
from apps.stammdaten.models import (
    Checkliste,
    Einheit,
    Kaution,
    Partei,
    Schluessel,
    Uebergabe,
    Vertrag,
)
from apps.zaehler import services as zaehler_services
from tests.stammdaten.conftest import foto

pytestmark = pytest.mark.django_db
D = datetime.date


@pytest.fixture
def bereit(entwurf: Vertrag, garage: Einheit) -> Vertrag:
    """Ein Entwurf mit Unterschrift, eingegangener Kaution und drei Schlüsseln beim Hof."""
    services.vertrag_unterschrieben(entwurf, D(2026, 11, 20))
    services.kaution_eingang_erfassen(entwurf.kaution, D(2026, 11, 25), "Vivid Kautionen")
    services.schluessel_anlegen(garage, anzahl=3, inhaber=Schluessel.Inhaber.HOF)
    return entwurf


def test_kein_schluessel_vor_unterschrift_und_kaution(entwurf: Vertrag) -> None:
    """SOP, Grundregel 5: Die Übergabe schließt erst nach Unterschrift und Kaution ab."""
    uebergabe = services.uebergabe_beginnen(entwurf, Uebergabe.Art.UEBERGABE, D(2026, 12, 1))

    hindernisse = services.uebergabe_hindernisse(uebergabe)
    assert len(hindernisse) == 3
    with pytest.raises(services.UebergabeNichtMoeglich, match="nicht unterschrieben"):
        services.uebergabe_abschliessen(uebergabe)
    entwurf.refresh_from_db()
    assert entwurf.status == Vertrag.Status.ENTWURF


def test_uebergabe_setzt_vertrag_aktiv(bereit: Vertrag, garage: Einheit, mieter: Partei) -> None:
    """Schritt 4, Aufgabe 4: Die Übergabe setzt den Vertrag aktiv; Schlüssel gehen gegen
    Quittung an den Mieter, die Einheit ist ab dem Datum vermietet. Schritt 8, Aufgabe 5:
    Der Zählerstand steht als Zählerstand „bei Übergabe“ am Zähler der Einheit."""
    zaehler = zaehler_services.zaehler_anlegen(
        "Z-7", "strom", einheit=garage, einbaudatum=D(2020, 1, 1), einbau_stand="0"
    )
    uebergabe = services.uebergabe_beginnen(bereit, Uebergabe.Art.UEBERGABE, D(2026, 12, 1))
    (schluessel,) = services.schluessel_der_uebergabe(uebergabe)
    services.uebergabe_speichern(
        uebergabe,
        zaehlerstaende=[{"zaehler": zaehler.pk, "stand": "123,4"}, {"zaehler": "", "stand": ""}],
        schluessel=[{"schluessel": schluessel.pk, "anzahl": 2}],
        zustand="sauber",
        unterschrift=foto("unterschrift.png"),
    )
    services.uebergabe_foto_hinzufuegen(uebergabe, foto())

    services.uebergabe_abschliessen(uebergabe)

    bereit.refresh_from_db()
    uebergabe.refresh_from_db()
    assert bereit.status == Vertrag.Status.AKTIV
    assert garage.status_am(D(2026, 12, 1)).status == "vermietet"  # type: ignore[union-attr]
    assert uebergabe.status == Uebergabe.Status.ABGESCHLOSSEN
    (stand,) = uebergabe.zaehlerstaende.all()
    assert (stand.zaehler, stand.datum, stand.stand) == (zaehler, D(2026, 12, 1), Decimal("123.4"))
    assert stand.bei_uebergabe and stand.art == "abgelesen"
    assert schreiben.kontext_uebergabe(uebergabe)["uebergabe"]["zaehlerstaende"] == [
        {"nummer": "Z-7", "stand": "123.4", "art": "abgelesen", "einheit": "NW-G-0001"}
    ]
    assert uebergabe.schluessel == [
        {"einheit": "NW-G-0001", "art": "schluessel", "bezeichnung": "", "anzahl": 2}
    ]
    bestand = {(s.inhaber, s.anzahl) for s in garage.schluessel.all()}
    assert bestand == {("partei", 2), ("hof", 1)}
    assert garage.schluessel.get(inhaber="partei").partei == mieter
    assert Arbeitsposten.objects.filter(
        art="Schreiben erstellen", hinweis__contains="uebergabeprotokoll"
    ).exists()
    with pytest.raises(services.UebergabeNichtMoeglich):
        services.uebergabe_speichern(uebergabe, zustand="später")


def test_zaehlerstand_muss_zahl_sein(bereit: Vertrag, garage: Einheit) -> None:
    zaehler_services.zaehler_anlegen("Z", "strom", einheit=garage)
    uebergabe = services.uebergabe_beginnen(bereit, Uebergabe.Art.UEBERGABE, D(2026, 12, 1))
    with pytest.raises(services.StammdatenFehler, match="keine Zahl"):
        services.uebergabe_speichern(uebergabe, zaehlerstaende=[{"nummer": "Z", "stand": "viel"}])
    with pytest.raises(services.StammdatenFehler, match="keiner Einheit"):
        services.uebergabe_speichern(uebergabe, zaehlerstaende=[{"nummer": "", "stand": "5"}])
    with pytest.raises(services.StammdatenFehler, match="gehört ein Stand"):
        services.uebergabe_speichern(uebergabe, zaehlerstaende=[{"nummer": "Z", "stand": ""}])
    assert not uebergabe.zaehlerstaende.exists()


@pytest.fixture
def gekuendigt(aktiver_vertrag: Vertrag, garage: Einheit, mieter: Partei) -> Vertrag:
    services.schluessel_anlegen(garage, anzahl=2, inhaber="partei", partei=mieter)
    services.kuendigung_erfassen(aktiver_vertrag, D(2026, 11, 3))
    aktiver_vertrag.refresh_from_db()
    return aktiver_vertrag


def test_rueckgabe_gibt_einheit_frei_und_stoesst_kautionsabrechnung_an(
    gekuendigt: Vertrag, garage: Einheit
) -> None:
    """Schritt 4, Aufgabe 4: Die Rückgabe gibt die Einheit frei und legt die
    Kautionsabrechnung als Arbeitsposten mit Frist an (rund sechs Monate, SOP)."""
    rueckgabe = services.uebergabe_beginnen(gekuendigt, Uebergabe.Art.RUECKGABE, D(2027, 1, 29))
    (schluessel,) = services.schluessel_der_uebergabe(rueckgabe)
    services.uebergabe_speichern(
        rueckgabe, schluessel=[{"schluessel": schluessel.pk, "anzahl": 1}], zurueckgelassen="Regal"
    )

    services.uebergabe_abschliessen(rueckgabe)

    gekuendigt.refresh_from_db()
    kaution = Kaution.objects.get(vertrag=gekuendigt)
    assert gekuendigt.status == Vertrag.Status.BEENDET
    assert gekuendigt.ende == D(2027, 1, 31)
    assert garage.status_am(D(2027, 1, 29)).status == "frei"  # type: ignore[union-attr]
    assert kaution.abrechnung_faellig_am == D(2027, 7, 29)
    posten = Arbeitsposten.objects.get(art="Kaution abrechnen")
    assert (posten.bezug, posten.faellig_am) == (kaution, D(2027, 7, 29))
    (frist,) = fristen.fristen_zu(kaution, nur_offen=True)
    assert (frist.art, frist.quelle, frist.datum) == (
        Frist.Art.KAUTIONSABRECHNUNG,
        Frist.Quelle.KAUTION,
        D(2027, 7, 29),
    )
    # Rückgabetermin und Fristende sind erledigt; ein Schlüssel fehlt.
    assert not fristen.fristen_zu(gekuendigt, nur_offen=True).exists()
    assert not fristen.fristen_zu(gekuendigt.kuendigungen.get(), nur_offen=True).exists()
    fehlt = Arbeitsposten.objects.get(art="Schlüssel fehlt")
    assert "NW-G-0001: 1 ×" in fehlt.hinweis
    assert {(s.inhaber, s.anzahl) for s in garage.schluessel.all()} == {("hof", 1), ("partei", 1)}
    # Checkliste für die Neuvermietung (Standardablauf 4, Schritt 6)
    assert garage.checklisten.filter(phase=Checkliste.Phase.VORBEREITUNG).exists()


def test_kaution_abrechnen(gekuendigt: Vertrag) -> None:
    """H2.10: Die Abrechnung entsteht aus der Vorlage; Arbeitsposten und Frist sind erledigt."""
    rueckgabe = services.uebergabe_beginnen(gekuendigt, Uebergabe.Art.RUECKGABE, D(2027, 1, 31))
    services.uebergabe_abschliessen(rueckgabe)
    kaution = Kaution.objects.get(vertrag=gekuendigt)

    with pytest.raises(services.StammdatenFehler):
        services.kaution_abrechnen(kaution, D(2027, 3, 1), Decimal("500.00"))
    services.kaution_abrechnen(kaution, D(2027, 3, 1), Decimal("30.00"), "Reinigung")

    kaution.refresh_from_db()
    assert (kaution.einbehalt, kaution.ausgezahlt) == (Decimal("30.00"), Decimal("150.00"))
    assert not Arbeitsposten.objects.filter(art="Kaution abrechnen", status="offen").exists()
    assert not fristen.fristen_zu(kaution, nur_offen=True).exists()
    assert Arbeitsposten.objects.filter(
        art="Schreiben erstellen", hinweis__startswith="Vorlagenart kautionsabrechnung"
    ).exists()
    with pytest.raises(services.StammdatenFehler):
        services.kaution_abrechnen(kaution, D(2027, 3, 2), Decimal("0.00"))


def test_schluessel_teilen_und_zusammenlegen(garage: Einheit, mieter: Partei) -> None:
    """H1.5: Bestand je Einheit und wer ihn hat; Ausgabe und Rücknahme teilen und legen
    zusammen."""
    bestand = services.schluessel_anlegen(garage, anzahl=3, inhaber=Schluessel.Inhaber.HOF)
    beim_mieter = services.schluessel_uebergeben(bestand, mieter, 2)
    assert beim_mieter.pk != bestand.pk
    bestand.refresh_from_db()
    assert bestand.anzahl == 1

    zurueck = services.schluessel_zuruecknehmen(beim_mieter, 2)
    assert zurueck.pk == bestand.pk and zurueck.anzahl == 3
    assert garage.schluessel.count() == 1
    with pytest.raises(services.StammdatenFehler):
        services.schluessel_uebergeben(zurueck, mieter, 4)
    with pytest.raises(services.StammdatenFehler):
        services.schluessel_anlegen(garage, inhaber=Schluessel.Inhaber.PARTEI)


def test_checkliste_abhaken(garage: Einheit) -> None:
    """Schritt 4, Aufgabe 1: Checkliste je Phase mit Arbeitsposten; sind alle Punkte erledigt,
    ist sie abgeschlossen und der Arbeitsposten erledigt."""
    liste = services.checkliste_anlegen(Checkliste.Phase.UEBERGABE, einheit=garage)
    posten = Arbeitsposten.objects.get(art="Checkliste offen")
    assert posten.status == "offen" and "NW-G-0001" in posten.hinweis

    for punkt in liste.punkte.all():
        services.checkpunkt_erledigen(punkt, D(2026, 12, 1))
    liste.refresh_from_db()
    posten.refresh_from_db()
    assert liste.abgeschlossen_am == D(2026, 12, 1)
    assert posten.status == "erledigt"
    services.checkpunkt_oeffnen(liste.punkte.first())  # type: ignore[arg-type]
    liste.refresh_from_db()
    assert liste.abgeschlossen_am is None


# --- Seite am Handy -------------------------------------------------------------------


def test_uebergabe_am_handy_in_schritten_mit_kamera(
    bereit: Vertrag, garage: Einheit, angemeldet: Client
) -> None:
    """Schritt 4, Aufgabe 4 (O1): eine Seite in Schritten, Fotos direkt aus der Kamera
    (accept=image/* capture=environment), am Ende ist der Vertrag aktiv. Schritt 8,
    Aufgabe 5: Der Schritt Zählerstände zeigt die Zähler der Einheit, mit Foto."""
    zaehler = zaehler_services.zaehler_anlegen("Z-1", "strom", einheit=garage)
    antwort = angemeldet.post(
        reverse("stammdaten:uebergabe_starten", args=[bereit.pk, "uebergabe"])
    )
    uebergabe = Uebergabe.objects.get(vertrag=bereit)
    assert antwort["Location"] == reverse("stammdaten:uebergabe", args=[uebergabe.pk, 1])

    seite = angemeldet.get(reverse("stammdaten:uebergabe", args=[uebergabe.pk, 3])).content.decode()
    assert 'name="viewport"' in seite
    assert (
        # Review 2, B8: Kameraknopf über die Dateiknöpfe (C4)
        'type="file" name="fotos" accept="image/*" capture="environment" multiple' in seite
    )
    assert "Schritt 3 von 7" in seite

    def schritt(n: int, daten: dict[str, object]) -> None:
        url = reverse("stammdaten:uebergabe", args=[uebergabe.pk, n])
        antwort = angemeldet.post(url, daten)
        assert antwort.status_code == 302, antwort.content.decode()[:300]

    zaehler_seite = angemeldet.get(
        reverse("stammdaten:uebergabe", args=[uebergabe.pk, 2])
    ).content.decode()
    assert "Strom Z-1" in zaehler_seite
    assert f'name="foto_{zaehler.pk}" accept="image/*" capture="environment"' in zaehler_seite

    schluessel = services.schluessel_der_uebergabe(uebergabe)[0]
    schritt(1, {"datum": "2026-12-01"})
    schritt(2, {f"stand_{zaehler.pk}": "77", f"foto_{zaehler.pk}": foto("z.png")})
    schritt(3, {"fotos": [foto("a.png"), foto("b.png")]})
    schritt(4, {f"schluessel_{schluessel.pk}": "2"})
    schritt(5, {"betrag": "180,00", "art": "ueberweisung", "eingang_am": "2026-11-25"})
    schritt(6, {"zustand": "gut", "unterschrift": foto("u.png")})
    schritt(7, {})

    bereit.refresh_from_db()
    assert bereit.status == Vertrag.Status.AKTIV
    assert uebergabe.fotos.count() == 2
    (stand,) = zaehler.staende.all()
    assert (stand.stand, stand.uebergabe, bool(stand.foto)) == (Decimal("77"), uebergabe, True)
    foto_seite = angemeldet.get(
        reverse("stammdaten:uebergabe_foto", args=[uebergabe.fotos.first().pk])
    )  # type: ignore[union-attr]
    assert foto_seite.status_code == 200


def test_nur_fotos_werden_angenommen(bereit: Vertrag, angemeldet: Client) -> None:
    uebergabe = services.uebergabe_beginnen(bereit, Uebergabe.Art.UEBERGABE, D(2026, 12, 1))
    pdf = SimpleUploadedFile("x.pdf", b"%PDF-1.4", content_type="application/pdf")
    antwort = angemeldet.post(
        reverse("stammdaten:uebergabe", args=[uebergabe.pk, 3]), {"fotos": [pdf]}, follow=True
    )
    assert "Nur Fotos" in antwort.content.decode()
    assert not uebergabe.fotos.exists()


def test_abschluss_mit_hindernis_bleibt_auf_der_seite(entwurf: Vertrag, angemeldet: Client) -> None:
    uebergabe = services.uebergabe_beginnen(entwurf, Uebergabe.Art.UEBERGABE, D(2026, 12, 1))
    antwort = angemeldet.post(reverse("stammdaten:uebergabe", args=[uebergabe.pk, 7]), follow=True)
    assert "Kaution" in antwort.content.decode()
    entwurf.refresh_from_db()
    assert entwurf.status == Vertrag.Status.ENTWURF
