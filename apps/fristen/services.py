"""Schreibweg der Fristen (H9.1).

Andere Apps legen ihre Fristen nur über diese Funktionen an (CLAUDE.md, Regel 4.1).
Typische Anbindung::

    from apps.fristen import services as fristen
    from apps.fristen.models import Frist

    # Stammdaten, beim Speichern eines Vertrags mit Kündigungsfrist:
    fristen.frist_anlegen(
        Frist.Art.KUENDIGUNG,
        vertrag.kuendigung_spaetestens_am,
        Frist.Quelle.VERTRAG,
        vertrag,
        vorlauf_tage=30,
        datum_angleichen=True,  # ändert sich der Vertrag, wandert die Frist mit
    )

    # Bank, nach einer erneuerten Bankfreigabe:
    fristen.frist_erledigen(alte_frist)
    fristen.frist_anlegen(Frist.Art.BANKFREIGABE, neu_bis, Frist.Quelle.BANKFREIGABE, zugang)

    # Eingangsrechnungen, sobald bezahlt:
    for frist in fristen.fristen_zu(rechnung, nur_offen=True):
        fristen.frist_erledigen(frist)

Regeln:
- Eine Frist nennt immer ihre Quelle und einen Bezug (ein gespeichertes Modellobjekt).
- Je Quelle, Art und Bezug gibt es höchstens eine offene Frist. ``frist_anlegen`` ist
  idempotent: Ein zweiter Aufruf liefert die vorhandene Frist zurück.
- Eine Frist wird nicht gelöscht. Sie verschwindet aus den Listen erst mit einem
  Erledigt-Datum (``frist_erledigen``). Zurücknehmen geht über das Protokoll
  (``kern.services.vorgang_zuruecknehmen``; siehe ``apps.fristen.ruecknahme``).
- Ist eine Frist fällig (Datum minus Vorlauf erreicht), legt der Lauf ``fristen`` einen
  Arbeitsposten „Frist fällig“ an, höchstens einen je Frist. Mit dem Erledigen der
  Frist schließt er sich.
"""

from __future__ import annotations

import datetime
import logging
from collections.abc import Callable
from dataclasses import dataclass, field

from django.contrib.contenttypes.models import ContentType
from django.db import IntegrityError, models, transaction
from django.db.models import ExpressionWrapper, F, QuerySet

from apps.fristen.models import Frist, Fristverschiebung
from apps.kern import services as kern_services
from apps.kern import zeit
from apps.kern.models import Arbeitsposten, Lauf

logger = logging.getLogger(__name__)

#: Art des Arbeitspostens, den der Lauf ``fristen`` je fälliger Frist anlegt.
ART_FRIST_FAELLIG = "Frist fällig"
#: Aktionen am Posten: die Frist erledigen (schließt auch den Posten) oder zurückstellen.
AKTION_ERLEDIGEN = "frist_erledigen"
POSTEN_AKTIONEN = (AKTION_ERLEDIGEN, "zurueckstellen")
#: Ein Posten in diesem Status gilt als „offen“: Für die Frist entsteht kein zweiter.
_POSTEN_OFFEN = (Arbeitsposten.Status.OFFEN, Arbeitsposten.Status.ZURUECKGESTELLT)


class FristFehler(ValueError):
    """Die Frist lässt sich so nicht anlegen oder ändern. Die Meldung nennt nur IDs."""


# --- Lesen ---------------------------------------------------------------------------


def _bezug_schluessel(bezug: models.Model) -> tuple[ContentType, int]:
    if bezug is None or bezug.pk is None:
        raise FristFehler("Eine Frist braucht einen gespeicherten Bezug.")
    return ContentType.objects.get_for_model(bezug), int(bezug.pk)


def fristen_zu(bezug: models.Model, *, nur_offen: bool = False) -> QuerySet[Frist]:
    """Alle Fristen zu einem Objekt, nach Datum. ``nur_offen``: ohne Erledigt-Datum."""
    typ, pk = _bezug_schluessel(bezug)
    fristen = Frist.objects.filter(bezug_typ=typ, bezug_id=pk)
    if nur_offen:
        fristen = fristen.filter(erledigt_am__isnull=True)
    return fristen.order_by("datum", "pk")


def offene_fristen() -> QuerySet[Frist]:
    """Alle Fristen ohne Erledigt-Datum, auch überfällige."""
    return Frist.objects.filter(erledigt_am__isnull=True)


def faellige_fristen(stichtag: datetime.date) -> QuerySet[Frist]:
    """Offene Fristen, die am Stichtag fällig oder überfällig sind (Vorlauf eingerechnet).

    Datum minus Vorlauf rechnet die Datenbank (in Postgres: ``date - integer``).
    """
    return (
        offene_fristen()
        .annotate(
            faellig_ab_db=ExpressionWrapper(
                F("datum") - F("vorlauf_tage"), output_field=models.DateField()
            )
        )
        .filter(faellig_ab_db__lte=stichtag)
    )


def _offene_posten(frist: Frist) -> QuerySet[Arbeitsposten]:
    return Arbeitsposten.objects.filter(
        art=ART_FRIST_FAELLIG,
        bezug_typ=ContentType.objects.get_for_model(Frist),
        bezug_id=frist.pk,
        status__in=_POSTEN_OFFEN,
    )


def _posten_schliessen(frist: Frist) -> int:
    anzahl = 0
    for posten in _offene_posten(frist).select_for_update():
        kern_services.arbeitsposten_erledigen(posten)
        anzahl += 1
    return anzahl


# --- Schreiben -----------------------------------------------------------------------


def frist_anlegen(
    art: str,
    datum: datetime.date,
    quelle: str,
    bezug: models.Model,
    *,
    hinweis: str = "",
    vorlauf_tage: int = 0,
    datum_angleichen: bool = False,
) -> Frist:
    """Legt eine Frist an oder liefert die vorhandene (idempotent je Quelle, Art, Bezug).

    - ``art``: ein Wert aus ``Frist.Art`` (z. B. ``Frist.Art.KUENDIGUNG``).
    - ``quelle``: ein Wert aus ``Frist.Quelle``; die aufrufende App nennt sich selbst
      (Stammdaten: ``VERTRAG`` oder ``KAUTION``, Bank: ``BANKFREIGABE``, Dokumente:
      ``EINGANGSRECHNUNG``, Zähler: ``ZAEHLER``, Oberfläche: ``HAND``).
    - ``bezug``: das gespeicherte Objekt, um das es geht.
    - ``hinweis``: kurzer Text für die Fristenliste. Ist ein Personenfeld; er erscheint
      nie in Benachrichtigungen.
    - ``vorlauf_tage``: so viele Tage vor ``datum`` wird die Frist zum Arbeitsposten.

    Gibt es zu Quelle, Art und Bezug schon eine offene Frist, kommt sie unverändert
    zurück. Mit ``datum_angleichen=True`` wird sie auf das neue Datum verschoben
    (mit Eintrag in den Verschiebungen). Gibt es nur eine erledigte Frist mit genau
    diesem Datum, kommt die erledigte zurück: Ein erneuter Import legt dieselbe Frist
    nicht noch einmal an. Mit anderem Datum entsteht eine neue Frist (z. B. die nächste
    Bankfreigabe).
    """
    if art not in Frist.Art.values:
        raise FristFehler(f"Unbekannte Art der Frist: {art}")
    if quelle not in Frist.Quelle.values:
        raise FristFehler(f"Unbekannte Quelle der Frist: {quelle}")
    if vorlauf_tage < 0:
        raise FristFehler("Der Vorlauf kann nicht negativ sein.")
    typ, pk = _bezug_schluessel(bezug)
    schluessel = {"quelle": quelle, "art": art, "bezug_typ": typ, "bezug_id": pk}

    with transaction.atomic():
        vorhanden = (
            Frist.objects.select_for_update().filter(**schluessel, erledigt_am__isnull=True).first()
        )
        if vorhanden is not None:
            if datum_angleichen and vorhanden.datum != datum:
                return frist_verschieben(
                    vorhanden, datum, grund=f"Datum aus Quelle „{vorhanden.get_quelle_display()}“"
                )
            return vorhanden
        erledigt = (
            Frist.objects.filter(**schluessel, datum=datum, erledigt_am__isnull=False)
            .order_by("-erledigt_am", "-pk")
            .first()
        )
        if erledigt is not None:
            return erledigt

        frist = Frist(
            art=art,
            datum=datum,
            quelle=quelle,
            bezug_typ=typ,
            bezug_id=pk,
            vorlauf_tage=vorlauf_tage,
            hinweis=hinweis,
        )
        frist.full_clean(validate_constraints=False)
        try:
            with transaction.atomic():
                frist.save()
        except IntegrityError:
            # Gleichzeitig angelegt: Die andere Frist gilt.
            return Frist.objects.get(**schluessel, erledigt_am__isnull=True)
    logger.info("Frist #%s angelegt (%s, %s, Bezug %s #%s)", frist.pk, art, quelle, typ.model, pk)
    return frist


def frist_verschieben(
    frist: Frist,
    datum: datetime.date,
    *,
    grund: str = "",
    heute: datetime.date | None = None,
) -> Frist:
    """Verschiebt eine offene Frist auf ein neues Datum und hält das fest.

    Die Verschiebung steht als ``Fristverschiebung`` (von, nach, Grund) an der Frist und
    wie jede Änderung im Protokoll. Ist die Frist danach am Tag ``heute`` (Standard:
    heute) nicht mehr fällig, schließt sich ihr Arbeitsposten. Erledigte Fristen lassen
    sich nicht verschieben.
    """
    heute = heute or zeit.heute()
    with transaction.atomic():
        frist = Frist.objects.select_for_update().get(pk=frist.pk)
        if frist.ist_erledigt:
            raise FristFehler(f"Frist #{frist.pk} ist erledigt und lässt sich nicht verschieben.")
        if frist.datum == datum:
            return frist
        Fristverschiebung.objects.create(
            frist=frist, von_datum=frist.datum, nach_datum=datum, grund=grund
        )
        frist.datum = datum
        frist.save(update_fields=["datum"])
        if frist.faellig_ab > heute:
            _posten_schliessen(frist)
    logger.info("Frist #%s verschoben", frist.pk)
    return frist


def frist_erledigen(frist: Frist, am: datetime.date | None = None) -> Frist:
    """Setzt das Erledigt-Datum (Standard: heute) und schließt den Arbeitsposten.

    Ist die Frist schon erledigt, bleibt sie unverändert. Ein Datum in der Zukunft ist
    nicht erlaubt.
    """
    heute = zeit.heute()
    am = am or heute
    if am > heute:
        raise FristFehler(f"Frist #{frist.pk}: Das Erledigt-Datum liegt in der Zukunft.")
    with transaction.atomic():
        frist = Frist.objects.select_for_update().get(pk=frist.pk)
        if frist.ist_erledigt:
            return frist
        frist.erledigt_am = am
        frist.save(update_fields=["erledigt_am"])
        _posten_schliessen(frist)
    logger.info("Frist #%s erledigt", frist.pk)
    return frist


# --- Lauf „fristen“ ------------------------------------------------------------------


@dataclass(frozen=True)
class Fristenpruefung:
    """Ergebnis von ``fristen_pruefen``: nur Zähler."""

    faellig: int
    neue_posten: list[Arbeitsposten]
    geschlossen: int
    #: Zähler der angemeldeten weiteren Prüfungen, nach Name (siehe ``pruefung_anmelden``)
    weitere: dict[str, int] = field(default_factory=dict)


#: Erweiterungsstelle: Weitere tägliche Prüfungen, die zum Lauf ``fristen`` gehören, etwa
#: überfällige Eingangsrechnungen (Dokumente, Schritt 3b). Die Funktion bekommt Stichtag und
#: Lauf und liefert eine Anzahl (z. B. markierte Posten).
Pruefung = Callable[[datetime.date, Lauf | None], int]
_PRUEFUNGEN: dict[str, Pruefung] = {}


def pruefung_anmelden(name: str, funktion: Pruefung) -> None:
    """Meldet eine weitere Prüfung für den Lauf ``fristen`` an (beim Start der App)."""
    _PRUEFUNGEN[name] = funktion


def _posten_hinweis(frist: Frist) -> str:
    """Hinweis am Posten ohne Personendaten: nur ID, Art, Quelle und Datum."""
    return (
        f"Frist #{frist.pk}: {frist.get_art_display()} (Quelle: {frist.get_quelle_display()}), "
        f"Datum {frist.datum:%d.%m.%Y}."
    )


def fristen_pruefen(stichtag: datetime.date, *, lauf: Lauf | None = None) -> Fristenpruefung:
    """Macht fällige und überfällige Fristen zu Arbeitsposten „Frist fällig“.

    Idempotent: Je Frist gibt es höchstens einen offenen (oder zurückgestellten) Posten.
    Posten, deren Frist inzwischen erledigt oder nicht mehr fällig ist, schließen sich.
    """
    frist_typ = ContentType.objects.get_for_model(Frist)
    offene_posten = Arbeitsposten.objects.filter(
        art=ART_FRIST_FAELLIG, bezug_typ=frist_typ, status__in=_POSTEN_OFFEN
    )
    mit_posten = set(offene_posten.values_list("bezug_id", flat=True))

    faellig = list(faellige_fristen(stichtag).order_by("datum", "pk"))
    neue: list[Arbeitsposten] = []
    for frist in faellig:
        if frist.pk in mit_posten:
            continue
        neue.append(
            kern_services.arbeitsposten_anlegen(
                ART_FRIST_FAELLIG,
                bezug=frist,
                aktionen=POSTEN_AKTIONEN,
                faellig_am=frist.datum,
                hinweis=_posten_hinweis(frist),
                lauf=lauf,
            )
        )

    faellige_ids = {f.pk for f in faellig}
    geschlossen = 0
    for posten in offene_posten.exclude(bezug_id__in=faellige_ids).select_for_update():
        kern_services.arbeitsposten_erledigen(posten)
        geschlossen += 1
    weitere = {name: funktion(stichtag, lauf) for name, funktion in sorted(_PRUEFUNGEN.items())}
    return Fristenpruefung(
        faellig=len(faellig), neue_posten=neue, geschlossen=geschlossen, weitere=weitere
    )


def frist_zum_posten(posten: Arbeitsposten) -> Frist:
    """Die Frist hinter einem Arbeitsposten „Frist fällig“."""
    frist_typ = ContentType.objects.get_for_model(Frist)
    if (
        posten.art != ART_FRIST_FAELLIG
        or posten.bezug_typ_id != frist_typ.pk
        or posten.bezug_id is None
    ):
        raise FristFehler(f"Arbeitsposten #{posten.pk} gehört zu keiner Frist.")
    try:
        return Frist.objects.get(pk=posten.bezug_id)
    except Frist.DoesNotExist as fehler:
        raise FristFehler(f"Arbeitsposten #{posten.pk}: Die Frist fehlt.") from fehler


__all__ = [
    "ART_FRIST_FAELLIG",
    "FristFehler",
    "Fristenpruefung",
    "faellige_fristen",
    "frist_anlegen",
    "frist_erledigen",
    "frist_verschieben",
    "frist_zum_posten",
    "fristen_pruefen",
    "fristen_zu",
    "offene_fristen",
    "pruefung_anmelden",
]
