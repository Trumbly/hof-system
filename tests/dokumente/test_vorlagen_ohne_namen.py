"""Vorlagen enthalten nur Platzhalter (Abnahme Schritt 3; C2; CLAUDE.md Abschnitt 1).

Der Test baut den Testhof und sammelt aus allen Personenfeldern die Wörter, die Faker
(``de_DE``) als Vor- oder Nachname oder Ort erzeugt, auch als Teil eines Straßennamens.
Fachwörter in Personenfeldern (etwa Verwendungszwecke wie „Abwasser“) sind keine Namen.
Keiner dieser Namen darf in einer Vorlage stehen: weder
in den Vorlagen der Datenbank (Testhof-Modul ``testhof/dokumente.py``), noch in den
Beispieldateien für den Notion-Import, noch im Rahmen des Briefs.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from io import StringIO
from pathlib import Path

import pytest
from django.conf import settings
from django.core.management import call_command
from faker.providers.address.de_DE import Provider as AdresseDE
from faker.providers.person.de_DE import Provider as PersonDE

from apps.dokumente import services
from apps.dokumente.models import Vorlage
from apps.kern import personenfelder
from testhof.aufbau import Testhof

#: Namen und Orte, aus denen Faker (de_DE) erfindet.
FAKER_NAMEN = frozenset(PersonDE.first_names) | frozenset(PersonDE.last_names)
FAKER_ORTE = frozenset(AdresseDE.cities)
STRASSEN_ENDUNGEN = AdresseDE.street_suffixes_short
WORT = re.compile(r"[A-Za-zÄÖÜäöüß][A-Za-zÄÖÜäöüß\-]{3,}")

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "vorlagen"
RAHMEN = Path(settings.BASE_DIR) / "templates" / "dokumente" / "pdf"


def _ist_name(wort: str) -> bool:
    if wort in FAKER_NAMEN or wort in FAKER_ORTE:
        return True
    # Straßennamen aus Nachnamen, z. B. „Müllerweg“ oder „Müllerstr“
    return any(
        wort == name + endung.rstrip(".") for name in FAKER_NAMEN for endung in STRASSEN_ENDUNGEN
    )


def namen_aus_dem_testhof() -> set[str]:
    """Erfundene Namen und Orte (ab vier Zeichen) aus den Personenfeldern der Datenbank."""
    namen: set[str] = set()
    for modell in personenfelder.eigene_modelle():
        felder = personenfelder.personenfelder(modell)
        if not felder or hasattr(modell, "pgh_tracked_model"):
            continue
        for zeile in modell._default_manager.values_list(*sorted(felder)):
            for wert in zeile:
                for wort in WORT.findall(str(wert or "")):
                    if wort[0].isupper() and _ist_name(wort):
                        namen.add(wort)
    return namen


def funde(texte: Iterable[tuple[str, str]], namen: set[str]) -> list[str]:
    """``Quelle: Wort`` für jedes Wort aus ``namen``, das wörtlich in einem Text steht."""
    ergebnis = []
    for quelle, text in texte:
        for name in sorted(namen):
            if re.search(rf"(?<![\wÄÖÜäöüß]){re.escape(name)}(?![\wÄÖÜäöüß])", text):
                ergebnis.append(f"{quelle}: {name}")
    return ergebnis


def alle_vorlagentexte() -> list[tuple[str, str]]:
    texte = [
        (f"Vorlage #{v.pk} ({v.name})", f"{v.name}\n{v.betreff}\n{v.text}")
        for v in Vorlage.objects.all()
    ]
    texte += [(f"Fixture {p.name}", p.read_text()) for p in sorted(FIXTURES.glob("*.md"))]
    texte += [(f"Rahmen {p.name}", p.read_text()) for p in sorted(RAHMEN.glob("*.html"))]
    return texte


@pytest.mark.django_db
def test_vorlagen_enthalten_keinen_namen_aus_dem_testhof(testhof: Testhof) -> None:
    """Abnahme Schritt 3: Vorlagen enthalten nur Platzhalter; ein Test schlägt an, wenn eine
    Vorlage einen Namen aus dem Testhof wörtlich enthält."""
    call_command("vorlagen_import", str(FIXTURES), "--ausfuehren", stdout=StringIO())
    namen = namen_aus_dem_testhof()
    assert len(namen) >= 5, "Der Testhof sollte erfundene Namen und Anschriften liefern."
    assert Vorlage.objects.filter(herkunft="testhof").count() >= 4

    assert funde(alle_vorlagentexte(), namen) == []


@pytest.mark.django_db
def test_der_test_schlaegt_bei_einem_namen_an(testhof: Testhof) -> None:
    """Abnahme Schritt 3: Gegenprobe – steht ein Testhof-Name in einer Vorlage, schlägt
    die Prüfung an."""
    namen = namen_aus_dem_testhof()
    name = sorted(namen)[0]
    vorlage = services.vorlage_anlegen(
        "Mitteilung mit Namen", "mitteilung", f"<p>Liebe Familie {name},</p>"
    )

    assert funde(alle_vorlagentexte(), namen) == [
        f"Vorlage #{vorlage.pk} (Mitteilung mit Namen): {name}"
    ]
