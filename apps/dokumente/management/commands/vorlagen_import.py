"""Import der Vorlagen-Texte aus Notion (Markdown-Export): ``manage.py vorlagen_import <pfad>``.

``<pfad>`` ist eine Markdown-Datei oder ein Ordner (alle ``*.md`` darin, ohne
Unterordner). Format: ``apps.dokumente.notion``.

- ``--probelauf`` (Standard) prüft nur und schreibt nichts.
- ``--ausfuehren`` legt neue Vorlagen als Entwurf an; gibt es den Namen schon mit anderem
  Text, entsteht eine neue Version als Entwurf. Gleicher Text: nichts.

Der Bericht nennt nur Zähler, Abweichungsarten und Vorlagen-IDs (CLAUDE.md, Abschnitt 1).
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from django.core.management.base import CommandError, CommandParser

from apps.dokumente import notion, services
from apps.dokumente.models import Vorlage
from apps.dokumente.platzhalter import PlatzhalterFehler
from apps.kern.laeufe import LaufBefehl


class Command(LaufBefehl):
    help = "Übernimmt Vorlagen-Texte (Markdown aus Notion) als Entwurf. Standard: Probelauf."
    lauf_name = "vorlagen_import"

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("pfad", help="Markdown-Datei oder Ordner mit *.md")
        modus = parser.add_mutually_exclusive_group()
        modus.add_argument(
            "--probelauf", action="store_true", help="nur prüfen, nichts schreiben (Standard)"
        )
        modus.add_argument("--ausfuehren", action="store_true", help="Vorlagen anlegen")

    def ausfuehren(self, **optionen: Any) -> str:
        pfad = Path(optionen["pfad"])
        if pfad.is_dir():
            dateien = sorted(pfad.glob("*.md"))
        elif pfad.is_file():
            dateien = [pfad]
        else:
            raise CommandError("Pfad nicht gefunden.")
        ausfuehren = bool(optionen["ausfuehren"])

        zaehler: Counter[str] = Counter()
        ids: list[int] = []
        for nummer, datei in enumerate(dateien, start=1):
            try:
                eintrag = notion.lesen(datei.read_text(encoding="utf-8"), datei.name)
                letzte = Vorlage.objects.filter(name=eintrag.name).order_by("-version").first()
                if (
                    letzte is not None
                    and letzte.text == eintrag.text
                    and letzte.betreff == eintrag.betreff
                    and letzte.art == eintrag.art
                ):
                    zaehler["unveraendert"] += 1
                    continue
                art = "neue_version" if letzte is not None else "neu"
                if ausfuehren:
                    vorlage = services.vorlage_anlegen(
                        eintrag.name,
                        eintrag.art,
                        eintrag.text,
                        betreff=eintrag.betreff,
                        herkunft="notion",
                    )
                    ids.append(vorlage.pk)
                    self.gebucht()
                else:
                    services.vorlage_pruefen(eintrag.art, eintrag.text, eintrag.betreff)
                zaehler[art] += 1
            except notion.ImportFehler as fehler:
                zaehler[f"fehler_{fehler.art}"] += 1
                self.fehler()
                self.stderr.write(f"Datei {nummer}: {fehler.art}")
            except PlatzhalterFehler as fehler:
                zaehler["fehler_platzhalter"] += 1
                self.fehler()
                # Die Meldung nennt nur Platzhalter, keine Inhalte.
                self.stderr.write(f"Datei {nummer}: platzhalter ({fehler})")

        teile = [f"{schluessel} {anzahl}" for schluessel, anzahl in sorted(zaehler.items())]
        modus = "ausgeführt" if ausfuehren else "Probelauf, nichts geschrieben"
        meldung = f"{len(dateien)} Dateien ({modus}): " + (", ".join(teile) or "nichts")
        if ids:
            meldung += f"; Vorlagen-IDs {', '.join(map(str, ids))}"
        self.stdout.write(meldung)
        return meldung
