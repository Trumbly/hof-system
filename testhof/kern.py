"""Testhof-Modul des Kerns: Mandanten, Objekte und der Testbenutzer.

- vier Mandanten (GbR, Nachlass, KG-Linie, privat) mit erfundenen Namen,
- drei Objekte mit erfundenen Anschriften,
- der Benutzer ``test@testhof.invalid`` mit festem Passwort und festem TOTP-Geheimnis,
  nur für Entwicklung und Tests (in der Produktion sperrt ``testhof.sperre``).

Geschrieben wird über ``apps.kern.services``.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass

from apps.kern import services
from apps.kern.models import Benutzer, Mandant, Objekt
from testhof import register
from testhof.aufbau import Kontext, TesthofFehler

NAME = "kern"

EMAIL = "test@testhof.invalid"
#: Nur für Entwicklung und Tests. Steht absichtlich im Code: Es schützt nichts Echtes.
PASSWORT = "Testhof-nur-dev-und-tests-42"
#: Festes TOTP-Geheimnis (Base32) für die Authenticator-App oder ``totp_code()`` in Tests.
TOTP_GEHEIMNIS = base64.b32encode(b"testhof-nur-dev-test").decode("ascii")


@dataclass(frozen=True)
class KernDaten:
    mandanten: dict[str, Mandant]  # nach Art, z. B. ``mandanten["gbr"]``
    objekte: dict[str, Objekt]  # nach Kürzel, z. B. ``objekte["HOF"]``
    benutzer: Benutzer


# Kürzel und Bezeichnungen der Objekte sind unbedenklich (keine Personendaten).
OBJEKTE: tuple[tuple[str, str, str], ...] = (
    ("HOF", "Hofanlage mit Garagen und Stellplätzen", Mandant.Art.GBR),
    ("HALLE", "Gewerbehalle mit Lagerflächen", Mandant.Art.KG_LINIE),
    ("FELD", "Außenflächen und Scheune", Mandant.Art.NACHLASS),
)


def _mandanten(kontext: Kontext) -> dict[str, Mandant]:
    f = kontext.faker
    bezeichnungen = {
        Mandant.Art.GBR: ("GBR", f"{f.last_name()} und {f.last_name()} GbR"),
        Mandant.Art.NACHLASS: ("NACHLASS", f"Nachlass {f.first_name()} {f.last_name()}"),
        Mandant.Art.KG_LINIE: ("KG", f"KG-Linie {f.last_name()}"),
        Mandant.Art.PRIVAT: ("PRIVAT", f"{f.first_name()} {f.last_name()} (privat)"),
    }
    return {
        str(art): services.mandant_anlegen(art, kuerzel, bezeichnung)
        for art, (kuerzel, bezeichnung) in bezeichnungen.items()
    }


def _anschrift(kontext: Kontext) -> str:
    f = kontext.faker
    return f"{f.street_name()} {f.building_number()}\n{f.postcode()} {f.city()}"


def aufbauen(kontext: Kontext) -> KernDaten:
    if Mandant.objects.exists() or Objekt.objects.exists():
        raise TesthofFehler(
            "In der Datenbank stehen schon Mandanten oder Objekte. "
            "Mit --leeren wird der Testhof neu aufgebaut."
        )
    if Benutzer.objects.filter(email=EMAIL).exists():
        raise TesthofFehler(f"Den Benutzer {EMAIL} gibt es schon. Mit --leeren neu aufbauen.")

    mandanten = _mandanten(kontext)
    objekte = {
        kuerzel: services.objekt_anlegen(
            mandanten[str(art)], kuerzel, bezeichnung, anschrift=_anschrift(kontext)
        )
        for kuerzel, bezeichnung, art in OBJEKTE
    }
    benutzer = services.benutzer_anlegen(EMAIL, PASSWORT)
    services.zweiten_faktor_einrichten(benutzer, TOTP_GEHEIMNIS)
    return KernDaten(mandanten=mandanten, objekte=objekte, benutzer=benutzer)


def leeren() -> None:
    """Löscht Objekte, Mandanten und den Testbenutzer (nur erfundene Daten)."""
    Objekt.objects.all().delete()
    Mandant.objects.all().delete()
    # Anmeldedaten von allauth (E-Mail, TOTP) hängen am Benutzer und gehen mit.
    Benutzer.objects.filter(email=EMAIL).delete()


register.registrieren(NAME, aufbauen=aufbauen, leeren=leeren)
