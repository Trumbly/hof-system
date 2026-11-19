"""Liste der anstehenden Übergaben und Rückgaben (Review 2, C1): Ziel des Knopfs „Übergabe“
unten am Handy."""

from __future__ import annotations

import datetime

from django.test import Client
from django.urls import reverse

from apps.stammdaten import services
from apps.stammdaten.models import Einheit, Partei, Vertrag
from tests.stammdaten.conftest import garage_anlegen, mieter_anlegen, vertrag_anlegen

D = datetime.date


def test_uebergaben_listet_entwuerfe_gekuendigte_und_begonnene(
    angemeldet: Client, garage: Einheit, mieter: Partei, aktiver_vertrag: Vertrag
) -> None:
    """Review 2, C1: „Übergabe“ führt zu den anstehenden Übergaben (Verträge im Entwurf) und
    Rückgaben (gekündigte Verträge); begonnene Erfassungen stehen oben mit „weiter“."""
    services.kuendigung_erfassen(aktiver_vertrag, D(2026, 11, 3))
    zweite = garage_anlegen(garage.objekt)
    entwurf = vertrag_anlegen(zweite, mieter_anlegen("Zweiter"), beginn=D(2026, 12, 1))
    dritte = garage_anlegen(garage.objekt)
    begonnen = vertrag_anlegen(dritte, mieter_anlegen("Dritter"), beginn=D(2026, 12, 15))
    uebergabe = services.uebergabe_beginnen(begonnen, "uebergabe", D(2026, 11, 19))

    antwort = angemeldet.get(reverse("stammdaten:uebergaben"))
    html = antwort.content.decode()

    assert antwort.status_code == 200
    rueckgaben = html.split('id="rueckgaben"', 1)[1]
    uebergaben = html.split('id="uebergaben"', 1)[1].split('id="rueckgaben"', 1)[0]
    oben = html.split('id="begonnen"', 1)[1].split('id="uebergaben"', 1)[0]
    assert aktiver_vertrag.nummer in rueckgaben and "Rückgabe beginnen" in rueckgaben
    assert entwurf.nummer in uebergaben and "Übergabe beginnen" in uebergaben
    assert "Zweiter" in uebergaben  # Name des Mieters
    assert begonnen.nummer in oben and begonnen.nummer not in uebergaben
    assert reverse("stammdaten:uebergabe", args=[uebergabe.pk, 1]) in oben
    assert reverse("stammdaten:uebergabe_starten", args=[entwurf.pk, "uebergabe"]) in uebergaben


def test_uebergaben_leer(angemeldet: Client) -> None:
    """Review 2, C1: Ohne Anstehendes sagt die Seite das, statt leer zu sein."""
    html = angemeldet.get(reverse("stammdaten:uebergaben")).content.decode()
    assert "Keine Übergabe steht an." in html
    assert "Keine Rückgabe steht an." in html


def test_knopf_uebergabe_unten_fuehrt_zur_liste(angemeldet: Client) -> None:
    """Review 2, C1: Unten am Handy führt „Übergabe“ zur Liste und ist dort markiert."""
    html = angemeldet.get(reverse("stammdaten:uebergaben")).content.decode()
    unten = html.split('class="unten"', 1)[1].split("</nav>", 1)[0]
    assert f'href="{reverse("stammdaten:uebergaben")}" aria-current="page"' in unten
