"""Schreibweg des Kerns.

Jede Änderung an Daten läuft über eine Funktion in diesem Modul (CLAUDE.md, Regel 4.1).
Views, Läufe und Importe rufen diese Funktionen auf, nie ``Model.objects.create()``.
"""

from __future__ import annotations

import base64
import binascii
import datetime
import logging
import uuid
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from allauth.account.models import EmailAddress
from allauth.mfa.models import Authenticator
from allauth.mfa.totp.internal.auth import TOTP
from django.apps import apps
from django.contrib.auth.password_validation import validate_password
from django.contrib.contenttypes.models import ContentType
from django.db import IntegrityError, models, transaction
from django.db.models.deletion import Collector, ProtectedError, RestrictedError
from django.urls import reverse
from django.utils import timezone
from pghistory.models import Context, Events

from apps.kern import aufgaben, vorgaenge, zeit
from apps.kern.models import Arbeitsposten, Benutzer, Lauf, Mandant, Objekt
from apps.kern.protokoll import ist_protokolliert
from apps.kern.vorgaenge import vorgang

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


def zweiten_faktor_einrichten(benutzer: Benutzer, geheimnis: str) -> Authenticator:
    """Richtet den zweiten Faktor (TOTP) mit einem vorgegebenen Geheimnis ein.

    Im Betrieb richtet der Benutzer TOTP bei der ersten Anmeldung selbst ein. Diese
    Funktion ist für den Testhof und die Tests, die ein festes Geheimnis brauchen.
    Das Geheimnis ist Base32 (wie in der Authenticator-App).
    """
    try:
        base64.b32decode(geheimnis, casefold=True)
    except binascii.Error as fehler:
        raise ValueError("Das TOTP-Geheimnis ist kein gültiges Base32.") from fehler
    totp: Any = TOTP.activate(benutzer, geheimnis)
    authenticator: Authenticator = totp.instance
    logger.info("Zweiter Faktor für Benutzer #%s eingerichtet", benutzer.pk)
    return authenticator


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
    return Lauf.objects.create(name=name, gestartet_am=timezone.now())  # zeit: echt (Laufprotokoll)


def lauf_beenden(
    lauf: Lauf,
    *,
    gebucht: int,
    arbeitsposten: int,
    fehler: int,
    meldung: str = "",
    abgebrochen: bool = False,
    uebersprungen: bool = False,
) -> Lauf:
    """Schließt einen Lauf mit den Zählern der drei Körbe.

    Die Meldung nennt nur IDs und Zähler, keine Personendaten. ``uebersprungen``: Der
    Lauf hatte nichts zu tun (Status „übersprungen“); Abbruch und Fehler gehen vor.
    """
    if abgebrochen:
        status = Lauf.Status.ABGEBROCHEN
    elif fehler:
        status = Lauf.Status.MIT_FEHLERN
    elif uebersprungen:
        status = Lauf.Status.UEBERSPRUNGEN
    else:
        status = Lauf.Status.ERFOLGREICH
    lauf.beendet_am = timezone.now()  # zeit: echt (Laufprotokoll)
    lauf.status = status
    lauf.gebucht = gebucht
    lauf.arbeitsposten = arbeitsposten
    lauf.fehler = fehler
    lauf.meldung = meldung
    lauf.save()
    return lauf


class LaufNichtGestartet(ValueError):
    """Den Befehl gibt es nicht, oder er ist kein Lauf (``LaufBefehl``)."""


def lauf_ausfuehren(befehl: str, **optionen: Any) -> Lauf:
    """Führt einen Lauf sofort aus, wie der Timer auf dem Server (Review 2, B16: Knopf
    „jetzt ausführen“; „Erneut abrufen“ an einem Bankkonto).

    Rückgabe ist der Eintrag im Laufprotokoll. Bricht der Lauf ab, ist das kein Fehler
    dieser Funktion: Er steht dann als „abgebrochen“ im Protokoll, seine Änderungen sind
    zurückgerollt (``LaufBefehl``). Welche Läufe die Oberfläche anbietet, entscheidet sie
    selbst (``apps.kern.views.ausfuehrbare_laeufe``).
    """
    from io import StringIO

    from django.core.management import call_command, get_commands, load_command_class
    from django.core.management.base import CommandError

    from apps.kern.laeufe import LaufBefehl

    app = get_commands().get(befehl)
    if app is None:
        raise LaufNichtGestartet(f"Den Lauf {befehl} gibt es nicht.")
    instanz = load_command_class(app, befehl)
    if not isinstance(instanz, LaufBefehl):
        raise LaufNichtGestartet(f"{befehl} ist kein Lauf.")
    try:
        call_command(instanz, stdout=StringIO(), stderr=StringIO(), **optionen)
    except CommandError:
        # LaufBefehl hat den Abbruch schon im Laufprotokoll festgehalten.
        logger.info("Lauf %s aus der Oberfläche abgebrochen", befehl)
    lauf: Lauf | None = getattr(instanz, "lauf", None)
    if lauf is None or lauf.pk is None:
        raise LaufNichtGestartet(f"Der Lauf {befehl} ist nicht gestartet.")
    lauf.refresh_from_db()
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
    aufgaben.melden(posten)
    return posten


def arbeitsposten_erledigen(posten: Arbeitsposten) -> Arbeitsposten:
    posten.status = Arbeitsposten.Status.ERLEDIGT
    posten.erledigt_am = zeit.jetzt()
    posten.save(update_fields=["status", "erledigt_am"])
    aufgaben.melden(posten)
    return posten


def arbeitsposten_zurueckstellen(posten: Arbeitsposten) -> Arbeitsposten:
    posten.status = Arbeitsposten.Status.ZURUECKGESTELLT
    posten.save(update_fields=["status"])
    aufgaben.melden(posten)
    return posten


def arbeitsposten_hinweis_setzen(posten: Arbeitsposten, hinweis: str) -> Arbeitsposten:
    """Ändert den Hinweis eines Postens, etwa „überfällig“ (nur IDs und Daten, keine Namen)."""
    posten.hinweis = hinweis
    posten.save(update_fields=["hinweis"])
    aufgaben.melden(posten)
    return posten


def arbeitsposten_wieder_oeffnen(posten: Arbeitsposten) -> Arbeitsposten:
    posten.status = Arbeitsposten.Status.OFFEN
    posten.erledigt_am = None
    posten.save(update_fields=["status", "erledigt_am"])
    aufgaben.melden(posten)
    return posten


def verwaiste_arbeitsposten() -> list[Arbeitsposten]:
    """Offene und zurückgestellte Posten, deren Bezug es nicht mehr gibt.

    Ein Posten ohne Bezug (``bezug_typ`` leer) ist nicht verwaist. Fehlt das Modell des
    Bezugs (``ContentType`` ohne Klasse), gilt der Bezug als fehlend.
    """
    offen = Arbeitsposten.objects.filter(
        status__in=[Arbeitsposten.Status.OFFEN, Arbeitsposten.Status.ZURUECKGESTELLT],
        bezug_typ__isnull=False,
    )
    verwaist: list[Arbeitsposten] = []
    for typ_id in offen.values_list("bezug_typ", flat=True).distinct().order_by():
        modell = ContentType.objects.get_for_id(typ_id).model_class()
        posten = offen.filter(bezug_typ_id=typ_id)
        if modell is None:
            verwaist += list(posten)
            continue
        ids = set(posten.values_list("bezug_id", flat=True))
        vorhanden = set(modell._default_manager.filter(pk__in=ids).values_list("pk", flat=True))
        verwaist += [p for p in posten if p.bezug_id not in vorhanden]
    return sorted(verwaist, key=lambda p: p.pk)


def verwaiste_arbeitsposten_schliessen(*, lauf: Lauf | None = None) -> list[Arbeitsposten]:
    """Erledigt Posten, deren Bezug fehlt (Review 1, Punkt 3), und protokolliert das.

    Der Hinweis bekommt einen Vermerk mit Bezug und Lauf (nur IDs); die Änderung steht im
    Protokoll, mit dem Lauf als Kontext, und im Log.
    """
    geschlossen = []
    for posten in verwaiste_arbeitsposten():
        typ = ContentType.objects.get_for_id(posten.bezug_typ_id) if posten.bezug_typ_id else None
        bezug = f"{typ.app_label}.{typ.model} #{posten.bezug_id}" if typ else f"#{posten.bezug_id}"
        vermerk = f"Geschlossen: Bezug {bezug} fehlt" + (f" (Lauf #{lauf.pk})." if lauf else ".")
        posten.hinweis = f"{posten.hinweis}\n{vermerk}".strip()
        posten.status = Arbeitsposten.Status.ERLEDIGT
        posten.erledigt_am = zeit.jetzt()
        posten.save(update_fields=["hinweis", "status", "erledigt_am"])
        aufgaben.melden(posten)
        logger.info("Arbeitsposten #%s geschlossen: Bezug %s fehlt", posten.pk, bezug)
        geschlossen.append(posten)
    return geschlossen


# --- Protokoll und Rücknahme (H11.6; Review 2, A4) -----------------------------------
#
# Das Protokoll zeigt Vorgänge (``apps.kern.vorgaenge``): alle Änderungen einer Nutzer-
# aktion oder eines Laufs. Zurückgenommen wird nur ein ganzer Vorgang
# (``vorgang_zuruecknehmen``). Die Rücknahme eines einzelnen Ereignisses
# (``zuruecknehmen``) bleibt als Baustein und für Tests; die Oberfläche bietet sie nicht an.


class RuecknahmeNichtMoeglich(Exception):
    """Die Änderung lässt sich nicht (mehr) zurücknehmen. Die Meldung ist ein ganzer Satz
    und nennt nur Nummern und IDs."""


@dataclass(frozen=True)
class Sperre:
    """Warum sich ein Vorgang nicht zurücknehmen lässt, mit Link zur Ursache."""

    grund: str
    url: str = ""
    link_titel: str = ""


class RuecknahmeGesperrt(RuecknahmeNichtMoeglich):
    """Der Vorgang lässt sich grundsätzlich nicht zurücknehmen (etwa: Schreiben versandt)."""

    def __init__(self, sperren: Sequence[Sperre]) -> None:
        self.sperren = list(sperren)
        super().__init__(" ".join(s.grund for s in self.sperren))


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
    zeit: datetime.datetime | None = field(default=None)
    vorgang_id: uuid.UUID | None = field(default=None)


@dataclass(frozen=True)
class Schritt:
    """Ein Schritt einer Rücknahme, lesbar und mit optionalem Link."""

    text: str
    url: str = ""
    #: Das Objekt ist entfernt (danach räumen die Apps ab, was daran hängt).
    entfernt: bool = False


@dataclass
class Objektaenderung:
    """Was ein Vorgang an einem Objekt geändert hat: alle seine Ereignisse dazu, der Reihe
    nach."""

    modell: type[models.Model]
    objekt_id: Any
    ereignisse: list[Ereignis]

    @property
    def erstes(self) -> Ereignis:
        return self.ereignisse[0]

    @property
    def letztes(self) -> Ereignis:
        return self.ereignisse[-1]

    @property
    def angelegt(self) -> bool:
        return self.erstes.art == "insert"

    @property
    def geloescht(self) -> bool:
        return self.letztes.art == "delete"

    @property
    def vorher(self) -> dict[str, Any] | None:
        """Der Stand vor dem Vorgang (``None``: Der Vorgang hat das Objekt angelegt)."""
        if self.erstes.art == "insert":
            return None
        if self.erstes.art == "delete":
            return self.erstes.daten
        return self.erstes.vorher

    @property
    def nachher(self) -> dict[str, Any] | None:
        """Der Stand nach dem Vorgang (``None``: Der Vorgang hat das Objekt gelöscht)."""
        return None if self.geloescht else self.letztes.daten

    @property
    def titel(self) -> str:
        return vorgaenge.objekt_titel(self.modell, self.letztes.daten)


@dataclass
class Ruecknahmeplan:
    """Ein Vorgang, bereit zur Rücknahme: seine Änderungen je Objekt, nach erster Änderung."""

    vorgang_id: uuid.UUID
    titel: str
    aenderungen: list[Objektaenderung]

    def von(self, modell: type[models.Model]) -> list[Objektaenderung]:
        return [a for a in self.aenderungen if a.modell is modell]

    @property
    def anzahl_ereignisse(self) -> int:
        return sum(len(a.ereignisse) for a in self.aenderungen)


@dataclass
class Ruecknahmebericht:
    """Was eine Rücknahme getan hat (oder in der Vorschau täte)."""

    vorgang_id: uuid.UUID
    titel: str
    ereignisse: int
    ruecknahme_id: uuid.UUID | None = None
    schritte: list[Schritt] = field(default_factory=list)


# Erweiterungsstelle: Gebuchte Werte (ab Schritt 2) werden storniert, nicht
# zurückgedreht. Eine App meldet dafür je Modell eine Funktion an; die Rücknahme
# ruft dann diese statt der allgemeinen auf.
StornoWeg = Callable[[Ereignis], None]
_STORNOWEGE: dict[type[models.Model], StornoWeg] = {}


def stornoweg_registrieren(modell: type[models.Model], funktion: StornoWeg) -> None:
    """Meldet für ein Modell an, dass Rücknahmen als Stornierung laufen."""
    _STORNOWEGE[modell] = funktion


def stornoweg(modell: type[models.Model]) -> StornoWeg | None:
    return _STORNOWEGE.get(modell)


@dataclass(frozen=True)
class Ruecknahmeweg:
    """Wie die Rücknahme eines Vorgangs mit einem Modell umgeht (statt der allgemeinen).

    - ``anlage``: Hat der Vorgang das Objekt angelegt, ersetzt diese Funktion das Löschen
      (Schreiben: verwerfen; Arbeitsposten: erledigen). Rückgabe: der Schritt oder ``None``.
    - ``danach``: läuft nach der allgemeinen Rücknahme einer Änderung (etwa „melden“).
    - ``abgeleitet``: ``True`` für Ereignisse, die nicht zurückgedreht, sondern danach neu
      abgeleitet werden (Sollposten aus dem Vertrag: der Soll-Haken im Nachlauf).
    """

    anlage: Callable[[Any], Schritt | None] | None = None
    danach: Callable[[Any], None] | None = None
    abgeleitet: Callable[[dict[str, Any]], bool] | None = None


Pruefung = Callable[["Ruecknahmeplan"], list[Sperre]]
Aufraeumen = Callable[[type[models.Model], Any], list[Schritt]]
Nachlauf = Callable[["Ruecknahmeplan"], list[Schritt]]

_RUECKNAHMEWEGE: dict[type[models.Model], Ruecknahmeweg] = {}
_PRUEFUNGEN: list[Pruefung] = []
_AUFRAEUMEN: list[Aufraeumen] = []
_NACHLAEUFE: list[Nachlauf] = []


def ruecknahmeweg_registrieren(
    modell: type[models.Model],
    *,
    anlage: Callable[[Any], Schritt | None] | None = None,
    danach: Callable[[Any], None] | None = None,
    abgeleitet: Callable[[dict[str, Any]], bool] | None = None,
) -> None:
    """Meldet an, wie die Rücknahme eines Vorgangs mit ``modell`` umgeht."""
    _RUECKNAHMEWEGE[modell] = Ruecknahmeweg(anlage=anlage, danach=danach, abgeleitet=abgeleitet)


def ruecknahme_pruefung_registrieren(funktion: Pruefung) -> None:
    """Eine Prüfung vor jeder Rücknahme; liefert sie Sperren, ist die Rücknahme gesperrt."""
    if funktion not in _PRUEFUNGEN:
        _PRUEFUNGEN.append(funktion)


def ruecknahme_aufraeumen_registrieren(funktion: Aufraeumen) -> None:
    """Läuft für jedes Objekt, das eine Rücknahme entfernt hat (Abhängige schließen)."""
    if funktion not in _AUFRAEUMEN:
        _AUFRAEUMEN.append(funktion)


def ruecknahme_nachlauf_registrieren(funktion: Nachlauf) -> None:
    """Läuft am Ende jeder Rücknahme, im selben Vorgang (z. B. der Soll-Haken)."""
    if funktion not in _NACHLAEUFE:
        _NACHLAEUFE.append(funktion)


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
        zeit=getattr(zeile, "pgh_created_at", None),
        vorgang_id=getattr(zeile, "pgh_context_id", None),
    )


def zuruecknehmen(ereignis_id: str) -> Ereignis:
    """Stellt den Stand vor einem einzelnen protokollierten Ereignis wieder her.

    Baustein und für Tests: Die Oberfläche nimmt nur ganze Vorgänge zurück
    (``vorgang_zuruecknehmen``). Die Rücknahme ist ein eigener Vorgang mit dem Kontext
    ``ruecknahme_ereignis=<ereignis_id>``.

    - Änderung: Die Felder, die das Ereignis geändert hat, bekommen ihren alten Wert.
      Wurde eines davon seither erneut geändert, bricht die Rücknahme ab.
    - Anlage: Das Objekt wird gelöscht, wenn es seither unverändert ist.
    - Löschung: Das Objekt entsteht mit derselben ID neu.
    - Modelle mit Stornoweg (gebuchte Werte): Die angemeldete Funktion storniert.
    """
    ereignis = ereignis_laden(ereignis_id)
    with (
        transaction.atomic(),
        vorgang(
            f"Rücknahme von {ereignis_id}",
            art=vorgaenge.Art.RUECKNAHME,
            neu=True,
            ruecknahme_ereignis=ereignis_id,
        ),
    ):
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


def _loeschung_zuruecknehmen(ereignis: Ereignis, daten: dict[str, Any] | None = None) -> None:
    if ereignis.modell._default_manager.filter(pk=ereignis.objekt_id).exists():
        raise RuecknahmeNichtMoeglich(
            f"{ereignis.modell._meta.label} #{ereignis.objekt_id} gibt es bereits wieder."
        )
    objekt = ereignis.modell(**(daten or ereignis.daten))
    try:
        with transaction.atomic():
            objekt.save(force_insert=True)
    except IntegrityError as fehler:
        raise RuecknahmeNichtMoeglich(
            f"{ereignis.modell._meta.label} #{ereignis.objekt_id} lässt sich nicht "
            "wiederherstellen (Bezug fehlt oder Wert doppelt)."
        ) from fehler


# --- Vorgang zurücknehmen ------------------------------------------------------------


def _vorgang_id(wert: uuid.UUID | str) -> uuid.UUID:
    try:
        return wert if isinstance(wert, uuid.UUID) else uuid.UUID(str(wert))
    except ValueError as fehler:
        raise RuecknahmeNichtMoeglich("Diesen Vorgang gibt es nicht.") from fehler


def ruecknahme_planen(vorgang_id: uuid.UUID | str) -> Ruecknahmeplan:
    """Liest einen Vorgang und prüft, ob er sich zurücknehmen lässt.

    Raises:
        RuecknahmeGesperrt: grundsätzlich nicht (Rücknahme einer Rücknahme, schon
            zurückgenommen, Aufbau des Testhofs, versandtes Schreiben …), mit Gründen.
        RuecknahmeNichtMoeglich: Den Vorgang gibt es nicht, oder er hat nichts geändert.
    """
    vid = _vorgang_id(vorgang_id)
    kontext = Context.objects.filter(pk=vid).first()
    if kontext is None:
        raise RuecknahmeNichtMoeglich("Diesen Vorgang gibt es nicht.")
    meta: dict[str, Any] = kontext.metadata or {}
    if meta.get("ruecknahme_von") or meta.get("ruecknahme_ereignis"):
        raise RuecknahmeGesperrt(
            [
                Sperre(
                    "Eine Rücknahme lässt sich nicht zurücknehmen. Bitte die ursprüngliche "
                    "Aktion neu ausführen, etwa die Kündigung neu erfassen."
                )
            ]
        )
    if "testhof_seed" in meta or "testhof_leeren" in meta:
        raise RuecknahmeGesperrt(
            [
                Sperre(
                    "Den Aufbau des Testhofs nimmt man nicht zurück; „hof testhof“ baut ihn "
                    "neu auf."
                )
            ]
        )
    schon = Context.objects.filter(metadata__ruecknahme_von=str(vid)).first()
    if schon is not None:
        raise RuecknahmeGesperrt(
            [
                Sperre(
                    "Dieser Vorgang ist schon zurückgenommen.",
                    url=reverse("kern:vorgang", args=[schon.pk]),
                    link_titel="Rücknahme ansehen",
                )
            ]
        )
    slugs = list(
        Events.objects.filter(pgh_context_id=vid)
        .order_by("pgh_created_at", "pgh_model", "pgh_id")
        .values_list("pgh_slug", flat=True)
    )
    if not slugs:
        raise RuecknahmeNichtMoeglich("Dieser Vorgang hat nichts geändert.")

    je_objekt: dict[tuple[type[models.Model], Any], Objektaenderung] = {}
    for slug in slugs:
        ereignis = ereignis_laden(slug)
        schluessel = (ereignis.modell, ereignis.objekt_id)
        if schluessel in je_objekt:
            je_objekt[schluessel].ereignisse.append(ereignis)
        else:
            je_objekt[schluessel] = Objektaenderung(ereignis.modell, ereignis.objekt_id, [ereignis])
    plan = Ruecknahmeplan(
        vorgang_id=vid, titel=vorgaenge.titel_von(vid), aenderungen=list(je_objekt.values())
    )
    sperren = [sperre for pruefung in _PRUEFUNGEN for sperre in pruefung(plan)]
    if sperren:
        raise RuecknahmeGesperrt(sperren)
    return plan


def vorgang_zuruecknehmen(vorgang_id: uuid.UUID | str) -> Ruecknahmebericht:
    """Nimmt einen ganzen Vorgang zurück (Review 2, A4).

    Die Änderungen des Vorgangs werden je Objekt in umgekehrter Reihenfolge zurückgenommen,
    als ein neuer, protokollierter Vorgang „Rücknahme von …“ (Kontext ``ruecknahme_von``).

    - Angelegtes wird entfernt, wenn es seither unverändert ist und nichts daran hängt.
      Ausnahmen über ``ruecknahmeweg_registrieren``: Schreiben im Status „bereit“ werden
      verworfen, Arbeitsposten erledigt (damit schließt auch die Aufgabe im Hof-Backlog).
    - Gebuchtes läuft über die Stornowege (``stornoweg_registrieren``).
    - Geändertes bekommt seinen Stand vor dem Vorgang. Wurde ein Feld seither in einem
      anderen Vorgang geändert, bricht die Rücknahme mit einer Meldung ab, die sagt, wann.
    - Für jedes entfernte Objekt räumen die Apps ab, was daran hängt (offene Arbeitsposten,
      Fristen, bereite Schreiben). Zum Schluss laufen die Nachläufe, etwa der Soll-Haken.

    Alles in einer Transaktion: Scheitert ein Schritt, bleibt alles, wie es war.
    """
    plan = ruecknahme_planen(vorgang_id)
    with (
        transaction.atomic(),
        vorgang(
            f"Rücknahme von „{plan.titel}“",
            art=vorgaenge.Art.RUECKNAHME,
            neu=True,
            ruecknahme_von=str(plan.vorgang_id),
        ) as neu,
    ):
        bericht = _plan_ausfuehren(plan)
        bericht.ruecknahme_id = neu.id
        # Auch eine Rücknahme ohne eigene Änderung steht im Protokoll (sonst ließe sich der
        # Vorgang ein zweites Mal „zurücknehmen“).
        Context.objects.get_or_create(pk=neu.id, defaults={"metadata": vorgaenge.metadaten()})
    logger.info(
        "Vorgang %s zurückgenommen als Vorgang %s (%s Ereignisse)",
        plan.vorgang_id,
        bericht.ruecknahme_id,
        bericht.ereignisse,
    )
    return bericht


class _Probelauf(Exception):
    def __init__(self, bericht: Ruecknahmebericht) -> None:
        super().__init__("Probelauf")
        self.bericht = bericht


def ruecknahme_vorschau(vorgang_id: uuid.UUID | str) -> Ruecknahmebericht:
    """Was ``vorgang_zuruecknehmen`` täte, ohne etwas zu ändern (Probelauf, zurückgerollt).

    Raises wie ``vorgang_zuruecknehmen``.
    """
    try:
        with transaction.atomic():
            bericht = vorgang_zuruecknehmen(vorgang_id)
            bericht.ruecknahme_id = None
            raise _Probelauf(bericht)
    except _Probelauf as probe:
        return probe.bericht


def _plan_ausfuehren(plan: Ruecknahmeplan) -> Ruecknahmebericht:
    bericht = Ruecknahmebericht(
        vorgang_id=plan.vorgang_id, titel=plan.titel, ereignisse=plan.anzahl_ereignisse
    )
    entfernt: list[tuple[type[models.Model], Any]] = []
    for aenderung in reversed(plan.aenderungen):
        weg = _RUECKNAHMEWEGE.get(aenderung.modell, Ruecknahmeweg())
        if weg.abgeleitet is not None and all(
            weg.abgeleitet(e.daten) for e in aenderung.ereignisse
        ):
            continue  # wird im Nachlauf neu abgeleitet
        if aenderung.angelegt and aenderung.geloescht:
            continue  # im Vorgang angelegt und wieder gelöscht
        if aenderung.angelegt:
            schritt = _angelegtes_zuruecknehmen(plan, aenderung, weg)
        elif aenderung.geloescht:
            schritt = _geloeschtes_zuruecknehmen(aenderung)
        else:
            schritt = _geaendertes_zuruecknehmen(plan, aenderung, weg)
        if schritt is not None:
            bericht.schritte.append(schritt)
            if schritt.entfernt:
                entfernt.append((aenderung.modell, aenderung.objekt_id))
    # Im Bericht in der Reihenfolge des Vorgangs: Kündigung, Vertrag, Schreiben, Fristen …
    bericht.schritte.reverse()
    for aufraeumen in _AUFRAEUMEN:
        for modell, objekt_id in entfernt:
            bericht.schritte += aufraeumen(modell, objekt_id)
    for nachlauf in _NACHLAEUFE:
        bericht.schritte += nachlauf(plan)
    return bericht


def _objekt_holen(aenderung: Objektaenderung) -> models.Model | None:
    objekt: models.Model | None = (
        aenderung.modell._default_manager.select_for_update().filter(pk=aenderung.objekt_id).first()
    )
    return objekt


def _spaeter_geaendert(
    plan: Ruecknahmeplan, aenderung: Objektaenderung, felder: Sequence[models.Field[Any, Any]]
) -> str:
    """Meldung, wenn ein Objekt nach dem Vorgang in einem anderen Vorgang geändert wurde."""
    namen = ", ".join(vorgaenge.feld_titel(aenderung.modell, f.attname) for f in felder)
    spaeter = (
        Events.objects.filter(
            pgh_obj_model=aenderung.modell._meta.label,
            pgh_obj_id=str(aenderung.objekt_id),
        )
        .exclude(pgh_context_id=plan.vorgang_id)
        .order_by("-pgh_created_at")
        .first()
    )
    if spaeter is None or (
        aenderung.letztes.zeit is not None and spaeter.pgh_created_at <= aenderung.letztes.zeit
    ):
        return (
            f"{aenderung.titel} wurde nach diesem Vorgang erneut geändert ({namen}). "
            "Zuerst die spätere Änderung zurücknehmen."
        )
    wann = timezone.localtime(spaeter.pgh_created_at).strftime("%d.%m.%Y um %H:%M")
    zusatz = ""
    if spaeter.pgh_context_id is not None:
        zusatz = f" im Vorgang „{vorgaenge.titel_von(spaeter.pgh_context_id)}“"
    return (
        f"{aenderung.titel} wurde danach am {wann}{zusatz} erneut geändert ({namen}). "
        "Zuerst diesen späteren Vorgang zurücknehmen."
    )


def _abhaengiges(objekt: models.Model) -> models.Model | None:
    """Ein Objekt, das mit ``objekt`` gelöscht würde oder es schützt (sonst ``None``)."""
    sammler = Collector(using=objekt._state.db or "default")
    try:
        sammler.collect([objekt])
    except (ProtectedError, RestrictedError) as fehler:
        geschuetzt = getattr(fehler, "protected_objects", None) or getattr(
            fehler, "restricted_objects", None
        )
        erstes: models.Model = next(iter(geschuetzt or []), objekt)
        return erstes
    for modell, objekte in sammler.data.items():
        for anderes in objekte:
            if not (modell is type(objekt) and anderes.pk == objekt.pk):
                return anderes
    for abfrage in sammler.fast_deletes:
        zeile: models.Model | None = abfrage.first()
        if zeile is not None:
            return zeile
    return None


def _titel_von_objekt(objekt: models.Model) -> str:
    return vorgaenge.objekt_titel(type(objekt), _werte(objekt, type(objekt)._meta.concrete_fields))


def _angelegtes_zuruecknehmen(
    plan: Ruecknahmeplan, aenderung: Objektaenderung, weg: Ruecknahmeweg
) -> Schritt | None:
    objekt = _objekt_holen(aenderung)
    if objekt is None:
        return None  # schon entfernt (etwa mit einem anderen Objekt)
    if weg.anlage is not None:
        return weg.anlage(objekt)
    storno = stornoweg(aenderung.modell)
    if storno is not None:
        storno(aenderung.erstes)
        return Schritt(f"{aenderung.titel} storniert")
    nachher = aenderung.nachher or {}
    abweichend = [
        f
        for f in _rueckgaengig_felder(aenderung.modell)
        if getattr(objekt, f.attname) != nachher.get(f.attname)
    ]
    if abweichend:
        raise RuecknahmeNichtMoeglich(_spaeter_geaendert(plan, aenderung, abweichend))
    abhaengig = _abhaengiges(objekt)
    if abhaengig is not None:
        raise RuecknahmeNichtMoeglich(
            f"{aenderung.titel} wird noch verwendet ({_titel_von_objekt(abhaengig)}). "
            "Zuerst den Vorgang zurücknehmen, der das angelegt hat."
        )
    objekt.delete()
    return Schritt(f"{aenderung.titel} entfernt", entfernt=True)


def _geloeschtes_zuruecknehmen(aenderung: Objektaenderung) -> Schritt:
    storno = stornoweg(aenderung.modell)
    if storno is not None:
        storno(aenderung.letztes)
        return Schritt(f"{aenderung.titel} storniert")
    _loeschung_zuruecknehmen(aenderung.letztes, aenderung.vorher)
    return Schritt(f"{aenderung.titel} wiederhergestellt")


def _geaendertes_zuruecknehmen(
    plan: Ruecknahmeplan, aenderung: Objektaenderung, weg: Ruecknahmeweg
) -> Schritt | None:
    vorher, nachher = aenderung.vorher, aenderung.nachher
    if vorher is None or nachher is None:
        raise RuecknahmeNichtMoeglich(f"Zu {aenderung.titel} fehlt der Stand vor dem Vorgang.")
    objekt = _objekt_holen(aenderung)
    if objekt is None:
        raise RuecknahmeNichtMoeglich(f"{aenderung.titel} gibt es nicht mehr.")
    geaendert = [
        f for f in _rueckgaengig_felder(aenderung.modell) if vorher[f.attname] != nachher[f.attname]
    ]
    # Was schon wieder auf dem alten Stand ist (etwa durch einen Storno davor), bleibt.
    offen = [f for f in geaendert if getattr(objekt, f.attname) != vorher[f.attname]]
    if not offen:
        return None
    storno = stornoweg(aenderung.modell)
    if storno is not None:
        storno(aenderung.letztes)
        return Schritt(f"{aenderung.titel} storniert")
    spaeter = [f for f in offen if getattr(objekt, f.attname) != nachher[f.attname]]
    if spaeter:
        raise RuecknahmeNichtMoeglich(_spaeter_geaendert(plan, aenderung, spaeter))
    for f in offen:
        setattr(objekt, f.attname, vorher[f.attname])
    objekt.save(update_fields=[f.name for f in offen])
    if weg.danach is not None:
        weg.danach(objekt)
    teile = [
        f"{vorgaenge.feld_titel(aenderung.modell, f.attname)} "
        f"{vorgaenge.wert_text(aenderung.modell, f.attname, nachher[f.attname])} → "
        f"{vorgaenge.wert_text(aenderung.modell, f.attname, vorher[f.attname])}"
        for f in offen
        if not vorgaenge.ist_technisch(f.attname)
    ]
    return Schritt(f"{aenderung.titel}: {', '.join(teile)}" if teile else aenderung.titel)


# --- Rücknahme: Arbeitsposten --------------------------------------------------------


def _arbeitsposten_aus_vorgang(posten: Arbeitsposten) -> Schritt | None:
    """Ein Arbeitsposten, den der Vorgang angelegt hat, wird erledigt statt gelöscht: So
    schließt auch die Aufgabe im Hof-Backlog, und der Posten bleibt nachvollziehbar."""
    if posten.status == Arbeitsposten.Status.ERLEDIGT:
        return None
    arbeitsposten_erledigen(posten)
    return Schritt(f"Arbeitsposten #{posten.pk} ({posten.art}) erledigt")


def _arbeitsposten_zu_entferntem(modell: type[models.Model], objekt_id: Any) -> list[Schritt]:
    """Offene Arbeitsposten zu einem entfernten Objekt sind gegenstandslos: erledigen."""
    posten = Arbeitsposten.objects.filter(
        bezug_typ=ContentType.objects.get_for_model(modell),
        bezug_id=objekt_id,
        status__in=(Arbeitsposten.Status.OFFEN, Arbeitsposten.Status.ZURUECKGESTELLT),
    )
    schritte = []
    for p in posten:
        arbeitsposten_erledigen(p)
        schritte.append(Schritt(f"Arbeitsposten #{p.pk} ({p.art}) erledigt"))
    return schritte


ruecknahmeweg_registrieren(Arbeitsposten, anlage=_arbeitsposten_aus_vorgang, danach=aufgaben.melden)
ruecknahme_aufraeumen_registrieren(_arbeitsposten_zu_entferntem)
