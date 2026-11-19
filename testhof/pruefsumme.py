"""Hash über die Tabellen: Gleicher Seed muss exakt dieselben Daten ergeben.

Verglichen wird der fachliche Inhalt jeder Tabelle. Drei Dinge gehören nicht dazu,
weil sie sich bei gleichem Inhalt von Lauf zu Lauf unterscheiden müssen:

1. **Auto-IDs.** Postgres zählt Sequenzen nach ``--leeren`` weiter; derselbe Mandant
   hat im zweiten Lauf eine andere ID. Primärschlüssel fallen deshalb weg. Fremdschlüssel
   (auch generische über ``content_type`` und ``object_id``) werden durch den Inhalt der
   Zeile ersetzt, auf die sie zeigen: Entscheidend ist, *worauf* ein Objekt zeigt, nicht
   welche Nummer es gerade hat.
2. **Zeitstempel, die die Uhr setzt.** Felder mit ``auto_now``, ``auto_now_add`` oder
   ``default=timezone.now`` sowie ``UHR_FELDER`` (letzte Anmeldung, letzte Nutzung)
   halten fest, *wann* gespeichert wurde, nicht *was*. Fachliche Zeitpunkte (Buchungstag,
   Vertragsbeginn …) bleiben drin; die Module rechnen sie von ``aufbau.ZEITPUNKT`` aus.
   Ebenso fällt der Passwort-Hash weg (``ZUFALLS_FELDER``): Django salzt jedes Passwort
   absichtlich mit Zufall, gleiches Passwort ergibt also nie denselben Hash. Von Dateien
   (``FileField``, etwa Fotos) zählt nur, ob eine da ist: Ihre Namen sind absichtlich
   zufällig, damit sie keine Teile des Originalnamens tragen.
3. **Protokoll-Tabellen.** Ereignisse von django-pghistory, deren Kontexte, das
   Laufprotokoll (``kern.Lauf``), das Protokoll der Verwaltungsseiten und die Sitzungen
   halten fest, was *wann* passiert ist, und hängen an Uhrzeit und Reihenfolge der
   Läufe. ``--leeren`` leert sie mit (``aufbau.leeren``), Sitzungen ausgenommen.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from django.apps import apps
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.utils import timezone

#: Apps, deren Tabellen Protokolle oder Laufzeitzustand sind.
PROTOKOLL_APPS = frozenset({"pghistory", "sessions", "admin"})
#: Einzelne Modelle, die selbst ein Protokoll sind.
PROTOKOLL_MODELLE = frozenset({"kern.Lauf", "benachrichtigung.Benachrichtigung"})
#: Felder, die die Uhr bei Nutzung setzt (nicht über einen Standardwert erkennbar), etwa
#: das Ende eines Bankabrufs. ``erledigt_am`` setzt ``kern.services.arbeitsposten_erledigen``
#: mit ``zeit.jetzt()``; ``vorgemerkt_am`` und ``abgeglichen_am`` setzt das Cockpit beim
#: Vormerken und Abgleichen der Aufgaben im Hof-Backlog.
UHR_FELDER = frozenset(
    {"last_login", "last_used_at", "beendet_am", "erledigt_am", "vorgemerkt_am", "abgeglichen_am"}
)
#: Felder mit absichtlichem Zufall (Modell-Label, Feld).
ZUFALLS_FELDER = frozenset({("kern.Benutzer", "password")})

PROTOKOLL_VERWEIS = "‹Protokoll›"


def ist_protokoll(modell: type[models.Model]) -> bool:
    meta = modell._meta
    return (
        meta.app_label in PROTOKOLL_APPS
        or meta.label in PROTOKOLL_MODELLE
        or hasattr(modell, "pgh_tracked_model")
    )


def verglichene_modelle() -> list[type[models.Model]]:
    """Alle Tabellen mit eigenem Inhalt, ohne Protokolle, sortiert nach Label."""
    modelle = [
        m
        for m in apps.get_models(include_auto_created=True)
        if m._meta.managed and not m._meta.proxy and not ist_protokoll(m)
    ]
    return sorted(modelle, key=lambda m: m._meta.label)


def _ist_zeitstempel(feld: models.Field[Any, Any]) -> bool:
    if feld.name in UHR_FELDER:
        return True
    if isinstance(feld, models.DateField):
        # zeit: echt (erkennt den Standardwert, fragt die Uhr nicht)
        return bool(feld.auto_now or feld.auto_now_add or feld.default is timezone.now)
    return False


def _generische_id_felder(modell: type[models.Model]) -> dict[str, GenericForeignKey]:
    """``object_id``-Feld je generischem Fremdschlüssel."""
    return {f.fk_field: f for f in modell._meta.private_fields if isinstance(f, GenericForeignKey)}


def _verglichene_felder(modell: type[models.Model]) -> list[models.Field[Any, Any]]:
    return [
        f
        for f in modell._meta.concrete_fields
        if not f.primary_key
        and not _ist_zeitstempel(f)
        and (modell._meta.label, f.name) not in ZUFALLS_FELDER
    ]


def _json(wert: Any) -> str:
    return json.dumps(wert, sort_keys=True, default=str, ensure_ascii=False)


class _Hasher:
    def __init__(self) -> None:
        self._inhalte: dict[tuple[type[models.Model], Any], str] = {}

    @staticmethod
    def _wert(zeile: models.Model, f: models.Field[Any, Any]) -> Any:
        wert = getattr(zeile, f.attname)
        if isinstance(f, models.FileField):
            return bool(wert)
        return wert

    def eigener_inhalt(self, modell: type[models.Model], pk: Any) -> str:
        """Inhalt einer Zeile ohne Schlüssel und Verweise, als Hash (für Fremdschlüssel)."""
        if pk is None:
            return "null"
        if ist_protokoll(modell):
            return PROTOKOLL_VERWEIS
        schluessel = (modell, pk)
        if schluessel not in self._inhalte:
            zeile = modell._default_manager.filter(pk=pk).first()
            if zeile is None:
                inhalt = f"fehlt:{modell._meta.label}"
            else:
                generisch = _generische_id_felder(modell)
                werte = {
                    f.attname: self._wert(zeile, f)
                    for f in _verglichene_felder(modell)
                    if not f.is_relation and f.attname not in generisch
                }
                inhalt = hashlib.sha256(_json(werte).encode()).hexdigest()
            self._inhalte[schluessel] = inhalt
        return self._inhalte[schluessel]

    def zeile(self, modell: type[models.Model], zeile: models.Model) -> str:
        generisch = _generische_id_felder(modell)
        werte: dict[str, Any] = {}
        for f in _verglichene_felder(modell):
            wert = self._wert(zeile, f)
            if f.is_relation and f.related_model is not None:
                ziel: type[models.Model] = f.related_model  # type: ignore[assignment]
                if ziel is ContentType and wert is not None:
                    typ = ContentType.objects.get_for_id(wert)
                    wert = f"{typ.app_label}.{typ.model}"
                else:
                    wert = self.eigener_inhalt(ziel, wert)
            elif f.attname in generisch:
                gfk = generisch[f.attname]
                typ_id = getattr(zeile, modell._meta.get_field(gfk.ct_field).attname)
                ziel_modell = (
                    ContentType.objects.get_for_id(typ_id).model_class() if typ_id else None
                )
                wert = self.eigener_inhalt(ziel_modell, wert) if ziel_modell is not None else wert
            werte[f.attname] = wert
        return _json(werte)


def tabellen_hashes() -> dict[str, str]:
    """Hash je Tabelle (Modell-Label), über alle Zeilen in sortierter Reihenfolge."""
    hasher = _Hasher()
    ergebnis = {}
    for modell in verglichene_modelle():
        zeilen = sorted(hasher.zeile(modell, z) for z in modell._default_manager.all())
        inhalt = "\n".join(zeilen)
        ergebnis[modell._meta.label] = hashlib.sha256(inhalt.encode()).hexdigest()
    return ergebnis


def pruefsumme() -> str:
    """Ein Hash über alle Tabellen."""
    return hashlib.sha256(_json(tabellen_hashes()).encode()).hexdigest()


def zeilen_je_tabelle() -> dict[str, int]:
    """Anzahl der Zeilen je verglichener Tabelle, nur Tabellen mit Inhalt."""
    anzahl = {m._meta.label: m._default_manager.count() for m in verglichene_modelle()}
    return {label: n for label, n in anzahl.items() if n}
