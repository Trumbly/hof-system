"""„Umsätze einordnen“ im Posteingang der Arbeitslisten (Review 2, B2 und B3; Review 1, 5).

In der Zeile stehen Umsatz, Vorschlag und Knöpfe: Kategorie (nach Richtung gefiltert) und
Objekt sind vorgewählt, „Als Regel merken“ ist angehakt, wenn die Gegenpartei schon vorkam.
Ein Klick auf „Übernehmen“ bucht. Posten mit derselben Ursache bilden eine Gruppe mit „für
alle anwenden“: gleiche Gegenpartei ohne Regel, oder dieselben gleichrangigen Regeln.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from typing import Any

from django.urls import reverse

from apps.bank.models import Bankumsatz
from apps.kassenbuch import einordnen, services
from apps.kassenbuch.models import Kategorie, Zuordnungsregel
from apps.kern import posteingang
from apps.kern.models import Arbeitsposten, Benutzer, Objekt
from apps.kern.posteingang import Ergebnis, Gruppe, PostenFehler

#: Ab so vielen Posten mit derselben Ursache entsteht eine Gruppe.
GRUPPE_AB = 2


def _umsaetze(posten: Sequence[Arbeitsposten]) -> dict[int, Bankumsatz]:
    ids = [p.bezug_id for p in posten if p.bezug_id]
    return {u.pk: u for u in Bankumsatz.objects.filter(pk__in=ids).select_related("konto")}


def _auswahl(richtung: str, vorschlag: einordnen.Vorschlag) -> list[Kategorie]:
    return list(einordnen.kategorien_fuer(richtung, dazu=vorschlag.kategorie))


def zeilen_kontext(posten: Sequence[Arbeitsposten]) -> dict[int, dict[str, Any]]:
    umsaetze = _umsaetze(posten)
    vorschlaege = einordnen.vorschlaege(list(umsaetze.values()))
    objekte = list(einordnen.aktive_objekte())
    kategorien: dict[str, list[Kategorie]] = {}
    ergebnis: dict[int, dict[str, Any]] = {}
    for p in posten:
        umsatz = umsaetze.get(p.bezug_id or 0)
        if umsatz is None:
            continue
        vorschlag = vorschlaege[umsatz.pk]
        if vorschlag.kategorie is not None and vorschlag.kategorie.art not in (
            einordnen.ARTEN_JE_RICHTUNG.get(umsatz.richtung, ())
        ):
            auswahl = _auswahl(umsatz.richtung, vorschlag)
        else:
            if umsatz.richtung not in kategorien:
                kategorien[umsatz.richtung] = list(einordnen.kategorien_fuer(umsatz.richtung))
            auswahl = kategorien[umsatz.richtung]
        ergebnis[p.pk] = {
            "titel": umsatz.gegenpartei or "Umsatz ohne Gegenpartei",
            "umsatz": umsatz,
            "vorschlag": vorschlag,
            "kategorien": auswahl,
            "objekte": objekte,
            "kann_uebernehmen": vorschlag.vollstaendig,
            "aufteilen_url": reverse("kassenbuch:aufteilen", args=[umsatz.pk]),
        }
    return ergebnis


def _umsatz(posten: Arbeitsposten) -> Bankumsatz:
    umsatz = Bankumsatz.objects.select_related("konto").filter(pk=posten.bezug_id).first()
    if umsatz is None:
        raise PostenFehler(f"Den Umsatz zu Posten #{posten.pk} gibt es nicht mehr.")
    return umsatz


def _werte_lesen(
    werte: Mapping[str, str], vorschlag: einordnen.Vorschlag
) -> tuple[Kategorie, Objekt, bool]:
    """Kategorie, Objekt und „Als Regel merken“ aus der Zeile, sonst aus dem Vorschlag."""
    if posteingang.aus_zeile(werte):
        kategorie = Kategorie.objects.filter(pk=_zahl(werte.get("kategorie")), aktiv=True).first()
        objekt = Objekt.objects.filter(pk=_zahl(werte.get("objekt")), aktiv=True).first()
        regel_merken = werte.get("regel") == "1"
    else:
        kategorie, objekt, regel_merken = (
            vorschlag.kategorie,
            vorschlag.objekt,
            vorschlag.regel_merken,
        )
    if kategorie is None or objekt is None:
        raise PostenFehler("Bitte Kategorie und Objekt wählen; es gibt keinen Vorschlag.")
    return kategorie, objekt, regel_merken


def _zahl(text: str | None) -> int:
    return int(text) if text and text.isdigit() else 0


def _mandant(vorschlag: einordnen.Vorschlag, kategorie: Kategorie, objekt: Objekt) -> Any:
    """Der Mandant des Vorschlags, solange Kategorie und Objekt unverändert sind."""
    if vorschlag.kategorie == kategorie and vorschlag.objekt == objekt:
        return vorschlag.mandant
    return None


def meldung(
    uebernahme: services.Uebernahme,
    erster: Bankumsatz,
    kategorie: Kategorie,
    objekt: Objekt,
) -> Ergebnis:
    anzahl = uebernahme.umsaetze
    wer = f"Umsatz #{erster.pk}" if anzahl == 1 else f"{anzahl} Umsätze"
    text = f"{wer} gebucht: {kategorie.bezeichnung} · {objekt.kuerzel}."
    link = reverse("kassenbuch:umsatz", args=[erster.pk])
    link_text = "Umsatz ansehen" if anzahl == 1 else "ersten ansehen"
    weitere = anzahl > 1
    if uebernahme.regel is not None:
        regel = uebernahme.regel
        text += f" Regel #{regel.pk} angelegt."
        passend = len(services.regel_vorschau(regel).zu_buchen)
        link = reverse("kassenbuch:regel_anwenden", args=[regel.pk])
        if passend:
            text += f" {passend} weitere offene Umsätze passen darauf."
            link_text = "jetzt anwenden"
        else:
            link_text = "Regel ansehen"
    elif uebernahme.regel_fehler:
        text += f" Keine Regel angelegt: {uebernahme.regel_fehler}"
    return Ergebnis(text=text, link=link, link_text=link_text, weitere=weitere)


def uebernehmen(posten: Arbeitsposten, benutzer: Benutzer, werte: Mapping[str, str]) -> Ergebnis:
    umsatz = _umsatz(posten)
    vorschlag = einordnen.vorschlag(umsatz)
    kategorie, objekt, regel_merken = _werte_lesen(werte, vorschlag)
    try:
        uebernahme = services.einordnen_uebernehmen(
            [umsatz],
            kategorie=kategorie,
            objekt=objekt,
            mandant=_mandant(vorschlag, kategorie, objekt),
            regel_merken=regel_merken,
            regel=vorschlag.regel,
        )
    except services.KassenbuchFehler as fehler:
        raise PostenFehler(str(fehler)) from None
    return meldung(uebernahme, umsatz, kategorie, objekt)


# --- Gruppen (Review 1, Punkt 5) ----------------------------------------------------


def gruppen(posten: Sequence[Arbeitsposten], kontext: Mapping[int, dict[str, Any]]) -> list[Gruppe]:
    """Gleiche Ursache, eine Gruppe: dieselben gleichrangigen Regeln, oder dieselbe
    Gegenpartei in derselben Richtung ohne passende Regel."""
    je: dict[tuple[str, ...], list[Arbeitsposten]] = defaultdict(list)
    for p in posten:
        k = kontext.get(p.pk)
        if k is None:
            continue
        umsatz: Bankumsatz = k["umsatz"]
        vorschlag: einordnen.Vorschlag = k["vorschlag"]
        if vorschlag.gleichauf:
            je[("regeln", *(str(r.pk) for r in vorschlag.gleichauf))].append(p)
        elif vorschlag.regel is None:
            art, wert = einordnen.gegenpartei_schluessel(umsatz)
            if wert:
                je[("partei", art, wert, umsatz.richtung)].append(p)
    ergebnis = []
    for schluessel, liste in je.items():
        if len(liste) < GRUPPE_AB:
            continue
        erster = kontext[liste[0].pk]
        umsatz = erster["umsatz"]
        vorschlag = erster["vorschlag"]
        if schluessel[0] == "regeln":
            ids = " und ".join(f"#{i}" for i in schluessel[1:])
            titel = f"{len(liste)} Umsätze: Regeln {ids} passen mit gleicher Priorität"
            hinweis = (
                "Für alle anwenden bucht alle mit Kategorie und Objekt. Damit es nicht wieder "
                "vorkommt, eine der Regeln enger fassen oder ihre Priorität ändern."
            )
            regel_merken: bool | None = None  # keine neue Regel: es gibt schon zwei
        else:
            wer = umsatz.gegenpartei or umsatz.gegen_iban
            titel = f"{len(liste)} Umsätze von {wer} ohne Regel"
            hinweis = "Für alle anwenden bucht alle mit Kategorie und Objekt."
            regel_merken = True
        ergebnis.append(
            Gruppe(
                schluessel=":".join(schluessel),
                titel=titel,
                posten=liste,
                hinweis=hinweis,
                vorlage="kassenbuch/_einordnen_gruppe.html",
                kontext={
                    "vorschlag": vorschlag,
                    "kategorien": erster["kategorien"],
                    "objekte": erster["objekte"],
                    "regel_merken": regel_merken,
                    "regeln_url": reverse("kassenbuch:regeln") if schluessel[0] == "regeln" else "",
                },
            )
        )
    return ergebnis


def gruppe_anwenden(
    posten: Sequence[Arbeitsposten], benutzer: Benutzer, werte: Mapping[str, str]
) -> Ergebnis:
    """„Für alle anwenden“: alle Umsätze der Gruppe mit Kategorie und Objekt aus dem Kopf der
    Gruppe; mit „Als Regel merken“ zuerst eine Regel aus dem ersten Umsatz."""
    umsaetze = [_umsatz(p) for p in posten]
    if not umsaetze:
        raise PostenFehler("Die Gruppe ist leer.")
    vorschlag = einordnen.vorschlag(umsaetze[0])
    kategorie, objekt, regel_merken = _werte_lesen(werte, vorschlag)
    parteien = {einordnen.gegenpartei_schluessel(u) for u in umsaetze}
    if regel_merken and len(parteien) > 1:
        raise PostenFehler("Eine Regel lässt sich nur für Umsätze derselben Gegenpartei merken.")
    regel: Zuordnungsregel | None = vorschlag.regel
    try:
        uebernahme = services.einordnen_uebernehmen(
            umsaetze,
            kategorie=kategorie,
            objekt=objekt,
            mandant=_mandant(vorschlag, kategorie, objekt),
            regel_merken=regel_merken,
            regel=regel,
        )
    except services.KassenbuchFehler as fehler:
        raise PostenFehler(str(fehler)) from None
    ergebnis = meldung(uebernahme, umsaetze[0], kategorie, objekt)
    ergebnis.weitere = True
    return ergebnis


def _bearbeiten(posten: Arbeitsposten) -> str:
    return reverse("kassenbuch:zuordnen", args=[posten.bezug_id])


posteingang.registrieren(
    posteingang.Bearbeitung(
        art=services.ART_EINORDNEN,
        vorlage="kassenbuch/_einordnen_zeile.html",
        kontext=zeilen_kontext,
        uebernehmen=uebernehmen,
        gruppen=gruppen,
        gruppe_anwenden=gruppe_anwenden,
        bearbeiten=_bearbeiten,
        bearbeiten_als_dialog=True,
    )
)
