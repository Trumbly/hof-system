"""Schreibweg der Bank (CLAUDE.md, Regel 4.1).

Abruf (Enable Banking), Zulieferung vom hof-mac, Import und Testhof schreiben Umsätze
über ``umsaetze_uebernehmen``; dort entsteht der Fingerabdruck gegen Doppelte.
Meldungen und Hinweise nennen nur IDs, Daten und Zähler, nie Beträge, Namen oder Zwecke.
"""

from __future__ import annotations

import datetime
import hashlib
import itertools
import logging
import secrets
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from django.contrib.contenttypes.models import ContentType
from django.db import models, transaction
from django.db.models import Case, F, Sum, When
from django.urls import reverse
from django.utils import timezone

from apps.bank import fingerabdruck
from apps.bank.models import Bankabruf, Bankfreigabe, Bankkonto, Bankumsatz, Kontostand
from apps.kern import services as kern_services
from apps.kern import zeit
from apps.kern.models import Arbeitsposten, Lauf, Mandant

logger = logging.getLogger(__name__)

#: So viele Tage vor Ablauf einer Bankfreigabe entsteht ein Arbeitsposten (H3.2).
FREIGABE_VORLAUF_TAGE = 14
#: Ohne Abruf oder Zulieferung seit so vielen Tagen entsteht ein Arbeitsposten.
ZULIEFERUNG_SPAETESTENS_TAGE = 2

ART_SALDO = "Saldo weicht ab"
ART_FREIGABE = "Bankfreigabe läuft ab"
ART_FREIGABE_FEHLT = "Bankfreigabe fehlt"
ART_ABRUF = "Bankabruf gescheitert"
ART_ZULIEFERUNG = "Bankumsätze fehlen"

#: Grund eines gescheiterten Abrufs ohne gültige Freigabe (steht im Hinweis des Postens).
GRUND_KEINE_FREIGABE = "keine gültige Bankfreigabe"
#: Aktionen an den Posten (Review 1, Punkt 8; registriert in ``apps.bank.aktionen``): Ein
#: gescheiterter Abruf und eine fehlende Freigabe sind erst erledigt, wenn es wieder stimmt.
AKTIONEN_ABRUF = ("bank_erneut_abrufen", "bank_freigabe_erneuern", "zurueckstellen")
AKTIONEN_FREIGABE = ("bank_freigabe_erneuern", "zurueckstellen")

TOKEN_PRAEFIX = "hofz_"  # noqa: S105  (Präfix, kein Geheimnis)

CENT = Decimal("0.01")


class BankFehler(ValueError):
    """Eingaben passen nicht. Die Meldung nennt nur IDs, Feldnamen und Zähler."""


# --- Konten --------------------------------------------------------------------------


def bankkonto_anlegen(
    mandant: Mandant,
    *,
    bank: str,
    bezeichnung: str,
    iban: str,
    zugang: str,
    eb_bank_name: str = "",
    waehrung: str = "EUR",
) -> Bankkonto:
    """Legt ein eigenes Konto an. Die IBAN wird ohne Leerzeichen gespeichert."""
    if zugang not in Bankkonto.Zugang.values:
        raise BankFehler(f"Unbekannter Zugang: {zugang}")
    konto = Bankkonto(
        mandant=mandant,
        bank=bank,
        bezeichnung=bezeichnung,
        iban=fingerabdruck.iban_normal(iban),
        zugang=zugang,
        eb_bank_name=eb_bank_name,
        waehrung=waehrung,
    )
    konto.full_clean()
    konto.save()
    return konto


def bankkonto_aendern(
    konto: Bankkonto,
    *,
    bezeichnung: str | None = None,
    aktiv: bool | None = None,
    eb_bank_name: str | None = None,
) -> Bankkonto:
    if bezeichnung is not None:
        konto.bezeichnung = bezeichnung
    if aktiv is not None:
        konto.aktiv = aktiv
    if eb_bank_name is not None:
        konto.eb_bank_name = eb_bank_name
    konto.full_clean()
    konto.save()
    return konto


def _token_hash(token: str) -> str:
    # Der Token hat 256 Bit Zufall; ein einfacher SHA-256 genügt (kein Passwort).
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def zulieferung_token_erzeugen(konto: Bankkonto) -> str:
    """Erzeugt einen neuen Token für die Zulieferung dieses einen Kontos.

    Gespeichert wird nur sein Hash; der Token selbst kommt genau einmal zurück. Ein
    vorher erzeugter Token gilt danach nicht mehr.
    """
    if konto.zugang != Bankkonto.Zugang.ZULIEFERUNG:
        raise BankFehler(f"Konto #{konto.pk} hat keinen Zugang über Zulieferung.")
    token = TOKEN_PRAEFIX + secrets.token_urlsafe(32)
    konto.zulieferung_token_hash = _token_hash(token)
    konto.save(update_fields=["zulieferung_token_hash"])
    logger.info("Neuer Zulieferungs-Token für Konto #%s", konto.pk)
    return token


def konto_zum_token(token: str) -> Bankkonto | None:
    """Das aktive Zulieferungs-Konto zu einem Token, sonst None."""
    if not token.startswith(TOKEN_PRAEFIX):
        return None
    return Bankkonto.objects.filter(
        zulieferung_token_hash=_token_hash(token),
        zugang=Bankkonto.Zugang.ZULIEFERUNG,
        aktiv=True,
    ).first()


# --- Freigaben (H3.2) ----------------------------------------------------------------


def aktuelle_freigabe(konto: Bankkonto) -> Bankfreigabe | None:
    """Die Freigabe mit dem spätesten Ablauf (für Anzeige und Ablaufprüfung)."""
    return konto.freigaben.order_by("-gueltig_bis", "-pk").first()


def gueltige_freigabe(konto: Bankkonto, am: datetime.date) -> Bankfreigabe | None:
    """Die jüngste Freigabe des Kontos, die ``am`` gilt (gültig ab ≤ am ≤ gültig bis).

    „Jüngste“ heißt: zuletzt erteilt. Eine abgelaufene alte Freigabe neben einer neuen
    stört nicht, ebenso wenig eine, die erst später beginnt (Review 1, Punkt 2).
    """
    return (
        konto.freigaben.filter(gueltig_ab__lte=am, gueltig_bis__gte=am)
        .order_by("-gueltig_ab", "-angelegt_am", "-pk")
        .first()
    )


def bankfreigabe_anlegen(
    konto: Bankkonto,
    *,
    gueltig_bis: datetime.date,
    gueltig_ab: datetime.date | None = None,
    sitzung_id: str = "",
    eb_konto_uid: str | None = None,
) -> Bankfreigabe:
    """Hält eine neue Freigabe fest. Offene Posten zu älteren Freigaben sind damit erledigt."""
    gueltig_ab = gueltig_ab or zeit.heute()
    if gueltig_bis < gueltig_ab:
        raise BankFehler(f"Freigabe für Konto #{konto.pk}: gültig bis liegt vor gültig ab.")
    freigabe = Bankfreigabe(
        konto=konto, gueltig_ab=gueltig_ab, gueltig_bis=gueltig_bis, sitzung_id=sitzung_id
    )
    freigabe.full_clean()
    freigabe.save()
    if eb_konto_uid is not None and eb_konto_uid != konto.eb_konto_uid:
        konto.eb_konto_uid = eb_konto_uid
        konto.save(update_fields=["eb_konto_uid"])

    for posten in _posten_zu(ART_FREIGABE, konto.freigaben.exclude(pk=freigabe.pk), offen=True):
        kern_services.arbeitsposten_erledigen(posten)
    for posten in _posten_zu(ART_FREIGABE_FEHLT, [konto], offen=True):
        kern_services.arbeitsposten_erledigen(posten)
    # Ein Abruf, der an der fehlenden Freigabe gescheitert ist, ist mit ihr behoben. Ob
    # der nächste Abruf klappt, zeigt der nächste Lauf; scheitert er, kommt ein neuer Posten.
    for posten in _posten_zu(ART_ABRUF, [konto], offen=True):
        if GRUND_KEINE_FREIGABE in posten.hinweis:
            kern_services.arbeitsposten_erledigen(posten)
    _freigabe_frist(freigabe)
    return freigabe


def _freigabe_frist(freigabe: Bankfreigabe) -> None:
    """Frist „Bankfreigabe erneuern“ zum Ablauf der Freigabe (H9.1, Quelle Bankfreigabe).

    Die Fristen älterer Freigaben des Kontos sind damit erledigt, am Beginn der neuen
    Freigabe (höchstens heute). Ohne Vorlauf: 14 Tage vorher warnt schon der eigene Posten
    „Bankfreigabe läuft ab“ mit Link zur Erneuerung; ein Posten „Frist fällig“ entsteht
    erst, wenn die Freigabe wirklich abgelaufen ist.
    """
    from apps.fristen import services as fristen
    from apps.fristen.models import Frist

    erledigt_am = min(freigabe.gueltig_ab, zeit.heute())
    for alte in freigabe.konto.freigaben.exclude(pk=freigabe.pk):
        for frist in fristen.fristen_zu(alte, nur_offen=True).filter(
            quelle=Frist.Quelle.BANKFREIGABE
        ):
            fristen.frist_erledigen(frist, am=erledigt_am)
    fristen.frist_anlegen(
        Frist.Art.BANKFREIGABE, freigabe.gueltig_bis, Frist.Quelle.BANKFREIGABE, freigabe
    )


def freigaben_pruefen(heute: datetime.date, *, lauf: Lauf | None = None) -> list[Arbeitsposten]:
    """Legt 14 Tage vor Ablauf einer Bankfreigabe einen Arbeitsposten an (H3.2).

    Idempotent: Je Freigabe entsteht höchstens ein Posten. Erst eine neue Freigabe führt
    zu einem neuen Posten, wenn auch sie abläuft. Konten mit Enable Banking ohne jede
    Freigabe bekommen den Posten „Bankfreigabe fehlt“ (höchstens einen offenen).
    """
    angelegt: list[Arbeitsposten] = []
    grenze = heute + datetime.timedelta(days=FREIGABE_VORLAUF_TAGE)
    konten = Bankkonto.objects.filter(aktiv=True).exclude(zugang=Bankkonto.Zugang.NUR_IMPORT)
    for konto in konten.order_by("pk"):
        freigabe = aktuelle_freigabe(konto)
        if freigabe is None:
            if konto.zugang == Bankkonto.Zugang.ENABLE_BANKING and not _posten_zu(
                ART_FREIGABE_FEHLT, [konto], offen=True
            ):
                angelegt.append(
                    kern_services.arbeitsposten_anlegen(
                        ART_FREIGABE_FEHLT,
                        bezug=konto,
                        aktionen=AKTIONEN_FREIGABE,
                        faellig_am=heute,
                        hinweis=(
                            f"Für Konto #{konto.pk} gibt es keine Bankfreigabe. "
                            f"Freigeben: {freigabe_url(konto)}"
                        ),
                        lauf=lauf,
                    )
                )
            continue
        if freigabe.gueltig_bis > grenze or _posten_zu(ART_FREIGABE, [freigabe]):
            continue
        abgelaufen = freigabe.gueltig_bis < heute
        angelegt.append(
            kern_services.arbeitsposten_anlegen(
                ART_FREIGABE,
                bezug=freigabe,
                aktionen=AKTIONEN_FREIGABE,
                faellig_am=freigabe.gueltig_bis,
                hinweis=(
                    f"Die Bankfreigabe #{freigabe.pk} für Konto #{konto.pk} "
                    f"{'ist am' if abgelaufen else 'läuft am'} "
                    f"{freigabe.gueltig_bis:%d.%m.%Y} {'abgelaufen' if abgelaufen else 'ab'}. "
                    f"Erneuern: {freigabe_url(konto)}"
                ),
                lauf=lauf,
            )
        )
    return angelegt


def freigabe_url(konto: Bankkonto) -> str:
    return reverse("bank:freigabe", args=[konto.pk])


# --- Abrufe --------------------------------------------------------------------------


def bankabruf_starten(
    konto: Bankkonto,
    quelle: str,
    *,
    von: datetime.date | None = None,
    bis: datetime.date | None = None,
    lauf: Lauf | None = None,
) -> Bankabruf:
    if quelle not in Bankabruf.Quelle.values:
        raise BankFehler(f"Unbekannte Quelle: {quelle}")
    abruf = Bankabruf(konto=konto, quelle=quelle, von=von, bis=bis, lauf=lauf)
    abruf.save()
    return abruf


def bankabruf_beenden(
    abruf: Bankabruf, *, gescheitert: bool = False, meldung: str = ""
) -> Bankabruf:
    """Schließt einen Abruf. Die Meldung nennt nur IDs und Zähler."""
    abruf.status = Bankabruf.Status.GESCHEITERT if gescheitert else Bankabruf.Status.ERFOLGREICH
    abruf.beendet_am = zeit.jetzt()
    abruf.meldung = meldung
    abruf.save(update_fields=["status", "beendet_am", "meldung"])
    return abruf


@dataclass(frozen=True)
class AbrufErgebnis:
    """Was „Erneut abrufen“ ergeben hat: der Lauf und, falls es dazu kam, sein Abruf."""

    lauf: Lauf
    abruf: Bankabruf | None


def abruf_starten(konto: Bankkonto) -> AbrufErgebnis:
    """Knopf „Erneut abrufen“ (Review 2, R2-6 an der Kontoseite, R2-9 an der Kontenliste):
    ruft dieses Konto sofort ab, als Lauf ``bank_abruf --konto <ID>`` mit eigenem Eintrag im
    Laufprotokoll. Wie nachts prüft der Lauf danach Salden, Freigaben und Zulieferung.

    Nur für Konten mit Enable Banking; die übrigen liefert der hof-mac zu oder ein Import.
    Scheitert der Abruf, steht das im Abruf (``status``, ``meldung``) und als Arbeitsposten.
    """
    if konto.zugang != Bankkonto.Zugang.ENABLE_BANKING or not konto.aktiv:
        raise BankFehler(
            f"Konto #{konto.pk} ruft das Hof-System nicht selbst ab (Zugang: "
            f"{konto.get_zugang_display()}{'' if konto.aktiv else ', inaktiv'})."
        )
    lauf = kern_services.lauf_ausfuehren("bank_abruf", konto=konto.pk)
    abruf = konto.abrufe.filter(lauf=lauf).order_by("-pk").first()
    return AbrufErgebnis(lauf=lauf, abruf=abruf)


def abruf_gescheitert_melden(
    konto: Bankkonto, grund: str, *, lauf: Lauf | None = None
) -> Arbeitsposten | None:
    """Ein Posten „Bankabruf gescheitert“ je Konto, solange einer offen ist.

    ``grund`` nennt nur IDs, Statuscodes oder Fehlerarten, nie Daten der Bank.
    """
    if _posten_zu(ART_ABRUF, [konto], offen=True):
        return None
    return kern_services.arbeitsposten_anlegen(
        ART_ABRUF,
        bezug=konto,
        aktionen=AKTIONEN_ABRUF,
        faellig_am=zeit.heute(),
        hinweis=f"Der Abruf für Konto #{konto.pk} ist gescheitert: {grund}",
        lauf=lauf,
    )


def abruf_gelungen(konto: Bankkonto) -> list[Arbeitsposten]:
    """Ein erfolgreicher Abruf erledigt offene Posten „Bankabruf gescheitert“ des Kontos."""
    erledigt = []
    for posten in _posten_zu(ART_ABRUF, [konto], offen=True):
        erledigt.append(kern_services.arbeitsposten_erledigen(posten))
    return erledigt


def zulieferung_pruefen(heute: datetime.date, *, lauf: Lauf | None = None) -> list[Arbeitsposten]:
    """Meldet Konten, für die seit zwei Tagen kein erfolgreicher Abruf eingegangen ist."""
    angelegt = []
    grenze = heute - datetime.timedelta(days=ZULIEFERUNG_SPAETESTENS_TAGE)
    konten = Bankkonto.objects.filter(aktiv=True).exclude(zugang=Bankkonto.Zugang.NUR_IMPORT)
    for konto in konten.order_by("pk"):
        letzter = (
            konto.abrufe.filter(status=Bankabruf.Status.ERFOLGREICH)
            .exclude(quelle=Bankabruf.Quelle.IMPORT)
            .order_by("-gestartet_am")
            .first()
        )
        if letzter is not None and timezone.localdate(letzter.gestartet_am) >= grenze:
            continue
        if _posten_zu(ART_ZULIEFERUNG, [konto], offen=True):
            continue
        angelegt.append(
            kern_services.arbeitsposten_anlegen(
                ART_ZULIEFERUNG,
                bezug=konto,
                faellig_am=heute,
                hinweis=(
                    f"Für Konto #{konto.pk} kam seit {ZULIEFERUNG_SPAETESTENS_TAGE} Tagen "
                    "kein erfolgreicher Abruf an."
                ),
                lauf=lauf,
            )
        )
    return angelegt


# --- Umsätze -------------------------------------------------------------------------


@dataclass(frozen=True)
class UmsatzDaten:
    """Ein Umsatz, wie Abruf, Zulieferung oder Import ihn liefern (Betrag positiv)."""

    buchungsdatum: datetime.date
    betrag: Decimal
    richtung: str
    gegenpartei: str = ""
    gegen_iban: str = ""
    verwendungszweck: str = ""
    valuta: datetime.date | None = None
    bank_referenz: str = ""
    waehrung: str = "EUR"
    rohdaten: Mapping[str, Any] = field(default_factory=dict)


@dataclass
class Uebernahme:
    """Zähler einer Lieferung."""

    geliefert: int = 0
    neu: int = 0
    doppelt: int = 0
    umsaetze: list[Bankumsatz] = field(default_factory=list)


def _pruefen(daten: UmsatzDaten, nummer: int) -> None:
    if daten.richtung not in Bankumsatz.Richtung.values:
        raise BankFehler(f"Umsatz {nummer}: Richtung ungültig")
    if daten.betrag < 0:
        raise BankFehler(f"Umsatz {nummer}: Betrag muss positiv sein, Richtung extra")
    if daten.betrag != daten.betrag.quantize(CENT):
        raise BankFehler(f"Umsatz {nummer}: Betrag hat mehr als zwei Nachkommastellen")


def _zuordnen(
    konto: Bankkonto, umsaetze: Sequence[UmsatzDaten]
) -> tuple[list[tuple[UmsatzDaten, int, str]], set[str]]:
    for nummer, daten in enumerate(umsaetze, start=1):
        _pruefen(daten, nummer)
    nummern = fingerabdruck.laufende_nummern(konto.iban, umsaetze)
    zeilen = [(d, n, f) for d, (n, f) in zip(umsaetze, nummern, strict=True)]
    vorhanden = set(
        Bankumsatz.objects.filter(fingerabdruck__in=[f for _, _, f in zeilen]).values_list(
            "fingerabdruck", flat=True
        )
    )
    return zeilen, vorhanden


def umsaetze_vorschau(konto: Bankkonto, umsaetze: Sequence[UmsatzDaten]) -> Uebernahme:
    """Zählt, was ``umsaetze_uebernehmen`` täte, ohne etwas zu schreiben (Probelauf)."""
    zeilen, vorhanden = _zuordnen(konto, umsaetze)
    neu = sum(1 for _, _, f in zeilen if f not in vorhanden)
    return Uebernahme(geliefert=len(zeilen), neu=neu, doppelt=len(zeilen) - neu)


def umsaetze_uebernehmen(
    konto: Bankkonto, umsaetze: Sequence[UmsatzDaten], *, abruf: Bankabruf | None = None
) -> Uebernahme:
    """Speichert die gelieferten Umsätze, die noch nicht im System stehen.

    Eine Lieferung muss ganze Tage enthalten (siehe ``apps.bank.fingerabdruck``). Ein
    Umsatz mit bekanntem Fingerabdruck wird übersprungen und als „doppelt“ gezählt;
    der Unique-Constraint in der Datenbank sichert das zusätzlich ab.
    """
    if abruf is not None and abruf.konto_id != konto.pk:
        raise BankFehler(f"Abruf #{abruf.pk} gehört nicht zu Konto #{konto.pk}.")
    zeilen, vorhanden = _zuordnen(konto, umsaetze)
    neue = [
        Bankumsatz(
            konto=konto,
            abruf=abruf,
            betrag=daten.betrag,
            richtung=daten.richtung,
            waehrung=daten.waehrung,
            buchungsdatum=daten.buchungsdatum,
            valuta=daten.valuta,
            gegenpartei=daten.gegenpartei[:200],
            gegen_iban=fingerabdruck.iban_normal(daten.gegen_iban),
            verwendungszweck=fingerabdruck.zweck_normal(daten.verwendungszweck),
            bank_referenz=daten.bank_referenz[:100],
            rohdaten=dict(daten.rohdaten),
            laufende_nummer=nummer,
            fingerabdruck=abdruck,
        )
        for daten, nummer, abdruck in zeilen
        if abdruck not in vorhanden
    ]
    with transaction.atomic():
        angelegt = Bankumsatz.objects.bulk_create(neue, batch_size=500)
        ergebnis = Uebernahme(
            geliefert=len(zeilen),
            neu=len(angelegt),
            doppelt=len(zeilen) - len(angelegt),
            umsaetze=angelegt,
        )
        if abruf is not None:
            Bankabruf.objects.filter(pk=abruf.pk).update(
                geliefert=F("geliefert") + ergebnis.geliefert,
                neu=F("neu") + ergebnis.neu,
                doppelt=F("doppelt") + ergebnis.doppelt,
            )
            abruf.refresh_from_db(fields=["geliefert", "neu", "doppelt"])
    return ergebnis


def _vorzeichen_summe() -> Sum:
    return Sum(
        Case(
            When(richtung=Bankumsatz.Richtung.EINGANG, then=F("betrag")),
            default=-F("betrag"),
            output_field=models.DecimalField(max_digits=14, decimal_places=2),
        )
    )


# --- Kontostände und Saldo-Prüfung ---------------------------------------------------


def kontostand_erfassen(
    konto: Bankkonto,
    datum: datetime.date,
    saldo: Decimal,
    *,
    abruf: Bankabruf | None = None,
) -> Kontostand:
    """Hält den Saldo zum Ende eines Tages fest; ein späterer Abruf aktualisiert ihn."""
    stand = Kontostand.objects.filter(konto=konto, datum=datum).first()
    if stand is None:
        stand = Kontostand(konto=konto, datum=datum, saldo=saldo, abruf=abruf)
        stand.full_clean()
        stand.save()
    elif stand.saldo != saldo:
        stand.saldo = saldo
        stand.abruf = abruf
        stand.save(update_fields=["saldo", "abruf"])
    return stand


def kontostaende_erfassen(
    konto: Bankkonto,
    staende: Mapping[datetime.date, Decimal],
    *,
    abruf: Bankabruf | None = None,
) -> int:
    """Viele Tagessalden auf einmal (Testhof, Import). Vorhandene Tage werden aktualisiert."""
    vorhanden = {k.datum: k for k in konto.kontostaende.filter(datum__in=list(staende))}
    neue = [
        Kontostand(konto=konto, datum=datum, saldo=saldo, abruf=abruf)
        for datum, saldo in sorted(staende.items())
        if datum not in vorhanden
    ]
    with transaction.atomic():
        Kontostand.objects.bulk_create(neue, batch_size=500)
        for datum in vorhanden:
            kontostand_erfassen(konto, datum, staende[datum], abruf=abruf)
    return len(neue)


def kontostand_aus_saldo(
    konto: Bankkonto,
    saldo: Decimal,
    *,
    stand_am: datetime.date,
    abruf: Bankabruf | None = None,
) -> Kontostand:
    """Rechnet einen aktuellen Saldo der Bank in den Saldo zum Ende des Vortags um.

    Die Bank meldet den Saldo zum Zeitpunkt des Abrufs; er enthält schon die Umsätze,
    die am Abruftag gebucht sind. Der Tagesend-Saldo des Vortags ist deshalb
    ``saldo - Summe der Umsätze ab stand_am``. Voraussetzung: Der Abruf hat die Umsätze
    bis einschließlich ``stand_am`` gerade mitgeholt.
    """
    spaeter = konto.umsaetze.filter(buchungsdatum__gte=stand_am).aggregate(
        summe=_vorzeichen_summe()
    )["summe"] or Decimal("0")
    return kontostand_erfassen(
        konto, stand_am - datetime.timedelta(days=1), saldo - spaeter, abruf=abruf
    )


@dataclass(frozen=True)
class SaldoStand:
    """Der Saldo eines Kontos zur Anzeige (Review 1, Punkt 2).

    ``saldo`` ist der letzte Tagesend-Saldo der Bank plus die Umsätze danach, die der
    letzte erfolgreiche Abruf mitgebracht hat. ``stand`` ist der Tag, bis zu dem dieser
    Abruf reichte (höchstens der Tag des Abrufs), mindestens aber der Tag des Kontostands.
    ``abgerufen_am`` ist der Zeitpunkt des letzten erfolgreichen Abrufs (ohne Importe).
    """

    saldo: Decimal
    stand: datetime.date
    kontostand_datum: datetime.date
    abgerufen_am: datetime.datetime | None


def letzter_erfolgreicher_abruf(konto: Bankkonto) -> Bankabruf | None:
    """Der letzte erfolgreiche Abruf oder die letzte Zulieferung, ohne Importe."""
    return (
        konto.abrufe.filter(status=Bankabruf.Status.ERFOLGREICH)
        .exclude(quelle=Bankabruf.Quelle.IMPORT)
        .order_by("-gestartet_am", "-pk")
        .first()
    )


def saldo_stand(konto: Bankkonto) -> SaldoStand | None:
    """Saldo mit dem Datum des letzten erfolgreichen Abrufs; None ohne Kontostand."""
    kontostand = konto.kontostaende.order_by("-datum").first()
    if kontostand is None:
        return None
    abruf = letzter_erfolgreicher_abruf(konto)
    abgerufen_am = (abruf.beendet_am or abruf.gestartet_am) if abruf is not None else None
    stand = kontostand.datum
    # Nur ein Abruf mit bekanntem Zeitraum verschiebt den Stand über den Kontostand hinaus.
    if abruf is not None and abgerufen_am is not None and abruf.bis is not None:
        stand = max(stand, min(timezone.localdate(abgerufen_am), abruf.bis))
    danach = konto.umsaetze.filter(
        buchungsdatum__gt=kontostand.datum, buchungsdatum__lte=stand
    ).aggregate(summe=_vorzeichen_summe())["summe"] or Decimal("0")
    return SaldoStand(
        saldo=kontostand.saldo + danach,
        stand=stand,
        kontostand_datum=kontostand.datum,
        abgerufen_am=abgerufen_am,
    )


@dataclass(frozen=True)
class Saldoabweichung:
    """Saldo am ``kontostand.datum`` passt nicht zum Saldo am ``vorher`` plus Umsätzen."""

    kontostand: Kontostand
    vorher: datetime.date


def tagessummen(konto: Bankkonto) -> dict[datetime.date, Decimal]:
    """Summe der Umsätze je Buchungstag, mit Vorzeichen (Eingang plus, Ausgang minus)."""
    zeilen = (
        konto.umsaetze.values("buchungsdatum")
        .annotate(summe=_vorzeichen_summe())
        .values_list("buchungsdatum", "summe")
    )
    return {datum: summe for datum, summe in zeilen}


def saldenabweichungen(konto: Bankkonto) -> list[Saldoabweichung]:
    """Vergleicht je zwei aufeinanderfolgende Kontostände mit den Umsätzen dazwischen."""
    staende = list(konto.kontostaende.order_by("datum"))
    summen = sorted(tagessummen(konto).items())
    abweichungen = []
    zeiger = 0
    # Umsätze bis zum ersten Kontostand sind darin schon enthalten.
    while zeiger < len(summen) and staende and summen[zeiger][0] <= staende[0].datum:
        zeiger += 1
    for vorher, stand in itertools.pairwise(staende):
        erwartet = vorher.saldo
        while zeiger < len(summen) and summen[zeiger][0] <= stand.datum:
            erwartet += summen[zeiger][1]
            zeiger += 1
        if erwartet != stand.saldo:
            abweichungen.append(Saldoabweichung(kontostand=stand, vorher=vorher.datum))
    return abweichungen


def saldenabweichungen_melden(konto: Bankkonto, *, lauf: Lauf | None = None) -> list[Arbeitsposten]:
    """Legt je abweichendem Tag einen Arbeitsposten an (Konto-ID und Datum, keine Beträge).

    Idempotent: Je Kontostand höchstens ein Posten. Offene Posten zu Tagen, die inzwischen
    passen (etwa weil ein fehlender Umsatz nachgeliefert wurde), werden erledigt.
    """
    abweichungen = saldenabweichungen(konto)
    abweichend = {a.kontostand.pk for a in abweichungen}
    for posten in _posten_zu(ART_SALDO, konto.kontostaende.all(), offen=True):
        if posten.bezug_id not in abweichend:
            kern_services.arbeitsposten_erledigen(posten)

    gemeldet = {p.bezug_id for p in _posten_zu(ART_SALDO, konto.kontostaende.all())}
    angelegt = []
    for abweichung in abweichungen:
        stand = abweichung.kontostand
        if stand.pk in gemeldet:
            continue
        angelegt.append(
            kern_services.arbeitsposten_anlegen(
                ART_SALDO,
                bezug=stand,
                faellig_am=stand.datum,
                hinweis=(
                    f"Konto #{konto.pk}: Der Saldo der Bank am {stand.datum:%d.%m.%Y} passt "
                    f"nicht zum Saldo am {abweichung.vorher:%d.%m.%Y} und den Umsätzen "
                    "dazwischen. Fehlt ein Umsatz oder ist einer doppelt?"
                ),
                lauf=lauf,
            )
        )
    return angelegt


# --- Hilfen --------------------------------------------------------------------------


def _posten_zu(
    art: str,
    bezuege: Iterable[models.Model] | models.QuerySet[Any],
    *,
    offen: bool = False,
) -> list[Arbeitsposten]:
    """Arbeitsposten einer Art zu den Bezügen; mit ``offen`` nur offene und zurückgestellte."""
    if isinstance(bezuege, models.QuerySet):
        typ = ContentType.objects.get_for_model(bezuege.model)
        ids: Any = bezuege.values("pk")
    else:
        liste = list(bezuege)
        if not liste:
            return []
        typ = ContentType.objects.get_for_model(liste[0])
        ids = [b.pk for b in liste]
    posten = Arbeitsposten.objects.filter(art=art, bezug_typ=typ, bezug_id__in=ids)
    if offen:
        posten = posten.filter(
            status__in=[Arbeitsposten.Status.OFFEN, Arbeitsposten.Status.ZURUECKGESTELLT]
        )
    return list(posten)
