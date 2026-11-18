"""Schreibweg des Status-MCP (Schritt 11): Kennzahlen ausrechnen und den Lese-Benutzer anlegen.

``kennzahlen_aktualisieren`` rechnet mit dem Python-Code der Fach-Apps (eine Wahrheit) und
schreibt das Ergebnis als Momentaufnahme in ``Kennzahl``. Die Sichten ``sm_*`` lesen daraus;
der Status-MCP liest nur die Sichten. Hier entstehen nur Zähler, Summen je Objekt, Tage,
Daten, Objekt-Kürzel und NW-IDs, nie Namen, Beträge je Vertrag oder Verwendungszwecke.

``lesebenutzer_anlegen`` legt den Login-Benutzer an, mit dem der Status-MCP sich verbindet.
Er bekommt nur die Rolle ``hof_statusmcp`` (Migration ``0003_rechte``), also nur
``SELECT`` auf die Sichten.
"""

from __future__ import annotations

import datetime
import re
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from decimal import Decimal

from django.db import connection, models, transaction
from django.utils import timezone
from psycopg import sql

from apps.abgleich import services as abgleich
from apps.cockpit import services as cockpit
from apps.fristen.models import Frist
from apps.stammdaten import datenstand, leerstand
from apps.stammdaten.models import Einheit, Kaution, Partei, Vertrag
from apps.statusmcp import rechte
from apps.statusmcp.models import Kennzahl
from apps.zaehler.models import Zaehler

NULL = Decimal("0.00")
#: Objekt-Kürzel für Verträge ohne Einheit (sollte es nicht geben, zählt aber mit).
OHNE_OBJEKT = "-"

#: Welche Modelle hinter den Bereichen des Datenstands stehen (``Luecke.objekt_id``).
DATENSTAND_MODELLE: dict[str, type[models.Model]] = {
    "einheit": Einheit,
    "schluessel": Einheit,
    "partei": Partei,
    "vertrag": Vertrag,
    "kaution": Kaution,
    "zaehler": Zaehler,
}


def altersklasse(tage: int) -> str:
    """Altersklasse eines Rückstands wie in ``abgleich.services.rueckstaende``."""
    if tage <= 30:
        return "0_30"
    if tage <= 60:
        return "31_60"
    return "ueber_60"


@dataclass
class _Objektsumme:
    vertraege: int = 0
    in_klaerung: int = 0
    summe: Decimal = NULL
    bis_30: Decimal = NULL
    bis_60: Decimal = NULL
    ueber_60: Decimal = NULL
    nicht_pruefbar: Decimal = NULL
    #: Verträge je Altersklasse (ein Vertrag zählt in jeder Klasse, in der er etwas offen hat)
    in_klasse: dict[str, int] = field(default_factory=dict)


def _rueckstaende(stichtag: datetime.date) -> list[Kennzahl]:
    stand = abgleich.rueckstaende(stichtag)
    objekt_von = dict(Einheit.objects.values_list("nw_id", "objekt__kuerzel"))
    je_objekt: dict[str, _Objektsumme] = defaultdict(_Objektsumme)
    gesamt = _Objektsumme()
    zeilen: list[Kennzahl] = []
    for eintrag in stand.vertraege:
        nw_ids = sorted(eintrag.nw_ids)
        objekt = objekt_von.get(nw_ids[0], OHNE_OBJEKT) if nw_ids else OHNE_OBJEKT
        for summe in (je_objekt[objekt], gesamt):
            summe.in_klaerung += int(eintrag.in_klaerung)
            summe.nicht_pruefbar += eintrag.nicht_pruefbar
            if eintrag.summe <= 0:
                continue
            summe.vertraege += 1
            summe.summe += eintrag.summe
            summe.bis_30 += eintrag.bis_30
            summe.bis_60 += eintrag.bis_60
            summe.ueber_60 += eintrag.ueber_60
            for klasse, betrag in (
                ("0_30", eintrag.bis_30),
                ("31_60", eintrag.bis_60),
                ("ueber_60", eintrag.ueber_60),
            ):
                if betrag > 0:
                    summe.in_klasse[klasse] = summe.in_klasse.get(klasse, 0) + 1
        if eintrag.summe <= 0:
            continue
        # Alter der ältesten offenen Fälligkeit, die als Rückstand zählt (nicht „nicht
        # prüfbar“). Kein Betrag je Vertrag.
        alter = max(
            (
                (stichtag - p.faellig_am).days
                for p in eintrag.posten
                if stand.datenstand is not None and p.faellig_am <= stand.datenstand and p.offen > 0
            ),
            default=0,
        )
        for nw_id in nw_ids:
            zeilen.append(
                Kennzahl(
                    bereich=Kennzahl.Bereich.RUECKSTAND_EINHEIT,
                    objekt=objekt_von.get(nw_id, OHNE_OBJEKT),
                    kennung=nw_id,
                    tage=alter,
                    schluessel=altersklasse(alter),
                )
            )

    def objektzeilen(objekt: str, summe: _Objektsumme) -> Iterable[Kennzahl]:
        werte = (
            ("summe", summe.vertraege, summe.summe),
            ("0_30", summe.in_klasse.get("0_30", 0), summe.bis_30),
            ("31_60", summe.in_klasse.get("31_60", 0), summe.bis_60),
            ("ueber_60", summe.in_klasse.get("ueber_60", 0), summe.ueber_60),
            ("nicht_pruefbar", None, summe.nicht_pruefbar),
            ("in_klaerung", summe.in_klaerung, None),
        )
        for schluessel, anzahl, betrag in werte:
            yield Kennzahl(
                bereich=Kennzahl.Bereich.RUECKSTAND,
                objekt=objekt,
                schluessel=schluessel,
                anzahl=anzahl,
                betrag=betrag,
                datum=stand.datenstand,
            )

    # Leeres Objekt: alle Objekte zusammen (auch ohne Rückstand, wegen des Datenstands).
    zeilen += objektzeilen("", gesamt)
    for objekt in sorted(je_objekt):
        zeilen += objektzeilen(objekt, je_objekt[objekt])
    return zeilen


def _datenstand(stichtag: datetime.date) -> list[Kennzahl]:
    zeilen: list[Kennzahl] = []
    for stand in datenstand.ermitteln(stichtag):
        zeilen.append(
            Kennzahl(
                bereich=Kennzahl.Bereich.DATENSTAND,
                schluessel=stand.schluessel,
                anzahl=stand.anzahl_luecken,
                gesamt=stand.geprueft,
            )
        )
        modell = DATENSTAND_MODELLE.get(stand.schluessel)
        if modell is None or not stand.luecken:
            continue
        objekte = modell._default_manager.in_bulk([luecke.objekt_id for luecke in stand.luecken])
        fehlend: dict[str, int] = defaultdict(int)
        for luecke in stand.luecken:
            for kennung in cockpit.kennungen_zu_bezug(objekte.get(luecke.objekt_id)):
                fehlend[kennung] += len(luecke.felder)
        zeilen += [
            Kennzahl(
                bereich=Kennzahl.Bereich.DATENSTAND_KENNUNG,
                schluessel=stand.schluessel,
                kennung=kennung,
                anzahl=anzahl,
            )
            for kennung, anzahl in sorted(fehlend.items())
        ]
    return zeilen


def _leerstand(stichtag: datetime.date) -> list[Kennzahl]:
    """Einheiten, die am Stichtag frei sind (Status „frei“, kein Vertrag deckt den Tag)."""
    zeilen = []
    for zeile in leerstand.berechnen(stichtag, stichtag):
        frei_seit = zeile.frei_seit or stichtag
        zeilen.append(
            Kennzahl(
                bereich=Kennzahl.Bereich.LEERSTAND,
                kennung=zeile.einheit.nw_id,
                objekt=zeile.einheit.objekt.kuerzel,
                schluessel=zeile.einheit.typ,
                datum=frei_seit,
                tage=(stichtag - frei_seit).days + 1,
            )
        )
    return zeilen


def _fristen() -> list[Kennzahl]:
    zeilen = []
    for frist in Frist.objects.filter(erledigt_am__isnull=True).select_related("bezug_typ"):
        for kennung in cockpit.kennungen_zu_bezug(frist.bezug):
            zeilen.append(
                Kennzahl(bereich=Kennzahl.Bereich.FRIST, frist_id=frist.pk, kennung=kennung)
            )
    return zeilen


@transaction.atomic
def kennzahlen_aktualisieren(stichtag: datetime.date | None = None) -> dict[str, int]:
    """Schreibt die Momentaufnahme neu (alles oder nichts). Rückgabe: Zeilen je Bereich."""
    stichtag = stichtag or timezone.localdate()
    jetzt = timezone.now()
    zeilen = _rueckstaende(stichtag) + _datenstand(stichtag) + _leerstand(stichtag) + _fristen()
    for zeile in zeilen:
        zeile.stichtag = stichtag
        zeile.erstellt_am = jetzt
    # Eine Momentaufnahme, kein gebuchter Bestand: Die alte wird ersetzt, nicht storniert.
    Kennzahl.objects.all().delete()
    Kennzahl.objects.bulk_create(zeilen)
    anzahl: dict[str, int] = defaultdict(int)
    for zeile in zeilen:
        anzahl[zeile.bereich] += 1
    return dict(anzahl)


# --- Lese-Benutzer -------------------------------------------------------------------

BENUTZERNAME = re.compile(r"[a-z_][a-z0-9_]{2,62}")
PASSWORT_MINDESTLAENGE = 24


class LesebenutzerFehler(ValueError):
    pass


def lesebenutzer_anlegen(name: str, passwort: str) -> bool:
    """Legt den Login-Benutzer des Status-MCP an oder setzt sein Passwort neu (idempotent).

    Der Benutzer bekommt nur die Rolle ``hof_statusmcp``; Transaktionen sind nur lesend,
    Anfragen dauern höchstens 10 Sekunden. Rückgabe: True, wenn er neu ist. Braucht einen
    Datenbank-Benutzer mit CREATEROLE (in Entwicklung und auf dem Server ist ``hof``
    Superuser). Das Passwort steht in keinem Log und keiner Ausgabe.
    """
    if not BENUTZERNAME.fullmatch(name) or name == rechte.ROLLE:
        raise LesebenutzerFehler(f"Ungültiger Benutzername „{name}“ (a–z, 0–9, _).")
    if len(passwort) < PASSWORT_MINDESTLAENGE or any(z.isspace() for z in passwort):
        raise LesebenutzerFehler(
            f"Das Passwort braucht mindestens {PASSWORT_MINDESTLAENGE} Zeichen ohne Leerzeichen."
        )
    benutzer = sql.Identifier(name)
    with transaction.atomic():
        # Am Cursor von Django vorbei: Mit DEBUG merkt sich Django jede Anweisung samt Text,
        # und das Passwort steht als Literal darin.
        connection.ensure_connection()
        roh = connection.connection
        if roh is None:
            raise RuntimeError("Keine Datenbankverbindung.")
        with roh.cursor() as cursor:
            cursor.execute("SELECT rolsuper FROM pg_roles WHERE rolname = %s", [name])
            vorhanden = cursor.fetchone()
            if vorhanden is not None and vorhanden[0]:
                raise LesebenutzerFehler(f"„{name}“ ist Superuser, kein Lese-Benutzer.")
            befehl = sql.SQL("ALTER" if vorhanden else "CREATE")
            cursor.execute(
                sql.SQL(
                    "{} ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION "
                    "NOBYPASSRLS INHERIT CONNECTION LIMIT 10 PASSWORD {}"
                ).format(befehl, benutzer, sql.Literal(passwort))
            )
            cursor.execute(sql.SQL("GRANT {} TO {}").format(sql.Identifier(rechte.ROLLE), benutzer))
            for einstellung in (
                "SET default_transaction_read_only = on",
                "SET statement_timeout = '10s'",
                "SET search_path = public",
            ):
                cursor.execute(sql.SQL("ALTER ROLE {} " + einstellung).format(benutzer))
    return vorhanden is None
