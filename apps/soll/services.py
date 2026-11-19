"""Schreibweg der Sollstellung (Schritt 5, H3.4; CLAUDE.md, Regel 4.1).

- ``sollstellung_erzeugen(vertrag, bis_datum)`` gleicht die Sollposten eines Vertrags mit
  seinen Konditionen ab: Fehlende entstehen, offene Posten, die nicht mehr passen, werden
  storniert und neu erzeugt. Idempotent: Ein zweiter Aufruf ändert nichts. Die Rechnung steht
  in ``apps.soll.rechnung``.
- ``vertrag_geaendert(vertrag)`` ist der eine Haken für Änderungen am Vertrag (Kondition,
  Laufzeit, Kündigung, Übergabe, Rückgabe). Die Services der Stammdaten rufen ihn auf; es
  gibt keine verstreuten Signale.
- Bezahlte oder teilweise bezahlte Posten ändert die Sollstellung nie still. Passt so ein
  Posten nicht mehr, entsteht ein Arbeitsposten „Sollposten prüfen“.
- Erweiterungsstelle für Schritt 6: ``zahlungsstand_setzen`` hält fest, was auf einen Posten
  gezahlt ist; der Abgleich ruft es auf.
- ``offen(vertrag, stichtag)`` ist die eine Wahrheit für „offen“ (Review 1, Punkt 1): offene
  Posten mit Soll, bezahlt und offen und die Summen. Wer einen offenen Betrag nennt, liest
  ihn dort (Seiten mit Filter über ``offen_abfrage``).
- Die Sollstellung beginnt je Vertrag am Stichtag des Umzugs (``soll_ab``). Was davor im
  alten System offen war, ist ein Posten „Vortrag“ je Vertrag (``vortrag_setzen``, aus dem
  Import oder von Hand).

Meldungen nennen Vertragsnummer, NW-ID und Posten-ID, keine Namen und keine Beträge.
"""

from __future__ import annotations

import contextlib
import contextvars
import datetime
import logging
from collections.abc import Iterator
from dataclasses import dataclass, field
from decimal import Decimal

from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.db.models import Count, F, Max, QuerySet, Sum

from apps.kern import services as kern
from apps.kern import zeit
from apps.kern.models import Arbeitsposten, Lauf
from apps.soll import konfiguration, rechnung
from apps.soll.models import Sollposten, Sollregel
from apps.stammdaten import fristrechnung
from apps.stammdaten.models import MIT_SOLL, Vertrag

logger = logging.getLogger(__name__)

ART_PRUEFEN = "Sollposten prüfen"
GRUND_AENDERUNG = "Vertrag geändert"

#: Arten, die aus den Konditionen entstehen. Kaution und Sonstiges legt man von Hand an.
ARTEN_AUS_VERTRAG = (Sollposten.Art.MIETE, Sollposten.Art.PAUSCHALE)
#: Verträge in diesen Status haben ein Soll (bis zu ihrem Ende).
STATUS_MIT_SOLL = MIT_SOLL  # stammdaten.models.VertragQuerySet
GELTEND = (Sollposten.Status.OFFEN, Sollposten.Status.TEILWEISE, Sollposten.Status.BEZAHLT)


class SollFehler(Exception):
    """Eine Änderung an der Sollstellung ist so nicht möglich. Die Meldung nennt nur IDs."""


_OHNE: contextvars.ContextVar[str] = contextvars.ContextVar("soll_ohne_sollstellung", default="")


@contextlib.contextmanager
def ohne_sollstellung(grund: str) -> Iterator[None]:
    """In diesem Block löst ``vertrag_geaendert`` nichts aus (z. B. im Testhof, der das Soll
    danach für einen festen Stichtag erzeugt, damit gleicher Seed gleiche Daten ergibt)."""
    marke = _OHNE.set(grund)
    try:
        yield
    finally:
        _OHNE.reset(marke)


def heute() -> datetime.date:
    """Das heutige Datum der Sollstellung (in Tests austauschbar)."""
    return zeit.heute()


# --- Regeln je Vertrag ---------------------------------------------------------------


def _regel(vertrag: Vertrag) -> Sollregel | None:
    return Sollregel.objects.filter(vertrag=vertrag).first()


def faelligkeitsregel(vertrag: Vertrag) -> rechnung.Faelligkeitsregel:
    """Fälligkeit des Vertrags: seine Sollregel, sonst der Standard aus ``konfiguration.toml``."""
    standard = konfiguration.laden().faelligkeit
    regel = _regel(vertrag)
    art, tag = standard.art, standard.tag
    if regel is not None and regel.faelligkeit:
        art = regel.faelligkeit
        tag = regel.faelligkeit_tag or standard.tag
    return rechnung.Faelligkeitsregel(
        art=art, tag=tag, land=standard.land, samstag_ist_werktag=standard.samstag_ist_werktag
    )


def soll_ab(vertrag: Vertrag) -> datetime.date | None:
    """Ab wann das Hof-System das Soll des Vertrags führt (vorher das alte System)."""
    regel = _regel(vertrag)
    if regel is not None and regel.soll_ab is not None:
        return regel.soll_ab
    beginn: datetime.date | None = getattr(settings, "SOLL_BEGINN", None)
    return beginn


def stufen(vertrag: Vertrag) -> list[rechnung.Stufe]:
    return [
        rechnung.Stufe(
            von=k.gueltig_ab,
            bis=k.gueltig_bis,
            miete=k.miete,
            pauschale=k.pauschale,
            steuersatz=k.steuersatz,
            zahlweise=k.zahlweise,
        )
        for k in vertrag.konditionen.order_by("gueltig_ab")
    ]


def plan(vertrag: Vertrag, bis_datum: datetime.date) -> list[rechnung.Planposten]:
    """Die Posten, die der Vertrag bis ``bis_datum`` haben soll (ohne Datenbank-Änderung)."""
    if vertrag.status not in STATUS_MIT_SOLL:
        return []
    return rechnung.plan(
        stufen(vertrag),
        beginn=vertrag.beginn,
        ende=vertrag.ende,
        bis_datum=bis_datum,
        regel=faelligkeitsregel(vertrag),
        soll_ab=soll_ab(vertrag),
    )


def sollregel_setzen(
    vertrag: Vertrag,
    *,
    faelligkeit: str | None = None,
    faelligkeit_tag: int | None = None,
    soll_ab: datetime.date | None = None,
    soll_ab_leeren: bool = False,
) -> Sollregel:
    """Legt Fälligkeit oder Beginn der Sollstellung für einen Vertrag fest und stellt das Soll
    neu. ``faelligkeit=""`` setzt die Fälligkeit auf den Standard zurück."""
    regel = _regel(vertrag) or Sollregel(vertrag=vertrag)
    if faelligkeit is not None:
        if faelligkeit:
            try:
                konfiguration.faelligkeit_pruefen(faelligkeit, faelligkeit_tag or 0)
            except konfiguration.KonfigurationFehler as fehler:
                raise SollFehler(f"Vertrag {vertrag.nummer}: {fehler}") from fehler
            regel.faelligkeit_tag = faelligkeit_tag
        else:
            regel.faelligkeit_tag = None
        regel.faelligkeit = faelligkeit
    if soll_ab is not None:
        regel.soll_ab = soll_ab
    elif soll_ab_leeren:
        regel.soll_ab = None
    with transaction.atomic():
        regel.save()
        vertrag_geaendert(vertrag)
    return regel


# --- Abgleich von Plan und Bestand ---------------------------------------------------


@dataclass
class Ergebnis:
    """Was eine Sollstellung geändert hat. ``unveraendert`` zählt Posten, die passten."""

    neu: list[Sollposten] = field(default_factory=list)
    storniert: list[Sollposten] = field(default_factory=list)
    #: Bezahlte oder teilweise bezahlte Posten, die nicht mehr passen (nicht geändert).
    abweichend: list[Sollposten] = field(default_factory=list)
    arbeitsposten: list[Arbeitsposten] = field(default_factory=list)
    unveraendert: int = 0
    vertraege: int = 0
    fehler: list[str] = field(default_factory=list)

    def dazu(self, anderes: Ergebnis) -> None:
        self.neu += anderes.neu
        self.storniert += anderes.storniert
        self.abweichend += anderes.abweichend
        self.arbeitsposten += anderes.arbeitsposten
        self.unveraendert += anderes.unveraendert
        self.vertraege += anderes.vertraege
        self.fehler += anderes.fehler

    def text(self) -> str:
        teile = [
            f"{self.vertraege} Verträge",
            f"{len(self.neu)} Posten neu",
            f"{len(self.storniert)} storniert",
            f"{self.unveraendert} unverändert",
            f"{len(self.abweichend)} bezahlt und abweichend",
            f"{len(self.arbeitsposten)} Arbeitsposten",
        ]
        if self.fehler:
            teile.append(f"Fehler bei {', '.join(self.fehler)}")
        return "; ".join(teile)


def _gleich(posten: Sollposten, soll: rechnung.Planposten) -> bool:
    return (
        posten.netto == soll.netto
        and posten.steuersatz == soll.steuersatz
        and posten.steuer == soll.steuer
        and posten.betrag == soll.betrag
        and posten.faellig_am == soll.faellig_am
    )


def _bestand(vertrag: Vertrag) -> QuerySet[Sollposten]:
    return Sollposten.objects.filter(
        vertrag=vertrag, quelle=Sollposten.Quelle.VERTRAG, status__in=GELTEND
    )


def _melden(posten: Sollposten, vertrag: Vertrag, lauf: Lauf | None) -> Arbeitsposten | None:
    """Ein Arbeitsposten je abweichendem bezahlten Posten; ein erledigter gilt als geklärt."""
    schon = Arbeitsposten.objects.filter(
        art=ART_PRUEFEN,
        bezug_typ=ContentType.objects.get_for_model(Sollposten),
        bezug_id=posten.pk,
    ).exists()
    if schon:
        return None
    einheiten = ", ".join(e.nw_id for e in vertrag.einheiten.all())
    return kern.arbeitsposten_anlegen(
        ART_PRUEFEN,
        bezug=posten,
        hinweis=(
            f"Vertrag {vertrag.nummer} ({einheiten}): Posten #{posten.pk} "
            f"({posten.get_art_display()} {posten.zeitraum_text}) ist "
            f"{posten.get_status_display()}, passt aber nicht mehr zum Vertrag. "
            "Zahlung prüfen; erst danach lässt er sich stornieren."
        ),
        lauf=lauf,
    )


def _stornieren(posten: Sollposten, grund: str, am: datetime.date) -> None:
    posten.status = Sollposten.Status.STORNIERT
    posten.storniert_am = am
    posten.storno_grund = grund
    posten.save(update_fields=["status", "storniert_am", "storno_grund"])


def sollstellung_erzeugen(
    vertrag: Vertrag, bis_datum: datetime.date, *, lauf: Lauf | None = None
) -> Ergebnis:
    """Erzeugt die fehlenden Sollposten eines Vertrags bis ``bis_datum`` und gleicht die
    vorhandenen mit den Konditionen ab.

    - Es entstehen alle Posten, deren Zeitraum spätestens am ``bis_datum`` beginnt. Gibt es
      schon spätere Posten, werden auch sie geprüft (der Bestand schrumpft nie still).
    - Ein offener Posten, der nicht mehr zum Plan passt (Betrag, Zeitraum, Fälligkeit oder
      ganz weggefallen), wird storniert; der neue Posten zeigt mit ``ersetzt`` auf ihn.
    - Ein teilweise oder ganz bezahlter Posten bleibt, wie er ist. Er wird als
      Arbeitsposten „Sollposten prüfen“ gemeldet, und im selben Zeitraum entsteht nichts
      Neues, bis er geklärt ist.
    """
    with transaction.atomic():
        vertrag = Vertrag.objects.select_for_update().get(pk=vertrag.pk)
        ergebnis = Ergebnis(vertraege=1)
        letzter = _bestand(vertrag).aggregate(m=Max("von"))["m"]
        horizont = max(bis_datum, letzter) if letzter is not None else bis_datum
        soll = plan(vertrag, horizont)
        offen_nach_schluessel = {p.schluessel: p for p in soll}
        uebergang = soll_ab(vertrag)

        passend: set[tuple[str, datetime.date, datetime.date]] = set()
        abweichend: list[Sollposten] = []
        for posten in _bestand(vertrag).order_by("von", "art"):
            if uebergang is not None and uebergang > vertrag.beginn and posten.von < uebergang:
                continue  # vor dem Beginn der Sollstellung: gehört dem alten System
            schluessel = (posten.art, posten.von, posten.bis)
            treffer = offen_nach_schluessel.get(schluessel)
            if treffer is not None and schluessel not in passend and _gleich(posten, treffer):
                passend.add(schluessel)
                ergebnis.unveraendert += 1
            else:
                abweichend.append(posten)

        am = heute()
        for posten in abweichend:
            if posten.status == Sollposten.Status.OFFEN and posten.bezahlt == 0:
                _stornieren(posten, GRUND_AENDERUNG, am)
                ergebnis.storniert.append(posten)
            else:
                ergebnis.abweichend.append(posten)
                gemeldet = _melden(posten, vertrag, lauf)
                if gemeldet is not None:
                    ergebnis.arbeitsposten.append(gemeldet)

        neu = []
        for p in soll:
            if p.schluessel in passend:
                continue
            if any(
                b.art == p.art and rechnung.ueberschneiden(b.von, b.bis, p.von, p.bis)
                for b in ergebnis.abweichend
            ):
                continue  # erst klären, dann neu stellen
            ersetzt = next(
                (
                    s
                    for s in ergebnis.storniert
                    if s.art == p.art and rechnung.ueberschneiden(s.von, s.bis, p.von, p.bis)
                ),
                None,
            )
            neu.append(
                Sollposten(
                    vertrag=vertrag,
                    art=p.art,
                    quelle=Sollposten.Quelle.VERTRAG,
                    von=p.von,
                    bis=p.bis,
                    netto=p.netto,
                    steuersatz=p.steuersatz,
                    steuer=p.steuer,
                    betrag=p.betrag,
                    faellig_am=p.faellig_am,
                    ersetzt=ersetzt,
                )
            )
        ergebnis.neu = Sollposten.objects.bulk_create(neu)
    if ergebnis.neu or ergebnis.storniert or ergebnis.abweichend:
        logger.debug(
            "Sollstellung Vertrag %s: %s neu, %s storniert, %s abweichend",
            vertrag.nummer,
            len(ergebnis.neu),
            len(ergebnis.storniert),
            len(ergebnis.abweichend),
        )
    return ergebnis


def horizont(vertrag: Vertrag) -> datetime.date:
    """Bis wohin eine Änderung das Soll stellt: bis Ende des laufenden Monats, mindestens bis
    Ende des ersten Vertragsmonats (die erste Miete steht mit der Übergabe fest)."""
    return max(fristrechnung.monatsende(heute()), fristrechnung.monatsende(vertrag.beginn))


def vertrag_geaendert(vertrag: Vertrag, *, lauf: Lauf | None = None) -> Ergebnis | None:
    """Haken für jede Änderung am Vertrag: stellt sein Soll neu (siehe ``sollstellung_erzeugen``).

    Rückwirkende Änderungen stornieren betroffene offene Posten und erzeugen neue. Läuft
    nichts in einem Block ``ohne_sollstellung``.
    """
    if _OHNE.get():
        return None
    if vertrag.status not in STATUS_MIT_SOLL and not _bestand(vertrag).exists():
        return Ergebnis()
    return sollstellung_erzeugen(vertrag, horizont(vertrag), lauf=lauf)


def monatslauf(stichtag: datetime.date, *, lauf: Lauf | None = None) -> Ergebnis:
    """Sollstellung für alle Verträge bis zum Ende des Monats von ``stichtag`` (Lauf
    ``soll_monat`` am Monatsersten). Ein Fehler bei einem Vertrag hält die anderen nicht auf."""
    bis = fristrechnung.monatsende(stichtag)
    gesamt = Ergebnis()
    vertraege = Vertrag.objects.filter(status__in=STATUS_MIT_SOLL).order_by("nummer")
    for vertrag in vertraege:
        try:
            with transaction.atomic():
                gesamt.dazu(sollstellung_erzeugen(vertrag, bis, lauf=lauf))
        except SollFehler:
            logger.exception("Sollstellung für Vertrag %s gescheitert", vertrag.nummer)
            gesamt.fehler.append(vertrag.nummer)
    return gesamt


# --- Einzelne Posten -----------------------------------------------------------------


def sollposten_anlegen(
    vertrag: Vertrag,
    art: str,
    *,
    von: datetime.date,
    bis: datetime.date | None = None,
    netto: Decimal,
    steuersatz: Decimal = Decimal("0.00"),
    faellig_am: datetime.date | None = None,
    bezeichnung: str = "",
) -> Sollposten:
    """Ein Posten von Hand: Kaution oder Sonstiges (z. B. Ersatz eines Handsenders).

    Miete und Pauschale entstehen nur aus den Konditionen.
    """
    if art not in (Sollposten.Art.KAUTION, Sollposten.Art.SONSTIGES):
        raise SollFehler(f"Vertrag {vertrag.nummer}: {art} entsteht aus den Konditionen.")
    if netto <= 0:
        raise SollFehler(f"Vertrag {vertrag.nummer}: Der Betrag muss positiv sein.")
    bis = bis or von
    if bis < von:
        raise SollFehler(f"Vertrag {vertrag.nummer}: Der Zeitraum endet vor seinem Beginn.")
    netto = netto.quantize(rechnung.CENT)
    steuer = rechnung.runden(netto * steuersatz / 100)
    posten = Sollposten(
        vertrag=vertrag,
        art=art,
        quelle=Sollposten.Quelle.HAND,
        von=von,
        bis=bis,
        netto=netto,
        steuersatz=steuersatz,
        steuer=steuer,
        betrag=netto + steuer,
        faellig_am=faellig_am or rechnung.faellig_am(von, faelligkeitsregel(vertrag)),
        bezeichnung=bezeichnung,
    )
    if Sollposten.objects.filter(
        vertrag=vertrag, art=art, von=von, bis=bis, status__in=GELTEND
    ).exists():
        raise SollFehler(f"Vertrag {vertrag.nummer}: Für diesen Zeitraum gibt es den Posten schon.")
    posten.save()
    return posten


def sollposten_stornieren(
    posten: Sollposten, grund: str, *, am: datetime.date | None = None
) -> Sollposten:
    """Storniert einen offenen Posten. Bezahltes wird erst storniert, wenn die Zahlung gelöst
    ist (ab Schritt 6 im Abgleich). Posten aus dem Vertrag entstehen beim nächsten Lauf neu,
    solange der Vertrag sie vorsieht."""
    if posten.status == Sollposten.Status.STORNIERT:
        raise SollFehler(f"Sollposten #{posten.pk} ist schon storniert.")
    if posten.bezahlt > 0:
        raise SollFehler(
            f"Sollposten #{posten.pk} ist (teilweise) bezahlt. Erst die Zahlung lösen."
        )
    if not grund.strip():
        raise SollFehler(f"Sollposten #{posten.pk}: Ein Storno braucht einen Grund.")
    _stornieren(posten, grund.strip(), am or heute())
    return posten


# --- Vortrag aus dem alten System (Review 1, Punkt 1) --------------------------------

GRUND_VORTRAG = "Vortrag geändert"


def vortrag(vertrag: Vertrag) -> Sollposten | None:
    """Der geltende Vortrag des Vertrags (offen, teilweise oder ganz bezahlt), sonst None."""
    return (
        Sollposten.objects.filter(vertrag=vertrag, art=Sollposten.Art.VORTRAG, status__in=GELTEND)
        .order_by("-von", "-pk")
        .first()
    )


def vortrag_setzen(
    vertrag: Vertrag,
    betrag: Decimal,
    *,
    faellig_am: datetime.date | None = None,
    am: datetime.date | None = None,
) -> Sollposten | None:
    """Setzt, was beim Umzug aus dem alten System offen war: ein Posten „Vortrag“ je Vertrag.

    - Die Sollstellung des Hof-Systems beginnt am Stichtag des Umzugs (``soll_ab``: Sollregel
      des Vertrags, sonst ``HOF_SOLL_BEGINN``). Ohne diesen Stichtag oder bei einem Vertrag,
      der erst am Stichtag oder später beginnt, gibt es keinen Vortrag (``SollFehler``).
    - Zeitraum des Postens ist der Stichtag selbst (``von = bis = soll_ab``), fällig ist er
      am Stichtag (oder ``faellig_am``). Damit liegt er im Abgleich und ist ab dem Tag nach
      dem Stichtag überfällig, wie jeder Rückstand.
    - Idempotent: Gleicher Betrag und gleiche Fälligkeit ändern nichts. Ein anderer Betrag
      storniert den offenen Vortrag und legt einen neuen an (``ersetzt``); ``betrag = 0``
      storniert ihn nur. Ist auf den Vortrag schon gezahlt, ändert sich nichts
      (``SollFehler``): erst die Zahlung lösen.

    Rückgabe: der geltende Vortrag, bei ``betrag = 0`` None. Meldungen nennen nur IDs.
    """
    stichtag = soll_ab(vertrag)
    if stichtag is None:
        raise SollFehler(
            f"Vertrag {vertrag.nummer}: Ohne Stichtag des Umzugs (HOF_SOLL_BEGINN oder "
            "Sollregel) gibt es keinen Vortrag."
        )
    if vertrag.beginn >= stichtag:
        raise SollFehler(
            f"Vertrag {vertrag.nummer} beginnt erst am Stichtag des Umzugs oder später; "
            "er hat keinen Vortrag."
        )
    if betrag < 0:
        raise SollFehler(f"Vertrag {vertrag.nummer}: Der Vortrag ist nie negativ.")
    betrag = betrag.quantize(rechnung.CENT)
    faellig_am = faellig_am or stichtag
    with transaction.atomic():
        alt = vortrag(vertrag)
        if alt is not None:
            if alt.betrag == betrag and alt.faellig_am == faellig_am and alt.von == stichtag:
                return alt
            if alt.bezahlt > 0:
                raise SollFehler(
                    f"Vertrag {vertrag.nummer}: Auf den Vortrag (Posten #{alt.pk}) ist schon "
                    "gezahlt. Erst die Zahlung lösen."
                )
            _stornieren(alt, GRUND_VORTRAG, am or heute())
        if betrag == 0:
            return None
        posten = Sollposten(
            vertrag=vertrag,
            art=Sollposten.Art.VORTRAG,
            quelle=Sollposten.Quelle.UEBERNAHME,
            von=stichtag,
            bis=stichtag,
            netto=betrag,
            steuersatz=Decimal("0.00"),
            steuer=Decimal("0.00"),
            betrag=betrag,
            faellig_am=faellig_am,
            bezeichnung=f"offen im alten System zum {stichtag:%d.%m.%Y}",
            ersetzt=alt,
        )
        posten.save()
    logger.info("Vortrag für Vertrag %s gesetzt (Posten #%s)", vertrag.nummer, posten.pk)
    return posten


def zahlungsstand_setzen(posten: Sollposten, bezahlt: Decimal) -> Sollposten:
    """Erweiterungsstelle für den Abgleich (Schritt 6): wie viel auf den Posten gezahlt ist.

    Setzt den Status: nichts gezahlt „offen“, alles „bezahlt“, sonst „teilweise“. Was über
    den Betrag hinausgeht, ist Guthaben und gehört nicht an den Posten.
    """
    if posten.status == Sollposten.Status.STORNIERT:
        raise SollFehler(f"Sollposten #{posten.pk} ist storniert.")
    if bezahlt < 0 or bezahlt > posten.betrag:
        raise SollFehler(f"Sollposten #{posten.pk}: Zahlung außerhalb des Betrags.")
    posten.bezahlt = bezahlt
    if bezahlt == 0:
        posten.status = Sollposten.Status.OFFEN
    elif bezahlt == posten.betrag:
        posten.status = Sollposten.Status.BEZAHLT
    else:
        posten.status = Sollposten.Status.TEILWEISE
    posten.save(update_fields=["bezahlt", "status"])
    return posten


# --- Eine Wahrheit für „offen“ (Review 1, Punkt 1) -----------------------------------

NULL = Decimal("0.00")


@dataclass(frozen=True)
class OffenerPosten:
    """Ein offener oder teilweise bezahlter Sollposten mit Soll, bezahlt und offen.

    ``soll`` ist der Zahlbetrag des Postens (``Sollposten.betrag``), ``bezahlt`` die Summe
    der gültigen Zuordnungen aus dem Abgleich (``Sollposten.bezahlt``, gepflegt von
    ``zahlungsstand_setzen``), ``offen`` die Differenz. Immer gilt
    ``soll == bezahlt + offen`` und ``offen > 0``.
    """

    sollposten: Sollposten
    soll: Decimal
    bezahlt: Decimal
    offen: Decimal

    @property
    def vertrag(self) -> Vertrag:
        return self.sollposten.vertrag

    @property
    def faellig_am(self) -> datetime.date:
        return self.sollposten.faellig_am

    def alter(self, stichtag: datetime.date) -> int:
        """Tage seit der Fälligkeit am ``stichtag`` (Altersklassen der Rückstände)."""
        return (stichtag - self.sollposten.faellig_am).days

    @property
    def monat(self) -> str:
        """„10/2026“ bei einem Posten innerhalb eines Monats, sonst der Zeitraum; beim Vortrag
        „bis 30.06.2026“ (der Tag vor dem Stichtag des Umzugs)."""
        p = self.sollposten
        if p.art == Sollposten.Art.VORTRAG:
            return f"bis {p.von - datetime.timedelta(days=1):%d.%m.%Y}"
        if (p.von.year, p.von.month) == (p.bis.year, p.bis.month):
            return f"{p.von:%m/%Y}"
        return p.zeitraum_text

    def kontext(self) -> dict[str, object]:
        """Einfache Werte für Vorlagen (``forderung.posten`` im Mahnwesen)."""
        p = self.sollposten
        return {
            "art": p.get_art_display(),
            "monat": self.monat,
            "zeitraum": p.zeitraum_text,
            "faellig_am": p.faellig_am,
            "soll": self.soll,
            "bezahlt": self.bezahlt,
            "offen": self.offen,
        }


@dataclass(frozen=True)
class Offen:
    """Ergebnis von ``offen()``: die offenen Posten (älteste Fälligkeit zuerst) und Summen.

    ``summe_offen`` ist die Summe von ``posten[i].offen``, ``summe_soll`` und
    ``summe_bezahlt`` entsprechend. ``guthaben`` ist das offene, noch nicht verrechnete
    Guthaben aus dem Abgleich (nur Information, es mindert ``summe_offen`` nicht: Der Abgleich
    verrechnet es selbst, sobald der Zeitraum des nächsten Postens begonnen hat).
    """

    posten: list[OffenerPosten]
    guthaben: Decimal = NULL
    stichtag: datetime.date | None = None

    @property
    def summe_soll(self) -> Decimal:
        return sum((p.soll for p in self.posten), NULL)

    @property
    def summe_bezahlt(self) -> Decimal:
        return sum((p.bezahlt for p in self.posten), NULL)

    @property
    def summe_offen(self) -> Decimal:
        return sum((p.offen for p in self.posten), NULL)

    @property
    def anzahl(self) -> int:
        return len(self.posten)

    @property
    def vertraege(self) -> int:
        return len({p.sollposten.vertrag_id for p in self.posten})

    def je_vertrag(self) -> dict[int, list[OffenerPosten]]:
        """Die Posten nach Vertrag-ID, in der Reihenfolge von ``posten``."""
        ergebnis: dict[int, list[OffenerPosten]] = {}
        for p in self.posten:
            ergebnis.setdefault(p.sollposten.vertrag_id, []).append(p)
        return ergebnis

    def kontext(self) -> dict[str, object]:
        """Einfache Werte für Vorlagen: Posten je Zeile und die drei Summen."""
        return {
            "posten": [p.kontext() for p in self.posten],
            "summe": self.summe_offen,
            "summe_offen": self.summe_offen,
            "summe_soll": self.summe_soll,
            "summe_bezahlt": self.summe_bezahlt,
            "stichtag": self.stichtag,
        }


#: Reihenfolge der offenen Posten: Fälligkeit, Vertragsnummer, Zeitraum, Art, ID.
REIHENFOLGE = ("faellig_am", "vertrag__nummer", "von", "art", "pk")
OFFENE_STATUS = (Sollposten.Status.OFFEN, Sollposten.Status.TEILWEISE)


def offen_abfrage(
    vertrag: Vertrag | None = None, stichtag: datetime.date | None = None
) -> QuerySet[Sollposten]:
    """Die Posten von ``offen()`` als Abfrage, für Seiten, die weiter filtern und summieren
    (Offene Posten: Filter, Zählen und Summe über alle Treffer). Gleiche Regeln wie
    ``offen()``; die Summe offen ist ``Sum(betrag - bezahlt)``."""
    # Spät importiert: ``abgleich`` importiert ``soll`` (Ring beim Laden).
    from apps.abgleich import services as abgleich

    qs = Sollposten.objects.filter(status__in=OFFENE_STATUS).select_related("vertrag")
    anfang = abgleich.beginn()
    if anfang is not None:
        qs = qs.filter(bis__gte=anfang)
    if vertrag is not None:
        qs = qs.filter(vertrag=vertrag)
    if stichtag is not None:
        qs = qs.filter(faellig_am__lt=stichtag)
    return qs


def summen(qs: QuerySet[Sollposten]) -> tuple[int, Decimal]:
    """Anzahl und Summe offen einer Abfrage von Sollposten (stornierte zählen nicht)."""
    werte = qs.exclude(status=Sollposten.Status.STORNIERT).aggregate(
        anzahl=Count("pk"), offen=Sum(F("betrag") - F("bezahlt"))
    )
    return int(werte["anzahl"] or 0), werte["offen"] or NULL


def offen(vertrag: Vertrag | None = None, stichtag: datetime.date | None = None) -> Offen:
    """Die eine Wahrheit für „offen“ (Review 1, Punkt 1): offene Posten und Summen.

    Gemeinsame Schnittstelle für alles, was einen offenen Betrag nennt: Cockpit (Kacheln
    Offene Posten und Rückstand), Seite Offene Posten, ``abgleich.rueckstaende`` (und damit
    der Status-MCP), Saldo im Mietkonto und Vertragskopf, Auslöser des Mahnlaufs
    (``mahnwesen.lagen``) und die Schreiben des Mahnwesens (``mahnwesen.forderung``).

    - ``vertrag``: nur dieser Vertrag; ``None`` alle Verträge.
    - ``stichtag``: nur Posten, die **vor** dem Stichtag fällig waren (Rückstand am
      Stichtag; ein am Stichtag fälliger Posten ist noch nicht überfällig). ``None``: alle
      offenen Posten, auch künftig fällige.
    - Gezählt werden Posten im Status „offen“ und „teilweise bezahlt“, deren Zeitraum nicht
      vor dem Beginn des Abgleichs endet (``abgleich.services.beginn()``): Davor führt das
      alte System die Zahlungen. Was dort beim Umzug offen war, steht als ein Posten
      „Vortrag“ je Vertrag am Stichtag des Umzugs (``vortrag_setzen``).
    - ``bezahlt`` je Posten kommt aus ``Sollposten.bezahlt``, das der Abgleich über
      ``zahlungsstand_setzen`` aus den gültigen Zuordnungen pflegt. ``soll`` hängt deshalb
      nicht von ``abgleich`` ab, außer beim Beginn und beim Guthaben (spät importiert, weil
      ``abgleich`` seinerseits ``soll`` importiert).
    - ``bezahlt`` ist der heutige Stand, nicht der am Stichtag.

    Reihenfolge: Fälligkeit, Vertragsnummer, Zeitraum, Art, ID.
    """
    # Spät importiert: ``abgleich`` importiert ``soll`` (Ring beim Laden).
    from apps.abgleich.models import Guthaben

    qs = offen_abfrage(vertrag, stichtag)
    posten = [
        OffenerPosten(sollposten=p, soll=p.betrag, bezahlt=p.bezahlt, offen=p.offen)
        for p in qs.order_by(*REIHENFOLGE)
        if p.offen > 0
    ]
    guthaben_qs = Guthaben.objects.filter(storniert_am__isnull=True, aufgeloest_am__isnull=True)
    if vertrag is not None:
        guthaben_qs = guthaben_qs.filter(vertrag=vertrag)
    guthaben = guthaben_qs.aggregate(s=Sum("betrag"))["s"] or NULL
    return Offen(posten=posten, guthaben=guthaben, stichtag=stichtag)


# --- Drei Begriffe: überfällig, nicht prüfbar, noch nicht fällig (Review 3, Punkt 1) ----

#: Die drei Teile von „offen“, in dieser Reihenfolge. Erklärungen: ``kern.begriffe``.
TEILE = ("ueberfaellig", "nicht_pruefbar", "noch_nicht_faellig")


def datenstand_bank() -> datetime.date | None:
    """Bis wann die Bankdaten vorliegen (der letzte Bankabruf), ``None`` ohne Bankdaten."""
    # Spät importiert: ``abgleich`` importiert ``soll`` (Ring beim Laden).
    from apps.abgleich import services as abgleich

    return abgleich.bank_datenstand()


def einordnen(
    faellig_am: datetime.date, stichtag: datetime.date, datenstand: datetime.date | None
) -> str:
    """Der Teil von „offen“, zu dem ein offener Posten am ``stichtag`` gehört.

    - ``noch_nicht_faellig``: fällig am Stichtag oder später.
    - ``nicht_pruefbar``: vor dem Stichtag fällig, aber nach dem letzten Bankabruf (oder es
      gibt keine Bankdaten). Ob bezahlt ist, zeigt erst der nächste Abruf; kein Rückstand.
    - ``ueberfaellig``: vor dem Stichtag und spätestens am Datenstand der Bank fällig. Das ist
      der Rückstand (Kachel Rückstand, Seite Rückstände, Mahnwesen).
    """
    if faellig_am >= stichtag:
        return "noch_nicht_faellig"
    if datenstand is None or faellig_am > datenstand:
        return "nicht_pruefbar"
    return "ueberfaellig"


def teil_abfrage(
    teil: str,
    vertrag: Vertrag | None = None,
    stichtag: datetime.date | None = None,
    datenstand: datetime.date | None = None,
) -> QuerySet[Sollposten]:
    """Die Posten von ``offen_abfrage`` in einem Teil (``TEILE``), als Abfrage für Seiten mit
    Filter. ``datenstand`` ohne Angabe: ``datenstand_bank()``. Gleiche Regeln wie
    ``einordnen``."""
    stichtag = stichtag or heute()
    qs = offen_abfrage(vertrag)
    if teil == "noch_nicht_faellig":
        return qs.filter(faellig_am__gte=stichtag)
    datenstand = datenstand if datenstand is not None else datenstand_bank()
    qs = qs.filter(faellig_am__lt=stichtag)
    if teil == "nicht_pruefbar":
        return qs if datenstand is None else qs.filter(faellig_am__gt=datenstand)
    if teil == "ueberfaellig":
        return qs.none() if datenstand is None else qs.filter(faellig_am__lte=datenstand)
    raise ValueError(f"Unbekannter Teil von „offen“: {teil}")


@dataclass(frozen=True)
class Teil:
    """Anzahl und Summe offen eines Teils von „offen“."""

    anzahl: int = 0
    summe: Decimal = NULL


@dataclass(frozen=True)
class Aufteilung:
    """Ergebnis von ``aufteilung()``: offen = überfällig + nicht prüfbar + noch nicht fällig.

    Cockpit (Kacheln Offene Posten und Rückstand), die Seite Offene Posten, die Seite
    Rückstände und die Spalte „offen“ der Einheiten- und Vertragsliste lesen hier.
    """

    stichtag: datetime.date
    datenstand: datetime.date | None
    ueberfaellig: Teil
    nicht_pruefbar: Teil
    noch_nicht_faellig: Teil

    @property
    def offen(self) -> Teil:
        teile = (self.ueberfaellig, self.nicht_pruefbar, self.noch_nicht_faellig)
        return Teil(sum(t.anzahl for t in teile), sum((t.summe for t in teile), NULL))

    def kontext(self) -> dict[str, object]:
        """Einfache Werte für Vorlagen."""
        return {
            "stichtag": self.stichtag,
            "datenstand": self.datenstand,
            "offen": self.offen,
            "ueberfaellig": self.ueberfaellig,
            "nicht_pruefbar": self.nicht_pruefbar,
            "noch_nicht_faellig": self.noch_nicht_faellig,
        }


def aufteilung(vertrag: Vertrag | None = None, stichtag: datetime.date | None = None) -> Aufteilung:
    """Die eine Aufteilung von „offen“ (Review 3, Punkt 1), aus ``offen()``.

    ``stichtag`` ohne Angabe: ``heute()``. „Nicht prüfbar“ heißt: vor dem Stichtag, aber
    nach dem letzten Bankabruf fällig (``datenstand_bank``).
    """
    stichtag = stichtag or heute()
    datenstand = datenstand_bank()
    anzahl = dict.fromkeys(TEILE, 0)
    summe = dict.fromkeys(TEILE, NULL)
    for p in offen(vertrag).posten:
        teil = einordnen(p.faellig_am, stichtag, datenstand)
        anzahl[teil] += 1
        summe[teil] += p.offen
    return Aufteilung(
        stichtag=stichtag,
        datenstand=datenstand,
        ueberfaellig=Teil(anzahl["ueberfaellig"], summe["ueberfaellig"]),
        nicht_pruefbar=Teil(anzahl["nicht_pruefbar"], summe["nicht_pruefbar"]),
        noch_nicht_faellig=Teil(anzahl["noch_nicht_faellig"], summe["noch_nicht_faellig"]),
    )


# --- Rücknahme im Protokoll ----------------------------------------------------------


def _sollposten_zuruecknehmen(ereignis: kern.Ereignis) -> None:
    """„Zurücknehmen“ im Protokoll storniert einen Posten, statt ihn zu löschen."""
    if ereignis.art != "insert":
        raise kern.RuecknahmeNichtMoeglich(
            f"Sollposten #{ereignis.objekt_id}: Nur die Anlage lässt sich zurücknehmen, "
            "und zwar als Storno."
        )
    posten = Sollposten.objects.filter(pk=ereignis.objekt_id).first()
    if posten is None:
        raise kern.RuecknahmeNichtMoeglich(f"Sollposten #{ereignis.objekt_id} gibt es nicht.")
    try:
        sollposten_stornieren(posten, "im Protokoll zurückgenommen")
    except SollFehler as fehler:
        raise kern.RuecknahmeNichtMoeglich(str(fehler)) from fehler


def stornowege_registrieren() -> None:
    """Meldet beim Kern an, dass Rücknahmen von Sollposten als Storno laufen."""
    kern.stornoweg_registrieren(Sollposten, _sollposten_zuruecknehmen)
