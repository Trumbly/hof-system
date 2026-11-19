"""Kontext für jede Vorlage (Context Processor)."""

from __future__ import annotations

from typing import Any

from django.http import HttpRequest

from apps.kern import zeit


def hof(request: HttpRequest) -> dict[str, Any]:
    """``hof_stichtag``: der feste Stichtag (``HOF_STICHTAG``, nur dev und test), sonst
    ``None``. Ist er gesetzt, zeigt jede Seite das Band „Testhof · Stichtag …“. Er kommt aus
    ``kern.zeit`` wie jedes „heute“, Band und Seiten zeigen also denselben Tag."""
    return {"hof_stichtag": zeit.stichtag()}
