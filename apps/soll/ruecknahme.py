"""Rücknahme eines Vorgangs und Sollstellung (Review 2, A4; schließt die Lücke aus Schritt 5,
„Konditionsänderung über Zurücknehmen“).

Sollposten aus dem Vertrag (``quelle = vertrag``) sind abgeleitet: Die Rücknahme dreht sie
nicht einzeln zurück. Sie stellt erst Vertrag, Konditionen und Kündigung wieder her und ruft
dann für jeden betroffenen Vertrag den Soll-Haken (``vertrag_geaendert``). So stimmt das
Soll sofort: Unpassende offene Posten werden storniert, fehlende neu gestellt, Bezahltes
wird als Arbeitsposten gemeldet. Posten von Hand laufen weiter über den Stornoweg.
"""

from __future__ import annotations

from typing import Any

from apps.kern import services as kern
from apps.soll import services
from apps.soll.models import Sollposten
from apps.stammdaten.models import Vertrag


def _abgeleitet(daten: dict[str, Any]) -> bool:
    return daten.get("quelle") == Sollposten.Quelle.VERTRAG


def _betroffene_vertraege(plan: kern.Ruecknahmeplan) -> set[Any]:
    ids: set[Any] = set()
    for aenderung in plan.aenderungen:
        if aenderung.modell is Vertrag:
            ids.add(aenderung.objekt_id)
        for ereignis in aenderung.ereignisse:
            if ereignis.daten.get("vertrag_id"):
                ids.add(ereignis.daten["vertrag_id"])
    return ids


def _soll_neu_stellen(plan: kern.Ruecknahmeplan) -> list[kern.Schritt]:
    schritte = []
    for vertrag in Vertrag.objects.filter(pk__in=_betroffene_vertraege(plan)).order_by("nummer"):
        ergebnis = services.vertrag_geaendert(vertrag)
        if ergebnis is None or not (ergebnis.neu or ergebnis.storniert or ergebnis.arbeitsposten):
            continue
        teile = [f"{len(ergebnis.neu)} Posten neu", f"{len(ergebnis.storniert)} storniert"]
        if ergebnis.arbeitsposten:
            teile.append(f"{len(ergebnis.arbeitsposten)} zu prüfen")
        schritte.append(kern.Schritt(f"Soll {vertrag.nummer} neu gestellt: {', '.join(teile)}"))
    return schritte


def registrieren() -> None:
    kern.ruecknahmeweg_registrieren(Sollposten, abgeleitet=_abgeleitet)
    kern.ruecknahme_nachlauf_registrieren(_soll_neu_stellen)
