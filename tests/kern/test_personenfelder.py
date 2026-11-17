"""Register der Personenfelder und Logging-Filter (Schritt 0b, Aufgabe 5)."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from typing import Any

import pytest
from django.conf import settings
from django.core.exceptions import FieldDoesNotExist
from django.db import models
from django.test.utils import isolate_apps

from apps.kern import personenfelder
from apps.kern.models import Mandant, Objekt
from apps.kern.personenfelder import ERSATZ, PersonenfelderFilter
from tests.factories import ObjektFabrik


@pytest.fixture
def register_sichern() -> Iterator[None]:
    """Stellt das Register nach dem Test wieder her (Testmodelle bleiben nicht hängen)."""
    personen = {m: set(f) for m, f in personenfelder._PERSONENFELDER.items()}
    unbedenklich = {m: set(f) for m, f in personenfelder._UNBEDENKLICH.items()}
    yield
    personenfelder._PERSONENFELDER.clear()
    personenfelder._PERSONENFELDER.update(personen)
    personenfelder._UNBEDENKLICH.clear()
    personenfelder._UNBEDENKLICH.update(unbedenklich)


def test_jedes_textfeld_ist_registriert_oder_unbedenklich() -> None:
    """Schritt 0b, Aufgabe 5 (CLAUDE.md, Regel 4.6): Jedes CharField und TextField aller
    eigenen Modelle steht im Register der Personenfelder oder ist als unbedenklich markiert.
    Das gilt auch für die Protokoll-Modelle von pghistory."""
    modelle = personenfelder.eigene_modelle()
    assert any(m._meta.label == "kern.ObjektEvent" for m in modelle)

    assert personenfelder.unmarkierte_textfelder(modelle) == []


@isolate_apps("apps.kern")
def test_registry_test_schlaegt_bei_neuem_unmarkiertem_textfeld_an(register_sichern: None) -> None:
    """Fertig-Kriterium Schritt 0b: Der Registry-Test schlägt bei einem neuen, nicht
    markierten Textfeld an (Test mit Testmodell)."""

    class Testmodell(models.Model):
        kuerzel = models.CharField(max_length=10)
        notiz = models.TextField()
        email = models.EmailField()
        anzahl = models.IntegerField()

        class Meta:
            app_label = "kern"

    personenfelder.unbedenklich(Testmodell, ["kuerzel"])
    personenfelder.register(Testmodell, ["email"])

    assert personenfelder.unmarkierte_textfelder([Testmodell]) == ["kern.Testmodell.notiz"]

    personenfelder.register(Testmodell, ["notiz"])
    assert personenfelder.unmarkierte_textfelder([Testmodell]) == []


def test_register_kennt_die_personenfelder() -> None:
    """Schritt 0b, Aufgabe 5: register() und unbedenklich() ordnen Felder ein."""
    assert personenfelder.ist_personenfeld(Objekt, "anschrift")
    assert not personenfelder.ist_personenfeld(Objekt, "kuerzel")
    assert personenfelder.personenfelder(Mandant) == {"bezeichnung"}
    # Das Protokoll-Modell erbt die Einordnung.
    assert personenfelder.ist_personenfeld(Objekt.pgh_event_model, "anschrift")  # type: ignore[attr-defined]


@isolate_apps("apps.kern")
def test_register_weist_tippfehler_und_widersprueche_ab(register_sichern: None) -> None:
    """Schritt 0b, Aufgabe 5: Unbekannte Felder und doppelte Einordnung sind Fehler."""

    class Testmodell(models.Model):
        name = models.CharField(max_length=10)

        class Meta:
            app_label = "kern"

    with pytest.raises(FieldDoesNotExist):
        personenfelder.register(Testmodell, ["nmae"])
    personenfelder.register(Testmodell, ["name"])
    with pytest.raises(ValueError):
        personenfelder.unbedenklich(Testmodell, ["name"])


def _gefiltert(msg: str, args: Any, **extra: Any) -> logging.LogRecord:
    record = logging.LogRecord("test", logging.INFO, __file__, 1, msg, args, None)
    for schluessel, wert in extra.items():
        setattr(record, schluessel, wert)
    assert PersonenfelderFilter().filter(record) is True
    return record


@pytest.mark.django_db
def test_logfilter_ersetzt_personenwerte_eines_objekts() -> None:
    """Schritt 0b, Aufgabe 5: Der Logging-Filter ersetzt registrierte Werte durch ‹…›."""
    objekt = ObjektFabrik(anschrift="Erfundene Straße 1, 12345 Testdorf")
    mandant = objekt.mandant

    record = _gefiltert(
        "Objekt %s an %s, Mandant %s (%s)",
        (objekt, objekt.anschrift, mandant, mandant.bezeichnung),
    )

    text = record.getMessage()
    assert "Erfundene Straße" not in text
    assert mandant.bezeichnung not in text
    assert text == f"Objekt {objekt.kuerzel} an {ERSATZ}, Mandant {mandant.kuerzel} ({ERSATZ})"


def test_logfilter_ersetzt_personenfelder_nach_namen() -> None:
    """Schritt 0b, Aufgabe 5: Werte unter dem Namen eines Personenfelds werden ersetzt,
    als Wörterbuch-Argument und über extra=…"""
    record = _gefiltert(
        "Anschrift %(anschrift)s, Kürzel %(kuerzel)s",
        {"anschrift": "Erfundene Straße 1", "kuerzel": "HOF"},
        hinweis="Freitext über eine Person",
    )

    assert record.getMessage() == f"Anschrift {ERSATZ}, Kürzel HOF"
    assert record.hinweis == ERSATZ


def test_logfilter_laesst_attribute_des_logrecords_stehen(register_sichern: None) -> None:
    """Heißt ein Personenfeld wie ein Attribut des LogRecord (etwa ``name``), bleibt der
    Logger-Name stehen; Personenwerte aus ``extra=…`` werden trotzdem ersetzt."""
    personenfelder._PERSONENFELDER.setdefault(Mandant, set()).update({"name", "levelname"})
    record = logging.getLogger("apps.kassenbuch.services").makeRecord(
        "apps.kassenbuch.services",
        logging.INFO,
        __file__,
        1,
        "Umsatz #%s gebucht",
        (7,),
        None,
        extra={"bezeichnung": "Erika Beispiel"},
    )

    PersonenfelderFilter().filter(record)

    assert {"name", "msg", "args", "levelname", "funcName", "taskName"} <= (
        personenfelder.LOGRECORD_ATTRIBUTE
    )
    zeile = logging.Formatter("%(levelname)s %(name)s: %(message)s [%(bezeichnung)s]").format(
        record
    )
    assert zeile == f"INFO apps.kassenbuch.services: Umsatz #7 gebucht [{ERSATZ}]"


def test_logfilter_laesst_meldungen_ohne_personendaten_stehen() -> None:
    """Schritt 0b, Aufgabe 5: Meldungen mit IDs und Zählern bleiben unverändert."""
    record = _gefiltert("Lauf #%s: %s gebucht", (12, 3))

    assert record.getMessage() == "Lauf #12: 3 gebucht"


def test_logfilter_haengt_am_konsolen_log() -> None:
    """Schritt 0b, Aufgabe 5: Der Filter ist in allen Umgebungen am Log eingehängt."""
    assert "personenfelder" in settings.LOGGING["handlers"]["console"]["filters"]
