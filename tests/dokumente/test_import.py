"""Import der Vorlagen-Texte aus Notion (Schritt 3c, Aufgabe 9)."""

from __future__ import annotations

import shutil
from io import StringIO
from pathlib import Path

import pytest
from django.core.management import call_command

from apps.dokumente import notion
from apps.dokumente.models import Vorlage
from apps.kern.models import Lauf

pytestmark = pytest.mark.django_db

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "vorlagen"


def _import(pfad: Path, *argumente: str) -> str:
    ausgabe = StringIO()
    call_command("vorlagen_import", str(pfad), *argumente, stdout=ausgabe, stderr=StringIO())
    return ausgabe.getvalue()


def test_probelauf_ist_standard_und_schreibt_nichts() -> None:
    """Schritt 3c, Aufgabe 9: Import hat --probelauf (Standard) und --ausfuehren."""
    ausgabe = _import(FIXTURES)
    assert "Probelauf" in ausgabe
    assert "neu 2" in ausgabe
    assert not Vorlage.objects.exists()
    assert Lauf.objects.get(name="vorlagen_import").status == Lauf.Status.ERFOLGREICH


def test_markdown_mit_platzhaltern_wird_entwurf() -> None:
    """Schritt 3c, Aufgabe 9: Der Befehl übernimmt Markdown mit Platzhaltern, Status Entwurf."""
    _import(FIXTURES, "--ausfuehren")

    erinnerung = Vorlage.objects.get(name="Zahlungserinnerung Garage")
    assert erinnerung.status == Vorlage.Status.ENTWURF
    assert (erinnerung.art, erinnerung.version, erinnerung.herkunft) == (
        "zahlungserinnerung",
        1,
        "notion",
    )
    assert erinnerung.betreff == "Zahlungserinnerung Vertrag {{ vertrag.nummer }}"
    assert "<strong>{{ betrag.offen|euro }}</strong>" in erinnerung.text
    assert '{{ empfaenger.anrede|default:"Guten Tag" }}' in erinnerung.text
    assert "Zuletzt bearbeitet" not in erinnerung.text  # Eigenschaft, kein Text
    aushang = Vorlage.objects.get(name="Aushang Winterdienst")
    assert aushang.art == "aushang"
    assert "<li>Bitte keine Fahrzeuge vor den Toren abstellen.</li>" in aushang.text


def test_zweiter_import_unveraendert_geaendert_wird_neue_version(tmp_path: Path) -> None:
    """Schritt 3c, Aufgabe 9: Gleicher Text bleibt, geänderter wird eine neue Version."""
    for datei in FIXTURES.glob("*.md"):
        shutil.copy(datei, tmp_path / datei.name)
    _import(tmp_path, "--ausfuehren")
    assert "unveraendert 2" in _import(tmp_path, "--ausfuehren")

    datei = next(tmp_path.glob("Aushang*.md"))
    datei.write_text(datei.read_text().replace("7 Uhr", "8 Uhr"))
    ausgabe = _import(tmp_path, "--ausfuehren")

    assert "neue_version 1" in ausgabe
    versionen = Vorlage.objects.filter(name="Aushang Winterdienst").order_by("version")
    assert [(v.version, v.status) for v in versionen] == [(1, "entwurf"), (2, "entwurf")]


def test_fehlerhafte_dateien_zaehlen_als_fehler_ohne_inhalte(tmp_path: Path) -> None:
    """Schritt 3c, Aufgabe 9: Unbekannte Platzhalter oder Art werden nicht übernommen; der
    Bericht nennt nur Zähler und Abweichungsarten (CLAUDE.md, Abschnitt 1)."""
    (tmp_path / "a.md").write_text("# Mahnung Alt\n\nArt: Mahnung\n\nHallo {{ Mieter }}!\n")
    (tmp_path / "b.md").write_text("# Sonstiges\n\nArt: Rundbrief\n\nText\n")
    (tmp_path / "c.md").write_text("# Ohne Art\n\nText\n")
    fehler = StringIO()
    ausgabe = StringIO()

    call_command("vorlagen_import", str(tmp_path), "--ausfuehren", stdout=ausgabe, stderr=fehler)

    text = ausgabe.getvalue()
    assert "fehler_platzhalter 1" in text
    assert "fehler_unbekannte_art 1" in text
    assert "fehler_ohne_art 1" in text
    assert not Vorlage.objects.exists()
    assert "Mahnung Alt" not in text + fehler.getvalue()
    assert Lauf.objects.get(name="vorlagen_import").fehler == 3


def test_name_aus_dateiname_ohne_notion_kennung() -> None:
    """Schritt 3c, Aufgabe 9: Ohne Überschrift gilt der Dateiname ohne Notion-Kennung."""
    eintrag = notion.lesen(
        "Art: Aushang\n\nText mit {{ objekt.kuerzel }}",
        "Aushang Hoftor 0123456789abcdef0123456789abcdef.md",
    )
    assert eintrag.name == "Aushang Hoftor"
    assert eintrag.text == "<p>Text mit {{ objekt.kuerzel }}</p>\n"
