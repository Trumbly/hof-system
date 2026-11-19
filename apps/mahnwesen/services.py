"""Schreibweg des Mahnwesens (Schritt 7, H4.1 bis H4.3, H4.6; CLAUDE.md, Regel 4.1).

Das Mahnwesen gilt nur für Verträge im Hof-System; den Wohnraum mahnt Verwaltio. Verträge
mit einer Einheit vom Typ Wohnung lässt der Lauf immer aus.

**Rückstand** (Standardablauf 3, Schritt 1): aus dem laufenden Konto, also aus den offenen
und teilweise bezahlten Posten im Abgleich (``abgleich.posten_im_abgleich``), die vor dem
Stichtag fällig waren. Posten, die nach dem Datenstand der Bank fällig sind, sind nicht
prüfbar und zählen nie (Datenlücke).

**Lauf** ``mahnlauf`` (montags nach dem Abgleich), je Vertrag:

1. Sperren abgleichen: Ein offener Klärfall aus dem Abgleich setzt eine automatische Sperre
   (und hebt sie auf, wenn er geklärt ist). Rechtsfall, Ratenplan und Hand setzt der
   Benutzer (``sperre_setzen``); weitere Quellen meldet ``sperrquelle_anmelden`` an.
2. Belege nachtragen: Ein Schritt ist belegt, wenn sein Schreiben gesendet ist, bei einer
   einseitigen Erklärung erst, wenn es mit Zustellnachweis zugestellt ist. Im Probebetrieb
   belegt der Benutzer von Hand (``schritt_von_hand_belegen``).
3. Ist kein Posten des Falls mehr offen, schließt sich der Fall (bereite Schreiben werden
   verworfen, Arbeitsposten erledigt).
4. Gesperrte Fälle schreiten nicht fort; im Fall steht, warum.
5. Sonst die nächste Stufe, wenn ihr Auslöser erfüllt ist, die vorige belegt ist (keine
   Stufe ohne belegte Vorstufe), ihre Frist abgelaufen ist und für den Vertrag in den
   letzten 14 Tagen kein Schritt derselben Stufe entstand. Je Fall und Lauf höchstens eine
   Stufe.
6. Die Stufe bekommt ein Schreiben im Status „bereit“ und einen Arbeitsposten „Mahnungen
   senden“. Ohne freigegebene Vorlage bleibt sie im Probebetrieb: nur der Arbeitsposten mit
   „Probebetrieb: Vorlage fehlt“. Gibt es die Vorlage später, holt der Lauf das Schreiben
   für denselben Schritt nach.

Der Lauf ist idempotent: Ein zweiter Lauf am selben Stichtag ändert nichts.

Meldungen, Hinweise und Berichte nennen Vertragsnummern, NW-IDs, IDs, Daten und Zähler,
nie Namen oder Beträge je Person.
"""

from __future__ import annotations

import datetime
import logging
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from django.db import transaction
from django.db.models import Q, QuerySet
from django.utils import timezone

from apps.abgleich import services as abgleich
from apps.dokumente import pdf, vorlagenarten
from apps.dokumente import services as dokumente
from apps.dokumente.models import Schreiben, Vorlage
from apps.dokumente.vorlagenarten import Versandweg
from apps.kern import services as kern
from apps.kern.models import Arbeitsposten, Lauf
from apps.mahnwesen import startdaten
from apps.mahnwesen.models import Mahnfall, Mahnschritt, Mahnsperre, Mahnstufe
from apps.soll import services as soll
from apps.soll.models import Sollposten
from apps.stammdaten import fristrechnung, konfiguration
from apps.stammdaten import schreiben as stammdaten_schreiben
from apps.stammdaten import services as stammdaten
from apps.stammdaten.models import LAUFEND, Einheit, Vertrag

logger = logging.getLogger(__name__)

#: Art der Arbeitsposten; steht in ``HOF_NOTION_ARTEN`` und ``HOF_BENACHRICHTIGUNG_ARTEN``.
ART = "Mahnungen senden"
AKTIONEN = ("mahnfall_ansehen", "zurueckstellen")
#: Kein zweites Schreiben derselben Stufe binnen so vieler Tage (H4.1).
SPERRFRIST_TAGE = 14
#: Angenommener Zugang einer Kündigung nach dem Stichtag (für die hilfsweise ordentliche
#: Kündigung und die Vorschau der ordentlichen Kündigung).
ZUGANG_TAGE = 3
#: Vorlagen der ordentlichen Kündigung (H4.6): zuerst je Vertragsart, sonst die allgemeine.
VORLAGE_ORDENTLICH = "Kündigung {titel}"
VORLAGE_ORDENTLICH_STANDARD = "Kündigung durch den Hof"
PROBEBETRIEB = "Probebetrieb: Vorlage fehlt"

NULL = Decimal("0.00")
CENT = Decimal("0.01")
OFFEN = (Sollposten.Status.OFFEN, Sollposten.Status.TEILWEISE)
MONATSPOSTEN = (Sollposten.Art.MIETE, Sollposten.Art.PAUSCHALE)


class MahnwesenFehler(ValueError):
    """Eine Änderung im Mahnwesen ist so nicht möglich. Die Meldung nennt nur IDs."""


def heute() -> datetime.date:
    """Das heutige Datum des Mahnwesens (in Tests austauschbar)."""
    return timezone.localdate()


def _nw_ids(vertrag: Vertrag) -> list[str]:
    return sorted(e.nw_id for e in vertrag.einheiten.all())


def fall_kennungen(fall: Mahnfall) -> list[str]:
    """NW-IDs der Einheiten des Vertrags (Aufgaben im Hof-Backlog). Nur IDs, nie Namen."""
    return _nw_ids(fall.vertrag)


def _vertrag_text(vertrag: Vertrag) -> str:
    ids = ", ".join(_nw_ids(vertrag))
    return f"Vertrag {vertrag.nummer}" + (f" ({ids})" if ids else "")


# --- Stufen --------------------------------------------------------------------------

STUFE_FELDER = (
    "titel",
    "ausloeser",
    "ausloeser_wert",
    "frist_tage",
    "vorlagenart",
    "vorlage_name",
    "versandweg",
    "einseitige_erklaerung",
    "aktiv",
)


def startdaten_anlegen() -> int:
    """Legt die Stufen nach der SOP für jede Vertragsart an, die noch keine hat
    (``apps.mahnwesen.startdaten``). Idempotent. Rückgabe: Anzahl neuer Stufen."""
    vorhanden = set(Mahnstufe.objects.values_list("vertragsart", flat=True))
    neu = 0
    for felder in startdaten.fehlende(vorhanden):
        stufe = Mahnstufe(**felder)
        stufe.full_clean()
        stufe.save()
        neu += 1
    return neu


def stufe_aendern(stufe: Mahnstufe, **felder: Any) -> Mahnstufe:
    """Ändert eine Stufe (Seite „Stufen“). Vertragsart, Reihenfolge und Schlüssel bleiben."""
    unbekannt = set(felder) - set(STUFE_FELDER)
    if unbekannt:
        raise MahnwesenFehler(f"Mahnstufe #{stufe.pk}: Felder nicht änderbar: {sorted(unbekannt)}")
    if felder.get("versandweg", stufe.versandweg) not in Versandweg.TITEL:
        raise MahnwesenFehler(f"Mahnstufe #{stufe.pk}: unbekannter Versandweg.")
    for name, wert in felder.items():
        setattr(stufe, name, wert)
    stufe.full_clean()
    stufe.save()
    return stufe


def stufen(vertragsart: str) -> list[Mahnstufe]:
    """Die aktiven Stufen einer Vertragsart in ihrer Reihenfolge."""
    return list(
        Mahnstufe.objects.filter(vertragsart=vertragsart, aktiv=True).order_by("reihenfolge")
    )


# --- Sperren -------------------------------------------------------------------------

#: Eine Quelle automatischer Sperren: Stichtag → {Vertrag-ID: Hinweis (nur IDs)}.
Sperrquelle = Callable[[datetime.date], Mapping[int, str]]
_SPERRQUELLEN: dict[str, Sperrquelle] = {}


def sperrquelle_anmelden(grund: str, funktion: Sperrquelle) -> None:
    """Meldet eine Quelle automatischer Sperren an (z. B. Rechtsfälle aus H4.5, v2).

    Der Lauf setzt für jeden genannten Vertrag eine automatische Sperre mit diesem Grund und
    hebt sie auf, sobald die Quelle ihn nicht mehr nennt.
    """
    if grund not in Mahnsperre.Grund.values:
        raise ValueError(f"Unbekannter Grund einer Mahnsperre: {grund}")
    _SPERRQUELLEN[grund] = funktion


def _klaerfaelle(stichtag: datetime.date) -> dict[int, str]:
    return {pk: "offener Klärfall im Abgleich" for pk in abgleich.vertraege_in_klaerung()}


sperrquelle_anmelden(Mahnsperre.Grund.KLAERFALL, _klaerfaelle)


def automatische_sperren_aufheben(grund: str, tag: datetime.date) -> int:
    """Hebt automatische Sperren auf, die ihre Quelle nicht mehr nennt (ohne neue zu setzen).
    Rückgabe: Anzahl."""
    quelle = _SPERRQUELLEN.get(grund)
    if quelle is None:
        return 0
    genannt = set(quelle(tag))
    aufgehoben = 0
    for sperre in gueltige_sperren(tag).filter(grund=grund, automatisch=True).order_by("pk"):
        if sperre.vertrag_id not in genannt:
            sperre_aufheben(sperre, tag)
            logger.info("Mahnsperre #%s aufgehoben: Klärfall geklärt", sperre.pk)
            aufgehoben += 1
    return aufgehoben


def _nach_klaerung(_klaerfall: Any) -> None:
    """Review 2, A11: Ist der Klärfall geklärt, endet seine Sperre sofort."""
    automatische_sperren_aufheben(Mahnsperre.Grund.KLAERFALL, heute())


abgleich.nach_klaerung_anmelden("mahnwesen", _nach_klaerung)


def gueltige_sperren(tag: datetime.date) -> QuerySet[Mahnsperre]:
    """Sperren, die am ``tag`` gelten."""
    return Mahnsperre.objects.filter(von__lte=tag).filter(
        Q(bis__isnull=True) | Q(bis__gte=tag),
        Q(aufgehoben_am__isnull=True) | Q(aufgehoben_am__gt=tag),
    )


def sperre_setzen(
    vertrag: Vertrag,
    grund: str,
    *,
    von: datetime.date,
    bis: datetime.date | None = None,
    hinweis: str = "",
    automatisch: bool = False,
) -> Mahnsperre:
    """Setzt eine Sperre. Ein offener Fall des Vertrags hält sofort an: Bereite Schreiben
    werden verworfen, damit keine Mahnung an einen gesperrten Vertrag hinausgeht."""
    if grund not in Mahnsperre.Grund.values:
        raise MahnwesenFehler(f"Unbekannter Grund einer Mahnsperre: {grund}")
    if bis is not None and bis < von:
        raise MahnwesenFehler("Eine Sperre endet nicht vor ihrem Beginn.")
    with transaction.atomic():
        sperre = Mahnsperre(
            vertrag=vertrag,
            grund=grund,
            von=von,
            bis=bis,
            hinweis=hinweis,
            automatisch=automatisch,
        )
        sperre.full_clean()
        sperre.save()
        if sperre.gilt_am(von):
            fall = offener_fall(vertrag)
            if fall is not None:
                _fall_sperren(fall, sperrgruende(vertrag, von), von)
    logger.info("Mahnsperre #%s (%s) für Vertrag %s", sperre.pk, grund, vertrag.nummer)
    return sperre


def sperre_aufheben(sperre: Mahnsperre, am: datetime.date) -> Mahnsperre:
    """Hebt eine Sperre zum ``am`` auf. Der Fall schreitet beim nächsten Lauf fort."""
    if sperre.aufgehoben_am is not None:
        raise MahnwesenFehler(f"Mahnsperre #{sperre.pk} ist schon aufgehoben.")
    sperre.aufgehoben_am = am
    sperre.full_clean()
    sperre.save(update_fields=["aufgehoben_am"])
    return sperre


def sperren_abgleichen(stichtag: datetime.date) -> tuple[int, int]:
    """Setzt und hebt die automatischen Sperren nach ihren Quellen. Rückgabe: (gesetzt,
    aufgehoben)."""
    gesetzt = aufgehoben = 0
    for grund, quelle in _SPERRQUELLEN.items():
        genannt = dict(quelle(stichtag))
        aktiv = {
            s.vertrag_id: s
            for s in gueltige_sperren(stichtag).filter(grund=grund, automatisch=True)
        }
        for vertrag_id, sperre in aktiv.items():
            # Schon aufgehoben (etwa sofort beim Klären, Review 2, A11): nichts mehr zu tun.
            if vertrag_id not in genannt and sperre.aufgehoben_am is None:
                sperre_aufheben(sperre, stichtag)
                aufgehoben += 1
        for vertrag_id, hinweis in sorted(genannt.items()):
            if vertrag_id in aktiv:
                continue
            vertrag = Vertrag.objects.filter(pk=vertrag_id).first()
            if vertrag is None:
                continue
            sperre_setzen(vertrag, grund, von=stichtag, hinweis=hinweis, automatisch=True)
            gesetzt += 1
    return gesetzt, aufgehoben


def sperrgruende(vertrag: Vertrag, tag: datetime.date) -> list[str]:
    """Warum ein Vertrag am ``tag`` gesperrt ist: Grund und ID je Sperre (keine Namen)."""
    return [
        f"{s.get_grund_display()} (Sperre #{s.pk}"
        + (f" bis {s.bis:%d.%m.%Y}" if s.bis else "")
        + ")"
        for s in gueltige_sperren(tag).filter(vertrag=vertrag).order_by("von", "pk")
    ]


# --- Rückstand je Vertrag ------------------------------------------------------------


@dataclass
class Lage:
    """Rückstand eines Vertrags am Stichtag: offene, prüfbare, überfällige Posten."""

    vertrag: Vertrag
    posten: list[Sollposten] = field(default_factory=list)
    monatsmiete: Decimal = NULL

    @property
    def aelteste_faelligkeit(self) -> datetime.date | None:
        return min((p.faellig_am for p in self.posten), default=None)

    @property
    def rueckstand(self) -> Decimal:
        return sum((p.offen for p in self.posten), NULL)

    @property
    def monatsposten(self) -> list[Sollposten]:
        return [p for p in self.posten if p.art in MONATSPOSTEN]

    @property
    def rueckstand_miete(self) -> Decimal:
        """Offene Miete und Pauschale (ohne Kaution und Sonstiges)."""
        return sum((p.offen for p in self.monatsposten), NULL)

    @property
    def offene_monatsmieten(self) -> int:
        """Anzahl der Monate mit offener Miete oder Pauschale (ein Quartalsposten deckt drei)."""
        monate: set[tuple[int, int]] = set()
        for p in self.monatsposten:
            if p.offen <= 0:
                continue
            tag = p.von.replace(day=1)
            while tag <= p.bis:
                monate.add((tag.year, tag.month))
                tag = fristrechnung.monate_addieren(tag, 1)
        return len(monate)

    @property
    def rueckstand_in_monatsmieten(self) -> Decimal:
        if self.monatsmiete <= 0:
            return NULL
        return (self.rueckstand_miete / self.monatsmiete).quantize(CENT)


def _monatsmiete(vertrag: Vertrag, stichtag: datetime.date, posten: list[Sollposten]) -> Decimal:
    """Monatsmiete brutto (Miete und Pauschale mit Steuer) der Kondition am Stichtag."""
    kondition = (
        vertrag.kondition_am(stichtag) or vertrag.konditionen.order_by("-gueltig_ab").first()
    )
    if kondition is not None:
        netto = kondition.miete + kondition.pauschale
        return (netto * (1 + kondition.steuersatz / 100)).quantize(CENT)
    return max((p.betrag for p in posten if p.art in MONATSPOSTEN), default=NULL)


def _ohne_wohnraum(qs: QuerySet[Sollposten]) -> QuerySet[Sollposten]:
    """Den Wohnraum mahnt Verwaltio: Verträge mit einer Wohnung bleiben immer außen vor."""
    return qs.exclude(vertrag__einheiten__typ=Einheit.Typ.WOHNUNG)


def lagen(stichtag: datetime.date) -> dict[int, Lage]:
    """Rückstand je Vertrag am Stichtag (nur Verträge mit überfälligen, prüfbaren Posten)."""
    datenstand = abgleich.bank_datenstand()
    if datenstand is None:
        return {}
    posten = (
        _ohne_wohnraum(abgleich.posten_im_abgleich())
        .filter(faellig_am__lt=stichtag, faellig_am__lte=datenstand)
        .select_related("vertrag")
        .order_by("vertrag__nummer", "faellig_am", "von", "pk")
    )
    ergebnis: dict[int, Lage] = {}
    for p in posten:
        lage = ergebnis.get(p.vertrag_id)
        if lage is None:
            lage = ergebnis[p.vertrag_id] = Lage(vertrag=p.vertrag)
        lage.posten.append(p)
    for lage in ergebnis.values():
        lage.monatsmiete = _monatsmiete(lage.vertrag, stichtag, lage.posten)
    return ergebnis


def lage_von(vertrag: Vertrag, stichtag: datetime.date) -> Lage:
    return lagen(stichtag).get(vertrag.pk) or Lage(vertrag=vertrag)


def ausloeser_erfuellt(
    stufe: Mahnstufe, lage: Lage, fall: Mahnfall | None, stichtag: datetime.date
) -> bool:
    """Ob der Auslöser einer Stufe am Stichtag erfüllt ist."""
    if not lage.posten:
        return False
    wert = stufe.ausloeser_wert
    art = stufe.ausloeser
    vorstufe = fall.stufe_belegt_am if fall is not None else None
    if art == Mahnstufe.Ausloeser.TAGE_NACH_VORSTUFE and vorstufe is not None:
        return (stichtag - vorstufe).days >= wert
    if art in (Mahnstufe.Ausloeser.TAGE_NACH_FAELLIGKEIT, Mahnstufe.Ausloeser.TAGE_NACH_VORSTUFE):
        aelteste = lage.aelteste_faelligkeit
        return aelteste is not None and (stichtag - aelteste).days >= wert
    if art == Mahnstufe.Ausloeser.OFFENE_MONATSMIETEN:
        return lage.offene_monatsmieten >= wert
    if art == Mahnstufe.Ausloeser.RUECKSTAND_MONATSMIETEN:
        return lage.monatsmiete > 0 and lage.rueckstand_miete >= wert * lage.monatsmiete
    raise MahnwesenFehler(f"Mahnstufe #{stufe.pk}: unbekannter Auslöser {art}.")


# --- Fälle ---------------------------------------------------------------------------


def offener_fall(vertrag: Vertrag) -> Mahnfall | None:
    return Mahnfall.objects.exclude(status=Mahnfall.Status.ERLEDIGT).filter(vertrag=vertrag).first()


def letzter_schritt(fall: Mahnfall) -> Mahnschritt | None:
    return fall.schritte.select_related("schreiben", "stufe").order_by("-stichtag", "-pk").first()


def naechste_stufe(fall: Mahnfall, stichtag: datetime.date) -> Mahnstufe | None:
    """Die Stufe, die als nächste kommen darf (ohne Auslöser): die erste; dieselbe, wenn die
    aktuelle nicht belegt ist (Schreiben verworfen); sonst die folgende, sobald die Frist der
    belegten Stufe abgelaufen ist."""
    folge = stufen(fall.vertrag.vertragsart)
    if not folge:
        return None
    if fall.stufe is None:
        return folge[0]
    if fall.stufe_belegt_am is None:
        return fall.stufe if fall.stufe.aktiv else None
    if fall.naechste_stufe_fruehestens is not None and stichtag < fall.naechste_stufe_fruehestens:
        return None
    spaeter = [s for s in folge if s.reihenfolge > fall.stufe.reihenfolge]
    return spaeter[0] if spaeter else None


def _kuerzlich(vertrag: Vertrag, stufe: Mahnstufe, stichtag: datetime.date) -> bool:
    """Ob für den Vertrag in den letzten 14 Tagen ein Schritt derselben Stufe entstand."""
    return Mahnschritt.objects.filter(
        fall__vertrag=vertrag,
        stufe__schluessel=stufe.schluessel,
        stichtag__gt=stichtag - datetime.timedelta(days=SPERRFRIST_TAGE),
        stichtag__lte=stichtag,
    ).exists()


def _speichern(objekt: Mahnfall | Mahnschritt) -> None:
    objekt.full_clean()
    objekt.save()


def _fall_anlegen(lage: Lage, stichtag: datetime.date) -> Mahnfall:
    fall = Mahnfall(vertrag=lage.vertrag, eroeffnet_am=stichtag)
    _speichern(fall)
    fall.offene_posten.add(*lage.posten)
    logger.info("Mahnfall #%s für Vertrag %s eröffnet", fall.pk, lage.vertrag.nummer)
    return fall


def _posten_erledigen(schritt: Mahnschritt) -> None:
    posten = schritt.arbeitsposten
    if posten is not None and posten.status != Arbeitsposten.Status.ERLEDIGT:
        kern.arbeitsposten_erledigen(posten)


def _schritt_abbrechen(schritt: Mahnschritt, tag: datetime.date, grund: str) -> None:
    """Ein offener Schritt ohne gesendetes Schreiben endet (Fall bezahlt oder gesperrt):
    Ein bereites Schreiben wird verworfen, der Arbeitsposten erledigt."""
    if schritt.schreiben is not None:
        schritt.schreiben.refresh_from_db()
        if schritt.schreiben.status in (Schreiben.Status.GESENDET, Schreiben.Status.ZUGESTELLT):
            return  # schon unterwegs: Es kann noch belegt werden.
        if schritt.schreiben.status == Schreiben.Status.BEREIT:
            dokumente.schreiben_verwerfen(schritt.schreiben)
    schritt.abgebrochen_am = tag
    schritt.hinweis = ((f"{schritt.hinweis} · " if schritt.hinweis else "") + grund)[:300]
    _speichern(schritt)
    _posten_erledigen(schritt)


def _offene_schritte(fall: Mahnfall) -> list[Mahnschritt]:
    return [s for s in fall.schritte.select_related("schreiben", "arbeitsposten") if s.offen]


def _verworfene_abschliessen(fall: Mahnfall, tag: datetime.date) -> None:
    """Hat der Benutzer ein Schreiben verworfen, endet sein Schritt; die Stufe kommt nach der
    Sperrfrist von 14 Tagen erneut."""
    for schritt in fall.schritte.filter(
        belegt_am__isnull=True,
        abgebrochen_am__isnull=True,
        schreiben__status=Schreiben.Status.VERWORFEN,
    ).select_related("schreiben", "arbeitsposten"):
        _schritt_abbrechen(schritt, tag, "Schreiben verworfen")


def _fall_sperren(fall: Mahnfall, gruende: list[str], tag: datetime.date) -> None:
    fall.status = Mahnfall.Status.GESPERRT
    fall.sperrgrund = "; ".join(gruende)[:300]
    _speichern(fall)
    for schritt in _offene_schritte(fall):
        _schritt_abbrechen(schritt, tag, f"gesperrt am {tag:%d.%m.%Y}")


def _fall_schliessen(fall: Mahnfall, tag: datetime.date) -> None:
    fall.status = Mahnfall.Status.ERLEDIGT
    fall.geschlossen_am = tag
    fall.sperrgrund = ""
    _speichern(fall)
    for schritt in _offene_schritte(fall):
        _schritt_abbrechen(schritt, tag, f"bezahlt, Fall erledigt am {tag:%d.%m.%Y}")
    logger.info("Mahnfall #%s erledigt (Vertrag %s)", fall.pk, fall.vertrag.nummer)


def _schritt_belegen(schritt: Mahnschritt, am: datetime.date, vermerk: str = "") -> None:
    schritt.belegt_am = am
    if vermerk:
        schritt.vermerk = vermerk
    _speichern(schritt)
    fall = schritt.fall
    if fall.stufe_id == schritt.stufe_id:
        fall.stufe_belegt_am = am
        fall.naechste_stufe_fruehestens = am + datetime.timedelta(days=schritt.stufe.frist_tage)
        _speichern(fall)
    _posten_erledigen(schritt)


def _beleg_aus_schreiben(schritt: Mahnschritt) -> datetime.date | None:
    """Wann das Schreiben eines Schritts die Stufe belegt hat (oder None)."""
    schreiben = schritt.schreiben
    if schreiben is None:
        return None
    if schritt.stufe.einseitige_erklaerung or schreiben.einseitige_erklaerung:
        if schreiben.status == Schreiben.Status.ZUGESTELLT and schreiben.hat_zustellnachweis:
            return schreiben.zugestellt_am
        return None
    if schreiben.status in (Schreiben.Status.GESENDET, Schreiben.Status.ZUGESTELLT):
        gesendet = schreiben.gesendet_am
        return timezone.localdate(gesendet) if gesendet else schreiben.zugestellt_am
    return None


def belege_pruefen(fall: Mahnfall) -> int:
    """Trägt nach, welche Schritte inzwischen belegt sind. Rückgabe: Anzahl."""
    neu = 0
    for schritt in fall.schritte.filter(belegt_am__isnull=True, schreiben__isnull=False).order_by(
        "stichtag", "pk"
    ):
        am = _beleg_aus_schreiben(schritt)
        if am is not None:
            _schritt_belegen(schritt, am)
            neu += 1
    return neu


def schritt_von_hand_belegen(schritt: Mahnschritt, *, am: datetime.date, vermerk: str = "") -> None:
    """Belegt einen Schritt ohne Schreiben (Probebetrieb: Mahnung auf altem Weg verschickt).

    Bei einer einseitigen Erklärung ist der Vermerk zum Zustellnachweis Pflicht.
    """
    vermerk = vermerk.strip()
    if schritt.schreiben_id is not None:
        raise MahnwesenFehler(
            f"Mahnschritt #{schritt.pk} hat ein Schreiben; belegt wird über dessen Versand."
        )
    if not schritt.offen:
        raise MahnwesenFehler(f"Mahnschritt #{schritt.pk} ist nicht mehr offen.")
    if schritt.stufe.einseitige_erklaerung and not vermerk:
        raise MahnwesenFehler(
            f"Mahnschritt #{schritt.pk}: einseitige Erklärung, ohne Vermerk zum "
            "Zustellnachweis nicht belegt."
        )
    with transaction.atomic():
        _schritt_belegen(schritt, am, vermerk or "von Hand belegt")


# --- Schreiben -----------------------------------------------------------------------


def pruefstichtag(stichtag: datetime.date) -> datetime.date:
    """Bis wohin das Mahnwesen prüfen kann: Posten, die vor dem Stichtag und spätestens am
    Bankdatenstand fällig waren (wie ``lagen``). Als ``stichtag`` für ``soll.offen``."""
    datenstand = abgleich.bank_datenstand()
    if datenstand is None:
        return stichtag
    return min(stichtag, datenstand + datetime.timedelta(days=1))


def forderung(vertrag: Vertrag, stichtag: datetime.date) -> soll.Offen:
    """Die offenen Posten eines Mahnschreibens: allein aus ``soll.offen`` (Review 1, Punkt 1;
    Review 2, A2). Text und Tabelle lesen beide diese Werte, deshalb stimmt die Summe der
    Tabelle immer mit dem Betrag im Text überein."""
    return soll.offen(vertrag, pruefstichtag(stichtag))


def kontext_mahnung(
    fall: Mahnfall, stufe: Mahnstufe, lage: Lage, stichtag: datetime.date
) -> dict[str, Any]:
    """Platzhalter eines Schreibens im Mahnwesen: Vertrag (wie die Stammdaten), Betrag,
    Aufstellung je Posten, Frist, Stufe; bei der Kündigung die Fristen.

    Betrag, Aufstellung und Zahl der offenen Monatsmieten kommen allein aus
    ``soll.offen`` (``forderung``): je Posten ``soll``, ``bezahlt`` und ``offen``, dazu
    ``forderung.summe_soll``, ``forderung.summe_bezahlt`` und ``forderung.summe`` (offen,
    gleich ``betrag.offen``). Aus der ``lage`` kommt nur die Monatsmiete."""
    vertrag = fall.vertrag
    kontext = stammdaten_schreiben.kontext_vertrag(vertrag, stichtag)
    frist_bis = stichtag + datetime.timedelta(days=stufe.frist_tage)
    stand = forderung(vertrag, stichtag)
    brief = Lage(
        vertrag=vertrag,
        posten=[p.sollposten for p in stand.posten],
        monatsmiete=lage.monatsmiete,
    )
    kontext["betrag"] = {
        **kontext["betrag"],
        "offen": stand.summe_offen,
        "miete_offen": brief.rueckstand_miete,
        "monatsmiete": brief.monatsmiete,
    }
    kontext["forderung"] = {**stand.kontext(), "stichtag": stichtag}
    kontext["frist"] = {"tage": stufe.frist_tage, "bis": frist_bis}
    kontext["mahnung"] = {
        "stufe": stufe.titel,
        "schluessel": stufe.schluessel,
        "stichtag": stichtag,
        "offene_monatsmieten": brief.offene_monatsmieten,
        "rueckstand_monatsmieten": brief.rueckstand_in_monatsmieten,
        "fall": fall.pk,
    }
    if stufe.vorlagenart == "kuendigung":
        zugang = stichtag + datetime.timedelta(days=ZUGANG_TAGE)
        regel = stammdaten.kuendigungsregel_fuer(vertrag)
        kontext["kuendigung"] = {
            "art": "fristlos, hilfsweise ordentlich",
            "zugang_angenommen": zugang,
            "fristende_ordentlich": fristrechnung.fristende(zugang, regel),
            "kuendigungsfrist": regel.titel,
            "raeumung_bis": frist_bis,
        }
    return kontext


def _versandweg(stufe: Mahnstufe, kontext: Mapping[str, Any]) -> str:
    """Versandweg der Stufe; eine Mail ohne Adresse geht als Brief, wenn eine Anschrift da ist."""
    empfaenger = kontext["empfaenger"]
    if stufe.versandweg == Versandweg.MAIL and not empfaenger["email"] and empfaenger["anschrift"]:
        return Versandweg.BRIEF
    return stufe.versandweg


@dataclass
class Versuch:
    schreiben: Schreiben | None
    hinweis: str = ""
    probebetrieb: bool = False


def _probebetrieb_hinweis(stufe: Mahnstufe) -> str:
    name = f", Name „{stufe.vorlage_name}“" if stufe.vorlage_name else ""
    return f"{PROBEBETRIEB} (Art {stufe.vorlagenart}{name})"


def _schreiben_versuchen(
    fall: Mahnfall, stufe: Mahnstufe, lage: Lage, stichtag: datetime.date
) -> Versuch:
    """Erzeugt das Schreiben einer Stufe. Ohne freigegebene Vorlage: Probebetrieb."""
    try:
        vorlage = dokumente.freigegebene_vorlage(stufe.vorlagenart, stufe.vorlage_name or None)
    except dokumente.VorlageNichtFreigegeben:
        return Versuch(None, _probebetrieb_hinweis(stufe), probebetrieb=True)
    kontext = kontext_mahnung(fall, stufe, lage, stichtag)
    if stufe.ist_brief and not kontext["empfaenger"]["anschrift"]:
        return Versuch(None, "Schreiben nicht erzeugt: Anschrift des Mieters fehlt (Brief).")
    try:
        with transaction.atomic():
            schreiben = dokumente.schreiben_erzeugen(
                vorlage, fall, kontext, _versandweg(stufe, kontext)
            )
    except (dokumente.SchreibenFehler, dokumente.PlatzhalterFehler, pdf.PdfNichtVerfuegbar) as f:
        return Versuch(None, f"Schreiben nicht erzeugt: {f}"[:300])
    return Versuch(schreiben)


def _posten_hinweis(schritt: Mahnschritt) -> str:
    teile = [f"{_vertrag_text(schritt.fall.vertrag)}: {schritt.stufe.titel}"]
    if schritt.schreiben is not None:
        weg = Versandweg.TITEL.get(schritt.schreiben.versandweg, schritt.schreiben.versandweg)
        teile.append(f"Schreiben #{schritt.schreiben.pk} bereit ({weg})")
    if schritt.hinweis:
        teile.append(schritt.hinweis)
    if schritt.stufe.anwalt_bestaetigung:
        teile.append(f"Kündigung: {vorlagenarten.ANWALT_HINWEIS}")
    if schritt.stufe.einseitige_erklaerung:
        teile.append("belegt erst mit Zustellnachweis")
    return ". ".join(teile) + "."


@dataclass
class Ergebnis:
    """Zähler eines Mahnlaufs (nur Zähler, keine Namen)."""

    stichtag: datetime.date
    faelle_neu: int = 0
    offen: int = 0
    gesperrt: int = 0
    erledigt: int = 0
    schreiben: int = 0
    probebetrieb: int = 0
    ohne_schreiben: int = 0
    belegt: int = 0
    sperren_gesetzt: int = 0
    sperren_aufgehoben: int = 0
    arbeitsposten: int = 0
    ohne_stufen: list[str] = field(default_factory=list)

    def text(self) -> str:
        teile = [
            f"Stichtag {self.stichtag:%d.%m.%Y}: {self.offen} Fälle offen "
            f"({self.faelle_neu} neu), {self.gesperrt} gesperrt, {self.erledigt} erledigt",
            f"{self.schreiben} Schreiben bereit, {self.probebetrieb} im Probebetrieb, "
            f"{self.ohne_schreiben} ohne Schreiben, {self.belegt} Stufen belegt",
            f"Sperren {self.sperren_gesetzt} gesetzt, {self.sperren_aufgehoben} aufgehoben",
        ]
        if self.ohne_stufen:
            teile.append(f"ohne Stufen: Vertragsart {', '.join(sorted(set(self.ohne_stufen)))}")
        return "; ".join(teile)


def _schritt_anlegen(
    fall: Mahnfall,
    stufe: Mahnstufe,
    lage: Lage,
    stichtag: datetime.date,
    lauf: Lauf | None,
    ergebnis: Ergebnis,
) -> Mahnschritt:
    versuch = _schreiben_versuchen(fall, stufe, lage, stichtag)
    schritt = Mahnschritt(
        fall=fall,
        stufe=stufe,
        stichtag=stichtag,
        schreiben=versuch.schreiben,
        probebetrieb=versuch.probebetrieb,
        hinweis=versuch.hinweis,
        lauf=lauf,
    )
    _speichern(schritt)
    fall.stufe = stufe
    fall.stufe_belegt_am = None
    fall.naechste_stufe_fruehestens = None
    _speichern(fall)
    schritt.arbeitsposten = kern.arbeitsposten_anlegen(
        ART,
        bezug=fall,
        aktionen=AKTIONEN,
        faellig_am=stichtag,
        hinweis=_posten_hinweis(schritt),
        lauf=lauf,
    )
    _speichern(schritt)
    ergebnis.arbeitsposten += 1
    if versuch.schreiben is not None:
        ergebnis.schreiben += 1
    elif versuch.probebetrieb:
        ergebnis.probebetrieb += 1
    else:
        ergebnis.ohne_schreiben += 1
    logger.info(
        "Mahnfall #%s: Stufe %s (Schritt #%s, Schreiben #%s)",
        fall.pk,
        stufe.schluessel,
        schritt.pk,
        versuch.schreiben.pk if versuch.schreiben else "-",
    )
    return schritt


def _schreiben_nachholen(
    schritt: Mahnschritt, lage: Lage, stichtag: datetime.date, ergebnis: Ergebnis
) -> None:
    """Ein offener Schritt ohne Schreiben bekommt es, sobald es geht (Vorlage freigegeben)."""
    versuch = _schreiben_versuchen(schritt.fall, schritt.stufe, lage, stichtag)
    if versuch.schreiben is None:
        if versuch.hinweis != schritt.hinweis:
            schritt.hinweis = versuch.hinweis
            schritt.probebetrieb = versuch.probebetrieb
            _speichern(schritt)
        return
    schritt.schreiben = versuch.schreiben
    schritt.probebetrieb = False
    schritt.hinweis = ""
    _speichern(schritt)
    ergebnis.schreiben += 1
    if schritt.arbeitsposten is not None:
        kern.arbeitsposten_hinweis_setzen(schritt.arbeitsposten, _posten_hinweis(schritt))


def _fortschreiben(
    fall: Mahnfall,
    lage: Lage | None,
    stichtag: datetime.date,
    lauf: Lauf | None,
    ergebnis: Ergebnis,
) -> None:
    ergebnis.belegt += belege_pruefen(fall)
    _verworfene_abschliessen(fall, stichtag)
    fall.refresh_from_db()
    if lage is not None:
        fall.offene_posten.add(*lage.posten)
    if lage is None and not fall.offene_posten.filter(status__in=OFFEN).exists():
        _fall_schliessen(fall, stichtag)
        ergebnis.erledigt += 1
        return

    gruende = sperrgruende(fall.vertrag, stichtag)
    if gruende:
        _fall_sperren(fall, gruende, stichtag)
        ergebnis.gesperrt += 1
        return
    if fall.status != Mahnfall.Status.OFFEN or fall.sperrgrund:
        fall.status = Mahnfall.Status.OFFEN
        fall.sperrgrund = ""
        _speichern(fall)
    ergebnis.offen += 1
    if lage is None:
        return  # offene Posten, aber derzeit keiner prüfbar überfällig

    letzter = letzter_schritt(fall)
    if letzter is not None and letzter.offen:
        if letzter.schreiben is None:
            _schreiben_nachholen(letzter, lage, stichtag, ergebnis)
        return  # keine Stufe ohne belegte Vorstufe
    stufe = naechste_stufe(fall, stichtag)
    if stufe is None or not ausloeser_erfuellt(stufe, lage, fall, stichtag):
        return
    if _kuerzlich(fall.vertrag, stufe, stichtag):
        return
    _schritt_anlegen(fall, stufe, lage, stichtag, lauf, ergebnis)


def mahnlauf(stichtag: datetime.date | None = None, *, lauf: Lauf | None = None) -> Ergebnis:
    """Der Lauf ``mahnlauf`` (Standardablauf 3): Fälle fortschreiben, je fälliger Stufe ein
    Schreiben „bereit“ und ein Arbeitsposten „Mahnungen senden“. Idempotent."""
    stichtag = stichtag or heute()
    ergebnis = Ergebnis(stichtag=stichtag)
    ergebnis.sperren_gesetzt, ergebnis.sperren_aufgehoben = sperren_abgleichen(stichtag)
    rueckstaende = lagen(stichtag)

    for fall in (
        Mahnfall.objects.exclude(status=Mahnfall.Status.ERLEDIGT)
        .select_related("vertrag", "stufe")
        .order_by("vertrag__nummer")
    ):
        with transaction.atomic():
            _fortschreiben(fall, rueckstaende.pop(fall.vertrag_id, None), stichtag, lauf, ergebnis)

    for _, eintrag in sorted(rueckstaende.items(), key=lambda e: e[1].vertrag.nummer):
        folge = stufen(eintrag.vertrag.vertragsart)
        if not folge:
            ergebnis.ohne_stufen.append(eintrag.vertrag.vertragsart)
            continue
        if not ausloeser_erfuellt(folge[0], eintrag, None, stichtag):
            continue
        with transaction.atomic():
            fall = _fall_anlegen(eintrag, stichtag)
            ergebnis.faelle_neu += 1
            _fortschreiben(fall, eintrag, stichtag, lauf, ergebnis)
    return ergebnis


# --- Ordentliche Kündigung durch den Vermieter (H4.6) --------------------------------


@dataclass(frozen=True)
class Kuendigungsfrist:
    zugang_am: datetime.date
    fristende: datetime.date
    regel: str
    titel: str


def kuendigungsfrist(vertrag: Vertrag, zugang_am: datetime.date) -> Kuendigungsfrist:
    """Fristende der ordentlichen Kündigung je Vertragsart (``stammdaten.fristrechnung``)."""
    regel = stammdaten.kuendigungsregel_fuer(vertrag)
    ende = fristrechnung.fristende(zugang_am, regel)
    if vertrag.ende is not None and vertrag.ende < ende:
        ende = vertrag.ende
    return Kuendigungsfrist(zugang_am, ende, regel.schluessel, regel.titel)


def vorlage_ordentlich(vertrag: Vertrag) -> Vorlage:
    """Die Vorlage der ordentlichen Kündigung: „Kündigung <Vertragsart>“, sonst „Kündigung
    durch den Hof“ (Art ``kuendigung``). Fehlt beides: ``VorlageNichtFreigegeben``."""
    titel = konfiguration.vertragsart(vertrag.vertragsart).titel
    for name in (VORLAGE_ORDENTLICH.format(titel=titel), VORLAGE_ORDENTLICH_STANDARD):
        try:
            return dokumente.freigegebene_vorlage("kuendigung", name)
        except dokumente.VorlageNichtFreigegeben:
            continue
    raise dokumente.VorlageNichtFreigegeben(
        f"{PROBEBETRIEB}: keine freigegebene Vorlage „{VORLAGE_ORDENTLICH.format(titel=titel)}“ "
        f"oder „{VORLAGE_ORDENTLICH_STANDARD}“ (Art kuendigung)."
    )


def ordentliche_kuendigung_erzeugen(vertrag: Vertrag, zugang_am: datetime.date) -> Schreiben:
    """Schreiben der ordentlichen Kündigung durch den Vermieter (H4.6) im Status „bereit“.

    Das System rechnet die Frist je Vertragsart; ``vertrag.ende`` im Kontext ist das
    Fristende. Versand nach dem Standard für einseitige Erklärungen (Brief, Zustellnachweis)
    und erst, wenn der Anwalt das Schreiben gesehen hat.
    """
    if vertrag.status not in LAUFEND:
        raise MahnwesenFehler(f"Vertrag {vertrag.nummer} ist nicht aktiv ({vertrag.status}).")
    if vertrag.einheiten.filter(typ=Einheit.Typ.WOHNUNG).exists():
        raise MahnwesenFehler(f"Vertrag {vertrag.nummer}: Wohnraum kündigt Verwaltio.")
    try:
        vorlage = vorlage_ordentlich(vertrag)
    except dokumente.VorlageNichtFreigegeben as fehler:
        raise MahnwesenFehler(str(fehler)) from fehler
    frist = kuendigungsfrist(vertrag, zugang_am)
    kontext = stammdaten_schreiben.kontext_vertrag(vertrag, zugang_am)
    kontext["vertrag"] = {**kontext["vertrag"], "ende": frist.fristende}
    kontext["kuendigung"] = {
        "art": "ordentlich",
        "zugang_angenommen": zugang_am,
        "fristende_ordentlich": frist.fristende,
        "kuendigungsfrist": frist.titel,
    }
    if not kontext["empfaenger"]["anschrift"]:
        raise MahnwesenFehler(f"Vertrag {vertrag.nummer}: Anschrift des Mieters fehlt (Brief).")
    try:
        return dokumente.schreiben_erzeugen(vorlage, vertrag, kontext, Versandweg.SELBST)
    except (dokumente.SchreibenFehler, dokumente.PlatzhalterFehler, pdf.PdfNichtVerfuegbar) as f:
        raise MahnwesenFehler(f"Vertrag {vertrag.nummer}: {f}") from f


# --- Lesen für Seiten, Kachel und Vergleich ------------------------------------------


def kachel_mahnfaelle() -> dict[str, Any]:
    """Kontext der Kachel „Mahnfälle“: Anzahl je Stufe und gesperrte. Nur Zähler."""
    offen = Mahnfall.objects.filter(status=Mahnfall.Status.OFFEN).select_related("stufe")
    je_stufe: dict[str, int] = {}
    reihenfolge: dict[str, int] = {}
    for fall in offen:
        titel = fall.stufe.titel if fall.stufe else "noch ohne Stufe"
        je_stufe[titel] = je_stufe.get(titel, 0) + 1
        reihenfolge[titel] = fall.stufe.reihenfolge if fall.stufe else 0
    gesperrt = Mahnfall.objects.filter(status=Mahnfall.Status.GESPERRT).count()
    return {
        "anzahl": sum(je_stufe.values()) + gesperrt,
        "je_stufe": [
            {"titel": t, "anzahl": je_stufe[t]}
            for t in sorted(je_stufe, key=reihenfolge.__getitem__)
        ],
        "gesperrt": gesperrt,
        "senden": Arbeitsposten.objects.filter(art=ART, status=Arbeitsposten.Status.OFFEN).count(),
    }


def schritte_im_zeitraum(von: datetime.date, bis: datetime.date) -> QuerySet[Mahnschritt]:
    return (
        Mahnschritt.objects.filter(stichtag__gte=von, stichtag__lte=bis)
        .select_related("stufe", "fall__vertrag")
        .prefetch_related("fall__vertrag__einheiten")
        .order_by("stichtag", "fall__vertrag__nummer", "pk")
    )


def faelle_offen_zwischen(von: datetime.date, bis: datetime.date) -> QuerySet[Mahnfall]:
    """Fälle, die zwischen ``von`` und ``bis`` offen waren."""
    return (
        Mahnfall.objects.filter(eroeffnet_am__lte=bis)
        .filter(Q(geschlossen_am__isnull=True) | Q(geschlossen_am__gte=von))
        .select_related("vertrag", "stufe")
        .prefetch_related("vertrag__einheiten")
    )


def gesperrte_vertraege(von: datetime.date, bis: datetime.date) -> set[int]:
    """Verträge mit einer Sperre, die zwischen ``von`` und ``bis`` galt."""
    return set(
        Mahnsperre.objects.filter(von__lte=bis)
        .filter(Q(bis__isnull=True) | Q(bis__gte=von))
        .filter(Q(aufgehoben_am__isnull=True) | Q(aufgehoben_am__gt=von))
        .values_list("vertrag_id", flat=True)
    )


def nw_ids(vertraege: Iterable[Vertrag]) -> list[str]:
    return sorted({i for v in vertraege for i in _nw_ids(v)})
