"""Review 1, Punkt 9: ``benachrichtigen`` ohne Kanal ist in dev und test „übersprungen“,
in prod ein Fehler. Der Status „übersprungen“ erscheint in Läufen, Cockpit, Tageslauf,
Benachrichtigung und Status-MCP, ohne als gescheitert zu zählen."""

from __future__ import annotations

import datetime
from typing import Any

import pytest
from django.core.management import call_command
from django.test import Client, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.benachrichtigung import services
from apps.benachrichtigung.models import Benachrichtigung
from apps.cockpit import eigene_kacheln, tageslauf
from apps.fristen import services as fristen
from apps.fristen.models import Frist
from apps.kern.laeufe import LaufBefehl
from apps.kern.models import Lauf
from apps.statusmcp import werkzeuge
from testhof.aufbau import STICHTAG
from tests.factories import ObjektFabrik
from tests.statusmcp.conftest import RollenQuelle

pytestmark = pytest.mark.django_db

OHNE_KANAL = override_settings(HOF_BENACHRICHTIGUNG_MAIL="", HOF_NTFY_URL="", HOF_NTFY_THEMA="")


def _frist_faellig() -> None:
    fristen.frist_anlegen(
        Frist.Art.SONSTIGE,
        timezone.localdate() - datetime.timedelta(days=1),
        Frist.Quelle.HAND,
        ObjektFabrik(),
    )
    call_command("fristen")


def _lauf() -> Lauf:
    return Lauf.objects.filter(name="benachrichtigen").order_by("-pk")[0]


@pytest.mark.parametrize("umgebung", [None, "dev", "test"])
def test_benachrichtigen_ohne_kanal_in_dev_und_test_uebersprungen(
    monkeypatch: pytest.MonkeyPatch, umgebung: str | None
) -> None:
    """Review 1, Punkt 9: Ohne Kanal endet benachrichtigen in dev und test mit Status
    „übersprungen“, ohne Fehler; gemeldet ist nichts."""
    if umgebung is None:
        monkeypatch.delenv("DJANGO_ENV", raising=False)
    else:
        monkeypatch.setenv("DJANGO_ENV", umgebung)
    _frist_faellig()

    with OHNE_KANAL:
        call_command("benachrichtigen")

    lauf = _lauf()
    assert lauf.status == Lauf.Status.UEBERSPRUNGEN
    assert lauf.get_status_display() == "übersprungen"
    assert lauf.fehler == 0
    assert "übersprungen" in lauf.meldung
    assert not Benachrichtigung.objects.exists()


def test_benachrichtigen_ohne_kanal_in_prod_ein_fehler(monkeypatch: pytest.MonkeyPatch) -> None:
    """Review 1, Punkt 9: In prod bleibt „kein Kanal“ ein Fehler."""
    monkeypatch.setenv("DJANGO_ENV", "prod")
    _frist_faellig()

    with OHNE_KANAL:
        assert services.benachrichtigen().status == "fehlgeschlagen"
        call_command("benachrichtigen")

    lauf = _lauf()
    assert lauf.status == Lauf.Status.MIT_FEHLERN
    assert lauf.fehler == 1
    assert "kein Kanal" in lauf.meldung


def test_ohne_lage_bleibt_es_still_auch_ohne_kanal() -> None:
    """Review 1, Punkt 9: Ohne etwas zu melden ist der Lauf erfolgreich wie bisher."""
    with OHNE_KANAL:
        call_command("benachrichtigen")
    assert _lauf().status == Lauf.Status.ERFOLGREICH


class UebersprungenerLauf(LaufBefehl):
    lauf_name = "benachrichtigen"

    def ausfuehren(self, **optionen: Any) -> str:
        self.ueberspringen()
        return "übersprungen"


def test_uebersprungen_zaehlt_nirgends_als_gescheitert(angemeldet: Client) -> None:
    """Review 1, Punkt 9: Ein übersprungener Lauf ist in Benachrichtigung, Cockpit und
    Status-MCP kein Problem und steht als „übersprungen“ in den Läufen."""
    call_command(UebersprungenerLauf())

    assert services.lage_ermitteln().gescheitert == {}
    assert eigene_kacheln.laeufe()["gescheitert"] == 0

    html = angemeldet.get(reverse("kern:laeufe")).content.decode()
    # R2-9: Status als Badge (neutral) in der neuen Gestaltung
    assert '<span class="badge badge-neutral">übersprungen</span>' in html
    start = angemeldet.get(reverse("kern:startseite")).content.decode()
    assert "status-uebersprungen" in start

    with RollenQuelle().lesen() as leser:
        laeufe = werkzeuge.ueberblick(leser, STICHTAG)["laeufe"]
    assert laeufe["uebersprungen"] == ["benachrichtigen"]
    assert laeufe["mit_problem"] == []
    assert [z["status"] for z in laeufe["letzte"]] == ["uebersprungen"]


def test_tageslauf_zaehlt_uebersprungenen_schritt_nicht_als_fehler() -> None:
    """Review 1, Punkt 9: Im Tageslauf ist der Schritt benachrichtigen ohne Kanal kein
    Fehler; der Tageslauf bleibt erfolgreich."""
    namen = [s.name for s in tageslauf.schritte() if s.name != "benachrichtigen"]
    _frist_faellig()

    with OHNE_KANAL:
        call_command("tageslauf", *[f"--ohne={n}" for n in namen])

    lauf = Lauf.objects.filter(name="tageslauf").get()
    assert lauf.status == Lauf.Status.ERFOLGREICH, lauf.meldung
    assert "benachrichtigen: übersprungen" in lauf.meldung
