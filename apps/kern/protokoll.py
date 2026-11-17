"""Protokoll jeder Änderung mit django-pghistory (H11.6).

Ein Modell erbt von ``ProtokolliertesModell`` und bekommt damit ohne weitere Arbeit
- ein Ereignismodell ``<Modell>Event`` in seiner App (eine Zeile je Anlage, Änderung
  und Löschung, geschrieben von Triggern in der Datenbank),
- den Kontext jeder Änderung: Benutzer und Adresse aus der Anfrage
  (``pghistory.middleware.HistoryMiddleware``), Lauf aus der Lauf-Basisklasse
  (``apps.kern.laeufe``), Rücknahme aus ``kern.services.zuruecknehmen``.

Die Ereignismodelle entstehen beim Laden der Modelle und gehen über
``makemigrations`` in die Migrationen ihrer App ein.
"""

from __future__ import annotations

from typing import Any

import pghistory
from django.db import models
from django.db.models.signals import class_prepared

# Was jedes protokollierte Modell aufzeichnet: Anlage, Änderung, Löschung.
EREIGNISSE = ("insert", "update", "delete")

ART_TITEL = {
    "insert": "angelegt",
    "update": "geändert",
    "delete": "gelöscht",
}

_PROTOKOLLIERT: list[type[models.Model]] = []


class ProtokolliertesModell(models.Model):
    """Basisklasse für alle Modelle, deren Änderungen ins Protokoll gehören."""

    class Meta:
        abstract = True


def protokollierte_modelle() -> list[type[models.Model]]:
    """Alle Modelle, die über ``ProtokolliertesModell`` protokolliert werden."""
    return list(_PROTOKOLLIERT)


def ist_protokolliert(model: type[models.Model]) -> bool:
    return model in _PROTOKOLLIERT


def _protokoll_einrichten(sender: type[models.Model], **kwargs: Any) -> None:
    if not issubclass(sender, ProtokolliertesModell):
        return
    meta = sender._meta
    if meta.abstract or meta.proxy or not meta.managed:
        return
    pghistory.track(
        pghistory.InsertEvent(),
        pghistory.UpdateEvent(),
        pghistory.DeleteEvent(),
    )(sender)
    _PROTOKOLLIERT.append(sender)


# Muss verbunden sein, bevor das erste Modell mit dieser Basisklasse entsteht;
# models.py importiert dieses Modul deshalb vor seinen Klassen.
class_prepared.connect(_protokoll_einrichten, dispatch_uid="kern_protokoll_einrichten")
