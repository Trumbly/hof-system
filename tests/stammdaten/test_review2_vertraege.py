"""Review 2, R2-7: Verträge und Einheiten (Teil B, Punkte 5 bis 8 und 11).

Buchungsanfrage → Vertrag (B5), neuer Vertrag (B6), Vertragsseite und Einheitenseite (B7),
Übergabe am Handy (B8), Listen Einheiten, Parteien und Verträge (B11). Alle Namen und
Adressen sind erfunden (``.invalid``).
"""

from __future__ import annotations

import datetime
import json
import re
from decimal import Decimal

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.anbindungen import services as anbindungen
from apps.anbindungen.models import Buchungsanfrage
from apps.dokumente.models import Schreiben, Vorlage
from apps.kern import zeit
from apps.kern.models import Objekt
from apps.stammdaten import einheitenliste, services, views
from apps.stammdaten.models import Einheit, Kontaktweg, Partei, Uebergabe, Vertrag
from apps.stammdaten.vertragsseite import protokoll_url
from apps.zaehler import services as zaehler_services
from tests.stammdaten.conftest import garage_anlegen, mieter_anlegen, vertrag_anlegen

pytestmark = pytest.mark.django_db
D = datetime.date
HTMX = {"HTTP_HX_REQUEST": "true"}
DIALOG = {**HTMX, "HTTP_HX_TARGET": "dialog-inhalt"}
WUNSCH = D(2027, 1, 1)


def _anfrage(
    einheit: Einheit, *, name: str = "Paula Probe", email: str = "paula@testhof.invalid"
) -> Buchungsanfrage:
    anfrage, _ = anbindungen.ereignis_annehmen(
        anbindungen.WebsiteEreignis(
            ereignis_id=f"w-{einheit.nw_id}-{email}",
            art="buchung",
            nw_id=einheit.nw_id,
            gemeldet_am=timezone.now(),
            wunsch_ab=WUNSCH,
            kontakt_name=name,
            kontakt_email=email,
            kontakt_telefon="0151 0000000",
        )
    )
    return anfrage


def _formular_daten(initial: dict[str, object], **mehr: object) -> dict[str, object]:
    """POST-Daten so, wie der Browser das vorausgefüllte Formular sendet."""
    daten = {k: ("" if v is None else v) for k, v in initial.items()}
    daten.setdefault("zahlweise", "monatlich")
    if isinstance(daten.get("miete"), Decimal):
        daten["miete"] = f"{daten['miete']:.2f}".replace(".", ",")
    if isinstance(daten.get("beginn"), datetime.date):
        daten["beginn"] = daten["beginn"].isoformat()  # type: ignore[union-attr]
    return daten | mehr


# --- B5: Buchungsanfrage → Vertrag ---------------------------------------------------------


def test_annehmen_und_vertrag_anlegen_vorausgefuellt(objekt: Objekt, angemeldet: Client) -> None:
    """Review 2, B5: „Annehmen und Vertrag anlegen“ öffnet den neuen Vertrag vorausgefüllt
    (Einheit, Beginn = Wunschtermin, Miete = Zielmiete, Partei aus den Kontaktdaten). Ohne
    Partei mit gleicher E-Mail wird eine neue angelegt, mit E-Mail und Telefon. Danach
    verweisen Anfrage und Vertrag aufeinander."""
    einheit = garage_anlegen(objekt, zielmiete="55.00")
    anfrage = _anfrage(einheit)
    seite = angemeldet.get(reverse("anbindungen:anfrage", args=[anfrage.pk])).content.decode()
    url = reverse("stammdaten:vertrag_neu") + f"?anfrage={anfrage.pk}"
    assert f'hx-get="{url}"' in seite and "Annehmen und Vertrag anlegen" in seite

    dialog = angemeldet.get(url, **DIALOG)
    form = dialog.context["form"]
    assert form.initial["einheit"] == einheit.pk
    assert form.initial["beginn"] == WUNSCH
    assert form.initial["miete"] == Decimal("55.00")
    assert form.initial["mieter"] == "neu"
    assert (form.initial["neu_vorname"], form.initial["neu_nachname"]) == ("Paula", "Probe")
    html = dialog.content.decode()
    assert 'class="dialog-formular"' in html and "<html" not in html
    assert 'name="neu_email"' in html and "paula@testhof.invalid" in html

    antwort = angemeldet.post(url, _formular_daten(form.initial), **DIALOG)
    vertrag = Vertrag.objects.get(vertragseinheiten__einheit=einheit)
    assert antwort["HX-Redirect"] == reverse("stammdaten:vertrag", args=[vertrag.pk])
    assert vertrag.beginn == WUNSCH and vertrag.konditionen.get().miete == Decimal("55.00")
    partei = vertrag.vertragsparteien.get().partei
    assert (partei.vorname, partei.nachname) == ("Paula", "Probe")
    assert set(partei.kontaktwege.values_list("art", "wert")) == {
        (Kontaktweg.Art.EMAIL, "paula@testhof.invalid"),
        (Kontaktweg.Art.TELEFON, "0151 0000000"),
    }
    anfrage.refresh_from_db()
    assert (anfrage.status, anfrage.vertrag) == ("vertrag_angelegt", vertrag)

    vertragsseite = angemeldet.get(antwort["HX-Redirect"]).content.decode()
    assert reverse("anbindungen:anfrage", args=[anfrage.pk]) in vertragsseite
    assert "Buchungsanfrage <a href=" in vertragsseite  # Meldung mit Link
    anfrageseite = angemeldet.get(reverse("anbindungen:anfrage", args=[anfrage.pk]))
    assert reverse("stammdaten:vertrag", args=[vertrag.pk]) in anfrageseite.content.decode()


def test_vorhandene_partei_mit_gleicher_email(objekt: Objekt, angemeldet: Client) -> None:
    """Review 2, B5: Gibt es eine Partei mit derselben E-Mail-Adresse, ist sie vorgeschlagen;
    es entsteht keine zweite."""
    einheit = garage_anlegen(objekt)
    bekannt = mieter_anlegen("Bekannt")  # bekannt@testhof.invalid
    anfrage = _anfrage(einheit, name="B. Bekannt", email="Bekannt@Testhof.invalid")
    url = reverse("stammdaten:vertrag_neu") + f"?anfrage={anfrage.pk}"
    dialog = angemeldet.get(url, **DIALOG)
    form = dialog.context["form"]
    assert form.initial["mieter"] == str(bekannt.pk)
    assert f'value="{bekannt.pk}" checked' in dialog.content.decode()
    anzahl = Partei.objects.count()

    angemeldet.post(url, _formular_daten(form.initial), **DIALOG)
    assert Partei.objects.count() == anzahl
    vertrag = Vertrag.objects.get(vertragseinheiten__einheit=einheit)
    assert vertrag.vertragsparteien.get().partei == bekannt


def test_anfrage_seite_im_neuen_stil(objekt: Objekt, angemeldet: Client) -> None:
    """Review 2, B5: Die Anfrage-Seite hat einen Kopf mit Kontaktdaten, Einheit und Aktionen
    (Hauptaktion „Annehmen und Vertrag anlegen“, daneben annehmen und ablehnen)."""
    einheit = garage_anlegen(objekt, zielmiete="61.00")
    anfrage = _anfrage(einheit)
    seite = angemeldet.get(reverse("anbindungen:anfrage", args=[anfrage.pk])).content.decode()
    assert 'class="seitenkopf' in seite and 'class="fakten"' in seite
    assert "Paula Probe" in seite and 'href="mailto:paula@testhof.invalid"' in seite
    assert 'href="tel:01510000000"' in seite
    assert einheit.nw_id in seite and "61,00\xa0€" in seite and "01.01.2027" in seite
    assert seite.count("knopf-primaer") == 1
    assert "Nur annehmen" in seite and "Ablehnen" in seite


# --- B6: Neuer Vertrag --------------------------------------------------------------------


def test_einheitenauswahl_nur_frei_mit_schalter(
    objekt: Objekt, mieter: Partei, angemeldet: Client
) -> None:
    """Review 2, B6: Die Einheitenliste zeigt nur freie Einheiten mit Typ und Zielmiete;
    der Schalter „alle“ zeigt auch vermietete."""
    frei = garage_anlegen(objekt, zielmiete="55.00")
    vermietet = garage_anlegen(objekt)
    vertrag_anlegen(vermietet, mieter, status="aktiv")
    url = reverse("stammdaten:vertrag_neu")

    html = angemeldet.get(url).content.decode()
    assert f'value="{frei.pk}"' in html and f'value="{vermietet.pk}"' not in html
    assert f"{frei.nw_id} · Garage · Garage Test · Ziel 55,00\xa0€" in html

    alle = angemeldet.get(url, {"alle": "on", "teil": "einheit"}, **HTMX).content.decode()
    assert 'id="vertrag-neu-einheit"' in alle and "<html" not in alle
    assert f'value="{vermietet.pk}"' in alle and "vermietet" in alle


def test_miete_aus_zielmiete_vorbelegt(objekt: Objekt, angemeldet: Client) -> None:
    """Review 2, B6: Die Miete ist aus der Zielmiete vorbelegt, auch beim Wechsel der Einheit
    (die Optionen tragen die Zielmiete für bausteine.js)."""
    einheit = garage_anlegen(objekt, zielmiete="72.50")
    antwort = angemeldet.get(reverse("stammdaten:vertrag_neu"), {"einheit": einheit.pk})
    assert antwort.context["form"].initial["miete"] == Decimal("72.50")
    html = antwort.content.decode()
    assert 'name="miete" value="72,50"' in html
    werte = json.dumps({"miete": "72,50"}).replace('"', "&quot;")
    assert f'data-werte="{werte}"' in html


def test_partei_per_suchfeld(objekt: Objekt, angemeldet: Client) -> None:
    """Review 2, B6: Die Partei wählt man per Suchfeld mit Namen (Treffer als Auswahl)."""
    gesucht = mieter_anlegen("Suchmann")
    mieter_anlegen("Andere")
    url = reverse("stammdaten:partei_suche")
    treffer = angemeldet.get(url, {"partei_suche": "such"}, **HTMX).content.decode()
    assert f'name="mieter" value="{gesucht.pk}"' in treffer and "Erika Suchmann" in treffer
    assert "Andere" not in treffer
    leer = angemeldet.get(url, {"partei_suche": "gibtsnicht"}, **HTMX).content.decode()
    assert "Keine Partei zu „gibtsnicht“" in leer


def test_neue_partei_im_dialog_und_fehler_am_feld(objekt: Objekt, angemeldet: Client) -> None:
    """Review 2, B6: „Neue Partei“ legt man direkt im Dialog an. Fehlt der Name, steht der
    Fehler als Satz am Feld und der Dialog bleibt offen."""
    einheit = garage_anlegen(objekt)
    url = reverse("stammdaten:vertrag_neu")
    daten = {
        "einheit": einheit.pk,
        "mieter": "neu",
        "beginn": "2026-12-01",
        "miete": "80,00",
        "zahlweise": "monatlich",
    }
    antwort = angemeldet.post(url, daten, **DIALOG)
    assert antwort.status_code == 200
    assert "Bitte den Namen der neuen Partei angeben." in antwort.content.decode()
    assert not Vertrag.objects.exists()

    ohne = angemeldet.post(url, {**daten, "mieter": ""}, **DIALOG).content.decode()
    assert "Bitte eine Partei über die Suche wählen oder „Neue Partei“ anlegen." in ohne

    antwort = angemeldet.post(url, {**daten, "neu_firma": "Testfirma GmbH"}, **DIALOG)
    vertrag = Vertrag.objects.get()
    partei = vertrag.vertragsparteien.get().partei
    assert (partei.art, partei.firma) == (Partei.Art.FIRMA, "Testfirma GmbH")


def test_kaution_mit_option_keine(objekt: Objekt, mieter: Partei, angemeldet: Client) -> None:
    """Review 2, B6: Kaution mit Option „keine“ (Betrag 0); leer bleibt sie offen."""
    url = reverse("stammdaten:vertrag_neu")
    for haken, erwartet in (("on", Decimal("0.00")), ("", None)):
        einheit = garage_anlegen(objekt)
        angemeldet.post(
            url,
            {
                "einheit": einheit.pk,
                "mieter": mieter.pk,
                "beginn": "2026-12-01",
                "miete": "80,00",
                "zahlweise": "monatlich",
                "kaution": "",
                "keine_kaution": haken,
            },
        )
        vertrag = Vertrag.objects.get(vertragseinheiten__einheit=einheit)
        assert vertrag.kaution.betrag == erwartet


def test_bonitaetshinweis_ab_100(objekt: Objekt, angemeldet: Client) -> None:
    """Review 2, B6 (P8): Ab 100 € Monatsmiete erscheint im Formular der Bonitätshinweis;
    darunter ist er ausgeblendet (bausteine.js blendet ihn beim Tippen ein)."""
    url = reverse("stammdaten:vertrag_neu")
    teuer = garage_anlegen(objekt, zielmiete="120.00")
    html = angemeldet.get(url, {"einheit": teuer.pk}).content.decode()
    hinweis = re.search(r'<p class="hinweis-band[^>]*data-wenn="miete"[^>]*>', html)
    assert hinweis and "hidden" not in hinweis[0] and 'data-ab="100' in hinweis[0]
    assert "Bonitätsnachweis" in html
    guenstig = garage_anlegen(objekt, zielmiete="50.00")
    html = angemeldet.get(url, {"einheit": guenstig.pk}).content.decode()
    hinweis = re.search(r'<p class="hinweis-band[^>]*data-wenn="miete"[^>]*>', html)
    assert hinweis and "hidden" in hinweis[0]


# --- B7: Vertragsseite (Feinschliff) und Einheitenseite -------------------------------------


def test_schreiben_erstellen_zum_vertrag(
    aktiver_vertrag: Vertrag, vorlagen: dict[str, Vorlage], angemeldet: Client
) -> None:
    """Review 2, B7: „Schreiben erstellen“ öffnet einen Dialog mit der Auswahl der Vorlage
    und erzeugt ein Einzelschreiben zum Vertrag (Status „bereit“, nichts versendet). Die
    Meldung verlinkt auf das Schreiben."""
    url = reverse("stammdaten:vertrag", args=[aktiver_vertrag.pk])
    kopf = angemeldet.get(url).content.decode()
    assert f'hx-get="{url}?dialog=schreiben_erstellen"' in kopf
    dialog = angemeldet.get(f"{url}?dialog=schreiben_erstellen", **HTMX).content.decode()
    mitteilung = vorlagen["mitteilung"]
    assert f'value="{mitteilung.pk}"' in dialog and "Mitteilung an Nutzer" in dialog
    assert f'value="{vorlagen["zahlungserinnerung"].pk}"' not in dialog

    antwort = angemeldet.post(
        url,
        {
            "aktion": "schreiben_erstellen",
            "schreiben_erstellen-vorlage": mitteilung.pk,
            "schreiben_erstellen-versandweg": "",
        },
        **HTMX,
    )
    brief = Schreiben.objects.get(vorlage=mitteilung)
    assert (brief.bezug, brief.status) == (aktiver_vertrag, Schreiben.Status.BEREIT)
    html = antwort.content.decode()
    assert reverse("dokumente:schreiben", args=[brief.pk]) in html
    assert f"Schreiben #{brief.pk}" in html and 'id="vertrag-reiter"' in html


def test_verlauf_fuehrt_zum_protokoll(aktiver_vertrag: Vertrag, angemeldet: Client) -> None:
    """Review 2, B7: „Verlauf“ führt zum Protokoll mit ?bezug=stammdaten.vertrag:ID."""
    url = protokoll_url(aktiver_vertrag)
    assert url.endswith(f"?bezug=stammdaten.vertrag%3A{aktiver_vertrag.pk}")
    seite = angemeldet.get(reverse("stammdaten:vertrag", args=[aktiver_vertrag.pk]))
    assert url in seite.content.decode()
    protokoll = angemeldet.get(url)
    assert protokoll.status_code == 200
    assert protokoll.context["bezug"] == aktiver_vertrag


def test_einheitenseite_kopf_und_reiter(aktiver_vertrag: Vertrag, angemeldet: Client) -> None:
    """Review 2, B7: Einheitenseite mit Kopf (Typ, Fläche, Status, aktueller Vertrag und
    Mieter, Zielmiete, Zähler) und den Reitern Übersicht · Verträge · Zähler · Bilder und
    Katalog · Verlauf; Formulare als Dialoge."""
    einheit = aktiver_vertrag.einheiten.get()
    zaehler_services.zaehler_anlegen("Z-77", "strom", einheit=einheit)
    url = reverse("stammdaten:einheit", args=[einheit.pk])
    seite = angemeldet.get(url).content.decode()
    kopf = seite[seite.index('id="einheit-kopf"') : seite.index('id="einheit-reiter"')]
    for text in (
        einheit.nw_id,
        "Garage",
        "18,00 m²",
        "vermietet",
        aktiver_vertrag.nummer,
        "Erika Beispiel",
        "Ziel 90,00\xa0€",
        "Strom Z-77",
    ):
        assert text in kopf, text
    for titel in ("Übersicht", "Verträge", "Zähler", "Bilder und Katalog", "Verlauf"):
        assert f">{titel}</a>" in seite
    assert 'name="zielmiete-betrag"' not in seite  # kein dauerhaft offenes Formular
    for reiter in ("vertraege", "zaehler", "bilder", "verlauf"):
        teil = angemeldet.get(url, {"reiter": reiter}, HTTP_HX_TARGET="einheit-reiter", **HTMX)
        assert teil.status_code == 200 and "<html" not in teil.content.decode()
    assert aktiver_vertrag.nummer in angemeldet.get(url, {"reiter": "vertraege"}).content.decode()
    assert "Z-77" in angemeldet.get(url, {"reiter": "zaehler"}).content.decode()


def test_einheit_zielmiete_im_dialog(garage: Einheit, angemeldet: Client) -> None:
    """Review 2, B7: Zielmiete ändern als Dialog, vorausgefüllt; Speichern ersetzt Kopf und
    Reiter und meldet mit Link, was passiert ist."""
    url = reverse("stammdaten:einheit", args=[garage.pk])
    dialog = angemeldet.get(f"{url}?dialog=zielmiete", **HTMX)
    assert dialog.context["form"].initial["betrag"] == Decimal("90.00")
    antwort = angemeldet.post(
        url,
        {
            "aktion": "zielmiete",
            "zielmiete-betrag": "95.00",
            "zielmiete-gueltig_ab": "2027-01-01",
            "zielmiete-begruendung": "",
        },
        **HTMX,
    )
    html = antwort.content.decode()
    assert 'id="einheit-kopf" hx-swap-oob="true"' in html
    assert "Zielmiete 95,00\xa0€ ab 01.01.2027" in html
    assert json.loads(antwort["HX-Trigger"]) == {"dialogSchliessen": True}
    assert garage.zielmiete_am(D(2027, 1, 1)).betrag == Decimal("95.00")  # type: ignore[union-attr]

    fehler = angemeldet.post(
        url, {"aktion": "zielmiete", "zielmiete-betrag": "", "zielmiete-gueltig_ab": ""}, **HTMX
    )
    assert fehler.status_code == 200 and 'class="feld-fehler"' in fehler.content.decode()


def test_wohnraum_ohne_vertrag_und_zielmiete(objekt: Objekt, angemeldet: Client) -> None:
    """Review 2, B7: Wohnraum zeigt nur Übersicht und Verlauf, ohne Vertrag, Status und
    Zielmiete (Verwaltio)."""
    wohnung = services.einheit_anlegen(objekt, Einheit.Typ.WOHNUNG)
    seite = angemeldet.get(reverse("stammdaten:einheit", args=[wohnung.pk])).content.decode()
    assert "geführt in Verwaltio" in seite and "Zielmiete ändern" not in seite
    assert "?reiter=zaehler" not in seite and "?reiter=verlauf" in seite


# --- B8: Übergabe am Handy ------------------------------------------------------------------


def test_uebergabe_fortschritt_kamera_und_zusammenfassung(
    entwurf: Vertrag, garage: Einheit, angemeldet: Client
) -> None:
    """Review 2, B8: Fortschrittsleiste, Kameraknopf je Schritt (Zähler, Fotos, Unterschrift),
    Kautionsschritt mit „keine“, am Ende die Zusammenfassung mit allem Fehlenden und je
    einem Link dorthin; zurück zum Vertrag aus dem Assistenten."""
    zaehler = zaehler_services.zaehler_anlegen("Z-5", "strom", einheit=garage)
    uebergabe = services.uebergabe_beginnen(entwurf, Uebergabe.Art.UEBERGABE, D(2026, 12, 1))

    def seite(n: int) -> str:
        return angemeldet.get(
            reverse("stammdaten:uebergabe", args=[uebergabe.pk, n])
        ).content.decode()

    erste = seite(1)
    assert 'class="fortschritt"' in erste and 'aria-current="step"' in erste
    assert reverse("stammdaten:vertrag", args=[entwurf.pk]) in erste
    kamera = 'accept="image/*" capture="environment"'
    assert f'name="foto_{zaehler.pk}" {kamera}' in seite(2)
    assert f'name="fotos" {kamera} multiple' in seite(3)
    assert f'name="unterschrift" {kamera}' in seite(6)
    kaution = seite(5)
    assert 'name="keine"' in kaution and 'data-wenn="keine" data-gleich="aus"' in kaution

    abschluss = seite(7)
    assert "Was noch fehlt" in abschluss
    assert abschluss.count("fortschritt-luecke") >= 3  # Zähler, Fotos, Unterschrift …
    nummern = {name: n for n, (name, _) in enumerate(views.schritte_fuer(uebergabe), start=1)}
    for text, schritt in services.uebergabe_fehlt(uebergabe):
        assert text in abschluss
        ziel = reverse("stammdaten:uebergabe", args=[uebergabe.pk, nummern[schritt]])
        assert f'<a href="{ziel}">nachholen</a>' in abschluss


# --- B11: Listen ---------------------------------------------------------------------------


def test_einheitenliste_suche_filter_spalten(
    objekt: Objekt, mieter: Partei, angemeldet: Client
) -> None:
    """Review 2, B11: Einheiten mit Suche, Filter nach Typ, Objekt und Status (vermietet,
    frei, gekündigt) und den Spalten Mieter, Miete, offen (aus soll.offen) und frei ab."""
    vermietet = garage_anlegen(objekt)
    vertrag_anlegen(vermietet, mieter, status="aktiv", beginn=D(2026, 2, 1))
    frei = garage_anlegen(objekt, zielmiete="44.00")
    stellplatz = services.einheit_anlegen(objekt, Einheit.Typ.STELLPLATZ)
    url = reverse("stammdaten:einheiten")

    alle = angemeldet.get(url).content.decode()
    zeile = re.search(rf'<tr id="einheit-{vermietet.pk}">.*?</tr>', alle, re.S)
    assert zeile and "Erika Beispiel" in zeile[0] and "85,00\xa0€" in zeile[0]
    assert "vermietet" in zeile[0]
    zeile = re.search(rf'<tr id="einheit-{frei.pk}">.*?</tr>', alle, re.S)
    assert zeile and "Ziel 44,00\xa0€" in zeile[0] and "seit 01.01.2026" in zeile[0]

    def ids(**filter_: str) -> set[int]:
        html = angemeldet.get(url, filter_).content.decode()
        return {int(i) for i in re.findall(r'<tr id="einheit-(\d+)">', html)}

    assert ids(status="frei") == {frei.pk, stellplatz.pk}
    assert ids(status="vermietet") == {vermietet.pk}
    assert ids(status="gekuendigt") == set()
    assert ids(typ="stellplatz") == {stellplatz.pk}
    assert ids(objekt=objekt.kuerzel, suche="beispiel") == {vermietet.pk}
    assert ids(objekt="GIBTSNICHT") == set()


def test_saldo_der_liste_ist_soll_offen(testhof: object) -> None:
    """Review 2, B11 und Review 1, Punkt 1: Die Spalte „offen“ der Einheitenliste ist
    dieselbe Zahl wie ``soll.offen`` (Rückstand bis heute) für den Vertrag."""
    from apps.soll import services as soll

    heute = zeit.heute()
    lagen = [lage for lage in einheitenliste.lagen(heute) if lage.vertrag is not None]
    assert any(lage.offen > 0 for lage in lagen)
    for lage in lagen:
        assert lage.vertrag is not None
        assert lage.offen == soll.offen(lage.vertrag, stichtag=heute).summe_offen


def test_parteienliste_rolle_und_vertraege(
    aktiver_vertrag: Vertrag, objekt: Objekt, angemeldet: Client
) -> None:
    """Review 2, B11: Parteien mit Suche und den Spalten Rolle und Verträge."""
    services.vertragspartei_hinzufuegen(aktiver_vertrag, mieter_anlegen("Zahlfrau"), "zahler")
    mieter_anlegen("Ohnevertrag")
    url = reverse("stammdaten:parteien")
    html = angemeldet.get(url).content.decode()
    assert "<th>Rolle</th>" in html and "<th>Verträge</th>" in html
    zeile = re.search(r"Erika Zahlfrau</a>.*?</tr>", html, re.S)
    assert zeile and "Zahler" in zeile[0] and aktiver_vertrag.nummer in zeile[0]
    assert "(1 laufend)" in zeile[0]
    gesucht = angemeldet.get(url, {"suche": "ohnever"}).content.decode()
    assert "Erika Ohnevertrag" in gesucht and "Zahlfrau" not in gesucht
    assert "ohne Vertrag" in gesucht


def test_vertragsliste_im_neuen_stil(aktiver_vertrag: Vertrag, angemeldet: Client) -> None:
    """Review 2, B11: Vertragsliste im neuen Stil: Status als Reiter, Suche, Spalten Miete
    und offen, Status als Badge."""
    url = reverse("stammdaten:vertraege")
    html = angemeldet.get(url).content.decode()
    assert 'class="reiter"' in html and 'class="filterleiste"' in html
    assert "85,00\xa0€" in html and 'class="badge badge-ok">aktiv' in html
    assert aktiver_vertrag.nummer in angemeldet.get(url, {"suche": "beispiel"}).content.decode()
    assert (
        aktiver_vertrag.nummer not in angemeldet.get(url, {"suche": "gibtsnicht"}).content.decode()
    )
