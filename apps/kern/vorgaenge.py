"""Vorgänge: jede Nutzeraktion und jeder Lauf als eine Einheit im Protokoll (Review 2, A4).

Ein Vorgang ist ein Kontext von django-pghistory. Alle Ereignisse eines Vorgangs tragen
dieselbe ``pgh_context_id``; sie ist die Vorgangs-ID. So entstehen Vorgänge:

- **Anfrage:** ``pghistory.middleware.HistoryMiddleware`` öffnet je Anfrage einen Kontext
  (mit ``user`` und ``url``). Alles, was die Anfrage schreibt, ist ein Vorgang.
- **Lauf:** ``LaufBefehl`` öffnet je Lauf einen eigenen Vorgang (``neu=True``), auch wenn
  ein anderer Lauf ihn aufruft (Tageslauf) oder er aus einer Anfrage heraus startet.
- **Rücknahme:** ``kern.services.vorgang_zuruecknehmen`` ist ein eigener, neuer Vorgang.
- **Service ohne Anfrage** (Konsole, Tests): Ein Service mit ``@als_vorgang`` öffnet
  selbst einen Vorgang, wenn noch keiner offen ist.

Der Titel kommt aus dem Service: ``@als_vorgang("Kündigung {vertrag.nummer} erfasst")``
formatiert die Argumente des Aufrufs (und ``ergebnis``, den Rückgabewert). Im selben
Vorgang gewinnt der erste Titel; ein Service, den ein anderer aufruft, ändert ihn nicht.
Fehlt ein Titel, bildet das Protokoll einen aus der ersten Änderung.

Titel nennen nur Nummern und IDs (V-0002, NW-G-0001, #12), keine Namen: Sie stehen in der
Datenbank neben den Ereignissen und gehen in Logs.

Metadaten eines Vorgangs: ``titel``, ``art`` (siehe ``Art``), dazu ``user``/``url``
(Anfrage), ``lauf``/``lauf_name`` (Lauf), ``ruecknahme_von`` (ID des zurückgenommenen
Vorgangs), ``testhof_seed`` (Aufbau des Testhofs).
"""

from __future__ import annotations

import datetime
import functools
import inspect
import re
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from decimal import Decimal
from types import TracebackType
from typing import Any, ClassVar, ParamSpec, TypeVar

import pghistory
from django.apps import apps as django_apps
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.db.models import Count, Min
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime
from pghistory import runtime
from pghistory.models import Context, Events

from apps.kern.protokoll import ART_TITEL

P = ParamSpec("P")
R = TypeVar("R")

#: Metadaten, die ein neuer Vorgang vom umgebenden übernimmt (wer, von wo).
GEERBT = ("user", "url")


class Art:
    """Woher ein Vorgang kommt."""

    NUTZER = "nutzer"
    LAUF = "lauf"
    RUECKNAHME = "ruecknahme"
    SYSTEM = "system"

    TITEL: ClassVar[dict[str, str]] = {
        NUTZER: "Aktion",
        LAUF: "Lauf",
        RUECKNAHME: "Rücknahme",
        SYSTEM: "System",
    }


def _aktiv() -> Any:
    """Der offene Kontext von pghistory oder ``None``."""
    return getattr(runtime._tracker, "value", None)


def aktuelle_id() -> uuid.UUID | None:
    """ID des offenen Vorgangs (``None`` außerhalb eines Vorgangs)."""
    aktiv = _aktiv()
    return aktiv.id if aktiv is not None else None


def metadaten() -> dict[str, Any]:
    """Metadaten des offenen Vorgangs (leer außerhalb eines Vorgangs)."""
    aktiv = _aktiv()
    return dict(aktiv.metadata) if aktiv is not None else {}


def titel_setzen(titel: str) -> None:
    """Setzt den Titel des offenen Vorgangs, auch wenn schon geschrieben wurde.

    Die Metadaten eines Kontexts schreibt pghistory erst mit der nächsten Änderung in die
    Datenbank. Ein Titel, der erst nach der letzten Änderung feststeht (etwa mit der neuen
    Vertragsnummer), geht deshalb hier direkt in die Zeile des Kontexts.
    """
    aktiv = _aktiv()
    if aktiv is None:
        return
    aktiv.metadata["titel"] = titel
    Context.objects.filter(pk=aktiv.id).update(metadata=aktiv.metadata)


class vorgang:  # Kleinschreibung: liest sich wie pghistory.context
    """Kontextmanager für einen Vorgang.

    - Ist kein Vorgang offen, beginnt ein neuer.
    - Ist einer offen, gehört alles zu ihm; ein Titel gilt nur, wenn er noch keinen hat.
    - ``neu=True`` beginnt immer einen eigenen Vorgang (Lauf, Rücknahme). Benutzer und
      Adresse übernimmt er vom umgebenden; nach dem Block gilt wieder der umgebende.
    """

    def __init__(
        self,
        titel: str | None = None,
        *,
        art: str | None = None,
        neu: bool = False,
        **weitere: Any,
    ) -> None:
        self.titel = titel
        self.art = art
        self.neu = neu
        self.weitere = weitere
        self.id: uuid.UUID | None = None
        #: Dieser Block hat den Titel des Vorgangs gesetzt (und darf ihn noch ändern).
        self.besitzt_titel = False
        self._eigener: pghistory.context | None = None
        self._umgebend: Any = None

    def _eigene_metadaten(self) -> dict[str, Any]:
        daten = dict(self.weitere)
        if self.titel is not None:
            daten["titel"] = self.titel
        if self.art is not None:
            daten["art"] = self.art
        return daten

    def __enter__(self) -> vorgang:
        aktiv = _aktiv()
        if aktiv is None:
            self._eigener = pghistory.context(**self._eigene_metadaten())
            self.id = self._eigener.__enter__().id  # type: ignore[no-untyped-call]
            self.besitzt_titel = self.titel is not None
        elif self.neu:
            geerbt = {k: aktiv.metadata[k] for k in GEERBT if k in aktiv.metadata}
            self._umgebend = aktiv
            neu = runtime.Context(id=uuid.uuid4(), metadata={**geerbt, **self._eigene_metadaten()})
            runtime._tracker.value = neu
            self.id = neu.id
            self.besitzt_titel = self.titel is not None
        else:
            aktiv.metadata.update(self.weitere)
            if self.titel is not None and "titel" not in aktiv.metadata:
                aktiv.metadata["titel"] = self.titel
                self.besitzt_titel = True
            if self.art is not None and "art" not in aktiv.metadata:
                aktiv.metadata["art"] = self.art
            self.id = aktiv.id
        return self

    def __exit__(
        self,
        typ: type[BaseException] | None,
        fehler: BaseException | None,
        verlauf: TracebackType | None,
    ) -> None:
        if self._eigener is not None:
            self._eigener.__exit__(typ, fehler, verlauf)  # type: ignore[no-untyped-call]
        elif self._umgebend is not None:
            runtime._tracker.value = self._umgebend

    def titel_aendern(self, titel: str) -> None:
        """Ändert den Titel, wenn dieser Block ihn gesetzt hat."""
        if self.besitzt_titel:
            titel_setzen(titel)


def _formatieren(vorlage: str, werte: dict[str, Any]) -> str | None:
    try:
        return vorlage.format_map(werte)
    except (KeyError, AttributeError, IndexError, ValueError, TypeError):
        return None


def als_vorgang(
    vorlage: str, *, art: str | None = None
) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Decorator für Services: Der Aufruf ist ein Vorgang (oder Teil des offenen).

    ``vorlage`` wird mit den Argumenten formatiert, z. B. ``"Miete {vertrag.nummer} ab
    {gueltig_ab:%d.%m.%Y} geändert"``. ``{ergebnis…}`` nimmt den Rückgabewert, etwa
    ``"Vertrag {ergebnis.nummer} angelegt"``; dieser Titel steht nach dem Aufruf fest.
    """

    def dekorieren(funktion: Callable[P, R]) -> Callable[P, R]:
        signatur = inspect.signature(funktion)

        @functools.wraps(funktion)
        def huelle(*args: P.args, **kwargs: P.kwargs) -> R:
            gebunden = signatur.bind_partial(*args, **kwargs)
            gebunden.apply_defaults()
            werte = dict(gebunden.arguments)
            vorab = _formatieren(vorlage, werte)
            with vorgang(vorab if vorab is not None else "", art=art) as v:
                ergebnis = funktion(*args, **kwargs)
                if vorab is None and v.besitzt_titel:
                    nachher = _formatieren(vorlage, {**werte, "ergebnis": ergebnis})
                    if nachher:
                        v.titel_aendern(nachher)
            return ergebnis

        return huelle

    return dekorieren


# --- Lesen: Vorgänge für das Protokoll ----------------------------------------------

#: Felder, die nur in den aufgeklappten Einzelheiten stehen (Technik, kein Inhalt).
TECHNISCHE_FELDER = frozenset(
    {
        "inhalt_html",
        "pdf",
        "pdf_sha256",
        "dienst_auftrag",
        "inhalt_schluessel",
        "page_id",
        "vorgemerkt_am",
        "abgeglichen_am",
        "aktionen",
        "bezug_typ_id",
        "zustellnachweis",
        "vorlage_version",
        "einseitige_erklaerung",
    }
)
#: So viele Einzelheiten zeigt ein Vorgang in der Liste (der Testhof hat Tausende).
EINTRAEGE_JE_VORGANG = 60


def ist_technisch(attname: str) -> bool:
    return attname in TECHNISCHE_FELDER or attname.endswith(("_html", "_sha256"))


def _feld(modell: type[models.Model], schluessel: str) -> models.Field[Any, Any] | None:
    for f in modell._meta.concrete_fields:
        if schluessel in (f.attname, f.name):
            return f
    return None


def feld_titel(modell: type[models.Model], schluessel: str) -> str:
    """Deutscher Name eines Felds aus ``verbose_name``: „Status“, „Gekündigt von“."""
    f = _feld(modell, schluessel)
    if f is None:
        return schluessel
    name = str(f.verbose_name)
    ziel = _ziel(f)
    if ziel is not None and name == f.name.replace("_", " "):
        name = str(ziel._meta.verbose_name)
    return name[:1].upper() + name[1:]


def _ziel(f: models.Field[Any, Any]) -> type[models.Model] | None:
    """Das Modell, auf das ein Fremdschlüssel zeigt (sonst ``None``)."""
    ziel = f.related_model if f.is_relation else None
    return ziel if isinstance(ziel, type) else None


def _zahl(wert: Decimal) -> str:
    return f"{wert:,.2f}".replace(",", " ").replace(".", ",").replace(" ", ".")


def wert_text(modell: type[models.Model], schluessel: str, wert: Any) -> str:
    """Ein Wert lesbar: Auswahl als Anzeige („gekündigt“), Datum deutsch, Beträge mit Komma."""
    if wert is None or wert == "":
        return "–"
    f = _feld(modell, schluessel)
    if f is None:
        return str(wert)
    if f.choices:
        return str(dict(f.flatchoices).get(wert, wert))
    try:
        if isinstance(f, models.BooleanField):
            return "ja" if wert else "nein"
        if isinstance(f, models.DateTimeField):
            zeit = wert if isinstance(wert, datetime.datetime) else parse_datetime(str(wert))
            if zeit is not None:
                return timezone.localtime(zeit).strftime("%d.%m.%Y %H:%M")
        elif isinstance(f, models.DateField):
            tag = wert if isinstance(wert, datetime.date) else parse_date(str(wert))
            if tag is not None:
                return tag.strftime("%d.%m.%Y")
        if isinstance(f, models.DecimalField):
            return _zahl(Decimal(str(wert)))
    except (ValueError, ArithmeticError):
        return str(wert)
    ziel = _ziel(f)
    if ziel is not None:
        return f"{ziel._meta.verbose_name} #{wert}"
    text = str(wert)
    return text if len(text) <= 120 else text[:117] + "…"


def objekt_titel(modell: type[models.Model], daten: dict[str, Any]) -> str:
    """Lesbarer Name eines Objekts aus seinen Daten: „Vertrag V-0002“, „Kündigung #3“.

    Nutzt ``__str__`` des Modells; das nennt im Hof-System nur Nummern und IDs.
    """
    name = str(modell._meta.verbose_name)
    pk = daten.get(modell._meta.pk.attname)
    erlaubt = {f.attname for f in modell._meta.concrete_fields}
    try:
        text = str(modell(**{k: v for k, v in daten.items() if k in erlaubt}))
    except Exception:  # __str__ darf hier nie die Seite kippen
        text = ""
    if not text or "object (" in text:
        return f"{name} #{pk}"
    if text.lower().startswith(name.lower()):
        return text
    if "#" in text:
        return f"{name} #{pk}"
    return f"{name} {text}"


def art_von(meta: dict[str, Any]) -> str:
    if meta.get("art"):
        return str(meta["art"])
    if meta.get("ruecknahme_von") or meta.get("ruecknahme_ereignis"):
        return Art.RUECKNAHME
    if meta.get("lauf"):
        return Art.LAUF
    if meta.get("user"):
        return Art.NUTZER
    return Art.SYSTEM


def _modell(label: str) -> type[models.Model] | None:
    try:
        return django_apps.get_model(label)
    except (LookupError, ValueError):
        return None


def _titel(meta: dict[str, Any], erstes: Events | None) -> str:
    # Der Testhof zuerst: Seine Services setzen sonst ihren Titel für den ganzen Aufbau.
    if "testhof_seed" in meta:
        return f"Testhof aufgebaut (Seed {meta['testhof_seed']})"
    if meta.get("testhof_leeren"):
        return "Testhof geleert"
    if meta.get("titel"):
        return str(meta["titel"])
    if meta.get("lauf"):
        return f"Lauf {meta.get('lauf_name') or ''} #{meta['lauf']}".replace("  ", " ")
    if erstes is not None:
        modell = _modell(erstes.pgh_obj_model)
        if modell is not None:
            titel = objekt_titel(modell, erstes.pgh_data or {})
            return f"{titel} {ART_TITEL.get(erstes.pgh_label, erstes.pgh_label)}"
    return "Vorgang ohne Titel"


def titel_von(vorgang_id: uuid.UUID) -> str:
    """Der lesbare Titel eines Vorgangs (aus den Metadaten, sonst aus der ersten Änderung)."""
    kontext = Context.objects.filter(pk=vorgang_id).first()
    meta = (kontext.metadata or {}) if kontext is not None else {}
    erstes = (
        None
        if meta.get("titel")
        else Events.objects.filter(pgh_context_id=vorgang_id).order_by("pgh_created_at").first()
    )
    return _titel(meta, erstes)


@dataclass
class Feldzeile:
    feld: str
    alt: str
    neu: str
    technisch: bool


@dataclass
class Eintrag:
    """Eine Änderung in einem Vorgang, lesbar."""

    ereignis_id: str
    art: str
    art_titel: str
    objekt: str
    zeit: datetime.datetime
    felder: list[Feldzeile]

    @property
    def sichtbar(self) -> list[Feldzeile]:
        return [f for f in self.felder if not f.technisch]

    @property
    def technisch(self) -> list[Feldzeile]:
        return [f for f in self.felder if f.technisch]


@dataclass
class Vorgangszeile:
    """Ein Vorgang im Protokoll: Titel, Zeit, wer, Anzahl, Einzelheiten."""

    id: uuid.UUID
    titel: str
    zeit: datetime.datetime
    art: str
    wer: str
    anzahl: int
    eintraege: list[Eintrag] = field(default_factory=list)
    #: Die Rücknahme, die diesen Vorgang zurückgenommen hat.
    zurueckgenommen_durch: uuid.UUID | None = None
    #: Der Vorgang, den dieser zurücknimmt.
    ruecknahme_von: uuid.UUID | None = None
    testhof: bool = False

    @property
    def art_titel(self) -> str:
        return Art.TITEL.get(self.art, self.art)

    @property
    def weitere(self) -> int:
        return max(0, self.anzahl - len(self.eintraege))

    @property
    def anzahl_text(self) -> str:
        """„1 Änderung“, „2 Änderungen“ (Titel des Aufklappers im Protokoll)."""
        return f"{self.anzahl} Änderung" + ("" if self.anzahl == 1 else "en")

    @property
    def zuruecknehmbar(self) -> bool:
        """Ob die Oberfläche „Zurücknehmen“ anbietet (die genaue Prüfung macht der Service)."""
        return not (
            self.zurueckgenommen_durch
            or self.ruecknahme_von
            or self.testhof
            or self.art == Art.RUECKNAHME
        )


def _eintrag(e: Events) -> Eintrag:
    modell = _modell(e.pgh_obj_model)
    felder: list[Feldzeile] = []
    if modell is not None and e.pgh_label == "update":
        for schluessel, (alt, neu) in sorted((e.pgh_diff or {}).items()):
            felder.append(
                Feldzeile(
                    feld=feld_titel(modell, schluessel),
                    alt=wert_text(modell, schluessel, alt),
                    neu=wert_text(modell, schluessel, neu),
                    technisch=ist_technisch(schluessel),
                )
            )
    return Eintrag(
        ereignis_id=e.pgh_slug,
        art=e.pgh_label,
        art_titel=ART_TITEL.get(e.pgh_label, e.pgh_label),
        objekt=(
            objekt_titel(modell, e.pgh_data or {})
            if modell is not None
            else f"{e.pgh_obj_model} #{e.pgh_obj_id}"
        ),
        zeit=e.pgh_created_at,
        felder=felder,
    )


# Erweiterte Bezüge für den Filter: Eine Einheit zeigt auch die Vorgänge ihrer Verträge.
_BEZUG_ERWEITERN: dict[type[models.Model], Callable[[Any], Iterable[models.Model]]] = {}


def bezug_erweitern_registrieren(
    modell: type[models.Model], funktion: Callable[[Any], Iterable[models.Model]]
) -> None:
    """Meldet an, welche Objekte der Filter „nach Bezug“ zu einem Objekt mit einschließt."""
    _BEZUG_ERWEITERN[modell] = funktion


@dataclass(frozen=True)
class BezugSuche:
    """Suchfeld des Protokolls (R2-9): eine App findet ihre Objekte zu einem Text, schlägt
    Texte für die Auswahlliste vor und beschriftet ein Objekt so, dass die Suche es wiederfindet.

    Die Texte sind nur für die Oberfläche (Max' eigene Seiten), nie für Logs.
    """

    suchen: Callable[[str], list[models.Model]]
    vorschlaege: Callable[[], list[str]]
    beschriften: Callable[[models.Model], str | None]


_BEZUG_SUCHE: list[BezugSuche] = []


def bezug_suche_registrieren(suche: BezugSuche) -> None:
    _BEZUG_SUCHE.append(suche)


def bezug_suchen(text: str) -> list[models.Model]:
    """Alle Objekte, die zu ``text`` passen (V-Nummer, NW-ID, Name …), erste Treffer zuerst."""
    treffer: list[models.Model] = []
    for suche in _BEZUG_SUCHE:
        treffer.extend(suche.suchen(text))
    return treffer


def bezug_vorschlaege() -> list[str]:
    return [text for suche in _BEZUG_SUCHE for text in suche.vorschlaege()]


def bezug_beschriften(objekt: models.Model) -> str:
    """Der Suchtext zu einem Objekt (etwa „V-0005 · NW-G-0018 · Tom Bohnbach“)."""
    for suche in _BEZUG_SUCHE:
        text = suche.beschriften(objekt)
        if text:
            return text
    return objekt_titel(
        type(objekt),
        {f.attname: getattr(objekt, f.attname) for f in type(objekt)._meta.concrete_fields},
    )


def bezug_aus_text(text: str) -> models.Model | None:
    """``"stammdaten.vertrag:12"`` → das Objekt (oder ``None``)."""
    try:
        label, pk = text.rsplit(":", 1)
        modell = django_apps.get_model(label)
        objekt: models.Model | None = modell._default_manager.filter(pk=int(pk)).first()
    except (ValueError, LookupError):
        return None
    return objekt


def bezug_als_text(objekt: models.Model) -> str:
    return f"{objekt._meta.label_lower}:{objekt.pk}"


def _kontexte_zu(objekt: models.Model) -> set[uuid.UUID]:
    """Vorgänge, die ``objekt`` (und die erweiterten Bezüge) betreffen: Ereignisse des
    Objekts, Ereignisse mit Fremdschlüssel darauf und mit generischem Bezug darauf."""
    erweitern = _BEZUG_ERWEITERN.get(type(objekt))
    objekte = [objekt, *(erweitern(objekt) if erweitern is not None else [])]
    ids: set[uuid.UUID] = set()
    for o in objekte:
        typ = ContentType.objects.get_for_model(o)
        for abfrage in (
            Events.objects.references(o),
            Events.objects.filter(pgh_data__bezug_typ_id=typ.pk, pgh_data__bezug_id=o.pk),
        ):
            ids |= {i for i in abfrage.values_list("pgh_context_id", flat=True) if i is not None}
    return ids


#: Werte des Filters „wer“ (``vorgaenge_suchen``)
WER_LAUF = "lauf"
WER_BENUTZER = "benutzer:"


def vorgaenge_suchen(
    *,
    modell: str = "",
    bezug: models.Model | None = None,
    von: datetime.date | None = None,
    bis: datetime.date | None = None,
    wer: str = "",
    anzahl: int = 50,
    versatz: int = 0,
) -> list[Vorgangszeile]:
    """Die letzten Vorgänge, neueste zuerst, gefiltert nach Modell, Bezug und Zeitraum.

    Datenbasis der Filter „nach Vertrag/Einheit“ und „nach Person“ (``bezug``: Vertrag,
    Einheit oder Partei; die Stammdaten melden an, was dazugehört) und „Zeitraum“.
    ``wer``: ``"benutzer:<ID>"`` (Aktionen dieses Benutzers) oder ``"lauf"`` (nur Läufe).
    """
    ereignisse = Events.objects.filter(pgh_context_id__isnull=False)
    if wer == WER_LAUF:
        ereignisse = ereignisse.filter(
            pgh_context_id__in=Context.objects.filter(metadata__has_key="lauf").values("pk")
        )
    elif wer.startswith(WER_BENUTZER):
        try:
            benutzer_id = int(wer.removeprefix(WER_BENUTZER))
        except ValueError:
            benutzer_id = 0
        ereignisse = ereignisse.filter(
            pgh_context_id__in=Context.objects.filter(metadata__user=benutzer_id).values("pk")
        )
    if modell:
        ids = set(
            Events.objects.filter(pgh_obj_model=modell).values_list("pgh_context_id", flat=True)
        )
        ereignisse = ereignisse.filter(pgh_context_id__in=ids)
    if bezug is not None:
        ereignisse = ereignisse.filter(pgh_context_id__in=_kontexte_zu(bezug))
    gruppen = ereignisse.values("pgh_context_id").annotate(
        beginn=Min("pgh_created_at"), zahl=Count("pgh_slug")
    )
    if von is not None:
        gruppen = gruppen.filter(beginn__date__gte=von)
    if bis is not None:
        gruppen = gruppen.filter(beginn__date__lte=bis)
    seite = list(gruppen.order_by("-beginn")[versatz : versatz + anzahl])
    return _zeilen([(g["pgh_context_id"], g["beginn"], g["zahl"]) for g in seite])


def vorgang_lesen(vorgang_id: uuid.UUID) -> Vorgangszeile | None:
    """Ein Vorgang mit allen Einzelheiten (``None``, wenn es ihn nicht gibt)."""
    gruppe = (
        Events.objects.filter(pgh_context_id=vorgang_id)
        .values("pgh_context_id")
        .annotate(beginn=Min("pgh_created_at"), zahl=Count("pgh_slug"))
        .order_by("pgh_context_id")
        .first()
    )
    if gruppe is not None:
        return _zeilen([(vorgang_id, gruppe["beginn"], gruppe["zahl"])], alle=True)[0]
    kontext = Context.objects.filter(pk=vorgang_id).first()
    if kontext is None:
        return None
    return _zeilen([(vorgang_id, kontext.created_at, 0)], alle=True)[0]


def _wer(meta: dict[str, Any], benutzer: dict[int, str]) -> str:
    if meta.get("lauf"):
        return f"Lauf #{meta['lauf']}"
    if meta.get("user"):
        return benutzer.get(int(meta["user"]), f"Benutzer #{meta['user']}")
    if "testhof_seed" in meta or meta.get("testhof_leeren"):
        return "Testhof"
    return "System"


def _zeilen(
    gruppen: list[tuple[uuid.UUID, datetime.datetime, int]], *, alle: bool = False
) -> list[Vorgangszeile]:
    if not gruppen:
        return []
    ids = [g[0] for g in gruppen]
    kontexte = {k.pk: (k.metadata or {}) for k in Context.objects.filter(pk__in=ids)}
    klein = [vid for vid, _b, zahl in gruppen if alle or zahl <= EINTRAEGE_JE_VORGANG]
    je_vorgang: dict[uuid.UUID, list[Events]] = {vid: [] for vid in ids}
    for e in Events.objects.filter(pgh_context_id__in=klein).order_by("pgh_created_at", "pgh_id"):
        je_vorgang[e.pgh_context_id].append(e)
    for vid in ids:
        if vid not in klein:
            je_vorgang[vid] = list(
                Events.objects.filter(pgh_context_id=vid).order_by("pgh_created_at", "pgh_id")[
                    :EINTRAEGE_JE_VORGANG
                ]
            )
    rueck = {
        str(k.metadata.get("ruecknahme_von")): k.pk
        for k in Context.objects.filter(metadata__ruecknahme_von__in=[str(i) for i in ids])
    }
    benutzer_ids = {int(m["user"]) for m in kontexte.values() if m.get("user")}
    benutzer = {b.pk: b.email for b in get_user_model().objects.filter(pk__in=benutzer_ids)}
    zeilen = []
    for vid, beginn, zahl in gruppen:
        meta = kontexte.get(vid, {})
        liste = je_vorgang.get(vid, [])
        von = meta.get("ruecknahme_von")
        zeilen.append(
            Vorgangszeile(
                id=vid,
                titel=_titel(meta, liste[0] if liste else None),
                zeit=beginn,
                art=art_von(meta),
                wer=_wer(meta, benutzer),
                anzahl=zahl,
                eintraege=[_eintrag(e) for e in liste],
                zurueckgenommen_durch=rueck.get(str(vid)),
                ruecknahme_von=uuid.UUID(str(von)) if von else None,
                testhof="testhof_seed" in meta or bool(meta.get("testhof_leeren")),
            )
        )
    return zeilen


_NUMMERIERT = re.compile(r"^(?P<wort>\S+) #(?P<nr>\d+)(?P<rest>.*)$")


def zusammenfassen(texte: Iterable[str]) -> list[str]:
    """Gleichartige Schritte einer Rücknahme bündeln, Wichtiges zuerst.

    „Checkpunkt #43 entfernt“, „Checkpunkt #42 entfernt“ … wird „Checkpunkt ×5 entfernt“;
    Schritte, die es nur einmal gibt (Vertrag, Kündigung, Schreiben), stehen vorn.
    """
    liste = list(texte)
    gruppen: dict[tuple[str, str], int] = {}
    for text in liste:
        treffer = _NUMMERIERT.match(text)
        if treffer is not None:
            schluessel = (treffer["wort"], treffer["rest"])
            gruppen[schluessel] = gruppen.get(schluessel, 0) + 1
    einzeln = []
    for text in liste:
        treffer = _NUMMERIERT.match(text)
        if treffer is None or gruppen[(treffer["wort"], treffer["rest"])] == 1:
            einzeln.append(text)
    gebuendelt = [f"{wort} ×{zahl}{rest}" for (wort, rest), zahl in gruppen.items() if zahl > 1]
    return einzeln + gebuendelt
