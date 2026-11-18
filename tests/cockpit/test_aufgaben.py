"""Aufgaben im Hof-Backlog (Schritt 9, Aufgabe 6): melden, abgleichen, ohne Personenfelder."""

from __future__ import annotations

import datetime
import io
import json
import re
from typing import Any

import httpx
import pytest
from django.core.management import call_command

from apps.cockpit import notion, services
from apps.cockpit.models import NotionAufgabe
from apps.cockpit.notion import AufgabenInhalt, FakeNotion, NotionFehler, NotionHttpClient
from apps.kern import aufgaben
from apps.kern import services as kern
from apps.kern.models import Arbeitsposten, Lauf
from tests.factories import MandantFabrik, ObjektFabrik

pytestmark = pytest.mark.django_db

BASIS = "https://hof-system.testhof.invalid"
FRIST = "Frist fällig"
MAHNUNG = "Mahnungen senden"


def _abgleich() -> Lauf:
    call_command("aufgaben_abgleich", stdout=io.StringIO())
    return Lauf.objects.filter(name="aufgaben_abgleich").latest("gestartet_am", "pk")


def _seite(page_id: str) -> dict[str, Any]:
    eigenschaften = FakeNotion.seiten[page_id]
    return {
        "titel": eigenschaften["Name"]["title"][0]["text"]["content"],
        "status": eigenschaften["Status"]["status"]["name"],
        "faellig": (eigenschaften["Fällig"]["date"] or {}).get("start"),
        "link": eigenschaften["Link"]["url"],
        "text": eigenschaften["Details"]["rich_text"][0]["text"]["content"],
    }


def _posten(art: str, bezug: Any = None, faellig: datetime.date | None = None) -> Arbeitsposten:
    return kern.arbeitsposten_anlegen(art, bezug=bezug, faellig_am=faellig, hinweis="geheim")


def test_melden_merkt_nur_vor_und_ruft_notion_nicht() -> None:
    """Aufgabe 6, Entscheidung: ``melden`` (zentral in kern.services) merkt nur vor; Notion
    ruft erst der Lauf. Arten außerhalb von HOF_NOTION_ARTEN bleiben außen vor."""
    _posten("Umsätze einordnen")
    assert not NotionAufgabe.objects.exists()

    _posten(FRIST)
    aufgabe = NotionAufgabe.objects.get()
    assert aufgabe.art == FRIST and aufgabe.vorgemerkt_am is not None and not aufgabe.page_id
    assert FakeNotion.aufrufe == []


def test_aufgaben_entstehen_und_schliessen_sich() -> None:
    """Abnahme Schritt 9: Aufgaben im Hof-Backlog entstehen und schließen sich. Je Art eine
    Aufgabe „<Anzahl> <Art>“ mit NW-IDs bzw. Kürzeln, frühester Fälligkeit und Link; kommen
    Posten hinzu oder gehen welche, wird sie aktualisiert; sind alle erledigt, Erledigt."""
    hof = ObjektFabrik(kuerzel="HOF", mandant=MandantFabrik())
    wald = ObjektFabrik(kuerzel="WALD", mandant=hof.mandant)
    erster = _posten(FRIST, hof, datetime.date(2026, 12, 5))
    _posten(FRIST, wald, datetime.date(2026, 12, 1))
    mahnung = _posten(MAHNUNG, hof, datetime.date(2026, 12, 10))

    lauf = _abgleich()
    assert lauf.status == Lauf.Status.ERFOLGREICH
    assert lauf.gebucht == 2
    fristen = NotionAufgabe.objects.get(art=FRIST)
    seite = _seite(fristen.page_id)
    assert seite["titel"] == "2 Fristen fällig"
    assert seite["status"] == "Offen"
    assert seite["faellig"] == "2026-12-01"
    assert seite["link"] == f"{BASIS}/arbeitslisten/?art=Frist+f%C3%A4llig"
    assert seite["text"].splitlines() == [
        "NW-IDs/Kürzel: HOF, WALD",
        "Fällig: 01.12.2026 (frühester Posten)",
        f"Im Hof-System: {seite['link']}",
    ]
    assert _seite(NotionAufgabe.objects.get(art=MAHNUNG).page_id)["titel"] == "1 Mahnung senden"

    # Ohne Änderung kein Aufruf.
    aufrufe = len(FakeNotion.aufrufe)
    _abgleich()
    assert len(FakeNotion.aufrufe) == aufrufe

    # Ein Posten mehr: dieselbe Aufgabe, neuer Titel.
    _posten(FRIST, hof, datetime.date(2026, 11, 20))
    _abgleich()
    assert NotionAufgabe.objects.filter(art=FRIST).count() == 1
    assert _seite(fristen.page_id)["titel"] == "3 Fristen fällig"
    assert _seite(fristen.page_id)["faellig"] == "2026-11-20"

    # Alle erledigt: Aufgabe auf Erledigt.
    for p in Arbeitsposten.objects.filter(art=FRIST):
        kern.arbeitsposten_erledigen(p)
    kern.arbeitsposten_zurueckstellen(mahnung)
    _abgleich()
    fristen.refresh_from_db()
    assert fristen.status == NotionAufgabe.Status.ERLEDIGT
    assert _seite(fristen.page_id)["status"] == "Erledigt"
    assert _seite(NotionAufgabe.objects.get(art=MAHNUNG).page_id)["status"] == "Erledigt"

    # Neue Posten danach: neue Aufgabe, die alte bleibt erledigt.
    kern.arbeitsposten_wieder_oeffnen(erster)
    _abgleich()
    offen = NotionAufgabe.objects.get(art=FRIST, status="offen")
    assert offen.page_id != fristen.page_id
    assert _seite(offen.page_id)["titel"] == "1 Frist fällig"


def test_notion_faellt_aus_buchung_geht_trotzdem() -> None:
    """Fällt Notion aus, scheitert kein Arbeitsposten; der Lauf steht auf „mit Fehlern“ und
    holt es beim nächsten Mal nach."""
    FakeNotion.scheitern = True
    posten = _posten(FRIST)
    assert posten.pk is not None

    lauf = _abgleich()
    assert lauf.status == Lauf.Status.MIT_FEHLERN
    aufgabe = NotionAufgabe.objects.get(art=FRIST)
    assert aufgabe.fehler == "Notion antwortet 503 service_unavailable"
    assert not aufgabe.page_id

    FakeNotion.scheitern = False
    assert _abgleich().status == Lauf.Status.ERFOLGREICH
    aufgabe.refresh_from_db()
    assert aufgabe.page_id and not aufgabe.fehler


def test_fehler_im_empfaenger_haelt_den_posten_nicht_auf(monkeypatch: pytest.MonkeyPatch) -> None:
    def kaputt(posten: Arbeitsposten) -> None:
        raise RuntimeError("kaputt")

    monkeypatch.setattr(aufgaben, "_EMPFAENGER", [kaputt, *aufgaben._EMPFAENGER])
    posten = _posten(FRIST)
    assert Arbeitsposten.objects.filter(pk=posten.pk).exists()
    assert NotionAufgabe.objects.filter(art=FRIST).exists()


def test_nicht_eingerichtet(settings: Any) -> None:
    settings.HOF_NOTION_CLIENT = "apps.cockpit.notion.NotionHttpClient"
    settings.HOF_NOTION_TOKEN = ""
    _posten(FRIST)
    lauf = _abgleich()
    assert lauf.status == Lauf.Status.ERFOLGREICH
    assert "nicht eingerichtet" in lauf.meldung


def test_kennungen_folgen_dem_bezug() -> None:
    """NW-ID bzw. Kürzel: über den Bezug (auch Frist → Objekt), sonst Modell und ID."""
    from apps.fristen import services as fristen

    hof = ObjektFabrik(kuerzel="HOF", mandant=MandantFabrik())
    frist = fristen.frist_anlegen("sonstige", datetime.date(2026, 12, 1), "hand", hof)
    assert services.kennungen(_posten(FRIST, frist)) == ["HOF"]
    lauf = kern.lauf_starten("probe")
    assert services.kennungen(_posten(FRIST, lauf)) == [f"Lauf #{lauf.pk}"]
    ohne = _posten(FRIST)
    assert services.kennungen(ohne) == [f"Arbeitsposten #{ohne.pk}"]


# --- Echter Client gegen eine nachgebaute API (kein Netz) ----------------------------


def test_http_client_spricht_die_notion_api(settings: Any) -> None:
    """Der echte Client: POST /v1/pages mit Parent und Eigenschaften, PATCH zum Ändern,
    Header mit Token und aktueller Notion-Version. Fehler nennen nur Status und Code."""
    settings.HOF_NOTION_TOKEN = "ntn_nur_fuer_tests"  # noqa: S105
    settings.HOF_NOTION_DATENBANK_ID = "db-123"
    anfragen: list[httpx.Request] = []

    def antwort(anfrage: httpx.Request) -> httpx.Response:
        anfragen.append(anfrage)
        if anfrage.url.path.endswith("/kaputt"):
            return httpx.Response(400, json={"code": "validation_error", "message": "Erika"})
        return httpx.Response(200, json={"id": "page-1"})

    client = NotionHttpClient(transport=httpx.MockTransport(antwort))
    inhalt = AufgabenInhalt(
        titel="1 Frist fällig", text="NW-IDs/Kürzel: HOF", faellig_am=None, link=f"{BASIS}/"
    )
    assert client.anlegen(inhalt) == "page-1"
    client.aktualisieren("page-1", AufgabenInhalt(**{**inhalt.__dict__, "erledigt": True}))
    with pytest.raises(NotionFehler, match=r"^Notion antwortet 400 validation_error$"):
        client.aktualisieren("kaputt", inhalt)

    anlegen, aendern = anfragen[0], anfragen[1]
    assert anlegen.method == "POST" and str(anlegen.url) == "https://api.notion.com/v1/pages"
    assert anlegen.headers["Authorization"] == "Bearer ntn_nur_fuer_tests"
    assert anlegen.headers["Notion-Version"] == notion.NOTION_VERSION
    koerper = json.loads(anlegen.content)
    assert koerper["parent"] == {"type": "database_id", "database_id": "db-123"}
    assert koerper["properties"]["Name"]["title"][0]["text"]["content"] == "1 Frist fällig"
    assert koerper["properties"]["Fällig"] == {"date": None}
    assert aendern.method == "PATCH" and aendern.url.path == "/v1/pages/page-1"
    assert json.loads(aendern.content)["properties"]["Status"] == {"status": {"name": "Erledigt"}}


def test_http_client_ohne_token(settings: Any) -> None:
    settings.HOF_NOTION_TOKEN = ""
    with pytest.raises(notion.NotionNichtEingerichtet):
        NotionHttpClient()


def test_feldnamen_kommen_aus_der_konfiguration(settings: Any) -> None:
    settings.HOF_NOTION_FELDER = {
        **settings.HOF_NOTION_FELDER,
        "titel": "Aufgabe",
        "status_typ": "select",
        "text": "",
    }
    daten = notion.eigenschaften(AufgabenInhalt("1 Frist fällig", "x", None, "https://x.invalid"))
    assert set(daten) == {"Aufgabe", "Status", "Fällig", "Link"}
    assert daten["Status"] == {"select": {"name": "Offen"}}


# --- Keine Personenfelder in Titel oder Text -----------------------------------------


def _personenwerte() -> set[str]:
    from apps.kern import personenfelder

    werte: set[str] = set()
    for modell in personenfelder.eigene_modelle():
        felder = sorted(personenfelder.personenfelder(modell))
        if not felder or hasattr(modell, "pgh_tracked_model"):
            continue
        for zeile in modell._default_manager.values_list(*felder):
            werte |= {str(w) for w in zeile if w and len(str(w)) >= 4}
    return werte


def test_keine_personenfelder_in_titel_oder_text(testhof: Any) -> None:
    """Abnahme Schritt 9: Ein Test prüft, dass keine Personenfelder in Titel oder Text der
    Aufgaben landen. Mit dem Testhof: Fristen werden Posten (Bezug Vertrag, Kaution,
    Bankfreigabe …), dazu Posten aller fünf Arten an Einheiten, Verträgen und Parteien;
    kein Wert eines registrierten Personenfelds und kein Betrag steht in Notion."""
    from testhof.aufbau import STICHTAG

    call_command("fristen", "--stichtag", STICHTAG.isoformat(), stdout=io.StringIO())
    stammdaten = testhof.stammdaten
    bezuege = [
        *list(stammdaten.einheiten.values())[:3],
        *list(stammdaten.vertraege.values())[:3],
        *stammdaten.parteien[:3],
    ]
    for art in services.arten():
        for bezug in bezuege:
            _posten(art, bezug, STICHTAG)
    werte = _personenwerte()
    assert any(" " in w for w in werte), "Der Testhof sollte Namen und Anschriften liefern."

    assert _abgleich().status == Lauf.Status.ERFOLGREICH
    assert len(FakeNotion.seiten) == len(services.arten())
    for page_id in FakeNotion.seiten:
        seite = _seite(page_id)
        inhalt = f"{seite['titel']}\n{seite['text']}"
        for wert in werte:
            assert wert not in inhalt, (page_id, wert)
        assert not re.search(r"\d+,\d{2}|€|EUR", inhalt)
        assert re.fullmatch(r"\d+ [A-Za-zäöüÄÖÜß ]+", seite["titel"])
        assert "NW-IDs/Kürzel: " in seite["text"]
