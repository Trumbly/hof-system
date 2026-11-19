"""Import der Mietregeln aus hof-fints (``regeln.yaml``): lesen, prüfen, Bericht mit Zählern.

Je Eintrag entsteht eine Zahlerregel an einer Regel des Kassenbuchs: Gibt es schon eine Regel
mit derselben Gegen-IBAN (ohne IBAN: gleicher Name und Zweck), bekommt sie die Zahlerregel;
sonst entsteht eine neue Regel (Kategorie nach Vertragsart, Objekt der Einheit, siehe
``importformat``). Eine vorhandene Zahlerregel wird nie überschrieben; weicht sie ab, zählt
der Bericht das (Abweichung zwischen den zwei Rechnern, klärt Max).

Der Bericht nennt nur Zähler, Abweichungsarten, Eintrag-IDs der Datei und NW-IDs, nie Namen,
IBANs, Zwecke oder Beträge. Der Probelauf rechnet alles in einer Transaktion und rollt sie
zurück: Er zählt genau, was ``--ausfuehren`` täte, und ändert nichts.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

import yaml
from django.db import transaction

from apps.abgleich import services
from apps.abgleich.importformat import MIETE, PACHT, RegelFormat
from apps.abgleich.models import Zahlerregel
from apps.bank import fingerabdruck
from apps.bank.models import Bankumsatz
from apps.kassenbuch import services as kassenbuch
from apps.kassenbuch.models import Kategorie, Zuordnungsregel
from apps.kern.models import Objekt
from apps.stammdaten.models import LAUFEND, Einheit, Vertrag

KEINE_BEDINGUNG = "keine Bedingung für den Zahler"
DOPPELT = "doppelt in der Datei"
KEIN_VERTRAG = "kein Vertrag angegeben"
VERTRAG_FEHLT = "Vertrag nicht gefunden"
EINHEIT_FEHLT = "Einheit nicht gefunden"
WOHNRAUM = "Wohnraum (Verwaltio)"
NICHT_LAUFEND = "kein laufender Vertrag an der Einheit"
MEHRERE_VERTRAEGE = "mehrere laufende Verträge an der Einheit"
BETRAG = "Betrag unlesbar"
MEHRERE_REGELN = "mehrere Regeln im Kassenbuch passen"
KATEGORIE_FEHLT = "Kategorie fehlt im Kassenbuch"
OBJEKT_FEHLT = "Objekt fehlt"
UNGUELTIG = "Regel ungültig"
ABWEICHEND = "Zahlerregel weicht ab"


class ImportFehler(ValueError):
    """Die Datei passt nicht zum Format. Die Meldung nennt nur Abschnitte und Zähler."""


@dataclass
class ImportBericht:
    eintraege: int = 0
    gueltig: int = 0
    neu: int = 0
    neue_kassenbuch_regeln: int = 0
    an_vorhandene_regel: int = 0
    unveraendert: int = 0
    nicht_vertragsbezogen: int = 0
    fehlerhaft: Counter[str] = field(default_factory=Counter)
    #: Abweichungsart -> Eintrag-IDs der Datei
    eintraege_je_art: dict[str, list[str]] = field(default_factory=dict)
    #: Abweichungsart -> NW-IDs
    nw_ids_je_art: dict[str, list[str]] = field(default_factory=dict)
    ausgefuehrt: bool = False

    def abweichung(self, art: str, schluessel: str, nw_ids: list[str]) -> None:
        self.fehlerhaft[art] += 1
        self.eintraege_je_art.setdefault(art, []).append(schluessel)
        if nw_ids:
            self.nw_ids_je_art.setdefault(art, []).extend(nw_ids)

    def zeilen_text(self) -> list[str]:
        """Der Bericht: nur Zähler, Abweichungsarten, Eintrag-IDs und NW-IDs."""
        zeilen = [
            "Ausgeführt." if self.ausgefuehrt else "Probelauf: nichts geändert.",
            f"Einträge gelesen: {self.eintraege}",
            f"gültig: {self.gueltig}",
            f"Zahlerregeln neu: {self.neu} (davon neue Regeln im Kassenbuch: "
            f"{self.neue_kassenbuch_regeln}, an vorhandene Regel: {self.an_vorhandene_regel}, "
            f"nicht vertragsbezogen: {self.nicht_vertragsbezogen})",
            f"unverändert vorhanden: {self.unveraendert}",
            f"abweichend oder fehlerhaft: {sum(self.fehlerhaft.values())}",
        ]
        for art, anzahl in sorted(self.fehlerhaft.items()):
            text = f"  {art}: {anzahl} (Einträge {', '.join(self.eintraege_je_art[art])})"
            if self.nw_ids_je_art.get(art):
                text += f"; NW-IDs {', '.join(sorted(set(self.nw_ids_je_art[art])))}"
            zeilen.append(text)
        return zeilen


def _wert(eintrag: Any, pfad: str) -> Any:
    for teil in pfad.split("."):
        if not isinstance(eintrag, dict):
            return None
        eintrag = eintrag.get(teil)
    return eintrag


def lesen(text: str, format: RegelFormat) -> list[tuple[str, dict[str, Any], int]]:
    """Einträge als (Art, Eintrag, Position). Prüft nur den Aufbau der Datei."""
    try:
        daten = yaml.safe_load(text)
    except yaml.YAMLError as fehler:
        raise ImportFehler("Die Datei ist kein gültiges YAML.") from fehler
    if not isinstance(daten, dict):
        raise ImportFehler("Die Datei hat keine Abschnitte.")
    gefunden = [a for a in format.abschnitte if a in daten]
    if not gefunden:
        raise ImportFehler(
            f"Keiner der Abschnitte gefunden: {', '.join(sorted(format.abschnitte))}."
        )
    eintraege = []
    position = 0
    for abschnitt in gefunden:
        liste = daten[abschnitt] or []
        if not isinstance(liste, list):
            raise ImportFehler(f"Abschnitt {abschnitt} ist keine Liste.")
        for eintrag in liste:
            position += 1
            eintraege.append((format.abschnitte[abschnitt], eintrag or {}, position))
    return eintraege


@dataclass
class _Ziel:
    vertraege: list[Vertrag]
    fehler: str = ""
    nw_ids: list[str] = field(default_factory=list)


def _vertrag_zu(kennung: str) -> tuple[Vertrag | None, str, list[str]]:
    kennung = kennung.strip().upper()
    if kennung.startswith("NW-"):
        einheit = Einheit.objects.filter(nw_id__iexact=kennung).first()
        if einheit is None:
            return None, EINHEIT_FEHLT, [kennung]
        if einheit.ist_wohnraum:
            return None, WOHNRAUM, [einheit.nw_id]
        laufend = list(einheit.vertraege.filter(status__in=LAUFEND))
        if not laufend:
            return None, NICHT_LAUFEND, [einheit.nw_id]
        if len(laufend) > 1:
            return None, MEHRERE_VERTRAEGE, [einheit.nw_id]
        return laufend[0], "", [einheit.nw_id]
    vertrag = Vertrag.objects.filter(nummer__iexact=kennung).first()
    if vertrag is None:
        return None, VERTRAG_FEHLT, []
    return vertrag, "", [e.nw_id for e in vertrag.einheiten.all()]


def _ziel(eintrag: dict[str, Any], format: RegelFormat) -> _Ziel:
    felder = format.felder
    liste = _wert(eintrag, felder["vertraege"])
    kennungen: list[str]
    if isinstance(liste, list) and liste:
        kennungen = [str(k) for k in liste]
    else:
        einzeln = _wert(eintrag, felder["vertrag"]) or _wert(eintrag, felder["nw_id"])
        kennungen = [str(einzeln)] if einzeln else []
    if not kennungen:
        return _Ziel([], KEIN_VERTRAG)
    ziel = _Ziel([])
    for kennung in kennungen:
        vertrag, fehler, nw_ids = _vertrag_zu(kennung)
        ziel.nw_ids += nw_ids
        if fehler:
            ziel.fehler = ziel.fehler or fehler
        elif vertrag is not None:
            ziel.vertraege.append(vertrag)
    return ziel


def _vorhandene_regeln(iban: str, name: str, zweck: str) -> list[Zuordnungsregel]:
    qs = Zuordnungsregel.objects.filter(aktiv=True).exclude(richtung=Bankumsatz.Richtung.AUSGANG)
    if iban:
        return list(qs.filter(gegen_iban=iban).order_by("pk"))
    return list(
        qs.filter(gegen_iban="", gegenpartei__iexact=name, zweck__iexact=zweck).order_by("pk")
    )


def _gleich(
    zahlerregel: Zahlerregel, vertraege: list[Vertrag], betrag: Decimal | None, ohne: bool
) -> bool:
    return (
        zahlerregel.nicht_vertragsbezogen == ohne
        and {v.pk for v in zahlerregel.vertraege()} == {v.pk for v in vertraege}
        and zahlerregel.betrag == betrag
    )


def _eintrag(
    art: str,
    eintrag: dict[str, Any],
    position: int,
    format: RegelFormat,
    bericht: ImportBericht,
    ibans: set[str],
) -> None:
    felder = format.felder
    schluessel = str(_wert(eintrag, felder["schluessel"]) or f"#{position}")
    iban = fingerabdruck.iban_normal(str(_wert(eintrag, felder["iban"]) or ""))
    name = str(_wert(eintrag, felder["name"]) or "").strip()
    zweck = str(_wert(eintrag, felder["zweck"]) or "").strip()
    ohne_vertrag = art == PACHT
    ziel = _Ziel([]) if ohne_vertrag else _ziel(eintrag, format)
    if not (iban or name or zweck):
        bericht.abweichung(KEINE_BEDINGUNG, schluessel, ziel.nw_ids)
        return
    if iban and iban in ibans:
        bericht.abweichung(DOPPELT, schluessel, ziel.nw_ids)
        return
    if iban:
        ibans.add(iban)
    if ziel.fehler:
        bericht.abweichung(ziel.fehler, schluessel, ziel.nw_ids)
        return
    roh = _wert(eintrag, felder["betrag"])
    betrag = None
    if roh not in (None, "") and not ohne_vertrag:
        try:
            betrag = Decimal(str(roh)).quantize(Decimal("0.01"))
        except InvalidOperation:
            bericht.abweichung(BETRAG, schluessel, ziel.nw_ids)
            return
    vorhanden = _vorhandene_regeln(iban, name, zweck)
    if len(vorhanden) > 1:
        bericht.abweichung(MEHRERE_REGELN, schluessel, ziel.nw_ids)
        return
    herkunft = f"{format.name}:{schluessel}"[:100]
    if vorhanden:
        regel = vorhanden[0]
        zahlerregel = Zahlerregel.objects.filter(regel=regel).first()
        if zahlerregel is not None:
            if _gleich(zahlerregel, ziel.vertraege, betrag, ohne_vertrag):
                bericht.unveraendert += 1
                bericht.gueltig += 1
            else:
                bericht.abweichung(ABWEICHEND, schluessel, ziel.nw_ids)
            return
        neue = False
    else:
        if ohne_vertrag:
            kategorie_name, objekt = (
                format.pacht_kategorie,
                Objekt.objects.filter(kuerzel=format.pacht_objekt).first(),
            )
        else:
            erster = ziel.vertraege[0]
            kategorie_name = format.kategorien.get(erster.vertragsart, "")
            einheit = erster.einheiten.select_related("objekt").first()
            objekt = einheit.objekt if einheit is not None else None
        kategorie = Kategorie.objects.filter(bezeichnung=kategorie_name, aktiv=True).first()
        if kategorie is None:
            bericht.abweichung(KATEGORIE_FEHLT, schluessel, ziel.nw_ids)
            return
        if objekt is None:
            bericht.abweichung(OBJEKT_FEHLT, schluessel, ziel.nw_ids)
            return
        try:
            regel = kassenbuch.regel_anlegen(
                f"{format.name} {schluessel}"[:200],
                kategorie=kategorie,
                objekt=objekt,
                gegen_iban=iban,
                gegenpartei="" if iban else name,
                zweck="" if iban else zweck,
                richtung=Bankumsatz.Richtung.EINGANG,
                prioritaet=20,
            )
        except kassenbuch.KassenbuchFehler:
            bericht.abweichung(UNGUELTIG, schluessel, ziel.nw_ids)
            return
        neue = True
    try:
        with transaction.atomic():
            services.zahlerregel_setzen(
                regel,
                vertrag=ziel.vertraege[0] if len(ziel.vertraege) == 1 else None,
                vertraege=ziel.vertraege if len(ziel.vertraege) > 1 else (),
                betrag=betrag,
                nicht_vertragsbezogen=ohne_vertrag,
                herkunft=herkunft,
            )
    except services.AbgleichFehler:
        bericht.abweichung(UNGUELTIG, schluessel, ziel.nw_ids)
        return
    bericht.gueltig += 1
    bericht.neu += 1
    bericht.neue_kassenbuch_regeln += int(neue)
    bericht.an_vorhandene_regel += int(not neue)
    bericht.nicht_vertragsbezogen += int(ohne_vertrag)


def importieren(text: str, format: RegelFormat, *, ausfuehren: bool) -> ImportBericht:
    """Liest die Datei und legt die Zahlerregeln an (im Probelauf zurückgerollt)."""
    eintraege = lesen(text, format)
    bericht = ImportBericht(eintraege=len(eintraege), ausgefuehrt=ausfuehren)
    with transaction.atomic():
        ibans: set[str] = set()
        for art, eintrag, position in eintraege:
            if art not in (MIETE, PACHT):
                continue
            with transaction.atomic():
                _eintrag(art, eintrag, position, format, bericht, ibans)
        if not ausfuehren:
            transaction.set_rollback(True)
    return bericht
