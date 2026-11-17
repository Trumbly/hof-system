"""Schreibweg des Kerns.

Jede Änderung an Daten läuft über eine Funktion in diesem Modul (CLAUDE.md, Regel 4.1).
Views, Läufe und Importe rufen diese Funktionen auf, nie ``Model.objects.create()``.
"""

from __future__ import annotations

import datetime
import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

import pghistory
from allauth.account.models import EmailAddress
from django.apps import apps
from django.contrib.auth.password_validation import validate_password
from django.contrib.contenttypes.models import ContentType
from django.db import IntegrityError, models, transaction
from django.db.models.deletion import ProtectedError, RestrictedError
from django.utils import timezone

from apps.kern.models import Arbeitsposten, Benutzer, Lauf, Mandant, Objekt
from apps.kern.protokoll import ist_protokolliert

logger = logging.getLogger(__name__)

STANDARD_AKTIONEN = ("erledigen", "zurueckstellen")


# --- Benutzer ------------------------------------------------------------------------


def benutzer_anlegen(email: str, passwort: str) -> Benutzer:
    """Legt den Benutzer an (eine Rolle: darf alles, auch die Verwaltungsseiten).

    Die E-Mail gilt als bestätigt. Den zweiten Faktor richtet der Benutzer bei der
    ersten Anmeldung selbst ein; vorher kommt er nicht über die Anmeldung hinaus.
    """
    email = email.strip().lower()
    benutzer = Benutzer(username=email, email=email, is_staff=True, is_superuser=True)
    validate_password(passwort, user=benutzer)
    benutzer.set_password(passwort)
    benutzer.save()
    EmailAddress.objects.create(user=benutzer, email=email, primary=True, verified=True)
    logger.info("Benutzer #%s angelegt", benutzer.pk)
    return benutzer


# --- Mandant und Objekt --------------------------------------------------------------


def mandant_anlegen(art: str, kuerzel: str, bezeichnung: str) -> Mandant:
    if art not in Mandant.Art.values:
        raise ValueError(f"Unbekannte Mandanten-Art: {art}")
    mandant = Mandant(art=art, kuerzel=kuerzel, bezeichnung=bezeichnung)
    mandant.full_clean()
    mandant.save()
    return mandant


def mandant_aendern(
    mandant: Mandant,
    *,
    bezeichnung: str | None = None,
    aktiv: bool | None = None,
) -> Mandant:
    if bezeichnung is not None:
        mandant.bezeichnung = bezeichnung
    if aktiv is not None:
        mandant.aktiv = aktiv
    mandant.full_clean()
    mandant.save()
    return mandant


def objekt_anlegen(mandant: Mandant, kuerzel: str, bezeichnung: str, anschrift: str = "") -> Objekt:
    objekt = Objekt(mandant=mandant, kuerzel=kuerzel, bezeichnung=bezeichnung, anschrift=anschrift)
    objekt.full_clean()
    objekt.save()
    return objekt


def objekt_aendern(
    objekt: Objekt,
    *,
    bezeichnung: str | None = None,
    anschrift: str | None = None,
    mandant: Mandant | None = None,
    aktiv: bool | None = None,
) -> Objekt:
    if bezeichnung is not None:
        objekt.bezeichnung = bezeichnung
    if anschrift is not None:
        objekt.anschrift = anschrift
    if mandant is not None:
        objekt.mandant = mandant
    if aktiv is not None:
        objekt.aktiv = aktiv
    objekt.full_clean()
    objekt.save()
    return objekt


# --- Läufe ---------------------------------------------------------------------------


def lauf_starten(name: str) -> Lauf:
    """Öffnet einen Lauf im Status „läuft“."""
    return Lauf.objects.create(name=name, gestartet_am=timezone.now())


def lauf_beenden(
    lauf: Lauf,
    *,
    gebucht: int,
    arbeitsposten: int,
    fehler: int,
    meldung: str = "",
    abgebrochen: bool = False,
) -> Lauf:
    """Schließt einen Lauf mit den Zählern der drei Körbe.

    Die Meldung nennt nur IDs und Zähler, keine Personendaten.
    """
    if abgebrochen:
        status = Lauf.Status.ABGEBROCHEN
    elif fehler:
        status = Lauf.Status.MIT_FEHLERN
    else:
        status = Lauf.Status.ERFOLGREICH
    lauf.beendet_am = timezone.now()
    lauf.status = status
    lauf.gebucht = gebucht
    lauf.arbeitsposten = arbeitsposten
    lauf.fehler = fehler
    lauf.meldung = meldung
    lauf.save()
    return lauf


# --- Arbeitsposten -------------------------------------------------------------------


def arbeitsposten_anlegen(
    art: str,
    *,
    bezug: models.Model | None = None,
    aktionen: Iterable[str] = STANDARD_AKTIONEN,
    faellig_am: datetime.date | None = None,
    hinweis: str = "",
    lauf: Lauf | None = None,
) -> Arbeitsposten:
    """Legt einen offenen Arbeitsposten an. Aktionen sind Schlüssel aus ``kern.aktionen``."""
    from apps.kern import aktionen as aktionen_register

    schluessel = list(aktionen)
    unbekannt = [s for s in schluessel if not aktionen_register.ist_registriert(s)]
    if unbekannt:
        raise ValueError(f"Unbekannte Aktionen: {unbekannt}")
    posten = Arbeitsposten(
        art=art,
        aktionen=schluessel,
        faellig_am=faellig_am,
        hinweis=hinweis,
        lauf=lauf,
    )
    if bezug is not None:
        posten.bezug_typ = ContentType.objects.get_for_model(bezug)
        posten.bezug_id = bezug.pk
    posten.save()
    return posten


def arbeitsposten_erledigen(posten: Arbeitsposten) -> Arbeitsposten:
    posten.status = Arbeitsposten.Status.ERLEDIGT
    posten.erledigt_am = timezone.now()
    posten.save(update_fields=["status", "erledigt_am"])
    return posten


def arbeitsposten_zurueckstellen(posten: Arbeitsposten) -> Arbeitsposten:
    posten.status = Arbeitsposten.Status.ZURUECKGESTELLT
    posten.save(update_fields=["status"])
    return posten


def arbeitsposten_wieder_oeffnen(posten: Arbeitsposten) -> Arbeitsposten:
    posten.status = Arbeitsposten.Status.OFFEN
    posten.erledigt_am = None
    posten.save(update_fields=["status", "erledigt_am"])
    return posten


# --- Protokoll und Rücknahme (H11.6) -------------------------------------------------


class RuecknahmeNichtMoeglich(Exception):
    """Die Änderung lässt sich nicht (mehr) zurücknehmen. Die Meldung nennt nur IDs."""


@dataclass(frozen=True)
class Ereignis:
    """Eine protokollierte Änderung, gelesen aus der Tabelle von django-pghistory.

    ``ereignis_id`` hat die Form ``<app>.<Modell>Event:<Nummer>``, wie ``pgh_slug``
    in ``pghistory.models.Events``.
    """

    ereignis_id: str
    art: str  # "insert", "update" oder "delete"
    modell: type[models.Model]
    objekt_id: Any
    daten: dict[str, Any]
    vorher: dict[str, Any] | None = field(default=None)


# Erweiterungsstelle: Gebuchte Werte (ab Schritt 2) werden storniert, nicht
# zurückgedreht. Eine App meldet dafür je Modell eine Funktion an; ``zuruecknehmen``
# ruft dann diese statt der allgemeinen Rücknahme auf.
StornoWeg = Callable[[Ereignis], None]
_STORNOWEGE: dict[type[models.Model], StornoWeg] = {}


def stornoweg_registrieren(modell: type[models.Model], funktion: StornoWeg) -> None:
    """Meldet für ein Modell an, dass Rücknahmen als Stornierung laufen."""
    _STORNOWEGE[modell] = funktion


def stornoweg(modell: type[models.Model]) -> StornoWeg | None:
    return _STORNOWEGE.get(modell)


def _ereignismodell(ereignis_id: str) -> tuple[type[models.Model], int]:
    try:
        modellname, nummer = ereignis_id.rsplit(":", 1)
        app_label, name = modellname.split(".", 1)
        modell = apps.get_model(app_label, name)
        pgh_id = int(nummer)
    except (ValueError, LookupError) as fehler:
        raise RuecknahmeNichtMoeglich(f"Unbekanntes Ereignis {ereignis_id}") from fehler
    verfolgt = getattr(modell, "pgh_tracked_model", None)
    if verfolgt is None or not ist_protokolliert(verfolgt):
        raise RuecknahmeNichtMoeglich(f"Kein Ereignis des Protokolls: {ereignis_id}")
    return modell, pgh_id


def _rueckgaengig_felder(modell: type[models.Model]) -> list[models.Field[Any, Any]]:
    """Felder, die eine Rücknahme wiederherstellt: alle außer Schlüssel und Zeitstempel."""
    return [
        f
        for f in modell._meta.concrete_fields
        if not f.primary_key
        and not getattr(f, "auto_now", False)
        and not getattr(f, "auto_now_add", False)
    ]


def _werte(objekt: models.Model, felder: Iterable[models.Field[Any, Any]]) -> dict[str, Any]:
    return {f.attname: getattr(objekt, f.attname) for f in felder}


def ereignis_laden(ereignis_id: str) -> Ereignis:
    """Liest ein Ereignis samt dem Stand davor (bei Änderungen)."""
    ereignismodell, pgh_id = _ereignismodell(ereignis_id)
    verfolgt: type[models.Model] = ereignismodell.pgh_tracked_model  # type: ignore[attr-defined]
    try:
        zeile = ereignismodell._default_manager.get(pgh_id=pgh_id)
    except ereignismodell.DoesNotExist as fehler:  # type: ignore[attr-defined]
        raise RuecknahmeNichtMoeglich(f"Unbekanntes Ereignis {ereignis_id}") from fehler

    pk_attname = verfolgt._meta.pk.attname
    alle_felder = verfolgt._meta.concrete_fields
    daten = _werte(zeile, alle_felder)
    art: str = getattr(zeile, "pgh_label")  # noqa: B009  (Feld des erzeugten Ereignismodells)
    vorher = None
    if art == "update":
        objekt_id = getattr(zeile, "pgh_obj_id")  # noqa: B009
        vorige = (
            ereignismodell._default_manager.filter(pgh_obj_id=objekt_id, pgh_id__lt=pgh_id)
            .order_by("-pgh_id")
            .first()
        )
        if vorige is None:
            raise RuecknahmeNichtMoeglich(
                f"Zum Ereignis {ereignis_id} fehlt der Stand davor im Protokoll."
            )
        vorher = _werte(vorige, alle_felder)
    return Ereignis(
        ereignis_id=ereignis_id,
        art=art,
        modell=verfolgt,
        objekt_id=daten[pk_attname],
        daten=daten,
        vorher=vorher,
    )


def zuruecknehmen(ereignis_id: str) -> Ereignis:
    """Stellt den Stand vor einer protokollierten Änderung wieder her.

    Die Rücknahme ist selbst eine neue Änderung und steht mit dem Kontext
    ``ruecknahme_von=<ereignis_id>`` im Protokoll.

    - Änderung: Die Felder, die das Ereignis geändert hat, bekommen ihren alten Wert.
      Wurde eines davon seither erneut geändert, bricht die Rücknahme ab.
    - Anlage: Das Objekt wird gelöscht, wenn es seither unverändert ist.
    - Löschung: Das Objekt entsteht mit derselben ID neu.
    - Modelle mit Stornoweg (gebuchte Werte): Die angemeldete Funktion storniert.
    """
    ereignis = ereignis_laden(ereignis_id)
    with transaction.atomic(), pghistory.context(ruecknahme_von=ereignis_id):
        storno = stornoweg(ereignis.modell)
        if storno is not None:
            storno(ereignis)
        elif ereignis.art == "update":
            _aenderung_zuruecknehmen(ereignis)
        elif ereignis.art == "insert":
            _anlage_zuruecknehmen(ereignis)
        elif ereignis.art == "delete":
            _loeschung_zuruecknehmen(ereignis)
        else:
            raise RuecknahmeNichtMoeglich(
                f"Ereignis {ereignis_id} hat die unbekannte Art {ereignis.art}."
            )
    logger.info("Ereignis %s zurückgenommen", ereignis_id)
    return ereignis


def _aktuelles_objekt(ereignis: Ereignis) -> models.Model:
    try:
        return ereignis.modell._default_manager.select_for_update().get(pk=ereignis.objekt_id)
    except ereignis.modell.DoesNotExist as fehler:  # type: ignore[attr-defined]
        raise RuecknahmeNichtMoeglich(
            f"{ereignis.modell._meta.label} #{ereignis.objekt_id} gibt es nicht mehr."
        ) from fehler


def _aenderung_zuruecknehmen(ereignis: Ereignis) -> None:
    if ereignis.vorher is None:
        raise RuecknahmeNichtMoeglich(f"Zum Ereignis {ereignis.ereignis_id} fehlt der Stand davor.")
    objekt = _aktuelles_objekt(ereignis)
    felder = _rueckgaengig_felder(ereignis.modell)
    geaendert = [f for f in felder if ereignis.vorher[f.attname] != ereignis.daten[f.attname]]
    if not geaendert:
        raise RuecknahmeNichtMoeglich(f"Ereignis {ereignis.ereignis_id} hat nichts geändert.")
    spaeter = [f.name for f in geaendert if getattr(objekt, f.attname) != ereignis.daten[f.attname]]
    if spaeter:
        raise RuecknahmeNichtMoeglich(
            f"{ereignis.modell._meta.label} #{ereignis.objekt_id}: {', '.join(spaeter)} "
            "wurde seither erneut geändert. Zuerst die späteren Änderungen zurücknehmen."
        )
    for f in geaendert:
        setattr(objekt, f.attname, ereignis.vorher[f.attname])
    objekt.save(update_fields=[f.name for f in geaendert])


def _anlage_zuruecknehmen(ereignis: Ereignis) -> None:
    objekt = _aktuelles_objekt(ereignis)
    felder = _rueckgaengig_felder(ereignis.modell)
    if _werte(objekt, felder) != {f.attname: ereignis.daten[f.attname] for f in felder}:
        raise RuecknahmeNichtMoeglich(
            f"{ereignis.modell._meta.label} #{ereignis.objekt_id} wurde seit der Anlage "
            "geändert. Zuerst die späteren Änderungen zurücknehmen."
        )
    try:
        objekt.delete()
    except (ProtectedError, RestrictedError) as fehler:
        raise RuecknahmeNichtMoeglich(
            f"{ereignis.modell._meta.label} #{ereignis.objekt_id} wird noch verwendet."
        ) from fehler


def _loeschung_zuruecknehmen(ereignis: Ereignis) -> None:
    if ereignis.modell._default_manager.filter(pk=ereignis.objekt_id).exists():
        raise RuecknahmeNichtMoeglich(
            f"{ereignis.modell._meta.label} #{ereignis.objekt_id} gibt es bereits wieder."
        )
    objekt = ereignis.modell(**ereignis.daten)
    try:
        with transaction.atomic():
            objekt.save(force_insert=True)
    except IntegrityError as fehler:
        raise RuecknahmeNichtMoeglich(
            f"{ereignis.modell._meta.label} #{ereignis.objekt_id} lässt sich nicht "
            "wiederherstellen (Bezug fehlt oder Wert doppelt)."
        ) from fehler
