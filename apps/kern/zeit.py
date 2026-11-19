"""Eine Uhr für das ganze System (Review 3, Punkt 1).

Alles, was nach dem fachlichen Tag fragt (Vorbelegungen, Fälligkeit, Fristen,
Bankfreigaben, Läufe, Cockpit-Kopf), fragt ``heute()`` bzw. ``jetzt()``:

- **prod** (``settings.HOF_STICHTAG`` ist ``None``): das echte Datum und die echte Zeit.
- **dev und test** (``HOF_STICHTAG`` = Stichtag des Testhofs, ``testhof.aufbau.STICHTAG``):
  der Stichtag. Das Band oben („Testhof · Stichtag …“) und das Rechnen stimmen damit
  überein.
- **gestellte Uhr** (``uhr_stellen()``): Tests, der Aufbau des Testhofs und Läufe, die mit
  ``--stichtag`` gestartet sind, stellen die Uhr für ihre Dauer auf einen eigenen Tag.

``jetzt()`` ist im Testhof der Stichtag **mit der echten Uhrzeit**, nicht fest 08:00:
Zeitpunkte von Aktionen (erledigt, bestätigt, entschieden) bleiben so in der Reihenfolge,
in der sie geklickt wurden, und „vor 5 Minuten“ stimmt. Wer einen festen Zeitpunkt braucht
(Testhof-Aufbau mit ``aufbau.ZEITPUNKT``, Tests), stellt die Uhr mit einem ``datetime``.

Technische Zeitstempel bleiben echte Zeit: ``auto_now``/``erstellt_am``, Start und Ende
eines Laufs, Sitzungsablauf, TOTP, Alter einer Sicherung. Solche Stellen tragen im Code den
Marker ``# zeit: echt``; der Wächter-Test (``tests/kern/test_zeit.py``) lässt nur sie zu.
"""

from __future__ import annotations

import datetime
from collections.abc import Iterator
from contextlib import contextmanager
from zoneinfo import ZoneInfo

from django.conf import settings
from django.utils import timezone

ZEITZONE = ZoneInfo("Europe/Berlin")

#: Gestellte Uhren, die innerste zuletzt. Ein ``datetime`` legt auch die Uhrzeit fest,
#: ein ``date`` nur den Tag (die Uhrzeit bleibt echt).
_gestellt: list[datetime.date] = []


def stichtag() -> datetime.date | None:
    """Der feste Tag, falls einer gilt (gestellte Uhr oder ``HOF_STICHTAG``), sonst ``None``."""
    if _gestellt:
        wann = _gestellt[-1]
        return wann.date() if isinstance(wann, datetime.datetime) else wann
    fest: datetime.date | None = getattr(settings, "HOF_STICHTAG", None)
    return fest


def heute() -> datetime.date:
    """Der fachliche Tag: Stichtag im Testhof, sonst das echte Datum (Europe/Berlin)."""
    return stichtag() or timezone.localdate(timezone.now(), ZEITZONE)


def jetzt() -> datetime.datetime:
    """Der fachliche Zeitpunkt (zeitzonenbewusst, Europe/Berlin).

    Im Testhof der Stichtag mit der echten Uhrzeit; ist die Uhr mit einem ``datetime``
    gestellt, genau dieser Zeitpunkt.
    """
    echt = timezone.localtime(timezone.now(), ZEITZONE)
    if _gestellt and isinstance(_gestellt[-1], datetime.datetime):
        return timezone.localtime(_gestellt[-1], ZEITZONE)
    tag = stichtag()
    if tag is None:
        return echt
    return datetime.datetime.combine(tag, echt.time(), tzinfo=ZEITZONE)


@contextmanager
def uhr_stellen(wann: datetime.date | datetime.datetime | None) -> Iterator[None]:
    """Stellt die Uhr für die Dauer des Blocks auf ``wann``. ``None`` lässt sie, wie sie ist.

    Ein ``datetime`` ohne Zeitzone gilt als Europe/Berlin.
    """
    if wann is None:
        yield
        return
    if isinstance(wann, datetime.datetime) and timezone.is_naive(wann):
        wann = wann.replace(tzinfo=ZEITZONE)
    _gestellt.append(wann)
    try:
        yield
    finally:
        _gestellt.pop()
