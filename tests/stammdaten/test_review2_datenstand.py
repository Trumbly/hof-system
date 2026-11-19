"""Review 2, B12: Datenstand gruppiert nach fehlendem Feld, dichte Tabelle, Sammelaktion
„Scans hochladen“ mit Zuordnung über die V-Nummer im Dateinamen. Mit dem Testhof (ein Teil
der Verträge hat dort einen Scan)."""

from __future__ import annotations

import re

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse

from apps.stammdaten import datenstand, scans
from apps.stammdaten.models import Vertrag
from testhof.aufbau import STICHTAG, Testhof
from testhof.pdfs import einfaches_pdf

SEITE = reverse("stammdaten:datenstand")


def _ohne_scan() -> list[Vertrag]:
    return [v for v in Vertrag.objects.bindend().order_by("nummer") if not v.unterschriebener_scan]


def test_gruppiert_nach_fehlendem_feld(testhof: Testhof, angemeldet: Client) -> None:
    """Review 2, B12: „14 Verträge ohne Scan“ als eine Gruppe mit Sammelaktion; die Seite ist
    eine dichte Tabelle statt Karten mit Kacheln."""
    stand = {b.schluessel: b for b in datenstand.ermitteln(STICHTAG)}
    gruppen = {g.feld: g for g in datenstand.nach_feld(stand["vertrag"])}
    scan = gruppen["scan"]
    anzahl = len(_ohne_scan())
    assert scan.anzahl == anzahl and scan.titel == f"{anzahl} Verträge ohne Scan"
    assert scan.sammelaktion == "scans"
    assert len(gruppen) > 1  # mehr als nur der eine Punkt

    html = angemeldet.get(SEITE).content.decode()
    assert f"{anzahl} Verträge ohne Scan" in html
    assert 'id="datenstand-uebersicht"' in html and 'class="kacheln"' not in html
    assert 'id="bereich-vertrag"' in html and 'id="bereich-katalog"' in html
    assert "?dialog=scans" in html


def test_v_nummer_im_dateinamen() -> None:
    """Review 2, B12: Die V-Nummer im Dateinamen in jeder üblichen Schreibweise."""
    assert scans.v_nummer("V-0005 unterschrieben.pdf") == "V-0005"
    assert scans.v_nummer("scan_v0012.pdf") == "V-0012"
    assert scans.v_nummer("Vertrag V 7.pdf") == "V-0007"
    assert scans.v_nummer("ordner/V_0031-final.PDF") == "V-0031"
    assert scans.v_nummer("Rechnung 4779.pdf") == ""
    assert scans.v_nummer("NWV-0005.pdf") == ""


def _datei(name: str, inhalt: bytes) -> SimpleUploadedFile:
    return SimpleUploadedFile(name, inhalt, content_type="application/pdf")


def test_scans_hochladen_vorschau_dann_uebernehmen(testhof: Testhof, angemeldet: Client) -> None:
    """Review 2, B12: Mehrere Scans hochladen, die Vorschau zeigt je Datei den Vertrag oder
    den Grund; erst „übernehmen“ hängt sie an. Danach fehlen die Verträge nicht mehr."""
    erster, zweiter = _ohne_scan()[:2]
    mit_scan = next(v for v in Vertrag.objects.bindend() if v.unterschriebener_scan)
    pdf = einfaches_pdf(["Scan (erfunden)"])
    url = reverse("stammdaten:datenstand_scans")

    dialog = angemeldet.get(SEITE, {"dialog": "scans"}).content.decode()
    assert 'name="scans"' in dialog and "multiple" in dialog

    antwort = angemeldet.post(
        url,
        {
            "aktion": "vorschau",
            "scans": [
                _datei(f"{erster.nummer} unterschrieben.pdf", pdf),
                _datei(f"scan_{zweiter.nummer.replace('-', '')}.pdf", pdf + b"%2"),
                _datei(f"{mit_scan.nummer}.pdf", pdf + b"%3"),
                _datei("ohne-nummer.pdf", pdf + b"%4"),
                _datei("V-0999.pdf", pdf + b"%5"),
                _datei(f"{erster.nummer}-foto.jpg", b"\xff\xd8\xff"),
            ],
        },
        HTTP_HX_REQUEST="true",
    )
    vorschau = antwort.content.decode()
    assert "2 von 6 Dateien passen" in vorschau
    assert "Hat schon einen Scan; der bleibt." in vorschau
    assert "Keine V-Nummer im Dateinamen." in vorschau
    assert "Einen Vertrag V-0999 gibt es nicht." in vorschau
    assert "Kein PDF; Scans bitte als PDF." in vorschau
    erster.refresh_from_db()
    assert not erster.unterschriebener_scan  # noch nichts angehängt
    kennung = re.search(r'name="kennung" value="([0-9a-f]+)"', vorschau)
    assert kennung is not None

    fertig = angemeldet.post(
        url, {"aktion": "uebernehmen", "kennung": kennung.group(1)}, HTTP_HX_REQUEST="true"
    )
    teil = fertig.content.decode()
    assert f"2 Scans übernommen · {erster.nummer}, {zweiter.nummer}" in teil
    assert 'id="datenstand-inhalt"' in teil and 'hx-swap-oob="true"' in teil
    for vertrag in (erster, zweiter):
        vertrag.refresh_from_db()
        assert vertrag.unterschriebener_scan
    assert erster not in _ohne_scan()

    nochmal = angemeldet.post(
        url, {"aktion": "uebernehmen", "kennung": kennung.group(1)}, HTTP_HX_REQUEST="true"
    )
    assert "Die Vorschau ist abgelaufen" in nochmal.content.decode()
