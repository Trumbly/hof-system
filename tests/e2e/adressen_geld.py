"""Adressen der Seiten im Bereich Geld (Apple-Design, PR D) für ``test_apple_geld.py``.

Läuft wie ``tests/e2e/adressen.py`` im Server-Prozess (``manage.py shell -c``) nach dem
Aufbau des Testhofs und dem Tageslauf. Die Objekte werden über feste Kennungen oder die erste
passende Zeile gesucht, die Adressen mit ``reverse()`` gebaut.

Ausgabe: eine JSON-Zeile auf stdout (``GELD {…}``).
"""

from __future__ import annotations

import json

from django.db.models import Count
from django.urls import reverse

from apps.abgleich.models import Klaerfall, Zahlerregel
from apps.bank.models import Bankkonto, Bankumsatz
from apps.kassenbuch.models import Zuordnungsregel
from apps.mahnwesen.models import Mahnfall, Mahnstufe
from apps.stammdaten.models import Vertrag

VERTRAG = "V-0005"  # aktiv, mit Rückstand und Zahlungserinnerung (wie im Smoke-Test)


def sammeln() -> dict[str, str]:
    vertrag = Vertrag.objects.get(nummer=VERTRAG)
    konto = Bankkonto.objects.exclude(zugang=Bankkonto.Zugang.NUR_IMPORT).order_by("pk")[0]
    gebucht = (
        Bankumsatz.objects.annotate(n=Count("buchungen"))
        .filter(n__gt=0)
        .order_by("-buchungsdatum", "pk")[0]
    )
    klaerfall = Klaerfall.objects.filter(art=Klaerfall.Art.ZWECK, ergebnis="").order_by("pk")[0]
    regel = Zuordnungsregel.objects.order_by("pk")[0]
    zahlerregel = Zahlerregel.objects.order_by("pk")[0]
    fall = (
        Mahnfall.objects.annotate(n=Count("schritte")).filter(n__gt=0).order_by("pk").first()
        or Mahnfall.objects.order_by("pk")[0]
    )
    stufe = Mahnstufe.objects.order_by("pk")[0]
    return {
        # Bank
        "bank_konten": reverse("bank:konten"),
        "bank_umsaetze": reverse("bank:umsaetze"),
        "bank_import": reverse("bank:import"),
        "bank_freigabe": reverse("bank:freigabe", args=[konto.pk]),
        # Kassenbuch
        "kassenbuch": reverse("kassenbuch:buchungen"),
        "kassenbuch_umsatz": reverse("kassenbuch:umsatz", args=[gebucht.pk]),
        "kassenbuch_zuordnen": reverse("kassenbuch:zuordnen", args=[gebucht.pk]),
        "kassenbuch_aufteilen": reverse("kassenbuch:aufteilen", args=[gebucht.pk]),
        "kassenbuch_regel_aus_umsatz": reverse("kassenbuch:regel_aus_umsatz", args=[gebucht.pk]),
        "kassenbuch_regeln": reverse("kassenbuch:regeln"),
        "kassenbuch_regel_neu": reverse("kassenbuch:regel_neu"),
        "kassenbuch_regel": reverse("kassenbuch:regel", args=[regel.pk]),
        "kassenbuch_regel_anwenden": reverse("kassenbuch:regel_anwenden", args=[regel.pk]),
        "kassenbuch_abschluesse": reverse("kassenbuch:abschluesse"),
        "kassenbuch_bericht": reverse("kassenbuch:bericht"),
        # Soll
        "offene_posten": reverse("soll:liste"),
        "soll_vertrag": reverse("soll:vertrag", args=[vertrag.pk]),
        # Abgleich
        "rueckstaende": reverse("abgleich:rueckstaende"),
        "mietkonto": reverse("abgleich:mietkonto", args=[vertrag.pk]),
        "handzahlung": reverse("abgleich:handzahlung", args=[vertrag.pk]),
        "klaerfall": reverse("abgleich:klaerfall", args=[klaerfall.pk]),
        "klaerfall_zuordnen": reverse("abgleich:zuordnen", args=[klaerfall.pk]),
        "klaerfall_aufteilen": reverse("abgleich:aufteilen", args=[klaerfall.pk]),
        "klaerfall_regel": reverse("abgleich:regel_aus_klaerfall", args=[klaerfall.pk]),
        "zahlerregeln": reverse("abgleich:zahlerregeln"),
        "zahlerregel": reverse("abgleich:zahlerregel", args=[zahlerregel.regel_id]),
        # Mahnwesen
        "mahnwesen": reverse("mahnwesen:faelle"),
        "mahnfall": reverse("mahnwesen:fall", args=[fall.pk]),
        "mahnstufen": reverse("mahnwesen:stufen"),
        "mahnstufe": reverse("mahnwesen:stufe", args=[stufe.pk]),
        "sperren": reverse("mahnwesen:sperren"),
        "kuendigen": reverse("mahnwesen:kuendigen", args=[vertrag.pk]),
    }


def ausgeben() -> None:
    print("GELD " + json.dumps(sammeln(), ensure_ascii=False))
