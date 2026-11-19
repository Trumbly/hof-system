"""Seite „Läufe“ in der neuen Gestaltung (Review 2, B16; PR R2-9): Knopf „jetzt ausführen“,
Meldungen mit Links zu den betroffenen Posten, Status-Badges."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from django.conf import settings
from django.core.management import call_command
from django.test import Client
from django.urls import reverse

from apps.cockpit import tageslauf
from apps.kern import meldungen, services
from apps.kern.laeufe_seite import TIMER_LAEUFE, ausfuehrbare_laeufe
from apps.kern.models import Arbeitsposten, Lauf

pytestmark = pytest.mark.django_db

SEITE = reverse("kern:laeufe")
AUSFUEHREN = reverse("kern:lauf_ausfuehren")
HTMX = {"HTTP_HX_REQUEST": "true"}


def test_jetzt_ausfuehren_startet_den_lauf_in_dev(angemeldet: Client) -> None:
    """Review 2, B16: Je Lauf ein Knopf „jetzt ausführen“; in dev startet er den Lauf sofort,
    die Meldung nennt Status und Körbe und verlinkt auf den neuen Lauf."""
    seite = angemeldet.get(SEITE).content.decode()
    assert 'data-lauf="kern_selbsttest"' in seite and 'data-lauf="tageslauf"' in seite
    assert "?dialog=ausfuehren" not in seite  # dev: ohne Bestätigungsdialog

    antwort = angemeldet.post(AUSFUEHREN, {"lauf": "kern_selbsttest"}, follow=True)

    lauf = Lauf.objects.get(name="kern_selbsttest")
    assert lauf.status == Lauf.Status.ERFOLGREICH and lauf.arbeitsposten == 1
    html = antwort.content.decode()
    assert f"Lauf #{lauf.pk} kern_selbsttest: erfolgreich · 1 Arbeitsposten" in html
    assert f'href="{SEITE}#lauf-{lauf.pk}"' in html


def test_jetzt_ausfuehren_per_htmx_ersetzt_die_liste(angemeldet: Client) -> None:
    """Review 2, B-Leitlinien: Nach dem Start wird die Liste ersetzt (die Seite springt nicht),
    dazu eine Toast-Meldung mit Link."""
    antwort = angemeldet.post(AUSFUEHREN, {"lauf": "kern_selbsttest"}, **HTMX)
    html = antwort.content.decode()
    lauf = Lauf.objects.get(name="kern_selbsttest")
    assert 'id="laeufe-liste" hx-swap-oob="true"' in html
    assert 'id="laeufe-start" hx-swap-oob="true"' in html
    assert f'id="lauf-{lauf.pk}"' in html and 'class="toast-link"' in html
    assert "dialogSchliessen" in json.loads(antwort["HX-Trigger"])

    # Ein Knopf auf einer anderen Seite („Alle Konten abrufen“): nur die Meldung.
    antwort = angemeldet.post(AUSFUEHREN, {"lauf": "kern_selbsttest", "ausserhalb": "1"}, **HTMX)
    html = antwort.content.decode()
    assert "laeufe-liste" not in html and 'class="toast-link"' in html


def test_nur_laeufe_aus_der_freigabeliste(angemeldet: Client) -> None:
    """Review 2, B16: Gestartet wird nur, was im Tageslauf steht oder einen Timer hat; andere
    Befehle (Testhof, Importe) nie."""
    for name in ("testhof", "import_einheiten", "migrate", "gibt_es_nicht"):
        antwort = angemeldet.post(AUSFUEHREN, {"lauf": name}, follow=True)
        assert "Diesen Lauf startet die Oberfläche nicht." in antwort.content.decode()
    assert not Lauf.objects.exists()


def test_freigabeliste_ist_tageslauf_und_timer() -> None:
    """Review 2, B16: Die Freigabeliste sind die Schritte des Tageslaufs, der Tageslauf selbst
    und die Läufe mit eigenem Timer auf dem Server (deploy/systemd/laeufe)."""
    timer = {p.stem for p in Path(settings.BASE_DIR, "deploy", "systemd", "laeufe").glob("*.conf")}
    assert timer == set(TIMER_LAEUFE)
    erwartet = timer | {s.befehl for s in tageslauf.schritte()}
    assert set(ausfuehrbare_laeufe()) == erwartet
    assert next(iter(ausfuehrbare_laeufe())) == "tageslauf"


def test_in_prod_erst_nach_bestaetigung(
    angemeldet: Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review 2, B16: In prod startet „jetzt ausführen“ nur nach der Bestätigung im Dialog."""
    monkeypatch.setenv("DJANGO_ENV", "prod")
    seite = angemeldet.get(SEITE).content.decode()
    assert "?dialog=ausfuehren&amp;lauf=kern_selbsttest" in seite
    assert 'data-lauf="kern_selbsttest"' not in seite

    # Ohne Bestätigung: nichts läuft, es kommt der Dialog.
    antwort = angemeldet.post(AUSFUEHREN, {"lauf": "kern_selbsttest"})
    assert antwort.status_code == 302 and "dialog=ausfuehren" in antwort["Location"]
    antwort = angemeldet.post(AUSFUEHREN, {"lauf": "kern_selbsttest"}, **HTMX)
    assert "Jetzt ausführen" in antwort.content.decode()
    assert not Lauf.objects.exists()

    dialog = angemeldet.get(SEITE, {"dialog": "ausfuehren", "lauf": "kern_selbsttest"})
    html = dialog.content.decode()
    assert "Lauf kern_selbsttest jetzt ausführen?" in html and "echten Daten" in html
    assert 'name="bestaetigt"' in html

    angemeldet.post(AUSFUEHREN, {"lauf": "kern_selbsttest", "bestaetigt": "True"})
    assert Lauf.objects.get(name="kern_selbsttest").status == Lauf.Status.ERFOLGREICH


def test_status_badges_auch_fuer_uebersprungen(angemeldet: Client) -> None:
    """Review 2, B16: Status als Badge, auch „übersprungen“ und „abgebrochen“."""
    for status in ("uebersprungen", "abgebrochen", "erfolgreich", "mit_fehlern"):
        lauf = services.lauf_starten(f"lauf_{status}")
        services.lauf_beenden(
            lauf,
            gebucht=0,
            arbeitsposten=0,
            fehler=1 if status == "mit_fehlern" else 0,
            abgebrochen=status == "abgebrochen",
            uebersprungen=status == "uebersprungen",
        )
    html = angemeldet.get(SEITE).content.decode()
    assert '<span class="badge badge-neutral">übersprungen</span>' in html
    assert '<span class="badge badge-fehler">abgebrochen</span>' in html
    assert '<span class="badge badge-ok">erfolgreich</span>' in html
    assert '<span class="badge badge-warnung">mit Fehlern</span>' in html


def test_meldungen_verlinken_die_betroffenen_posten(angemeldet: Client) -> None:
    """Review 2, B16: Lauf-Meldungen bekommen Links zu den betroffenen Posten, etwa
    „Arbeitsposten #12“; der Text selbst bleibt (nur IDs)."""
    call_command("kern_selbsttest")
    posten = Arbeitsposten.objects.get()
    lauf = Lauf.objects.get()
    assert lauf.meldung == f"Arbeitsposten #{posten.pk} angelegt."

    html = angemeldet.get(SEITE).content.decode()
    ziel = f"{reverse('kern:arbeitslisten')}?art=Selbsttest#posten-{posten.pk}"
    assert f'<a href="{ziel}">Arbeitsposten #{posten.pk}</a> angelegt.' in html
    # Die Posten des Laufs stehen zusätzlich aufklappbar an der Zeile.
    assert "Arbeitsposten dieses Laufs" in html

    text = str(meldungen.verlinken("Lauf #3; Konto #2: gescheitert <b>x</b>"))
    assert f'<a href="{SEITE}#lauf-3">Lauf #3</a>' in text
    assert f'<a href="{reverse("bank:konten")}#konto-2">Konto #2</a>' in text
    assert "&lt;b&gt;x&lt;/b&gt;" in text  # maskiert
    assert str(meldungen.verlinken("V-9999 und NW-G-9999")) == "V-9999 und NW-G-9999"
