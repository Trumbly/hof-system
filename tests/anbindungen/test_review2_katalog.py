"""Review 2, A11: Einheiten ohne Fläche sind nicht buchbar und fehlen im Katalog; was dem
Katalog fehlt, steht im Datenstand als eigener Bereich „Katalog“."""

from __future__ import annotations

from decimal import Decimal

from django.test import Client
from django.urls import reverse

from apps.anbindungen import services
from apps.stammdaten import datenstand
from apps.stammdaten import services as stammdaten
from apps.stammdaten.models import Einheit
from testhof.aufbau import STICHTAG, Testhof


def test_einheit_ohne_flaeche_ist_nicht_buchbar(testhof: Testhof) -> None:
    """Review 2, A11: NW-G-0017 (Testhof, frei, auf der Website, ohne Fläche) steht nicht im
    Katalog. Mit Fläche ist sie buchbar."""
    einheit = Einheit.objects.get(nw_id="NW-G-0017")
    assert einheit.vermarktung == Einheit.Vermarktung.WEBSITE and not einheit.flaeche_qm
    assert services.buchbar_ab(einheit, STICHTAG) is None
    assert "NW-G-0017" not in {e.nw_id for e in services.katalog(STICHTAG)}
    assert all(e.flaeche_qm for e in services.katalog(STICHTAG))

    stammdaten.einheit_aendern(einheit, flaeche_qm=Decimal("18.50"))
    einheit.refresh_from_db()
    assert services.buchbar_ab(einheit, STICHTAG) is not None
    assert "NW-G-0017" in {e.nw_id for e in services.katalog(STICHTAG)}


def test_datenstand_hat_bereich_katalog(testhof: Testhof, angemeldet: Client) -> None:
    """Review 2, A11: Der Datenstand nennt je vermarkteter Einheit, was dem Katalog fehlt
    (Fläche, Bilder, Slug), mit Link zur Eingabe."""
    (katalog,) = [b for b in datenstand.ermitteln(STICHTAG) if b.schluessel == "katalog"]
    assert katalog.titel == "Katalog"
    luecken = {luecke.kennung: luecke for luecke in katalog.luecken}
    assert "Fläche" in luecken["NW-G-0017"].felder_titel
    assert luecken["NW-G-0017"].url.endswith("#stammfelder")
    nur_bilder = [lu for lu in katalog.luecken if lu.felder[0][0] == "bilder"]
    assert nur_bilder and nur_bilder[0].url == reverse(
        "anbindungen:bilder", args=[nur_bilder[0].objekt_id]
    )
    seite = angemeldet.get(reverse("stammdaten:datenstand")).content.decode()
    assert 'id="bereich-katalog"' in seite
