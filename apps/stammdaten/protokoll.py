"""Stammdaten im Protokoll (Review 2, B14): Filter „nach Vertrag/Einheit“ und „nach Person“.

Der Filter ``?bezug=stammdaten.einheit:<ID>`` zeigt auch die Vorgänge der Verträge der
Einheit, ``?bezug=stammdaten.partei:<ID>`` die der Verträge der Partei.
"""

from __future__ import annotations

from collections.abc import Iterable

from django.db import models

from apps.kern import vorgaenge
from apps.stammdaten.models import Einheit, Partei, Vertrag


def _vertraege_der_einheit(einheit: Einheit) -> Iterable[models.Model]:
    return Vertrag.objects.filter(einheiten=einheit).distinct()


def _vertraege_der_partei(partei: Partei) -> Iterable[models.Model]:
    return Vertrag.objects.filter(vertragsparteien__partei=partei).distinct()


def registrieren() -> None:
    vorgaenge.bezug_erweitern_registrieren(Einheit, _vertraege_der_einheit)
    vorgaenge.bezug_erweitern_registrieren(Partei, _vertraege_der_partei)
