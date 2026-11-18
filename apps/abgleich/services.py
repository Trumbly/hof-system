"""Schreibweg des Abgleichs (Schritt 6; H1.4, H3.5 bis H3.7, H3.11; CLAUDE.md, Regel 4.1).

Der Abgleich ist der kritische Pfad. Grundsatz: lieber ein Klärfall zu viel als eine falsche
Buchung. Meldungen, Hinweise und Berichte nennen IDs, Vertragsnummern, NW-IDs, Daten und
Zähler, nie Namen, Zwecke oder Beträge je Person.

**Was der Lauf ansieht.** Gültige Buchungen im Eingang (weder Storno noch storniert) mit
einer Kategorie der Art Einnahme oder Umbuchung (Kautionen), ohne Wohnraum-Merkmal, ab dem
Beginn des Abgleichs (``HOF_ABGLEICH_BEGINN``, sonst ``HOF_SOLL_BEGINN``), die noch nicht
voll zugeordnet sind. Nie: Wohnraum, Überträge von eigenen Konten, Buchungen nach einer
Zahlerregel „nicht vertragsbezogen“. Posten: offene und teilweise bezahlte Sollposten, deren
Zeitraum am Beginn des Abgleichs oder später endet (früheres führt das alte System).

**Reihenfolge** (``entscheiden``), je Buchung:

1. Zahlerregel mit einem Vertrag: Der Betrag passt *genau* auf die ältesten offenen Posten
   → buchen.
2. Sammelzahler: Der Betrag passt genau auf die offenen Posten der Vertragsliste → aufteilen
   und buchen.
3. Jahreszahler: Der Betrag passt genau auf die offenen Posten eines Kalenderjahres → buchen.
4. Alles andere → Arbeitsposten „Zahlungen klären“ mit Vorschlag und Konfidenz, nie
   automatisch gebucht.

**„Passt genau“** heißt: centgenau, und genau ein Kandidat. Kandidaten sind die
Postengruppen aus den Stufen 1 bis 3 (älteste offene Posten bis zum Ende des Zahlungsmonats;
die Posten eines Jahres). Finden zwei Stufen verschiedene Gruppen mit genau dem Betrag, ist
das ein Klärfall („mehrere gleich gute Treffer“), keine Buchung.

**Der Zweck wird immer geprüft** (Standardablauf 2): Nennt er eine NW-ID oder Vertragsnummer,
die nicht zur Regel passt (oder Wohnraum), ist das ein Klärfall, auch wenn der Betrag stimmt.

**Über- und Teilzahlung**, nur bei einer Zahlerregel mit genau einem Vertrag und ohne
Widerspruch im Zweck:

- Überzahlung (mehr als alle offenen Posten bis zum Zahlungsmonat): Die Posten werden
  bezahlt, der Rest wird Guthaben. Der Lauf verrechnet Guthaben mit dem nächsten fälligen
  Posten, sobald dessen Zeitraum begonnen hat.
- Teilzahlung mit klarem Betrag wird gebucht, der Rest bleibt offen. Klar ist der Betrag,
  wenn er (a) nur einen Teil des ältesten Postens deckt, (b) genau dem erwarteten Betrag der
  Zahlerregel entspricht oder (c) genau der Monatsmiete einer früheren Kondition entspricht
  (Dauerauftrag mit alter Miete). Begründung: Zahler und Vertrag sind sicher, kein Posten
  wird zu viel bezahlt, und der Rest bleibt sichtbar offen (für Rückstände und Mahnwesen;
  ein Klärfall ließe den ganzen Monat offen).
- Sonst (der Betrag deckt mehr als den ältesten Posten, aber keine Postengruppe genau) ist
  der Betrag unklar: Klärfall mit Vorschlag, nicht still gebucht.

Gebuchtes wird nie geändert, nur storniert (``zuordnung_stornieren``, ``buchung_loesen``,
``handzahlung_stornieren``). Nach jeder Änderung setzt ``soll.services.zahlungsstand_setzen``
den Status der betroffenen Posten (offen, teilweise, bezahlt).
"""

from __future__ import annotations

import contextlib
import contextvars
import datetime
import logging
import re
from collections import Counter, defaultdict
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.core.files.base import File
from django.db import IntegrityError, models, transaction
from django.db.models import Exists, F, Max, OuterRef, Q, QuerySet, Subquery, Sum
from django.db.models.functions import Coalesce
from django.utils import timezone

from apps.abgleich.models import (
    Guthaben,
    Handzahlung,
    Klaerfall,
    Sammelvertrag,
    Zahlerregel,
    Zuordnung,
)
from apps.bank import fingerabdruck
from apps.bank.models import Bankkonto, Bankumsatz, Kontostand
from apps.kassenbuch import services as kassenbuch
from apps.kassenbuch.models import Buchung, Kategorie, Zuordnungsregel
from apps.kern import services as kern
from apps.kern.models import Arbeitsposten, Lauf, Objekt
from apps.soll import services as soll
from apps.soll.models import Sollposten
from apps.stammdaten import fristrechnung
from apps.stammdaten import services as stammdaten
from apps.stammdaten.models import Einheit, Kaution, Kondition, Vertrag

logger = logging.getLogger(__name__)

#: Art der Arbeitsposten; meldet sich über ``HOF_BENACHRICHTIGUNG_ARTEN``.
ART_KLAEREN = "Zahlungen klären"
#: Feste Aktionen eines Klärfalls (H3.6), registriert in ``apps.abgleich.aktionen``.
AKTIONEN_KLAEREN = (
    "zahlung_zuordnen",
    "zahlung_aufteilen",
    "zahlerregel_anlegen",
    "zurueckstellen",
    "nicht_vertragsbezogen",
)
AKTIONEN_RUECKBUCHUNG = ("rueckbuchung_bestaetigen", "keine_rueckbuchung", "zurueckstellen")
AKTIONEN_LIEFERANT = ("nicht_vertragsbezogen", "zahlung_zuordnen", "zurueckstellen")

NULL = Decimal("0.00")
CENT = Decimal("0.01")
EINGANG = Bankumsatz.Richtung.EINGANG
AUSGANG = Bankumsatz.Richtung.AUSGANG
OFFEN = (Sollposten.Status.OFFEN, Sollposten.Status.TEILWEISE)
#: Posten, die monatlich entstehen. Sie kommen erst ab ihrem Monat als Kandidat in Frage;
#: Kaution und Sonstiges jederzeit.
MONATSPOSTEN = (Sollposten.Art.MIETE, Sollposten.Art.PAUSCHALE)
#: Kategorien, deren Eingänge der Abgleich ansieht (Kautionen buchen viele als Umbuchung).
ABGLEICH_ARTEN = (Kategorie.Art.EINNAHME, Kategorie.Art.UMBUCHUNG)
#: Eingänge auf diese Kategorien (Erstattung oder Rücklastschrift eines Lieferanten) gehen
#: nie in den Mieterabgleich, sondern als Klärfall „Eingang eines Lieferanten“.
LIEFERANT_ARTEN = (Kategorie.Art.AUSGABE,)

#: Konfidenzen der Vorschläge (0 bis 1).
K_HOCH = Decimal("0.80")
K_HISTORIE = Decimal("0.70")
K_MITTEL = Decimal("0.60")
K_HALB = Decimal("0.50")
K_SCHWACH = Decimal("0.40")
K_BETRAG = Decimal("0.30")
K_GERING = Decimal("0.10")

#: NW-ID (``NW-G-0007``) und Vertragsnummer (``V-0012``) im Verwendungszweck.
NW_ID_MUSTER = re.compile(r"\bNW-([A-Z]{1,3})-(\d{1,6})\b", re.IGNORECASE)
VERTRAG_MUSTER = re.compile(r"\bV-(\d{1,6})\b", re.IGNORECASE)


class AbgleichFehler(ValueError):
    """Eingaben passen nicht. Die Meldung nennt nur IDs, Vertragsnummern und Zähler."""


def heute() -> datetime.date:
    """Das heutige Datum des Abgleichs (in Tests austauschbar)."""
    return timezone.localdate()


_BEGINN: contextvars.ContextVar[datetime.date | str | None] = contextvars.ContextVar(
    "abgleich_beginn", default="einstellung"
)


def beginn() -> datetime.date | None:
    """Erster Tag des Abgleichs: ``HOF_ABGLEICH_BEGINN``, sonst ``HOF_SOLL_BEGINN``.

    Davor liegende Zahlungen und Posten (Zeitraum endet vorher) führt das alte System; sie
    gehen weder als Kandidat noch als Rückstand ein.
    """
    wert = _BEGINN.get()
    if not isinstance(wert, str):
        return wert
    eigener: datetime.date | None = getattr(settings, "ABGLEICH_BEGINN", None)
    if eigener is not None:
        return eigener
    soll_beginn: datetime.date | None = getattr(settings, "SOLL_BEGINN", None)
    return soll_beginn


@contextlib.contextmanager
def beginn_setzen(datum: datetime.date | None) -> Iterator[None]:
    """In diesem Block gilt ``datum`` als Beginn des Abgleichs (Rückrechnung)."""
    marke = _BEGINN.set(datum)
    try:
        yield
    finally:
        _BEGINN.reset(marke)


def _geld(wert: Decimal) -> Decimal:
    if wert != wert.quantize(CENT):
        raise AbgleichFehler("Beträge haben höchstens zwei Nachkommastellen.")
    return wert.quantize(CENT)


def _prozent(konfidenz: Decimal) -> str:
    return f"{int(konfidenz * 100)} %"


# --- Zahlerregeln (H1.4) -------------------------------------------------------------


@transaction.atomic
def zahlerregel_setzen(
    regel: Zuordnungsregel,
    *,
    vertrag: Vertrag | None = None,
    vertraege: Sequence[Vertrag] = (),
    betrag: Decimal | None = None,
    nicht_vertragsbezogen: bool = False,
    herkunft: str = "",
) -> Zahlerregel:
    """Legt die Zahlerregel einer Zuordnungsregel an oder ändert sie (steht im Protokoll).

    Genau eins: ein Vertrag, eine Liste von mindestens zwei Verträgen (Sammelzahler) oder
    „nicht vertragsbezogen“.
    """
    liste = list(dict.fromkeys(vertraege))
    if vertrag is not None and len(liste) == 1 and liste[0] == vertrag:
        liste = []
    if len(liste) == 1 and vertrag is None:
        vertrag, liste = liste[0], []
    wahl = (vertrag is not None) + bool(liste) + nicht_vertragsbezogen
    if wahl != 1:
        raise AbgleichFehler(
            f"Regel #{regel.pk}: genau eins angeben: ein Vertrag, mehrere Verträge "
            "(Sammelzahler) oder „nicht vertragsbezogen“."
        )
    if regel.richtung == AUSGANG:
        raise AbgleichFehler(f"Regel #{regel.pk} gilt für Ausgänge; Zahler zahlen ein.")
    if betrag is not None:
        betrag = _geld(betrag)
        if betrag <= 0:
            raise AbgleichFehler(f"Regel #{regel.pk}: Der erwartete Betrag muss positiv sein.")
    if nicht_vertragsbezogen and betrag is not None:
        raise AbgleichFehler(f"Regel #{regel.pk}: Ohne Vertrag gibt es keinen erwarteten Betrag.")
    zahlerregel = Zahlerregel.objects.select_for_update().filter(regel=regel).first()
    if zahlerregel is None:
        zahlerregel = Zahlerregel(regel=regel)
    zahlerregel.vertrag = vertrag
    zahlerregel.betrag = betrag
    zahlerregel.nicht_vertragsbezogen = nicht_vertragsbezogen
    if herkunft:
        zahlerregel.herkunft = herkunft
    zahlerregel.full_clean()
    zahlerregel.save()
    vorhanden = {z.vertrag_id: z for z in zahlerregel.sammelzeilen.all()}
    soll_ids = {v.pk for v in liste}
    for vertrag_id, zeile in vorhanden.items():
        if vertrag_id not in soll_ids:
            zeile.delete()
    for v in liste:
        if v.pk not in vorhanden:
            Sammelvertrag(zahlerregel=zahlerregel, vertrag=v).save()
    return zahlerregel


def zahlerregel_zu(buchung: Buchung) -> Zahlerregel | None:
    """Die Zahlerregel der Zuordnungsregel, nach der die Buchung gebucht ist."""
    if buchung.regel_id is None:
        return None
    return (
        Zahlerregel.objects.filter(regel_id=buchung.regel_id, regel__aktiv=True)
        .select_related("vertrag")
        .first()
    )


# --- Posten und Stand ----------------------------------------------------------------


def posten_im_abgleich() -> QuerySet[Sollposten]:
    """Offene und teilweise bezahlte Posten, deren Zeitraum nicht vor dem Beginn endet."""
    qs = Sollposten.objects.filter(status__in=OFFEN)
    anfang = beginn()
    if anfang is not None:
        qs = qs.filter(bis__gte=anfang)
    return qs


def _reihenfolge(qs: QuerySet[Sollposten]) -> QuerySet[Sollposten]:
    return qs.order_by("faellig_am", "von", "art", "pk")


def offene_posten(vertrag: Vertrag, *, bis_monat: datetime.date | None = None) -> list[Sollposten]:
    """Offene Posten des Vertrags, älteste zuerst. Mit ``bis_monat`` nur Monatsposten, deren
    Zeitraum spätestens im Monat dieses Datums beginnt (Kaution und Sonstiges immer)."""
    qs = posten_im_abgleich().filter(vertrag=vertrag)
    if bis_monat is not None:
        qs = qs.filter(Q(von__lte=fristrechnung.monatsende(bis_monat)) | ~Q(art__in=MONATSPOSTEN))
    return list(_reihenfolge(qs))


def _summe(qs: QuerySet[Any], feld: str = "betrag") -> Decimal:
    wert = qs.aggregate(s=Sum(feld))["s"]
    return wert if wert is not None else NULL


def _gueltige_zuordnungen() -> QuerySet[Zuordnung]:
    return Zuordnung.objects.filter(storniert_am__isnull=True)


def _offene_guthaben() -> QuerySet[Guthaben]:
    return Guthaben.objects.filter(storniert_am__isnull=True, aufgeloest_am__isnull=True)


def zugeordnet(quelle: Buchung | Handzahlung) -> Decimal:
    """Was von einer Zahlung schon verteilt ist: Zuordnungen und offene Guthaben."""
    if isinstance(quelle, Buchung):
        return _summe(_gueltige_zuordnungen().filter(buchung=quelle)) + _summe(
            _offene_guthaben().filter(buchung=quelle)
        )
    return _summe(_gueltige_zuordnungen().filter(handzahlung=quelle)) + _summe(
        _offene_guthaben().filter(handzahlung=quelle)
    )


def offener_betrag(quelle: Buchung | Handzahlung) -> Decimal:
    return quelle.betrag - zugeordnet(quelle)


def guthaben_summe(vertrag: Vertrag) -> Decimal:
    return _summe(_offene_guthaben().filter(vertrag=vertrag))


def _zahlungsstand(posten: Sollposten) -> Sollposten:
    """Setzt Status und bezahlten Betrag eines Postens aus seinen gültigen Zuordnungen."""
    vorher = posten.status
    bezahlt = _summe(_gueltige_zuordnungen().filter(sollposten=posten))
    soll.zahlungsstand_setzen(posten, bezahlt)
    if posten.art == Sollposten.Art.KAUTION:
        _kaution_nachfuehren(posten, vorher_bezahlt=vorher == Sollposten.Status.BEZAHLT)
    return posten


def _kaution_nachfuehren(posten: Sollposten, *, vorher_bezahlt: bool) -> None:
    """Ein bezahlter Kautionsposten trägt den Eingang an der Kaution ein (H2.10)."""
    kaution: Kaution | None = Kaution.objects.filter(vertrag_id=posten.vertrag_id).first()
    if kaution is None or not kaution.betrag or kaution.abgerechnet_am is not None:
        return
    if posten.status == Sollposten.Status.BEZAHLT and kaution.eingang_am is None:
        letzte = (
            _gueltige_zuordnungen()
            .filter(sollposten=posten)
            .select_related("buchung__umsatz__konto")
            .order_by("-datum", "-pk")
            .first()
        )
        if letzte is None:
            return
        konto = (
            letzte.buchung.umsatz.konto.bezeichnung
            if letzte.buchung is not None
            else Handzahlung.Art(letzte.handzahlung.art).label  # type: ignore[union-attr]
        )
        stammdaten.kaution_eingang_erfassen(kaution, letzte.datum, konto[:100])
    elif posten.status != Sollposten.Status.BEZAHLT and vorher_bezahlt and kaution.eingang_am:
        stammdaten.kaution_aendern(kaution, eingang_am=None)


# --- Zuordnen ------------------------------------------------------------------------


@dataclass(frozen=True)
class Anteil:
    """Ein Teil einer Zahlung auf einen Posten."""

    posten: Sollposten
    betrag: Decimal


def verteilen(betrag: Decimal, posten: Sequence[Sollposten]) -> tuple[list[Anteil], Decimal]:
    """Verteilt einen Betrag auf die Posten in ihrer Reihenfolge (älteste zuerst).

    Rückgabe: die Anteile und der Rest, der übrig bleibt (Guthaben).
    """
    rest = betrag
    anteile = []
    for p in posten:
        if rest <= 0:
            break
        teil = min(rest, p.offen)
        if teil > 0:
            anteile.append(Anteil(p, teil))
            rest -= teil
    return anteile, rest


def _quelle_sperren(quelle: Buchung | Handzahlung) -> tuple[Buchung | Handzahlung, datetime.date]:
    if isinstance(quelle, Buchung):
        buchung = (
            Buchung.objects.select_for_update(of=("self",))
            .select_related("umsatz", "kategorie")
            .get(pk=quelle.pk)
        )
        if buchung.storno_von_id is not None or hasattr(buchung, "storniert_durch"):
            raise AbgleichFehler(f"Buchung #{buchung.pk} ist storniert oder ein Storno.")
        if buchung.richtung != EINGANG:
            raise AbgleichFehler(f"Buchung #{buchung.pk} ist kein Eingang.")
        if buchung.wohnraum or buchung.kategorie.wohnraum:
            raise AbgleichFehler(
                f"Buchung #{buchung.pk} ist Wohnraum (Verwaltio) und geht nie in den Abgleich."
            )
        return buchung, buchung.umsatz.buchungsdatum
    handzahlung = Handzahlung.objects.select_for_update().get(pk=quelle.pk)
    if handzahlung.storniert_am is not None:
        raise AbgleichFehler(f"Handzahlung #{handzahlung.pk} ist storniert.")
    return handzahlung, handzahlung.datum


def _quelle_name(quelle: Buchung | Handzahlung) -> str:
    return f"{'Buchung' if isinstance(quelle, Buchung) else 'Handzahlung'} #{quelle.pk}"


@transaction.atomic
def zuordnen(
    quelle: Buchung | Handzahlung,
    anteile: Sequence[Anteil],
    *,
    guthaben: Decimal = NULL,
    guthaben_vertrag: Vertrag | None = None,
    zahlerregel: Zahlerregel | None = None,
    klaerfall: Klaerfall | None = None,
    lauf: Lauf | None = None,
) -> list[Zuordnung]:
    """Ordnet eine Zahlung (Buchung oder Handzahlung) Posten zu; ein Rest wird Guthaben.

    Prüft unter ``select_for_update`` auf Zahlung und Posten: Die Summe je Zahlung bleibt
    höchstens ihr offener Betrag, die Summe je Posten höchstens sein offener Betrag. Die
    Datenbank prüft dasselbe noch einmal (Trigger).
    """
    quelle, datum = _quelle_sperren(quelle)
    name = _quelle_name(quelle)
    guthaben = _geld(guthaben)
    if guthaben < 0:
        raise AbgleichFehler(f"{name}: Guthaben kann nicht negativ sein.")
    if not anteile and guthaben == 0:
        raise AbgleichFehler(f"{name}: Es gibt nichts zuzuordnen.")
    je_posten: dict[int, Decimal] = defaultdict(lambda: NULL)
    for nummer, anteil in enumerate(anteile, start=1):
        betrag = _geld(anteil.betrag)
        if betrag <= 0:
            raise AbgleichFehler(f"{name}, Anteil {nummer}: Der Betrag muss positiv sein.")
        je_posten[anteil.posten.pk] += betrag
    summe = sum(je_posten.values(), NULL) + guthaben
    if summe > offener_betrag(quelle):
        raise AbgleichFehler(f"{name}: Die Anteile übersteigen den offenen Betrag der Zahlung.")
    gesperrt = {
        p.pk: p
        for p in Sollposten.objects.select_for_update()
        .select_related("vertrag")
        .filter(pk__in=sorted(je_posten))
        .order_by("pk")
    }
    for pk, betrag in je_posten.items():
        posten = gesperrt[pk]
        if posten.status == Sollposten.Status.STORNIERT:
            raise AbgleichFehler(f"Sollposten #{pk} ist storniert.")
        if betrag > posten.offen:
            raise AbgleichFehler(f"Sollposten #{pk}: Der Anteil übersteigt den offenen Betrag.")
    if isinstance(quelle, Handzahlung):
        fremd = [p.pk for p in gesperrt.values() if p.vertrag_id != quelle.vertrag_id]
        if fremd:
            raise AbgleichFehler(
                f"{name}: Posten {', '.join(f'#{i}' for i in fremd)} gehören zu einem anderen "
                "Vertrag."
            )
    neu = []
    for pk, betrag in je_posten.items():
        zuordnung = Zuordnung(
            buchung=quelle if isinstance(quelle, Buchung) else None,
            handzahlung=quelle if isinstance(quelle, Handzahlung) else None,
            sollposten=gesperrt[pk],
            betrag=betrag,
            datum=datum,
            zahlerregel=zahlerregel,
            klaerfall=klaerfall,
            lauf=lauf,
        )
        _speichern(zuordnung)
        neu.append(zuordnung)
    if guthaben > 0:
        vertrag = guthaben_vertrag
        if vertrag is None and isinstance(quelle, Handzahlung):
            vertrag = quelle.vertrag
        if vertrag is None and gesperrt:
            vertraege = {p.vertrag_id for p in gesperrt.values()}
            if len(vertraege) == 1:
                vertrag = next(iter(gesperrt.values())).vertrag
        if vertrag is None:
            raise AbgleichFehler(f"{name}: Für das Guthaben fehlt der Vertrag.")
        _speichern(
            Guthaben(
                vertrag=vertrag,
                betrag=guthaben,
                buchung=quelle if isinstance(quelle, Buchung) else None,
                handzahlung=quelle if isinstance(quelle, Handzahlung) else None,
            )
        )
    for posten in gesperrt.values():
        _zahlungsstand(posten)
    return neu


def _speichern(objekt: models.Model) -> None:
    try:
        with transaction.atomic():
            objekt.save()
    except IntegrityError as fehler:
        # Trigger oder Bedingung der Datenbank: Summe überschritten o. Ä.
        raise AbgleichFehler(
            f"{type(objekt)._meta.verbose_name}: Die Datenbank lehnt die Zuordnung ab "
            "(Summe überschritten)."
        ) from fehler


@transaction.atomic
def zuordnung_stornieren(zuordnung: Zuordnung, grund: str) -> Zuordnung:
    """Storniert eine Zuordnung; der Posten ist danach wieder (teilweise) offen. Stammte sie
    aus einem Guthaben, entsteht das Guthaben neu."""
    zuordnung = Zuordnung.objects.select_for_update().get(pk=zuordnung.pk)
    if zuordnung.storniert_am is not None:
        raise AbgleichFehler(f"Zuordnung #{zuordnung.pk} ist schon storniert.")
    grund = grund.strip()
    if not grund:
        raise AbgleichFehler(f"Zuordnung #{zuordnung.pk}: Ein Storno braucht einen Grund.")
    _stornieren(zuordnung, grund)
    if zuordnung.guthaben_id is not None:
        alt = Guthaben.objects.get(pk=zuordnung.guthaben_id)
        _speichern(
            Guthaben(
                vertrag_id=alt.vertrag_id,
                betrag=zuordnung.betrag,
                buchung_id=zuordnung.buchung_id,
                handzahlung_id=zuordnung.handzahlung_id,
                rest_von=alt,
            )
        )
    _zahlungsstand(Sollposten.objects.select_for_update().get(pk=zuordnung.sollposten_id))
    return zuordnung


def _stornieren(objekt: Zuordnung | Guthaben | Handzahlung, grund: str) -> None:
    objekt.storniert_am = heute()
    objekt.storno_grund = grund[:200]
    objekt.save(update_fields=["storniert_am", "storno_grund"])


@transaction.atomic
def buchung_loesen(buchung: Buchung, grund: str) -> int:
    """Storniert alle Zuordnungen und offenen Guthaben einer Buchung (z. B. nach Storno im
    Kassenbuch oder einer Rückbuchung). Gibt die Zahl der stornierten Zuordnungen zurück."""
    posten_ids = set()
    zuordnungen = list(
        _gueltige_zuordnungen().select_for_update().filter(buchung=buchung).order_by("pk")
    )
    for z in zuordnungen:
        _stornieren(z, grund)
        posten_ids.add(z.sollposten_id)
    for g in _offene_guthaben().select_for_update().filter(buchung=buchung).order_by("pk"):
        _stornieren(g, grund)
    for posten in Sollposten.objects.select_for_update().filter(pk__in=posten_ids).order_by("pk"):
        _zahlungsstand(posten)
    return len(zuordnungen)


def stornierte_buchungen_loesen() -> int:
    """Zuordnungen zu Buchungen, die das Kassenbuch inzwischen storniert hat, werden storniert
    (das Kassenbuch kennt den Abgleich nicht). Die neue Buchung des Umsatzes geht neu in den
    Abgleich."""
    betroffen = (
        Buchung.objects.filter(storniert_durch__isnull=False)
        .filter(
            Q(zuordnungen__storniert_am__isnull=True)
            | Q(guthaben__storniert_am__isnull=True, guthaben__aufgeloest_am__isnull=True)
        )
        .distinct()
        .order_by("pk")
    )
    anzahl = 0
    for buchung in betroffen:
        buchung_loesen(buchung, f"Buchung #{buchung.pk} im Kassenbuch storniert")
        anzahl += 1
    return anzahl


# --- Guthaben ------------------------------------------------------------------------


def _guthaben_aufloesen(
    guthaben: Guthaben,
    *,
    am: datetime.date,
    verrechnet_mit: Sollposten | None = None,
    erstattet_durch: Handzahlung | None = None,
    rest: Decimal = NULL,
) -> None:
    guthaben.aufgeloest_am = am
    guthaben.verrechnet_mit = verrechnet_mit
    guthaben.erstattet_durch = erstattet_durch
    guthaben.save(update_fields=["aufgeloest_am", "verrechnet_mit", "erstattet_durch"])
    if rest > 0:
        _speichern(
            Guthaben(
                vertrag_id=guthaben.vertrag_id,
                betrag=rest,
                buchung_id=guthaben.buchung_id,
                handzahlung_id=guthaben.handzahlung_id,
                rest_von=guthaben,
            )
        )


def _zahlungsdatum(guthaben: Guthaben) -> datetime.date:
    if guthaben.buchung_id is not None:
        datum: datetime.date = (
            Buchung.objects.select_related("umsatz")
            .get(pk=guthaben.buchung_id)
            .umsatz.buchungsdatum
        )
        return datum
    return Handzahlung.objects.get(guthaben=guthaben).datum


@transaction.atomic
def guthaben_verrechnen(
    *,
    stichtag: datetime.date,
    vertraege: Iterable[Vertrag] | None = None,
    lauf: Lauf | None = None,
) -> int:
    """Verrechnet offene Guthaben mit den nächsten fälligen Posten ihres Vertrags, deren
    Zeitraum bis zum Stichtag begonnen hat (älteste zuerst). Ein Rest bleibt Guthaben.

    Gibt die Zahl der verrechneten Guthaben zurück.
    """
    offene = _offene_guthaben().select_related("vertrag").order_by("pk")
    if vertraege is not None:
        offene = offene.filter(vertrag__in=list(vertraege))
    anzahl = 0
    for guthaben in offene:
        posten = [p for p in offene_posten(guthaben.vertrag) if p.von <= stichtag]
        if not posten:
            continue
        guthaben = Guthaben.objects.select_for_update().get(pk=guthaben.pk)
        if not guthaben.offen:
            continue
        gesperrt = list(
            Sollposten.objects.select_for_update().filter(pk__in=[p.pk for p in posten])
        )
        reihe = sorted(gesperrt, key=lambda p: (p.faellig_am, p.von, p.art, p.pk))
        anteile, rest = verteilen(guthaben.betrag, reihe)
        if not anteile:
            continue
        _guthaben_aufloesen(guthaben, am=stichtag, verrechnet_mit=anteile[-1].posten, rest=rest)
        datum = _zahlungsdatum(guthaben)
        for anteil in anteile:
            _speichern(
                Zuordnung(
                    buchung_id=guthaben.buchung_id,
                    handzahlung_id=guthaben.handzahlung_id,
                    sollposten=anteil.posten,
                    betrag=anteil.betrag,
                    datum=datum,
                    guthaben=guthaben,
                    lauf=lauf,
                )
            )
        for anteil in anteile:
            _zahlungsstand(anteil.posten)
        anzahl += 1
    return anzahl


# --- Handzahlungen (H3.7) ------------------------------------------------------------


@transaction.atomic
def handzahlung_erfassen(
    vertrag: Vertrag,
    art: str,
    *,
    datum: datetime.date,
    betrag: Decimal,
    grund: str,
    beleg: File[Any],
    anteile: Sequence[Anteil] | None = None,
) -> Handzahlung:
    """Erfasst eine Zahlung ohne Bankumsatz. Grund und Beleg sind Pflicht.

    - Barzahlung, Zahlung auf ein fremdes Konto: wie eine Bankzahlung auf die offenen Posten
      des Vertrags (ohne ``anteile`` älteste zuerst); ein Rest wird Guthaben.
    - Erstattung: zahlt ein Guthaben aus (die Überweisung machst du selbst in Vivid). Der
      Betrag darf das offene Guthaben des Vertrags nicht übersteigen.
    """
    if art not in Handzahlung.Art.values:
        raise AbgleichFehler(f"Unbekannte Art der Handzahlung: {art}")
    betrag = _geld(betrag)
    if betrag <= 0:
        raise AbgleichFehler(f"Vertrag {vertrag.nummer}: Der Betrag muss positiv sein.")
    grund = grund.strip()
    if not grund:
        raise AbgleichFehler(f"Vertrag {vertrag.nummer}: Eine Handzahlung braucht einen Grund.")
    if not beleg:
        raise AbgleichFehler(f"Vertrag {vertrag.nummer}: Eine Handzahlung braucht einen Beleg.")
    if art == Handzahlung.Art.ERSTATTUNG and betrag > guthaben_summe(vertrag):
        raise AbgleichFehler(
            f"Vertrag {vertrag.nummer}: Die Erstattung übersteigt das offene Guthaben."
        )
    handzahlung = Handzahlung(
        art=art, vertrag=vertrag, datum=datum, betrag=betrag, grund=grund, beleg=beleg
    )
    handzahlung.full_clean()
    handzahlung.save()
    if art == Handzahlung.Art.ERSTATTUNG:
        _erstatten(handzahlung)
        return handzahlung
    if anteile is None:
        anteile, rest = verteilen(betrag, offene_posten(vertrag))
    else:
        rest = betrag - sum((a.betrag for a in anteile), NULL)
    zuordnen(handzahlung, anteile, guthaben=rest, guthaben_vertrag=vertrag)
    return handzahlung


def _erstatten(handzahlung: Handzahlung) -> None:
    rest = handzahlung.betrag
    for guthaben in (
        _offene_guthaben().select_for_update().filter(vertrag=handzahlung.vertrag).order_by("pk")
    ):
        if rest <= 0:
            break
        teil = min(rest, guthaben.betrag)
        _guthaben_aufloesen(
            guthaben, am=handzahlung.datum, erstattet_durch=handzahlung, rest=guthaben.betrag - teil
        )
        rest -= teil


@transaction.atomic
def handzahlung_stornieren(handzahlung: Handzahlung, grund: str) -> Handzahlung:
    """Storniert eine Handzahlung mit ihren Zuordnungen und Guthaben. Eine stornierte
    Erstattung gibt das erstattete Guthaben zurück."""
    handzahlung = Handzahlung.objects.select_for_update().get(pk=handzahlung.pk)
    if handzahlung.storniert_am is not None:
        raise AbgleichFehler(f"Handzahlung #{handzahlung.pk} ist schon storniert.")
    grund = grund.strip()
    if not grund:
        raise AbgleichFehler(f"Handzahlung #{handzahlung.pk}: Ein Storno braucht einen Grund.")
    posten_ids = set()
    for z in _gueltige_zuordnungen().select_for_update().filter(handzahlung=handzahlung):
        _stornieren(z, grund)
        posten_ids.add(z.sollposten_id)
    for g in _offene_guthaben().select_for_update().filter(handzahlung=handzahlung):
        _stornieren(g, grund)
    for g in Guthaben.objects.filter(erstattet_durch=handzahlung).order_by("pk"):
        erstattet = g.betrag - _summe(g.reste.all())
        if erstattet > 0:
            _speichern(
                Guthaben(
                    vertrag_id=g.vertrag_id,
                    betrag=erstattet,
                    buchung_id=g.buchung_id,
                    handzahlung_id=g.handzahlung_id,
                    rest_von=g,
                )
            )
    _stornieren(handzahlung, grund)
    for posten in Sollposten.objects.select_for_update().filter(pk__in=posten_ids).order_by("pk"):
        _zahlungsstand(posten)
    return handzahlung


# --- Kaution über die Bank (Standardablauf 1, Schritt 6) -----------------------------


def kautionsposten_stellen(*, stichtag: datetime.date) -> list[Sollposten]:
    """Stellt die vereinbarte, noch nicht eingegangene Kaution als Sollposten, damit der
    Abgleich ihren Eingang über die Bank erkennt.

    Nur für Entwürfe und für aktive Verträge, die am oder nach dem Beginn des Abgleichs
    beginnen: Bei älteren Verträgen ist ein fehlender Eingang eine Lücke im Datenstand, kein
    Rückstand.
    """
    anfang = beginn()
    vertraege = (
        Vertrag.objects.filter(
            status__in=(Vertrag.Status.ENTWURF, Vertrag.Status.AKTIV),
            kaution__betrag__gt=0,
            kaution__eingang_am__isnull=True,
            kaution__abgerechnet_am__isnull=True,
        )
        .exclude(
            Exists(
                Sollposten.objects.filter(
                    vertrag=OuterRef("pk"), art=Sollposten.Art.KAUTION, status__in=soll.GELTEND
                )
            )
        )
        .select_related("kaution")
        .order_by("nummer")
    )
    neu = []
    for vertrag in vertraege:
        if vertrag.status == Vertrag.Status.AKTIV and (anfang is None or vertrag.beginn < anfang):
            continue
        betrag: Decimal = vertrag.kaution.betrag  # type: ignore[assignment]
        neu.append(
            soll.sollposten_anlegen(
                vertrag,
                Sollposten.Art.KAUTION,
                von=min(stichtag, vertrag.beginn),
                netto=betrag,
                faellig_am=vertrag.beginn,
                bezeichnung="Kaution",
            )
        )
    return neu


# --- Verwendungszweck ----------------------------------------------------------------


@dataclass(frozen=True)
class Nennung:
    """Eine NW-ID oder Vertragsnummer im Verwendungszweck und die Verträge dazu."""

    kennung: str
    vertraege: frozenset[int]
    wohnraum: bool = False


def zweck_nennungen(zweck: str, datum: datetime.date) -> list[Nennung]:
    """Findet NW-IDs und Vertragsnummern im Zweck. Zu einer NW-ID gehören die Verträge der
    Einheit, die am Zahlungstag laufen (sonst alle Verträge der Einheit)."""
    nennungen: list[Nennung] = []
    for treffer in NW_ID_MUSTER.finditer(zweck or ""):
        kennung = f"NW-{treffer.group(1).upper()}-{int(treffer.group(2)):04d}"
        einheit = Einheit.objects.filter(nw_id__iexact=kennung).first()
        if einheit is None:
            nennungen.append(Nennung(kennung, frozenset()))
            continue
        alle = list(einheit.vertraege.exclude(status=Vertrag.Status.ENTWURF).order_by("nummer"))
        laufend = [v for v in alle if v.beginn <= datum and (v.ende is None or v.ende >= datum)]
        ids = frozenset(v.pk for v in (laufend or alle))
        nennungen.append(Nennung(kennung, ids, wohnraum=einheit.ist_wohnraum))
    for treffer in VERTRAG_MUSTER.finditer(zweck or ""):
        kennung = f"V-{int(treffer.group(1)):04d}"
        vertrag = Vertrag.objects.filter(nummer__iexact=kennung).first()
        nennungen.append(Nennung(kennung, frozenset([vertrag.pk]) if vertrag else frozenset()))
    return nennungen


def _widerspruch(nennungen: Sequence[Nennung], vertrag_ids: set[int]) -> list[Nennung]:
    """Nennungen, die nicht zu den Verträgen der Regel passen (oder Wohnraum sind)."""
    return [n for n in nennungen if n.wohnraum or not (n.vertraege & vertrag_ids)]


# --- Entscheidung je Buchung ---------------------------------------------------------


@dataclass(frozen=True)
class Entscheidung:
    """Was mit einer Buchung geschieht: buchen (``anteile``, ``guthaben``) oder Klärfall."""

    buchen: bool
    stufe: str = ""
    anteile: tuple[Anteil, ...] = ()
    guthaben: Decimal = NULL
    guthaben_vertrag: Vertrag | None = None
    zahlerregel: Zahlerregel | None = None
    # Klärfall
    art: str = ""
    grund: str = ""
    vorschlag_vertrag: Vertrag | None = None
    vorschlag_anteile: tuple[Anteil, ...] = ()
    vorschlag_guthaben: Decimal = NULL
    alternativen: tuple[int, ...] = ()
    konfidenz: Decimal = NULL

    def vorschlag(self) -> dict[str, Any]:
        """Vorschlag als JSON: IDs und Beträge (nur in der Datenbank, nie im Log)."""
        if self.vorschlag_vertrag is None and not self.vorschlag_anteile:
            return {}
        daten: dict[str, Any] = {
            "posten": [[a.posten.pk, str(a.betrag)] for a in self.vorschlag_anteile],
            "guthaben": str(self.vorschlag_guthaben),
        }
        if self.vorschlag_vertrag is not None:
            daten["vertrag"] = self.vorschlag_vertrag.pk
            daten["vertrag_nummer"] = self.vorschlag_vertrag.nummer
        if self.alternativen:
            daten["alternativen"] = list(self.alternativen)
        return daten


def _buchen(stufe: str, anteile: Sequence[Anteil], **weiter: Any) -> Entscheidung:
    return Entscheidung(buchen=True, stufe=stufe, anteile=tuple(anteile), **weiter)


def _klaeren(art: str, grund: str, konfidenz: Decimal, **weiter: Any) -> Entscheidung:
    return Entscheidung(buchen=False, art=art, grund=grund, konfidenz=konfidenz, **weiter)


def _ids(posten: Iterable[Sollposten]) -> frozenset[int]:
    return frozenset(p.pk for p in posten)


def _praefix(betrag: Decimal, posten: Sequence[Sollposten]) -> list[Sollposten] | None:
    """Die ältesten Posten, deren offene Summe genau den Betrag ergibt (Stufe 1)."""
    summe = NULL
    for k, p in enumerate(posten, start=1):
        summe += p.offen
        if summe == betrag:
            return list(posten[:k])
        if summe > betrag:
            return None
    return None


def _jahre(betrag: Decimal, posten: Sequence[Sollposten]) -> list[list[Sollposten]]:
    """Kalenderjahre, deren offene Posten zusammen genau den Betrag ergeben (Stufe 3)."""
    je_jahr: dict[int, list[Sollposten]] = defaultdict(list)
    for p in posten:
        if p.art in MONATSPOSTEN:
            je_jahr[p.von.year].append(p)
    return [g for _, g in sorted(je_jahr.items()) if sum((p.offen for p in g), NULL) == betrag]


def _fruehere_monatsbetraege(vertrag: Vertrag, datum: datetime.date) -> set[Decimal]:
    """Monatsbeträge (Miete und Pauschale mit Steuer) der Konditionen, die vor dem Tag
    endeten: Daran erkennt der Abgleich einen Dauerauftrag mit alter Miete."""
    betraege = set()
    for k in Kondition.objects.filter(vertrag=vertrag, gueltig_bis__lt=datum):
        netto = k.miete + k.pauschale
        betraege.add((netto + netto * k.steuersatz / 100).quantize(CENT))
    return betraege


def _exakt(betrag: Decimal, vertrag: Vertrag, datum: datetime.date) -> list[Sollposten] | None:
    return _praefix(betrag, offene_posten(vertrag, bis_monat=datum))


def _vertrag_text(vertrag: Vertrag) -> str:
    nw = ", ".join(e.nw_id for e in vertrag.einheiten.all())
    return f"{vertrag.nummer} ({nw})" if nw else vertrag.nummer


def _einzel(
    betrag: Decimal, regel: Zahlerregel, vertrag: Vertrag, datum: datetime.date
) -> Entscheidung:
    """Stufen 1 und 3 für eine Zahlerregel mit genau einem Vertrag, dazu Über- und
    Teilzahlung."""
    kandidaten = offene_posten(vertrag, bis_monat=datum)
    gruppen: dict[frozenset[int], tuple[str, list[Sollposten]]] = {}
    praefix = _praefix(betrag, kandidaten)
    if praefix:
        gruppen[_ids(praefix)] = ("regel", praefix)
    for gruppe in _jahre(betrag, offene_posten(vertrag)):
        gruppen.setdefault(_ids(gruppe), ("jahr", gruppe))
    vorschlag, rest = verteilen(betrag, kandidaten)
    if len(gruppen) == 1:
        stufe, posten = next(iter(gruppen.values()))
        return _buchen(stufe, [Anteil(p, p.offen) for p in posten], zahlerregel=regel)
    if len(gruppen) > 1:
        texte = "; ".join(
            ", ".join(f"#{i}" for i in sorted(ids)) for ids in sorted(gruppen, key=sorted)
        )
        return _klaeren(
            Klaerfall.Art.MEHRDEUTIG,
            f"Der Betrag passt genau auf {len(gruppen)} verschiedene Postengruppen von Vertrag "
            f"{_vertrag_text(vertrag)} (Posten {texte}).",
            K_HALB,
            vorschlag_vertrag=vertrag,
            vorschlag_anteile=tuple(vorschlag),
            vorschlag_guthaben=rest,
            zahlerregel=regel,
        )
    if not kandidaten:
        return _klaeren(
            Klaerfall.Art.KEIN_POSTEN,
            f"Vertrag {_vertrag_text(vertrag)} hat keinen offenen Posten bis zum Zahlungsmonat.",
            K_HALB,
            vorschlag_vertrag=vertrag,
            vorschlag_guthaben=betrag,
            zahlerregel=regel,
        )
    summe = sum((p.offen for p in kandidaten), NULL)
    if betrag > summe:
        return _buchen(
            "ueberzahlung",
            [Anteil(p, p.offen) for p in kandidaten],
            guthaben=betrag - summe,
            guthaben_vertrag=vertrag,
            zahlerregel=regel,
        )
    klar = (
        betrag < kandidaten[0].offen
        or (regel.betrag is not None and betrag == regel.betrag)
        or betrag in _fruehere_monatsbetraege(vertrag, datum)
    )
    if klar:
        return _buchen("teilzahlung", vorschlag, zahlerregel=regel)
    return _klaeren(
        Klaerfall.Art.BETRAG,
        f"Der Betrag passt auf keine Postengruppe von Vertrag {_vertrag_text(vertrag)} "
        "(mehr als der älteste Posten, weniger als die offenen Posten).",
        K_MITTEL,
        vorschlag_vertrag=vertrag,
        vorschlag_anteile=tuple(vorschlag),
        zahlerregel=regel,
    )


def _sammel(
    betrag: Decimal, regel: Zahlerregel, vertraege: Sequence[Vertrag], datum: datetime.date
) -> Entscheidung:
    """Stufe 2: Sammelzahler. Genau passen muss entweder die Summe aller offenen Posten der
    Vertragsliste (bis zum Zahlungsmonat) oder die Summe des ältesten Monats je Vertrag."""
    je_vertrag = {v.pk: offene_posten(v, bis_monat=datum) for v in vertraege}
    alle = [p for posten in je_vertrag.values() for p in posten]
    aeltester = []
    for posten in je_vertrag.values():
        if posten:
            erster = posten[0].faellig_am
            aeltester += [p for p in posten if p.faellig_am == erster]
    gruppen = {
        _ids(g): g for g in (alle, aeltester) if g and sum((p.offen for p in g), NULL) == betrag
    }
    nummern = ", ".join(v.nummer for v in vertraege)
    if len(gruppen) == 1:
        posten = next(iter(gruppen.values()))
        return _buchen("sammel", [Anteil(p, p.offen) for p in posten], zahlerregel=regel)
    reihe = sorted(alle, key=lambda p: (p.faellig_am, p.vertrag.nummer, p.art, p.pk))
    vorschlag, rest = verteilen(betrag, reihe)
    return _klaeren(
        Klaerfall.Art.BETRAG if not gruppen else Klaerfall.Art.MEHRDEUTIG,
        f"Sammelzahler für {nummern}: Der Betrag passt nicht genau auf die offenen Posten "
        "der Vertragsliste.",
        K_HALB,
        vorschlag_anteile=tuple(vorschlag),
        vorschlag_guthaben=rest,
        alternativen=tuple(v.pk for v in vertraege),
        zahlerregel=regel,
    )


def _ohne_regel(betrag: Decimal, buchung: Buchung, nennungen: Sequence[Nennung]) -> Entscheidung:
    """Stufe 4: Keine Zahlerregel mit Vertrag. Immer ein Klärfall; der Vorschlag kommt aus
    dem Zweck, aus früheren Zuordnungen derselben IBAN oder aus dem Betrag."""
    umsatz = buchung.umsatz
    datum = umsatz.buchungsdatum
    genannt = {i for n in nennungen if not n.wohnraum for i in n.vertraege}
    kopf = "Keine Zahlerregel mit Vertrag."

    def mit_vorschlag(vertrag: Vertrag, grund: str, hoch: Decimal, tief: Decimal) -> Entscheidung:
        exakt = _exakt(betrag, vertrag, datum)
        anteile, rest = (
            ([Anteil(p, p.offen) for p in exakt], NULL)
            if exakt
            else verteilen(betrag, offene_posten(vertrag, bis_monat=datum))
        )
        return _klaeren(
            Klaerfall.Art.UNBEKANNT,
            f"{kopf} {grund}",
            hoch if exakt else tief,
            vorschlag_vertrag=vertrag,
            vorschlag_anteile=tuple(anteile),
            vorschlag_guthaben=rest,
        )

    if len(genannt) == 1:
        vertrag = Vertrag.objects.get(pk=next(iter(genannt)))
        return mit_vorschlag(
            vertrag, f"Der Zweck nennt Vertrag {_vertrag_text(vertrag)}.", K_HOCH, K_HALB
        )
    if len(genannt) > 1:
        return _klaeren(
            Klaerfall.Art.UNBEKANNT,
            f"{kopf} Der Zweck nennt mehrere Verträge.",
            Decimal("0.20"),
            alternativen=tuple(sorted(genannt)),
        )
    iban = fingerabdruck.iban_normal(umsatz.gegen_iban)
    if iban:
        frueher = set(
            _gueltige_zuordnungen()
            .filter(buchung__umsatz__gegen_iban=iban)
            .exclude(buchung=buchung)
            .values_list("sollposten__vertrag_id", flat=True)
        )
        if len(frueher) == 1:
            vertrag = Vertrag.objects.get(pk=next(iter(frueher)))
            return mit_vorschlag(
                vertrag,
                f"Dieselbe IBAN zahlte schon für Vertrag {_vertrag_text(vertrag)}.",
                K_HISTORIE,
                K_SCHWACH,
            )
    passend = [
        v
        for v in Vertrag.objects.filter(status__in=soll.STATUS_MIT_SOLL)
        .filter(Exists(posten_im_abgleich().filter(vertrag=OuterRef("pk"))))
        .order_by("nummer")
        if _exakt(betrag, v, datum)
    ]
    if len(passend) == 1:
        return mit_vorschlag(
            passend[0],
            f"Nur der Betrag passt, und zwar auf Vertrag {_vertrag_text(passend[0])}.",
            K_BETRAG,
            K_BETRAG,
        )
    if len(passend) > 1:
        return _klaeren(
            Klaerfall.Art.UNBEKANNT,
            f"{kopf} Der Betrag passt auf {len(passend)} Verträge.",
            K_GERING,
            alternativen=tuple(v.pk for v in passend),
        )
    return _klaeren(Klaerfall.Art.UNBEKANNT, f"{kopf} Kein Vorschlag.", NULL)


def entscheiden(buchung: Buchung, zahlerregel: Zahlerregel | None = None) -> Entscheidung:
    """Wendet die Stufen 1 bis 4 auf den offenen Betrag einer Buchung an (ohne zu buchen)."""
    betrag = offener_betrag(buchung)
    umsatz = buchung.umsatz
    datum = umsatz.buchungsdatum
    nennungen = zweck_nennungen(umsatz.verwendungszweck, datum)
    vertraege = (
        zahlerregel.vertraege() if zahlerregel and not zahlerregel.nicht_vertragsbezogen else []
    )
    if not vertraege or zahlerregel is None:
        return _ohne_regel(betrag, buchung, nennungen)
    widerspruch = _widerspruch(nennungen, {v.pk for v in vertraege})
    if widerspruch:
        andere = {i for n in widerspruch for i in n.vertraege}
        kennungen = ", ".join(n.kennung for n in widerspruch)
        regel_text = ", ".join(_vertrag_text(v) for v in vertraege)
        vorschlag, rest = verteilen(betrag, offene_posten(vertraege[0], bis_monat=datum))
        return _klaeren(
            Klaerfall.Art.ZWECK,
            f"Der Zweck nennt {kennungen}, die Zahlerregel #{zahlerregel.pk} nennt {regel_text}.",
            K_HALB if len(vertraege) == 1 else K_SCHWACH,
            vorschlag_vertrag=vertraege[0] if len(vertraege) == 1 else None,
            vorschlag_anteile=tuple(vorschlag) if len(vertraege) == 1 else (),
            vorschlag_guthaben=rest if len(vertraege) == 1 else NULL,
            alternativen=tuple(sorted(andere)),
            zahlerregel=zahlerregel,
        )
    if len(vertraege) == 1:
        return _einzel(betrag, zahlerregel, vertraege[0], datum)
    return _sammel(betrag, zahlerregel, vertraege, datum)


# --- Klärfälle (H3.6) ----------------------------------------------------------------


def _hinweis_kopf(buchung: Buchung) -> str:
    umsatz = buchung.umsatz
    return (
        f"Buchung #{buchung.pk} (Umsatz #{umsatz.pk} vom {umsatz.buchungsdatum:%d.%m.%Y}, "
        f"Konto #{umsatz.konto_id}): "
    )


def _vorschlag_text(entscheidung: Entscheidung) -> str:
    if entscheidung.vorschlag_vertrag is not None:
        return (
            f" Vorschlag: Vertrag {_vertrag_text(entscheidung.vorschlag_vertrag)}, "
            f"Konfidenz {_prozent(entscheidung.konfidenz)}."
        )
    if entscheidung.vorschlag_anteile:
        posten = ", ".join(f"#{a.posten.pk}" for a in entscheidung.vorschlag_anteile)
        return f" Vorschlag: Posten {posten}, Konfidenz {_prozent(entscheidung.konfidenz)}."
    return " Kein Vorschlag."


def _aktionen(art: str) -> tuple[str, ...]:
    if art == Klaerfall.Art.RUECKBUCHUNG:
        return AKTIONEN_RUECKBUCHUNG
    if art == Klaerfall.Art.LIEFERANT:
        return AKTIONEN_LIEFERANT
    return AKTIONEN_KLAEREN


def klaerfall_anlegen(
    buchung: Buchung,
    art: str,
    *,
    hinweis: str,
    vorschlag: dict[str, Any] | None = None,
    konfidenz: Decimal = NULL,
    gegen_umsatz: Bankumsatz | None = None,
    lauf: Lauf | None = None,
) -> Klaerfall:
    """Legt einen Klärfall mit seinem Arbeitsposten „Zahlungen klären“ an."""
    klaerfall = Klaerfall(
        buchung=buchung,
        art=art,
        vorschlag=vorschlag or {},
        konfidenz=konfidenz,
        gegen_umsatz=gegen_umsatz,
        lauf=lauf,
    )
    klaerfall.full_clean()
    klaerfall.save()
    kern.arbeitsposten_anlegen(
        ART_KLAEREN,
        bezug=klaerfall,
        aktionen=_aktionen(art),
        faellig_am=buchung.umsatz.buchungsdatum,
        hinweis=hinweis,
        lauf=lauf,
    )
    return klaerfall


def _klaerfall_aus(buchung: Buchung, entscheidung: Entscheidung, lauf: Lauf | None) -> Klaerfall:
    return klaerfall_anlegen(
        buchung,
        entscheidung.art,
        hinweis=_hinweis_kopf(buchung) + entscheidung.grund + _vorschlag_text(entscheidung),
        vorschlag=entscheidung.vorschlag(),
        konfidenz=entscheidung.konfidenz,
        lauf=lauf,
    )


def klaerfall_posten(klaerfall: Klaerfall) -> Arbeitsposten | None:
    """Der Arbeitsposten des Klärfalls (offen, zurückgestellt oder erledigt)."""
    return (
        Arbeitsposten.objects.filter(
            art=ART_KLAEREN,
            bezug_typ=ContentType.objects.get_for_model(Klaerfall),
            bezug_id=klaerfall.pk,
        )
        .order_by("-pk")
        .first()
    )


def _klaerfall_schliessen(klaerfall: Klaerfall, ergebnis: str) -> Klaerfall:
    klaerfall = Klaerfall.objects.select_for_update().get(pk=klaerfall.pk)
    if not klaerfall.offen:
        raise AbgleichFehler(f"Klärfall #{klaerfall.pk} ist schon geklärt.")
    klaerfall.ergebnis = ergebnis
    klaerfall.erledigt_am = timezone.now()
    klaerfall.save(update_fields=["ergebnis", "erledigt_am"])
    posten = klaerfall_posten(klaerfall)
    if posten is not None and posten.status != Arbeitsposten.Status.ERLEDIGT:
        kern.arbeitsposten_erledigen(posten)
    return klaerfall


def _offen_pruefen(klaerfall: Klaerfall) -> None:
    if not klaerfall.offen:
        raise AbgleichFehler(f"Klärfall #{klaerfall.pk} ist schon geklärt.")


@transaction.atomic
def klaerfall_zuordnen(
    klaerfall: Klaerfall, vertrag: Vertrag, *, rest_als_guthaben: bool = True
) -> list[Zuordnung]:
    """Aktion „Zuordnen“: der offene Betrag auf die offenen Posten des Vertrags, älteste
    zuerst; ein Rest wird Guthaben (oder die Aktion bricht ab)."""
    _offen_pruefen(klaerfall)
    buchung = klaerfall.buchung
    betrag = offener_betrag(buchung)
    anteile, rest = verteilen(betrag, offene_posten(vertrag))
    if rest > 0 and not rest_als_guthaben:
        raise AbgleichFehler(
            f"Klärfall #{klaerfall.pk}: Die Zahlung übersteigt die offenen Posten von Vertrag "
            f"{vertrag.nummer}. Rest als Guthaben erlauben oder aufteilen."
        )
    neu = zuordnen(buchung, anteile, guthaben=rest, guthaben_vertrag=vertrag, klaerfall=klaerfall)
    _klaerfall_schliessen(klaerfall, Klaerfall.Ergebnis.ZUGEORDNET)
    return neu


@transaction.atomic
def klaerfall_aufteilen(
    klaerfall: Klaerfall,
    anteile: Sequence[Anteil],
    *,
    guthaben: Decimal = NULL,
    guthaben_vertrag: Vertrag | None = None,
) -> list[Zuordnung]:
    """Aktion „Aufteilen“: Anteile auf beliebige Posten (auch mehrerer Verträge) und ein
    Guthaben. Zusammen genau der offene Betrag der Buchung."""
    _offen_pruefen(klaerfall)
    buchung = klaerfall.buchung
    summe = sum((a.betrag for a in anteile), NULL) + guthaben
    if summe != offener_betrag(buchung):
        raise AbgleichFehler(
            f"Klärfall #{klaerfall.pk}: Anteile und Guthaben müssen zusammen genau den offenen "
            "Betrag der Buchung ergeben."
        )
    neu = zuordnen(
        buchung, anteile, guthaben=guthaben, guthaben_vertrag=guthaben_vertrag, klaerfall=klaerfall
    )
    _klaerfall_schliessen(klaerfall, Klaerfall.Ergebnis.ZUGEORDNET)
    return neu


@transaction.atomic
def klaerfall_nicht_vertragsbezogen(klaerfall: Klaerfall) -> Klaerfall:
    """Aktion „Nicht vertragsbezogen“: Die Buchung gehört zu keinem Vertrag; der Lauf lässt
    sie künftig aus."""
    _offen_pruefen(klaerfall)
    return _klaerfall_schliessen(klaerfall, Klaerfall.Ergebnis.NICHT_VERTRAGSBEZOGEN)


@transaction.atomic
def rueckbuchung_bestaetigen(klaerfall: Klaerfall) -> Klaerfall:
    """Die Zahlung ist zurückgebucht: Zuordnungen lösen, die Posten sind wieder offen."""
    _offen_pruefen(klaerfall)
    buchung_loesen(klaerfall.buchung, f"Rückbuchung (Umsatz #{klaerfall.gegen_umsatz_id})")
    return _klaerfall_schliessen(klaerfall, Klaerfall.Ergebnis.RUECKBUCHUNG)


@transaction.atomic
def keine_rueckbuchung(klaerfall: Klaerfall) -> Klaerfall:
    """Keine Rückbuchung: Zuordnungen bleiben; eine noch offene Buchung gleicht der nächste
    Lauf normal ab."""
    _offen_pruefen(klaerfall)
    return _klaerfall_schliessen(klaerfall, Klaerfall.Ergebnis.KEINE_RUECKBUCHUNG)


@dataclass
class RegelErgebnis:
    zahlerregel: Zahlerregel
    #: Neue Zuordnungsregel im Kassenbuch (nur, wenn es keine passende gab).
    neue_regel: Zuordnungsregel | None
    gebucht: bool


@transaction.atomic
def klaerfall_regel_anlegen(
    klaerfall: Klaerfall,
    *,
    vertrag: Vertrag | None = None,
    vertraege: Sequence[Vertrag] = (),
    betrag: Decimal | None = None,
    kategorie: Kategorie | None = None,
    objekt: Objekt | None = None,
) -> RegelErgebnis:
    """Aktion „Regel anlegen“: Zahlerregel für den Zahler dieser Buchung, danach wird die
    Buchung mit der neuen Regel abgeglichen (gebucht nur, wenn sie genau passt).

    Ist die Buchung nach einer Zuordnungsregel ohne Zahlerregel gebucht, bekommt diese die
    Zahlerregel. Sonst entsteht im Kassenbuch eine Regel nach Gegen-IBAN (Kategorie und
    Objekt wie die Buchung, sofern nicht anders gewählt), mit höherer Priorität als die
    bisher passenden Regeln.
    """
    _offen_pruefen(klaerfall)
    buchung = Buchung.objects.select_related("umsatz", "regel", "kategorie", "objekt").get(
        pk=klaerfall.buchung_id
    )
    umsatz = buchung.umsatz
    neue_regel = None
    regel = buchung.regel
    if regel is None or Zahlerregel.objects.filter(regel=regel).exists():
        iban = fingerabdruck.iban_normal(umsatz.gegen_iban)
        if not iban:
            raise AbgleichFehler(
                f"Umsatz #{umsatz.pk} hat keine IBAN. Bitte im Kassenbuch eine Regel anlegen."
            )
        treffer = kassenbuch.einordnen(umsatz, kassenbuch.aktive_regeln()).treffer
        prioritaet = max((r.prioritaet for r in treffer), default=10) + 1
        ziel = objekt or buchung.objekt
        nummern = vertrag.nummer if vertrag else ", ".join(v.nummer for v in vertraege)
        neue_regel = kassenbuch.regel_anlegen(
            f"Zahler {nummern}"[:200],
            kategorie=kategorie or buchung.kategorie,
            objekt=ziel,
            gegen_iban=iban,
            richtung=EINGANG,
            prioritaet=prioritaet,
        )
        regel = neue_regel
    zahlerregel = zahlerregel_setzen(regel, vertrag=vertrag, vertraege=vertraege, betrag=betrag)
    entscheidung = entscheiden(buchung, zahlerregel)
    gebucht = False
    if entscheidung.buchen:
        zuordnen(
            buchung,
            entscheidung.anteile,
            guthaben=entscheidung.guthaben,
            guthaben_vertrag=entscheidung.guthaben_vertrag,
            zahlerregel=zahlerregel,
            klaerfall=klaerfall,
        )
        _klaerfall_schliessen(klaerfall, Klaerfall.Ergebnis.ZUGEORDNET)
        gebucht = True
    return RegelErgebnis(zahlerregel=zahlerregel, neue_regel=neue_regel, gebucht=gebucht)


# --- Rückbuchungen -------------------------------------------------------------------


def abgleich_buchungen() -> QuerySet[Buchung]:
    """Buchungen, die der Abgleich grundsätzlich ansieht (ohne Blick auf den Stand)."""
    eigene = [
        fingerabdruck.iban_normal(i) for i in Bankkonto.objects.values_list("iban", flat=True)
    ]
    qs = (
        kassenbuch.gueltige_buchungen()
        .filter(
            richtung=EINGANG,
            wohnraum=False,
            kategorie__wohnraum=False,
            kategorie__art__in=(*ABGLEICH_ARTEN, *LIEFERANT_ARTEN),
        )
        .exclude(umsatz__gegen_iban__in=eigene)
    )
    anfang = beginn()
    if anfang is not None:
        qs = qs.filter(umsatz__buchungsdatum__gte=anfang)
    return qs


def rueckbuchungen_pruefen(*, bis: datetime.date | None = None, lauf: Lauf | None = None) -> int:
    """Findet Ausgänge, die eine Zahlung zurückbuchen (``kassenbuch.rueckbuchung_zu``), und
    legt für die Buchung des Eingangs einen Klärfall „Rückbuchung“ an. Gebucht wird dabei
    nichts; erst „Rückbuchung bestätigen“ löst die Zuordnungen."""
    eingaenge = Bankumsatz.objects.filter(
        konto_id=OuterRef("konto_id"),
        richtung=EINGANG,
        gegen_iban=OuterRef("gegen_iban"),
        betrag=OuterRef("betrag"),
        buchungsdatum__lte=OuterRef("buchungsdatum"),
    )
    ausgaenge = (
        Bankumsatz.objects.filter(richtung=AUSGANG)
        .exclude(gegen_iban="")
        .filter(Exists(eingaenge))
        .order_by("buchungsdatum", "pk")
    )
    anfang = beginn()
    if anfang is not None:
        ausgaenge = ausgaenge.filter(buchungsdatum__gte=anfang)
    if bis is not None:
        ausgaenge = ausgaenge.filter(buchungsdatum__lte=bis)
    kandidaten = abgleich_buchungen()
    anzahl = 0
    for ausgang in ausgaenge:
        eingang = kassenbuch.rueckbuchung_zu(ausgang)
        if eingang is None:
            continue
        for buchung in kandidaten.filter(umsatz=eingang).select_related("umsatz"):
            if Klaerfall.objects.filter(buchung=buchung, gegen_umsatz=ausgang).exists():
                continue
            if Klaerfall.objects.filter(buchung=buchung, ergebnis="").exists():
                continue  # ein offener Klärfall genügt; die Rückbuchung prüft der nächste Lauf
            zugeordnet_schon = _gueltige_zuordnungen().filter(buchung=buchung).exists()
            klaerfall_anlegen(
                buchung,
                Klaerfall.Art.RUECKBUCHUNG,
                hinweis=_hinweis_kopf(buchung)
                + f"Vermutlich zurückgebucht durch Umsatz #{ausgang.pk} vom "
                f"{ausgang.buchungsdatum:%d.%m.%Y}."
                + (
                    " Die Zahlung ist schon zugeordnet; bestätigen löst die Zuordnung."
                    if zugeordnet_schon
                    else " Nicht zugeordnet."
                ),
                konfidenz=K_HOCH,
                gegen_umsatz=ausgang,
                lauf=lauf,
            )
            anzahl += 1
    return anzahl


# --- Lauf „abgleich“ -----------------------------------------------------------------


@dataclass
class AbgleichErgebnis:
    gebucht: int = 0
    arbeitsposten: int = 0
    fehler: int = 0
    verrechnet: int = 0
    nicht_vertragsbezogen: int = 0
    geloest: int = 0
    kautionsposten: int = 0
    je_stufe: Counter[str] = field(default_factory=Counter)
    je_art: Counter[str] = field(default_factory=Counter)

    def text(self) -> str:
        stufen = ", ".join(f"{k} {v}" for k, v in sorted(self.je_stufe.items()))
        arten = ", ".join(f"{k} {v}" for k, v in sorted(self.je_art.items()))
        return (
            f"{self.gebucht} Buchungen zugeordnet ({stufen or 'keine'}), "
            f"{self.arbeitsposten} Klärfälle ({arten or 'keine'}), {self.fehler} Fehler, "
            f"{self.verrechnet} Guthaben verrechnet, {self.nicht_vertragsbezogen} nicht "
            f"vertragsbezogen, {self.geloest} stornierte Buchungen gelöst, "
            f"{self.kautionsposten} Kautionsposten gestellt"
        )


def _summe_sub(qs: QuerySet[Any]) -> Coalesce:
    return Coalesce(
        Subquery(
            qs.values("buchung").annotate(s=Sum("betrag")).values("s")[:1],
            output_field=models.DecimalField(max_digits=14, decimal_places=2),
        ),
        NULL,
        output_field=models.DecimalField(max_digits=14, decimal_places=2),
    )


def offene_buchungen(*, bis: datetime.date | None = None) -> QuerySet[Buchung]:
    """Buchungen im Abgleich, die noch nicht voll zugeordnet sind und keinen offenen oder
    abschließenden Klärfall haben (nicht vertragsbezogen, Rückbuchung bestätigt)."""
    zuordnungen = Zuordnung.objects.filter(buchung=OuterRef("pk"), storniert_am__isnull=True)
    guthaben = Guthaben.objects.filter(
        buchung=OuterRef("pk"), storniert_am__isnull=True, aufgeloest_am__isnull=True
    )
    sperrend = Klaerfall.objects.filter(buchung=OuterRef("pk")).filter(
        Q(ergebnis=Klaerfall.Ergebnis.OFFEN)
        | Q(
            ergebnis__in=(
                Klaerfall.Ergebnis.NICHT_VERTRAGSBEZOGEN,
                Klaerfall.Ergebnis.RUECKBUCHUNG,
            )
        )
    )
    qs = (
        abgleich_buchungen()
        .annotate(verteilt=_summe_sub(zuordnungen) + _summe_sub(guthaben))
        .filter(verteilt__lt=F("betrag"))
        .exclude(Exists(sperrend))
        .select_related("umsatz", "umsatz__konto", "regel", "kategorie")
        .order_by("umsatz__buchungsdatum", "pk")
    )
    if bis is not None:
        qs = qs.filter(umsatz__buchungsdatum__lte=bis)
    return qs


def _lieferanten_iban(buchung: Buchung) -> bool:
    """Zahlte das Hof-System an diese IBAN Ausgaben (Kategorie Ausgabe), und hat sie nie für
    einen Vertrag gezahlt? Dann ist der Eingang eher eine Erstattung als eine Miete."""
    iban = fingerabdruck.iban_normal(buchung.umsatz.gegen_iban)
    if not iban:
        return False
    ausgaben = kassenbuch.gueltige_buchungen().filter(
        richtung=AUSGANG, kategorie__art__in=LIEFERANT_ARTEN, umsatz__gegen_iban=iban
    )
    if not ausgaben.exists():
        return False
    return not _gueltige_zuordnungen().filter(buchung__umsatz__gegen_iban=iban).exists()


def buchung_abgleichen(
    buchung: Buchung, *, lauf: Lauf | None = None, ergebnis: AbgleichErgebnis | None = None
) -> Entscheidung | None:
    """Gleicht eine Buchung ab: buchen, Klärfall oder (nicht vertragsbezogen) auslassen."""
    ergebnis = ergebnis if ergebnis is not None else AbgleichErgebnis()
    zahlerregel = zahlerregel_zu(buchung)
    if zahlerregel is not None and zahlerregel.nicht_vertragsbezogen:
        ergebnis.nicht_vertragsbezogen += 1
        return None
    lieferant = buchung.kategorie.art in LIEFERANT_ARTEN or (
        zahlerregel is None and _lieferanten_iban(buchung)
    )
    if lieferant:
        # Erstattung oder Rücklastschrift eines Lieferanten: nie gegen Sollposten von Mietern.
        _klaerfall_aus(
            buchung,
            Entscheidung(
                buchen=False,
                art=Klaerfall.Art.LIEFERANT,
                grund="Eingang eines Lieferanten (Ausgabe-Kategorie oder IBAN aus Ausgaben): "
                "Erstattung oder Rücklastschrift, nicht im Mieterabgleich.",
            ),
            lauf,
        )
        ergebnis.arbeitsposten += 1
        ergebnis.je_art[Klaerfall.Art.LIEFERANT] += 1
        return None
    schon = (
        Zuordnung.objects.filter(buchung=buchung).exists()
        or Guthaben.objects.filter(buchung=buchung).exists()
    )
    if schon:
        # Einmal zugeordnet und jetzt (nach einem Storno) wieder offen: nie von selbst.
        _klaerfall_aus(
            buchung,
            Entscheidung(
                buchen=False,
                art=Klaerfall.Art.ERNEUT,
                grund="Die Zahlung war schon zugeordnet und ist nach einem Storno wieder "
                "(teilweise) offen.",
            ),
            lauf,
        )
        ergebnis.arbeitsposten += 1
        ergebnis.je_art[Klaerfall.Art.ERNEUT] += 1
        return None
    entscheidung = entscheiden(buchung, zahlerregel)
    if entscheidung.buchen:
        try:
            with transaction.atomic():
                zuordnen(
                    buchung,
                    entscheidung.anteile,
                    guthaben=entscheidung.guthaben,
                    guthaben_vertrag=entscheidung.guthaben_vertrag,
                    zahlerregel=entscheidung.zahlerregel,
                    lauf=lauf,
                )
        except AbgleichFehler as fehler:
            ergebnis.fehler += 1
            logger.warning("Buchung #%s: Zuordnung abgelehnt", buchung.pk)
            klaerfall_anlegen(
                buchung,
                Klaerfall.Art.BETRAG,
                hinweis=_hinweis_kopf(buchung)
                + f"Die Zuordnung ließ sich nicht buchen ({fehler}).",
                lauf=lauf,
            )
            ergebnis.arbeitsposten += 1
            return entscheidung
        ergebnis.gebucht += 1
        ergebnis.je_stufe[entscheidung.stufe] += 1
        return entscheidung
    _klaerfall_aus(buchung, entscheidung, lauf)
    ergebnis.arbeitsposten += 1
    ergebnis.je_art[entscheidung.art] += 1
    return entscheidung


def abgleichen(
    *,
    stichtag: datetime.date | None = None,
    bis: datetime.date | None = None,
    lauf: Lauf | None = None,
) -> AbgleichErgebnis:
    """Der Lauf ``abgleich`` (H3.5): nach dem Einordnen im Kassenbuch, täglich. Idempotent.

    1. Zuordnungen zu Buchungen lösen, die das Kassenbuch storniert hat.
    2. Kautionen als Posten stellen (Eingang über die Bank).
    3. Rückbuchungen erkennen (Klärfall, nie still gebucht).
    4. Je offene Buchung, nach Datum: zuerst Guthaben mit Posten verrechnen, deren Zeitraum
       bis zum Zahlungstag begonnen hat, dann ``entscheiden`` und buchen oder Klärfall.
    5. Am Ende Guthaben mit den Posten bis zum Stichtag verrechnen.
    """
    stichtag = stichtag or heute()
    ergebnis = AbgleichErgebnis()
    ergebnis.geloest = stornierte_buchungen_loesen()
    ergebnis.kautionsposten = len(kautionsposten_stellen(stichtag=stichtag))
    rueck = rueckbuchungen_pruefen(bis=bis, lauf=lauf)
    ergebnis.arbeitsposten += rueck
    if rueck:
        ergebnis.je_art[Klaerfall.Art.RUECKBUCHUNG] += rueck
    letzter: datetime.date | None = None
    for buchung in offene_buchungen(bis=bis).iterator(chunk_size=200):
        tag = buchung.umsatz.buchungsdatum
        if tag != letzter:
            ergebnis.verrechnet += guthaben_verrechnen(stichtag=tag, lauf=lauf)
            letzter = tag
        buchung_abgleichen(buchung, lauf=lauf, ergebnis=ergebnis)
    ergebnis.verrechnet += guthaben_verrechnen(stichtag=stichtag, lauf=lauf)
    return ergebnis


# --- Rückstände (H3.11) --------------------------------------------------------------


def bank_datenstand() -> datetime.date | None:
    """Bis wann die Bankdaten aller aktiven Konten vorliegen (der früheste Stand). Eine Lücke
    in den Bankdaten gilt nie als Rückstand (Standardablauf 2)."""
    staende = []
    for konto in Bankkonto.objects.filter(aktiv=True):
        stand = max(
            (
                d
                for d in (
                    Kontostand.objects.filter(konto=konto).aggregate(m=Max("datum"))["m"],
                    konto.umsaetze.aggregate(m=Max("buchungsdatum"))["m"],
                )
                if d is not None
            ),
            default=None,
        )
        if stand is None:
            return None
        staende.append(stand)
    return min(staende) if staende else None


@dataclass
class Rueckstand:
    """Rückstand eines Vertrags nach Alter der Fälligkeit (Tage)."""

    vertrag: Vertrag
    nw_ids: list[str]
    bis_30: Decimal = NULL
    bis_60: Decimal = NULL
    ueber_60: Decimal = NULL
    #: fällig nach dem Datenstand der Bank: nicht prüfbar, kein Rückstand
    nicht_pruefbar: Decimal = NULL
    guthaben: Decimal = NULL
    in_klaerung: bool = False
    posten: list[Sollposten] = field(default_factory=list)

    @property
    def summe(self) -> Decimal:
        return self.bis_30 + self.bis_60 + self.ueber_60


@dataclass
class Rueckstaende:
    stichtag: datetime.date
    datenstand: datetime.date | None
    vertraege: list[Rueckstand]

    @property
    def summe(self) -> Decimal:
        return sum((r.summe for r in self.vertraege), NULL)

    def summe_nach(self, feld: str) -> Decimal:
        return sum((getattr(r, feld) for r in self.vertraege), NULL)

    @property
    def mit_rueckstand(self) -> list[Rueckstand]:
        return [r for r in self.vertraege if r.summe > 0]

    def kennzahlen(self) -> dict[str, Any]:
        """Zähler und Summen ohne Namen, für Hof-Cockpit, Tageslauf und Status-MCP: nur
        Anzahlen, Gesamtsummen je Altersklasse und NW-IDs, keine Beträge je Vertrag."""
        mit = self.mit_rueckstand
        return {
            "stichtag": self.stichtag.isoformat(),
            "datenstand_bank": self.datenstand.isoformat() if self.datenstand else None,
            "vertraege_mit_rueckstand": len(mit),
            "vertraege_in_klaerung": sum(1 for r in self.vertraege if r.in_klaerung),
            "summe": self.summe,
            "summe_0_30": self.summe_nach("bis_30"),
            "summe_31_60": self.summe_nach("bis_60"),
            "summe_ueber_60": self.summe_nach("ueber_60"),
            "nicht_pruefbar": self.summe_nach("nicht_pruefbar"),
            "nw_ids": sorted(i for r in mit for i in r.nw_ids),
        }


def vertraege_in_klaerung() -> set[int]:
    """Verträge, die ein offener Klärfall betrifft (Vorschlag, Alternativen, Zahlerregel)."""
    ids: set[int] = set()
    for klaerfall in Klaerfall.objects.filter(ergebnis=Klaerfall.Ergebnis.OFFEN).select_related(
        "buchung"
    ):
        vorschlag = klaerfall.vorschlag or {}
        if vorschlag.get("vertrag"):
            ids.add(int(vorschlag["vertrag"]))
        ids.update(int(i) for i in vorschlag.get("alternativen", []))
        for posten_id, _ in vorschlag.get("posten", []):
            vertrag_id = (
                Sollposten.objects.filter(pk=posten_id).values_list("vertrag_id", flat=True).first()
            )
            if vertrag_id:
                ids.add(vertrag_id)
        zahlerregel = zahlerregel_zu(klaerfall.buchung)
        if zahlerregel is not None:
            ids.update(v.pk for v in zahlerregel.vertraege())
    return ids


def rueckstaende(stichtag: datetime.date | None = None) -> Rueckstaende:
    """Rückstände je Vertrag mit Alter (0–30, 31–60, über 60 Tage nach Fälligkeit) und die
    Summe (H3.11). Für Hof-Cockpit und Status-MCP; liest nur, rechnet live aus dem Stand.

    Fällige Posten nach dem Datenstand der Bank sind „nicht prüfbar“, kein Rückstand.
    """
    stichtag = stichtag or heute()
    datenstand = bank_datenstand()
    klaerung = vertraege_in_klaerung()
    je_vertrag: dict[int, Rueckstand] = {}
    posten = (
        posten_im_abgleich()
        .filter(faellig_am__lt=stichtag)
        .select_related("vertrag")
        .prefetch_related("vertrag__einheiten")
        .order_by("vertrag__nummer", "faellig_am", "pk")
    )
    for p in posten:
        eintrag = je_vertrag.get(p.vertrag_id)
        if eintrag is None:
            eintrag = je_vertrag[p.vertrag_id] = Rueckstand(
                vertrag=p.vertrag,
                nw_ids=[e.nw_id for e in p.vertrag.einheiten.all()],
                in_klaerung=p.vertrag_id in klaerung,
                guthaben=guthaben_summe(p.vertrag),
            )
        eintrag.posten.append(p)
        if datenstand is None or p.faellig_am > datenstand:
            eintrag.nicht_pruefbar += p.offen
            continue
        alter = (stichtag - p.faellig_am).days
        if alter <= 30:
            eintrag.bis_30 += p.offen
        elif alter <= 60:
            eintrag.bis_60 += p.offen
        else:
            eintrag.ueber_60 += p.offen
    return Rueckstaende(
        stichtag=stichtag,
        datenstand=datenstand,
        vertraege=sorted(je_vertrag.values(), key=lambda r: r.vertrag.nummer),
    )


# --- Mietkonto je Vertrag ------------------------------------------------------------


@dataclass
class Kontozeile:
    posten: Sollposten
    zuordnungen: list[Zuordnung]


@dataclass
class Mietkonto:
    vertrag: Vertrag
    zeilen: list[Kontozeile]
    guthaben: list[Guthaben]
    handzahlungen: list[Handzahlung]
    klaerfaelle: list[Klaerfall]

    @property
    def soll_faellig(self) -> Decimal:
        tag = heute()
        return sum((z.posten.betrag for z in self.zeilen if z.posten.faellig_am <= tag), NULL)

    @property
    def bezahlt(self) -> Decimal:
        return sum((z.posten.bezahlt for z in self.zeilen), NULL)

    @property
    def guthaben_summe(self) -> Decimal:
        return sum((g.betrag for g in self.guthaben), NULL)

    @property
    def saldo(self) -> Decimal:
        """Bezahlt plus Guthaben minus fälliges Soll (negativ: Rückstand)."""
        return self.bezahlt + self.guthaben_summe - self.soll_faellig


def mietkonto(vertrag: Vertrag) -> Mietkonto:
    """Das Konto eines Vertrags: Posten im Abgleich mit ihren Zuordnungen, Guthaben,
    Handzahlungen und offene Klärfälle."""
    qs = Sollposten.objects.filter(vertrag=vertrag).exclude(status=Sollposten.Status.STORNIERT)
    anfang = beginn()
    if anfang is not None:
        qs = qs.filter(bis__gte=anfang)
    zeilen = [
        Kontozeile(
            posten=p,
            zuordnungen=list(
                p.zuordnungen.filter(storniert_am__isnull=True)
                .select_related("buchung__umsatz", "handzahlung")
                .order_by("datum", "pk")
            ),
        )
        for p in _reihenfolge(qs)
    ]
    klaerfaelle = [
        k
        for k in Klaerfall.objects.filter(ergebnis=Klaerfall.Ergebnis.OFFEN).select_related(
            "buchung"
        )
        if vertrag.pk in _klaerfall_vertraege(k)
    ]
    return Mietkonto(
        vertrag=vertrag,
        zeilen=zeilen,
        guthaben=list(_offene_guthaben().filter(vertrag=vertrag).order_by("pk")),
        handzahlungen=list(vertrag.handzahlungen.order_by("-datum", "-pk")),
        klaerfaelle=klaerfaelle,
    )


def _klaerfall_vertraege(klaerfall: Klaerfall) -> set[int]:
    vorschlag = klaerfall.vorschlag or {}
    ids = {int(i) for i in vorschlag.get("alternativen", [])}
    if vorschlag.get("vertrag"):
        ids.add(int(vorschlag["vertrag"]))
    zahlerregel = zahlerregel_zu(klaerfall.buchung)
    if zahlerregel is not None:
        ids.update(v.pk for v in zahlerregel.vertraege())
    return ids


# --- Rückrechnung --------------------------------------------------------------------

BEZAHLT = "bezahlt"
STAND_OFFEN = "offen"
IN_KLAERUNG = "in Klärung"


@dataclass(frozen=True)
class VertragStand:
    nummer: str
    nw_ids: tuple[str, ...]
    stand: str


def stand_je_vertrag(
    von: datetime.date, bis: datetime.date, *, stichtag: datetime.date
) -> dict[str, VertragStand]:
    """Stand je Vertrag für die Posten im Zeitraum, die bis zum Stichtag fällig sind:
    „in Klärung“ (offener Klärfall betrifft ihn), „bezahlt“ (alles bezahlt) oder „offen“."""
    grenze = min(stichtag, bis)
    klaerung = vertraege_in_klaerung()
    posten = (
        Sollposten.objects.filter(von__lte=bis, bis__gte=von, faellig_am__lte=grenze)
        .exclude(status=Sollposten.Status.STORNIERT)
        .select_related("vertrag")
        .prefetch_related("vertrag__einheiten")
        .order_by("vertrag__nummer")
    )
    je_vertrag: dict[str, list[Sollposten]] = defaultdict(list)
    vertraege: dict[str, Vertrag] = {}
    for p in posten:
        je_vertrag[p.vertrag.nummer].append(p)
        vertraege[p.vertrag.nummer] = p.vertrag
    ergebnis = {}
    for nummer, liste in je_vertrag.items():
        vertrag = vertraege[nummer]
        if vertrag.pk in klaerung:
            stand = IN_KLAERUNG
        elif all(p.status == Sollposten.Status.BEZAHLT for p in liste):
            stand = BEZAHLT
        else:
            stand = STAND_OFFEN
        ergebnis[nummer] = VertragStand(
            nummer=nummer, nw_ids=tuple(e.nw_id for e in vertrag.einheiten.all()), stand=stand
        )
    return ergebnis


@dataclass
class RueckrechnungBericht:
    von: datetime.date
    bis: datetime.date
    stichtag: datetime.date
    vorher: dict[str, VertragStand]
    nachher: dict[str, VertragStand]
    lauf: AbgleichErgebnis
    ohne_vertrag: int = 0

    def zaehler(self) -> Counter[str]:
        return Counter(s.stand for s in self.nachher.values())

    def abweichend(self) -> list[tuple[VertragStand, str]]:
        """Verträge, deren Stand in der Rückrechnung anders ist als im aktuellen Stand."""
        ergebnis = []
        for nummer, neu in sorted(self.nachher.items()):
            alt = self.vorher.get(nummer)
            alt_stand = alt.stand if alt is not None else "ohne Posten"
            if alt_stand != neu.stand:
                ergebnis.append((neu, alt_stand))
        return ergebnis

    def zeilen(self) -> list[str]:
        """Der Bericht: nur Zähler, Vertragsnummern und NW-IDs, keine Namen und keine Beträge
        je Vertrag."""
        zaehler = self.zaehler()
        zeilen = [
            f"Rückrechnung {self.von:%m/%Y} bis {self.bis:%m/%Y}, Stichtag "
            f"{self.stichtag:%d.%m.%Y} (in einer Kopie; nichts gespeichert)",
            f"Lauf: {self.lauf.text()}",
            f"Verträge: {len(self.nachher)}; bezahlt {zaehler[BEZAHLT]}, offen "
            f"{zaehler[STAND_OFFEN]}, in Klärung {zaehler[IN_KLAERUNG]}; Klärfälle ohne "
            f"Vertrag {self.ohne_vertrag}",
            "Stand je Vertrag:",
        ]
        for nummer, stand in sorted(self.nachher.items()):
            zeilen.append(f"  {nummer} ({', '.join(stand.nw_ids) or '–'}): {stand.stand}")
        for titel, wert in (("offen", STAND_OFFEN), ("in Klärung", IN_KLAERUNG)):
            ids = sorted(i for s in self.nachher.values() if s.stand == wert for i in s.nw_ids)
            zeilen.append(f"NW-IDs {titel}: {', '.join(ids) or 'keine'}")
        abweichend = self.abweichend()
        zeilen.append(f"Abweichung zum aktuellen Stand: {len(abweichend)}")
        for stand, alt in abweichend:
            zeilen.append(f"  {', '.join(stand.nw_ids) or stand.nummer}: {alt} → {stand.stand}")
        return zeilen


def _kopie_zuruecksetzen(von: datetime.date) -> None:
    """Nur in der Kopie (die Transaktion wird zurückgerollt): entfernt, was der Abgleich aus
    Buchungen ab ``von`` gemacht hat, damit der Lauf frisch entscheidet. Handzahlungen und
    Buchungen vor ``von`` (der Vortrag) bleiben."""
    buchungen = Buchung.objects.filter(umsatz__buchungsdatum__gte=von)
    zuordnungen = Zuordnung.objects.filter(buchung__in=buchungen)
    posten_ids = set(zuordnungen.values_list("sollposten_id", flat=True))
    zuordnungen.delete()
    guthaben = Guthaben.objects.filter(buchung__in=buchungen)
    while guthaben.exists():
        guthaben.filter(reste__isnull=True).delete()
    klaerfaelle = Klaerfall.objects.filter(buchung__in=buchungen)
    Arbeitsposten.objects.filter(
        bezug_typ=ContentType.objects.get_for_model(Klaerfall),
        bezug_id__in=list(klaerfaelle.values_list("pk", flat=True)),
    ).delete()
    klaerfaelle.delete()
    for posten in Sollposten.objects.filter(pk__in=posten_ids).order_by("pk"):
        _zahlungsstand(posten)


def rueckrechnung(
    von: datetime.date, bis: datetime.date, *, stichtag: datetime.date | None = None
) -> RueckrechnungBericht:
    """Rechnet den Abgleich für den Zeitraum neu, in einer Kopie: alles läuft in einer
    Transaktion, die am Ende zurückgerollt wird. Die Datenbank bleibt unverändert."""
    stichtag = stichtag or heute()
    bericht: RueckrechnungBericht | None = None
    with transaction.atomic():
        vorher = stand_je_vertrag(von, bis, stichtag=stichtag)
        with beginn_setzen(von):
            _kopie_zuruecksetzen(von)
            ergebnis = abgleichen(stichtag=min(stichtag, bis), bis=bis)
            nachher = stand_je_vertrag(von, bis, stichtag=stichtag)
            ohne = sum(
                1
                for k in Klaerfall.objects.filter(ergebnis=Klaerfall.Ergebnis.OFFEN)
                if not _klaerfall_vertraege(k)
            )
        bericht = RueckrechnungBericht(
            von=von,
            bis=bis,
            stichtag=stichtag,
            vorher=vorher,
            nachher=nachher,
            lauf=ergebnis,
            ohne_vertrag=ohne,
        )
        transaction.set_rollback(True)
    return bericht


# --- Rücknahme im Protokoll ----------------------------------------------------------


def _nur_anlage(titel: str, ereignis: kern.Ereignis) -> None:
    if ereignis.art != "insert":
        raise kern.RuecknahmeNichtMoeglich(
            f"{titel} #{ereignis.objekt_id}: Nur die Anlage lässt sich zurücknehmen, und zwar "
            "als Storno."
        )


def _zuordnung_zuruecknehmen(ereignis: kern.Ereignis) -> None:
    _nur_anlage("Zuordnung", ereignis)
    zuordnung = Zuordnung.objects.filter(pk=ereignis.objekt_id).first()
    if zuordnung is None:
        raise kern.RuecknahmeNichtMoeglich(f"Zuordnung #{ereignis.objekt_id} gibt es nicht.")
    try:
        zuordnung_stornieren(zuordnung, "im Protokoll zurückgenommen")
    except AbgleichFehler as fehler:
        raise kern.RuecknahmeNichtMoeglich(str(fehler)) from fehler


def _handzahlung_zuruecknehmen(ereignis: kern.Ereignis) -> None:
    _nur_anlage("Handzahlung", ereignis)
    handzahlung = Handzahlung.objects.filter(pk=ereignis.objekt_id).first()
    if handzahlung is None:
        raise kern.RuecknahmeNichtMoeglich(f"Handzahlung #{ereignis.objekt_id} gibt es nicht.")
    try:
        handzahlung_stornieren(handzahlung, "im Protokoll zurückgenommen")
    except AbgleichFehler as fehler:
        raise kern.RuecknahmeNichtMoeglich(str(fehler)) from fehler


def _guthaben_zuruecknehmen(ereignis: kern.Ereignis) -> None:
    _nur_anlage("Guthaben", ereignis)
    guthaben = Guthaben.objects.filter(pk=ereignis.objekt_id).first()
    if guthaben is None or not guthaben.offen:
        raise kern.RuecknahmeNichtMoeglich(
            f"Guthaben #{ereignis.objekt_id} ist schon verrechnet, erstattet oder storniert."
        )
    _stornieren(guthaben, "im Protokoll zurückgenommen")


def _endgueltig(titel: str) -> kern.StornoWeg:
    def verweigern(ereignis: kern.Ereignis) -> None:
        raise kern.RuecknahmeNichtMoeglich(
            f"{titel} #{ereignis.objekt_id} lässt sich nicht im Protokoll zurücknehmen; die "
            "Zuordnungen bitte im Mietkonto stornieren."
        )

    return verweigern


def stornowege_registrieren() -> None:
    """Meldet beim Kern an, dass Rücknahmen im Abgleich als Storno laufen."""
    kern.stornoweg_registrieren(Zuordnung, _zuordnung_zuruecknehmen)
    kern.stornoweg_registrieren(Handzahlung, _handzahlung_zuruecknehmen)
    kern.stornoweg_registrieren(Guthaben, _guthaben_zuruecknehmen)
    kern.stornoweg_registrieren(Klaerfall, _endgueltig("Klärfall"))
