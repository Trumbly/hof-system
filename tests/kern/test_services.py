"""Service-Funktionen des Kerns (CLAUDE.md, Abschnitt 5: jede Service-Funktion hat Tests)."""

from __future__ import annotations

import datetime

import pytest
from django.core.exceptions import ValidationError

from apps.kern import services
from apps.kern.models import Arbeitsposten, Lauf, Mandant
from tests.factories import BenutzerFabrik, MandantFabrik, ObjektFabrik

pytestmark = pytest.mark.django_db


def test_benutzer_anlegen_ohne_gueltiges_passwort() -> None:
    with pytest.raises(ValidationError):
        services.benutzer_anlegen("kurz@testhof.invalid", "12345")


def test_mandant_anlegen_und_aendern() -> None:
    mandant = services.mandant_anlegen(Mandant.Art.NACHLASS, "NL", "Nachlass Erfunden")
    assert mandant.art == "nachlass"

    services.mandant_aendern(mandant, bezeichnung="Nachlass Neu", aktiv=False)

    mandant.refresh_from_db()
    assert (mandant.bezeichnung, mandant.aktiv) == ("Nachlass Neu", False)


def test_mandant_mit_unbekannter_art() -> None:
    with pytest.raises(ValueError):
        services.mandant_anlegen("verein", "V", "Verein")


def test_mandant_kuerzel_ist_eindeutig() -> None:
    services.mandant_anlegen(Mandant.Art.GBR, "GBR", "GbR Erfunden")
    with pytest.raises(ValidationError):
        services.mandant_anlegen(Mandant.Art.PRIVAT, "GBR", "privat")


def test_objekt_anlegen_und_aendern() -> None:
    mandant, anderer = MandantFabrik(), MandantFabrik()
    objekt = services.objekt_anlegen(mandant, "HOF", "Hofstelle", "Erfundene Straße 1")

    services.objekt_aendern(objekt, bezeichnung="Hof", anschrift="", mandant=anderer, aktiv=False)

    objekt.refresh_from_db()
    assert (objekt.bezeichnung, objekt.anschrift, objekt.mandant, objekt.aktiv) == (
        "Hof",
        "",
        anderer,
        False,
    )


def test_lauf_starten_und_beenden() -> None:
    lauf = services.lauf_starten("test")
    assert lauf.status == Lauf.Status.LAEUFT

    services.lauf_beenden(lauf, gebucht=2, arbeitsposten=1, fehler=0, meldung="ok")

    lauf.refresh_from_db()
    assert lauf.status == Lauf.Status.ERFOLGREICH
    assert lauf.dauer_sekunden is not None and lauf.dauer_sekunden >= 0


@pytest.mark.parametrize(
    ("fehler", "abgebrochen", "status"),
    [(1, False, Lauf.Status.MIT_FEHLERN), (1, True, Lauf.Status.ABGEBROCHEN)],
)
def test_lauf_beenden_status(fehler: int, abgebrochen: bool, status: str) -> None:
    lauf = services.lauf_starten("test")
    services.lauf_beenden(lauf, gebucht=0, arbeitsposten=0, fehler=fehler, abgebrochen=abgebrochen)
    assert lauf.status == status


def test_arbeitsposten_anlegen_mit_bezug() -> None:
    objekt = ObjektFabrik()
    posten = services.arbeitsposten_anlegen(
        "Ablesung fehlt", bezug=objekt, faellig_am=datetime.date(2026, 12, 31), hinweis="Test"
    )

    posten.refresh_from_db()
    assert posten.bezug == objekt
    assert posten.aktionen == ["erledigen", "zurueckstellen"]
    assert posten.status == Arbeitsposten.Status.OFFEN


def test_arbeitsposten_mit_unbekannter_aktion() -> None:
    with pytest.raises(ValueError):
        services.arbeitsposten_anlegen("Test", aktionen=["zaubern"])


def test_arbeitsposten_erledigen_zurueckstellen_oeffnen() -> None:
    posten = services.arbeitsposten_anlegen("Test")

    services.arbeitsposten_erledigen(posten)
    assert posten.status == Arbeitsposten.Status.ERLEDIGT and posten.erledigt_am is not None

    services.arbeitsposten_wieder_oeffnen(posten)
    assert posten.status == Arbeitsposten.Status.OFFEN and posten.erledigt_am is None

    services.arbeitsposten_zurueckstellen(posten)
    posten.refresh_from_db()
    assert posten.status == Arbeitsposten.Status.ZURUECKGESTELLT


def test_ereignis_laden() -> None:
    from pghistory.models import Events

    objekt = ObjektFabrik(bezeichnung="A")
    services.objekt_aendern(objekt, bezeichnung="B")
    slug = Events.objects.tracks(objekt).order_by("-pgh_id").first().pgh_slug

    ereignis = services.ereignis_laden(slug)

    assert ereignis.modell is type(objekt)
    assert ereignis.objekt_id == objekt.pk
    assert ereignis.art == "update"
    assert ereignis.daten["bezeichnung"] == "B"
    assert ereignis.vorher is not None and ereignis.vorher["bezeichnung"] == "A"


def test_zweiten_faktor_einrichten_mit_festem_geheimnis() -> None:
    """Schritt 0c: Der Testbenutzer bekommt TOTP über allauth-mfa mit festem Geheimnis."""
    from allauth.mfa.models import Authenticator
    from allauth.mfa.totp.internal.auth import TOTP

    from tests.conftest import totp_code

    benutzer = BenutzerFabrik()
    geheimnis = "ORSXG5DIN5TC23TVOIWWIZLWFV2GK43U"

    authenticator = services.zweiten_faktor_einrichten(benutzer, geheimnis)

    assert authenticator.type == Authenticator.Type.TOTP
    assert authenticator.user == benutzer
    assert TOTP(authenticator).validate_code(totp_code(geheimnis))


def test_zweiten_faktor_einrichten_weist_ungueltiges_geheimnis_ab() -> None:
    from allauth.mfa.models import Authenticator

    benutzer = BenutzerFabrik()

    with pytest.raises(ValueError, match="Base32"):
        services.zweiten_faktor_einrichten(benutzer, "kein base32!")
    assert not Authenticator.objects.filter(user=benutzer).exists()
