"""Seite Arbeitslisten als Posteingang (Review 2, B2; Review 1, Punkte 5 und 8).

- Links die Arten mit ihrer Zahl, rechts die Posten der gewählten Art (``?art=…``; ohne Art
  alle offenen, ``?status=zurueckgestellt`` die zurückgestellten). Am Handy erst die Arten,
  nach der Wahl die Liste.
- Jeder Posten lässt sich in der Zeile bearbeiten (``apps.kern.posteingang``): „Übernehmen“
  bucht den Vorschlag, die Aktionen des Postens stehen daneben, der Link führt zum Bezug.
- Mehrfachauswahl mit „für alle übernehmen“ (und den Aktionen, die alle gewählten anbieten).
  Posten mit derselben Ursache stehen als Gruppe mit „für alle anwenden“.
- Tastatur (``static/js/posteingang.js``): j/k wechselt den Posten, Enter übernimmt den
  Vorschlag, e öffnet die Bearbeitung, x wählt den Posten. Nach einer Aktion springt die
  Auswahl zum nächsten Posten; die Zeile verschwindet, die Seite springt nicht.
- Rückmeldungen sagen, was passiert ist, mit Link (Toast).

Gespeichert wird nur über die Aktionen und ``posteingang``-Funktionen der Apps (deren
Services). Die Zeilen zeigen Namen aus dem Bezug; die Hinweistexte bleiben ohne Namen.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, urlsplit

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Count
from django.http import HttpRequest, HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.http import urlencode
from django.views.decorators.http import require_POST

from apps.kern import aktionen, htmx, posteingang, verweise, vorgaenge
from apps.kern.models import Arbeitsposten, Benutzer
from apps.kern.posteingang import Ergebnis, Gruppe, PostenFehler

OFFEN = Arbeitsposten.Status.OFFEN
ZURUECKGESTELLT = Arbeitsposten.Status.ZURUECKGESTELLT
#: Ereignis, auf das die Liste neu lädt (mehrere Posten haben sich geändert).
EREIGNIS_NEU = "posteingangNeu"
#: Aktion „für alle übernehmen“ in der Mehrfachauswahl
UEBERNEHMEN = "uebernehmen"


# --- Zeilen und Gruppen -----------------------------------------------------------------


@dataclass
class Knopf:
    schluessel: str
    titel: str
    #: POST-Adresse einer Aktion, die sofort wirkt
    url: str = ""
    #: Adresse der Seite einer Aktion mit Eingabe (ein Link)
    seite: str = ""


@dataclass
class Zeile:
    posten: Arbeitsposten
    p: str
    titel: str
    verweis: verweise.Verweis | None
    knoepfe: list[Knopf]
    vorlage: str = ""
    kontext: dict[str, Any] = field(default_factory=dict)
    kann_uebernehmen: bool = False
    bearbeiten_url: str = ""
    bearbeiten_dialog: bool = False
    fehler: str = ""

    @property
    def ist_gruppe(self) -> bool:
        return False


@dataclass
class Gruppenzeile:
    gruppe: Gruppe
    zeilen: list[Zeile]

    @property
    def ist_gruppe(self) -> bool:
        return True


def _knoepfe(posten: Arbeitsposten) -> list[Knopf]:
    knoepfe = []
    for aktion in aktionen.fuer_posten(posten):
        if aktion.seite is not None:
            # Ein fehlender Bezug darf die Liste nicht sprengen: Knopf weglassen.
            try:
                ziel = aktion.seite(posten)
            except Exception:  # noqa: S112
                continue
            knoepfe.append(Knopf(aktion.schluessel, aktion.titel, seite=ziel))
        else:
            url = reverse("kern:arbeitsposten_aktion", args=[posten.pk, aktion.schluessel])
            knoepfe.append(Knopf(aktion.schluessel, aktion.titel, url=url))
    return knoepfe


def _verweis(posten: Arbeitsposten) -> verweise.Verweis | None:
    # Bezug gelöscht oder unlesbar: Posten ohne Link zeigen.
    try:
        return verweise.fuer_posten(posten)
    except Exception:
        return None


#: Länge des Titels, wenn er aus dem Hinweis kommt (Posten ohne Bezug)
TITEL_LAENGE = 60


def _titel(posten: Arbeitsposten, kontext: dict[str, Any], verweis: verweise.Verweis | None) -> str:
    """Der Gegenstand des Postens, nie noch einmal die Art (Review 3, Punkt 8).

    Zuerst der Titel aus dem Kontext der Art, sonst die Beschreibung des Bezugs
    (``verweise``, mit Namen, nur in der Oberfläche). Ohne Bezug der Anfang des Hinweises bis
    zum ersten Doppelpunkt oder Punkt, zuletzt die Nummer des Postens.
    """
    for kandidat in (kontext.get("titel"), verweis.text if verweis else ""):
        if kandidat and kandidat != posten.art:
            return str(kandidat)
    anfang = re.split(r"[:.;]\s", f"{posten.hinweis} ", maxsplit=1)[0].strip()
    if anfang and anfang != posten.art:
        if len(anfang) > TITEL_LAENGE:
            anfang = anfang[: TITEL_LAENGE - 1].rstrip() + "…"
        return anfang
    return f"Posten #{posten.pk}"


def zeilen(posten_liste: Sequence[Arbeitsposten]) -> list[Zeile]:
    """Je Posten eine Zeile mit Titel, Link, Knöpfen und der Arbeitsfläche seiner Art."""
    je_art: dict[str, list[Arbeitsposten]] = defaultdict(list)
    for p in posten_liste:
        je_art[p.art].append(p)
    kontexte: dict[int, dict[str, Any]] = {}
    for art, liste in je_art.items():
        bearbeitung = posteingang.fuer_art(art)
        if bearbeitung is not None and bearbeitung.kontext is not None:
            kontexte |= bearbeitung.kontext([p for p in liste if p.status == OFFEN])
    ergebnis = []
    for p in posten_liste:
        bearbeitung = posteingang.fuer_art(p.art)
        kontext = kontexte.get(p.pk, {})
        verweis = _verweis(p)
        offen = p.status == OFFEN
        bearbeiten = ""
        if offen and bearbeitung is not None and bearbeitung.bearbeiten is not None and kontext:
            bearbeiten = bearbeitung.bearbeiten(p)
        elif offen and verweis is not None:
            bearbeiten = verweis.url
        ergebnis.append(
            Zeile(
                posten=p,
                p=posteingang.praefix(p),
                titel=_titel(p, kontext, verweis),
                verweis=verweis,
                knoepfe=_knoepfe(p),
                vorlage=bearbeitung.vorlage if (bearbeitung and kontext and offen) else "",
                kontext=kontext,
                kann_uebernehmen=bool(
                    offen
                    and bearbeitung is not None
                    and bearbeitung.uebernehmen is not None
                    and kontext.get("kann_uebernehmen")
                ),
                bearbeiten_url=bearbeiten,
                bearbeiten_dialog=bool(
                    bearbeiten and bearbeitung is not None and bearbeitung.bearbeiten_als_dialog
                ),
            )
        )
    return ergebnis


def eintraege(zeilen_: Sequence[Zeile]) -> list[Zeile | Gruppenzeile]:
    """Zeilen in Reihenfolge; Posten mit derselben Ursache als Gruppe an der Stelle ihres
    ersten Postens (Review 1, Punkt 5)."""
    je_art: dict[str, list[Zeile]] = defaultdict(list)
    for z in zeilen_:
        if z.posten.status == OFFEN:
            je_art[z.posten.art].append(z)
    gruppe_von: dict[int, Gruppe] = {}
    for art, liste in je_art.items():
        bearbeitung = posteingang.fuer_art(art)
        if bearbeitung is None or bearbeitung.gruppen is None:
            continue
        kontext = {z.posten.pk: z.kontext for z in liste if z.kontext}
        for gruppe in bearbeitung.gruppen([z.posten for z in liste], kontext):
            for p in gruppe.posten:
                gruppe_von[p.pk] = gruppe
    ergebnis: list[Zeile | Gruppenzeile] = []
    offen: dict[str, Gruppenzeile] = {}
    nummer = 0
    for z in zeilen_:
        gruppe = gruppe_von.get(z.posten.pk)
        if gruppe is None:
            ergebnis.append(z)
            continue
        schluessel = f"{z.posten.art}|{gruppe.schluessel}"
        if schluessel not in offen:
            nummer += 1
            gruppe.praefix = f"g{nummer}"
            offen[schluessel] = Gruppenzeile(gruppe=gruppe, zeilen=[])
            ergebnis.append(offen[schluessel])
        offen[schluessel].zeilen.append(z)
    return ergebnis


# --- Seite ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Ansicht:
    art: str
    status: str

    @property
    def zurueckgestellt(self) -> bool:
        return self.status == ZURUECKGESTELLT

    @property
    def gewaehlt(self) -> bool:
        """Hat Max eine Art (oder „alle“) gewählt? Am Handy zeigt die Seite dann die Liste."""
        return bool(self.art) or self.zurueckgestellt

    def url(self) -> str:
        parameter = {}
        if self.art:
            parameter["art"] = self.art
        if self.zurueckgestellt:
            parameter["status"] = ZURUECKGESTELLT
        basis = reverse("kern:arbeitslisten")
        return f"{basis}?{urlencode(parameter)}" if parameter else basis


def _ansicht(daten: Any) -> Ansicht:
    art = str(daten.get("art", "")).strip()
    status = ZURUECKGESTELLT if daten.get("status") == ZURUECKGESTELLT else OFFEN
    return Ansicht(art="" if art == "alle" else art, status=status)


@dataclass
class Art:
    titel: str
    anzahl: int
    url: str
    aktiv: bool


def _arten(ansicht: Ansicht) -> tuple[list[Art], int, int]:
    zahlen = dict(
        Arbeitsposten.objects.filter(status=OFFEN)
        .values_list("art")
        .annotate(n=Count("pk"))
        .order_by("art")
    )
    gesamt = sum(zahlen.values())
    zurueck = Arbeitsposten.objects.filter(status=ZURUECKGESTELLT).count()
    if ansicht.art and ansicht.art not in zahlen and not ansicht.zurueckgestellt:
        zahlen[ansicht.art] = 0  # gerade leer geworden: die Art bleibt gewählt
    arten = [
        Art(
            titel=art,
            anzahl=anzahl,
            url=Ansicht(art=art, status=OFFEN).url(),
            aktiv=art == ansicht.art and not ansicht.zurueckgestellt,
        )
        for art, anzahl in sorted(zahlen.items())
    ]
    return arten, gesamt, zurueck


def _posten(ansicht: Ansicht) -> list[Arbeitsposten]:
    abfrage = Arbeitsposten.objects.filter(status=ansicht.status).select_related(
        "bezug_typ", "lauf"
    )
    if ansicht.art:
        abfrage = abfrage.filter(art=ansicht.art)
    return list(abfrage.order_by("art", "faellig_am", "angelegt_am", "pk"))


def _kontext(request: HttpRequest, ansicht: Ansicht) -> dict[str, Any]:
    arten, gesamt, zurueck = _arten(ansicht)
    liste = _posten(ansicht)
    alle_zeilen = zeilen(liste)
    titel = "Zurückgestellt" if ansicht.zurueckgestellt else (ansicht.art or "Alle offenen Posten")
    sammel = _sammel_knoepfe(alle_zeilen)
    return {
        "ansicht": ansicht,
        "arten": arten,
        "gesamt": gesamt,
        "zurueck": zurueck,
        "alle_url": Ansicht(art="", status=OFFEN).url(),
        "alle_aktiv": not ansicht.art and not ansicht.zurueckgestellt,
        "zurueck_url": Ansicht(art="", status=ZURUECKGESTELLT).url(),
        "titel": titel,
        "anzahl": len(liste),
        "eintraege": eintraege(alle_zeilen),
        "sammel": sammel,
        "zeige_art": not ansicht.art,
    }


def _sammel_knoepfe(zeilen_: Sequence[Zeile]) -> list[Knopf]:
    """Was die Mehrfachauswahl anbietet: „Übernehmen“, wenn ein Posten einen Vorschlag hat,
    und die Aktionen ohne Eingabe, die mindestens ein Posten kennt."""
    knoepfe: list[Knopf] = []
    if any(z.kann_uebernehmen for z in zeilen_):
        knoepfe.append(Knopf(UEBERNEHMEN, "Für alle übernehmen"))
    gesehen: set[str] = set()
    for z in zeilen_:
        for k in z.knoepfe:
            if k.url and k.schluessel not in gesehen and k.schluessel in SAMMEL_AKTIONEN:
                gesehen.add(k.schluessel)
                knoepfe.append(Knopf(k.schluessel, SAMMEL_AKTIONEN[k.schluessel]))
    return knoepfe


#: Aktionen ohne Eingabe, die die Mehrfachauswahl anbietet (mit Text für den Knopf)
SAMMEL_AKTIONEN = {
    "zurueckstellen": "Zurückstellen",
    "erledigen": "Erledigt",
    aktionen.WIEDER_OEFFNEN: "Wieder öffnen",
}


@login_required
def arbeitslisten(request: HttpRequest) -> HttpResponse:
    """Der Posteingang. Per HTMX (Ziel ``posteingang``) nur der Inhalt ohne Rahmen."""
    ansicht = _ansicht(request.GET)
    kontext = _kontext(request, ansicht)
    if htmx.ist_htmx(request) and request.headers.get("HX-Target") == "posteingang":
        return render(request, "kern/_posteingang.html", kontext)
    return render(request, "kern/arbeitslisten.html", kontext)


# --- Antworten nach einer Aktion ----------------------------------------------------------


def _benutzer(request: HttpRequest) -> Benutzer:
    benutzer = request.user
    if not isinstance(benutzer, Benutzer):  # login_required stellt das sicher
        raise PermissionError("Nicht angemeldet.")
    return benutzer


def _arten_oob(request: HttpRequest, ansicht: Ansicht) -> str:
    arten, gesamt, zurueck = _arten(ansicht)
    return render_to_string(
        "kern/_posteingang_arten.html",
        {
            "ansicht": ansicht,
            "arten": arten,
            "gesamt": gesamt,
            "zurueck": zurueck,
            "alle_url": Ansicht(art="", status=OFFEN).url(),
            "alle_aktiv": not ansicht.art and not ansicht.zurueckgestellt,
            "zurueck_url": Ansicht(art="", status=ZURUECKGESTELLT).url(),
            "oob": True,
        },
        request=request,
    )


def _anzahl(ansicht: Ansicht) -> int:
    """Wie viele Posten die Liste der Ansicht zeigt (die Zahl im Listenkopf)."""
    abfrage = Arbeitsposten.objects.filter(status=ansicht.status)
    if ansicht.art:
        abfrage = abfrage.filter(art=ansicht.art)
    return abfrage.count()


def _anzahl_oob(ansicht: Ansicht) -> str:
    """Die Zahl im Listenkopf, per OOB zusammen mit den Zahlen links (Review 3, Punkt 3)."""
    return render_to_string(
        "kern/_posteingang_anzahl.html", {"anzahl": _anzahl(ansicht), "oob": True}
    )


def _zahlen_oob(request: HttpRequest, ansicht: Ansicht) -> str:
    """Zahlen links und im Listenkopf: immer beide zusammen, damit sie nie auseinanderlaufen."""
    return _arten_oob(request, ansicht) + _anzahl_oob(ansicht)


def _in_gruppe(daten: Any, posten: Arbeitsposten) -> bool:
    """Stand der Posten in einer Gruppe der Liste? Die Felder ``g<Nr>-posten`` kommen mit dem
    Formular der Liste. Dann ändert sich der Kopf der Gruppe, und die Liste lädt neu."""
    wert = str(posten.pk)
    return any(
        wert in daten.getlist(schluessel)
        for schluessel in daten
        if re.fullmatch(r"g\d+-posten", schluessel)
    )


def zeile_html(request: HttpRequest, posten: Arbeitsposten, *, fehler: str = "") -> str:
    (zeile,) = zeilen([posten])
    zeile.fehler = fehler
    return render_to_string("kern/_posten.html", {"z": zeile, "zeige_art": True}, request=request)


def entfernen_oob(posten_ids: Sequence[int]) -> list[str]:
    """OOB-Teile, die erledigte Posten aus der Liste nehmen (etwa nach einem Dialog)."""
    return [f'<li id="posten-{pk}" hx-swap-oob="delete"></li>' for pk in posten_ids]


def teile_nach_dialog(request: HttpRequest, posten_ids: Sequence[int]) -> list[str]:
    """Für ``htmx.erfolg`` nach einem Dialog aus der Arbeitsliste: die erledigten Zeilen
    entfernen und die Zahlen links erneuern (die Ansicht steht in ``HX-Current-URL``)."""
    adresse = urlsplit(request.headers.get("HX-Current-URL", ""))
    ansicht = _ansicht({k: v[0] for k, v in parse_qs(adresse.query).items()})
    return [*entfernen_oob(posten_ids), _zahlen_oob(request, ansicht)]


def nach_aktion(
    request: HttpRequest,
    posten: Arbeitsposten,
    ergebnis: Ergebnis,
    *,
    status_vorher: str,
) -> HttpResponse:
    """HTMX-Antwort für die Zeile: Ist der Posten erledigt (oder hat er die Ansicht
    verlassen), verschwindet die Zeile; sonst kommt sie neu. Dazu die Zahlen links und die
    Meldung mit Link."""
    posten.refresh_from_db()
    ansicht = _ansicht(request.POST or request.GET)
    html = "" if posten.status != status_vorher else zeile_html(request, posten)
    html += _zahlen_oob(request, ansicht)
    html += htmx.toast(request, ergebnis.text, link=ergebnis.link, link_text=ergebnis.link_text)
    antwort = HttpResponse(html)
    if ergebnis.weitere or _in_gruppe(request.POST, posten):
        antwort["HX-Trigger"] = json.dumps({EREIGNIS_NEU: True})
    return antwort


def _fehler_antwort(request: HttpRequest, posten: Arbeitsposten, text: str) -> HttpResponse:
    html = zeile_html(request, posten, fehler=text)
    html += htmx.toast(request, text, art="fehler")
    return HttpResponse(html)


def _standard_ergebnis(posten: Arbeitsposten, titel: str, rueckgabe: object) -> Ergebnis:
    if isinstance(rueckgabe, Ergebnis):
        return rueckgabe
    posten.refresh_from_db()
    verweis = _verweis(posten)
    return Ergebnis(
        text=f"Posten #{posten.pk} „{posten.art}“: {titel}, jetzt {posten.get_status_display()}.",
        link=verweis.url if verweis else "",
        link_text=verweis.titel if verweis else "ansehen",
    )


# --- Aktionen ----------------------------------------------------------------------------


@login_required
@require_POST
def arbeitsposten_aktion(request: HttpRequest, pk: int, aktion: str) -> HttpResponse:
    """Führt eine Aktion an einem Arbeitsposten aus (per HTMX oder als normales Formular)."""
    posten = get_object_or_404(Arbeitsposten.objects.select_for_update(), pk=pk)
    if not aktionen.erlaubt(posten, aktion):
        return HttpResponseBadRequest("Diese Aktion ist für den Arbeitsposten nicht möglich.")
    ausfuehrung = aktionen.holen(aktion)
    if ausfuehrung.seite is not None:
        # Aktion mit Eingabe: weiter auf ihre Seite (bei HTMX als ganze Seite).
        ziel = ausfuehrung.seite(posten)
        if htmx.ist_htmx(request):
            weiter = HttpResponse(status=200)
            weiter["HX-Redirect"] = ziel
            return weiter
        return redirect(ziel)
    vorher = posten.status
    try:
        with (
            transaction.atomic(),
            vorgaenge.vorgang(f"Arbeitsposten #{posten.pk}: {ausfuehrung.titel}"),
        ):
            rueckgabe = ausfuehrung.ausfuehren(posten, _benutzer(request))
    except (PostenFehler, ValueError) as fehler:
        if htmx.ist_htmx(request):
            return _fehler_antwort(request, posten, str(fehler))
        messages.error(request, f"Arbeitsposten #{posten.pk}: {fehler}")
        return redirect("kern:arbeitslisten")
    ergebnis = _standard_ergebnis(posten, ausfuehrung.titel, rueckgabe)
    if htmx.ist_htmx(request):
        return nach_aktion(request, posten, ergebnis, status_vorher=vorher)
    messages.success(request, ergebnis.text)
    return redirect("kern:arbeitslisten")


def _uebernehmen(posten: Arbeitsposten, benutzer: Benutzer, daten: Any) -> Ergebnis:
    bearbeitung = posteingang.fuer_art(posten.art)
    if bearbeitung is None or bearbeitung.uebernehmen is None or posten.status != OFFEN:
        raise PostenFehler(f"Posten #{posten.pk} hat keinen Vorschlag zum Übernehmen.")
    werte = posteingang.werte(daten, posteingang.praefix(posten))
    # Eigener Sicherungspunkt: Scheitert der Vorschlag, bleibt von ihm nichts stehen.
    with (
        transaction.atomic(),
        vorgaenge.vorgang(f"Arbeitsposten #{posten.pk}: Vorschlag übernommen"),
    ):
        return bearbeitung.uebernehmen(posten, benutzer, werte)


@login_required
@require_POST
def posten_uebernehmen(request: HttpRequest, pk: int) -> HttpResponse:
    """„Übernehmen“ (Enter): bucht den Vorschlag oder die Werte der Zeile."""
    posten = get_object_or_404(Arbeitsposten.objects.select_for_update(), pk=pk)
    vorher = posten.status
    try:
        ergebnis = _uebernehmen(posten, _benutzer(request), request.POST)
    except PostenFehler as fehler:
        if htmx.ist_htmx(request):
            return _fehler_antwort(request, posten, str(fehler))
        messages.error(request, str(fehler))
        return redirect(_ansicht(request.POST).url())
    if htmx.ist_htmx(request):
        return nach_aktion(request, posten, ergebnis, status_vorher=vorher)
    messages.success(request, ergebnis.text)
    return redirect(_ansicht(request.POST).url())


def _liste_neu(
    request: HttpRequest,
    text: str,
    *,
    art: str = "ok",
    link: str = "",
    link_text: str = "ansehen",
) -> HttpResponse:
    """Antwort mit der ganzen Liste (Ziel ``posteingang``) und einer Meldung."""
    ansicht = _ansicht(request.POST)
    html = render_to_string("kern/_posteingang.html", _kontext(request, ansicht), request=request)
    html += htmx.toast(request, text, art=art, link=link, link_text=link_text)
    return HttpResponse(html)


def _zahl(text: str) -> int | None:
    return int(text) if text.isdigit() else None


@login_required
@require_POST
def posten_sammel(request: HttpRequest) -> HttpResponse:
    """Mehrfachauswahl: „für alle übernehmen“ oder eine Aktion ohne Eingabe für alle
    gewählten Posten. Jeder Posten für sich; was nicht geht, steht in der Meldung."""
    aktion = request.POST.get("aktion", "")
    ids = [i for i in (_zahl(t) for t in request.POST.getlist("posten")) if i is not None]
    if aktion != UEBERNEHMEN and aktion not in SAMMEL_AKTIONEN:
        return HttpResponseBadRequest("Unbekannte Aktion.")
    benutzer = _benutzer(request)
    erledigt, uebersprungen, fehler = 0, 0, []
    for posten in Arbeitsposten.objects.select_for_update().filter(pk__in=ids).order_by("pk"):
        posten.refresh_from_db()
        if aktion == UEBERNEHMEN:
            if posten.status != OFFEN:
                uebersprungen += 1  # schon mit einem anderen erledigt (Regel, Gruppe)
                continue
            try:
                _uebernehmen(posten, benutzer, request.POST)
            except PostenFehler as grund:
                fehler.append(f"#{posten.pk}: {grund}")
                continue
            erledigt += 1
            continue
        if not aktionen.erlaubt(posten, aktion):
            uebersprungen += 1
            continue
        try:
            with (
                transaction.atomic(),
                vorgaenge.vorgang(f"Arbeitsposten #{posten.pk}: {SAMMEL_AKTIONEN[aktion]}"),
            ):
                aktionen.holen(aktion).ausfuehren(posten, benutzer)
        except (PostenFehler, ValueError) as grund:
            fehler.append(f"#{posten.pk}: {grund}")
            continue
        erledigt += 1
    titel = "übernommen" if aktion == UEBERNEHMEN else SAMMEL_AKTIONEN[aktion].lower()
    if not ids:
        text = "Keine Posten gewählt."
    else:
        text = f"{erledigt} von {len(ids)} Posten {titel}."
        if uebersprungen:
            text += f" {uebersprungen} übersprungen (schon erledigt oder ohne diese Aktion)."
        if fehler:
            text += " Nicht möglich: " + "; ".join(fehler)
    if htmx.ist_htmx(request):
        return _liste_neu(request, text, art="warnung" if fehler else "ok")
    (messages.warning if fehler else messages.success)(request, text)
    return redirect(_ansicht(request.POST).url())


@login_required
@require_POST
def posten_gruppe(request: HttpRequest) -> HttpResponse:
    """„Für alle anwenden“ im Kopf einer Gruppe (Review 1, Punkt 5)."""
    vorsilbe = request.POST.get("gruppe", "")
    if not vorsilbe.startswith("g") or not vorsilbe[1:].isdigit():
        return HttpResponseBadRequest("Unbekannte Gruppe.")
    ids = [i for i in (_zahl(t) for t in request.POST.getlist(f"{vorsilbe}-posten")) if i]
    posten = list(
        Arbeitsposten.objects.select_for_update().filter(pk__in=ids, status=OFFEN).order_by("pk")
    )
    arten = {p.art for p in posten}
    if len(arten) != 1:
        return HttpResponseBadRequest("Eine Gruppe enthält Posten genau einer Art.")
    bearbeitung = posteingang.fuer_art(arten.pop())
    if bearbeitung is None or bearbeitung.gruppe_anwenden is None:
        return HttpResponseBadRequest("Für diese Art gibt es kein „für alle anwenden“.")
    werte = posteingang.werte(request.POST, vorsilbe)
    try:
        with (
            transaction.atomic(),
            vorgaenge.vorgang(f"Arbeitsposten: Gruppe mit {len(posten)} Posten angewandt"),
        ):
            ergebnis = bearbeitung.gruppe_anwenden(posten, _benutzer(request), werte)
    except PostenFehler as fehler:
        if htmx.ist_htmx(request):
            return _liste_neu(request, str(fehler), art="fehler")
        messages.error(request, str(fehler))
        return redirect(_ansicht(request.POST).url())
    if htmx.ist_htmx(request):
        return _liste_neu(request, ergebnis.text, link=ergebnis.link, link_text=ergebnis.link_text)
    messages.success(request, ergebnis.text)
    return redirect(_ansicht(request.POST).url())
