"""Anmeldung mit E-Mail, Passwort und Pflicht zum zweiten Faktor (Schritt 0b, Aufgabe 1)."""

from __future__ import annotations

from urllib.parse import urlparse

import pytest
from django.conf import settings
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import Client
from django.urls import reverse

from apps.kern.models import Benutzer
from tests.conftest import anmelden, totp_code
from tests.factories import TESTPASSWORT

GESCHUETZTE_SEITEN = [
    "kern:startseite",
    "kern:arbeitslisten",
    "kern:protokoll",
    "kern:laeufe",
    "admin:index",
]


def _passwort_anmelden(client: Client, benutzer: Benutzer) -> str:
    antwort = client.post(
        reverse("account_login"), {"login": benutzer.email, "password": TESTPASSWORT}
    )
    assert antwort.status_code == 302
    return str(antwort["Location"])


def _einrichtung_oeffnen(client: Client) -> None:
    """Die Einrichtung des TOTP verlangt das Passwort noch einmal (allauth-Standard)."""
    antwort = client.get(reverse("mfa_activate_totp"))
    assert antwort.status_code == 302
    assert urlparse(antwort["Location"]).path == reverse("account_reauthenticate")
    antwort = client.post(reverse("account_reauthenticate"), {"password": TESTPASSWORT})
    assert antwort.status_code == 302
    assert client.get(reverse("mfa_activate_totp")).status_code == 200


def _kommt_nicht_hinein(client: Client) -> None:
    """Jede geschützte Seite leitet zurück zur Anmeldung."""
    anmeldung = {reverse("account_login"), reverse("admin:login")}
    for seite in GESCHUETZTE_SEITEN:
        antwort = client.get(reverse(seite))
        assert antwort.status_code == 302, seite
        assert urlparse(antwort["Location"]).path in anmeldung, seite


def test_ohne_zweiten_faktor_niemand_ueber_die_anmeldeseite_hinaus(
    client: Client, benutzer_mit_totp: Benutzer
) -> None:
    """Fertig-Kriterium Schritt 0b: Ohne zweiten Faktor kommt niemand über die Anmeldeseite
    hinaus. Nach dem Passwort folgt die Abfrage des TOTP-Codes; bis dahin ist keine Seite
    erreichbar."""
    ziel = _passwort_anmelden(client, benutzer_mit_totp)
    assert ziel == reverse("mfa_authenticate")

    _kommt_nicht_hinein(client)
    assert "_auth_user_id" not in client.session


def test_falscher_totp_code_laesst_niemanden_hinein(
    client: Client, benutzer_mit_totp: Benutzer
) -> None:
    """Fertig-Kriterium Schritt 0b: Ein falscher Code im zweiten Faktor öffnet keine Seite."""
    _passwort_anmelden(client, benutzer_mit_totp)
    antwort = client.post(reverse("mfa_authenticate"), {"code": "000000"})
    assert antwort.status_code == 200

    _kommt_nicht_hinein(client)


def test_mit_zweitem_faktor_ist_der_benutzer_angemeldet(
    client: Client, benutzer_mit_totp: Benutzer, totp_geheimnis: str
) -> None:
    """Abnahme Schritt 0, Punkt 1 (Teil Code): Anmeldung mit zweitem Faktor öffnet die Seiten."""
    anmelden(client, benutzer_mit_totp, totp_geheimnis)

    for seite in GESCHUETZTE_SEITEN:
        assert client.get(reverse(seite)).status_code == 200, seite


def test_benutzer_ohne_totp_muss_ihn_zuerst_einrichten(client: Client, benutzer: Benutzer) -> None:
    """Fertig-Kriterium Schritt 0b: Wer noch keinen zweiten Faktor hat, kommt nur zu dessen
    Einrichtung, nicht zu den Seiten des Hof-Systems."""
    ziel = _passwort_anmelden(client, benutzer)
    assert ziel == reverse("kern:startseite")

    for seite in GESCHUETZTE_SEITEN:
        antwort = client.get(reverse(seite))
        assert antwort.status_code == 302, seite
        assert antwort["Location"] == reverse("mfa_activate_totp"), seite
    _einrichtung_oeffnen(client)


def test_einrichtung_des_totp_schaltet_die_seiten_frei(client: Client, benutzer: Benutzer) -> None:
    """Fertig-Kriterium Schritt 0b: Nach der Einrichtung mit gültigem Code ist der Benutzer
    mit zweitem Faktor angemeldet."""
    _passwort_anmelden(client, benutzer)
    _einrichtung_oeffnen(client)
    geheimnis = client.session["mfa.totp.secret"]

    antwort = client.post(reverse("mfa_activate_totp"), {"code": totp_code(geheimnis)})
    assert antwort.status_code == 302

    assert client.get(reverse("kern:startseite")).status_code == 200


def test_anmeldung_ohne_zweiten_faktor_auf_anderem_weg_wird_abgewiesen(
    client: Client, benutzer_mit_totp: Benutzer
) -> None:
    """Fertig-Kriterium Schritt 0b: Eine Sitzung ohne bestandenen zweiten Faktor (etwa über
    eine andere Anmeldung) wird abgemeldet."""
    client.force_login(benutzer_mit_totp)

    antwort = client.get(reverse("kern:startseite"))

    assert antwort.status_code == 302
    assert antwort["Location"] == reverse("account_login")
    assert "_auth_user_id" not in client.session


@pytest.mark.django_db
def test_verwaltung_hat_keine_eigene_anmeldung(client: Client) -> None:
    """Fertig-Kriterium Schritt 0b: Die Anmeldung der Verwaltungsseiten leitet zur Anmeldung
    mit zweitem Faktor."""
    antwort = client.get(reverse("admin:login"))

    assert antwort.status_code == 302
    assert antwort["Location"].startswith(reverse("account_login"))


@pytest.mark.django_db
def test_keine_registrierung_ueber_die_oberflaeche(client: Client) -> None:
    """Schritt 0b, Aufgabe 1: Keine Registrierung über die Oberfläche."""
    client.post(
        reverse("account_signup"),
        {
            "email": "neu@testhof.invalid",
            "password1": TESTPASSWORT,
            "password2": TESTPASSWORT,
        },
    )

    assert not Benutzer.objects.exists()


def test_sitzung_laeuft_nach_12_stunden_ab(angemeldet: Client) -> None:
    """Schritt 0b, Aufgabe 1: Sitzungen laufen nach 12 Stunden ab."""
    assert settings.SESSION_COOKIE_AGE == 12 * 60 * 60
    assert angemeldet.session.get_expiry_age() == 12 * 60 * 60
    assert not angemeldet.session.get_expire_at_browser_close()


@pytest.mark.django_db
def test_anmeldeseite_im_eigenen_layout_und_am_handy(client: Client) -> None:
    """Schritt 0b, Aufgabe 1: Die Anmeldung steht im Layout des Hof-Systems, ohne Hinweis
    auf eine Registrierung."""
    html = client.get(reverse("account_login")).content.decode()

    assert 'name="viewport"' in html
    assert "css/apple/basis.css" in html
    assert 'autocomplete="username"' in html
    assert reverse("account_signup") not in html


def test_abmelden(angemeldet: Client) -> None:
    """Schritt 0b, Aufgabe 1: Abmelden beendet die Sitzung."""
    angemeldet.post(reverse("account_logout"))

    assert angemeldet.get(reverse("kern:startseite")).status_code == 302


@pytest.mark.django_db
def test_benutzer_anlegen_per_befehl(monkeypatch: pytest.MonkeyPatch) -> None:
    """Schritt 0b, Aufgabe 1: Der Benutzer entsteht per manage.py-Befehl."""
    monkeypatch.setattr("getpass.getpass", lambda prompt="": TESTPASSWORT)

    call_command("benutzer_anlegen", "--email", "Hof@Testhof.invalid")

    benutzer = Benutzer.objects.get()
    assert benutzer.email == "hof@testhof.invalid"
    assert benutzer.is_staff and benutzer.is_superuser
    assert benutzer.check_password(TESTPASSWORT)
    assert benutzer.emailaddress_set.get().verified


@pytest.mark.django_db
def test_benutzer_anlegen_weist_schwaches_passwort_ab(monkeypatch: pytest.MonkeyPatch) -> None:
    """Schritt 0b, Aufgabe 1: Der Befehl prüft das Passwort mit den Django-Regeln."""
    monkeypatch.setattr("getpass.getpass", lambda prompt="": "kurz")

    with pytest.raises(CommandError):
        call_command("benutzer_anlegen", "--email", "hof@testhof.invalid")
    assert not Benutzer.objects.exists()


def test_benutzer_anlegen_weist_doppelte_email_ab(
    benutzer: Benutzer, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Schritt 0b, Aufgabe 1: Zu einer E-Mail gibt es höchstens einen Benutzer."""
    monkeypatch.setattr("getpass.getpass", lambda prompt="": TESTPASSWORT)

    with pytest.raises(CommandError):
        call_command("benutzer_anlegen", "--email", benutzer.email.upper())
