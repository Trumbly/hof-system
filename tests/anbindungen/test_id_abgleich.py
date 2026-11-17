"""ID-Abgleich (H1.6): NW-IDs von Website und Verwaltio gegen das Hof-System."""

from __future__ import annotations

import datetime
import io
import json
import urllib.error
from typing import Any

import pytest
from django.core.management import call_command
from django.test import Client, override_settings
from django.urls import reverse

from apps.anbindungen import services, website
from apps.anbindungen.models import Abweichung
from apps.anbindungen.verwaltio import FakeVerwaltio, VerwaltioApiClient
from apps.anbindungen.website import (
    FakeWebsite,
    WebsiteEinheit,
    WebsiteFehler,
    WebsiteHttpClient,
    WebsiteNichtEingerichtet,
)
from apps.kern.models import Arbeitsposten, Lauf, Objekt
from apps.stammdaten import services as stammdaten
from apps.stammdaten.models import Einheit
from tests.ablaeufe.hilfen import posten
from tests.anbindungen.conftest import vermarktete_einheit

D = datetime.date
#: Die echten Clients (Pfade aus den Klassen, damit sie Umbenennungen folgen).
ECHT_VERWALTIO = f"{VerwaltioApiClient.__module__}.{VerwaltioApiClient.__name__}"
ECHT_WEBSITE = f"{WebsiteHttpClient.__module__}.{WebsiteHttpClient.__name__}"
TAG = D(2026, 11, 17)


@pytest.fixture
def hof(objekt: Objekt) -> dict[str, Einheit]:
    """Zwei vermarktete Einheiten, eine nicht vermarktete, eine aufgelöste, zwei Wohnungen."""
    garage = vermarktete_einheit(objekt)
    stammdaten.einheit_aendern(garage, website_slug="garage-hofseite")
    return {
        "garage": garage,
        "stellplatz": vermarktete_einheit(objekt, Einheit.Typ.STELLPLATZ),
        "lager": vermarktete_einheit(objekt, Einheit.Typ.LAGER, vermarktung="keine"),
        "alt": stammdaten.einheit_aufloesen(
            vermarktete_einheit(objekt, Einheit.Typ.HALLE), D(2026, 1, 31)
        ),
        "w1": stammdaten.einheit_anlegen(objekt, Einheit.Typ.WOHNUNG),
        "w2": stammdaten.einheit_anlegen(objekt, Einheit.Typ.WOHNUNG),
    }


def _befunde(abweichungen: list[Abweichung]) -> set[tuple[str, str, str, str]]:
    return {(a.quelle, a.art, a.nw_id, a.gemeldet) for a in abweichungen}


def test_jede_fehlende_oder_abweichende_id_steht_in_der_arbeitsliste(
    hof: dict[str, Einheit], angemeldet: Client
) -> None:
    """Abnahme Schritt 10: Jede Einheit-ID, die in Website oder Verwaltio fehlt oder
    abweicht, steht in der Arbeitsliste."""
    g, s, lager, alt = (hof[k].nw_id for k in ("garage", "stellplatz", "lager", "alt"))
    w1, w2 = hof["w1"].nw_id, hof["w2"].nw_id
    website_fake = FakeWebsite(
        einheiten=[
            WebsiteEinheit(g.lower(), "garage-alt"),  # Schreibweise und Slug weichen ab
            WebsiteEinheit(lager),  # nicht vermarktet
            WebsiteEinheit(alt),  # aufgelöst
            WebsiteEinheit("NW-G-0999"),  # gibt es nicht
            # Stellplatz fehlt
        ]
    )
    verwaltio_fake = FakeVerwaltio(ids=[w1, g, "W-17"])  # w2 fehlt, Garage dort falsch

    ergebnis = services.id_abgleich(website=website_fake, verwaltio=verwaltio_fake, datum=TAG)

    assert ergebnis.fehler == {}
    assert _befunde(ergebnis.quellen["website"].neu) == {
        ("website", "schreibweise", g, g.lower()),
        ("website", "slug", g, "garage-alt"),
        ("website", "nicht_erwartet", lager, lager),
        ("website", "nicht_erwartet", alt, alt),
        ("website", "unbekannt", "NW-G-0999", "NW-G-0999"),
        ("website", "fehlt", s, ""),
    }
    assert _befunde(ergebnis.quellen["verwaltio"].neu) == {
        ("verwaltio", "nicht_erwartet", g, g),
        ("verwaltio", "unbekannt", "W-17", "W-17"),
        ("verwaltio", "fehlt", w2, ""),
    }
    # Jede Abweichung hat genau einen offenen Arbeitsposten mit der ID im Hinweis.
    for abweichung in Abweichung.objects.all():
        (eintrag,) = posten(services.ART_ID_ABWEICHUNG, abweichung)
        assert eintrag.status == "offen"
        assert abweichung.nw_id in eintrag.hinweis
    assert Arbeitsposten.objects.filter(art=services.ART_ID_ABWEICHUNG).count() == 9

    liste = angemeldet.get(reverse("kern:arbeitslisten")).content.decode()
    assert "ID-Abweichung" in liste
    for nw_id in (g, s, lager, alt, "NW-G-0999", w2, "W-17"):
        assert nw_id in liste
    seite = angemeldet.get(reverse("anbindungen:abweichungen")).content.decode()
    assert "Offen (9)" in seite


def test_behobene_abweichung_schliesst_sich_beim_naechsten_lauf(hof: dict[str, Einheit]) -> None:
    s = hof["stellplatz"].nw_id
    ohne_stellplatz = FakeWebsite(
        einheiten=[WebsiteEinheit(hof["garage"].nw_id, "garage-hofseite")]
    )
    erster = services.id_abgleich(website=ohne_stellplatz, verwaltio=FakeVerwaltio(), datum=TAG)
    (fehlt,) = erster.quellen["website"].neu
    assert (fehlt.art, fehlt.nw_id) == ("fehlt", s)

    # Noch nicht behoben: bleibt offen, kein zweiter Posten.
    zweiter = services.id_abgleich(
        website=ohne_stellplatz, verwaltio=FakeVerwaltio(), datum=TAG + datetime.timedelta(1)
    )
    assert (zweiter.neu, zweiter.weiter_offen, zweiter.behoben) == (0, 1, 0)
    fehlt.refresh_from_db()
    assert fehlt.zuletzt_gesehen_am == TAG + datetime.timedelta(1)
    assert len(posten(services.ART_ID_ABWEICHUNG, fehlt)) == 1

    # Behoben (die Website spiegelt jetzt das Hof-System): Abweichung und Posten schließen.
    dritter = services.id_abgleich(
        website=FakeWebsite(), verwaltio=FakeVerwaltio(), datum=TAG + datetime.timedelta(2)
    )
    assert (dritter.neu, dritter.weiter_offen, dritter.behoben) == (0, 0, 1)
    fehlt.refresh_from_db()
    assert fehlt.behoben_am == TAG + datetime.timedelta(2)
    (eintrag,) = posten(services.ART_ID_ABWEICHUNG, fehlt)
    assert eintrag.status == "erledigt"

    # Tritt sie wieder auf, ist es eine neue Abweichung.
    vierter = services.id_abgleich(
        website=ohne_stellplatz, verwaltio=FakeVerwaltio(), datum=TAG + datetime.timedelta(3)
    )
    assert vierter.neu == 1
    assert Abweichung.objects.filter(nw_id=s).count() == 2


def test_zurueckgestellter_posten_schliesst_sich_auch(hof: dict[str, Einheit]) -> None:
    services.id_abgleich(website=FakeWebsite(einheiten=[]), verwaltio=None, datum=TAG)
    for eintrag in Arbeitsposten.objects.filter(art=services.ART_ID_ABWEICHUNG):
        from apps.kern import services as kern

        kern.arbeitsposten_zurueckstellen(eintrag)
    services.id_abgleich(website=FakeWebsite(), verwaltio=None, datum=TAG)
    assert not Arbeitsposten.objects.filter(art=services.ART_ID_ABWEICHUNG).exclude(
        status="erledigt"
    )


def test_schweigende_quelle_schliesst_nichts(hof: dict[str, Einheit]) -> None:
    """Antwortet eine Quelle nicht, bleiben ihre Abweichungen offen."""
    services.id_abgleich(
        website=FakeWebsite(einheiten=[]), verwaltio=FakeVerwaltio(ids=[]), datum=TAG
    )
    offen = Abweichung.objects.filter(behoben_am__isnull=True).count()
    assert offen == 4  # 2 auf der Website, 2 in Verwaltio

    ergebnis = services.id_abgleich(website=FakeWebsite(fehler=True), verwaltio=None, datum=TAG)
    assert ergebnis.fehler == {"website": "WebsiteFehler", "verwaltio": "nicht eingerichtet"}
    assert Abweichung.objects.filter(behoben_am__isnull=True).count() == offen


def test_doppelte_id_zaehlt_einmal(hof: dict[str, Einheit]) -> None:
    befunde = services.befunde_website(
        [WebsiteEinheit("NW-X-1"), WebsiteEinheit("NW-X-1"), *FakeWebsite().einheiten()], TAG
    )
    assert [(b.art, b.nw_id) for b in befunde] == [("unbekannt", "NW-X-1")]


def test_lauf_id_abgleich(hof: dict[str, Einheit]) -> None:
    """Der Lauf nutzt die Clients aus den Einstellungen (in Tests die Fakes, die das
    Hof-System spiegeln): ohne Abweichung nichts zu tun."""
    call_command("id_abgleich", "--stichtag", "2026-11-17", stdout=io.StringIO())
    lauf = Lauf.objects.get(name="id_abgleich")
    assert (lauf.status, lauf.arbeitsposten, lauf.fehler) == ("erfolgreich", 0, 0)
    assert "website: 0 neu" in lauf.meldung and "verwaltio: 0 neu" in lauf.meldung


@override_settings(
    HOF_WEBSITE_CLIENT=ECHT_WEBSITE,
    HOF_WEBSITE_URL="",
    HOF_VERWALTIO_CLIENT=ECHT_VERWALTIO,
    VERWALTIO_URL="",
)
def test_lauf_ohne_einrichtung(hof: dict[str, Einheit]) -> None:
    call_command("id_abgleich", "--stichtag", "2026-11-17", stdout=io.StringIO())
    lauf = Lauf.objects.get(name="id_abgleich")
    assert (lauf.status, lauf.fehler) == ("erfolgreich", 0)
    assert "website: nicht eingerichtet" in lauf.meldung
    assert not Abweichung.objects.exists()


@override_settings(
    HOF_VERWALTIO_CLIENT=ECHT_VERWALTIO,
    VERWALTIO_URL="https://verwaltio.invalid",
    VERWALTIO_TOKEN="nur-test",
)
def test_lauf_mit_offenem_verwaltio_client_zaehlt_fehler(hof: dict[str, Einheit]) -> None:
    call_command("id_abgleich", "--stichtag", "2026-11-17", stdout=io.StringIO())
    lauf = Lauf.objects.get(name="id_abgleich")
    assert (lauf.status, lauf.fehler) == ("mit_fehlern", 1)
    assert "verwaltio: NotImplementedError" in lauf.meldung


# --- Client der Website --------------------------------------------------------------


class _Antwort(io.BytesIO):
    def __enter__(self) -> _Antwort:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()


@override_settings(HOF_WEBSITE_URL="https://website.invalid/", HOF_WEBSITE_TOKEN="hofx_test")
def test_website_client_liest_die_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    gefragt: list[Any] = []

    def urlopen(anfrage: Any, timeout: int) -> _Antwort:
        gefragt.append(anfrage)
        daten = {"einheiten": [{"nw_id": "NW-G-0001", "slug": "garage-1"}, {"nw_id": "NW-S-1"}]}
        return _Antwort(json.dumps(daten).encode())

    monkeypatch.setattr(website.urllib.request, "urlopen", urlopen)
    assert WebsiteHttpClient().einheiten() == [
        WebsiteEinheit("NW-G-0001", "garage-1"),
        WebsiteEinheit("NW-S-1", ""),
    ]
    (anfrage,) = gefragt
    assert anfrage.full_url == "https://website.invalid/api/hof/einheiten"
    assert anfrage.headers["Authorization"] == "Bearer hofx_test"


@override_settings(HOF_WEBSITE_URL="https://website.invalid", HOF_WEBSITE_TOKEN="hofx_test")
@pytest.mark.parametrize(
    "antwort",
    [
        b"kein json",
        json.dumps({"liste": []}).encode(),
        json.dumps({"einheiten": [{"slug": "x"}]}).encode(),
        json.dumps({"einheiten": [{"nw_id": "NW-1", "slug": 3}]}).encode(),
    ],
)
def test_website_client_bei_falscher_antwort(
    monkeypatch: pytest.MonkeyPatch, antwort: bytes
) -> None:
    monkeypatch.setattr(website.urllib.request, "urlopen", lambda a, timeout: _Antwort(antwort))
    with pytest.raises(WebsiteFehler):
        WebsiteHttpClient().einheiten()


@override_settings(HOF_WEBSITE_URL="https://website.invalid", HOF_WEBSITE_TOKEN="hofx_test")
def test_website_client_nicht_erreichbar(monkeypatch: pytest.MonkeyPatch) -> None:
    def urlopen(anfrage: Any, timeout: int) -> Any:
        raise urllib.error.URLError("weg")

    monkeypatch.setattr(website.urllib.request, "urlopen", urlopen)
    with pytest.raises(WebsiteFehler, match="nicht erreichbar"):
        WebsiteHttpClient().einheiten()


@override_settings(HOF_WEBSITE_URL="", HOF_WEBSITE_TOKEN="")
def test_website_client_ohne_einrichtung() -> None:
    with pytest.raises(WebsiteNichtEingerichtet):
        WebsiteHttpClient()


def test_website_fake_in_produktion_gesperrt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DJANGO_ENV", "prod")
    with pytest.raises(WebsiteNichtEingerichtet, match="Produktion"):
        FakeWebsite()
