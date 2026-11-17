"""Generator des Testhofs: Inhalt, Gleichheit bei gleichem Seed, Sperre in prod (Schritt 0c)."""

from __future__ import annotations

import ast
import importlib
import io
import re
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from allauth.mfa.models import Authenticator
from django.apps import apps
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import models

from apps.kern.models import Benutzer, Mandant, Objekt
from testhof import aufbau, erfunden, kern, pruefsumme, sperre
from tests.conftest import totp_code

WURZEL = Path(__file__).resolve().parents[2]


def _befehl(*argumente: str) -> str:
    ausgabe = io.StringIO()
    call_command("testhof", *argumente, stdout=ausgabe)
    return ausgabe.getvalue()


def _pruefsumme_aus(ausgabe: str) -> str:
    treffer = re.search(r"Prüfsumme: ([0-9a-f]{64})", ausgabe)
    assert treffer, ausgabe
    return treffer.group(1)


# --- Inhalt ----------------------------------------------------------------------------


@pytest.mark.django_db
def test_kern_mandanten_objekte_und_testbenutzer() -> None:
    """Schritt 0c, Aufgabe 3: vier Mandanten (GbR, Nachlass, KG-Linie, privat), drei Objekte
    mit erfundenen Anschriften und der Benutzer test@testhof.invalid mit festem TOTP."""
    hof = aufbau.aufbauen(seed=42)

    assert sorted(Mandant.objects.values_list("art", flat=True)) == sorted(Mandant.Art.values)
    assert Objekt.objects.count() == 3
    for objekt in Objekt.objects.all():
        assert objekt.anschrift.count("\n") == 1
        assert re.search(r"\n\d{5} ", objekt.anschrift)

    benutzer = Benutzer.objects.get(email="test@testhof.invalid")
    assert benutzer.check_password(kern.PASSWORT)
    totp = Authenticator.objects.get(user=benutzer, type=Authenticator.Type.TOTP)
    from allauth.mfa.totp.internal.auth import TOTP

    assert TOTP(totp).validate_code(totp_code(kern.TOTP_GEHEIMNIS))

    assert hof.module == ["kern"]
    assert hof.kern.benutzer == benutzer
    assert set(hof.kern.objekte) == {"HOF", "HALLE", "FELD"}
    assert hof.kern.objekte["HALLE"].mandant.art == Mandant.Art.KG_LINIE


@pytest.mark.django_db
def test_daten_entstehen_ueber_die_services(monkeypatch: pytest.MonkeyPatch) -> None:
    """Schritt 0c: Der Generator schreibt über apps/kern/services.py (CLAUDE.md, Regel 4.1)."""
    from apps.kern import services

    aufrufe: list[str] = []
    for name in (
        "mandant_anlegen",
        "objekt_anlegen",
        "benutzer_anlegen",
        "zweiten_faktor_einrichten",
    ):
        original = getattr(services, name)

        def mitschreiben(
            *args: Any, _name: str = name, _f: Callable[..., Any] = original, **kw: Any
        ) -> Any:
            aufrufe.append(_name)
            return _f(*args, **kw)

        monkeypatch.setattr(services, name, mitschreiben)

    aufbau.aufbauen(seed=42)

    assert aufrufe.count("mandant_anlegen") == 4
    assert aufrufe.count("objekt_anlegen") == 3
    assert aufrufe.count("benutzer_anlegen") == 1
    assert aufrufe.count("zweiten_faktor_einrichten") == 1


@pytest.mark.django_db
def test_keine_echten_kontaktdaten_in_der_datenbank() -> None:
    """Abnahme Schritt 0, Punkt 4 (Teil Daten): Alle E-Mail-Adressen des Testhofs enden auf
    .invalid."""
    aufbau.aufbauen(seed=42)

    for modell in apps.get_models():
        for feld in modell._meta.concrete_fields:
            if isinstance(feld, models.EmailField):
                for wert in modell._default_manager.values_list(feld.name, flat=True):
                    assert erfunden.ist_invalid_mail(wert), f"{modell._meta.label}.{feld.name}"


@pytest.mark.django_db
def test_zweiter_aufbau_ohne_leeren_bricht_ab() -> None:
    """Schritt 0c, Aufgabe 1: Ohne --leeren baut der Befehl nicht über einen vorhandenen Hof."""
    _befehl("--seed", "42")

    with pytest.raises(CommandError, match="--leeren"):
        _befehl("--seed", "42")
    assert Mandant.objects.count() == 4


# --- Gleichheit -----------------------------------------------------------------------


@pytest.mark.django_db
def test_gleicher_seed_ergibt_identische_tabellen() -> None:
    """Fertig-Kriterium Schritt 0c: Zwei Läufe mit demselben Seed ergeben identische Daten
    (Test über einen Hash der Tabellen)."""
    aufbau.aufbauen(seed=42)
    erste_ids = set(Mandant.objects.values_list("pk", flat=True))
    erster = pruefsumme.tabellen_hashes()

    aufbau.leeren()
    aufbau.aufbauen(seed=42)
    zweiter = pruefsumme.tabellen_hashes()

    # Die IDs sind neu vergeben; der Hash sieht nur den Inhalt.
    assert not erste_ids & set(Mandant.objects.values_list("pk", flat=True))
    assert zweiter == erster
    assert "kern.Mandant" in erster and "kern.Objekt" in erster


@pytest.mark.django_db
def test_gleicher_seed_ueber_den_befehl() -> None:
    """Fertig-Kriterium Schritt 0c: manage.py testhof --seed 42 ergibt bei jedem Lauf dieselbe
    Prüfsumme (zweiter Lauf mit --leeren)."""
    erster = _pruefsumme_aus(_befehl("--seed", "42"))
    zweiter = _pruefsumme_aus(_befehl("--seed", "42", "--leeren"))

    assert erster == zweiter


@pytest.mark.django_db
def test_anderer_seed_ergibt_andere_daten() -> None:
    """Schritt 0c, Aufgabe 1: Der Hash erkennt Unterschiede; ein anderer Seed ergibt einen
    anderen Hof."""
    aufbau.aufbauen(seed=42)
    erster = pruefsumme.tabellen_hashes()
    aufbau.leeren()
    aufbau.aufbauen(seed=7)
    zweiter = pruefsumme.tabellen_hashes()

    assert zweiter["kern.Mandant"] != erster["kern.Mandant"]
    assert zweiter["kern.Objekt"] != erster["kern.Objekt"]


@pytest.mark.django_db
def test_hash_erkennt_eine_geaenderte_zeile() -> None:
    aufbau.aufbauen(seed=42)
    vorher = pruefsumme.tabellen_hashes()

    from apps.kern import services

    services.objekt_aendern(Objekt.objects.get(kuerzel="HOF"), bezeichnung="Hofanlage neu")

    nachher = pruefsumme.tabellen_hashes()
    assert nachher["kern.Objekt"] != vorher["kern.Objekt"]
    assert nachher["kern.Mandant"] == vorher["kern.Mandant"]


def test_protokoll_tabellen_gehen_nicht_in_den_hash() -> None:
    """Schritt 0c: Protokoll-Tabellen (pghistory, Läufe, Sitzungen) sind ausgenommen."""
    labels = {m._meta.label for m in pruefsumme.verglichene_modelle()}

    assert {"kern.Mandant", "kern.Objekt", "kern.Benutzer", "mfa.Authenticator"} <= labels
    assert "kern.Lauf" not in labels
    assert "kern.MandantEvent" not in labels
    assert not any(label.startswith(("pghistory.", "sessions.", "admin.")) for label in labels)


# --- Sperre für prod ------------------------------------------------------------------


@pytest.mark.django_db
def test_generator_bricht_in_prod_ab(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fertig-Kriterium Schritt 0c: Der Generator bricht in prod mit klarer Meldung ab."""
    monkeypatch.setenv("DJANGO_ENV", "prod")

    with pytest.raises(CommandError, match=r"läuft nicht in der Produktion \(DJANGO_ENV=prod\)"):
        _befehl("--seed", "42")
    with pytest.raises(CommandError, match="Produktion"):
        _befehl("--seed", "42", "--leeren")
    with pytest.raises(sperre.TesthofGesperrt):
        aufbau.aufbauen(seed=42)
    with pytest.raises(sperre.TesthofGesperrt):
        aufbau.leeren()

    assert not Mandant.objects.exists()
    assert not Benutzer.objects.exists()


@pytest.mark.parametrize("wert", ["prod", "PROD", " prod "])
def test_sperre_erkennt_prod(monkeypatch: pytest.MonkeyPatch, wert: str) -> None:
    monkeypatch.setenv("DJANGO_ENV", wert)
    with pytest.raises(sperre.TesthofGesperrt):
        sperre.pruefen()


@pytest.mark.parametrize("wert", [None, "", "dev", "test"])
def test_sperre_laesst_dev_und_test_durch(
    monkeypatch: pytest.MonkeyPatch, wert: str | None
) -> None:
    if wert is None:
        monkeypatch.delenv("DJANGO_ENV", raising=False)
    else:
        monkeypatch.setenv("DJANGO_ENV", wert)
    sperre.pruefen()


def test_prod_einstellungen_setzen_django_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fertig-Kriterium Schritt 0c: hof/settings/prod.py setzt DJANGO_ENV=prod als Standard,
    damit der Generator auf dem Server ohne weiteres Zutun gesperrt ist."""
    # Erst setzen, dann löschen: So stellt monkeypatch nach dem Test den alten Zustand her,
    # auch wenn prod.py die Variable währenddessen neu anlegt.
    monkeypatch.setenv("DJANGO_ENV", "vorher")
    monkeypatch.delenv("DJANGO_ENV")
    monkeypatch.setenv("DJANGO_SECRET_KEY", "nur-fuer-diesen-test")
    monkeypatch.setenv("DATABASE_URL", "postgres://hof:hof@localhost:5432/hof")
    monkeypatch.setenv("EMAIL_URL", "smtp://localhost:1025")
    monkeypatch.delitem(sys.modules, "hof.settings.prod", raising=False)

    importlib.import_module("hof.settings.prod")

    assert sperre.umgebung() == "prod"
    with pytest.raises(sperre.TesthofGesperrt):
        sperre.pruefen()


def _importe_auf_modulebene(pfad: Path) -> set[str]:
    baum = ast.parse(pfad.read_text(encoding="utf-8"))
    namen: set[str] = set()
    for knoten in baum.body:
        if isinstance(knoten, ast.Import):
            namen |= {alias.name for alias in knoten.names}
        elif isinstance(knoten, ast.ImportFrom) and knoten.module:
            namen |= {f"{knoten.module}.{alias.name}" for alias in knoten.names}
    return namen


def test_sperre_laedt_ohne_faker() -> None:
    """Schritt 0c, Aufgabe 7: Die Sperre greift auch ohne Entwicklungs-Abhängigkeiten; der
    Befehl importiert den Generator erst nach der Prüfung."""
    sperre_importe = _importe_auf_modulebene(WURZEL / "testhof" / "sperre.py")
    befehl_importe = _importe_auf_modulebene(
        WURZEL / "apps" / "kern" / "management" / "commands" / "testhof.py"
    )

    assert not any("faker" in name for name in sperre_importe)
    assert "testhof.sperre" in befehl_importe
    assert not any(name.startswith(("testhof.aufbau", "testhof.kern")) for name in befehl_importe)
    assert not any("faker" in name for name in befehl_importe)


# --- Pipeline -------------------------------------------------------------------------


def test_pipeline_nutzt_den_testhof() -> None:
    """Abnahme Schritt 0, Punkt 4: Die Pipeline nutzt den Testhof (migrate, dann
    manage.py testhof --seed 42 gegen Postgres); die Datenprüfung gegen Echtdaten läuft."""
    workflow = (WURZEL / ".github/workflows/pruefen.yml").read_text(encoding="utf-8")

    assert "manage.py migrate" in workflow
    assert "manage.py testhof --seed 42" in workflow
    assert "DJANGO_SETTINGS_MODULE: hof.settings.test" in workflow
    assert workflow.index("manage.py migrate") < workflow.index("manage.py testhof --seed 42")

    precommit = (WURZEL / ".pre-commit-config.yaml").read_text(encoding="utf-8")
    assert "id: keine-echtdaten" in precommit
    assert "id: gitleaks" in precommit
