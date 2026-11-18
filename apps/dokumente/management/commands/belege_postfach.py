"""Lauf ``belege_postfach`` (H6.1, Standardablauf 6): stündlich von 7 bis 21 Uhr.

Liest die ungelesenen Mails des Rechnungspostfachs (``HOF_BELEGE_IMAP_URL``) und legt jede
PDF-Anlage als Dokument der Art „Beleg“ in die Ablage. Dubletten (gleiche SHA-256) legen
nichts Neues an. Jeder neue Beleg bekommt einen Arbeitsposten „Belege zuordnen“.

Erst wenn die Ablage gespeichert ist, markiert der Lauf die Mails als gelesen oder
verschiebt sie (``HOF_BELEGE_NACH_DEM_LESEN``). Scheitert das, liest der nächste Lauf sie
noch einmal und erkennt die Dubletten.
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import CommandError

from apps.dokumente import postfach, services
from apps.kern.laeufe import LaufBefehl


class Command(LaufBefehl):
    help = (
        "Legt die PDF-Anlagen aus dem Rechnungspostfach als Belege ab (mit Dublettenprüfung) "
        "und legt je neuem Beleg einen Arbeitsposten „Belege zuordnen“ an."
    )

    client: postfach.PostfachClient | None
    erledigt: list[str]

    def handle(self, *args: Any, **optionen: Any) -> None:
        self.client = None
        self.erledigt = []
        super().handle(*args, **optionen)
        if self.client is None or not self.erledigt:
            return
        try:
            self.client.erledigen(self.erledigt)
        except postfach.PostfachFehler as fehler:
            raise CommandError(
                f"Lauf #{self.lauf.pk}: {len(self.erledigt)} Mails abgelegt, aber nicht "
                f"markiert ({fehler}). Der nächste Lauf erkennt sie als Dubletten."
            ) from fehler

    def ausfuehren(self, **optionen: Any) -> str:
        self.client = postfach.postfach_client()
        ergebnis = services.belege_aus_postfach(self.client.ungelesene(), lauf=self.lauf)
        self.gebucht(len(ergebnis.belege))
        self.koerbe.arbeitsposten += ergebnis.arbeitsposten
        self.erledigt = ergebnis.erledigt
        return (
            f"{ergebnis.mails} Mails, {len(ergebnis.belege)} neue Belege, "
            f"{ergebnis.dubletten} Dubletten, {ergebnis.ohne_pdf} ohne PDF"
        )
