"""Kontext für jede Vorlage (Context Processor)."""

from __future__ import annotations

from typing import Any

from django.conf import settings
from django.http import HttpRequest


def hof(request: HttpRequest) -> dict[str, Any]:
    """``hof_stichtag``: der feste Stichtag aus ``HOF_STICHTAG`` (nur dev und test), sonst
    ``None``. Ist er gesetzt, zeigt jede Seite das Band „Testhof · Stichtag …“."""
    return {"hof_stichtag": getattr(settings, "HOF_STICHTAG", None)}
