"""Register der Personenfelder (CLAUDE.md, Regel 4.6).

Jedes Text-Feld (``CharField``, ``TextField`` und Unterklassen wie ``EmailField``)
eines Modells unter ``apps/`` steht in genau einer von zwei Listen:

- ``register(Modell, ["feld", ...])``: Das Feld enthält Personendaten (Name, Anschrift,
  Kontakt, IBAN, Verwendungszweck, Freitext über Personen). Berichte, Logs und der
  Status-MCP lesen es nicht.
- ``unbedenklich(Modell, ["feld", ...])``: Das Feld enthält ausdrücklich keine
  Personendaten (Kürzel, Status, Art, Bezeichnung eines Objekts …).

Der Test ``tests/kern/test_personenfelder.py`` schlägt fehl, sobald ein Text-Feld in
keiner der beiden Listen steht.

Protokoll-Modelle von django-pghistory (``…Event``) erben die Einordnung des Modells,
das sie protokollieren; ihre eigenen ``pgh_*``-Felder sind unbedenklich.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from typing import Any

from django.db import models

ERSATZ = "‹…›"

_PERSONENFELDER: dict[type[models.Model], set[str]] = {}
_UNBEDENKLICH: dict[type[models.Model], set[str]] = {}


def _pruefe_felder(model: type[models.Model], felder: Iterable[str]) -> list[str]:
    namen = list(felder)
    for name in namen:
        # Wirft FieldDoesNotExist bei Tippfehlern, damit nichts still durchrutscht.
        model._meta.get_field(name)
    return namen


def register(model: type[models.Model], felder: Iterable[str]) -> None:
    """Markiert Felder eines Modells als Personenfelder."""
    namen = _pruefe_felder(model, felder)
    doppelt = _UNBEDENKLICH.get(model, set()) & set(namen)
    if doppelt:
        raise ValueError(
            f"{model._meta.label}: {sorted(doppelt)} stehen schon als unbedenklich im Register."
        )
    _PERSONENFELDER.setdefault(model, set()).update(namen)


def unbedenklich(model: type[models.Model], felder: Iterable[str]) -> None:
    """Markiert Felder eines Modells ausdrücklich als frei von Personendaten."""
    namen = _pruefe_felder(model, felder)
    doppelt = _PERSONENFELDER.get(model, set()) & set(namen)
    if doppelt:
        raise ValueError(
            f"{model._meta.label}: {sorted(doppelt)} stehen schon als Personenfelder im Register."
        )
    _UNBEDENKLICH.setdefault(model, set()).update(namen)


def _protokolliertes_modell(model: type[models.Model]) -> type[models.Model] | None:
    """Liefert für ein pghistory-Ereignismodell das protokollierte Modell."""
    verfolgt = getattr(model, "pgh_tracked_model", None)
    if isinstance(verfolgt, type) and issubclass(verfolgt, models.Model):
        return verfolgt
    return None


def _einordnung(model: type[models.Model], feld: str) -> str | None:
    """'person', 'unbedenklich' oder None (nicht markiert)."""
    verfolgt = _protokolliertes_modell(model)
    if verfolgt is not None:
        if feld.startswith("pgh_"):
            return "unbedenklich"
        model = verfolgt
    if feld in _PERSONENFELDER.get(model, set()):
        return "person"
    if feld in _UNBEDENKLICH.get(model, set()):
        return "unbedenklich"
    return None


def personenfelder(model: type[models.Model]) -> frozenset[str]:
    """Namen aller registrierten Personenfelder eines Modells."""
    verfolgt = _protokolliertes_modell(model)
    return frozenset(_PERSONENFELDER.get(verfolgt or model, set()))


def ist_personenfeld(model: type[models.Model], feld: str) -> bool:
    return _einordnung(model, feld) == "person"


def textfelder(model: type[models.Model]) -> list[models.Field[Any, Any]]:
    """Alle eigenen Text-Felder eines Modells (CharField, TextField und Unterklassen)."""
    return [
        feld
        for feld in model._meta.get_fields(include_parents=True)
        if isinstance(feld, models.CharField | models.TextField) and feld.concrete
    ]


def unmarkierte_textfelder(modelle: Iterable[type[models.Model]]) -> list[str]:
    """Text-Felder, die weder registriert noch als unbedenklich markiert sind.

    Ergebnis als ``app_label.Modell.feld``, sortiert.
    """
    fehlend = []
    for model in modelle:
        if model._meta.abstract or model._meta.proxy:
            continue
        for feld in textfelder(model):
            if _einordnung(model, feld.name) is None:
                fehlend.append(f"{model._meta.label}.{feld.name}")
    return sorted(fehlend)


def eigene_modelle() -> list[type[models.Model]]:
    """Alle Modelle der Apps unter ``apps/`` (ohne Drittanbieter und Django selbst)."""
    from django.apps import apps

    return [m for m in apps.get_models() if m.__module__.startswith("apps.")]


# --- Logging -----------------------------------------------------------------------


def _personenwerte(objekt: models.Model) -> list[str]:
    werte = []
    for feld in personenfelder(type(objekt)):
        wert = getattr(objekt, feld, None)
        if wert not in (None, ""):
            werte.append(str(wert))
    # Längere Werte zuerst ersetzen, damit Teilstücke nicht stehen bleiben.
    return sorted(werte, key=len, reverse=True)


#: Attribute, die jeder ``LogRecord`` selbst hat (``name``, ``msg``, ``args``, ``levelname`` …).
#: Der Filter ersetzt sie nie, auch wenn ein Personenfeld so heißt: Sonst verschwände etwa der
#: Name des Loggers. ``message`` und ``asctime`` setzt erst der Formatter.
LOGRECORD_ATTRIBUTE = frozenset(vars(logging.LogRecord("", 0, "", 0, "", None, None))) | {
    "message",
    "asctime",
}


def _alle_personenfeldnamen() -> set[str]:
    namen: set[str] = set()
    for felder in _PERSONENFELDER.values():
        namen |= felder
    return namen


class PersonenfelderFilter(logging.Filter):
    """Ersetzt Werte registrierter Personenfelder in Logmeldungen durch ``‹…›``.

    Erfasst werden
    - Modell-Objekte unter den Argumenten der Meldung: Die Werte ihrer Personenfelder
      werden im fertigen Text ersetzt (auch wenn ``str(objekt)`` sie enthält),
    - Argumente als Wörterbuch (``logger.info("%(name)s", {...})``) und Angaben über
      ``extra=...``, deren Schlüssel der Name eines Personenfelds ist. Die eigenen Attribute
      des ``LogRecord`` (``LOGRECORD_ATTRIBUTE``) bleiben immer stehen.

    Der Filter verwirft nie eine Meldung, er schreibt sie nur um.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        namen = _alle_personenfeldnamen()

        for schluessel in namen - LOGRECORD_ATTRIBUTE:
            if schluessel in record.__dict__:
                setattr(record, schluessel, ERSATZ)

        args = record.args
        objekte: list[models.Model] = []
        if isinstance(args, Mapping):
            neu = {k: (ERSATZ if k in namen else v) for k, v in args.items()}
            objekte = [v for v in neu.values() if isinstance(v, models.Model)]
            record.args = neu
        elif isinstance(args, tuple):
            objekte = [a for a in args if isinstance(a, models.Model)]

        werte = [wert for objekt in objekte for wert in _personenwerte(objekt)]
        if werte:
            text = record.getMessage()
            for wert in werte:
                text = text.replace(wert, ERSATZ)
            record.msg = text
            record.args = None
        return True
