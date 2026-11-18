"""Standardablauf 10: Tageslauf des Systems (Prozessliste), durchgehend mit dem Testhof.

Auslöser: der Zeitplan. Ergebnis: ein aktuelles Hof-Cockpit, bevor du es öffnest.

1. Nachts: Sicherung (hier eine Fake-Statusdatei), Abgleich mit Verwaltio, Austausch mit
   der Website (ID-Abgleich).
2. Morgens: Bankabruf, danach Zuordnung (Einordnen; der Abgleich kommt mit Schritt 6).
3. Danach: Fristen prüfen; montags der Mahnlauf (Schritt 7), am Monatsanfang Sollstellung.
4. System: benachrichtigt dich nur, wenn eine Freigabe wartet oder ein Lauf gescheitert
   ist; Aufgaben stehen im Hof-Backlog.
5. Claude sieht über den Status-MCP dieselben Zähler: kommt mit Schritt 11, hier nicht.
"""

from __future__ import annotations

import datetime
import io
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from django.core import mail
from django.core.management import call_command
from django.test import Client
from django.urls import reverse

from apps.benachrichtigung.kanaele import FakeKanal
from apps.cockpit.models import NotionAufgabe
from apps.cockpit.notion import FakeNotion
from apps.kern.models import Arbeitsposten, Lauf
from testhof.aufbau import STICHTAG, Testhof


@pytest.fixture(autouse=True)
def _fakes() -> Iterator[None]:
    FakeKanal.gesendet.clear()
    FakeNotion.zuruecksetzen()
    yield
    FakeKanal.gesendet.clear()
    FakeNotion.zuruecksetzen()


@pytest.fixture
def sicherung_von_heute_nacht(tmp_path: Path, settings: Any) -> Path:
    pfad = tmp_path / "backup-status.json"
    zeit = datetime.datetime.now(datetime.UTC) - datetime.timedelta(hours=3)
    pfad.write_text(
        json.dumps({"zeit": zeit.strftime("%Y-%m-%dT%H:%M:%SZ"), "ergebnis": "ok"}),
        encoding="utf-8",
    )
    settings.HOF_BACKUP_STATUS = str(pfad)
    return pfad


def _tageslauf() -> Lauf:
    call_command("tageslauf", "--stichtag", STICHTAG.isoformat(), stdout=io.StringIO())
    return Lauf.objects.filter(name="tageslauf").latest("gestartet_am", "pk")


def test_standardablauf_10_tageslauf(
    testhof: Testhof, angemeldet: Client, sicherung_von_heute_nacht: Path
) -> None:
    """Standardablauf 10 und Abnahme Schritt 9: Der Tageslauf ruft alle Teile in der
    Reihenfolge des Bauplans; danach zeigt das Cockpit den Stand direkt aus der Datenbank,
    fällige Fristen stehen als Aufgabe im Hof-Backlog, und die Benachrichtigung meldet die
    wartenden Freigaben einmal mit Zähler und Link."""
    lauf = _tageslauf()

    # 1.–3.: alle Teile als eigene Läufe, in Reihenfolge.
    teile = list(Lauf.objects.filter(pk__gt=lauf.pk).order_by("pk").values_list("name", "status"))
    namen = [n for n, _ in teile]
    assert namen == [
        "bank_abruf",
        "kassenbuch_einordnen",
        "soll_monat",
        "fristen",
        "zaehler_stichtag",
        "verwaltio_kennzahlen",
        "id_abgleich",
        "aufgaben_abgleich",
        "sicherung_pruefen",
        "benachrichtigen",
    ]
    lauf.refresh_from_db()
    gescheitert = [n for n, s in teile if s != Lauf.Status.ERFOLGREICH]
    assert lauf.fehler == len(gescheitert)
    for name in gescheitert:
        assert f"{name}: " in lauf.meldung

    # 3.: Fristen sind Arbeitsposten geworden.
    faellig = Arbeitsposten.objects.filter(art="Frist fällig", status="offen").count()
    assert faellig >= 1

    # 4.: Hof-Backlog: eine Aufgabe „Frist(en) fällig“ mit Zähler und Link.
    aufgabe = NotionAufgabe.objects.get(art="Frist fällig", status="offen")
    assert aufgabe.page_id in FakeNotion.seiten
    assert aufgabe.titel.startswith(f"{faellig} Frist")
    assert "/arbeitslisten/?art=Frist+f%C3%A4llig" in aufgabe.text

    # 4.: Benachrichtigung mit Zählern und Link, je Kanal einmal.
    assert len(mail.outbox) == 1
    assert [k for k, _ in FakeKanal.gesendet] == ["ntfy"]
    text = mail.outbox[0].body
    assert f"Frist fällig: {faellig}" in text
    assert "https://hof-system.testhof.invalid/" in text

    # Ergebnis: Das Cockpit zeigt den Stand direkt aus der Datenbank.
    html = angemeldet.get(reverse("kern:startseite")).content.decode()
    freigaben = html.split('id="kachel-freigaben"', 1)[1].split("</li>", 1)[0]
    assert f"Frist fällig: <strong>{faellig}</strong>" in freigaben
    laeufe = html.split('id="kachel-laeufe"', 1)[1].split("</li>", 1)[0]
    for name in namen:
        assert name in laeufe
    assert "in Ordnung" in html.split('id="kachel-sicherung"', 1)[1].split("</li>", 1)[0]

    # Weitere Tagesläufe am selben Tag legen nichts doppelt an. Gemeldet wird nur Neues:
    # einmal noch, dass der erste Tageslauf selbst mit Fehlern endete (der Testhof hat ein
    # Konto ohne gültige Bankfreigabe), danach bleibt es still.
    _tageslauf()
    _tageslauf()
    assert NotionAufgabe.objects.filter(art="Frist fällig", status="offen").count() == 1
    assert Arbeitsposten.objects.filter(art="Frist fällig", status="offen").count() == faellig
    if gescheitert:
        assert len(mail.outbox) == 2
        assert "- tageslauf: 1" in mail.outbox[1].body
    else:
        assert len(mail.outbox) == 1
