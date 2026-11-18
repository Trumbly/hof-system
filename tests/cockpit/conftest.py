"""Fixtures für das Cockpit: Fake-Notion und eine Fake-Statusdatei der Sicherung."""

from __future__ import annotations

import datetime
import json
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from django.conf import LazySettings

from apps.benachrichtigung.kanaele import FakeKanal
from apps.cockpit.notion import FakeNotion

Statusdatei = Callable[..., Path]


@pytest.fixture(autouse=True)
def fake_notion() -> Iterator[type[FakeNotion]]:
    FakeNotion.zuruecksetzen()
    FakeKanal.gesendet.clear()
    yield FakeNotion
    FakeNotion.zuruecksetzen()
    FakeKanal.gesendet.clear()


@pytest.fixture
def statusdatei(tmp_path: Path, settings: LazySettings) -> Statusdatei:
    """Schreibt eine Statusdatei wie scripts/backup.sh und stellt HOF_BACKUP_STATUS darauf."""
    pfad = tmp_path / "backup-status.json"
    settings.HOF_BACKUP_STATUS = str(pfad)

    def schreiben(
        *, alter_stunden: float = 3, ergebnis: str = "ok", fehler_bei: str | None = None
    ) -> Path:
        zeit = datetime.datetime.now(datetime.UTC) - datetime.timedelta(hours=alter_stunden)
        daten = {
            "zeit": zeit.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "beginn": zeit.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "ergebnis": ergebnis,
            "snapshot_id": "a1b2c3d4" if ergebnis == "ok" else None,
            "dauer_sekunden": 42,
            "fehler_bei": fehler_bei,
        }
        pfad.write_text(json.dumps(daten), encoding="utf-8")
        return pfad

    return schreiben
