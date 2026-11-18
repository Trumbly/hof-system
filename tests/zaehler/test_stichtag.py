"""Stichtag der Ablesung (H7.2) und Lauf ``zaehler_stichtag``."""

from __future__ import annotations

import datetime

import pytest
from django.conf import settings
from django.core.management import call_command
from django.test import Client
from django.urls import reverse

from apps.kern.models import Arbeitsposten, Lauf, Objekt
from apps.zaehler import konfiguration, services
from apps.zaehler.models import Zaehler

pytestmark = pytest.mark.django_db
D = datetime.date


def _lauf(tag: str) -> Lauf:
    call_command("zaehler_stichtag", "--stichtag", tag)
    return Lauf.objects.filter(name="zaehler_stichtag").order_by("-pk").first()  # type: ignore[return-value]


def _posten() -> list[Arbeitsposten]:
    return list(Arbeitsposten.objects.filter(art=services.ART_ABLESUNG_FEHLT).order_by("pk"))


def test_stichtag_31_12_steht_in_der_konfiguration() -> None:
    """Schritt 8, Aufgabe 4: Stichtag 31.12. als Konfiguration, Erinnerung ab dem 15.12."""
    k = konfiguration.laden()
    assert (k.stichtag.monat, k.stichtag.tag) == (12, 31)
    assert (k.erinnern_ab.monat, k.erinnern_ab.tag) == (12, 15)
    assert services.stichtag_im_jahr(2025) == D(2025, 12, 31)
    assert services.ablesefenster(D(2025, 12, 31)) == (D(2025, 12, 15), D(2026, 1, 15))
    assert services.aktueller_stichtag(D(2025, 12, 14)) is None
    assert services.aktueller_stichtag(D(2025, 12, 15)) == D(2025, 12, 31)
    assert services.aktueller_stichtag(D(2026, 1, 10)) == D(2025, 12, 31)
    assert services.letzter_stichtag(D(2026, 11, 1)) == D(2025, 12, 31)
    assert services.letzter_stichtag(D(2026, 12, 31)) == D(2026, 12, 31)


def test_konfiguration_lehnt_ungueltige_tage_ab() -> None:
    with pytest.raises(konfiguration.KonfigurationFehler, match="MM-TT"):
        konfiguration._lesen(
            {
                "stichtag": {"datum": "02-29", "erinnern_ab": "12-15", "nachlauf_tage": 15},
                "eichfrist": {"vorlauf_tage": 90},
            }
        )


def test_zum_stichtag_zeigt_die_arbeitsliste_fehlende_ablesungen(
    stromzaehler: Zaehler, objekt: Objekt, angemeldet: Client
) -> None:
    """Abnahme Schritt 8: Zum Stichtag zeigt die Arbeitsliste, welche Ablesung fehlt. Ab dem
    15.12. legt der Lauf je Zähler ohne Stand einen Arbeitsposten „Ablesung fehlt“ an,
    idempotent; mit dem Stand schließt sich der Posten."""
    mit_stand = services.zaehler_anlegen("TZ-W-1", "wasser", objekt=objekt)
    services.stand_erfassen(mit_stand, D(2025, 12, 16), "40")
    services.zaehler_anlegen("TZ-W-2", "wasser", objekt=objekt, einbaudatum=D(2026, 2, 1))

    vorher = _lauf("2025-12-14")
    assert vorher.status == Lauf.Status.ERFOLGREICH and "außerhalb" in vorher.meldung
    assert not _posten()

    lauf = _lauf("2025-12-15")
    (posten,) = _posten()
    assert (posten.bezug, posten.faellig_am, posten.lauf) == (stromzaehler, D(2025, 12, 31), lauf)
    assert "TZ-S-1" in posten.hinweis and "31.12.2025" in posten.hinweis
    assert lauf.arbeitsposten == 1

    # Idempotent: ein zweiter Lauf legt keinen zweiten Posten an.
    assert _lauf("2025-12-20").arbeitsposten == 0
    assert len(_posten()) == 1

    seite = angemeldet.get(reverse("kern:arbeitslisten")).content.decode()
    assert "Ablesung fehlt" in seite and "TZ-S-1" in seite
    ablesen = reverse("zaehler:ablesung", args=[objekt.pk]) + f"#zaehler-{stromzaehler.pk}"
    assert f'href="{ablesen}"' in seite

    # Mit dem Stand im Ablesefenster schließt sich der Posten.
    services.stand_erfassen(stromzaehler, D(2026, 1, 3), "812")
    posten.refresh_from_db()
    assert posten.status == Arbeitsposten.Status.ERLEDIGT
    assert _lauf("2026-01-05").arbeitsposten == 0


def test_lauf_schliesst_posten_und_legt_im_neuen_jahr_neu_an(stromzaehler: Zaehler) -> None:
    _lauf("2024-12-20")
    (alt,) = _posten()
    assert alt.faellig_am == D(2024, 12, 31)
    # Nach dem Fenster bleibt der Posten offen, bis der Stand nachgetragen ist.
    _lauf("2025-03-01")
    alt.refresh_from_db()
    assert alt.status == Arbeitsposten.Status.OFFEN
    services.stand_erfassen(stromzaehler, D(2024, 12, 30), "700")
    _lauf("2025-03-02")
    alt.refresh_from_db()
    assert alt.status == Arbeitsposten.Status.ERLEDIGT

    _lauf("2025-12-15")
    neu = _posten()[-1]
    assert neu.pk != alt.pk and neu.faellig_am == D(2025, 12, 31)


def test_ausgebauter_zaehler_braucht_keinen_stand(stromzaehler: Zaehler) -> None:
    _lauf("2025-12-15")
    (posten,) = _posten()
    services.zaehler_wechseln(
        stromzaehler, D(2025, 12, 18), "900", neue_nummer="TZ-S-2", einbau_stand="0"
    )
    posten.refresh_from_db()
    assert posten.status == Arbeitsposten.Status.ERLEDIGT
    # Der neue Zähler hat seinen Einbaustand im Fenster: kein Posten.
    _lauf("2025-12-19")
    assert [p.status for p in _posten()] == [Arbeitsposten.Status.ERLEDIGT]


def test_ablesung_fehlt_meldet_sich_als_freigabe() -> None:
    """Die neue Posten-Art steht im Standard der Benachrichtigung (ein Hinweis am Tag)."""
    assert services.ART_ABLESUNG_FEHLT in settings.HOF_BENACHRICHTIGUNG_ARTEN


def test_timer_taeglich_mit_eigener_pruefung() -> None:
    """Schritt 8, Aufgabe 4: Der Lauf läuft täglich (er prüft selbst das Fenster), seit
    Schritt 9 im Tageslauf nach den Fristen statt mit eigenem Zeitplan."""
    from apps.cockpit import tageslauf

    laeufe = settings.BASE_DIR / "deploy" / "systemd" / "laeufe"
    assert "OnCalendar=*-*-* 06:00:00" in (laeufe / "tageslauf.conf").read_text()
    assert not (laeufe / "zaehler_stichtag.conf").exists()
    schritt = next(s for s in tageslauf.schritte() if s.name == "zaehler_stichtag")
    assert schritt.nur_wochentag is None
    assert tageslauf.FRISTEN < schritt.reihenfolge < tageslauf.VERWALTIO
