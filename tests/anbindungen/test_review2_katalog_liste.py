"""Review 2, B11: Der Katalog zeigt standardmäßig nur buchbare Einheiten, „alle N“ ist ein
Schalter. Mit dem Testhof (NW-G-0017 hat keine Fläche und ist nicht buchbar)."""

from __future__ import annotations

from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.anbindungen import services
from testhof.aufbau import Testhof

SEITE = reverse("anbindungen:vermarktung")


def test_standard_nur_buchbare_alle_als_schalter(testhof: Testhof, angemeldet: Client) -> None:
    """Review 2, B11: standardmäßig nur buchbare Einheiten; „alle N“ zeigt auch die übrigen."""
    heute = timezone.localdate()
    buchbar = {e.nw_id for e in services.katalog(heute)}
    alle = services.vermarktbare_einheiten(heute).count()
    assert "NW-G-0017" not in buchbar and buchbar

    html = angemeldet.get(SEITE).content.decode()
    assert "NW-G-0017" not in html
    assert all(nw_id in html for nw_id in buchbar)
    assert f'href="{SEITE}?alle=1">alle <span class="zaehler">{alle}</span>' in html
    assert f'href="{SEITE}" aria-current="page">buchbar' in html

    mit_allen = angemeldet.get(SEITE, {"alle": "1"}).content.decode()
    assert "NW-G-0017" in mit_allen and "nicht buchbar" in mit_allen
