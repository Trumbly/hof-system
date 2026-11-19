"""Zählt Klicks, Seitenwechsel und Tippfelder eines Ablaufs (Review 2, B).

Regeln der Zählung (so in ``docs/klickmessung.md``):

- **Klick**: jedes ``click``, ``check`` oder ``select_option``. Eine Datei wählen zählt als
  ein Klick (der Knopf des Dateifelds). Tippen zählt nicht.
- **Seitenwechsel**: jede neue Seite im Hauptfenster nach der Startseite (auch das Neuladen
  nach dem Absenden eines Formulars). HTMX-Antworten, die nur einen Teil ersetzen, zählen
  nicht.
- **Tippfeld**: ein Feld, in das man von Hand tippen muss.

Vorbelegung zählt nicht: Steht in einer Auswahl schon der richtige Eintrag, ist kein Klick
nötig; steht in einem Feld schon der richtige Wert (oder bei ``vorbelegt_reicht`` ein
Vorschlag), ist kein Tippen nötig. Dadurch messen dieselben Skripte nach dem Umbau weniger,
ohne dass man sie ändern muss; anpassen muss man nur die Selektoren (je Ablauf an einer
Stelle, ``SELEKTOREN``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from playwright.sync_api import Frame, Locator, Page


class NichtMoeglich(Exception):
    """Die Oberfläche bietet für den Ablauf keinen Weg (z. B. Review 2, A3)."""


class Gescheitert(Exception):
    """Der Ablauf bricht an einem Fehler der Anwendung ab (Fehlermeldung, Abschluss
    verweigert)."""


class SelektorFehlt(AssertionError):
    """Ein Selektor findet nichts: Skript und Oberfläche passen nicht zusammen."""


@dataclass
class Ergebnis:
    ablauf: str
    titel: str
    stand: str = "gemessen"  # „gemessen“, „nicht möglich“, „gescheitert“
    klicks: int = 0
    seitenwechsel: int = 0
    tippfelder: int = 0
    hinweis: str = ""
    schritte: list[str] = field(default_factory=list)

    def als_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


class Messung:
    """Führt Aktionen auf ``seite`` aus und zählt sie. Gezählt wird erst ab ``beginnen()``."""

    def __init__(self, seite: Page, ergebnis: Ergebnis) -> None:
        self.seite = seite
        self.ergebnis = ergebnis
        self._laeuft = False
        seite.on("framenavigated", self._navigiert)

    # --- Rahmen ---------------------------------------------------------------------

    def beginnen(self, pfad: str) -> None:
        """Öffnet die Startseite des Ablaufs (zählt nicht) und beginnt die Zählung."""
        self.seite.goto(pfad, wait_until="networkidle")
        self._laeuft = True
        self.ergebnis.schritte.append(f"Start: {pfad}")

    def anhalten(self) -> None:
        self._laeuft = False

    def _navigiert(self, rahmen: Frame) -> None:
        if self._laeuft and rahmen == self.seite.main_frame:
            self.ergebnis.seitenwechsel += 1
            self.ergebnis.schritte.append(f"  → Seite {rahmen.url}")

    def _warten(self) -> None:
        # HTMX leitet nach der Antwort per HX-Redirect weiter: kurz warten, ob noch eine
        # Navigation beginnt, dann wieder auf Ruhe im Netz.
        self.seite.wait_for_load_state("networkidle")
        self.seite.wait_for_timeout(300)
        self.seite.wait_for_load_state("networkidle")
        fehler = self.seite.locator(".meldung-error").all_inner_texts()
        if fehler:
            raise Gescheitert("Fehlermeldung der Seite: " + " ".join(fehler))

    def _notieren(self, text: str) -> None:
        if self._laeuft:
            self.ergebnis.schritte.append(text)

    # --- Aktionen -------------------------------------------------------------------

    def gibt_es(self, selektor: str) -> bool:
        return self.seite.locator(selektor).count() > 0

    def _eins(self, selektor: str) -> Locator:
        ort = self.seite.locator(selektor)
        if ort.count() == 0:
            raise SelektorFehlt(f"Auf {self.seite.url} fehlt {selektor!r}.")
        return ort.first

    def klick(self, selektor: str) -> None:
        ort = self._eins(selektor)
        self._notieren(f"Klick {selektor}")
        if self._laeuft:
            self.ergebnis.klicks += 1
        ort.click()
        self._warten()

    def haken(self, selektor: str) -> None:
        ort = self._eins(selektor)
        if ort.is_checked():
            self._notieren(f"schon angehakt {selektor}")
            return
        self._notieren(f"Haken {selektor}")
        if self._laeuft:
            self.ergebnis.klicks += 1
        ort.check()

    def waehle(self, selektor: str, eintrag: str) -> None:
        """Wählt die erste Option, deren Text auf den regulären Ausdruck ``eintrag`` passt."""
        ort = self._eins(selektor)
        texte = [t.strip() for t in ort.locator("option").all_inner_texts()]
        muster = re.compile(eintrag)
        treffer = [i for i, t in enumerate(texte) if muster.search(t)]
        if not treffer:
            raise SelektorFehlt(f"{selektor}: keine Option passt auf {eintrag!r}.")
        if ort.evaluate("e => e.selectedIndex") == treffer[0]:
            self._notieren(f"schon gewählt {selektor} = {texte[treffer[0]]}")
            return
        self._notieren(f"Auswahl {selektor} = {texte[treffer[0]]}")
        if self._laeuft:
            self.ergebnis.klicks += 1
        ort.select_option(index=treffer[0])

    def tippe(self, selektor: str, text: str, *, vorbelegt_reicht: bool = False) -> None:
        """Tippt ``text``, wenn das Feld nicht schon passend vorbelegt ist."""
        ort = self._eins(selektor)
        jetzt = ort.input_value().strip()
        if jetzt == text or (vorbelegt_reicht and jetzt):
            self._notieren(f"vorbelegt {selektor} = {jetzt!r}")
            return
        self._notieren(f"Tippen {selektor}")
        if self._laeuft:
            self.ergebnis.tippfelder += 1
        ort.fill(text)

    def datei(self, selektor: str, pfad: str) -> None:
        """Wählt eine Datei; zählt als ein Klick (Knopf des Dateifelds)."""
        ort = self._eins(selektor)
        self._notieren(f"Datei {selektor}")
        if self._laeuft:
            self.ergebnis.klicks += 1
        ort.set_input_files(pfad)

    def text(self, selektor: str = "main") -> str:
        """Liest den Text der Seite (Lesen zählt nicht)."""
        return self._eins(selektor).inner_text()
