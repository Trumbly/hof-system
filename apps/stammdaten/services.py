"""Schreibweg der Stammdaten (CLAUDE.md, Regel 4.1).

Views, Importe und der Testhof ändern Einheiten, Parteien, Verträge und alles daran nur
über diese Funktionen. Fehlermeldungen nennen IDs (NW-ID, Vertragsnummer, Partei #id),
keine Namen.

Zeitabhängige Werte (Status, Zielmiete, Kondition) werden nie überschrieben: Eine Änderung
ist ein neuer Stand ab einem Datum; der vorige endet am Tag davor (``_zeitreihe_einfuegen``).
"""

from __future__ import annotations

import datetime
import logging
from collections.abc import Iterable, Mapping, Sequence
from decimal import Decimal
from typing import Any

from django.core.exceptions import ValidationError
from django.core.files import File
from django.core.validators import validate_email
from django.db import models, transaction
from django.db.models import Q
from django.utils import timezone

from apps.fristen import services as fristen
from apps.fristen.models import Frist
from apps.kern import services as kern
from apps.kern.models import Arbeitsposten, Mandant, Objekt
from apps.kern.vorgaenge import als_vorgang
from apps.stammdaten import fristrechnung, konfiguration, schreiben
from apps.stammdaten.models import (
    Checkliste,
    Checkpunkt,
    Einheit,
    EinheitStatus,
    Kaution,
    Kondition,
    Kontaktweg,
    Kuendigung,
    Partei,
    Schluessel,
    Uebergabe,
    UebergabeFoto,
    Vertrag,
    Vertragseinheit,
    Vertragspartei,
    Zielmiete,
)
from apps.zaehler import services as zaehler

logger = logging.getLogger(__name__)

EIN_TAG = datetime.timedelta(days=1)

#: Typ-Kürzel der NW-ID. Für vermietbare Typen gleich der Vertragsart.
TYP_KUERZEL: dict[str, str] = {
    Einheit.Typ.GARAGE: "G",
    Einheit.Typ.STELLPLATZ: "S",
    Einheit.Typ.LAGER: "L",
    Einheit.Typ.HALLE: "H",
    Einheit.Typ.GARTEN: "GA",
    Einheit.Typ.AUSSENFLAECHE: "A",
    Einheit.Typ.WOHNUNG: "W",
    Einheit.Typ.SONSTIGES: "X",
}

#: Vertragsart, die ein Vertrag über eine Einheit dieses Typs bekommt.
TYP_VERTRAGSART: dict[str, str] = {
    Einheit.Typ.GARAGE: Vertrag.Art.G,
    Einheit.Typ.STELLPLATZ: Vertrag.Art.S,
    Einheit.Typ.LAGER: Vertrag.Art.L,
    Einheit.Typ.HALLE: Vertrag.Art.H,
    Einheit.Typ.GARTEN: Vertrag.Art.GA,
    Einheit.Typ.AUSSENFLAECHE: Vertrag.Art.A,
    Einheit.Typ.SONSTIGES: Vertrag.Art.X,
}

#: Verträge in diesen Status binden ihre Einheiten.
BINDENDE_STATUS = (Vertrag.Status.ENTWURF, Vertrag.Status.AKTIV, Vertrag.Status.GEKUENDIGT)

ART_CHECKLISTE = "Checkliste offen"
#: Tage, die eine Frist vor ihrem Datum zum Arbeitsposten „Frist fällig“ wird.
VORLAUF_RUECKGABE = 7
VORLAUF_VERTRAGSENDE = 60
VORLAUF_KAUTION = 30
ART_KAUTION_ABRECHNEN = "Kaution abrechnen"
ART_SCHLUESSEL_FEHLT = "Schlüssel fehlt"


class StammdatenFehler(Exception):
    """Eine Änderung an den Stammdaten ist so nicht möglich. Die Meldung nennt nur IDs."""


class WohnraumNichtHier(StammdatenFehler):
    """Wohnraum führt Verwaltio: kein Vertrag, kein Status, keine Zielmiete im Hof-System."""


class UebergabeNichtMoeglich(StammdatenFehler):
    """Übergabe oder Rückgabe lässt sich (noch) nicht abschließen."""


def _speichern(objekt: models.Model) -> None:
    try:
        objekt.full_clean()
    except ValidationError as fehler:
        felder = ", ".join(sorted(fehler.message_dict)) if hasattr(fehler, "error_dict") else ""
        raise StammdatenFehler(
            f"{type(objekt)._meta.verbose_name} ungültig ({felder or fehler.messages[0]})"
        ) from fehler
    objekt.save()


def _setzen(objekt: models.Model, felder: dict[str, Any], erlaubt: Iterable[str]) -> None:
    unbekannt = set(felder) - set(erlaubt)
    if unbekannt:
        raise StammdatenFehler(f"Diese Felder lassen sich hier nicht ändern: {sorted(unbekannt)}")
    for name, wert in felder.items():
        setattr(objekt, name, wert)


def _arbeitsposten_erledigen(art: str, bezug: models.Model, hinweis_beginnt: str = "") -> int:
    """Erledigt offene Arbeitsposten einer Art an einem Bezug; Ergebnis: Anzahl."""
    from django.contrib.contenttypes.models import ContentType

    posten = Arbeitsposten.objects.filter(
        art=art,
        status=Arbeitsposten.Status.OFFEN,
        bezug_typ=ContentType.objects.get_for_model(bezug),
        bezug_id=bezug.pk,
    )
    if hinweis_beginnt:
        posten = posten.filter(hinweis__startswith=hinweis_beginnt)
    anzahl = 0
    for p in posten:
        kern.arbeitsposten_erledigen(p)
        anzahl += 1
    return anzahl


def _fristen_erledigen(bezug: models.Model, am: datetime.date) -> None:
    """Erledigt die offenen Fristen eines Bezugs (Erledigt-Datum nie in der Zukunft)."""
    heute = timezone.localdate()
    for frist in fristen.fristen_zu(bezug, nur_offen=True):
        fristen.frist_erledigen(frist, min(am, heute))


def _soll_aktualisieren(vertrag: Vertrag) -> None:
    """Meldet eine Änderung am Vertrag an die Sollstellung (Schritt 5).

    Der eine Haken dafür: Kondition, Laufzeit, Kündigung, Übergabe und Rückgabe rufen ihn
    auf. Rückwirkende Änderungen stornieren offene Posten und erzeugen neue.
    """
    from apps.soll import services as soll

    soll.vertrag_geaendert(vertrag)


# --- Zeitreihen ----------------------------------------------------------------------


def _zeitreihe_einfuegen[Z: (EinheitStatus, Zielmiete, Kondition)](
    reihe: models.QuerySet[Z], neu: Z, ab: datetime.date
) -> Z:
    """Fügt einen Stand ab ``ab`` in eine Zeitreihe ein, ohne alte Stände zu überschreiben.

    Der Stand davor endet am Vortag. Gibt es einen späteren Stand (z. B. eine Staffel),
    gilt der neue bis zum Tag vor diesem.
    """
    if reihe.filter(gueltig_ab=ab).exists():
        raise StammdatenFehler(f"Für den {ab:%d.%m.%Y} gibt es schon einen Stand.")
    vorher = reihe.filter(gueltig_ab__lt=ab).order_by("-gueltig_ab").first()
    nachher = reihe.filter(gueltig_ab__gt=ab).order_by("gueltig_ab").first()
    if vorher is not None and (vorher.gueltig_bis is None or vorher.gueltig_bis >= ab):
        vorher.gueltig_bis = ab - EIN_TAG
        vorher.save(update_fields=["gueltig_bis"])
    neu.gueltig_ab = ab
    neu.gueltig_bis = nachher.gueltig_ab - EIN_TAG if nachher is not None else None
    _speichern(neu)
    return neu


# --- Einheit -------------------------------------------------------------------------


def naechste_nw_id(typ: str) -> str:
    """Nächste freie NW-ID eines Typs: ``NW-<Kürzel>-<Nummer>``, z. B. ``NW-G-0007``.

    Gezählt wird je Typ ab der höchsten vergebenen Nummer; Lücken werden nicht neu
    vergeben, damit eine ID nie zwei Einheiten bezeichnet hat.
    """
    if typ not in TYP_KUERZEL:
        raise StammdatenFehler(f"Unbekannter Typ {typ}")
    praefix = f"NW-{TYP_KUERZEL[typ]}-"
    nummern = [
        int(rest)
        for nw_id in Einheit.objects.filter(nw_id__startswith=praefix).values_list(
            "nw_id", flat=True
        )
        if (rest := nw_id.removeprefix(praefix)).isdigit()
    ]
    return f"{praefix}{max(nummern, default=0) + 1:04d}"


def einheit_anlegen(
    objekt: Objekt,
    typ: str,
    *,
    bezeichnung: str = "",
    flaeche_qm: Decimal | None = None,
    vermarktung: str = Einheit.Vermarktung.KEINE,
    website_slug: str = "",
    notiz: str = "",
    nw_id: str | None = None,
    status: str | None = None,
    status_ab: datetime.date | None = None,
    zielmiete: Decimal | None = None,
    zielmiete_ab: datetime.date | None = None,
    zielmiete_begruendung: str = "",
) -> Einheit:
    """Legt eine Einheit an (H1.1). Ohne ``nw_id`` vergibt das System die nächste.

    Wohnraum steht nur mit NW-ID, Objekt und Bezeichnung hier (geführt in Verwaltio).
    """
    if typ not in Einheit.Typ.values:
        raise StammdatenFehler(f"Unbekannter Typ {typ}")
    if typ == Einheit.Typ.WOHNUNG and (
        status or zielmiete is not None or flaeche_qm is not None or vermarktung != "keine"
    ):
        raise WohnraumNichtHier("Wohnraum wird in Verwaltio geführt: nur NW-ID und Objekt.")
    if status and status_ab is None:
        raise StammdatenFehler("Ein Status braucht ein Datum, ab dem er gilt.")
    if zielmiete is not None and zielmiete_ab is None:
        raise StammdatenFehler("Eine Zielmiete braucht ein Datum, ab dem sie gilt.")
    with transaction.atomic():
        einheit = Einheit(
            nw_id=nw_id or naechste_nw_id(typ),
            typ=typ,
            objekt=objekt,
            bezeichnung=bezeichnung,
            flaeche_qm=flaeche_qm,
            vermarktung=vermarktung,
            website_slug=website_slug,
            notiz=notiz,
        )
        _speichern(einheit)
        if status and status_ab is not None:
            einheit_status_setzen(einheit, status, status_ab)
        if zielmiete is not None and zielmiete_ab is not None:
            zielmiete_setzen(einheit, zielmiete, zielmiete_ab, zielmiete_begruendung)
    logger.debug("Einheit %s angelegt", einheit.nw_id)
    return einheit


EINHEIT_FELDER = (
    "typ",
    "objekt",
    "bezeichnung",
    "flaeche_qm",
    "vermarktung",
    "website_slug",
    "notiz",
)


def einheit_aendern(einheit: Einheit, **felder: Any) -> Einheit:
    """Ändert Stammfelder einer Einheit. Die NW-ID bleibt immer gleich.

    Wohnraum behält nur Typ, Objekt, Bezeichnung und Notiz (geführt in Verwaltio).
    """
    if felder.get("typ", einheit.typ) == Einheit.Typ.WOHNUNG:
        if not einheit.ist_wohnraum and einheit.vertragseinheiten.exists():
            raise WohnraumNichtHier(
                f"Einheit {einheit.nw_id} hat Verträge; Wohnraum führt Verwaltio."
            )
        andere = set(felder) - {"typ", "objekt", "bezeichnung", "notiz"}
        if andere:
            raise WohnraumNichtHier(
                f"Einheit {einheit.nw_id} ist Wohnraum (geführt in Verwaltio): {sorted(andere)}"
            )
    _setzen(einheit, felder, EINHEIT_FELDER)
    _speichern(einheit)
    return einheit


def _kein_wohnraum(einheit: Einheit) -> None:
    if einheit.ist_wohnraum:
        raise WohnraumNichtHier(
            f"Einheit {einheit.nw_id} ist Wohnraum und wird in Verwaltio geführt."
        )


def einheit_status_setzen(
    einheit: Einheit, status: str, gueltig_ab: datetime.date, grund: str = ""
) -> EinheitStatus:
    """Neuer Status ab einem Datum; der alte bleibt mit Ende am Vortag stehen."""
    _kein_wohnraum(einheit)
    if status not in EinheitStatus.Status.values:
        raise StammdatenFehler(f"Unbekannter Status {status}")
    neu = EinheitStatus(einheit=einheit, status=status, grund=grund, gueltig_ab=gueltig_ab)
    return _zeitreihe_einfuegen(einheit.statusstaende.all(), neu, gueltig_ab)


def zielmiete_setzen(
    einheit: Einheit, betrag: Decimal, gueltig_ab: datetime.date, begruendung: str = ""
) -> Zielmiete:
    """Zielmiete mit Datum und Begründung (H2.1); die alte bleibt sichtbar."""
    _kein_wohnraum(einheit)
    neu = Zielmiete(einheit=einheit, betrag=betrag, begruendung=begruendung, gueltig_ab=gueltig_ab)
    return _zeitreihe_einfuegen(einheit.zielmieten.all(), neu, gueltig_ab)


def einheit_aufloesen(einheit: Einheit, datum: datetime.date, grund: str = "") -> Einheit:
    """Löst eine Einheit auf (H1.1), z. B. nach Umbau. Gebundene Verträge verhindern das."""
    gebunden = einheit.vertraege.filter(status__in=BINDENDE_STATUS).filter(
        Q(ende__isnull=True) | Q(ende__gte=datum)
    )
    if gebunden.exists():
        raise StammdatenFehler(
            f"Einheit {einheit.nw_id} ist noch an Vertrag {gebunden.first()} gebunden."
        )
    with transaction.atomic():
        if not einheit.ist_wohnraum:
            einheit_status_setzen(einheit, EinheitStatus.Status.AUFGELOEST, datum, grund)
        einheit.aufgeloest_am = datum
        _speichern(einheit)
    return einheit


# --- Partei --------------------------------------------------------------------------

PARTEI_FELDER = (
    "art",
    "anrede",
    "vorname",
    "nachname",
    "firma",
    "adresszusatz",
    "strasse",
    "plz",
    "ort",
    "notiz",
    "herkunft",
)


def _partei_pruefen(partei: Partei) -> None:
    if partei.art not in Partei.Art.values:
        raise StammdatenFehler(f"Unbekannte Art {partei.art}")
    if partei.art == Partei.Art.FIRMA and not partei.firma:
        raise StammdatenFehler("Eine Firma braucht einen Firmennamen.")
    if partei.art == Partei.Art.PERSON and not (partei.nachname or partei.vorname):
        raise StammdatenFehler("Eine Person braucht einen Namen.")


def partei_anlegen(art: str = Partei.Art.PERSON, **felder: Any) -> Partei:
    """Legt eine Partei an (H1.2): Mieter, Zahler oder Bürge außerhalb von Wohnraum."""
    partei = Partei(art=art)
    _setzen(partei, felder, PARTEI_FELDER)
    _partei_pruefen(partei)
    _speichern(partei)
    logger.debug("Partei #%s angelegt", partei.pk)
    return partei


def partei_aendern(partei: Partei, **felder: Any) -> Partei:
    _setzen(partei, felder, PARTEI_FELDER)
    _partei_pruefen(partei)
    _speichern(partei)
    return partei


def kontaktweg_hinzufuegen(
    partei: Partei, art: str, wert: str, *, bevorzugt: bool = False
) -> Kontaktweg:
    wert = wert.strip()
    if art not in Kontaktweg.Art.values:
        raise StammdatenFehler(f"Unbekannter Kontaktweg {art}")
    if not wert:
        raise StammdatenFehler("Der Kontaktweg ist leer.")
    if art == Kontaktweg.Art.EMAIL:
        try:
            validate_email(wert)
        except ValidationError as fehler:
            raise StammdatenFehler(
                f"Partei #{partei.pk}: keine gültige E-Mail-Adresse."
            ) from fehler
    with transaction.atomic():
        if bevorzugt:
            for anderer in partei.kontaktwege.filter(bevorzugt=True):
                anderer.bevorzugt = False
                anderer.save(update_fields=["bevorzugt"])
        kontaktweg = Kontaktweg(partei=partei, art=art, wert=wert, bevorzugt=bevorzugt)
        _speichern(kontaktweg)
    return kontaktweg


def kontaktweg_entfernen(kontaktweg: Kontaktweg) -> None:
    """Entfernt einen Kontaktweg (steht im Protokoll und lässt sich zurücknehmen)."""
    kontaktweg.delete()


# --- Vertrag -------------------------------------------------------------------------


def naechste_vertragsnummer() -> str:
    nummern = [
        int(rest)
        for nummer in Vertrag.objects.filter(nummer__startswith="V-").values_list(
            "nummer", flat=True
        )
        if (rest := nummer.removeprefix("V-")).isdigit()
    ]
    return f"V-{max(nummern, default=0) + 1:04d}"


def _regel_pruefen(schluessel: str) -> None:
    if schluessel:
        try:
            konfiguration.kuendigungsregel(schluessel)
        except konfiguration.KonfigurationFehler as fehler:
            raise StammdatenFehler(str(fehler)) from fehler


def _einheiten_frei(
    einheiten: Sequence[Einheit],
    beginn: datetime.date,
    ende: datetime.date | None,
    ausser: Vertrag | None = None,
) -> None:
    for einheit in einheiten:
        _kein_wohnraum(einheit)
        if einheit.aufgeloest_am is not None:
            raise StammdatenFehler(f"Einheit {einheit.nw_id} ist aufgelöst.")
        andere = einheit.vertraege.filter(status__in=BINDENDE_STATUS).filter(
            Q(ende__isnull=True) | Q(ende__gte=beginn)
        )
        if ende is not None:
            andere = andere.filter(beginn__lte=ende)
        if ausser is not None:
            andere = andere.exclude(pk=ausser.pk)
        belegt = andere.first()
        if belegt is not None:
            raise StammdatenFehler(
                f"Einheit {einheit.nw_id} ist in diesem Zeitraum an Vertrag {belegt} gebunden."
            )


@als_vorgang("Vertrag {ergebnis.nummer} angelegt")
def vertrag_anlegen(
    *,
    einheiten: Sequence[Einheit],
    mieter: Sequence[Partei],
    beginn: datetime.date,
    miete: Decimal | None,
    vertragsart: str | None = None,
    pauschale: Decimal = Decimal("0.00"),
    steuersatz: Decimal = Decimal("0.00"),
    zahlweise: str = Kondition.Zahlweise.MONATLICH,
    ende: datetime.date | None = None,
    kuendigungsregel: str | None = None,
    kaution: Decimal | None = None,
    mandant: Mandant | None = None,
    zahler: Sequence[Partei] = (),
    buergen: Sequence[Partei] = (),
    sonderabreden: str = "",
    herkunft: str = "",
    status: str = Vertrag.Status.ENTWURF,
    nummer: str | None = None,
    konditionsgrund: str = "",
) -> Vertrag:
    """Legt einen Vertrag an (H1.3), mit Einheiten, Parteien, erster Kondition und Kaution.

    - Wohnraum wird abgelehnt (``WohnraumNichtHier``); er gehört zu Verwaltio.
    - ``kuendigungsregel=None`` nimmt die Regel der Vertragsart aus ``konfiguration.toml``;
      ``""`` lässt sie offen (Import: aus dem PDF nachtragen, siehe Datenstand).
    - ``status="aktiv"`` nur für Bestandsverträge (Import, Testhof); neue Verträge beginnen
      als Entwurf und werden mit der Übergabe aktiv.
    """
    if not einheiten:
        raise StammdatenFehler("Ein Vertrag braucht mindestens eine Einheit.")
    if not mieter:
        raise StammdatenFehler("Ein Vertrag braucht mindestens einen Mieter.")
    if status not in (Vertrag.Status.ENTWURF, Vertrag.Status.AKTIV):
        raise StammdatenFehler("Ein neuer Vertrag ist ein Entwurf oder ein aktiver Bestand.")
    for einheit in einheiten:
        _kein_wohnraum(einheit)
    vertragsart = vertragsart or TYP_VERTRAGSART[einheiten[0].typ]
    if vertragsart not in Vertrag.Art.values:
        raise StammdatenFehler(f"Unbekannte Vertragsart {vertragsart}")
    if kuendigungsregel is None:
        kuendigungsregel = konfiguration.vertragsart(vertragsart).kuendigungsregel
    _regel_pruefen(kuendigungsregel)

    with transaction.atomic():
        _einheiten_frei(einheiten, beginn, ende)
        vertrag = Vertrag(
            nummer=nummer or naechste_vertragsnummer(),
            vertragsart=vertragsart,
            mandant=mandant or einheiten[0].objekt.mandant,
            beginn=beginn,
            ende=ende,
            kuendigungsregel=kuendigungsregel,
            status=status,
            sonderabreden=sonderabreden,
            herkunft=herkunft,
        )
        _speichern(vertrag)
        for einheit in einheiten:
            _speichern(Vertragseinheit(vertrag=vertrag, einheit=einheit))
        for rolle, parteien in (
            (Vertragspartei.Rolle.MIETER, mieter),
            (Vertragspartei.Rolle.ZAHLER, zahler),
            (Vertragspartei.Rolle.BUERGE, buergen),
        ):
            for partei in parteien:
                vertragspartei_hinzufuegen(vertrag, partei, rolle)
        if miete is not None:
            kondition_ab(
                vertrag,
                beginn,
                miete=miete,
                pauschale=pauschale,
                steuersatz=steuersatz,
                zahlweise=zahlweise,
                grund=konditionsgrund,
            )
        _speichern(Kaution(vertrag=vertrag, betrag=kaution))
        if status == Vertrag.Status.AKTIV:
            for einheit in einheiten:
                _vermietet_ab(einheit, beginn)
            _soll_aktualisieren(vertrag)
    logger.debug("Vertrag %s angelegt (%s)", vertrag.nummer, vertrag.status)
    return vertrag


def _vermietet_ab(einheit: Einheit, datum: datetime.date) -> None:
    jetzt = einheit.status_am(datum)
    if jetzt is not None and jetzt.status == EinheitStatus.Status.VERMIETET:
        return
    if jetzt is not None and jetzt.gueltig_ab == datum:
        # Zwei Stände am selben Tag gibt es nicht: Der Vertrag ist die spätere Tatsache und
        # berichtigt den Stand dieses Tages (die Berichtigung steht im Protokoll).
        if jetzt.status not in (EinheitStatus.Status.FREI, EinheitStatus.Status.RESERVIERT):
            raise StammdatenFehler(
                f"Einheit {einheit.nw_id} hat am {datum:%d.%m.%Y} den Status {jetzt.status}."
            )
        jetzt.status = EinheitStatus.Status.VERMIETET
        jetzt.grund = "Vertrag beginnt"
        _speichern(jetzt)
        return
    einheit_status_setzen(einheit, EinheitStatus.Status.VERMIETET, datum, "Vertrag beginnt")


VERTRAG_FELDER = (
    "vertragsart",
    "mandant",
    "beginn",
    "ende",
    "kuendigungsregel",
    "sonderabreden",
)


@als_vorgang("Vertrag {vertrag.nummer} geändert")
def vertrag_aendern(vertrag: Vertrag, **felder: Any) -> Vertrag:
    """Ändert Stammfelder eines Vertrags. Die Miete ändert ``kondition_ab``."""
    if "kuendigungsregel" in felder:
        _regel_pruefen(str(felder["kuendigungsregel"] or ""))
    _setzen(vertrag, felder, VERTRAG_FELDER)
    if {"beginn", "ende"} & set(felder) and vertrag.status in BINDENDE_STATUS:
        _einheiten_frei(list(vertrag.einheiten.all()), vertrag.beginn, vertrag.ende, ausser=vertrag)
    with transaction.atomic():
        _speichern(vertrag)
        if {"beginn", "ende"} & set(felder):
            _soll_aktualisieren(vertrag)
    return vertrag


def vertragspartei_hinzufuegen(vertrag: Vertrag, partei: Partei, rolle: str) -> Vertragspartei:
    if rolle not in Vertragspartei.Rolle.values:
        raise StammdatenFehler(f"Unbekannte Rolle {rolle}")
    vp = Vertragspartei(vertrag=vertrag, partei=partei, rolle=rolle)
    _speichern(vp)
    return vp


def vertragspartei_entfernen(vertragspartei: Vertragspartei) -> None:
    vertrag = vertragspartei.vertrag
    if (
        vertragspartei.rolle == Vertragspartei.Rolle.MIETER
        and vertrag.vertragsparteien.filter(rolle=Vertragspartei.Rolle.MIETER).count() <= 1
    ):
        raise StammdatenFehler(f"Vertrag {vertrag.nummer} braucht mindestens einen Mieter.")
    vertragspartei.delete()


@als_vorgang("Miete {vertrag.nummer} ab {gueltig_ab:%d.%m.%Y} geändert")
def kondition_ab(
    vertrag: Vertrag,
    gueltig_ab: datetime.date,
    *,
    miete: Decimal | None = None,
    pauschale: Decimal | None = None,
    steuersatz: Decimal | None = None,
    zahlweise: str | None = None,
    grund: str = "",
) -> Kondition:
    """Neue Kondition ab einem Datum: Mietänderung oder Stufe einer Staffel.

    Nicht genannte Werte übernimmt sie vom Stand davor. Der alte Stand bleibt mit Ende am
    Vortag sichtbar; eine spätere Stufe bleibt stehen.
    """
    vorher = vertrag.konditionen.filter(gueltig_ab__lt=gueltig_ab).order_by("-gueltig_ab").first()
    if vorher is None and miete is None:
        raise StammdatenFehler(f"Vertrag {vertrag.nummer}: Die erste Kondition braucht eine Miete.")
    if gueltig_ab < vertrag.beginn:
        raise StammdatenFehler(f"Vertrag {vertrag.nummer}: Kondition vor Vertragsbeginn.")
    if zahlweise is not None and zahlweise not in Kondition.Zahlweise.values:
        raise StammdatenFehler(f"Unbekannte Zahlweise {zahlweise}")

    def _wert(neu: Any, feld: str, standard: Any) -> Any:
        if neu is not None:
            return neu
        return getattr(vorher, feld) if vorher is not None else standard

    kondition = Kondition(
        vertrag=vertrag,
        miete=_wert(miete, "miete", None),
        pauschale=_wert(pauschale, "pauschale", Decimal("0.00")),
        steuersatz=_wert(steuersatz, "steuersatz", Decimal("0.00")),
        zahlweise=_wert(zahlweise, "zahlweise", ""),
        grund=grund,
        gueltig_ab=gueltig_ab,
    )
    with transaction.atomic():
        kondition = _zeitreihe_einfuegen(vertrag.konditionen.all(), kondition, gueltig_ab)
        _soll_aktualisieren(vertrag)
    return kondition


def vertrag_unterschrieben(
    vertrag: Vertrag, datum: datetime.date, scan: File[Any] | None = None
) -> Vertrag:
    """Hält die Unterschrift fest und hängt den unterschriebenen Scan an (H2.3)."""
    vertrag.unterschrieben_am = datum
    if scan is not None:
        vertrag.unterschriebener_scan.save(scan.name or "scan.pdf", scan, save=False)
    _speichern(vertrag)
    return vertrag


def vertragsdokument_erzeugen(vertrag: Vertrag) -> schreiben.Ergebnis:
    """Vertrag und Anlagen als PDF aus der Vorlage der Vertragsart (H2.3).

    Ergebnis ist ein Schreiben im Status „bereit“ oder, wenn keine freigegebene Vorlage
    da ist, ein Arbeitsposten „Schreiben erstellen“.
    """
    if vertrag.status != Vertrag.Status.ENTWURF:
        raise StammdatenFehler(f"Vertrag {vertrag.nummer} ist kein Entwurf mehr.")
    return schreiben.vertragsdokument_erzeugen(vertrag)


# --- Kündigung -----------------------------------------------------------------------


def kuendigungsregel_fuer(vertrag: Vertrag) -> konfiguration.Kuendigungsregel:
    """Die Regel des Vertrags, sonst die Standardregel seiner Vertragsart."""
    schluessel = (
        vertrag.kuendigungsregel or konfiguration.vertragsart(vertrag.vertragsart).kuendigungsregel
    )
    return konfiguration.kuendigungsregel(schluessel)


def kuendigung_fristende(vertrag: Vertrag, zugang_am: datetime.date) -> datetime.date:
    """Fristende einer ordentlichen Kündigung mit diesem Zugang (ohne Wunschtermin), höchstens
    das vereinbarte Vertragsende."""
    ende = fristrechnung.fristende(zugang_am, kuendigungsregel_fuer(vertrag))
    if vertrag.ende is not None and vertrag.ende < ende:
        return vertrag.ende
    return ende


def wunsch_vor_fristende(
    vertrag: Vertrag, zugang_am: datetime.date, zum_wunsch: datetime.date | None
) -> datetime.date | None:
    """Das Fristende, wenn der Wunschtermin davor liegt (Review 2, A11), sonst ``None``. Dann
    muss jemand wählen: Fristende oder Aufhebung zum Wunschtermin."""
    if zum_wunsch is None:
        return None
    ende = kuendigung_fristende(vertrag, zugang_am)
    return ende if zum_wunsch < ende else None


@als_vorgang("{ergebnis.vorgang_text} erfasst")
def kuendigung_erfassen(
    vertrag: Vertrag,
    zugang_am: datetime.date,
    *,
    von: str = Kuendigung.Von.MIETER,
    zum_wunsch: datetime.date | None = None,
    rueckgabetermin: datetime.date | None = None,
    zugangsweg: str = Kuendigung.Zugangsweg.UNBEKANNT,
    aufhebung: bool = False,
) -> Kuendigung:
    """Erfasst eine Kündigung (H2.8, Standardablauf 4).

    Das System rechnet das Fristende nach der Kündigungsfrist des Vertrags, setzt es als
    Vertragsende (damit endet das Soll), fordert die Bestätigung aus der Vorlage an und
    meldet den Rückgabetermin als Frist. Ein späteres Wunschende gilt; ein früheres nur mit
    ``aufhebung=True`` (Review 2, A11): Dann endet der Vertrag zum Wunschtermin, die
    Kündigung ist als Aufhebung gekennzeichnet, und statt der Bestätigung entsteht der
    Aufhebungsvertrag (Vorlagenart ``aufhebungsvertrag``). Ohne ``aufhebung`` gilt bei
    einem früheren Wunsch das Fristende; die Oberfläche lässt dazu wählen
    (``wunsch_vor_fristende``).

    ``zugangsweg`` hält fest, wie die Kündigung zuging (Website, Mail, Brief …). Bei Textform
    erkennt die Bestätigung die Form nicht ausdrücklich an (Platzhalter
    ``kuendigung.textform``).
    """
    if vertrag.status != Vertrag.Status.AKTIV:
        raise StammdatenFehler(f"Vertrag {vertrag.nummer} ist nicht aktiv ({vertrag.status}).")
    if von not in Kuendigung.Von.values:
        raise StammdatenFehler(f"Unbekannt, wer kündigt: {von}")
    if zugangsweg not in Kuendigung.Zugangsweg.values:
        raise StammdatenFehler(f"Unbekannter Zugangsweg: {zugangsweg}")
    regel = kuendigungsregel_fuer(vertrag)
    ende = kuendigung_fristende(vertrag, zugang_am)
    if aufhebung:
        if zum_wunsch is None or zum_wunsch >= ende:
            raise StammdatenFehler(
                "Eine Aufhebung zum Wunschtermin braucht einen Wunschtermin vor dem Fristende."
            )
        if zum_wunsch < zugang_am:
            raise StammdatenFehler("Der Wunschtermin liegt vor dem Zugang der Kündigung.")
        ende = zum_wunsch
    elif zum_wunsch is not None and zum_wunsch > ende:
        ende = zum_wunsch
        if vertrag.ende is not None and vertrag.ende < ende:
            ende = vertrag.ende
    rueckgabe = rueckgabetermin or ende
    if rueckgabe < zugang_am:
        raise StammdatenFehler("Der Rückgabetermin liegt vor dem Zugang der Kündigung.")

    with transaction.atomic():
        kuendigung = Kuendigung(
            vertrag=vertrag,
            von=von,
            zugang_am=zugang_am,
            kuendigungsregel=regel.schluessel,
            zum_wunsch=zum_wunsch,
            fristende=ende,
            rueckgabetermin=rueckgabe,
            zugangsweg=zugangsweg,
            aufhebung=aufhebung,
        )
        _speichern(kuendigung)
        vertrag.ende = ende
        vertrag.status = Vertrag.Status.GEKUENDIGT
        _speichern(vertrag)
        if aufhebung:
            schreiben.aufhebungsvertrag_erzeugen(kuendigung)
        else:
            schreiben.kuendigungsbestaetigung_erzeugen(kuendigung)
        fristen.frist_anlegen(
            Frist.Art.KUENDIGUNG,
            ende,
            Frist.Quelle.VERTRAG,
            kuendigung,
            hinweis=(
                f"Aufhebung zum Wunschtermin, Vertrag {vertrag.nummer}"
                if aufhebung
                else f"Fristende der Kündigung, Vertrag {vertrag.nummer}"
            ),
        )
        # Rückgabetermin: Eine Frist Vertragsende je Vertrag (bei Befristung wandert sie mit).
        fristen.frist_anlegen(
            Frist.Art.VERTRAGSENDE,
            rueckgabe,
            Frist.Quelle.VERTRAG,
            vertrag,
            hinweis=f"Rückgabetermin, Vertrag {vertrag.nummer}",
            vorlauf_tage=VORLAUF_RUECKGABE,
            datum_angleichen=True,
        )
        checkliste_anlegen(Checkliste.Phase.RUECKGABE, vertrag=vertrag)
        _soll_aktualisieren(vertrag)  # das Soll endet zum Fristende
    logger.debug("Kündigung #%s für Vertrag %s, Fristende %s", kuendigung.pk, vertrag.nummer, ende)
    return kuendigung


# --- Kaution -------------------------------------------------------------------------

KAUTION_FELDER = ("betrag", "eingang_am", "konto")


def kaution_aendern(kaution: Kaution, **felder: Any) -> Kaution:
    _setzen(kaution, felder, KAUTION_FELDER)
    _speichern(kaution)
    return kaution


def kaution_eingang_erfassen(kaution: Kaution, datum: datetime.date, konto: str) -> Kaution:
    """Eingang der Kaution. Ab Schritt 6 erkennt der Abgleich ihn aus dem Sammelkonto."""
    if not kaution.betrag:
        raise StammdatenFehler(f"Kaution von Vertrag {kaution.vertrag.nummer} hat keinen Betrag.")
    return kaution_aendern(kaution, eingang_am=datum, konto=konto)


def kaution_abrechnen(
    kaution: Kaution, datum: datetime.date, einbehalt: Decimal, notiz: str = ""
) -> Kaution:
    """Rechnet die Kaution ab (H2.10) und fordert das Schreiben aus der Vorlage an."""
    nummer = kaution.vertrag.nummer
    if kaution.abgerechnet_am is not None:
        raise StammdatenFehler(f"Kaution von Vertrag {nummer} ist schon abgerechnet.")
    if kaution.betrag is None or kaution.eingang_am is None:
        raise StammdatenFehler(f"Kaution von Vertrag {nummer}: Betrag oder Eingang fehlt.")
    if einbehalt < 0 or einbehalt > kaution.betrag:
        raise StammdatenFehler(f"Kaution von Vertrag {nummer}: Einbehalt außerhalb des Betrags.")
    with transaction.atomic():
        kaution.einbehalt = einbehalt
        kaution.ausgezahlt = kaution.betrag - einbehalt
        kaution.abgerechnet_am = datum
        kaution.abrechnung_notiz = notiz
        _speichern(kaution)
        schreiben.kautionsabrechnung_erzeugen(kaution)
        _arbeitsposten_erledigen(ART_KAUTION_ABRECHNEN, kaution)
        _fristen_erledigen(kaution, datum)
    return kaution


# --- Schlüssel -----------------------------------------------------------------------

SCHLUESSEL_FELDER = ("art", "bezeichnung", "anzahl", "inhaber", "partei", "inhaber_text")


def _schluessel_pruefen(schluessel: Schluessel) -> None:
    if schluessel.inhaber == Schluessel.Inhaber.PARTEI and schluessel.partei is None:
        raise StammdatenFehler("Bei „Partei“ muss die Partei genannt sein.")
    if schluessel.inhaber != Schluessel.Inhaber.PARTEI:
        schluessel.partei = None


def schluessel_anlegen(
    einheit: Einheit,
    *,
    art: str = Schluessel.Art.SCHLUESSEL,
    anzahl: int = 1,
    bezeichnung: str = "",
    inhaber: str = Schluessel.Inhaber.UNBEKANNT,
    partei: Partei | None = None,
    inhaber_text: str = "",
) -> Schluessel:
    """Bestand an Schlüsseln, Handsendern oder Zugängen einer Einheit (H1.5)."""
    _kein_wohnraum(einheit)
    schluessel = Schluessel(
        einheit=einheit,
        art=art,
        anzahl=anzahl,
        bezeichnung=bezeichnung,
        inhaber=inhaber,
        partei=partei,
        inhaber_text=inhaber_text,
    )
    _schluessel_pruefen(schluessel)
    _speichern(schluessel)
    return schluessel


def schluessel_aendern(schluessel: Schluessel, **felder: Any) -> Schluessel:
    _setzen(schluessel, felder, SCHLUESSEL_FELDER)
    _schluessel_pruefen(schluessel)
    _speichern(schluessel)
    return schluessel


def _schluessel_teilen(schluessel: Schluessel, anzahl: int) -> Schluessel:
    """Trennt ``anzahl`` Stück als eigene Zeile ab; Ergebnis: die abgetrennte Zeile."""
    if anzahl < 1 or anzahl > schluessel.anzahl:
        raise StammdatenFehler(f"Schlüssel #{schluessel.pk}: Anzahl außerhalb des Bestands.")
    if anzahl == schluessel.anzahl:
        return schluessel
    schluessel.anzahl -= anzahl
    _speichern(schluessel)
    teil = Schluessel(
        einheit=schluessel.einheit,
        art=schluessel.art,
        bezeichnung=schluessel.bezeichnung,
        anzahl=anzahl,
        inhaber=schluessel.inhaber,
        partei=schluessel.partei,
        inhaber_text=schluessel.inhaber_text,
    )
    _speichern(teil)
    return teil


def schluessel_uebergeben(schluessel: Schluessel, partei: Partei, anzahl: int) -> Schluessel:
    """Gibt ``anzahl`` Stück an eine Partei aus (Übergabe gegen Quittung)."""
    with transaction.atomic():
        teil = _schluessel_teilen(schluessel, anzahl)
        teil.inhaber = Schluessel.Inhaber.PARTEI
        teil.partei = partei
        teil.inhaber_text = ""
        _speichern(teil)
    return teil


def schluessel_zuruecknehmen(schluessel: Schluessel, anzahl: int) -> Schluessel:
    """Nimmt ``anzahl`` Stück zurück an den Hof; gleiche Schlüssel dort werden zusammengelegt."""
    with transaction.atomic():
        teil = _schluessel_teilen(schluessel, anzahl)
        beim_hof = (
            Schluessel.objects.filter(
                einheit=teil.einheit,
                art=teil.art,
                bezeichnung=teil.bezeichnung,
                inhaber=Schluessel.Inhaber.HOF,
            )
            .exclude(pk=teil.pk)
            .first()
        )
        if beim_hof is not None:
            beim_hof.anzahl += teil.anzahl
            _speichern(beim_hof)
            teil.delete()
            return beim_hof
        teil.inhaber = Schluessel.Inhaber.HOF
        teil.partei = None
        teil.inhaber_text = ""
        _speichern(teil)
    return teil


# --- Checklisten ---------------------------------------------------------------------


def checkliste_anlegen(
    phase: str, *, einheit: Einheit | None = None, vertrag: Vertrag | None = None
) -> Checkliste:
    """Checkliste einer Phase der SOP aus ``konfiguration.toml``, dazu ein Arbeitsposten."""
    if einheit is None and vertrag is None:
        raise StammdatenFehler("Eine Checkliste gehört zu einer Einheit oder einem Vertrag.")
    vorlage = konfiguration.checkliste(phase)
    with transaction.atomic():
        liste = Checkliste(phase=phase, titel=vorlage.titel, einheit=einheit, vertrag=vertrag)
        _speichern(liste)
        for nummer, text in enumerate(vorlage.punkte, start=1):
            _speichern(Checkpunkt(checkliste=liste, reihenfolge=nummer, text=text))
        bezug_text = f"Einheit {einheit.nw_id}" if einheit else f"Vertrag {vertrag}"
        kern.arbeitsposten_anlegen(
            ART_CHECKLISTE, bezug=liste, hinweis=f"{vorlage.titel} · {bezug_text}"
        )
    return liste


def checkpunkt_erledigen(punkt: Checkpunkt, datum: datetime.date, notiz: str = "") -> Checkpunkt:
    """Hakt einen Punkt ab. Sind alle erledigt, ist die Checkliste abgeschlossen."""
    with transaction.atomic():
        punkt.erledigt_am = datum
        if notiz:
            punkt.notiz = notiz
        _speichern(punkt)
        liste = punkt.checkliste
        if not liste.punkte.filter(erledigt_am__isnull=True).exists():
            liste.abgeschlossen_am = datum
            _speichern(liste)
            _arbeitsposten_erledigen(ART_CHECKLISTE, liste)
    return punkt


def checkpunkt_oeffnen(punkt: Checkpunkt) -> Checkpunkt:
    with transaction.atomic():
        punkt.erledigt_am = None
        _speichern(punkt)
        liste = punkt.checkliste
        if liste.abgeschlossen_am is not None:
            liste.abgeschlossen_am = None
            _speichern(liste)
    return punkt


# --- Übergabe und Rückgabe (H2.4, H2.9, O1) ------------------------------------------


def uebergabe_beginnen(vertrag: Vertrag, art: str, datum: datetime.date) -> Uebergabe:
    """Beginnt eine Übergabe (Vertrag im Entwurf) oder Rückgabe (Vertrag aktiv/gekündigt).

    Gibt es schon eine begonnene der gleichen Art, geht es mit ihr weiter.
    """
    if art == Uebergabe.Art.UEBERGABE:
        if vertrag.status != Vertrag.Status.ENTWURF:
            raise UebergabeNichtMoeglich(f"Vertrag {vertrag.nummer} ist kein Entwurf.")
    elif art == Uebergabe.Art.RUECKGABE:
        if vertrag.status not in (Vertrag.Status.AKTIV, Vertrag.Status.GEKUENDIGT):
            raise UebergabeNichtMoeglich(
                f"Vertrag {vertrag.nummer} ist weder aktiv noch gekündigt."
            )
    else:
        raise StammdatenFehler(f"Unbekannte Art {art}")
    offen = vertrag.uebergaben.filter(art=art, status=Uebergabe.Status.BEGONNEN).first()
    if offen is not None:
        return offen
    uebergabe = Uebergabe(vertrag=vertrag, art=art, datum=datum)
    _speichern(uebergabe)
    return uebergabe


def _offen(uebergabe: Uebergabe) -> None:
    if uebergabe.status != Uebergabe.Status.BEGONNEN:
        raise UebergabeNichtMoeglich(f"{uebergabe} ist schon abgeschlossen.")


def uebergabe_speichern(
    uebergabe: Uebergabe,
    *,
    datum: datetime.date | None = None,
    zaehlerstaende: Iterable[Mapping[str, Any]] | None = None,
    schluessel: Iterable[dict[str, int]] | None = None,
    zustand: str | None = None,
    zurueckgelassen: str | None = None,
    unterschrift: File[Any] | None = None,
) -> Uebergabe:
    """Speichert einen Schritt der Erfassung vor Ort.

    ``zaehlerstaende`` erfasst über ``zaehler.services.uebergabe_staende_erfassen``: je
    Eintrag ``{"zaehler": <Zähler oder ID>, "stand": "123,4", "foto": <Datei>}`` (statt
    der ID auch ``"nummer"``); nur Zähler der Einheiten des Vertrags.
    """
    _offen(uebergabe)
    datum_neu = datum is not None and datum != uebergabe.datum
    if datum is not None:
        uebergabe.datum = datum
    if schluessel is not None:
        erlaubt = set(_schluessel_der_uebergabe(uebergabe).values_list("pk", flat=True))
        eintraege = []
        for eintrag in schluessel:
            pk, anzahl = int(eintrag["schluessel"]), int(eintrag["anzahl"])
            if pk not in erlaubt:
                raise StammdatenFehler(f"Schlüssel #{pk} gehört nicht zu diesem Vorgang.")
            if anzahl > 0:
                eintraege.append({"schluessel": pk, "anzahl": anzahl})
        uebergabe.schluessel = eintraege
    if zustand is not None:
        uebergabe.zustand = zustand
    if zurueckgelassen is not None:
        uebergabe.zurueckgelassen = zurueckgelassen
    if unterschrift is not None:
        uebergabe.unterschrift.save(
            unterschrift.name or "unterschrift.jpg", unterschrift, save=False
        )
    try:
        with transaction.atomic():
            _speichern(uebergabe)
            if datum_neu:
                zaehler.uebergabe_datum_angleichen(uebergabe)
            if zaehlerstaende is not None:
                zaehler.uebergabe_staende_erfassen(uebergabe, zaehlerstaende)
    except zaehler.ZaehlerFehler as fehler:
        raise StammdatenFehler(str(fehler)) from fehler
    return uebergabe


def uebergabe_foto_hinzufuegen(
    uebergabe: Uebergabe, datei: File[Any], beschreibung: str = ""
) -> UebergabeFoto:
    _offen(uebergabe)
    foto = UebergabeFoto(uebergabe=uebergabe, beschreibung=beschreibung)
    foto.bild.save(datei.name or "foto.jpg", datei, save=False)
    _speichern(foto)
    return foto


def _schluessel_der_uebergabe(uebergabe: Uebergabe) -> models.QuerySet[Schluessel]:
    """Übergabe: Schlüssel der Einheiten, die nicht bei einer Partei sind.
    Rückgabe: Schlüssel der Einheiten bei den Parteien des Vertrags."""
    vertrag = uebergabe.vertrag
    alle = Schluessel.objects.filter(einheit__in=vertrag.einheiten.all())
    if uebergabe.art == Uebergabe.Art.UEBERGABE:
        return alle.exclude(inhaber=Schluessel.Inhaber.PARTEI)
    parteien = vertrag.vertragsparteien.values_list("partei", flat=True)
    return alle.filter(inhaber=Schluessel.Inhaber.PARTEI, partei__in=parteien)


def schluessel_der_uebergabe(uebergabe: Uebergabe) -> list[Schluessel]:
    """Die Schlüssel, die bei dieser Übergabe oder Rückgabe in Frage kommen."""
    return list(_schluessel_der_uebergabe(uebergabe).select_related("einheit"))


def uebergabe_hindernisse(uebergabe: Uebergabe) -> list[str]:
    """Was dem Abschluss noch fehlt.

    Übergabe (SOP, Grundregel 5): kein Schlüssel vor Unterschrift und Kaution; der Mieter
    quittiert mit Unterschrift. Die Rückgabe geht auch ohne den Mieter (Schlüssel
    eingeworfen); dann fehlt nur das Foto der Unterschrift, ohne den Abschluss zu sperren.
    """
    vertrag = uebergabe.vertrag
    fehlt = []
    if uebergabe.art == Uebergabe.Art.UEBERGABE:
        if vertrag.unterschrieben_am is None:
            fehlt.append("Der Vertrag ist noch nicht unterschrieben.")
        kaution: Kaution | None = getattr(vertrag, "kaution", None)
        if kaution is None or kaution.betrag is None:
            fehlt.append("Die Kaution ist nicht erfasst.")
        elif kaution.betrag > 0 and kaution.eingang_am is None:
            fehlt.append("Die Kaution ist noch nicht eingegangen.")
        if not uebergabe.unterschrift:
            fehlt.append("Das Foto der Unterschrift (Quittung) fehlt.")
    return fehlt


def uebergabe_abschliessen(uebergabe: Uebergabe) -> Uebergabe:
    """Schließt Übergabe oder Rückgabe ab.

    - Übergabe (Standardablauf 1, Schritt 8): Schlüssel an den Mieter, Vertrag aktiv,
      Einheiten vermietet ab dem Datum; damit beginnt die Sollstellung (apps.soll).
    - Rückgabe (Standardablauf 4, Schritt 4): Schlüssel zurück an den Hof (Fehlendes wird
      ein Arbeitsposten), Einheiten frei ab dem Datum, Vertrag beendet, Kautionsabrechnung
      als Arbeitsposten mit Frist, Checkliste für die Neuvermietung.

    In beiden Fällen wird das Protokoll aus der Vorlage angefordert. Zählerstände (über
    ``zaehler.services``, auf das Datum der Übergabe) und Sollstellung (``soll``) laufen in
    derselben Transaktion: Scheitert eines, bleibt die Übergabe begonnen.
    """
    _offen(uebergabe)
    hindernisse = uebergabe_hindernisse(uebergabe)
    if hindernisse:
        raise UebergabeNichtMoeglich(" ".join(hindernisse))
    with transaction.atomic():
        try:
            zaehler.uebergabe_datum_angleichen(uebergabe)
        except zaehler.ZaehlerFehler as fehler:
            raise UebergabeNichtMoeglich(str(fehler)) from fehler
        if uebergabe.art == Uebergabe.Art.UEBERGABE:
            _uebergabe_abschliessen(uebergabe)
        else:
            _rueckgabe_abschliessen(uebergabe)
        uebergabe.status = Uebergabe.Status.ABGESCHLOSSEN
        _speichern(uebergabe)
        schreiben.protokoll_erzeugen(uebergabe)
    logger.debug("%s für Vertrag %s abgeschlossen", uebergabe, uebergabe.vertrag.nummer)
    return uebergabe


def _schluessel_protokoll(schluessel: Schluessel, anzahl: int, fehlend: int = 0) -> dict[str, Any]:
    """Eintrag fürs Protokoll: beschreibend statt mit IDs, die sich beim Teilen ändern."""
    eintrag: dict[str, Any] = {
        "einheit": schluessel.einheit.nw_id,
        "art": schluessel.art,
        "bezeichnung": schluessel.bezeichnung,
        "anzahl": anzahl,
    }
    if fehlend:
        eintrag["fehlend"] = fehlend
    return eintrag


def _uebergabe_abschliessen(uebergabe: Uebergabe) -> None:
    vertrag = uebergabe.vertrag
    mieter = vertrag.parteien_mit_rolle(Vertragspartei.Rolle.MIETER)
    if not mieter:
        raise UebergabeNichtMoeglich(f"Vertrag {vertrag.nummer} hat keinen Mieter.")
    ausgegeben = []
    for eintrag in uebergabe.schluessel:
        schluessel = Schluessel.objects.get(pk=eintrag["schluessel"])
        teil = schluessel_uebergeben(schluessel, mieter[0], int(eintrag["anzahl"]))
        ausgegeben.append(_schluessel_protokoll(teil, teil.anzahl))
    uebergabe.schluessel = ausgegeben
    if uebergabe.datum < vertrag.beginn:
        raise UebergabeNichtMoeglich(f"Vertrag {vertrag.nummer} beginnt erst später.")
    vertrag.status = Vertrag.Status.AKTIV
    _speichern(vertrag)
    for einheit in vertrag.einheiten.all():
        _vermietet_ab(einheit, uebergabe.datum)
    _soll_aktualisieren(vertrag)  # die Sollstellung beginnt
    if vertrag.ende is not None:
        fristen.frist_anlegen(
            Frist.Art.VERTRAGSENDE,
            vertrag.ende,
            Frist.Quelle.VERTRAG,
            vertrag,
            hinweis=f"Vertragsende (befristet), Vertrag {vertrag.nummer}",
            vorlauf_tage=VORLAUF_VERTRAGSENDE,
            datum_angleichen=True,
        )


def _rueckgabe_abschliessen(uebergabe: Uebergabe) -> None:
    vertrag = uebergabe.vertrag
    datum = uebergabe.datum
    zurueck = {int(e["schluessel"]): int(e["anzahl"]) for e in uebergabe.schluessel}
    protokoll = []
    for schluessel in schluessel_der_uebergabe(uebergabe):
        anzahl = min(zurueck.get(schluessel.pk, 0), schluessel.anzahl)
        fehlend = schluessel.anzahl - anzahl
        protokoll.append(_schluessel_protokoll(schluessel, anzahl, fehlend))
        if anzahl:
            schluessel_zuruecknehmen(schluessel, anzahl)
        if fehlend:
            kern.arbeitsposten_anlegen(
                ART_SCHLUESSEL_FEHLT,
                bezug=vertrag,
                hinweis=(
                    f"Einheit {schluessel.einheit.nw_id}: "
                    f"{fehlend} × {schluessel.get_art_display()} fehlen"
                ),
            )
    uebergabe.schluessel = protokoll
    for einheit in vertrag.einheiten.all():
        jetzt = einheit.status_am(datum)
        if jetzt is None or jetzt.status != EinheitStatus.Status.FREI:
            if jetzt is not None and jetzt.gueltig_ab == datum:
                raise UebergabeNichtMoeglich(
                    f"Einheit {einheit.nw_id} hat am {datum:%d.%m.%Y} schon einen neuen Status."
                )
            einheit_status_setzen(einheit, EinheitStatus.Status.FREI, datum, "Rückgabe")
        checkliste_anlegen(Checkliste.Phase.VORBEREITUNG, einheit=einheit)
    if vertrag.ende is None:
        vertrag.ende = datum
    vertrag.status = Vertrag.Status.BEENDET
    _speichern(vertrag)
    _soll_aktualisieren(vertrag)
    _fristen_erledigen(vertrag, datum)
    for kuendigung in vertrag.kuendigungen.all():
        _fristen_erledigen(kuendigung, datum)

    kaution: Kaution | None = getattr(vertrag, "kaution", None)
    if kaution is not None and kaution.betrag and kaution.abgerechnet_am is None:
        faellig = fristrechnung.monate_addieren(
            datum, konfiguration.laden().kaution_abrechnung_monate
        )
        kaution.abrechnung_faellig_am = faellig
        _speichern(kaution)
        kern.arbeitsposten_anlegen(
            ART_KAUTION_ABRECHNEN,
            bezug=kaution,
            faellig_am=faellig,
            hinweis=f"Vertrag {vertrag.nummer}: Rückgabe am {datum:%d.%m.%Y}",
        )
        fristen.frist_anlegen(
            Frist.Art.KAUTIONSABRECHNUNG,
            faellig,
            Frist.Quelle.KAUTION,
            kaution,
            hinweis=f"Kautionsabrechnung, Vertrag {vertrag.nummer}",
            vorlauf_tage=VORLAUF_KAUTION,
        )
