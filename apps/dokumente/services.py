"""Schreibweg der Dokumente: Vorlagen, Schreiben, Versand (Schritt 3c).

Jede Änderung an Vorlagen und Schreiben läuft über eine Funktion in diesem Modul
(CLAUDE.md, Regel 4.1).

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
from typing import Any

from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import UploadedFile
from django.core.mail import EmailMessage
from django.db import models, transaction
from django.db.models import Max
from django.utils import timezone
from django.utils.html import strip_tags

from apps.dokumente import letterxpress, pdf, platzhalter, vorlagenarten
from apps.dokumente.models import Schreiben, Vorlage
from apps.dokumente.platzhalter import PlatzhalterFehler
from apps.dokumente.versand import VersandFehler, mail_senden
from apps.dokumente.vorlagenarten import Versandweg
from apps.kern import services as kern_services
from apps.kern.models import Benutzer

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
