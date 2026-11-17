"""Katalog für die Website (W1): Belegung und Preise aus dem Hof-System, Zugang per Token."""

from __future__ import annotations

import datetime
import json
from decimal import Decimal

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.anbindungen import services
from apps.anbindungen.models import Buchungsanfrage, Websitezugang
from apps.kern.models import Objekt
from apps.stammdaten import services as stammdaten
from apps.stammdaten.models import Einheit, EinheitStatus, Vertrag
from tests.anbindungen.conftest import aktiver_vertrag, rueckgabe, vermarktete_einheit
from tests.stammdaten.conftest import PNG, mieter_anlegen

D = datetime.date
TAG = datetime.timedelta(days=1)
URL = "/api/website/katalog"


def _heute() -> datetime.date:
    return timezone.localdate()


def _nw_ids(website: Client) -> list[str]:
    antwort = website.get(URL)
    assert antwort.status_code == 200, antwort.content
    return [e["nw_id"] for e in antwort.json()["einheiten"]]


def test_katalog_zeigt_belegung_und_preise(objekt: Objekt, website: Client) -> None:
    """Abnahme Schritt 10: Die Website zeigt Belegung und Preise aus dem Hof-System. Im
    Katalog stehen nur vermarktbare, buchbare Einheiten mit NW-ID, Typ, Fläche, Preis
    (Zielmiete), frei ab und Bildern, ohne Parteien."""
    frei = vermarktete_einheit(objekt)
    vermietet = vermarktete_einheit(objekt, Einheit.Typ.STELLPLATZ)
    vertrag = aktiver_vertrag(vermietet)
    vermarktete_einheit(objekt, Einheit.Typ.LAGER, vermarktung=Einheit.Vermarktung.DIREKT)
    stammdaten.einheit_anlegen(objekt, Einheit.Typ.WOHNUNG)  # Wohnraum: Verwaltio
    gesperrt = vermarktete_einheit(objekt, Einheit.Typ.HALLE, status="gesperrt")
    aufgeloest = vermarktete_einheit(objekt, Einheit.Typ.GARTEN)
    stammdaten.einheit_aufloesen(aufgeloest, D(2026, 6, 1))

    antwort = website.get(URL)

    assert antwort.status_code == 200
    assert antwort["Content-Type"].startswith("application/json")
    daten = antwort.json()
    assert daten["stand"] == _heute().isoformat()
    assert daten["anzahl"] == 1
    (eintrag,) = daten["einheiten"]
    assert eintrag == {
        "nw_id": frei.nw_id,
        "typ": "garage",
        "typ_titel": "Garage",
        "objekt": "HOF",
        "bezeichnung": "garage Hofseite",
        "flaeche_qm": "18.50",
        "preis_monat": "89.00",
        "waehrung": "EUR",
        "frei_ab": _heute().isoformat(),
        "slug": "",
        "bilder": [],
    }
    # Keine Parteien, keine Verträge, keine Personendaten.
    text = antwort.content.decode()
    mieter = vertrag.parteien_mit_rolle("mieter")[0]
    for verboten in (mieter.nachname, mieter.vorname, vertrag.nummer, vermietet.nw_id):
        assert verboten not in text
    assert gesperrt.nw_id not in text


def test_preis_ist_die_zielmiete_am_tag_frei_ab(objekt: Objekt) -> None:
    einheit = vermarktete_einheit(objekt, zielmiete="80.00", ab=D(2025, 1, 1))
    stammdaten.zielmiete_setzen(einheit, Decimal("95.00"), D(2027, 1, 1), "Anpassung")
    (heute,) = services.katalog(D(2026, 11, 17))
    assert heute.preis_monat == Decimal("80.00")
    (spaeter,) = services.katalog(D(2027, 2, 1))
    assert spaeter.preis_monat == Decimal("95.00")

    ohne = vermarktete_einheit(objekt, Einheit.Typ.STELLPLATZ, zielmiete=None)
    eintraege = {e.nw_id: e for e in services.katalog(D(2026, 11, 17))}
    assert eintraege[ohne.nw_id].preis_monat is None


def test_rueckgabe_macht_die_einheit_wieder_buchbar(objekt: Objekt, website: Client) -> None:
    """Abnahme Schritt 10: Eine Rückgabe aus Schritt 4 macht die Einheit ohne Handgriff
    wieder buchbar (Test über den Katalog)."""
    einheit = vermarktete_einheit(objekt)
    vertrag = aktiver_vertrag(einheit)
    assert einheit.nw_id not in _nw_ids(website)

    # Kündigung allein reicht nicht: Die Einheit ist bis zur Rückgabe vermietet.
    stammdaten.kuendigung_erfassen(vertrag, D(2026, 9, 1))
    assert einheit.nw_id not in _nw_ids(website)

    # Rückgabe in der Zukunft: Die Einheit steht schon im Katalog, frei ab dem Rückgabetag.
    tag = _heute() + 10 * TAG
    rueckgabe(vertrag, tag)
    eintrag = next(e for e in website.get(URL).json()["einheiten"] if e["nw_id"] == einheit.nw_id)
    assert eintrag["frei_ab"] == tag.isoformat()
    assert eintrag["preis_monat"] == "89.00"

    # Ab dem Rückgabetag: frei ab heute.
    (am_tag,) = services.katalog(tag + 5 * TAG)
    assert am_tag.frei_ab == tag + 5 * TAG


def test_entwurf_und_angenommene_anfrage_nehmen_die_einheit_aus_dem_katalog(
    objekt: Objekt,
) -> None:
    heute = D(2026, 11, 17)
    garage = vermarktete_einheit(objekt)
    lager = vermarktete_einheit(objekt, Einheit.Typ.LAGER)
    assert {e.nw_id for e in services.katalog(heute)} == {garage.nw_id, lager.nw_id}

    stammdaten.vertrag_anlegen(
        einheiten=[garage], mieter=[mieter_anlegen()], beginn=D(2026, 12, 1), miete=None
    )
    anfrage, _ = services.ereignis_annehmen(
        services.WebsiteEreignis(
            ereignis_id="w-1",
            art="buchung",
            nw_id=lager.nw_id,
            gemeldet_am=timezone.now(),
        )
    )
    assert lager.nw_id in {e.nw_id for e in services.katalog(heute)}  # offen: noch buchbar
    services.buchungsanfrage_entscheiden(anfrage, Buchungsanfrage.Status.ANGENOMMEN)
    assert services.katalog(heute) == []

    services.buchungsanfrage_entscheiden(anfrage, Buchungsanfrage.Status.ABGELEHNT)
    assert [e.nw_id for e in services.katalog(heute)] == [lager.nw_id]


def test_befristeter_vertrag_mit_folgendem_frei(objekt: Objekt) -> None:
    """Endet ein Vertrag vor dem Tag „frei ab“, bindet er die Einheit nicht mehr."""
    einheit = vermarktete_einheit(objekt, status=None)
    stammdaten.vertrag_anlegen(
        einheiten=[einheit],
        mieter=[mieter_anlegen()],
        beginn=D(2026, 1, 1),
        ende=D(2026, 12, 31),
        miete=Decimal("70.00"),
        status=Vertrag.Status.AKTIV,
    )
    assert services.katalog(D(2026, 11, 17)) == []
    stammdaten.einheit_status_setzen(einheit, EinheitStatus.Status.FREI, D(2027, 1, 1))
    (eintrag,) = services.katalog(D(2026, 11, 17))
    assert eintrag.frei_ab == D(2027, 1, 1)


def test_katalog_mit_bildern(objekt: Objekt, website: Client) -> None:
    einheit = vermarktete_einheit(objekt)
    erstes = services.einheitbild_hinzufuegen(
        einheit, SimpleUploadedFile("tor.png", PNG, content_type="image/png"), "Tor von außen"
    )
    zweites = services.einheitbild_hinzufuegen(
        einheit, SimpleUploadedFile("innen.png", PNG, content_type="image/png")
    )
    weg = services.einheitbild_hinzufuegen(einheit, SimpleUploadedFile("alt.png", PNG))
    services.einheitbild_entfernen(weg)

    (eintrag,) = website.get(URL).json()["einheiten"]
    basis = "https://hof-system.testhof.invalid"
    assert eintrag["bilder"] == [
        {
            "url": f"{basis}/api/website/bilder/{erstes.pk}",
            "beschreibung": "Tor von außen",
            "reihenfolge": 1,
        },
        {"url": f"{basis}/api/website/bilder/{zweites.pk}", "beschreibung": "", "reihenfolge": 2},
    ]
    # Der Dateiname enthält nichts vom Original.
    assert "tor" not in erstes.bild.name

    antwort = website.get(f"/api/website/bilder/{erstes.pk}")
    assert antwort.status_code == 200
    assert antwort["Content-Type"] == "image/png"
    assert b"".join(antwort.streaming_content) == PNG  # type: ignore[attr-defined]
    assert "immutable" in antwort["Cache-Control"]
    assert website.get(f"/api/website/bilder/{weg.pk}").status_code == 404


def test_bild_nur_als_bild_und_nicht_fuer_wohnraum(objekt: Objekt) -> None:
    einheit = vermarktete_einheit(objekt)
    with pytest.raises(services.AnbindungFehler, match="nur"):
        services.einheitbild_hinzufuegen(einheit, SimpleUploadedFile("liste.pdf", b"%PDF"))
    wohnung = stammdaten.einheit_anlegen(objekt, Einheit.Typ.WOHNUNG)
    with pytest.raises(services.AnbindungFehler, match="Wohnraum"):
        services.einheitbild_hinzufuegen(wohnung, SimpleUploadedFile("a.png", PNG))


def test_etag_spart_die_antwort(freie_garage: Einheit, website: Client) -> None:
    """Die Website darf zwischenspeichern: Unverändert kommt 304 ohne Inhalt."""
    erste = website.get(URL)
    etag = erste["ETag"]
    assert etag.startswith('"') and erste["Cache-Control"] == "private, no-cache"

    zweite = website.get(URL, HTTP_IF_NONE_MATCH=etag)
    assert zweite.status_code == 304
    assert zweite.content == b""
    assert zweite["ETag"] == etag

    stammdaten.zielmiete_setzen(freie_garage, Decimal("99.00"), D(2026, 2, 1))
    dritte = website.get(URL, HTTP_IF_NONE_MATCH=etag)
    assert dritte.status_code == 200
    assert dritte["ETag"] != etag
    assert dritte.json()["einheiten"][0]["preis_monat"] == "99.00"


def test_ohne_gueltigen_token_kein_katalog(freie_garage: Einheit, client: Client) -> None:
    antwort = client.get(URL)
    assert antwort.status_code == 401
    assert antwort["WWW-Authenticate"] == "Bearer"
    assert freie_garage.nw_id not in antwort.content.decode()

    assert client.get(URL, HTTP_AUTHORIZATION="Bearer hofw_falsch").status_code == 401
    assert client.get(URL, HTTP_AUTHORIZATION="Bearer hofz_anderer").status_code == 401

    token = services.website_token_erzeugen()
    assert client.get(URL, HTTP_AUTHORIZATION=f"Bearer {token}").status_code == 200
    # Ein neuer Token löst den alten ab.
    neu = services.website_token_erzeugen()
    assert client.get(URL, HTTP_AUTHORIZATION=f"Bearer {token}").status_code == 401
    assert client.get(URL, HTTP_AUTHORIZATION=f"Bearer {neu}").status_code == 200
    # Gespeichert ist nur der Hash.
    assert not Websitezugang.objects.filter(token_hash=neu).exists()
    services.website_token_sperren()
    assert client.get(URL, HTTP_AUTHORIZATION=f"Bearer {neu}").status_code == 401


def test_katalog_ohne_sitzung_und_ohne_zweiten_faktor(
    freie_garage: Einheit, angemeldet: Client, token: str
) -> None:
    """Die Schnittstelle ist wie /gesund und die Bank-Zulieferung von der TOTP-Pflicht
    ausgenommen; eine Sitzung ersetzt den Token nicht."""
    assert angemeldet.get(URL).status_code == 401
    antwort = angemeldet.get(URL, HTTP_AUTHORIZATION=f"Bearer {token}")
    assert antwort.status_code == 200
    assert "sessionid" not in antwort.cookies


@override_settings(HOF_WEBSITE_IP_FREIGABE=["100.64.0.7", "2001:db8::/64"])
def test_ip_freigabe(freie_garage: Einheit, website: Client) -> None:
    assert website.get(URL, REMOTE_ADDR="100.64.0.7").status_code == 200
    assert website.get(URL, REMOTE_ADDR="2001:db8::1").status_code == 200
    assert website.get(URL, REMOTE_ADDR="100.64.0.8").status_code == 403
    # Hinter Caddy zählt die letzte Adresse, die Caddy angehängt hat.
    hinter = website.get(URL, REMOTE_ADDR="172.18.0.2", HTTP_X_FORWARDED_FOR="1.2.3.4, 100.64.0.7")
    assert hinter.status_code == 200
    gefaelscht = website.get(
        URL, REMOTE_ADDR="172.18.0.2", HTTP_X_FORWARDED_FOR="100.64.0.7, 9.9.9.9"
    )
    assert gefaelscht.status_code == 403
    assert "fehler" in json.loads(gefaelscht.content)


def test_katalog_nur_lesend(freie_garage: Einheit, website: Client) -> None:
    assert website.post(URL).status_code == 405


def test_bild_auf_der_seite_nur_mit_anmeldung(objekt: Objekt, client: Client) -> None:
    einheit = vermarktete_einheit(objekt)
    bild = services.einheitbild_hinzufuegen(einheit, SimpleUploadedFile("a.png", PNG))
    antwort = client.get(reverse("anbindungen:bild", args=[bild.pk]))
    assert antwort.status_code == 302
