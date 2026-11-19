"""Filter ``|euro`` und ``|zahl`` für jede Vorlage (in den Einstellungen als ``builtins``
eingetragen, kein ``{% load %}`` nötig). Das Format selbst steht in ``apps.kern.zahlen``.

    {{ summe|euro }}          → 209.533,70 €
    {{ stand.wert|zahl }}     → 9.602,7
    {{ flaeche|zahl:1 }}      → 18,0
"""

from __future__ import annotations

from typing import Any

from django import template

from apps.kern import zahlen

register = template.Library()


@register.filter(name="euro", is_safe=True)
def euro(wert: Any) -> str:
    return zahlen.euro(wert)


@register.filter(name="zahl", is_safe=True)
def zahl(wert: Any, stellen: Any = None) -> str:
    if stellen in (None, ""):
        return zahlen.zahl(wert)
    return zahlen.zahl(wert, int(stellen))
