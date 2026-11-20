"""Review 2, A3 und A11: Eine Kündigung von der Website wird zur Kündigung am Vertrag, mit
Fristen und Bestätigung; der Wunschtermin vor dem Fristende geht nicht still verloren; ein
später angelegter Vertrag findet seine angenommene Buchungsanfrage."""

from __future__ import annotations

import datetime
import json
import re
import warnings
from decimal import Decimal

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from pghistory.models import Events

from apps.anbindungen import services
from apps.anbindungen.models import Buchungsanfrage
from apps.dokumente import pdf
from apps.dokumente import services as dokumente
from apps.dokumente.models import Schreiben
from apps.fristen import services as fristen
from apps.fristen.models import Frist
from apps.kern import services as kern
from apps.kern import vorgaenge
from apps.kern.models import Arbeitsposten, Benutzer, Objekt
from apps.soll import services as soll
from apps.soll.models import Sollposten
from apps.stammdaten import services as stammdaten
from apps.stammdaten.models import Kuendigung, Vertrag
from testhof import dokumente as testhof_dokumente
from tests.anbindungen.conftest import aktiver_vertrag, vermarktete_einheit
from tests.stammdaten.conftest import mieter_anlegen

pytestmark = pytest.mark.django_db
D = datetime.date
URL_API = "/api/website/ereignis"
ABSENDER = "Hanna Erfunden"
MAIL = "hanna.erfunden@testhof.invalid"
ZUGANG = D(2026, 11, 17)
ATTRAPPE = b"%PDF-1.7\n% Attrappe\n%%EOF\n"


@pytest.fixture(autouse=True)
def _pdf_ohne_weasyprint(monkeypatch: pytest.MonkeyPatch) -> None:
    da, grund = pdf.verfuegbar()
    if da:
        return
    warnings.warn(f"PDF-Attrappe statt WeasyPrint: {grund}", stacklevel=1)
    zaehler = iter(range(1, 1_000_000))
    monkeypatch.setattr(pdf, "erzeugen", lambda html: ATTRAPPE + str(next(zaehler)).encode())


@pytest.fixture
def vorlagen(benutzer: Benutzer) -> None:
    """Briefkopf, Kündigungsbestätigung und Aufhebungsvertrag mit den Texten des Testhofs."""
    for name, art, betreff, text in testhof_dokumente.VORLAGEN:
        if art in ("briefkopf", "kuendigungsbestaetigung", "aufhebungsvertrag"):
            vorlage = dokumente.vorlage_anlegen(name, art, text, betreff=betreff)
            dokumente.vorlage_freigeben(vorlage, benutzer, freigegeben_von="Test")


@pytest.fixture
def api(token: str) -> Client:
    """Die Website: eigener Client mit Token, getrennt vom angemeldeten Browser."""
    return Client(HTTP_AUTHORIZATION=f"Bearer {token}")


@pytest.fixture
def vertrag(objekt: Objekt) -> Vertrag:
    return aktiver_vertrag(vermarktete_einheit(objekt))


def _kuendigung_melden(website: Client, vertrag: Vertrag, wunsch: D | None) -> Buchungsanfrage:
    einheit = vertrag.einheiten.get()
    antwort = website.post(
        URL_API,
        data=json.dumps(
            {
                "ereignis_id": "web-2026-kuendigung-1",
                "art": "kuendigung",
                "nw_id": einheit.nw_id,
                "zeitpunkt": f"{ZUGANG.isoformat()}T10:15:00+01:00",
                "wunsch_ab": wunsch.isoformat() if wunsch else None,
                "vertragsnummer": vertrag.nummer,
                "kontakt": {"name": ABSENDER, "email": MAIL},
                "nachricht": "Hiermit kündige ich.",
            }
        ),
        content_type="application/json",
    )
    assert antwort.status_code == 201
    return Buchungsanfrage.objects.get(ereignis_id="web-2026-kuendigung-1")


def _formular(vertrag: Vertrag, anfrage: Buchungsanfrage | None, **felder: str) -> dict[str, str]:
    daten = {
        "aktion": "kuendigung",
        "kuendigung-zugang_am": ZUGANG.isoformat(),
        "kuendigung-von": "mieter",
        "kuendigung-zugangsweg": "website",
    }
    if anfrage is not None:
        daten["kuendigung-anfrage"] = str(anfrage.pk)
    daten.update({f"kuendigung-{k}": v for k, v in felder.items()})
    return daten


def _bestaetigung(kuendigung: Kuendigung) -> Schreiben:
    return Schreiben.objects.get(
        bezug_id=kuendigung.pk,
        art__in=(
            "kuendigungsbestaetigung",
            "aufhebungsvertrag",
        ),
    )


def test_website_kuendigung_durchgehend(
    vertrag: Vertrag, api: Client, angemeldet: Client, vorlagen: None
) -> None:
    """Review 2, A3: Ereignis über die API → Anfrage → Knopf „Kündigung anlegen“ →
    Kündigungsdialog vorausgefüllt (Zugang = Meldedatum, Mieter, Wunschtermin,
    Website-Formular) → Speichern: Kündigung, Fristen, Bestätigung „bereit“, Anfrage
    erledigt und mit der Kündigung verknüpft. Weicht der Absender ab, steht ein Hinweis da."""
    ende = stammdaten.kuendigung_fristende(vertrag, ZUGANG)
    wunsch = ende + datetime.timedelta(days=31)  # nach dem Fristende: gilt ohne Rückfrage
    anfrage = _kuendigung_melden(api, vertrag, wunsch)

    seite = angemeldet.get(reverse("anbindungen:anfrage", args=[anfrage.pk])).content.decode()
    assert "Kündigung anlegen" in seite
    assert "Kündigung erfasst" not in seite  # kein Knopf mehr, der nur die Anfrage schließt
    assert "Absender weicht ab" in seite and ABSENDER in seite
    link = reverse("stammdaten:vertrag", args=[vertrag.pk]) + f"?anfrage={anfrage.pk}"
    assert link in seite

    dialog = angemeldet.get(link)
    html = dialog.content.decode()
    form = dialog.context["kuendigung"]
    assert form.initial["zugang_am"] == ZUGANG
    assert form.initial["von"] == "mieter"
    assert form.initial["zum_wunsch"] == wunsch
    assert form.initial["zugangsweg"] == "website"
    assert f'name="kuendigung-anfrage" value="{anfrage.pk}"' in html
    assert "Absender weicht ab" in html and "Testmieter" in html
    anfrage.refresh_from_db()
    assert anfrage.status == "offen"  # erst nach dem Speichern erledigt

    antwort = angemeldet.post(
        link,
        # Review 3, Punkt 9: Der Absender weicht ab, also ist die Wahl dazu Pflicht.
        _formular(vertrag, anfrage, zum_wunsch=wunsch.isoformat(), absender="vollmacht"),
        follow=True,
    )

    kuendigung = Kuendigung.objects.get(vertrag=vertrag)
    assert (kuendigung.zugang_am, kuendigung.von, kuendigung.zugangsweg) == (
        ZUGANG,
        "mieter",
        "website",
    )
    assert kuendigung.fristende == wunsch and not kuendigung.aufhebung
    vertrag.refresh_from_db()
    assert (vertrag.status, vertrag.ende) == (Vertrag.Status.GEKUENDIGT, wunsch)
    assert {f.art for f in fristen.fristen_zu(kuendigung, nur_offen=True)} == {Frist.Art.KUENDIGUNG}
    assert {f.art for f in fristen.fristen_zu(vertrag, nur_offen=True)} == {Frist.Art.VERTRAGSENDE}
    bestaetigung = _bestaetigung(kuendigung)
    assert bestaetigung.status == Schreiben.Status.BEREIT
    anfrage.refresh_from_db()
    assert (anfrage.status, anfrage.kuendigung, anfrage.vertrag) == (
        "erledigt",
        kuendigung,
        vertrag,
    )
    assert not Arbeitsposten.objects.filter(
        art=services.ART_BUCHUNGSANFRAGE, status="offen"
    ).exists()
    meldung = antwort.content.decode()
    assert f"Kündigung erfasst · Ende {wunsch:%d.%m.%Y}" in meldung
    assert reverse("dokumente:schreiben", args=[bestaetigung.pk]) in meldung
    assert f'Anfrage <a href="{reverse("anbindungen:anfrage", args=[anfrage.pk])}">' in meldung


def test_bestaetigung_erkennt_textform_nicht_an(vertrag: Vertrag, vorlagen: None) -> None:
    """Review 2, A3: Bei Zugangsweg Website steht in der Kündigungsbestätigung kein Satz, der
    die Form ausdrücklich anerkennt (``kuendigung.textform``); beim Brief schon."""
    website = stammdaten.kuendigung_erfassen(vertrag, ZUGANG, zugangsweg="website")
    text = _bestaetigung(website).inhalt_html
    assert "Website-Formular" in text
    assert not re.search(r"schriftlich|formgerecht|Schriftform|wirksam", text, re.I)

    zweiter = aktiver_vertrag(vermarktete_einheit(vertrag.einheiten.get().objekt))
    brief = stammdaten.kuendigung_erfassen(zweiter, ZUGANG, zugangsweg="brief")
    assert "schriftliche Kündigung" in _bestaetigung(brief).inhalt_html


def test_erledigt_nur_mit_kuendigung(vertrag: Vertrag, api: Client) -> None:
    """Review 2, A3: Eine Website-Kündigung lässt sich nicht ohne erfasste Kündigung als
    erledigt schließen (sonst ginge die Kündigung verloren)."""
    anfrage = _kuendigung_melden(api, vertrag, None)
    with pytest.raises(services.AnbindungFehler, match="erst, wenn sie am Vertrag erfasst ist"):
        services.buchungsanfrage_entscheiden(anfrage, "erledigt")
    vertrag.refresh_from_db()
    assert vertrag.status == Vertrag.Status.AKTIV


def test_vorhandene_kuendigung_verknuepfen(
    vertrag: Vertrag, api: Client, angemeldet: Client, vorlagen: None
) -> None:
    """Review 2, A3: Ist die Kündigung schon (per Brief) erfasst, verknüpft die Anfrage sich
    mit ihr und ist dann erledigt."""
    anfrage = _kuendigung_melden(api, vertrag, None)
    kuendigung = stammdaten.kuendigung_erfassen(vertrag, ZUGANG, zugangsweg="brief")
    url = reverse("anbindungen:anfrage", args=[anfrage.pk])
    assert f"Mit Kündigung #{kuendigung.pk} verknüpfen" in angemeldet.get(url).content.decode()

    angemeldet.post(url, {"status": "erledigt", "kuendigung": kuendigung.pk})

    anfrage.refresh_from_db()
    assert (anfrage.status, anfrage.kuendigung) == ("erledigt", kuendigung)


def test_wunschtermin_vor_fristende_fragt_nach(
    vertrag: Vertrag, angemeldet: Client, vorlagen: None
) -> None:
    """Review 2, A11: Liegt der Wunschtermin vor dem Fristende, geht er nicht still verloren:
    Der Dialog fragt nach, mit der Wahl „Fristende <Datum>“ oder „Aufhebung zum Wunschtermin
    (Vorlage Aufhebungsvertrag)“. „Fristende“ kündigt zum Fristende."""
    ende = stammdaten.kuendigung_fristende(vertrag, ZUGANG)
    wunsch = D(2026, 12, 31)
    assert wunsch < ende
    url = reverse("stammdaten:vertrag", args=[vertrag.pk])

    antwort = angemeldet.post(url, _formular(vertrag, None, zum_wunsch=wunsch.isoformat()))

    assert antwort.status_code == 200
    assert not Kuendigung.objects.exists()
    html = antwort.content.decode()
    assert f"Der Wunschtermin {wunsch:%d.%m.%Y} liegt vor dem Fristende {ende:%d.%m.%Y}" in html
    assert f"Fristende {ende:%d.%m.%Y}" in html
    assert "Aufhebung zum Wunschtermin 31.12.2026 (Vorlage Aufhebungsvertrag)" in html
    assert 'type="radio" name="kuendigung-wunsch_wahl"' in html

    angemeldet.post(
        url, _formular(vertrag, None, zum_wunsch=wunsch.isoformat(), wunsch_wahl="fristende")
    )

    kuendigung = Kuendigung.objects.get()
    assert (kuendigung.fristende, kuendigung.zum_wunsch, kuendigung.aufhebung) == (
        ende,
        wunsch,
        False,
    )
    assert _bestaetigung(kuendigung).art == "kuendigungsbestaetigung"


def test_aufhebung_zum_wunschtermin(
    vertrag: Vertrag, angemeldet: Client, vorlagen: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review 2, A11: „Aufhebung zum Wunschtermin“ setzt das Vertragsende auf den Wunsch,
    kennzeichnet die Kündigung als Aufhebung, beendet das Soll zum Wunschtermin und legt den
    Aufhebungsvertrag als Schreiben „bereit“ an."""
    wunsch = D(2026, 12, 31)
    url = reverse("stammdaten:vertrag", args=[vertrag.pk])
    monkeypatch.setattr(soll, "heute", lambda: D(2027, 3, 15))
    soll.vertrag_geaendert(vertrag)
    assert Sollposten.objects.filter(vertrag=vertrag, von__gt=wunsch).exists()

    antwort = angemeldet.post(
        url,
        _formular(vertrag, None, zum_wunsch=wunsch.isoformat(), wunsch_wahl="aufhebung"),
        follow=True,
    )

    kuendigung = Kuendigung.objects.get()
    assert (kuendigung.fristende, kuendigung.aufhebung) == (wunsch, True)
    vertrag.refresh_from_db()
    assert (vertrag.status, vertrag.ende) == (Vertrag.Status.GEKUENDIGT, wunsch)
    assert (
        not Sollposten.objects.filter(vertrag=vertrag, von__gt=wunsch)
        .exclude(status="storniert")
        .exists()
    )
    aufhebung = _bestaetigung(kuendigung)
    assert (aufhebung.art, aufhebung.status) == ("aufhebungsvertrag", Schreiben.Status.BEREIT)
    assert "31.12.2026" in aufhebung.inhalt_html
    html = antwort.content.decode()
    assert "Aufhebung zum 31.12.2026 erfasst" in html and "Aufhebungsvertrag #" in html
    assert "Aufhebung zum Wunschtermin" in html  # gekennzeichnet am Vertrag


def test_vertrag_neu_verknuepft_angenommene_anfrage(objekt: Objekt, angemeldet: Client) -> None:
    """Review 2, A11: Ein Vertrag, der später (etwa von der Einheitenseite) auf eine Einheit
    mit angenommener Buchungsanfrage angelegt wird, ist mit der Anfrage verknüpft; die Anfrage
    steht auf „Vertrag angelegt“, ihr Arbeitsposten ist erledigt."""
    einheit = vermarktete_einheit(objekt)
    anfrage, _ = services.ereignis_annehmen(
        services.WebsiteEreignis(
            ereignis_id="w-buchung-1",
            art="buchung",
            nw_id=einheit.nw_id,
            gemeldet_am=timezone.now(),
            kontakt_name=ABSENDER,
            kontakt_email=MAIL,
        )
    )
    services.buchungsanfrage_entscheiden(anfrage, "angenommen")
    url = reverse("stammdaten:vertrag_neu") + f"?einheit={einheit.pk}"
    assert "angenommene Buchungsanfrage" in angemeldet.get(url).content.decode()
    mieter = mieter_anlegen("Neumieter")

    antwort = angemeldet.post(
        url,
        {
            "einheit": einheit.pk,
            "mieter": mieter.pk,
            "beginn": "2026-12-01",
            "miete": "85.00",
            "zahlweise": "monatlich",
            "kaution": "0.00",
        },
        follow=True,
    )

    vertrag = Vertrag.objects.get(vertragseinheiten__einheit=einheit)
    anfrage.refresh_from_db()
    assert (anfrage.status, anfrage.vertrag) == ("vertrag_angelegt", vertrag)
    assert not Arbeitsposten.objects.filter(
        art=services.ART_BUCHUNGSANFRAGE, status="offen", bezug_id=anfrage.pk
    ).exists()
    assert f'Buchungsanfrage <a href="{reverse("anbindungen:anfrage", args=[anfrage.pk])}">' in (
        antwort.content.decode()
    )
    assert Decimal("85.00") == vertrag.konditionen.get().miete


def test_ruecknahme_oeffnet_die_anfrage_wieder(
    vertrag: Vertrag, api: Client, vorlagen: None
) -> None:
    """Review 2, A3 mit A4: Der Vorgang „Kündigung V-… aus Anfrage #… erfasst“ lässt sich als
    Ganzes zurücknehmen. Danach ist die Anfrage wieder offen und ohne Verknüpfung zur
    Kündigung, ihr Arbeitsposten offen, der Vertrag aktiv, die Bestätigung verworfen."""
    anfrage = _kuendigung_melden(api, vertrag, None)
    (posten,) = Arbeitsposten.objects.filter(art=services.ART_BUCHUNGSANFRAGE)
    kuendigung = services.kuendigung_aus_anfrage(
        anfrage, vertrag, ZUGANG, absender=Kuendigung.Absender.VERTRAGSPARTEI
    )
    bestaetigung = _bestaetigung(kuendigung)
    vid = Events.objects.tracks(kuendigung).order_by("-pgh_created_at")[0].pgh_context_id
    titel = (
        f"Kündigung {vertrag.nummer} (Absender ist Vertragspartei (anderer Name/E-Mail)) "
        f"aus Anfrage #{anfrage.pk} erfasst"
    )
    assert vorgaenge.titel_von(vid) == titel

    bericht = kern.vorgang_zuruecknehmen(vid)

    anfrage.refresh_from_db()
    posten.refresh_from_db()
    vertrag.refresh_from_db()
    bestaetigung.refresh_from_db()
    assert bericht.titel == titel
    assert (anfrage.status, anfrage.kuendigung, anfrage.entschieden_am) == ("offen", None, None)
    assert anfrage.vertrag == vertrag  # aus der gemeldeten Vertragsnummer, wie vorher
    assert posten.status == Arbeitsposten.Status.OFFEN
    assert (vertrag.status, vertrag.ende) == (Vertrag.Status.AKTIV, None)
    assert not Kuendigung.objects.exists()
    assert bestaetigung.status == Schreiben.Status.VERWORFEN


def test_vorgangstitel_aufhebung_serie_und_verknuepfung(vertrag: Vertrag, vorlagen: None) -> None:
    """Review 2, A4 für die neuen Services: Aufhebung, Serie und Verknüpfung von Vertrag und
    Anfrage tragen eigene Titel (nur Nummern und IDs)."""
    aufhebung = stammdaten.kuendigung_erfassen(
        vertrag, ZUGANG, zum_wunsch=D(2026, 12, 31), aufhebung=True
    )
    vid = Events.objects.tracks(aufhebung).order_by("-pgh_created_at")[0].pgh_context_id
    assert vorgaenge.titel_von(vid) == f"Aufhebung {vertrag.nummer} zum 31.12.2026 erfasst"

    einheit = vermarktete_einheit(vertrag.einheiten.get().objekt)
    anfrage, _ = services.ereignis_annehmen(
        services.WebsiteEreignis(
            ereignis_id="w-buchung-titel",
            art="buchung",
            nw_id=einheit.nw_id,
            gemeldet_am=timezone.now(),
        )
    )
    services.buchungsanfrage_entscheiden(anfrage, "angenommen")
    neu = aktiver_vertrag(einheit, beginn=D(2026, 12, 1))
    services.vertrag_zur_anfrage(neu)
    anfrage.refresh_from_db()
    vid = Events.objects.tracks(anfrage).order_by("-pgh_created_at")[0].pgh_context_id
    assert vorgaenge.titel_von(vid) == f"Vertrag {neu.nummer} mit Anfrage #{anfrage.pk} verknüpft"
