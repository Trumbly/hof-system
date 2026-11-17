"""Benachrichtigung (H11.1): meldet gescheiterte Läufe und wartende Freigaben.

Regeln:
- Gemeldet wird nur, wenn ein Lauf gescheitert ist (Status „abgebrochen“ oder „mit
  Fehlern“ in den letzten ``HOF_BENACHRICHTIGUNG_FENSTER_STUNDEN``) oder offene
  Arbeitsposten einer Art aus ``HOF_BENACHRICHTIGUNG_ARTEN`` warten. Sonst bleibt es still.
- Inhalt nur Zähler und Links: Lauf-Namen, Arten und Anzahlen, nie Namen, Beträge,
  Hinweise oder Meldungstexte.
- Kein Spam: Was heute schon gemeldet ist, kommt heute nicht noch einmal. Eine neue
  Meldung gibt es nur, wenn die Lage etwas Neues enthält (ein weiterer Lauf ist
  gescheitert, oder bei einer Art warten mehr Posten als heute schon gemeldet).
"""

from __future__ import annotations

import datetime
import hashlib
import json
import logging
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from django.conf import settings
from django.db.models import Count
from django.urls import reverse
from django.utils import timezone

from apps.benachrichtigung.kanaele import Kanal, KanalFehler, Nachricht, kanaele_aus_einstellungen
from apps.benachrichtigung.models import Benachrichtigung
from apps.kern.models import Arbeitsposten, Lauf

logger = logging.getLogger(__name__)

GESCHEITERT = (Lauf.Status.ABGEBROCHEN, Lauf.Status.MIT_FEHLERN)


@dataclass(frozen=True)
class Lage:
    """Was gerade zu melden ist: nur Namen von Läufen, Arten und Anzahlen."""

    gescheitert: dict[str, int] = field(default_factory=dict)
    freigaben: dict[str, int] = field(default_factory=dict)

    @property
    def leer(self) -> bool:
        return not self.gescheitert and not self.freigaben

    def als_dict(self) -> dict[str, dict[str, int]]:
        return {
            "gescheitert": dict(sorted(self.gescheitert.items())),
            "freigaben": dict(sorted(self.freigaben.items())),
        }

    @property
    def schluessel(self) -> str:
        text = json.dumps(self.als_dict(), sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    @classmethod
    def aus_dict(cls, daten: dict[str, Any]) -> Lage:
        return cls(
            gescheitert={str(k): int(v) for k, v in (daten.get("gescheitert") or {}).items()},
            freigaben={str(k): int(v) for k, v in (daten.get("freigaben") or {}).items()},
        )

    def enthaelt_neues(self, gemeldet: Lage) -> bool:
        """Gibt es einen gescheiterten Lauf oder mehr wartende Posten als ``gemeldet``?"""
        if any(name not in gemeldet.gescheitert for name in self.gescheitert):
            return True
        return any(n > gemeldet.freigaben.get(art, 0) for art, n in self.freigaben.items())

    @staticmethod
    def zusammenfassen(lagen: Iterable[Lage]) -> Lage:
        """Alles, was in ``lagen`` gemeldet ist: je Lauf und Art die höchste Anzahl."""
        gescheitert: dict[str, int] = {}
        freigaben: dict[str, int] = {}
        for lage in lagen:
            for name, n in lage.gescheitert.items():
                gescheitert[name] = max(n, gescheitert.get(name, 0))
            for art, n in lage.freigaben.items():
                freigaben[art] = max(n, freigaben.get(art, 0))
        return Lage(gescheitert=gescheitert, freigaben=freigaben)


@dataclass(frozen=True)
class Ergebnis:
    """Ergebnis von ``benachrichtigen``.

    ``status``: ``still`` (nichts zu melden), ``schon_gemeldet`` (heute schon gemeldet,
    nichts Neues), ``gemeldet`` (mindestens ein Kanal hat gesendet) oder
    ``fehlgeschlagen`` (kein Kanal hat gesendet).
    """

    status: str
    lage: Lage
    nachricht: Nachricht | None = None
    gesendet: list[str] = field(default_factory=list)
    fehler: list[str] = field(default_factory=list)
    benachrichtigung: Benachrichtigung | None = None


# --- Lage ----------------------------------------------------------------------------


def lage_ermitteln(
    jetzt: datetime.datetime | None = None, *, arten: Sequence[str] | None = None
) -> Lage:
    """Zählt gescheiterte Läufe im Zeitfenster und offene Posten der gemeldeten Arten."""
    jetzt = jetzt or timezone.now()
    fenster = datetime.timedelta(hours=settings.HOF_BENACHRICHTIGUNG_FENSTER_STUNDEN)
    arten = list(settings.HOF_BENACHRICHTIGUNG_ARTEN if arten is None else arten)

    gescheitert = {
        zeile["name"]: zeile["anzahl"]
        for zeile in Lauf.objects.filter(
            status__in=GESCHEITERT, gestartet_am__gte=jetzt - fenster, gestartet_am__lte=jetzt
        )
        .values("name")
        .annotate(anzahl=Count("pk"))
        .order_by()
    }
    freigaben = {
        zeile["art"]: zeile["anzahl"]
        for zeile in Arbeitsposten.objects.filter(status=Arbeitsposten.Status.OFFEN, art__in=arten)
        .values("art")
        .annotate(anzahl=Count("pk"))
        .order_by()
    }
    return Lage(gescheitert=gescheitert, freigaben=freigaben)


# --- Nachricht -----------------------------------------------------------------------


def _link(name: str) -> str:
    basis: str = settings.HOF_BASIS_URL
    return basis.rstrip("/") + reverse(name)


def _mehrzahl(anzahl: int, eins: str, mehr: str) -> str:
    return f"{anzahl} {eins if anzahl == 1 else mehr}"


def nachricht_bauen(lage: Lage) -> Nachricht:
    """Titel, Text und Link. Nur Zähler, Lauf-Namen, Arten und Links."""
    teile_titel = []
    zeilen: list[str] = []
    if lage.gescheitert:
        summe = sum(lage.gescheitert.values())
        teile_titel.append(_mehrzahl(summe, "Lauf gescheitert", "Läufe gescheitert"))
        stunden = settings.HOF_BENACHRICHTIGUNG_FENSTER_STUNDEN
        zeilen.append(f"Gescheiterte Läufe (letzte {stunden} Stunden): {summe}")
        zeilen += [f"- {name}: {n}" for name, n in sorted(lage.gescheitert.items())]
        zeilen.append(f"Läufe: {_link('kern:laeufe')}")
    if lage.freigaben:
        if zeilen:
            zeilen.append("")
        summe = sum(lage.freigaben.values())
        teile_titel.append(_mehrzahl(summe, "Freigabe wartet", "Freigaben warten"))
        zeilen.append(f"Wartende Freigaben: {summe}")
        zeilen += [f"- {art}: {n}" for art, n in sorted(lage.freigaben.items())]
        zeilen.append(f"Arbeitslisten: {_link('kern:arbeitslisten')}")

    if lage.gescheitert and lage.freigaben:
        link = _link("kern:startseite")
    elif lage.gescheitert:
        link = _link("kern:laeufe")
    else:
        link = _link("kern:arbeitslisten")
    return Nachricht(
        titel="Hof-System: " + ", ".join(teile_titel), text="\n".join(zeilen), link=link
    )


# --- Melden --------------------------------------------------------------------------


def _heute_gemeldet(jetzt: datetime.datetime) -> Lage:
    lokal = timezone.localtime(jetzt)
    tagesbeginn = lokal.replace(hour=0, minute=0, second=0, microsecond=0)
    lagen = [
        Lage.aus_dict(daten)
        for daten in Benachrichtigung.objects.filter(
            gesendet_am__gte=tagesbeginn, gesendet_am__lt=tagesbeginn + datetime.timedelta(days=1)
        ).values_list("lage", flat=True)
    ]
    return Lage.zusammenfassen(lagen)


def benachrichtigen(
    *,
    jetzt: datetime.datetime | None = None,
    kanaele: Sequence[Kanal] | None = None,
    lauf: Lauf | None = None,
) -> Ergebnis:
    """Prüft die Lage und meldet sie, wenn nötig, über alle eingerichteten Kanäle.

    ``kanaele``: Standard sind die Kanäle aus den Einstellungen (Mail, ntfy). Ein Kanal,
    der scheitert, hält die anderen nicht auf. Hat kein Kanal gesendet, gilt die Lage
    als nicht gemeldet und kommt beim nächsten Lauf wieder.
    """
    jetzt = jetzt or timezone.now()
    lage = lage_ermitteln(jetzt)
    if lage.leer:
        return Ergebnis(status="still", lage=lage)
    if not lage.enthaelt_neues(_heute_gemeldet(jetzt)):
        return Ergebnis(status="schon_gemeldet", lage=lage)

    nachricht = nachricht_bauen(lage)
    kanaele = list(kanaele_aus_einstellungen() if kanaele is None else kanaele)
    gesendet: list[str] = []
    fehler: list[str] = []
    for kanal in kanaele:
        try:
            kanal.senden(nachricht)
        except KanalFehler as ausfall:
            logger.warning("Benachrichtigung über %s gescheitert: %s", kanal.name, ausfall)
            fehler.append(kanal.name)
        else:
            gesendet.append(kanal.name)
    if not gesendet:
        if not kanaele:
            logger.warning("Benachrichtigung: kein Kanal eingerichtet")
        return Ergebnis(
            status="fehlgeschlagen", lage=lage, nachricht=nachricht, fehler=fehler or ["kein Kanal"]
        )

    eintrag = Benachrichtigung.objects.create(
        gesendet_am=jetzt,
        schluessel=lage.schluessel,
        lage=lage.als_dict(),
        titel=nachricht.titel[:200],
        text=nachricht.text,
        kanaele=gesendet,
        lauf=lauf,
    )
    logger.info("Benachrichtigung #%s über %s gesendet", eintrag.pk, ", ".join(gesendet))
    return Ergebnis(
        status="gemeldet",
        lage=lage,
        nachricht=nachricht,
        gesendet=gesendet,
        fehler=fehler,
        benachrichtigung=eintrag,
    )
