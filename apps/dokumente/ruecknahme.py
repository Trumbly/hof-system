"""Rücknahme eines Vorgangs und Schreiben (Review 2, A4).

- **Gesperrt**, wenn ein Schreiben aus dem Vorgang (oder eines an einem Objekt, das der
  Vorgang angelegt hat) schon versandt ist: gesendet oder zugestellt. Was hinausgegangen
  ist, holt keine Rücknahme zurück. Die Sperre verlinkt das Schreiben.
- Ein Schreiben, das der Vorgang angelegt hat und das noch „bereit“ ist, wird
  **verworfen** statt gelöscht (Stornieren statt löschen, CLAUDE.md 4.4).
- Bereite Schreiben an einem Objekt, das die Rücknahme entfernt (etwa an der Kündigung),
  werden ebenfalls verworfen: Sie hätten sonst keinen Bezug mehr.
"""

from __future__ import annotations

from typing import Any

from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from apps.dokumente import services, vorlagenarten
from apps.dokumente.models import Schreiben
from apps.kern import services as kern

VERSANDT = (Schreiben.Status.GESENDET, Schreiben.Status.ZUGESTELLT)


def _url(schreiben: Schreiben) -> str:
    return reverse("dokumente:schreiben", args=[schreiben.pk])


def _art(schreiben: Schreiben) -> str:
    """Die Vorlagenart lesbar („Kündigungsbestätigung“), sonst ihr Schlüssel."""
    try:
        return vorlagenarten.holen(schreiben.art).titel
    except vorlagenarten.UnbekannteVorlagenart:
        return schreiben.art


def _versandt_pruefen(plan: kern.Ruecknahmeplan) -> list[kern.Sperre]:
    bedingung = Q(pk__in=[a.objekt_id for a in plan.von(Schreiben)])
    for a in plan.aenderungen:
        if a.angelegt and not a.geloescht:
            bedingung |= Q(
                bezug_typ=ContentType.objects.get_for_model(a.modell), bezug_id=a.objekt_id
            )
    sperren = []
    for schreiben in Schreiben.objects.filter(bedingung, status__in=VERSANDT).order_by("pk"):
        wann = (
            f" am {timezone.localtime(schreiben.gesendet_am):%d.%m.%Y}"
            if schreiben.gesendet_am
            else ""
        )
        sperren.append(
            kern.Sperre(
                f"Schreiben #{schreiben.pk} ({_art(schreiben)}) ist schon "
                f"{schreiben.get_status_display()}{wann}. Was versandt ist, lässt sich nicht "
                "zurückholen; bitte stattdessen ein neues Schreiben aufsetzen.",
                url=_url(schreiben),
                link_titel=f"Schreiben #{schreiben.pk} ansehen",
            )
        )
    return sperren


def _verwerfen(schreiben: Schreiben) -> kern.Schritt | None:
    if schreiben.status != Schreiben.Status.BEREIT:
        return None
    services.schreiben_verwerfen(schreiben)
    return kern.Schritt(f"Schreiben #{schreiben.pk} ({_art(schreiben)}) verworfen", _url(schreiben))


def _abhaengige_verwerfen(modell: type[models.Model], objekt_id: Any) -> list[kern.Schritt]:
    schritte = []
    for schreiben in Schreiben.objects.filter(
        bezug_typ=ContentType.objects.get_for_model(modell),
        bezug_id=objekt_id,
        status=Schreiben.Status.BEREIT,
    ):
        schritt = _verwerfen(schreiben)
        if schritt is not None:
            schritte.append(schritt)
    return schritte


def registrieren() -> None:
    kern.ruecknahme_pruefung_registrieren(_versandt_pruefen)
    kern.ruecknahmeweg_registrieren(Schreiben, anlage=_verwerfen)
    kern.ruecknahme_aufraeumen_registrieren(_abhaengige_verwerfen)
