"""Migrationen passen zu den Modellen (CLAUDE.md, Regel 4.10)."""

import ast
from pathlib import Path

import pytest
from django.apps import apps as django_apps
from django.conf import settings
from django.core.management import call_command

APPS = Path(settings.BASE_DIR) / "apps"


@pytest.mark.django_db
def test_keine_fehlenden_migrationen() -> None:
    """Schritt 0b: Modelle, Protokoll-Modelle und Trigger stehen vollständig in den Migrationen."""
    call_command("makemigrations", "--check", "--dry-run", verbosity=0)


def _historien_modelle() -> set[str]:
    """Namen aller pghistory-Event-Modelle (die Tabellen der Historie)."""
    import pghistory.models

    return {
        m.__name__
        for m in django_apps.get_models()
        if issubclass(m, pghistory.models.Event) and m._meta.app_label != "pghistory"
    }


def _get_model_namen(quelle: str) -> set[str]:
    """Alle Modellnamen, die eine Migration per ``apps.get_model(...)`` holt.

    Erfasst direkte Aufrufe mit Text-Argument und Schleifen der Form
    ``for name in ("A", "B"): apps.get_model("app", name)``.
    """
    baum = ast.parse(quelle)
    namen: set[str] = set()
    schleifen: dict[str, set[str]] = {}
    for knoten in ast.walk(baum):
        if (
            isinstance(knoten, ast.For)
            and isinstance(knoten.target, ast.Name)
            and isinstance(knoten.iter, (ast.Tuple, ast.List))
        ):
            schleifen[knoten.target.id] = {
                e.value
                for e in knoten.iter.elts
                if isinstance(e, ast.Constant) and isinstance(e.value, str)
            }
    for knoten in ast.walk(baum):
        if not (
            isinstance(knoten, ast.Call)
            and isinstance(knoten.func, ast.Attribute)
            and knoten.func.attr == "get_model"
        ):
            continue
        for arg in knoten.args[1:2]:
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                namen.add(arg.value)
            elif isinstance(arg, ast.Name):
                namen |= schleifen.get(arg.id, set())
    return namen


def test_get_model_namen_selbsttest() -> None:
    """Review 3, Abschluss: Der Wächter erkennt direkte Aufrufe und Schleifen."""
    quelle = (
        "def f(apps, se):\n"
        "    apps.get_model('stammdaten', 'Partei')\n"
        "    for name in ('Vertrag', 'ParteiEvent'):\n"
        "        apps.get_model('stammdaten', name)\n"
    )
    assert _get_model_namen(quelle) == {"Partei", "Vertrag", "ParteiEvent"}


def test_keine_migration_schreibt_in_die_historie() -> None:
    """Review 3, Abschluss: Keine Datenmigration holt ein Historien-Modell.

    Die Tabellen der Historie sind append-only geschützt. Eine Migration, die dort
    Zeilen ändert, scheitert auf jeder Datenbank mit Historie (dev, prod), läuft
    aber in den Tests durch, weil die Testdatenbank beim Migrieren leer ist. So
    geschehen mit stammdaten 0005 (Anrede). Protokoll wird nie umgeschrieben
    (CLAUDE.md, Regel 4.4).
    """
    historie = _historien_modelle()
    assert "ParteiEvent" in historie
    treffer = []
    for datei in sorted(APPS.glob("*/migrations/0*.py")):
        quelle = datei.read_text(encoding="utf-8")
        if "get_model" not in quelle:
            continue
        for name in sorted(_get_model_namen(quelle) & historie):
            treffer.append(f"{datei.relative_to(APPS)}: {name}")
    assert not treffer, "Migrationen schreiben in die Historie: " + ", ".join(treffer)
