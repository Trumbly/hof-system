"""Schreibweg der Dokumente: Ablage und Belege (Schritt 3a), Vorlagen, Schreiben, Versand
(Schritt 3c).

Jede Änderung an Dokumenten, Belegen, Vorlagen und Schreiben läuft über eine Funktion in
diesem Modul (CLAUDE.md, Regel 4.1). Ablage und Belege stehen im zweiten Teil der Datei.

Schnittstelle für andere Schritte (Stammdaten: Vertrag, Kündigungsbestätigung;
Mahnwesen: Mahnungen)
==================================================================================

Ein Schreiben entsteht mit::

    from apps.dokumente import services as dokumente

    schreiben = dokumente.schreiben_erzeugen(
        vorlage,            # freigegebene Vorlage, z. B. dokumente.freigegebene_vorlage("mahnung")
        bezug,              # beliebiges gespeichertes Modell-Objekt (Vertrag, Mahnfall …)
        {                   # Kontext: nur einfache Werte, nie Modell-Objekte
            "empfaenger": {
                "name": "…", "anrede": "…",
                "anschrift": "Straße 1\\n12345 Ort",   # Pflicht für Brief/Einschreiben
                "email": "…@….invalid",              # Pflicht für Mail
            },
            "vertrag": {"nummer": "V-12", "beginn": datum, …},
            "betrag": {"offen": Decimal("120.00")},
        },
        versandweg=None,    # None: Standard der Vorlagenart; sonst Versandweg.MAIL usw.
    )
    # → Schreiben im Status „bereit“, mit PDF; nichts ist hinausgegangen.

- Welche Platzhalter eine Vorlage nutzen darf, steht je Art in
  ``apps.dokumente.vorlagenarten`` (``VORLAGENARTEN``). ``absender``, ``datum`` und
  ``zeichen`` setzt der Service selbst; ``absender`` lässt sich im Kontext überschreiben.
- Fehlt ein Platzhalter im Kontext, gibt es ``PlatzhalterFehler``, nie einen leeren Text.
- Gesendet wird nur über ``schreiben_senden`` mit der Prüfsumme des PDFs, das der
  Benutzer in der Vorschau gesehen hat (die Seite „Schreiben“ macht das).
- Serien: ``serie_erzeugen(vorlage, [(bezug, kontext), …])`` und ``serie_senden``.
- Einseitige Erklärungen (Art mit ``einseitige_erklaerung``) gelten erst mit
  Zustellnachweis als erledigt (``schreiben_abschliessen``).
"""

from __future__ import annotations

import datetime
import hashlib
import html
import logging
import re
import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import UploadedFile
from django.core.mail import EmailMessage
from django.db import IntegrityError, models, transaction
from django.db.models import Max
from django.utils import timezone
from django.utils.html import strip_tags

from apps.bank import fingerabdruck
from apps.bank.models import Bankumsatz
from apps.dokumente import (
    dokumentarten,
    letterxpress,
    pdf,
    platzhalter,
    postfach,
    umwandeln,
    vorlagenarten,
)
from apps.dokumente.models import (
    Beleg,
    Dokument,
    Dokumentbezug,
    Eingangsrechnung,
    Lieferant,
    Schreiben,
    Vorlage,
)
from apps.dokumente.platzhalter import PlatzhalterFehler
from apps.dokumente.versand import VersandFehler, mail_senden
from apps.dokumente.vorlagenarten import Versandweg
from apps.fristen import services as fristen_services
from apps.fristen.models import Frist
from apps.kassenbuch import services as kassenbuch_services
from apps.kassenbuch.models import Buchung, Kategorie
from apps.kern import services as kern_services
from apps.kern.models import Arbeitsposten, Benutzer, Lauf, Objekt

logger = logging.getLogger(__name__)

__all__ = [
    "PlatzhalterFehler",
    "SchreibenFehler",
    "VersandFehler",
    "VorlageNichtFreigegeben",
    "ZustellnachweisFehlt",
    "briefkopf",
    "freigegebene_vorlage",
    "schreiben_abschliessen",
    "schreiben_erzeugen",
    "schreiben_senden",
    "schreiben_verwerfen",
    "serie_erzeugen",
    "serie_pruefsumme",
    "serie_senden",
    "versandstand_abrufen",
    "vorlage_aendern",
    "vorlage_anlegen",
    "vorlage_freigeben",
    "vorlage_neue_version",
    "vorlage_pruefen",
    "vorschau_pdf",
]


#: Arten der Arbeitsposten, die der Versand anlegt. Beide melden sich über die
#: Benachrichtigung (``HOF_BENACHRICHTIGUNG_ARTEN``).
ART_KOPIE_FEHLT = "Kopie in „Gesendet“ fehlt"
ART_BRIEF_STORNIERT = "Brief storniert"


class SchreibenFehler(ValueError):
    """Ein Schreiben lässt sich so nicht erzeugen, senden oder abschließen."""


class VorlageNichtFreigegeben(SchreibenFehler):
    pass


class ZustellnachweisFehlt(SchreibenFehler):
    pass


class VorschauFehlt(SchreibenFehler):
    """Gesendet wird nur, was der Benutzer in der Vorschau gesehen hat."""


# --- Vorlagen ------------------------------------------------------------------------


def vorlage_pruefen(art: str, text: str, betreff: str) -> vorlagenarten.Vorlagenart:
    vorlagenart = vorlagenarten.holen(art)
    erlaubt = vorlagenart.erlaubte_platzhalter()
    platzhalter.pruefen(text, erlaubt)
    platzhalter.pruefen(betreff, erlaubt)
    return vorlagenart


def vorlage_anlegen(
    name: str, art: str, text: str, *, betreff: str = "", herkunft: str = ""
) -> Vorlage:
    """Legt eine Vorlage als Entwurf an; gibt es den Namen schon, als nächste Version."""
    vorlage_pruefen(art, text, betreff)
    letzte = Vorlage.objects.filter(name=name).aggregate(m=Max("version"))["m"] or 0
    vorlage = Vorlage(
        name=name,
        art=art,
        version=letzte + 1,
        text=text,
        betreff=betreff,
        herkunft=herkunft,
        status=Vorlage.Status.ENTWURF,
    )
    vorlage.full_clean()
    vorlage.save()
    return vorlage


def vorlage_aendern(
    vorlage: Vorlage,
    *,
    text: str | None = None,
    betreff: str | None = None,
    art: str | None = None,
) -> Vorlage:
    """Ändert einen Entwurf. Freigegebene Versionen bleiben, wie sie sind."""
    if vorlage.status != Vorlage.Status.ENTWURF:
        raise SchreibenFehler(
            f"Vorlage #{vorlage.pk} ist {vorlage.get_status_display()}; "
            "Änderungen nur als neue Version."
        )
    if text is not None:
        vorlage.text = text
    if betreff is not None:
        vorlage.betreff = betreff
    if art is not None:
        vorlage.art = art
    vorlage_pruefen(vorlage.art, vorlage.text, vorlage.betreff)
    vorlage.full_clean()
    vorlage.save()
    return vorlage


def vorlage_neue_version(vorlage: Vorlage) -> Vorlage:
    """Legt die nächste Version als Entwurf an, mit dem Text dieser Version."""
    return vorlage_anlegen(
        vorlage.name, vorlage.art, vorlage.text, betreff=vorlage.betreff, herkunft=vorlage.herkunft
    )


def vorlage_freigeben(
    vorlage: Vorlage, benutzer: Benutzer, *, am: datetime.datetime | None = None
) -> Vorlage:
    """Gibt einen Entwurf frei. Die bisher freigegebene Version desselben Namens wird
    „abgelöst“; Schreiben behalten ihre Version."""
    if vorlage.status != Vorlage.Status.ENTWURF:
        raise SchreibenFehler(f"Vorlage #{vorlage.pk} ist kein Entwurf.")
    vorlage_pruefen(vorlage.art, vorlage.text, vorlage.betreff)
    with transaction.atomic():
        for alt in Vorlage.objects.filter(
            name=vorlage.name, status=Vorlage.Status.FREIGEGEBEN
        ).exclude(pk=vorlage.pk):
            alt.status = Vorlage.Status.ABGELOEST
            alt.save(update_fields=["status"])
        vorlage.status = Vorlage.Status.FREIGEGEBEN
        vorlage.freigegeben_von = benutzer
        vorlage.freigegeben_am = am or timezone.now()
        vorlage.save(update_fields=["status", "freigegeben_von", "freigegeben_am"])
    logger.info("Vorlage #%s freigegeben von Benutzer #%s", vorlage.pk, benutzer.pk)
    return vorlage


def freigegebene_vorlage(art: str, name: str | None = None) -> Vorlage:
    """Die neueste freigegebene Vorlage einer Art (optional mit Namen)."""
    abfrage = Vorlage.objects.filter(art=art, status=Vorlage.Status.FREIGEGEBEN)
    if name is not None:
        abfrage = abfrage.filter(name=name)
    vorlage = abfrage.order_by("-freigegeben_am", "-pk").first()
    if vorlage is None:
        raise VorlageNichtFreigegeben(f"Es gibt keine freigegebene Vorlage der Art {art}.")
    return vorlage


def briefkopf() -> Vorlage:
    """Der freigegebene Briefkopf (eine Vorlage der Art „briefkopf“)."""
    return freigegebene_vorlage("briefkopf")


# --- Kontext und PDF -----------------------------------------------------------------


def absender() -> dict[str, str]:
    """Absenderangaben aus den Einstellungen (``DOKUMENTE["ABSENDER"]``)."""
    werte: Mapping[str, str] = settings.DOKUMENTE["ABSENDER"]
    return dict(werte)


def _anschriftfeld(kontext: Mapping[str, Any]) -> str:
    empfaenger = kontext.get("empfaenger") or {}
    zeilen = [str(empfaenger.get("name") or ""), str(empfaenger.get("anschrift") or "")]
    return "\n".join(z for z in zeilen if z.strip())


def _briefkopf_html(kontext: Mapping[str, Any]) -> str:
    kopf = briefkopf()
    return platzhalter.rendern(kopf.text, {"absender": kontext["absender"]})


def _pdf_fuer(
    vorlage: Vorlage, kontext: Mapping[str, Any], *, briefkopf_html: str
) -> tuple[str, str, bytes]:
    """(Betreff, Inhalt-HTML, PDF) aus Vorlage und vollständigem Kontext."""
    inhalt = platzhalter.rendern(vorlage.text, kontext)
    betreff = html.unescape(platzhalter.rendern(vorlage.betreff, kontext)).strip()
    seite = pdf.brief_html(
        briefkopf_html=briefkopf_html,
        inhalt_html=inhalt,
        betreff=betreff,
        empfaenger_anschrift=_anschriftfeld(kontext),
        datum=kontext["datum"],
        zeichen=str(kontext["zeichen"]),
    )
    return betreff, inhalt, pdf.erzeugen(seite)


def vorschau_pdf(vorlage: Vorlage) -> bytes:
    """PDF einer Vorlage mit Beispielwerten (jeder Platzhalter zeigt seinen Namen).

    Geht auch für Entwürfe; es entsteht kein Schreiben. Ohne freigegebenen Briefkopf
    steht der Briefkopf-Entwurf mit der höchsten Version oben, sonst nichts.
    """
    erlaubt = vorlage.vorlagenart.erlaubte_platzhalter()
    kontext = platzhalter.beispielkontext(erlaubt, vorlage.text + vorlage.betreff)
    kontext["absender"] = {
        **platzhalter.beispielkontext(vorlagenarten.BRIEFKOPF_PLATZHALTER)["absender"],
        **kontext.get("absender", {}),
    }
    kontext["datum"] = timezone.localdate()
    kontext["zeichen"] = "S-Vorschau"
    if vorlage.vorlagenart.briefkopf:
        kopf_text = vorlage.text
        rumpf = Vorlage(art="mitteilung", text="<p>‹Inhalt des Schreibens›</p>", betreff="")
    else:
        kopf = (
            Vorlage.objects.filter(art="briefkopf")
            .order_by(
                models.Case(models.When(status=Vorlage.Status.FREIGEGEBEN, then=0), default=1),
                "-version",
            )
            .first()
        )
        kopf_text = kopf.text if kopf is not None else ""
        rumpf = vorlage
    kopf_html = platzhalter.rendern(kopf_text, {"absender": kontext["absender"]})
    return _pdf_fuer(rumpf, kontext, briefkopf_html=kopf_html)[2]


def _versandweg_pruefen(versandweg: str, kontext: Mapping[str, Any]) -> None:
    if versandweg not in Versandweg.TITEL:
        raise SchreibenFehler(f"Unbekannter Versandweg {versandweg}.")
    empfaenger = kontext.get("empfaenger")
    if not isinstance(empfaenger, Mapping):
        raise SchreibenFehler("Im Kontext fehlt „empfaenger“.")
    if versandweg == Versandweg.MAIL and not str(empfaenger.get("email") or "").strip():
        raise SchreibenFehler("Für den Versand per Mail fehlt empfaenger.email.")
    if (
        versandweg in (Versandweg.BRIEF, Versandweg.EINWURF_EINSCHREIBEN)
        and not str(empfaenger.get("anschrift") or "").strip()
    ):
        raise SchreibenFehler("Für den Versand als Brief fehlt empfaenger.anschrift.")


def _sha256(daten: bytes) -> str:
    return hashlib.sha256(daten).hexdigest()


def schreiben_erzeugen(
    vorlage: Vorlage,
    bezug: models.Model,
    kontext: Mapping[str, Any],
    versandweg: str | None = None,
    *,
    serie: uuid.UUID | None = None,
) -> Schreiben:
    """Erzeugt aus einer freigegebenen Vorlage ein Schreiben im Status „bereit“.

    Args:
        vorlage: Eine freigegebene Vorlage (Status „freigegeben“). Entwürfe und abgelöste
            Versionen lassen sich nicht scharf verwenden (``VorlageNichtFreigegeben``).
        bezug: Das gespeicherte Objekt, an dem das Schreiben hängt (generisch:
            Vertrag, Einheit, Mahnfall, Objekt …).
        kontext: Werte für die Platzhalter, nur einfache Werte (Text, Zahl, Decimal,
            Datum, Listen, Wörterbücher). Pflicht ist ``empfaenger`` mit ``name`` und je
            nach Versandweg ``anschrift`` (Brief) oder ``email`` (Mail). ``absender``,
            ``datum`` und ``zeichen`` ergänzt der Service.
        versandweg: ``Versandweg.MAIL``, ``BRIEF``, ``EINWURF_EINSCHREIBEN`` oder
            ``SELBST``; ``None`` nimmt den Standard der Vorlagenart.
        serie: Kennung einer Serie (``serie_erzeugen`` setzt sie).

    Returns:
        Das Schreiben mit PDF (Dateiname aus Art, Datum und ID) und Prüfsumme. Es ist
        nichts versendet; das geschieht nur über ``schreiben_senden``.

    Raises:
        VorlageNichtFreigegeben, PlatzhalterFehler (unbekannter oder fehlender
        Platzhalter, Kontext mit Objekten), SchreibenFehler (Empfängerangaben fehlen),
        pdf.PdfNichtVerfuegbar (WeasyPrint ohne Systembibliotheken).
    """
    if vorlage.status != Vorlage.Status.FREIGEGEBEN:
        raise VorlageNichtFreigegeben(
            f"Vorlage #{vorlage.pk} ist {vorlage.get_status_display()}, nicht freigegeben."
        )
    vorlagenart = vorlage.vorlagenart
    if vorlagenart.briefkopf:
        raise SchreibenFehler("Ein Briefkopf ist kein Schreiben.")
    if bezug.pk is None:
        raise SchreibenFehler("Der Bezug muss gespeichert sein.")
    versandweg = versandweg or vorlagenart.versandweg
    platzhalter.kontext_pruefen(kontext)
    _versandweg_pruefen(versandweg, kontext)

    heute = timezone.localdate()
    voll: dict[str, Any] = {"absender": absender(), **kontext, "datum": heute}
    kopf_html = _briefkopf_html(voll)

    with transaction.atomic():
        schreiben = Schreiben(
            vorlage=vorlage,
            vorlage_version=vorlage.version,
            art=vorlage.art,
            einseitige_erklaerung=vorlagenart.einseitige_erklaerung,
            bezug_typ=ContentType.objects.get_for_model(bezug),
            bezug_id=bezug.pk,
            serie=serie,
            versandweg=versandweg,
            empfaenger_email=str(voll["empfaenger"].get("email") or "").strip(),
        )
        schreiben.save()
        voll["zeichen"] = f"S-{schreiben.pk}"
        betreff, inhalt, daten = _pdf_fuer(vorlage, voll, briefkopf_html=kopf_html)
        schreiben.betreff = betreff[:300]
        schreiben.inhalt_html = inhalt
        schreiben.pdf_sha256 = _sha256(daten)
        dateiname = f"{vorlage.art}_{heute:%Y-%m-%d}_{schreiben.pk}.pdf"
        schreiben.pdf.save(dateiname, ContentFile(daten), save=False)
        schreiben.save()
    logger.info(
        "Schreiben #%s erzeugt (Vorlage #%s v%s, %s)",
        schreiben.pk,
        vorlage.pk,
        vorlage.version,
        versandweg,
    )
    return schreiben


def serie_erzeugen(
    vorlage: Vorlage,
    bezuege: Iterable[tuple[models.Model, Mapping[str, Any]]],
    versandweg: str | None = None,
) -> list[Schreiben]:
    """Serienmitteilung (H8.4): eine Vorlage, viele Bezüge, jedes Schreiben einzeln.

    ``bezuege`` sind Paare aus Objekt und Kontext. Alle Schreiben tragen dieselbe
    Serien-Kennung; ``serie_senden`` sendet sie mit einem Klick.
    """
    kennung = uuid.uuid4()
    with transaction.atomic():
        schreiben = [
            schreiben_erzeugen(vorlage, bezug, kontext, versandweg, serie=kennung)
            for bezug, kontext in bezuege
        ]
    if not schreiben:
        raise SchreibenFehler("Eine Serie braucht mindestens einen Bezug.")
    return schreiben


# --- Senden --------------------------------------------------------------------------


def _mail_text(schreiben: Schreiben) -> str:
    text = html.unescape(strip_tags(re.sub(r"<br\s*/?>|</p>", "\n", schreiben.inhalt_html)))
    zeilen = [z.strip() for z in text.splitlines()]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(zeilen)).strip() + "\n"


def _pdf_daten(schreiben: Schreiben) -> bytes:
    with schreiben.pdf.open("rb") as datei:
        daten: bytes = datei.read()
    if _sha256(daten) != schreiben.pdf_sha256:
        raise SchreibenFehler(f"PDF von Schreiben #{schreiben.pk} passt nicht zur Prüfsumme.")
    return daten


def _dateiname(schreiben: Schreiben) -> str:
    name: str = schreiben.pdf.name
    return name.rsplit("/", 1)[-1]


def _senden(schreiben: Schreiben, benutzer: Benutzer) -> Schreiben:
    daten = _pdf_daten(schreiben)
    jetzt = timezone.now()
    if schreiben.versandweg == Versandweg.MAIL:
        nachricht = EmailMessage(
            subject=schreiben.betreff,
            body=_mail_text(schreiben),
            from_email=settings.DOKUMENTE["MAIL_ABSENDER"],
            to=[schreiben.empfaenger_email],
        )
        nachricht.attach(_dateiname(schreiben), daten, "application/pdf")
        ergebnis = mail_senden(nachricht)
        schreiben.dienst_auftrag = ergebnis.message_id[:255]
        schreiben.einlieferungsdatum = timezone.localdate(jetzt)
        if ergebnis.kopie_fehler:
            kern_services.arbeitsposten_anlegen(
                ART_KOPIE_FEHLT,
                bezug=schreiben,
                hinweis=f"Schreiben #{schreiben.pk}: {ergebnis.kopie_fehler}",
            )
    elif schreiben.ist_brief:
        einschreiben = (
            letterxpress.EINWURF_EINSCHREIBEN
            if schreiben.versandweg == Versandweg.EINWURF_EINSCHREIBEN
            else None
        )
        auftrag = letterxpress.brief_client().auftrag_senden(
            daten,
            einschreiben=einschreiben,
            dateiname=_dateiname(schreiben),
            notiz=f"Schreiben {schreiben.pk}",
        )
        schreiben.dienst_auftrag = auftrag.auftrag_id
        schreiben.sendungsnummer = auftrag.sendungsnummer
    # Versandweg „selbst“: Der Klick hält fest, dass das Schreiben übergeben wird.
    schreiben.status = Schreiben.Status.GESENDET
    schreiben.gesendet_am = jetzt
    schreiben.gesendet_von = benutzer
    schreiben.save()
    logger.info(
        "Schreiben #%s gesendet (%s) von Benutzer #%s",
        schreiben.pk,
        schreiben.versandweg,
        benutzer.pk,
    )
    return schreiben


def _sendebereit(schreiben: Schreiben, benutzer: Benutzer) -> None:
    if not isinstance(benutzer, Benutzer) or not benutzer.is_active or benutzer.pk is None:
        raise SchreibenFehler("Senden nur durch einen angemeldeten Benutzer.")
    if schreiben.status != Schreiben.Status.BEREIT:
        raise SchreibenFehler(
            f"Schreiben #{schreiben.pk} ist {schreiben.get_status_display()}, nicht bereit."
        )


def schreiben_senden(schreiben: Schreiben, benutzer: Benutzer, *, pruefsumme: str) -> Schreiben:
    """Sendet ein Schreiben im Status „bereit“ (Aktion „Senden“, CLAUDE.md Regel 8).

    ``pruefsumme`` ist die SHA-256 des PDFs aus der Vorschau, die der Benutzer gesehen
    hat; passt sie nicht (anderes oder kein PDF gesehen), geht nichts hinaus.
    Mail: SMTP und Kopie in „Gesendet“. Brief und Einwurf-Einschreiben: Auftrag bei
    LetterXpress, Sendungsnummer und Einlieferung holt ``versandstand_abrufen``.
    Selbst: nur der Vermerk „gesendet“. Scheitert der Versand, bleibt es „bereit“.
    """
    with transaction.atomic():
        schreiben = Schreiben.objects.select_for_update().get(pk=schreiben.pk)
        _sendebereit(schreiben, benutzer)
        if not pruefsumme or pruefsumme != schreiben.pdf_sha256:
            raise VorschauFehlt(
                f"Schreiben #{schreiben.pk}: Erst die Vorschau des PDFs ansehen, dann senden."
            )
        return _senden(schreiben, benutzer)


def serie_pruefsumme(schreiben: Sequence[Schreiben]) -> str:
    """Prüfsumme über die PDFs einer Serie (für die Vorschau der Serie)."""
    teile = [f"{s.pk}:{s.pdf_sha256}" for s in sorted(schreiben, key=lambda s: s.pk)]
    return _sha256("\n".join(teile).encode("ascii"))


@dataclass
class SerienErgebnis:
    gesendet: list[Schreiben] = field(default_factory=list)
    fehler: dict[int, str] = field(default_factory=dict)  # Schreiben-ID → Grund


def serie_senden(serie: uuid.UUID, benutzer: Benutzer, *, pruefsumme: str) -> SerienErgebnis:
    """Sendet alle bereiten Schreiben einer Serie mit einem Klick (H8.4).

    ``pruefsumme`` kommt aus ``serie_pruefsumme`` über genau die Schreiben, die die
    Vorschau gezeigt hat. Jedes Schreiben geht einzeln; scheitert eines, bleibt es
    „bereit“ und die anderen gehen trotzdem.
    """
    ergebnis = SerienErgebnis()
    with transaction.atomic():
        bereit = list(
            Schreiben.objects.select_for_update()
            .filter(serie=serie, status=Schreiben.Status.BEREIT)
            .order_by("pk")
        )
        if not bereit:
            raise SchreibenFehler("In dieser Serie ist kein Schreiben bereit.")
        if pruefsumme != serie_pruefsumme(bereit):
            raise VorschauFehlt("Erst die Vorschau der Serie ansehen, dann senden.")
        for schreiben in bereit:
            _sendebereit(schreiben, benutzer)
            try:
                with transaction.atomic():
                    ergebnis.gesendet.append(_senden(schreiben, benutzer))
            except (VersandFehler, SchreibenFehler) as fehler:
                ergebnis.fehler[schreiben.pk] = str(fehler)
                logger.warning("Schreiben #%s nicht gesendet: %s", schreiben.pk, fehler)
    return ergebnis


def schreiben_verwerfen(schreiben: Schreiben) -> Schreiben:
    """Verwirft ein Schreiben, das noch nicht gesendet ist (Stornieren statt löschen)."""
    if schreiben.status != Schreiben.Status.BEREIT:
        raise SchreibenFehler(f"Schreiben #{schreiben.pk} ist nicht mehr bereit.")
    schreiben.status = Schreiben.Status.VERWORFEN
    schreiben.save(update_fields=["status"])
    return schreiben


# --- Nach dem Versand ----------------------------------------------------------------


def schreiben_abschliessen(
    schreiben: Schreiben,
    *,
    zugestellt_am: datetime.date,
    nachweis: UploadedFile | ContentFile[bytes] | None = None,
    vermerk: str = "",
) -> Schreiben:
    """Hält die Zustellung fest; das Schreiben ist dann erledigt (Status „zugestellt“).

    Bei einer einseitigen Erklärung (H8.3) ist ein Zustellnachweis Pflicht: eine Datei
    (Auslieferungsbeleg, Foto) oder ein Vermerk (Sendungsverfolgung, Einwurf durch
    Boten). Ohne ihn kommt ``ZustellnachweisFehlt``; die Datenbank sichert das zusätzlich
    mit einer Prüfbedingung.
    """
    schreiben.refresh_from_db()
    if schreiben.status != Schreiben.Status.GESENDET:
        raise SchreibenFehler(
            f"Schreiben #{schreiben.pk} ist {schreiben.get_status_display()}, nicht gesendet."
        )
    vermerk = vermerk.strip()
    ohne_nachweis = nachweis is None and not vermerk and not schreiben.hat_zustellnachweis
    if schreiben.einseitige_erklaerung and ohne_nachweis:
        raise ZustellnachweisFehlt(
            f"Schreiben #{schreiben.pk} ist eine einseitige Erklärung: "
            "ohne Zustellnachweis nicht abschließbar."
        )
    with transaction.atomic():
        if nachweis is not None:
            schreiben.zustellnachweis.save(nachweis.name or "nachweis.pdf", nachweis, save=False)
        if vermerk:
            schreiben.zustellnachweis_vermerk = vermerk
        schreiben.zugestellt_am = zugestellt_am
        schreiben.status = Schreiben.Status.ZUGESTELLT
        schreiben.save()
    return schreiben


_ZUGESTELLT_AM = re.compile(r"(\d{2})\.(\d{2})\.(\d{2,4})")


def _zustelldatum(sendungsstatus: str) -> datetime.date | None:
    treffer = _ZUGESTELLT_AM.search(sendungsstatus)
    if treffer is None:
        return None
    tag, monat, jahr = (int(t) for t in treffer.groups())
    try:
        return datetime.date(jahr + 2000 if jahr < 100 else jahr, monat, tag)
    except ValueError:
        return None


def versandstand_abrufen(schreiben: Schreiben) -> bool:
    """Holt Sendungsnummer, Einlieferungsdatum und Zustellung bei LetterXpress.

    Meldet die Sendungsverfolgung „Zugestellt“, wird das Schreiben mit diesem Vermerk
    als Zustellnachweis abgeschlossen. Rückgabe: ob sich etwas geändert hat.
    """
    if not schreiben.ist_brief or schreiben.status != Schreiben.Status.GESENDET:
        return False
    if not schreiben.dienst_auftrag:
        raise SchreibenFehler(f"Schreiben #{schreiben.pk} hat keinen Auftrag bei LetterXpress.")
    auftrag = letterxpress.brief_client().auftrag_abrufen(schreiben.dienst_auftrag)
    geaendert = False
    if auftrag.sendungsnummer and auftrag.sendungsnummer != schreiben.sendungsnummer:
        schreiben.sendungsnummer = auftrag.sendungsnummer
        geaendert = True
    if auftrag.einlieferungsdatum and auftrag.einlieferungsdatum != schreiben.einlieferungsdatum:
        schreiben.einlieferungsdatum = auftrag.einlieferungsdatum
        geaendert = True
    if auftrag.status == "canceled":
        kern_services.arbeitsposten_anlegen(
            ART_BRIEF_STORNIERT,
            bezug=schreiben,
            hinweis=f"LetterXpress-Auftrag {auftrag.auftrag_id} ist storniert.",
        )
    if geaendert:
        schreiben.save(update_fields=["sendungsnummer", "einlieferungsdatum"])
    if auftrag.zugestellt:
        schreiben_abschliessen(
            schreiben,
            zugestellt_am=_zustelldatum(auftrag.sendungsstatus) or timezone.localdate(),
            vermerk=(
                f"LetterXpress, Sendung {auftrag.sendungsnummer or auftrag.auftrag_id}: "
                f"{auftrag.sendungsstatus}"
            ),
        )
        geaendert = True
    return geaendert


# =====================================================================================
# Ablage und Belege (Schritt 3a, H6.1, H6.2, H6.4, H6.8)
# =====================================================================================
#
# Schnittstelle für andere Schritte
# ---------------------------------
# Ein Dokument ablegen und an etwas hängen (Stammdaten: Vertrag, Einheit, Partei)::
#
#     ablage = dokumente.dokument_ablegen(
#         inhalt, art="sonstiges", datum=datum, herkunft=Dokument.Herkunft.UPLOAD
#     )
#     dokumente.dokument_bezug_setzen(ablage.dokument, vertrag)
#     dokumente.dokumente_zu(vertrag)   # alle Dokumente am Vertrag
#
# ``dokument_ablegen`` erkennt Dubletten an der SHA-256 der eingegangenen Datei und liefert
# dann das vorhandene Dokument (``ablage.neu`` ist False). Fotos werden zu PDF.

#: Art des Arbeitspostens je offenem Beleg. Meldet sich über die Benachrichtigung.
ART_BELEGE_ZUORDNEN = "Belege zuordnen"
#: Aktionen am Posten: Seite des Belegs öffnen (dort ausfüllen und bestätigen) oder zurückstellen.
AKTIONEN_BELEG = ("beleg_zuordnen", "zurueckstellen")

#: So viele Tage vor und nach dem Belegdatum sucht die Seite des Belegs nach Buchungen.
KANDIDATEN_TAGE = 60


class AblageFehler(ValueError):
    """Ein Dokument oder Beleg lässt sich so nicht ablegen oder ändern (nur IDs in der
    Meldung)."""


class BelegFehler(AblageFehler):
    pass


# --- Lieferanten ---------------------------------------------------------------------


def _lieferant_pruefen(lieferant: Lieferant) -> None:
    lieferant.name = lieferant.name.strip()
    lieferant.iban = fingerabdruck.iban_normal(lieferant.iban)
    lieferant.email = lieferant.email.strip().lower()
    if not lieferant.name:
        raise AblageFehler("Der Lieferant braucht einen Namen.")
    doppelt = Lieferant.objects.filter(name__iexact=lieferant.name).exclude(pk=lieferant.pk)
    if doppelt.exists():
        raise AblageFehler("Einen Lieferanten mit diesem Namen gibt es schon.")
    lieferant.full_clean()


def lieferant_anlegen(name: str, *, iban: str = "", email: str = "") -> Lieferant:
    """Legt einen Lieferanten an. Die E-Mail erkennt ihn im Rechnungspostfach wieder."""
    lieferant = Lieferant(name=name, iban=iban, email=email)
    _lieferant_pruefen(lieferant)
    lieferant.save()
    return lieferant


def lieferant_aendern(
    lieferant: Lieferant,
    *,
    name: str | None = None,
    iban: str | None = None,
    email: str | None = None,
    aktiv: bool | None = None,
) -> Lieferant:
    if name is not None:
        lieferant.name = name
    if iban is not None:
        lieferant.iban = iban
    if email is not None:
        lieferant.email = email
    if aktiv is not None:
        lieferant.aktiv = aktiv
    _lieferant_pruefen(lieferant)
    lieferant.save()
    return lieferant


def lieferant_zur_mail(absender: str) -> Lieferant | None:
    """Der aktive Lieferant mit dieser Absenderadresse, wenn es genau einen gibt."""
    absender = absender.strip().lower()
    if not absender:
        return None
    treffer = list(Lieferant.objects.filter(email__iexact=absender, aktiv=True)[:2])
    return treffer[0] if len(treffer) == 1 else None


# --- Ablage --------------------------------------------------------------------------


@dataclass(frozen=True)
class Ablage:
    dokument: Dokument
    #: False: Die Datei lag schon in der Ablage (Dublette nach SHA-256).
    neu: bool


def _ablage_dateiname(art: str, datum: datetime.date, pk: int, endung: str = "pdf") -> str:
    """Art, Datum und ID, nie ein Personenname."""
    return f"{art}_{datum:%Y-%m-%d}_{pk}.{endung}"


def _endung(name: str) -> str:
    return name.rsplit(".", 1)[-1].lower() if "." in name else "bin"


def _datei_umbenennen(datei: Any, neuer_name: str, pfad: str) -> None:
    """Legt den Inhalt eines FileField unter neuem Namen ab und löscht die alte Datei."""
    if not datei or datei.name == pfad:
        return
    with datei.open("rb") as quelle:
        inhalt = quelle.read()
    alt = datei.name
    if datei.storage.exists(pfad):
        datei.storage.delete(pfad)
    datei.save(neuer_name, ContentFile(inhalt), save=False)
    datei.storage.delete(alt)


def _dateien_angleichen(dokument: Dokument) -> None:
    """Dateinamen und Ordner nach Art und Datum des Dokuments (nach einer Änderung)."""
    for feld, endung in (("datei", "pdf"), ("original", None)):
        datei = getattr(dokument, feld)
        if not datei:
            continue
        name = _ablage_dateiname(
            dokument.art, dokument.datum, dokument.pk, endung or _endung(datei.name)
        )
        _datei_umbenennen(datei, name, datei.field.generate_filename(dokument, name))


def dokument_ablegen(
    inhalt: bytes,
    *,
    art: str,
    datum: datetime.date,
    herkunft: str,
    eingangskennung: str = "",
    bemerkung: str = "",
) -> Ablage:
    """Legt eine Datei (PDF oder Foto) in die Ablage (H6.1, H6.2).

    Fotos werden zu PDF (``apps.dokumente.umwandeln``), das Foto bleibt als Original. Die
    Löschfrist kommt aus der Dokumentart. Gibt es eine Datei mit derselben SHA-256 schon,
    entsteht nichts Neues: Das Ergebnis nennt das vorhandene Dokument mit ``neu=False``.

    Raises:
        umwandeln.DateiUngueltig: weder PDF noch lesbares Foto (oder zu groß).
        AblageFehler: unbekannte Art oder Herkunft.
    """
    try:
        dokumentart = dokumentarten.holen(art)
    except dokumentarten.UnbekannteDokumentart as fehler:
        raise AblageFehler(f"Unbekannte Dokumentart: {art}") from fehler
    if herkunft not in Dokument.Herkunft.values:
        raise AblageFehler(f"Unbekannte Herkunft: {herkunft}")
    eingang = umwandeln.pruefen(inhalt)
    pruefsumme = _sha256(inhalt)
    vorhanden = Dokument.objects.filter(sha256=pruefsumme).first()
    if vorhanden is not None:
        logger.info("Dokument #%s: Dublette erkannt", vorhanden.pk)
        return Ablage(dokument=vorhanden, neu=False)

    dokument = Dokument(
        art=art,
        datum=datum,
        sha256=pruefsumme,
        dateityp=eingang.dateityp,
        herkunft=herkunft,
        eingangskennung=eingangskennung[:255],
        bemerkung=bemerkung,
        loeschfrist=dokumentart.loeschfrist(datum),
    )
    dokument.full_clean(exclude=["datei", "original", "sha256"])
    try:
        with transaction.atomic():
            dokument.save()
            dokument.datei.save(
                _ablage_dateiname(art, datum, dokument.pk), ContentFile(eingang.pdf), save=False
            )
            if eingang.original is not None:
                dokument.original.save(
                    _ablage_dateiname(art, datum, dokument.pk, eingang.endung),
                    ContentFile(eingang.original),
                    save=False,
                )
            dokument.save(update_fields=["datei", "original"])
    except IntegrityError:
        # Gleichzeitig abgelegt: Das andere Dokument gilt.
        return Ablage(dokument=Dokument.objects.get(sha256=pruefsumme), neu=False)
    logger.info("Dokument #%s abgelegt (%s, %s)", dokument.pk, art, herkunft)
    return Ablage(dokument=dokument, neu=True)


def dokument_aendern(
    dokument: Dokument,
    *,
    art: str | None = None,
    datum: datetime.date | None = None,
    bemerkung: str | None = None,
) -> Dokument:
    """Ändert Art, Datum oder Bemerkung. Löschfrist und Dateiname folgen Art und Datum."""
    if art is not None:
        try:
            neue_art = dokumentarten.holen(art)
        except dokumentarten.UnbekannteDokumentart as fehler:
            raise AblageFehler(f"Unbekannte Dokumentart: {art}") from fehler
        aktive_belege = dokument.belege.exclude(status=Beleg.Status.VERWORFEN)
        if art != dokument.art and not neue_art.beleg and aktive_belege.exists():
            raise AblageFehler(
                f"Dokument #{dokument.pk} hat Belege; erst verwerfen, dann umwidmen."
            )
        dokument.art = art
    if datum is not None:
        dokument.datum = datum
    if bemerkung is not None:
        dokument.bemerkung = bemerkung
    dokument.loeschfrist = dokument.dokumentart.loeschfrist(dokument.datum)
    with transaction.atomic():
        _dateien_angleichen(dokument)
        dokument.full_clean(exclude=["datei", "original"])
        dokument.save()
    return dokument


def dokument_bezug_setzen(dokument: Dokument, bezug: models.Model) -> Dokumentbezug:
    """Hängt ein Dokument an ein gespeichertes Objekt (idempotent)."""
    if bezug.pk is None:
        raise AblageFehler("Der Bezug muss gespeichert sein.")
    typ = ContentType.objects.get_for_model(bezug)
    vorhanden = Dokumentbezug.objects.filter(
        dokument=dokument, bezug_typ=typ, bezug_id=bezug.pk
    ).first()
    if vorhanden is not None:
        return vorhanden
    eintrag = Dokumentbezug(dokument=dokument, bezug_typ=typ, bezug_id=bezug.pk)
    eintrag.save()
    return eintrag


def dokument_bezug_loesen(dokument: Dokument, bezug: models.Model) -> bool:
    """Löst einen Bezug (steht im Protokoll und lässt sich zurücknehmen)."""
    typ = ContentType.objects.get_for_model(bezug)
    eintraege = Dokumentbezug.objects.filter(dokument=dokument, bezug_typ=typ, bezug_id=bezug.pk)
    geloest = False
    for eintrag in eintraege:
        eintrag.delete()
        geloest = True
    return geloest


def dokumente_zu(bezug: models.Model) -> models.QuerySet[Dokument]:
    """Alle Dokumente, die an einem Objekt hängen (neueste zuerst)."""
    typ = ContentType.objects.get_for_model(bezug)
    return Dokument.objects.filter(bezuege__bezug_typ=typ, bezuege__bezug_id=bezug.pk).distinct()


# --- Belege --------------------------------------------------------------------------


@dataclass(frozen=True)
class Vorschlag:
    """Vorschlag für einen neuen Beleg aus dem letzten bestätigten Beleg des Lieferanten."""

    objekt: Objekt
    kategorie: Kategorie
    leistungsjahr: int
    umlagefaehiger_anteil: Decimal
    aus_beleg: Beleg


def vorschlag(lieferant: Lieferant | None, datum: datetime.date) -> Vorschlag | None:
    """Objekt, Kostenart, Leistungsjahr und umlagefähiger Anteil wie beim letzten Beleg.

    Das Leistungsjahr hält den Abstand zum Belegdatum: Kam die letzte Jahresrechnung im
    Folgejahr (Leistungsjahr = Belegjahr - 1), ist es diesmal wieder das Vorjahr.
    """
    if lieferant is None or lieferant.pk is None:
        return None
    letzter = (
        Beleg.objects.filter(lieferant=lieferant, status=Beleg.Status.BESTAETIGT)
        .select_related("objekt", "kategorie", "dokument")
        .order_by("-dokument__datum", "-pk")
        .first()
    )
    if letzter is None or letzter.objekt is None or letzter.kategorie is None:
        return None
    belegjahr = letzter.dokument.datum.year
    abstand = (letzter.leistungsjahr or belegjahr) - belegjahr
    return Vorschlag(
        objekt=letzter.objekt,
        kategorie=letzter.kategorie,
        leistungsjahr=datum.year + abstand,
        umlagefaehiger_anteil=(
            letzter.umlagefaehiger_anteil
            if letzter.umlagefaehiger_anteil is not None
            else letzter.kategorie.umlagefaehiger_anteil
        ),
        aus_beleg=letzter,
    )


def _beleg_posten(beleg: Beleg) -> list[Arbeitsposten]:
    return list(
        Arbeitsposten.objects.filter(
            art=ART_BELEGE_ZUORDNEN,
            bezug_typ=ContentType.objects.get_for_model(Beleg),
            bezug_id=beleg.pk,
            status__in=[Arbeitsposten.Status.OFFEN, Arbeitsposten.Status.ZURUECKGESTELLT],
        )
    )


def _beleg_posten_schliessen(beleg: Beleg) -> None:
    for posten in _beleg_posten(beleg):
        kern_services.arbeitsposten_erledigen(posten)


def _beleg_hinweis(beleg: Beleg) -> str:
    """Hinweis am Posten ohne Personendaten, Beträge und IDs (der Bezug nennt den Beleg)."""
    dokument = beleg.dokument
    teile = [
        f"Eingang vom {dokument.datum:%d.%m.%Y} ({dokument.get_herkunft_display()})",
        "Lieferant erkannt" if beleg.lieferant_id else "Lieferant fehlt",
    ]
    if beleg.objekt_id and beleg.kategorie_id:
        teile.append("Vorschlag aus früheren Belegen eingetragen")
    return "; ".join(teile) + ". Bitte Felder prüfen und bestätigen."


def _beleg_posten_anlegen(beleg: Beleg, *, lauf: Lauf | None = None) -> Arbeitsposten:
    return kern_services.arbeitsposten_anlegen(
        ART_BELEGE_ZUORDNEN,
        bezug=beleg,
        aktionen=AKTIONEN_BELEG,
        faellig_am=beleg.dokument.datum,
        hinweis=_beleg_hinweis(beleg),
        lauf=lauf,
    )


def beleg_anlegen(
    dokument: Dokument,
    *,
    lieferant: Lieferant | None = None,
    lauf: Lauf | None = None,
    posten: bool = True,
) -> Beleg:
    """Legt einen offenen Beleg zu einem Dokument an, mit Vorschlag aus früheren Belegen
    des Lieferanten. Mit ``posten`` entsteht ein Arbeitsposten „Belege zuordnen“."""
    if not dokument.dokumentart.beleg:
        raise BelegFehler(f"Dokument #{dokument.pk} ist kein Beleg ({dokument.art}).")
    beleg = Beleg(dokument=dokument, lieferant=lieferant, status=Beleg.Status.OFFEN)
    treffer = vorschlag(lieferant, dokument.datum)
    if treffer is not None:
        beleg.objekt = treffer.objekt
        beleg.kategorie = treffer.kategorie
        beleg.leistungsjahr = treffer.leistungsjahr
        beleg.umlagefaehiger_anteil = treffer.umlagefaehiger_anteil
    beleg.save()
    if posten:
        _beleg_posten_anlegen(beleg, lauf=lauf)
    return beleg


def _betrag_pruefen(betrag: Decimal | None, feld: str = "Betrag") -> Decimal:
    if betrag is None:
        raise BelegFehler(f"{feld} fehlt.")
    wert = Decimal(betrag)
    if wert <= 0:
        raise BelegFehler(f"{feld} muss positiv sein.")
    if wert != wert.quantize(Decimal("0.01")):
        raise BelegFehler(f"{feld} hat mehr als zwei Nachkommastellen.")
    return wert


def _zuordnung_pruefen(
    beleg: Beleg,
    objekt: Objekt | None,
    kategorie: Kategorie | None,
    leistungsjahr: int | None,
    umlagefaehiger_anteil: Decimal | None,
) -> Decimal:
    if objekt is None or kategorie is None or leistungsjahr is None:
        raise BelegFehler(f"Beleg #{beleg.pk}: Objekt, Kostenart und Leistungsjahr gehören dazu.")
    if not objekt.aktiv:
        raise BelegFehler(f"Objekt #{objekt.pk} ist nicht aktiv.")
    if not kategorie.aktiv:
        raise BelegFehler(f"Kategorie #{kategorie.pk} ist nicht aktiv.")
    if kategorie.art != Kategorie.Art.AUSGABE:
        raise BelegFehler(f"Kategorie #{kategorie.pk} ist keine Ausgabe; Belege sind Kosten.")
    if not 1990 <= leistungsjahr <= 2100:
        raise BelegFehler(f"Beleg #{beleg.pk}: Leistungsjahr unplausibel.")
    anteil = (
        kategorie.umlagefaehiger_anteil
        if umlagefaehiger_anteil is None
        else Decimal(umlagefaehiger_anteil).quantize(Decimal("0.01"))
    )
    if not Decimal("0") <= anteil <= Decimal("100"):
        raise BelegFehler("Der umlagefähige Anteil muss zwischen 0 und 100 liegen.")
    return anteil


@transaction.atomic
def beleg_bestaetigen(
    beleg: Beleg,
    *,
    lieferant: Lieferant | None,
    betrag: Decimal | None,
    objekt: Objekt | None,
    kategorie: Kategorie | None,
    leistungsjahr: int | None,
    umlagefaehiger_anteil: Decimal | None = None,
    datum: datetime.date | None = None,
    am: datetime.datetime | None = None,
    faellig_am: datetime.date | None = None,
    rechnungsnummer: str = "",
) -> Beleg:
    """Bestätigt einen Beleg mit allen Feldern (Standardablauf 6, Schritt 3).

    Ohne umlagefähigen Anteil gilt die Vorgabe der Kostenart. Ein anderes Datum ändert das
    Datum des Dokuments (mit Löschfrist und Dateiname). Der Posten „Belege zuordnen“ ist
    danach erledigt; das Dokument hängt am Lieferanten.

    Mit ``faellig_am`` (Schritt 3b) entsteht dazu die Eingangsrechnung mit Arbeitsposten
    „Rechnung zahlen“ und Frist (``eingangsrechnung_anlegen``), solange der Beleg noch an
    keiner Buchung hängt.
    """
    beleg = Beleg.objects.select_for_update().select_related("dokument").get(pk=beleg.pk)
    if beleg.status == Beleg.Status.VERWORFEN:
        raise BelegFehler(f"Beleg #{beleg.pk} ist verworfen.")
    if lieferant is None:
        raise BelegFehler(f"Beleg #{beleg.pk}: Der Lieferant fehlt.")
    wert = _betrag_pruefen(betrag)
    anteil = _zuordnung_pruefen(beleg, objekt, kategorie, leistungsjahr, umlagefaehiger_anteil)
    if datum is not None and datum != beleg.dokument.datum:
        dokument_aendern(beleg.dokument, datum=datum)
    beleg.lieferant = lieferant
    beleg.betrag = wert
    beleg.objekt = objekt
    beleg.kategorie = kategorie
    beleg.leistungsjahr = leistungsjahr
    beleg.umlagefaehiger_anteil = anteil
    beleg.status = Beleg.Status.BESTAETIGT
    beleg.bestaetigt_am = beleg.bestaetigt_am or am or timezone.now()
    beleg.full_clean()
    beleg.save()
    _beleg_posten_schliessen(beleg)
    dokument_bezug_setzen(beleg.dokument, lieferant)
    logger.info("Beleg #%s bestätigt", beleg.pk)
    if faellig_am is not None:
        vorhanden = Eingangsrechnung.objects.filter(beleg__dokument=beleg.dokument_id).first()
        if vorhanden is not None and vorhanden.status == Eingangsrechnung.Status.BEZAHLT:
            return beleg  # schon bezahlt: Fälligkeit und Nummer bleiben, wie sie waren
        if vorhanden is None and beleg.buchung_id is not None:
            raise RechnungFehler(f"Beleg #{beleg.pk} hängt schon an einer Buchung (bezahlt).")
        eingangsrechnung_anlegen(
            vorhanden.beleg if vorhanden is not None else beleg,
            faellig_am=faellig_am,
            rechnungsnummer=rechnungsnummer,
            betrag=vorhanden.betrag if vorhanden is not None else None,
        )
    return beleg


@dataclass(frozen=True)
class Belegteil:
    """Ein Teil einer Sammelrechnung (H6.4). Ohne Anteil gilt die Vorgabe der Kostenart."""

    betrag: Decimal
    objekt: Objekt
    kategorie: Kategorie
    leistungsjahr: int
    umlagefaehiger_anteil: Decimal | None = None


@transaction.atomic
def beleg_aufteilen(
    beleg: Beleg, teile: Sequence[Belegteil], *, am: datetime.datetime | None = None
) -> list[Beleg]:
    """Teilt eine Sammelrechnung auf (H6.4): ein Beleg je Teil, alle auf demselben Dokument.

    Der Beleg braucht Lieferant und Gesamtbetrag; die Teile ergeben genau diesen Betrag. Der
    erste Teil bleibt der Beleg selbst, die übrigen entstehen neu; alle sind danach bestätigt.
    Eine schon zugeordnete Buchung bleibt am ersten Teil.
    """
    beleg = Beleg.objects.select_for_update().select_related("dokument").get(pk=beleg.pk)
    if beleg.status == Beleg.Status.VERWORFEN:
        raise BelegFehler(f"Beleg #{beleg.pk} ist verworfen.")
    if beleg.lieferant is None:
        raise BelegFehler(f"Beleg #{beleg.pk}: Der Lieferant fehlt.")
    gesamt = _betrag_pruefen(beleg.betrag, "Der Gesamtbetrag")
    if len(teile) < 2:
        raise BelegFehler("Eine Aufteilung braucht mindestens zwei Teile.")
    summe = sum((_betrag_pruefen(t.betrag) for t in teile), Decimal("0"))
    if summe != gesamt:
        raise BelegFehler(f"Beleg #{beleg.pk}: Die Teile ergeben nicht den Gesamtbetrag.")
    ergebnis = []
    for nummer, teil in enumerate(teile):
        ziel = beleg
        if nummer > 0:
            ziel = Beleg(dokument=beleg.dokument, lieferant=beleg.lieferant)
            ziel.save()
        ergebnis.append(
            beleg_bestaetigen(
                ziel,
                lieferant=beleg.lieferant,
                betrag=teil.betrag,
                objekt=teil.objekt,
                kategorie=teil.kategorie,
                leistungsjahr=teil.leistungsjahr,
                umlagefaehiger_anteil=teil.umlagefaehiger_anteil,
                am=am,
            )
        )
    return ergebnis


@transaction.atomic
def beleg_verwerfen(beleg: Beleg) -> Beleg:
    """Verwirft einen Beleg, der keiner ist (Stornieren statt Löschen). Das Dokument bleibt."""
    beleg = Beleg.objects.select_for_update().get(pk=beleg.pk)
    if beleg.buchung_id is not None:
        raise BelegFehler(f"Beleg #{beleg.pk} hängt an Buchung #{beleg.buchung_id}.")
    beleg.status = Beleg.Status.VERWORFEN
    beleg.save(update_fields=["status"])
    _beleg_posten_schliessen(beleg)
    return beleg


@transaction.atomic
def lieferantendokument_ablegen(
    inhalt: bytes,
    *,
    art: str,
    lieferant: Lieferant,
    datum: datetime.date,
    herkunft: str = Dokument.Herkunft.UPLOAD,
    bemerkung: str = "",
) -> Ablage:
    """Mahnung, Rücklastschrift oder Vollstreckung am Lieferanten ablegen, ohne Buchung (H6.8)."""
    if art not in dokumentarten.AM_LIEFERANTEN:
        raise AblageFehler(f"{art} ist keine Dokumentart am Lieferanten.")
    ablage = dokument_ablegen(inhalt, art=art, datum=datum, herkunft=herkunft, bemerkung=bemerkung)
    dokument_bezug_setzen(ablage.dokument, lieferant)
    return ablage


@transaction.atomic
def beleg_umwidmen(beleg: Beleg, *, art: str, lieferant: Lieferant) -> Dokument:
    """Ein Eingang aus dem Postfach ist kein Beleg, sondern etwa eine Mahnung (H6.8): Der
    Beleg wird verworfen, das Dokument bekommt die neue Art und hängt am Lieferanten."""
    if art not in dokumentarten.AM_LIEFERANTEN:
        raise AblageFehler(f"{art} ist keine Dokumentart am Lieferanten.")
    beleg = Beleg.objects.select_for_update().select_related("dokument").get(pk=beleg.pk)
    if not beleg.ist_offen:
        raise BelegFehler(f"Beleg #{beleg.pk} ist {beleg.get_status_display()}, nicht offen.")
    andere = beleg.dokument.belege.exclude(pk=beleg.pk).exclude(status=Beleg.Status.VERWORFEN)
    if andere.exists():
        raise BelegFehler(f"Dokument #{beleg.dokument_id} hat weitere Belege.")
    beleg_verwerfen(beleg)
    dokument = dokument_aendern(beleg.dokument, art=art)
    dokument_bezug_setzen(dokument, lieferant)
    return dokument


@dataclass(frozen=True)
class Hochgeladen:
    dokument: Dokument
    beleg: Beleg | None
    neu: bool


@transaction.atomic
def beleg_hochladen(
    inhalt: bytes,
    *,
    datum: datetime.date,
    lieferant: Lieferant | None = None,
    betrag: Decimal | None = None,
    objekt: Objekt | None = None,
    kategorie: Kategorie | None = None,
    leistungsjahr: int | None = None,
    umlagefaehiger_anteil: Decimal | None = None,
    bemerkung: str = "",
    faellig_am: datetime.date | None = None,
    rechnungsnummer: str = "",
) -> Hochgeladen:
    """Beleg vom Handy (H6.2): Foto oder PDF, die Felder von Hand (keine Texterkennung).

    Leere Felder füllt der Vorschlag aus früheren Belegen des Lieferanten. Sind danach alle
    Felder da, ist der Beleg bestätigt (mit ``faellig_am`` auch als Eingangsrechnung); sonst
    bleibt er offen mit Arbeitsposten „Belege zuordnen“. Eine Dublette legt nichts Neues an.
    """
    ablage = dokument_ablegen(
        inhalt,
        art=dokumentarten.BELEG,
        datum=datum,
        herkunft=Dokument.Herkunft.UPLOAD,
        bemerkung=bemerkung,
    )
    if not ablage.neu:
        vorhandener = ablage.dokument.belege.order_by("pk").first()
        return Hochgeladen(dokument=ablage.dokument, beleg=vorhandener, neu=False)
    beleg = beleg_anlegen(ablage.dokument, lieferant=lieferant, posten=False)
    if umlagefaehiger_anteil is None and kategorie in (None, beleg.kategorie):
        umlagefaehiger_anteil = beleg.umlagefaehiger_anteil
    objekt = objekt or beleg.objekt
    kategorie = kategorie or beleg.kategorie
    leistungsjahr = leistungsjahr or beleg.leistungsjahr
    if lieferant is not None and betrag is not None and objekt and kategorie and leistungsjahr:
        beleg = beleg_bestaetigen(
            beleg,
            lieferant=lieferant,
            betrag=betrag,
            objekt=objekt,
            kategorie=kategorie,
            leistungsjahr=leistungsjahr,
            umlagefaehiger_anteil=umlagefaehiger_anteil,
            faellig_am=faellig_am,
            rechnungsnummer=rechnungsnummer,
        )
    elif faellig_am is not None:
        raise RechnungFehler("Mit Fälligkeit braucht der Beleg alle Felder.")
    else:
        beleg.betrag = betrag
        beleg.objekt = objekt
        beleg.kategorie = kategorie
        beleg.leistungsjahr = leistungsjahr
        beleg.umlagefaehiger_anteil = umlagefaehiger_anteil
        beleg.full_clean()
        beleg.save()
        _beleg_posten_anlegen(beleg)
    return Hochgeladen(dokument=ablage.dokument, beleg=beleg, neu=True)


# --- Beleg und Buchung (H6.4) --------------------------------------------------------


def _belegte_umsaetze() -> models.QuerySet[Beleg, Any]:
    return Beleg.objects.filter(status=Beleg.Status.BESTAETIGT, buchung__isnull=False).values(
        "buchung__umsatz_id"
    )


def buchung_kandidaten(beleg: Beleg) -> list[Buchung]:
    """Gültige Ausgaben mit dem Betrag des Belegs um das Belegdatum, ohne eigenen Beleg."""
    if beleg.betrag is None:
        return []
    datum = beleg.dokument.datum
    spanne = datetime.timedelta(days=KANDIDATEN_TAGE)
    buchungen = (
        kassenbuch_services.gueltige_buchungen()
        .filter(
            richtung=Bankumsatz.Richtung.AUSGANG,
            betrag=beleg.betrag,
            datum__gte=datum - spanne,
            datum__lte=datum + spanne,
        )
        .exclude(umsatz_id__in=_belegte_umsaetze())
        .select_related("kategorie", "objekt", "umsatz")
    )
    return sorted(buchungen, key=lambda b: (abs((b.datum - datum).days), b.pk))


@transaction.atomic
def beleg_an_buchung(beleg: Beleg, buchung: Buchung) -> Beleg:
    """Hängt einen bestätigten Beleg an seine Buchung (H6.4).

    Die Buchung selbst bleibt unverändert (Gebuchtes ändert sich nie); der Beleg zeigt auf
    sie, und das Dokument hängt zusätzlich über einen Dokumentbezug an der Buchung.
    """
    beleg = Beleg.objects.select_for_update().select_related("dokument").get(pk=beleg.pk)
    if beleg.status != Beleg.Status.BESTAETIGT:
        raise BelegFehler(f"Beleg #{beleg.pk} ist nicht bestätigt.")
    if buchung.ist_storno or Buchung.objects.filter(storno_von=buchung).exists():
        raise BelegFehler(f"Buchung #{buchung.pk} ist storniert oder ein Storno.")
    if buchung.richtung != Bankumsatz.Richtung.AUSGANG:
        raise BelegFehler(f"Buchung #{buchung.pk} ist keine Ausgabe.")
    beleg.buchung = buchung
    beleg.save(update_fields=["buchung"])
    dokument_bezug_setzen(beleg.dokument, buchung)
    logger.info("Beleg #%s an Buchung #%s", beleg.pk, buchung.pk)
    return beleg


# --- Belegpflicht im Monatsabschluss (H6.6) ------------------------------------------


def belegpflicht() -> Mapping[str, Any]:
    """Die Regel aus ``DOKUMENTE["BELEGPFLICHT"]``: Arten, Mindestbetrag, Ausnahmen."""
    werte: Mapping[str, Any] = settings.DOKUMENTE["BELEGPFLICHT"]
    return werte


def belegpflichtige(buchungen: models.QuerySet[Buchung]) -> models.QuerySet[Buchung]:
    """Die Ausgaben unter ``buchungen``, die nach der Belegpflicht einen Beleg brauchen."""
    regel = belegpflicht()
    ohne = {str(k).strip().casefold() for k in regel.get("OHNE_KATEGORIEN", []) if str(k).strip()}
    auswahl = buchungen.filter(
        richtung=Bankumsatz.Richtung.AUSGANG,
        kategorie__art__in=list(regel.get("ARTEN", [Kategorie.Art.AUSGABE])),
        betrag__gte=Decimal(str(regel.get("AB_BETRAG", "0"))),
    )
    if ohne:
        ausgenommen = [
            k.pk for k in Kategorie.objects.all() if k.bezeichnung.strip().casefold() in ohne
        ]
        auswahl = auswahl.exclude(kategorie_id__in=ausgenommen)
    return auswahl


def buchungen_ohne_beleg(buchungen: models.QuerySet[Buchung]) -> list[int]:
    """IDs der belegpflichtigen Buchungen ohne Beleg.

    Belegt ist eine Buchung, wenn ein bestätigter Beleg an einer Buchung desselben Umsatzes
    hängt (auch nach Storno und Neubuchung oder bei einer aufgeteilten Buchung) oder ein
    Dokument über einen Dokumentbezug an ihr hängt.
    """
    mit_dokument = Dokumentbezug.objects.filter(
        bezug_typ=ContentType.objects.get_for_model(Buchung)
    ).values("bezug_id")
    return list(
        belegpflichtige(buchungen)
        .exclude(umsatz_id__in=_belegte_umsaetze())
        .exclude(pk__in=mit_dokument)
        .order_by("pk")
        .values_list("pk", flat=True)
    )


# --- Rechnungspostfach (Lauf belege_postfach, H6.1) ----------------------------------

#: Arbeitsposten für eine Mail im Rechnungspostfach ohne lesbares PDF.
ART_MAIL_OHNE_PDF = "Rechnungsmail ohne PDF"


@dataclass
class PostfachErgebnis:
    """Was ``belege_aus_postfach`` getan hat: nur Zähler und IDs."""

    mails: int = 0
    belege: list[Beleg] = field(default_factory=list)
    dubletten: int = 0
    ohne_pdf: int = 0
    arbeitsposten: int = 0
    #: UIDs der Mails, die danach als gelesen markiert oder verschoben werden
    erledigt: list[str] = field(default_factory=list)


def belege_aus_postfach(
    mails: Iterable[postfach.Postfachmail], *, lauf: Lauf | None = None
) -> PostfachErgebnis:
    """Legt jede PDF-Anlage als Dokument der Art „Beleg“ ab (H6.1, Standardablauf 6).

    - Dubletten (gleiche SHA-256) legen nichts Neues an.
    - Jeder neue Beleg bekommt einen Arbeitsposten „Belege zuordnen“; ist der Absender als
      Lieferant bekannt, stehen Lieferant und der Vorschlag aus früheren Belegen schon drin.
    - Eine Mail ohne lesbares PDF wird ein Arbeitsposten „Rechnungsmail ohne PDF“.

    Jede verarbeitete Mail steht in ``erledigt``; markiert oder verschoben wird sie erst,
    wenn die Ablage gespeichert ist (der Lauf macht das nach seiner Transaktion).
    """
    ergebnis = PostfachErgebnis()
    for mail in mails:
        ergebnis.mails += 1
        rechnung = postfach.lesen(mail)
        lieferant = lieferant_zur_mail(rechnung.absender)
        abgelegt = 0
        for anlage in rechnung.anlagen:
            try:
                ablage = dokument_ablegen(
                    anlage.inhalt,
                    art=dokumentarten.BELEG,
                    datum=rechnung.datum,
                    herkunft=Dokument.Herkunft.POSTFACH,
                    eingangskennung=rechnung.message_id,
                )
            except umwandeln.DateiUngueltig:
                logger.warning("Postfach: Anlage der Mail %s nicht lesbar", mail.uid)
                continue
            abgelegt += 1
            if not ablage.neu:
                ergebnis.dubletten += 1
                continue
            ergebnis.belege.append(beleg_anlegen(ablage.dokument, lieferant=lieferant, lauf=lauf))
            ergebnis.arbeitsposten += 1
        if not abgelegt:
            ergebnis.ohne_pdf += 1
            kern_services.arbeitsposten_anlegen(
                ART_MAIL_OHNE_PDF,
                faellig_am=rechnung.datum,
                hinweis=(
                    f"Mail vom {rechnung.datum:%d.%m.%Y} im Rechnungspostfach ohne lesbares PDF. "
                    "Bitte im Postfach nachsehen und den Beleg hochladen."
                ),
                lauf=lauf,
            )
            ergebnis.arbeitsposten += 1
        ergebnis.erledigt.append(mail.uid)
    return ergebnis


# =====================================================================================
# Eingangsrechnungen (Schritt 3b, H6.1, Standardablauf 6)
# =====================================================================================

#: Arbeitsposten je offener Rechnung; meldet sich über die Benachrichtigung und als Aufgabe
#: im Hof-Backlog (``kern.aufgaben.melden``).
ART_RECHNUNG_ZAHLEN = "Rechnung zahlen"
AKTIONEN_RECHNUNG = ("rechnung_ansehen", "zurueckstellen")
#: Unsicherer Treffer im Kassenbuch (etwa zwei Rechnungen mit gleichem Betrag): nie automatisch.
ART_RECHNUNG_ZUORDNEN = "Rechnung zuordnen"
AKTIONEN_RECHNUNG_ZUORDNEN = ("rechnung_ansehen", "zurueckstellen")
#: Erinnerung über den Lauf ``fristen``: so viele Tage vor der Fälligkeit (Arbeitsposten
#: „Frist fällig“, gemeldet von der Benachrichtigung).
ERINNERUNG_TAGE = 3
#: So viele Tage vor dem Belegdatum kann eine Abbuchung frühestens liegen.
ABBUCHUNG_VORLAUF_TAGE = 7
UEBERFAELLIG = "Überfällig"


class RechnungFehler(BelegFehler):
    pass


def _rechnung_posten(rechnung: Eingangsrechnung, art: str) -> list[Arbeitsposten]:
    return list(
        Arbeitsposten.objects.filter(
            art=art,
            bezug_typ=ContentType.objects.get_for_model(Eingangsrechnung),
            bezug_id=rechnung.pk,
            status__in=[Arbeitsposten.Status.OFFEN, Arbeitsposten.Status.ZURUECKGESTELLT],
        )
    )


def _rechnung_hinweis(rechnung: Eingangsrechnung) -> str:
    """Hinweis am Posten „Rechnung zahlen“: nur Datum und Status, keine Namen, keine Beträge."""
    text = (
        f"Rechnung fällig am {rechnung.faellig_am:%d.%m.%Y}. Zahlung in Vivid freigeben; "
        "der Link führt zum Beleg."
    )
    if rechnung.ueberfaellig_seit is not None:
        text = f"{UEBERFAELLIG} seit {rechnung.ueberfaellig_seit:%d.%m.%Y}. " + text
    return text


def normale_nummer(text: str) -> str:
    """Rechnungsnummer zum Vergleichen: ohne Leerraum, ohne Groß und Klein."""
    return re.sub(r"\s+", "", text or "").casefold()


@transaction.atomic
def eingangsrechnung_anlegen(
    beleg: Beleg,
    *,
    faellig_am: datetime.date,
    rechnungsnummer: str = "",
    betrag: Decimal | None = None,
    lauf: Lauf | None = None,
) -> Eingangsrechnung:
    """Macht aus einem bestätigten Beleg einen offenen Posten (Standardablauf 6, Schritt 4).

    Es entstehen die Eingangsrechnung, ein Arbeitsposten „Rechnung zahlen“ (gemeldet an das
    Hof-Backlog) und eine Frist (Zahlungsziel, drei Tage Vorlauf: die Erinnerung). Ohne Betrag
    gilt der Betrag des Belegs. Gibt es zum Beleg schon eine offene Rechnung, werden
    Fälligkeit und Nummer angeglichen; die Frist wandert mit.
    """
    beleg = Beleg.objects.select_for_update().select_related("dokument").get(pk=beleg.pk)
    if beleg.status != Beleg.Status.BESTAETIGT:
        raise RechnungFehler(f"Beleg #{beleg.pk} ist nicht bestätigt.")
    wert = _betrag_pruefen(betrag if betrag is not None else beleg.betrag)
    rechnung = Eingangsrechnung.objects.filter(beleg=beleg).first()
    if rechnung is not None and rechnung.status == Eingangsrechnung.Status.BEZAHLT:
        raise RechnungFehler(f"Eingangsrechnung #{rechnung.pk} ist schon bezahlt.")
    neu = rechnung is None
    if rechnung is None:
        rechnung = Eingangsrechnung(beleg=beleg)
    rechnung.betrag = wert
    rechnung.faellig_am = faellig_am
    rechnung.rechnungsnummer = rechnungsnummer.strip()[:100]
    rechnung.full_clean()
    rechnung.save()
    fristen_services.frist_anlegen(
        Frist.Art.ZAHLUNGSZIEL,
        faellig_am,
        Frist.Quelle.EINGANGSRECHNUNG,
        rechnung,
        hinweis=f"Eingangsrechnung, fällig am {faellig_am:%d.%m.%Y}",
        vorlauf_tage=ERINNERUNG_TAGE,
        datum_angleichen=True,
    )
    if neu or not _rechnung_posten(rechnung, ART_RECHNUNG_ZAHLEN):
        kern_services.arbeitsposten_anlegen(
            ART_RECHNUNG_ZAHLEN,
            bezug=rechnung,
            aktionen=AKTIONEN_RECHNUNG,
            faellig_am=faellig_am,
            hinweis=_rechnung_hinweis(rechnung),
            lauf=lauf,
        )
        # Gemeldet ans Hof-Backlog wird zentral in kern.services (aufgaben.melden).
    logger.info("Eingangsrechnung #%s angelegt (Beleg #%s)", rechnung.pk, beleg.pk)
    return rechnung


def _posten_schliessen(rechnung: Eingangsrechnung) -> None:
    for art in (ART_RECHNUNG_ZAHLEN, ART_RECHNUNG_ZUORDNEN):
        for posten in _rechnung_posten(rechnung, art):
            kern_services.arbeitsposten_erledigen(posten)


def _buchung_fuer(beleg: Beleg, buchungen: Sequence[Buchung]) -> Buchung:
    """Die Buchung eines Umsatzes, an die ein Teil der Rechnung gehört: gleiches Objekt und
    gleicher Betrag, sonst gleiches Objekt, sonst die erste."""
    for buchung in buchungen:
        if buchung.objekt_id == beleg.objekt_id and buchung.betrag == beleg.betrag:
            return buchung
    for buchung in buchungen:
        if buchung.objekt_id == beleg.objekt_id:
            return buchung
    return buchungen[0]


@transaction.atomic
def rechnung_bezahlt(
    rechnung: Eingangsrechnung, umsatz: Bankumsatz, *, lauf: Lauf | None = None
) -> Eingangsrechnung:
    """Verbindet Rechnung und Abbuchung (Standardablauf 6, Schritt 6).

    Die Rechnung ist danach bezahlt durch die (erste) gültige Buchung des Umsatzes; Posten
    „Rechnung zahlen“ und „Rechnung zuordnen“ sowie die Frist sind erledigt, und jeder Beleg
    der Rechnung (bei einer Sammelrechnung jeder Teil) hängt an seiner Buchung.
    """
    rechnung = (
        Eingangsrechnung.objects.select_for_update()
        .select_related("beleg__dokument")
        .get(pk=rechnung.pk)
    )
    if rechnung.status == Eingangsrechnung.Status.BEZAHLT:
        raise RechnungFehler(f"Eingangsrechnung #{rechnung.pk} ist schon bezahlt.")
    if umsatz.richtung != Bankumsatz.Richtung.AUSGANG:
        raise RechnungFehler(f"Umsatz #{umsatz.pk} ist keine Abbuchung.")
    if Eingangsrechnung.objects.filter(bezahlt_durch__umsatz=umsatz).exists():
        raise RechnungFehler(f"Umsatz #{umsatz.pk} bezahlt schon eine andere Rechnung.")
    buchungen = list(kassenbuch_services.gueltige_buchungen().filter(umsatz=umsatz).order_by("pk"))
    if not buchungen:
        raise RechnungFehler(f"Umsatz #{umsatz.pk} ist noch nicht gebucht.")
    rechnung.status = Eingangsrechnung.Status.BEZAHLT
    rechnung.bezahlt_durch = buchungen[0]
    rechnung.bezahlt_am = umsatz.buchungsdatum
    rechnung.save()
    _posten_schliessen(rechnung)
    for frist in fristen_services.fristen_zu(rechnung, nur_offen=True):
        fristen_services.frist_erledigen(frist)
    teile = rechnung.beleg.dokument.belege.filter(status=Beleg.Status.BESTAETIGT, buchung=None)
    for teil in teile.order_by("pk"):
        beleg_an_buchung(teil, _buchung_fuer(teil, buchungen))
    logger.info("Eingangsrechnung #%s bezahlt durch Umsatz #%s", rechnung.pk, umsatz.pk)
    return rechnung


def rechnung_strittig(rechnung: Eingangsrechnung, strittig: bool = True) -> Eingangsrechnung:
    """Markiert eine offene Rechnung als strittig (wird nie automatisch verbunden) oder
    wieder als offen."""
    if rechnung.status == Eingangsrechnung.Status.BEZAHLT:
        raise RechnungFehler(f"Eingangsrechnung #{rechnung.pk} ist schon bezahlt.")
    rechnung.status = (
        Eingangsrechnung.Status.STRITTIG if strittig else Eingangsrechnung.Status.OFFEN
    )
    rechnung.save(update_fields=["status"])
    return rechnung


# --- Abgleich im Lauf kassenbuch_einordnen -------------------------------------------


@dataclass(frozen=True)
class Treffer:
    rechnung: Eingangsrechnung
    umsatz: Bankumsatz
    #: Die Rechnungsnummer steht im Verwendungszweck (sonst passt nur die Gegen-IBAN).
    nummer: bool


def _kandidaten_umsaetze(rechnungen: Sequence[Eingangsrechnung]) -> list[Bankumsatz]:
    if not rechnungen:
        return []
    frueheste = min(r.beleg.dokument.datum for r in rechnungen) - datetime.timedelta(
        days=ABBUCHUNG_VORLAUF_TAGE
    )
    schon_bezahlt = Eingangsrechnung.objects.filter(bezahlt_durch__isnull=False).values(
        "bezahlt_durch__umsatz_id"
    )
    gebucht = kassenbuch_services.gueltige_buchungen().values("umsatz_id")
    return list(
        Bankumsatz.objects.filter(
            richtung=Bankumsatz.Richtung.AUSGANG,
            betrag__in={r.betrag for r in rechnungen},
            buchungsdatum__gte=frueheste,
            pk__in=gebucht,
        )
        .exclude(pk__in=schon_bezahlt)
        .order_by("buchungsdatum", "pk")
    )


def treffer(rechnungen: Sequence[Eingangsrechnung] | None = None) -> list[Treffer]:
    """Alle Paare aus offener Rechnung und gebuchter Abbuchung mit gleichem Betrag, bei denen
    die Gegen-IBAN die des Lieferanten ist oder die Rechnungsnummer im Zweck steht."""
    if rechnungen is None:
        rechnungen = list(
            Eingangsrechnung.objects.filter(status=Eingangsrechnung.Status.OFFEN).select_related(
                "beleg__dokument", "beleg__lieferant"
            )
        )
    ergebnis = []
    for umsatz in _kandidaten_umsaetze(rechnungen):
        zweck = normale_nummer(umsatz.verwendungszweck)
        iban = fingerabdruck.iban_normal(umsatz.gegen_iban)
        for rechnung in rechnungen:
            if rechnung.betrag != umsatz.betrag:
                continue
            if umsatz.buchungsdatum < rechnung.beleg.dokument.datum - datetime.timedelta(
                days=ABBUCHUNG_VORLAUF_TAGE
            ):
                continue
            nummer = normale_nummer(rechnung.rechnungsnummer)
            mit_nummer = bool(nummer) and nummer in zweck
            lieferant = rechnung.beleg.lieferant
            mit_iban = bool(iban) and lieferant is not None and lieferant.iban == iban
            if mit_nummer or mit_iban:
                ergebnis.append(Treffer(rechnung=rechnung, umsatz=umsatz, nummer=mit_nummer))
    return ergebnis


def _eindeutig(alle: list[Treffer]) -> tuple[list[Treffer], dict[int, list[Treffer]]]:
    """(eindeutige Treffer, unsichere Treffer je Rechnung).

    Eindeutig ist ein Paar, wenn die Rechnung genau eine Abbuchung hat und die Abbuchung genau
    zu dieser Rechnung passt. Steht die Rechnungsnummer im Zweck, zählen zuerst nur diese
    Treffer (die Nummer sticht die IBAN).
    """
    je_rechnung: dict[int, list[Treffer]] = {}
    je_umsatz: dict[int, list[Treffer]] = {}
    for t in alle:
        je_rechnung.setdefault(t.rechnung.pk, []).append(t)
        je_umsatz.setdefault(t.umsatz.pk, []).append(t)

    def beste(liste: list[Treffer]) -> list[Treffer]:
        mit_nummer = [t for t in liste if t.nummer]
        return mit_nummer or liste

    sicher, unsicher = [], {}
    for rechnung_id, liste in je_rechnung.items():
        auswahl = beste(liste)
        if len(auswahl) == 1:
            kandidat = auswahl[0]
            gegen = beste(je_umsatz[kandidat.umsatz.pk])
            if [t.rechnung.pk for t in gegen] == [rechnung_id]:
                sicher.append(kandidat)
                continue
        unsicher[rechnung_id] = liste
    return sicher, unsicher


def _unsicher_hinweis(liste: list[Treffer], alle: list[Treffer]) -> str:
    umsaetze = sorted({t.umsatz.pk for t in liste})
    andere = sorted(
        {t.rechnung.pk for t in alle if t.umsatz.pk in umsaetze} - {liste[0].rechnung.pk}
    )
    text = "Mögliche Abbuchungen: " + ", ".join(f"Umsatz #{u}" for u in umsaetze) + "."
    if andere:
        text += " Sie passen auch zu " + ", ".join(f"Eingangsrechnung #{r}" for r in andere) + "."
    return text + " Bitte auf der Seite der Rechnung die richtige Abbuchung wählen."


def rechnungen_abgleichen(lauf: Lauf | None = None) -> kassenbuch_services.Abgleich:
    """Erkennt die Abbuchungen offener Eingangsrechnungen (Standardablauf 6, Schritt 6).

    Eindeutige Treffer werden verbunden (``rechnung_bezahlt``). Unsichere Treffer, etwa zwei
    Rechnungen mit gleichem Betrag beim selben Lieferanten, werden je Rechnung ein
    Arbeitsposten „Rechnung zuordnen“, nie eine Verbindung.
    """
    alle = treffer()
    sicher, unsicher = _eindeutig(alle)
    verbunden = arbeitsposten = fehler = 0
    for t in sicher:
        try:
            with transaction.atomic():
                rechnung_bezahlt(t.rechnung, t.umsatz, lauf=lauf)
        except RechnungFehler:
            logger.warning("Eingangsrechnung #%s: Verbinden gescheitert", t.rechnung.pk)
            fehler += 1
            continue
        verbunden += 1
    for liste in unsicher.values():
        rechnung = liste[0].rechnung
        if _rechnung_posten(rechnung, ART_RECHNUNG_ZUORDNEN):
            continue
        kern_services.arbeitsposten_anlegen(
            ART_RECHNUNG_ZUORDNEN,
            bezug=rechnung,
            aktionen=AKTIONEN_RECHNUNG_ZUORDNEN,
            faellig_am=timezone.localdate(),
            hinweis=_unsicher_hinweis(liste, alle),
            lauf=lauf,
        )
        arbeitsposten += 1
    return kassenbuch_services.Abgleich(
        name="Eingangsrechnungen",
        verbunden=verbunden,
        arbeitsposten=arbeitsposten,
        fehler=fehler,
    )


def abbuchung_kandidaten(rechnung: Eingangsrechnung) -> list[Bankumsatz]:
    """Für die Seite der Rechnung: gebuchte Abbuchungen mit gleichem Betrag ab dem Belegdatum
    (ohne IBAN oder Nummer), die noch keine Rechnung bezahlen."""
    return _kandidaten_umsaetze([rechnung])


# --- Überfällig (Lauf fristen) -------------------------------------------------------


def rechnungen_ueberfaellig_markieren(stichtag: datetime.date, lauf: Lauf | None = None) -> int:
    """Nach Ablauf der Fälligkeit ist der Posten „Rechnung zahlen“ überfällig markiert
    (Hinweis beginnt mit „Überfällig seit …“). Läuft im Lauf ``fristen``; je Rechnung einmal."""
    anzahl = 0
    faellige = Eingangsrechnung.objects.filter(
        status=Eingangsrechnung.Status.OFFEN, faellig_am__lt=stichtag, ueberfaellig_seit=None
    ).order_by("faellig_am", "pk")
    for rechnung in faellige:
        rechnung.ueberfaellig_seit = stichtag
        rechnung.save(update_fields=["ueberfaellig_seit"])
        for posten in _rechnung_posten(rechnung, ART_RECHNUNG_ZAHLEN):
            kern_services.arbeitsposten_hinweis_setzen(posten, _rechnung_hinweis(rechnung))
        anzahl += 1
    return anzahl
