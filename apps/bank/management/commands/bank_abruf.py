"""Lauf ``bank_abruf`` (H3.1): täglich um 6 Uhr, siehe deploy/systemd/laeufe/bank_abruf.conf.

1. Für jedes aktive Konto mit Enable Banking: Umsätze abrufen (mit Überlappung zum
   letzten Abruf, Doppelte fängt der Fingerabdruck ab) und den Saldo mitlesen.
2. Saldo-Prüfung für alle Konten (auch VR, das der hof-mac zuliefert): Laufen Saldo und
   Umsätze auseinander, entsteht ein Arbeitsposten mit Konto-ID und Datum.
3. Bankfreigaben prüfen: 14 Tage vor Ablauf ein Arbeitsposten mit Link zur Erneuerung.
4. Konten ohne erfolgreichen Abruf seit zwei Tagen melden (etwa: hof-mac liefert nicht).

Ein gescheiterter Abruf eines Kontos rollt nur dessen Änderungen zurück, zählt als
Fehler und wird ein Arbeitsposten; die anderen Konten laufen weiter.
"""

from __future__ import annotations

import datetime
from typing import Any

from django.core.management.base import CommandParser
from django.db import transaction
from django.utils import timezone

from apps.bank import enablebanking, services
from apps.bank.models import Bankabruf, Bankkonto
from apps.kern.laeufe import LaufBefehl

#: So viele Tage überlappt ein Abruf mit dem letzten gespeicherten Umsatz.
UEBERLAPPUNG_TAGE = 7
#: Zeitraum des ersten Abrufs eines Kontos.
ERSTER_ABRUF_TAGE = 90


def _datum(text: str) -> datetime.date:
    return datetime.date.fromisoformat(text)


class Command(LaufBefehl):
    help = (
        "Ruft die Umsätze der Konten mit Enable Banking ab, prüft Salden und Bankfreigaben "
        "und meldet Konten ohne Abruf."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--konto", type=int, help="nur dieses Konto abrufen (ID)")
        parser.add_argument("--von", type=_datum, help="Beginn (JJJJ-MM-TT)")
        parser.add_argument("--bis", type=_datum, help="Ende (JJJJ-MM-TT), Standard heute")

    def ausfuehren(self, **optionen: Any) -> str:
        heute = timezone.localdate()
        konten = Bankkonto.objects.filter(aktiv=True, zugang=Bankkonto.Zugang.ENABLE_BANKING)
        if optionen.get("konto"):
            konten = konten.filter(pk=optionen["konto"])
        konten_liste = list(konten.order_by("pk"))

        client: enablebanking.BankClient | None = None
        meldungen = []
        for konto in konten_liste:
            try:
                if client is None:
                    client = enablebanking.client()
                with transaction.atomic():
                    abruf = self._abrufen(client, konto, heute, optionen)
                meldungen.append(f"Konto #{konto.pk}: Abruf #{abruf.pk}, {abruf.neu} neu")
            except (enablebanking.EnableBankingFehler, enablebanking.UmsatzUngueltig) as fehler:
                grund = (
                    f"Umsatz der Bank unlesbar ({fehler})"
                    if isinstance(fehler, enablebanking.UmsatzUngueltig)
                    else str(fehler)
                )
                self.fehler()
                self._gescheitert(konto, grund)
                meldungen.append(f"Konto #{konto.pk}: gescheitert")

        for konto in Bankkonto.objects.filter(aktiv=True).order_by("pk"):
            self.koerbe.arbeitsposten += len(
                services.saldenabweichungen_melden(konto, lauf=self.lauf)
            )
        self.koerbe.arbeitsposten += len(services.freigaben_pruefen(heute, lauf=self.lauf))
        self.koerbe.arbeitsposten += len(services.zulieferung_pruefen(heute, lauf=self.lauf))
        return "; ".join(meldungen) or "Kein Konto mit Enable Banking."

    def _gescheitert(self, konto: Bankkonto, grund: str) -> None:
        abruf = services.bankabruf_starten(konto, Bankabruf.Quelle.ENABLE_BANKING, lauf=self.lauf)
        services.bankabruf_beenden(abruf, gescheitert=True, meldung=grund)
        if services.abruf_gescheitert_melden(konto, grund, lauf=self.lauf) is not None:
            self.koerbe.arbeitsposten += 1

    def _abrufen(
        self,
        client: enablebanking.BankClient,
        konto: Bankkonto,
        heute: datetime.date,
        optionen: dict[str, Any],
    ) -> Bankabruf:
        freigabe = services.aktuelle_freigabe(konto)
        if freigabe is None or freigabe.gueltig_bis < heute:
            raise enablebanking.EnableBankingFehler("keine gültige Bankfreigabe")
        if not konto.eb_konto_uid:
            raise enablebanking.EnableBankingFehler("Konto-ID bei Enable Banking fehlt")

        bis = optionen.get("bis") or heute
        von = optionen.get("von")
        if von is None:
            letzter = konto.umsaetze.order_by("-buchungsdatum").first()
            von = (
                letzter.buchungsdatum - datetime.timedelta(days=UEBERLAPPUNG_TAGE)
                if letzter is not None
                else heute - datetime.timedelta(days=ERSTER_ABRUF_TAGE)
            )
        abruf = services.bankabruf_starten(
            konto, Bankabruf.Quelle.ENABLE_BANKING, von=von, bis=bis, lauf=self.lauf
        )
        umsaetze = [
            enablebanking.umsatz_aus_enablebanking(u)
            for u in client.umsaetze(konto.eb_konto_uid, von, bis)
            if enablebanking.ist_gebucht(u)
        ]
        ergebnis = services.umsaetze_uebernehmen(konto, umsaetze, abruf=abruf)
        self.gebucht(ergebnis.neu)

        saldo = enablebanking.bester_saldo(client.salden(konto.eb_konto_uid))
        # Der Saldo gilt für jetzt. Nur wenn der Abruf bis heute reicht, lässt sich daraus
        # der Saldo zum Ende des Vortags rechnen (services.kontostand_aus_saldo).
        if saldo is not None and bis >= heute:
            services.kontostand_aus_saldo(konto, saldo.betrag, stand_am=heute, abruf=abruf)
        services.bankabruf_beenden(
            abruf,
            meldung=(
                f"{ergebnis.geliefert} geliefert, {ergebnis.neu} neu, "
                f"{ergebnis.doppelt} schon vorhanden"
                + ("" if saldo is not None else "; kein Saldo")
            ),
        )
        return abruf
