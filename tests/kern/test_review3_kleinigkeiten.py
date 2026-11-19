"""Review 3, Punkt 10: Kleinigkeiten. Klartext im Protokoll, kurzer Toast nach einer
Rücknahme, Cockpit ohne große Lücken, Läufe in dev „übersprungen (dev)“."""

from __future__ import annotations

import io
from typing import Any

import pytest
from django.core.management import call_command
from django.test import Client
from django.urls import reverse
from django.utils.html import strip_tags

from apps.cockpit import tageslauf
from apps.dokumente.models import Dokument, Schreiben
from apps.kern import services
from apps.kern.models import Arbeitsposten, Lauf
from apps.kern.vorgaenge import objekt_titel, ruecknahme_titel, vorgang
from apps.stammdaten.models import Checkliste, EinheitStatus, Kontaktweg, Vertragspartei
from tests.factories import ObjektFabrik

pytestmark = pytest.mark.django_db


# --- Protokoll in Klartext -------------------------------------------------------------


@pytest.mark.parametrize(
    ("modell", "daten", "erwartet"),
    [
        (Checkliste, {"id": 18, "phase": "rueckgabe"}, "Checkliste Rückgabe"),
        (Checkliste, {"id": 3, "phase": "uebergabe"}, "Checkliste Übergabe"),
        (EinheitStatus, {"id": 5, "status": "frei"}, "Status frei"),
        (Kontaktweg, {"id": 2, "art": "email"}, "Kontaktweg E-Mail"),
        (Vertragspartei, {"id": 4, "rolle": "mieter"}, "Vertragspartei Mieter"),
        (Dokument, {"id": 9, "art": "beleg"}, "Dokument Beleg (Rechnung, Quittung)"),
        (Schreiben, {"id": 7, "art": "mitteilung"}, "Schreiben Mitteilung"),
    ],
)
def test_protokoll_nennt_objekte_in_klartext(
    modell: type[Any], daten: dict[str, Any], erwartet: str
) -> None:
    """Review 3, Punkt 10: Das Protokoll zeigt „Checkliste Rückgabe“ statt „Checkliste #18
    rueckgabe“, ebenso andere Objekte mit internen Schlüsseln."""
    titel = objekt_titel(modell, daten)
    assert titel.startswith(erwartet), titel
    assert "rueckgabe" not in titel and "#" not in titel


def test_klartext_auf_der_protokollseite(angemeldet: Client) -> None:
    """Review 3, Punkt 10: Auf der Seite des Protokolls steht der Klartext."""
    from apps.stammdaten import services as stammdaten

    objekt = ObjektFabrik()
    from apps.stammdaten.models import Einheit

    einheit = stammdaten.einheit_anlegen(objekt, Einheit.Typ.GARAGE, bezeichnung="Garage R3")
    with vorgang("Checkliste angelegt"):
        stammdaten.checkliste_anlegen("rueckgabe", einheit=einheit)
    html = angemeldet.get(reverse("kern:protokoll")).content.decode()
    text = " ".join(strip_tags(html).split())
    assert "Checkliste Rückgabe" in text
    assert " rueckgabe" not in text


# --- Toast nach einer Rücknahme --------------------------------------------------------


def test_ruecknahme_titel() -> None:
    """Review 3, Punkt 10: „Kündigung V-0002 erfasst“ wird „Kündigung V-0002
    zurückgenommen“; andere Titel stehen in Anführungszeichen."""
    assert ruecknahme_titel("Kündigung V-0002 erfasst") == "Kündigung V-0002 zurückgenommen"
    assert ruecknahme_titel("Vertrag V-0010 angelegt") == "Vertrag V-0010 zurückgenommen"
    assert (
        ruecknahme_titel("Miete V-0002 ab 01.12.2026 geändert")
        == "„Miete V-0002 ab 01.12.2026 geändert“ zurückgenommen"
    )


def test_toast_nach_ruecknahme_ist_kurz(angemeldet: Client) -> None:
    """Review 3, Punkt 10: Der Toast nach einer Rücknahme ist kurz: „Kündigung V-0002
    zurückgenommen · 2 Änderungen → ansehen“, ohne die Liste der einzelnen Schritte."""
    objekt = ObjektFabrik(bezeichnung="Halle Nord", anschrift="Alte Anschrift")
    with vorgang("Kündigung V-0002 erfasst") as v:
        services.objekt_aendern(objekt, bezeichnung="Halle Süd")
        services.objekt_aendern(objekt, anschrift="Neue Anschrift")
    antwort = angemeldet.post(reverse("kern:vorgang_zuruecknehmen", args=[v.id]), follow=True)
    html = antwort.content.decode()
    text = " ".join(strip_tags(html).split())
    assert "Kündigung V-0002 zurückgenommen · 2 Änderungen → ansehen" in text
    assert "Rücknahme von" not in text.split("zurückgenommen ·")[0][-60:]


# --- Cockpit ------------------------------------------------------------------------------


def test_cockpit_raster_als_mauerwerk(angemeldet: Client) -> None:
    """Review 3, Punkt 10: Die Kacheln des Cockpits liegen als Mauerwerk (``data-mauerwerk``,
    bausteine.js), damit unter kurzen Kacheln keine großen Lücken bleiben. Die Abstände
    misst der Oberflächentest (tests/e2e/test_review3.py) bei 1440 und 1024 px."""
    html = angemeldet.get(reverse("kern:startseite")).content.decode()
    assert '<ul class="cockpit-raster cockpit" data-mauerwerk>' in html


# --- Läufe in dev ---------------------------------------------------------------------


def _lauf(name: str) -> Lauf:
    return Lauf.objects.filter(name=name).latest("gestartet_am", "pk")


def test_sicherung_ohne_statusdatei_in_dev_uebersprungen(settings: Any) -> None:
    """Review 3, Punkt 10: In dev endet sicherung_pruefen ohne Statusdatei „übersprungen
    (dev)“ statt „mit Fehlern“, ohne Arbeitsposten."""
    settings.HOF_DEV_LAEUFE = True
    call_command("sicherung_pruefen", stdout=io.StringIO())
    lauf = _lauf("sicherung_pruefen")
    assert lauf.status == Lauf.Status.UEBERSPRUNGEN
    assert lauf.fehler == 0
    assert lauf.meldung.startswith("übersprungen (dev)")
    assert not Arbeitsposten.objects.filter(art="Sicherung prüfen").exists()


def test_sicherung_ohne_statusdatei_in_prod_ein_fehler(
    settings: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review 3, Punkt 10: In prod bleibt die fehlende Statusdatei ein Fehler, auch wenn
    HOF_DEV_LAEUFE versehentlich gesetzt ist."""
    settings.HOF_DEV_LAEUFE = True
    monkeypatch.setenv("DJANGO_ENV", "prod")
    call_command("sicherung_pruefen", stdout=io.StringIO())
    lauf = _lauf("sicherung_pruefen")
    assert lauf.status == Lauf.Status.MIT_FEHLERN
    assert "Statusdatei fehlt" in lauf.meldung


def test_tageslauf_in_dev_uebersprungen_statt_mit_fehlern(
    settings: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review 3, Punkt 10: Fehlen in dev Bank und Sicherung, ist der Tageslauf „übersprungen
    (dev)“ statt „mit Fehlern“; die Meldung nennt die übersprungenen Schritte."""
    settings.HOF_DEV_LAEUFE = True
    kopie = {n: s for n, s in tageslauf._SCHRITTE.items() if n in ("sicherung_pruefen", "fristen")}
    monkeypatch.setattr(tageslauf, "_SCHRITTE", kopie)
    call_command("tageslauf", stdout=io.StringIO())
    lauf = _lauf("tageslauf")
    assert lauf.status == Lauf.Status.UEBERSPRUNGEN
    assert lauf.fehler == 0
    assert lauf.meldung.startswith("übersprungen (dev): sicherung_pruefen.")
    assert _lauf("fristen").status == Lauf.Status.ERFOLGREICH

    settings.HOF_DEV_LAEUFE = False
    call_command("tageslauf", stdout=io.StringIO())
    assert _lauf("tageslauf").status == Lauf.Status.MIT_FEHLERN
