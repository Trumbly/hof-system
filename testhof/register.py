"""Anmeldung der Generator-Module und ihre Reihenfolge.

Jedes Modul unter ``testhof/`` meldet sich beim Import an::

    register.registrieren(
        "bank",
        abhaengig_von=("kern",),
        aufbauen=aufbauen,      # (Kontext) -> Daten für die Ablage
        leeren=leeren,          # () -> None
        sonderfaelle=("doppelter_umsatz",),
    )

``reihenfolge()`` sortiert die Module topologisch nach ihren Abhängigkeiten. Bei
gleichrangigen Modulen entscheidet der Name, damit die Reihenfolge und damit die Daten
für jeden Seed fest bleiben.
"""

from __future__ import annotations

import heapq
import importlib
import pkgutil
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from testhof.aufbau import Kontext


class RegisterFehler(Exception):
    """Abhängigkeiten der Module passen nicht (unbekannt, doppelt oder im Kreis)."""


@dataclass(frozen=True)
class Modul:
    name: str
    abhaengig_von: tuple[str, ...]
    aufbauen: Callable[[Kontext], Any]
    leeren: Callable[[], None]
    #: Schlüssel aus ``testhof.sonderfaelle.SONDERFAELLE``, die dieses Modul erzeugt.
    sonderfaelle: tuple[str, ...] = ()


_MODULE: dict[str, Modul] = {}


def registrieren(
    name: str,
    *,
    aufbauen: Callable[[Kontext], Any],
    leeren: Callable[[], None],
    abhaengig_von: tuple[str, ...] = (),
    sonderfaelle: tuple[str, ...] = (),
) -> Modul:
    """Meldet ein Generator-Modul an."""
    if name in _MODULE:
        raise RegisterFehler(f"Testhof-Modul {name!r} ist schon angemeldet.")
    modul = Modul(
        name=name,
        abhaengig_von=tuple(abhaengig_von),
        aufbauen=aufbauen,
        leeren=leeren,
        sonderfaelle=tuple(sonderfaelle),
    )
    _MODULE[name] = modul
    return modul


def laden() -> dict[str, Modul]:
    """Importiert alle Untermodule von ``testhof``; die Generator-Module melden sich an."""
    import testhof

    for info in pkgutil.iter_modules(testhof.__path__):
        if not info.name.startswith("_"):
            importlib.import_module(f"testhof.{info.name}")
    return dict(_MODULE)


def reihenfolge(module: Mapping[str, Modul] | None = None) -> list[Modul]:
    """Module so sortiert, dass jedes nach seinen Abhängigkeiten kommt (Kahn-Verfahren)."""
    module = laden() if module is None else module
    for modul in module.values():
        fehlend = [a for a in modul.abhaengig_von if a not in module]
        if fehlend:
            raise RegisterFehler(
                f"Testhof-Modul {modul.name!r} hängt von unbekannten Modulen ab: {fehlend}"
            )
    offen = {name: set(modul.abhaengig_von) for name, modul in module.items()}
    bereit = [name for name, abh in offen.items() if not abh]
    heapq.heapify(bereit)
    ergebnis: list[Modul] = []
    while bereit:
        name = heapq.heappop(bereit)
        ergebnis.append(module[name])
        del offen[name]
        for anderer, abh in offen.items():
            if name in abh:
                abh.discard(name)
                if not abh:
                    heapq.heappush(bereit, anderer)
    if offen:
        raise RegisterFehler(f"Testhof-Module hängen im Kreis voneinander ab: {sorted(offen)}")
    return ergebnis
