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

from apps.fristen import services as fristen
from apps.fristen.models import Frist
from apps.kern import services as kern
from apps.kern import zeit
from apps.kern.models import Arbeitsposten, Mandant, Objekt
from apps.kern.vorgaenge import als_vorgang
from apps.stammdaten import fristrechnung, konfiguration, schreiben
from apps.stammdaten.models import (
    BINDEND,
    LAUFEND,
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
BINDENDE_STATUS = BINDEND  # Entwurf, aktiv, gekündigt (stammdaten.models.VertragQuerySet)

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
    heute = zeit.heute()
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
    "anschrift_folgt",
    "notiz",
    "herkunft",
)

#: Satz am Feld, wenn der Name einer neuen Partei fehlt (Review 3, Punkt 4).
NAME_FEHLT_PERSON = "Bitte Vor- und Nachnamen angeben (oder den Firmennamen bei einer Firma)."
NAME_FEHLT_FIRMA = "Bitte den Firmennamen angeben."
ANSCHRIFT_FEHLT = (
    "Bitte die Anschrift mit Straße, PLZ und Ort eintragen oder „Anschrift folgt“ anhaken."
)


class ParteiUnvollstaendig(StammdatenFehler):
    """Name oder Anschrift einer neuen Partei fehlen. ``feld`` nennt das Formularfeld, an dem
    der Satz steht (``vorname``, ``nachname``, ``firma`` oder ``anschrift``)."""

    def __init__(self, satz: str, feld: str) -> None:
        super().__init__(satz)
        self.feld = feld


def _partei_pruefen(partei: Partei) -> None:
    if partei.art not in Partei.Art.values:
        raise StammdatenFehler(f"Unbekannte Art {partei.art}")
    if partei.anrede not in Partei.Anrede.values:
        raise StammdatenFehler(f"Unbekannte Anrede {partei.anrede}")
    if partei.art == Partei.Art.FIRMA and not partei.firma:
        raise ParteiUnvollstaendig("Eine Firma braucht einen Firmennamen.", "firma")
    if partei.art == Partei.Art.PERSON and not (partei.nachname or partei.vorname):
        raise ParteiUnvollstaendig("Eine Person braucht einen Namen.", "nachname")
    if partei.hat_anschrift:
        # Ergänzt: „Anschrift folgt“ ist erledigt, Briefe gehen wieder.
        partei.anschrift_folgt = False


def neue_partei_pruefen(
    *,
    art: str,
    vorname: str = "",
    nachname: str = "",
    firma: str = "",
    strasse: str = "",
    plz: str = "",
    ort: str = "",
    anschrift_folgt: bool = False,
) -> None:
    """Was eine neu erfasste Partei mindestens braucht (Review 3, Punkt 4): einen Namen
    (Vor- und Nachname bei einer Person, Firmenname bei einer Firma) und die Anschrift oder
    ausdrücklich „Anschrift folgt“. Wirft ``ParteiUnvollstaendig`` mit dem Feld des Fehlers.

    Gilt für „Neue Partei“ (Seite und Dialog im neuen Vertrag) und „Annehmen und Vertrag
    anlegen“. Importe und der Bestand kennen Lücken; sie stehen im Datenstand."""
    if art == Partei.Art.FIRMA:
        if not firma.strip():
            raise ParteiUnvollstaendig(NAME_FEHLT_FIRMA, "firma")
    else:
        if not vorname.strip():
            raise ParteiUnvollstaendig(NAME_FEHLT_PERSON, "vorname")
        if not nachname.strip():
            raise ParteiUnvollstaendig(NAME_FEHLT_PERSON, "nachname")
    vollstaendig = all(t.strip() for t in (strasse, plz, ort))
    if not vollstaendig and not anschrift_folgt:
        raise ParteiUnvollstaendig(ANSCHRIFT_FEHLT, "anschrift")


def partei_anlegen(art: str = Partei.Art.PERSON, **felder: Any) -> Partei:
    """Legt eine Partei an (H1.2): Mieter, Zahler oder Bürge außerhalb von Wohnraum.

    Ohne ``anrede`` bekommt eine Firma „Firma“ (Sehr geehrte Damen und Herren), eine Person
    „neutral“ (Guten Tag Vorname Nachname)."""
    partei = Partei(art=art)
    if "anrede" not in felder and art == Partei.Art.FIRMA:
        partei.anrede = Partei.Anrede.FIRMA
    _setzen(partei, felder, PARTEI_FELDER)
    _partei_pruefen(partei)
    _speichern(partei)
    logger.debug("Partei #%s angelegt", partei.pk)
    return partei


def partei_neu_anlegen(art: str = Partei.Art.PERSON, **felder: Any) -> Partei:
    """Neue Partei aus der Oberfläche (Seite „Neue Partei“): prüft Name und Anschrift wie
    ``neue_partei_pruefen`` und legt sie dann an."""
    neue_partei_pruefen(
        art=art,
        **{
            k: felder.get(k) or ""
            for k in ("vorname", "nachname", "firma", "strasse", "plz", "ort")
        },
        anschrift_folgt=bool(felder.get("anschrift_folgt")),
    )
    return partei_anlegen(art, **felder)


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


def partei_zur_email(email: str) -> Partei | None:
    """Die Partei mit dieser E-Mail-Adresse (Groß- und Kleinschreibung egal), sonst None.

    Review 2, B5: Aus einer Buchungsanfrage wird eine vorhandene Partei vorgeschlagen, statt
    sie doppelt anzulegen. Bei mehreren Treffern die älteste."""
    email = email.strip()
    if not email:
        return None
    return (
        Partei.objects.filter(
            kontaktwege__art=Kontaktweg.Art.EMAIL, kontaktwege__wert__iexact=email
        )
        .order_by("pk")
        .first()
    )


def partei_mit_kontakt_anlegen(
    *,
    vorname: str = "",
    nachname: str = "",
    firma: str = "",
    email: str = "",
    telefon: str = "",
    anrede: str = "",
    strasse: str = "",
    plz: str = "",
    ort: str = "",
    anschrift_folgt: bool = False,
) -> Partei:
    """Legt eine Partei mit Anschrift, E-Mail und Telefon in einem Schritt an (Review 2, B5
    und B6: „Neue Partei“ direkt im Vertragsdialog, vorausgefüllt aus einer Buchungsanfrage).

    Mit Firmenname und ohne Vor- und Nachnamen wird es eine Firma, sonst eine Person.
    Review 3, Punkt 4: Pflicht sind der Name (Vor- und Nachname oder Firmenname) und die
    Anschrift oder „Anschrift folgt“ (``neue_partei_pruefen``)."""
    art = (
        Partei.Art.FIRMA
        if firma.strip() and not (vorname or nachname).strip()
        else Partei.Art.PERSON
    )
    anschrift = {"strasse": strasse.strip(), "plz": plz.strip(), "ort": ort.strip()}
    neue_partei_pruefen(
        art=art,
        vorname=vorname,
        nachname=nachname,
        firma=firma,
        anschrift_folgt=anschrift_folgt,
        **anschrift,
    )
    felder: dict[str, Any] = {
        "vorname": vorname.strip(),
        "nachname": nachname.strip(),
        "firma": firma.strip(),
        "anschrift_folgt": anschrift_folgt,
        **anschrift,
    }
    if art == Partei.Art.FIRMA and anrede in ("", Partei.Anrede.NEUTRAL):
        anrede = Partei.Anrede.FIRMA
    if anrede:
        felder["anrede"] = anrede
    with transaction.atomic():
        partei = partei_anlegen(art, **felder)
        if email.strip():
            kontaktweg_hinzufuegen(partei, Kontaktweg.Art.EMAIL, email, bevorzugt=True)
        if telefon.strip():
            kontaktweg_hinzufuegen(partei, Kontaktweg.Art.TELEFON, telefon)
    return partei


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


def vertrag_scan_anhaengen(vertrag: Vertrag, scan: File[Any]) -> Vertrag:
    """Hängt den unterschriebenen Scan an, ohne das Datum der Unterschrift zu ändern
    (Datenstand, Sammelaktion „Scans hochladen“, Review 2, B12). Einen vorhandenen Scan
    ersetzt sie nicht."""
    if vertrag.unterschriebener_scan:
        raise StammdatenFehler(f"Vertrag {vertrag.nummer} hat schon einen Scan.")
    vertrag.unterschriebener_scan.save(scan.name or "scan.pdf", scan, save=False)
    _speichern(vertrag)
    logger.info("Vertrag #%s: Scan angehängt", vertrag.pk)
    return vertrag


def vertragsdokument_erzeugen(vertrag: Vertrag) -> schreiben.Ergebnis:
    """Vertrag und Anlagen als PDF aus der Vorlage der Vertragsart (H2.3).

    Ergebnis ist ein Schreiben im Status „bereit“ oder, wenn keine freigegebene Vorlage
    da ist, ein Arbeitsposten „Schreiben erstellen“.
    """
    if vertrag.status != Vertrag.Status.ENTWURF:
        raise StammdatenFehler(f"Vertrag {vertrag.nummer} ist kein Entwurf mehr.")
    ergebnis = schreiben.vertragsdokument_erzeugen(vertrag)
    if not isinstance(ergebnis, Arbeitsposten):
        checkpunkte_automatisch(vertrag, "vertragsdokument", zeit.heute())
    return ergebnis


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
    absender: str = Kuendigung.Absender.PASST,
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

    ``absender`` hält fest, warum die Kündigung gilt, wenn ihr Absender nicht als Mieter am
    Vertrag steht (Review 3, Punkt 9): Vollmacht oder Vertragspartei unter anderem Namen bzw.
    anderer E-Mail-Adresse. Ob das nötig ist, prüft bei Website-Kündigungen
    ``anbindungen.services.kuendigung_aus_anfrage``.
    """
    if vertrag.status != Vertrag.Status.AKTIV:
        raise StammdatenFehler(f"Vertrag {vertrag.nummer} ist nicht aktiv ({vertrag.status}).")
    if von not in Kuendigung.Von.values:
        raise StammdatenFehler(f"Unbekannt, wer kündigt: {von}")
    if zugangsweg not in Kuendigung.Zugangsweg.values:
        raise StammdatenFehler(f"Unbekannter Zugangsweg: {zugangsweg}")
    if absender not in Kuendigung.Absender.values:
        raise StammdatenFehler(f"Unbekannte Angabe zum Absender: {absender}")
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
            absender=absender,
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

KAUTION_FELDER = ("betrag", "art", "eingang_am", "konto")


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
    miete = _monatsmiete(vertrag) if vertrag is not None else None
    with transaction.atomic():
        liste = Checkliste(phase=phase, titel=vorlage.titel, einheit=einheit, vertrag=vertrag)
        _speichern(liste)
        for nummer, punkt in enumerate(vorlage.punkte_fuer(miete), start=1):
            _speichern(Checkpunkt(checkliste=liste, reihenfolge=nummer, text=punkt.text))
        bezug_text = f"Einheit {einheit.nw_id}" if einheit else f"Vertrag {vertrag}"
        kern.arbeitsposten_anlegen(
            ART_CHECKLISTE, bezug=liste, hinweis=f"{vorlage.titel} · {bezug_text}"
        )
        if vertrag is not None and _vertragsdokument_da(vertrag):
            checkpunkte_automatisch(vertrag, "vertragsdokument", zeit.heute())
    return liste


def _monatsmiete(vertrag: Vertrag) -> Decimal | None:
    """Miete je Monat (netto) zu Beginn bzw. heute, für Schwellen wie P8."""
    kondition = vertrag.kondition_am(max(zeit.heute(), vertrag.beginn))
    if kondition is None:
        kondition = vertrag.konditionen.order_by("gueltig_ab").first()
    return kondition.miete if kondition is not None else None


def bonitaet_noetig(vertrag: Vertrag) -> bool:
    """P8 (entschieden am 16.11.): Ab 100 € Monatsmiete ist ein Bonitätsnachweis Pflicht."""
    grenze = konfiguration.laden().bonitaet_ab_miete
    miete = _monatsmiete(vertrag)
    return grenze is not None and miete is not None and miete >= grenze


def _vertragsdokument_da(vertrag: Vertrag) -> bool:
    from django.contrib.contenttypes.models import ContentType

    from apps.dokumente.models import Schreiben

    return (
        Schreiben.objects.filter(
            art="vertrag",
            bezug_typ=ContentType.objects.get_for_model(Vertrag),
            bezug_id=vertrag.pk,
        )
        .exclude(status=Schreiben.Status.VERWORFEN)
        .exists()
    )


def checkpunkte_automatisch(vertrag: Vertrag, ereignis: str, datum: datetime.date) -> int:
    """Hakt die Punkte der Checklisten des Vertrags ab, die ``ereignis`` erledigt (Review 2,
    A11: „Vertrag und Anlagen aus der Vorlage erzeugt“, sobald das PDF da ist)."""
    texte = {
        p.text
        for vorlage in konfiguration.laden().checklisten.values()
        for p in vorlage.vorlagen
        if p.automatisch == ereignis
    }
    anzahl = 0
    offen = Checkpunkt.objects.filter(
        checkliste__vertrag=vertrag, text__in=texte, erledigt_am__isnull=True
    ).select_related("checkliste")
    for punkt in offen:
        checkpunkt_erledigen(punkt, datum, notiz="vom System abgehakt")
        anzahl += 1
    return anzahl


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
        if vertrag.status not in LAUFEND:
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
                if uebergabe.zaehlerstaende.exists():
                    _nicht_mehr_uebersprungen(uebergabe, "zaehler")
            if uebergabe.schluessel:
                _nicht_mehr_uebersprungen(uebergabe, "schluessel")
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


#: Schritte der Erfassung, die sich mit Begründung überspringen lassen (Review 2, A10).
UEBERSPRINGBAR = {"zaehler": "Zählerstände", "schluessel": "Schlüssel"}


@als_vorgang("{uebergabe} {uebergabe.vertrag.nummer}: Schritt {schritt} übersprungen")
def uebergabe_ueberspringen(uebergabe: Uebergabe, schritt: str, begruendung: str) -> Uebergabe:
    """Hält fest, dass ein Schritt ohne Inhalt bleibt, und warum (etwa „Stellplatz ohne
    Strom“). Die Begründung steht im Protokoll der Übergabe."""
    _offen(uebergabe)
    if schritt not in UEBERSPRINGBAR:
        raise StammdatenFehler(f"Der Schritt {schritt} lässt sich nicht überspringen.")
    text = (begruendung or "").strip()
    if not text:
        raise StammdatenFehler(
            f"Bitte kurz begründen, warum der Schritt „{UEBERSPRINGBAR[schritt]}“ entfällt."
        )
    uebersprungen = dict(uebergabe.uebersprungen or {})
    uebersprungen[schritt] = text[:300]
    uebergabe.uebersprungen = uebersprungen
    _speichern(uebergabe)
    return uebergabe


def _nicht_mehr_uebersprungen(uebergabe: Uebergabe, schritt: str) -> None:
    if schritt in (uebergabe.uebersprungen or {}):
        uebersprungen = dict(uebergabe.uebersprungen)
        del uebersprungen[schritt]
        uebergabe.uebersprungen = uebersprungen
        _speichern(uebergabe)


@als_vorgang("Kaution {uebergabe.vertrag.nummer} in der Übergabe erfasst")
def uebergabe_kaution_erfassen(
    uebergabe: Uebergabe,
    *,
    keine: bool,
    betrag: Decimal | None = None,
    art: str = "",
    eingang_am: datetime.date | None = None,
    konto: str | None = None,
) -> Kaution:
    """Schritt „Kaution“ der Übergabe (Review 2, A10): Betrag, Art und Eingang, oder „keine
    Kaution vereinbart“ (Betrag 0)."""
    _offen(uebergabe)
    vertrag = uebergabe.vertrag
    kaution: Kaution | None = Kaution.objects.filter(vertrag=vertrag).first()
    if kaution is None:
        kaution = Kaution(vertrag=vertrag)
    if art not in Kaution.Art.values:
        raise StammdatenFehler(f"Unbekannte Art der Kaution: {art}")
    if keine:
        felder: dict[str, Any] = {"betrag": Decimal("0.00"), "art": "", "eingang_am": None}
    else:
        if betrag is None or betrag <= 0:
            raise StammdatenFehler(
                "Bitte den Betrag der Kaution angeben oder „keine Kaution vereinbart“ wählen."
            )
        felder = {"betrag": betrag, "art": art, "eingang_am": eingang_am}
    if konto is not None:
        felder["konto"] = konto.strip()
    return kaution_aendern(kaution, **felder)


def uebergabe_fehlt(uebergabe: Uebergabe) -> list[tuple[str, str]]:
    """Zusammenfassung am Ende (Review 2, A10): was fehlt, je mit dem Schritt, in dem man es
    nachholt. Die Hindernisse (``uebergabe_hindernisse``) sperren den Abschluss, der Rest ist
    ein Hinweis. Rückgabe: (Text, Schlüssel des Schritts)."""
    fehlt: list[tuple[str, str]] = []
    for text in uebergabe_hindernisse(uebergabe):
        if "unterschrieben" in text:
            fehlt.append((text, "vorab"))
        elif "Kaution" in text:
            fehlt.append((text, "kaution"))
        else:
            fehlt.append((text, "unterschrift"))
    uebersprungen = uebergabe.uebersprungen or {}
    zaehler_alle = zaehler.zaehler_der_uebergabe(uebergabe)
    erfasst = uebergabe.zaehlerstaende.count()
    if "zaehler" not in uebersprungen:
        if not zaehler_alle:
            fehlt.append(("Kein Zähler erfasst und nicht begründet übersprungen.", "zaehler"))
        elif erfasst < len(zaehler_alle):
            fehlt.append((f"Zählerstände: {erfasst} von {len(zaehler_alle)} erfasst.", "zaehler"))
    if not uebergabe.fotos.exists():
        fehlt.append(("Noch kein Foto vom Zustand.", "fotos"))
    if "schluessel" not in uebersprungen:
        if not _schluessel_der_uebergabe(uebergabe).exists():
            fehlt.append(
                ("Keine Schlüssel erfasst und nicht begründet übersprungen.", "schluessel")
            )
        elif not uebergabe.schluessel:
            wort = "ausgegeben" if uebergabe.art == Uebergabe.Art.UEBERGABE else "zurück"
            fehlt.append((f"Keine Schlüssel {wort}.", "schluessel"))
    return fehlt


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
