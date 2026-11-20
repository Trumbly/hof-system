"""Adressen und Werte des Testhofs für die Oberflächentests.

Läuft nicht in pytest, sondern im Server-Prozess der Oberflächentests
(``manage.py shell -c``, siehe ``tests/e2e/server.py``), nach dem Aufbau des Testhofs und dem
Tageslauf. Die Primärschlüssel ändern sich bei jedem Neuaufbau (``--leeren`` setzt die
Sequenzen nicht zurück); deshalb sucht dieses Modul die Objekte über ihre festen Kennungen
(NW-ID, V-Nummer, Kürzel) und baut die Adressen mit ``reverse()`` aus den URL-Namen.

Ausgabe: eine JSON-Zeile auf stdout, gelesen von ``server.adressen_lesen``.
"""

from __future__ import annotations

import io
import json
import re
from typing import Any

from django.contrib.contenttypes.models import ContentType
from django.urls import reverse
from pypdf import PdfReader

from apps.abgleich.models import Klaerfall
from apps.anbindungen.models import Buchungsanfrage
from apps.dokumente.models import Beleg, Dokument
from apps.kern.models import Arbeitsposten, Objekt
from apps.stammdaten.models import Einheit, Vertrag
from testhof.kassenbuch import AUSGABE_KATEGORIE

#: Feste Kennungen aus dem Testhof (Seed 42).
VERTRAG_SMOKE = "V-0005"  # aktiv, mit Rückstand und Zahlungserinnerung
EINHEIT_SMOKE = "NW-G-0018"  # Einheit von V-0005
OBJEKT_ABLESUNG = "HOF"
#: frei, je eine für die Verträge der Übergabe ohne Kaution (im Assistenten, Umweg)
EINHEITEN_FREI = ("NW-G-0012", "NW-G-0013")
UMSATZ_ZWECK = "Material Baumarkt"  # „Keine Regel passt“ (testhof/kassenbuch.py)


def _posten_zu(modell: type, pk: int) -> Arbeitsposten | None:
    typ = ContentType.objects.get_for_model(modell)
    return (
        Arbeitsposten.objects.filter(bezug_typ=typ, bezug_id=pk, status="offen")
        .order_by("pk")
        .first()
    )


def _umsatz_einordnen() -> tuple[int, int]:
    """Erster offener Posten „Umsätze einordnen“ zu einem Umsatz „Material Baumarkt“."""
    from apps.bank.models import Bankumsatz

    typ = ContentType.objects.get_for_model(Bankumsatz)
    for posten in Arbeitsposten.objects.filter(
        bezug_typ=typ, status="offen", art="Umsätze einordnen"
    ).order_by("faellig_am", "pk"):
        umsatz = Bankumsatz.objects.get(pk=posten.bezug_id)
        if umsatz.verwendungszweck.startswith(UMSATZ_ZWECK):
            return umsatz.pk, posten.pk
    raise LookupError("Kein offener Umsatz „Material Baumarkt“ im Testhof.")


def _beleg_angaben(dokument: Dokument) -> dict[str, str]:
    """Was ein Mensch vom PDF abliest: Datum, Betrag, Rechnungsnummer (testhof/belege.py)."""
    with dokument.datei.open("rb") as datei:
        text = "\n".join(
            seite.extract_text() for seite in PdfReader(io.BytesIO(datei.read())).pages
        )
    datum = re.search(r"Rechnungsdatum: (\d\d)\.(\d\d)\.(\d{4})", text)
    betrag = re.search(r"Gesamtbetrag: ([\d.]+,\d\d) EUR", text)
    nummer = re.search(r"Rechnungs-Nr\.: (\S+)", text)
    leistung = re.search(r"Leistung: (.+)", text)
    if not (datum and betrag and nummer and leistung):
        raise LookupError(f"PDF von Dokument #{dokument.pk} ohne erwartete Angaben.")
    return {
        "datum": f"{datum[3]}-{datum[2]}-{datum[1]}",
        "betrag": betrag[1].replace(".", ""),
        "rechnungsnummer": nummer[1],
        # Kostenart zur Leistung, wie der Testhof sie bucht (testhof/kassenbuch.py)
        "kostenart": next(
            (k for art, k in AUSGABE_KATEGORIE.items() if leistung[1].startswith(art)), ""
        ),
    }


def sammeln() -> dict[str, Any]:
    vertrag = Vertrag.objects.get(nummer=VERTRAG_SMOKE)
    einheit = Einheit.objects.get(nw_id=EINHEIT_SMOKE)
    objekt = Objekt.objects.get(kuerzel=OBJEKT_ABLESUNG)
    umsatz, umsatz_posten = _umsatz_einordnen()
    klaerfall = Klaerfall.objects.filter(art=Klaerfall.Art.ZWECK, ergebnis="").order_by("pk")[0]
    anfrage_buchung = Buchungsanfrage.objects.get(
        art=Buchungsanfrage.Art.BUCHUNG, einheit__nw_id="NW-G-0004"
    )
    anfrage_kuendigung = Buchungsanfrage.objects.get(art=Buchungsanfrage.Art.KUENDIGUNG)
    beleg = Beleg.objects.filter(status=Beleg.Status.OFFEN, dokument__herkunft="postfach")[0]
    posten = {
        "klaerfall": _posten_zu(Klaerfall, klaerfall.pk),
        "anfrage_buchung": _posten_zu(Buchungsanfrage, anfrage_buchung.pk),
        "anfrage_kuendigung": _posten_zu(Buchungsanfrage, anfrage_kuendigung.pk),
        "beleg": _posten_zu(Beleg, beleg.pk),
    }
    return {
        # Seiten des Smoke-Tests (Review 2, C5); die Übergabe öffnet der Test selbst.
        "seiten": {
            "cockpit": reverse("kern:startseite"),
            "arbeitslisten": reverse("kern:arbeitslisten"),
            "einheiten": reverse("stammdaten:einheiten"),
            "einheit": reverse("stammdaten:einheit", args=[einheit.pk]),
            "vertraege": reverse("stammdaten:vertraege"),
            "vertrag": reverse("stammdaten:vertrag", args=[vertrag.pk]),
            "kassenbuch": reverse("kassenbuch:buchungen"),
            "umsatz_einordnen": reverse("kassenbuch:zuordnen", args=[umsatz]),
            "klaerfall": reverse("abgleich:klaerfall", args=[klaerfall.pk]),
            "fristen": reverse("fristen:liste"),
            "dokumente": reverse("dokumente:ablage"),
            "schreiben": reverse("dokumente:schreiben_liste"),
            "zaehler_ablesung": reverse("zaehler:ablesung", args=[objekt.pk]),
            "protokoll": reverse("kern:protokoll"),
            # R2-9
            "laeufe": reverse("kern:laeufe"),
            "zaehler": reverse("zaehler:uebersicht"),
            "bank": reverse("bank:konten"),
            "bank_umsaetze": reverse("bank:umsaetze"),
            "offene_posten": reverse("soll:liste"),
            "mahnwesen": reverse("mahnwesen:faelle"),
            "anbindungen": reverse("anbindungen:uebersicht"),
            "abschluesse": reverse("kassenbuch:abschluesse"),
        },
        # Weitere Seiten aus R2-8 (Belege, Fristen, Listen, Datenstand, Schreiben)
        "seiten_r2_8": _seiten_r2_8(beleg),
        # Stammdaten im Apple-Design (PR C, tests/e2e/test_apple_stammdaten.py)
        "seiten_stammdaten": _seiten_stammdaten(vertrag, einheit, anfrage_buchung),
        "login": reverse("account_login"),
        "gesund": reverse("kern:gesund"),
        "arbeitslisten": reverse("kern:arbeitslisten"),
        "vertrag_smoke": reverse("stammdaten:vertrag", args=[vertrag.pk]),
        "vertrag_neu": reverse("stammdaten:vertrag_neu"),
        # Arbeitsposten, von denen die Klickmessung ausgeht (``#posten-<pk>``)
        "posten": {name: p.pk if p else None for name, p in posten.items()}
        | {"umsatz": umsatz_posten},
        "klaerfall_pk": klaerfall.pk,
        "anfrage_buchung_pk": anfrage_buchung.pk,
        "anfrage_kuendigung_pk": anfrage_kuendigung.pk,
        "anfrage_kuendigung": reverse("anbindungen:anfrage", args=[anfrage_kuendigung.pk]),
        "beleg": reverse("dokumente:beleg", args=[beleg.pk]),
        "beleg_angaben": _beleg_angaben(beleg.dokument),
        "einheiten_frei": list(EINHEITEN_FREI),
        # Was Max für den Vertrag aus der Anfrage nachschlagen müsste (Zielmiete der Einheit)
        "zielmiete_anfrage": _zielmiete(anfrage_buchung.einheit),
    }


def _mitteilung_erzeugen() -> Any:
    """Ein Schreiben (Mitteilung an Nutzer, V-0005) für die Seite eines Schreibens, falls der
    frische Testhof noch keines hat. Erzeugt, nicht gesendet."""
    from apps.dokumente import services
    from apps.dokumente.models import Vorlage
    from apps.stammdaten import schreiben as stammdaten_schreiben

    vorlage = Vorlage.objects.filter(art="mitteilung", status=Vorlage.Status.FREIGEGEBEN).first()
    if vorlage is None:
        return None
    vertrag = Vertrag.objects.get(nummer=VERTRAG_SMOKE)
    kontext = dict(stammdaten_schreiben.kontext_vertrag(vertrag))
    kontext["mitteilung"] = {"text": "Am Samstag wird der Hof gekehrt (Beispiel)."}
    try:
        return services.schreiben_erzeugen(vorlage, vertrag, kontext)
    except Exception:
        return None  # ohne WeasyPrint (PDF) gibt es die Seite nicht; der Test überspringt sie


def _seiten_r2_8(beleg: Beleg) -> dict[str, str]:
    from apps.dokumente.models import Schreiben

    schreiben = Schreiben.objects.order_by("pk").first() or _mitteilung_erzeugen()
    seiten = {
        "beleg": reverse("dokumente:beleg", args=[beleg.pk]),
        "ablage": reverse("dokumente:ablage"),
        "belegmappe": reverse("dokumente:belegmappe"),
        "fristen": reverse("fristen:liste"),
        "kassenbuch": reverse("kassenbuch:buchungen"),
        "monatsbericht": reverse("kassenbuch:bericht"),
        "katalog": reverse("anbindungen:vermarktung"),
        "datenstand": reverse("stammdaten:datenstand"),
        "schreiben_liste": reverse("dokumente:schreiben_liste"),
        "serie_neu": reverse("dokumente:serie_neu"),
        "stil": reverse("kern:stil"),
    }
    if schreiben is not None:
        seiten["schreiben"] = reverse("dokumente:schreiben", args=[schreiben.pk])
    return seiten


def _seiten_stammdaten(
    vertrag: Vertrag, einheit: Einheit, anfrage_buchung: Buchungsanfrage
) -> dict[str, str]:
    mieter = vertrag.vertragsparteien.order_by("pk").first()
    anfrage_kuendigung = Buchungsanfrage.objects.get(art=Buchungsanfrage.Art.KUENDIGUNG)
    seiten = {
        "uebersicht": reverse("stammdaten:uebersicht"),
        "einheiten": reverse("stammdaten:einheiten"),
        "einheit": reverse("stammdaten:einheit", args=[einheit.pk]),
        "einheit_neu": reverse("stammdaten:einheit_neu"),
        "leerstand": reverse("stammdaten:leerstand"),
        "vertraege": reverse("stammdaten:vertraege"),
        "vertrag": reverse("stammdaten:vertrag", args=[vertrag.pk]),
        "vertrag_neu": reverse("stammdaten:vertrag_neu"),
        "parteien": reverse("stammdaten:parteien"),
        "partei_neu": reverse("stammdaten:partei_neu"),
        "uebergaben": reverse("stammdaten:uebergaben"),
        "anfragen": reverse("anbindungen:anfragen"),
        "anfrage_buchung": reverse("anbindungen:anfrage", args=[anfrage_buchung.pk]),
        "anfrage_kuendigung": reverse("anbindungen:anfrage", args=[anfrage_kuendigung.pk]),
    }
    if mieter is not None:
        seiten["partei"] = reverse("stammdaten:partei", args=[mieter.partei_id])
    return seiten


def _zielmiete(einheit: Einheit) -> str:
    zielmiete = einheit.zielmieten.order_by("-gueltig_ab").first()
    return f"{zielmiete.betrag:.2f}" if zielmiete else "50.00"


def ausgeben() -> None:
    print("ADRESSEN " + json.dumps(sammeln(), ensure_ascii=False))
