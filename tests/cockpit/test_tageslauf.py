"""Tageslauf (Schritt 9, Aufgabe 3): Register, Reihenfolge, Abbruch eines Teils, Montag."""

from __future__ import annotations

import datetime
import io
from typing import Any

import pytest
from django.core.management import call_command, get_commands, load_command_class

from apps.cockpit import tageslauf
from apps.kern.laeufe import LaufBefehl
from apps.kern.models import Lauf
from tests.cockpit.conftest import Statusdatei

pytestmark = pytest.mark.django_db

DIENSTAG = datetime.date(2026, 11, 17)
MONTAG = datetime.date(2026, 11, 16)

#: Reihenfolge laut Bauplan, soweit die Läufe heute auf main stehen.
ERWARTET = [
    "bank_abruf",
    "kassenbuch_einordnen",
    "soll_monat",
    "abgleich",
    "fristen",
    "zaehler_stichtag",
    "verwaltio_kennzahlen",
    "id_abgleich",
    "aufgaben_abgleich",
    "statusmcp_aktualisieren",
    "sicherung_pruefen",
    "benachrichtigen",
]


@pytest.fixture
def eigenes_register(monkeypatch: pytest.MonkeyPatch) -> dict[str, tageslauf.Schritt]:
    """Eine Kopie des Registers: Was ein Test anmeldet, verschwindet danach wieder."""
    kopie = dict(tageslauf._SCHRITTE)
    monkeypatch.setattr(tageslauf, "_SCHRITTE", kopie)
    return kopie


def _tageslauf(*argumente: str) -> Lauf:
    call_command("tageslauf", *argumente, stdout=io.StringIO())
    return Lauf.objects.filter(name="tageslauf").latest("gestartet_am", "pk")


def _laeufe_nach(lauf: Lauf) -> list[str]:
    return list(Lauf.objects.filter(pk__gt=lauf.pk).order_by("pk").values_list("name", flat=True))


def test_reihenfolge_laut_bauplan() -> None:
    """Schritt 9, Aufgabe 3: Die Apps melden ihre Läufe selbst an; die Ränge ergeben die
    Reihenfolge aus dem Bauplan (Bank, Einordnen, Soll, [Abgleich, Mahnwesen], Fristen,
    Verwaltio, Cockpit, Sicherung), die Benachrichtigung zum Schluss."""
    namen = [s.name for s in tageslauf.schritte()]
    assert [n for n in namen if n in ERWARTET] == ERWARTET
    assert (
        tageslauf.BANK
        < tageslauf.EINORDNEN
        < tageslauf.SOLL
        < tageslauf.ABGLEICH
        < tageslauf.MAHNWESEN
        < tageslauf.FRISTEN
        < tageslauf.VERWALTIO
        < tageslauf.COCKPIT
        < tageslauf.SICHERUNG
        < tageslauf.BENACHRICHTIGEN
    )


def test_jeder_schritt_ist_ein_lauf() -> None:
    """Jeder angemeldete Schritt ist ein Management-Befehl mit eigenem Laufprotokoll."""
    befehle = get_commands()
    for schritt in tageslauf.schritte():
        assert schritt.befehl in befehle, schritt.name
        assert isinstance(load_command_class(befehle[schritt.befehl], schritt.befehl), LaufBefehl)


def test_tageslauf_ruft_alle_schritte_in_reihenfolge(statusdatei: Statusdatei) -> None:
    """Schritt 9, Aufgabe 3: Der Tageslauf ruft jeden Schritt als eigenen Lauf, in der
    Reihenfolge des Registers, und endet erfolgreich, wenn alle Teile erfolgreich sind."""
    statusdatei()
    lauf = _tageslauf("--stichtag", DIENSTAG.isoformat())

    assert _laeufe_nach(lauf) == ERWARTET
    assert lauf.status == Lauf.Status.ERFOLGREICH, lauf.meldung
    assert lauf.meldung.startswith(f"Stichtag 17.11.2026, {len(ERWARTET)} Schritte.")
    for name in ERWARTET:
        assert f"{name}: erfolgreich (Lauf #" in lauf.meldung


def test_abbruch_eines_teils_haelt_die_anderen_nicht_auf(
    statusdatei: Statusdatei,
    eigenes_register: dict[str, tageslauf.Schritt],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Abnahme Schritt 9 (Aufgabe 3): Bricht ein Teil ab, laufen die übrigen weiter, und
    der Fehler steht im Tageslauf mit dem Namen des Schritts. Abhängige Schritte laufen
    trotzdem, mit Vermerk. Der Text des Fehlers (er könnte Daten nennen) steht nirgends."""
    from apps.kassenbuch import services as kassenbuch

    def kaputt(**_: Any) -> None:
        raise RuntimeError("Umsatz von Erika Mustermann")

    monkeypatch.setattr(kassenbuch, "umsaetze_einordnen", kaputt)
    tageslauf.schritt_anmelden(
        "abgleich_probe",
        "kern_selbsttest",
        reihenfolge=tageslauf.ABGLEICH,
        abhaengig_von=("kassenbuch_einordnen",),
    )
    statusdatei()

    lauf = _tageslauf("--stichtag", DIENSTAG.isoformat())

    einordnen = Lauf.objects.get(name="kassenbuch_einordnen")
    assert einordnen.status == Lauf.Status.ABGEBROCHEN
    nach = _laeufe_nach(lauf)
    assert nach.index("kassenbuch_einordnen") < nach.index("kern_selbsttest")
    assert nach[-1] == "benachrichtigen"
    assert set(ERWARTET) <= set(nach)
    for name in set(ERWARTET) - {"kassenbuch_einordnen"}:
        assert Lauf.objects.get(name=name).status == Lauf.Status.ERFOLGREICH, name

    assert lauf.status == Lauf.Status.MIT_FEHLERN
    assert lauf.fehler == 1
    assert f"kassenbuch_einordnen: abgebrochen (Lauf #{einordnen.pk}, RuntimeError)" in (
        lauf.meldung
    )
    assert "gescheitert: kassenbuch_einordnen" in lauf.meldung
    assert "abgleich_probe: erfolgreich" in lauf.meldung
    assert "lief trotz Fehler bei kassenbuch_einordnen" in lauf.meldung
    assert "Mustermann" not in lauf.meldung
    assert lauf.arbeitsposten >= 1  # der Selbsttest hat einen Posten angelegt


def test_lauf_mit_fehlern_zaehlt_als_gescheitert(statusdatei: Statusdatei) -> None:
    """Endet ein Teil „mit Fehlern“ (hier: Sicherung zu alt), steht er im Tageslauf."""
    statusdatei(alter_stunden=30)
    lauf = _tageslauf("--stichtag", DIENSTAG.isoformat())
    assert lauf.status == Lauf.Status.MIT_FEHLERN
    assert "sicherung_pruefen: mit Fehlern" in lauf.meldung
    assert Lauf.objects.get(name="benachrichtigen").status == Lauf.Status.ERFOLGREICH


def test_montags_schritt_nur_am_montag(
    statusdatei: Statusdatei, eigenes_register: dict[str, tageslauf.Schritt]
) -> None:
    """Mahnwesen läuft nur montags: ``nur_wochentag=0`` lässt den Schritt sonst aus."""
    schritt = tageslauf.schritt_anmelden(
        "mahnlauf_probe", "kern_selbsttest", reihenfolge=tageslauf.MAHNWESEN, nur_wochentag=0
    )
    assert schritt.rhythmus == "Montag"
    statusdatei()

    dienstag = _tageslauf("--stichtag", DIENSTAG.isoformat())
    assert "mahnlauf_probe" not in dienstag.meldung
    assert not Lauf.objects.filter(name="kern_selbsttest").exists()

    montag = _tageslauf("--stichtag", MONTAG.isoformat())
    assert "mahnlauf_probe: erfolgreich" in montag.meldung
    assert Lauf.objects.filter(name="kern_selbsttest").count() == 1


def test_stichtag_geht_an_schritte_die_ihn_kennen(statusdatei: Statusdatei) -> None:
    """Der Stichtag des Tageslaufs gilt auch für fristen, soll_monat usw."""
    statusdatei()
    _tageslauf("--stichtag", DIENSTAG.isoformat())
    assert Lauf.objects.get(name="fristen").meldung.startswith("Stichtag 17.11.2026")
    assert Lauf.objects.get(name="soll_monat").meldung.startswith("Stichtag 17.11.2026")


def test_ohne_laesst_schritt_aus(statusdatei: Statusdatei) -> None:
    statusdatei()
    lauf = _tageslauf("--stichtag", DIENSTAG.isoformat(), "--ohne", "bank_abruf")
    assert "bank_abruf: ausgelassen" in lauf.meldung
    assert not Lauf.objects.filter(name="bank_abruf").exists()


def test_fehlender_befehl_ist_ein_fehler(
    statusdatei: Statusdatei, eigenes_register: dict[str, tageslauf.Schritt]
) -> None:
    tageslauf.schritt_anmelden("gibt_es_nicht", reihenfolge=5)
    statusdatei()
    lauf = _tageslauf("--stichtag", DIENSTAG.isoformat())
    assert lauf.status == Lauf.Status.MIT_FEHLERN
    assert "gibt_es_nicht: Befehl gibt_es_nicht fehlt" in lauf.meldung
    assert Lauf.objects.filter(name="bank_abruf").exists()


def test_register_ersetzt_und_prueft_wochentage(
    eigenes_register: dict[str, tageslauf.Schritt],
) -> None:
    tageslauf.schritt_anmelden("probe", "kern_selbsttest", reihenfolge=1)
    tageslauf.schritt_anmelden("probe", "kern_selbsttest", reihenfolge=95)
    assert [s.reihenfolge for s in tageslauf.schritte() if s.name == "probe"] == [95]
    with pytest.raises(ValueError):
        tageslauf.schritt_anmelden("probe", reihenfolge=1, nur_wochentag=7)
    tageslauf.schritt_abmelden("probe")
    assert "probe" not in {s.name for s in tageslauf.schritte()}
