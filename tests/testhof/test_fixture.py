"""Fixture ``testhof`` und der Testhof in der Oberfläche (Schritt 0c, Aufgabe 6)."""

from __future__ import annotations

from django.test import Client
from django.urls import reverse

from apps.kern.models import Mandant, Objekt
from testhof import kern
from testhof.aufbau import Testhof
from tests.conftest import totp_code


def _als_testbenutzer_anmelden(client: Client) -> None:
    antwort = client.post(
        reverse("account_login"), {"login": kern.EMAIL, "password": kern.PASSWORT}
    )
    assert antwort["Location"] == reverse("mfa_authenticate")
    antwort = client.post(reverse("mfa_authenticate"), {"code": totp_code(kern.TOTP_GEHEIMNIS)})
    assert antwort.status_code == 302


def test_fixture_liefert_den_testhof(testhof: Testhof) -> None:
    """Schritt 0c, Aufgabe 6: Die Fixture testhof baut den Generator auf (Seed 42)."""
    assert testhof.seed == 42
    assert Mandant.objects.count() == 4
    assert Objekt.objects.count() == 3
    assert testhof.kern.benutzer.email == kern.EMAIL


def test_aenderung_im_test_wird_zurueckgerollt(testhof: Testhof) -> None:
    """Schritt 0c, Aufgabe 6: Was ein Test ändert, verschwindet nach dem Test."""
    from apps.kern import services

    services.objekt_anlegen(testhof.kern.mandanten["privat"], "NEU", "Neues Objekt")
    assert Objekt.objects.count() == 4


def test_testhof_einmal_pro_testlauf(testhof: Testhof) -> None:
    """Schritt 0c, Aufgabe 6: Der Testhof entsteht einmal pro Testlauf; alle Tests sehen
    dieselben Zeilen, Änderungen früherer Tests sind zurückgerollt."""
    assert Objekt.objects.count() == 3
    assert set(Objekt.objects.values_list("pk", flat=True)) == {
        o.pk for o in testhof.kern.objekte.values()
    }


def test_testhof_in_der_oberflaeche_sichtbar(client: Client, testhof: Testhof) -> None:
    """Fertig-Kriterium Schritt 0c: Der Testhof ist in der Oberfläche sichtbar. Der
    Testbenutzer meldet sich mit festem TOTP-Geheimnis an; die Startseite zeigt die Objekte."""
    _als_testbenutzer_anmelden(client)

    html = client.get(reverse("kern:startseite")).content.decode()

    for kuerzel, objekt in testhof.kern.objekte.items():
        assert kuerzel in html
        assert f'id="objekt-{objekt.pk}"' in html
        assert objekt.bezeichnung in html
    assert "Noch keine Objekte" not in html
    # Das Protokoll zeigt die Anlage durch den Testhof.
    assert "angelegt" in client.get(reverse("kern:protokoll")).content.decode()
