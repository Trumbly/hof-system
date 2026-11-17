"""Erfundene Kontaktdaten für den Testhof und die Tests aller Schritte.

- **IBAN:** gültig berechnete deutsche IBAN (Prüfziffer nach ISO 13616, Modulo 97) mit
  erfundener Bankleitzahl. Echte Bankleitzahlen beginnen mit der Ziffer des
  Clearinggebiets 1 bis 8; Test-Bankleitzahlen beginnen hier immer mit ``0`` und
  gehören damit zu keiner Bank.
- **Telefon:** nur Nummern, die die Bundesnetzagentur für Medienproduktionen freigibt
  („Drama Numbers“). Sie sind nicht geschaltet.
- **E-Mail:** immer auf ``.invalid`` (RFC 2606); dorthin kann keine Mail zugestellt werden.

Alle Funktionen nehmen einen ``random.Random``, damit gleicher Seed gleiche Werte ergibt.
"""

from __future__ import annotations

import random
import re
import unicodedata

# --- IBAN --------------------------------------------------------------------------

#: Erste Ziffer jeder Test-Bankleitzahl. Echte Bankleitzahlen beginnen mit 1 bis 8.
TEST_BLZ_PRAEFIX = "0"


def iban_pruefziffer(laendercode: str, bban: str) -> str:
    """Zweistellige Prüfziffer einer IBAN (ISO 13616, Modulo 97)."""
    umgestellt = bban + laendercode + "00"
    zahl = int("".join(str(int(zeichen, 36)) for zeichen in umgestellt.upper()))
    return f"{98 - zahl % 97:02d}"


def ist_gueltige_iban(iban: str) -> bool:
    """Prüft Aufbau und Prüfziffer einer IBAN (ohne Leerzeichen oder mit)."""
    iban = iban.replace(" ", "").upper()
    if not re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]{11,30}", iban):
        return False
    umgestellt = iban[4:] + iban[:4]
    return int("".join(str(int(zeichen, 36)) for zeichen in umgestellt)) % 97 == 1


def erfundene_blz(zufall: random.Random) -> str:
    """Eine erfundene achtstellige Bankleitzahl, die mit ``0`` beginnt."""
    return TEST_BLZ_PRAEFIX + "".join(zufall.choices("0123456789", k=7))


def erfundene_iban(zufall: random.Random, blz: str | None = None) -> str:
    """Eine gültige deutsche Test-IBAN mit erfundener Bankleitzahl (ohne Leerzeichen)."""
    blz = blz if blz is not None else erfundene_blz(zufall)
    if not ist_erfundene_blz(blz):
        raise ValueError("Test-IBANs brauchen eine erfundene Bankleitzahl (beginnt mit 0).")
    konto = "".join(zufall.choices("0123456789", k=10))
    bban = blz + konto
    return f"DE{iban_pruefziffer('DE', bban)}{bban}"


def ist_erfundene_blz(blz: str) -> bool:
    return bool(re.fullmatch(r"\d{8}", blz)) and blz.startswith(TEST_BLZ_PRAEFIX)


def ist_erfundene_iban(iban: str) -> bool:
    """Gültige deutsche IBAN mit erfundener Bankleitzahl."""
    iban = iban.replace(" ", "").upper()
    return (
        len(iban) == 22
        and iban.startswith("DE")
        and ist_gueltige_iban(iban)
        and ist_erfundene_blz(iban[4:12])
    )


# --- Telefon -----------------------------------------------------------------------

#: Rufnummernblöcke der Bundesnetzagentur für Medienproduktionen: Vorwahl, Stamm und
#: Anzahl der Endziffern (000 bis 999 bzw. 00 bis 99).
FIKTIVE_BLOECKE: tuple[tuple[str, str, int], ...] = (
    ("030", "23125", 3),  # Berlin
    ("069", "90009", 3),  # Frankfurt am Main
    ("040", "66969", 3),  # Hamburg
    ("0221", "4710", 3),  # Köln
    ("089", "99998", 3),  # München
    ("0171", "39200", 2),  # Mobilfunk
    ("0176", "040690", 2),  # Mobilfunk
)

#: Einzelne Mobilfunknummern der Bundesnetzagentur für Medienproduktionen.
FIKTIVE_EINZELNUMMERN: tuple[tuple[str, str], ...] = (
    ("0152", "28817386"),
    ("0152", "28895456"),
    ("0152", "54599371"),
    ("0172", "9925904"),
    ("0172", "9968532"),
    ("0172", "9973185"),
    ("0172", "9973186"),
    ("0172", "9980752"),
    ("0174", "9091317"),
    ("0174", "9464308"),
)


def telefon(zufall: random.Random, *, mobil: bool = False) -> str:
    """Eine Telefonnummer aus dem fiktiven Bereich, z. B. ``030 23125 417``."""
    bloecke = [b for b in FIKTIVE_BLOECKE if b[0].startswith("01") == mobil]
    vorwahl, stamm, stellen = zufall.choice(bloecke)
    ende = "".join(zufall.choices("0123456789", k=stellen))
    return f"{vorwahl} {stamm} {ende}"


def _nur_ziffern(nummer: str) -> str:
    ziffern = re.sub(r"\D", "", nummer)
    if ziffern.startswith("49"):
        ziffern = "0" + ziffern[2:]
    return ziffern


def ist_fiktive_telefonnummer(nummer: str) -> bool:
    """Liegt die Nummer in einem Bereich der Bundesnetzagentur für Medienproduktionen?"""
    ziffern = _nur_ziffern(nummer)
    for vorwahl, stamm, stellen in FIKTIVE_BLOECKE:
        praefix = vorwahl + stamm
        if ziffern.startswith(praefix) and len(ziffern) == len(praefix) + stellen:
            return True
    return any(ziffern == vorwahl + rest for vorwahl, rest in FIKTIVE_EINZELNUMMERN)


# --- E-Mail ------------------------------------------------------------------------

#: Erfundene Domains; die Endung ``.invalid`` hängt ``email()`` an.
DOMAINS = ("testhof", "beispielpost", "erfunden-mail", "hofpost")

_UMLAUTE = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"})


def _lokalteil(text: str) -> str:
    text = text.lower().translate(_UMLAUTE)
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^a-z0-9]+", ".", text).strip(".")
    return text or "kontakt"


def email(zufall: random.Random, *namen: str, domain: str | None = None) -> str:
    """Eine E-Mail-Adresse auf ``.invalid``, z. B. ``erika.mustermann@hofpost.invalid``."""
    lokal = ".".join(_lokalteil(n) for n in namen if n) or "kontakt"
    domain = domain if domain is not None else zufall.choice(DOMAINS)
    return f"{lokal}@{_lokalteil(domain)}.invalid"


def ist_invalid_mail(adresse: str) -> bool:
    return bool(re.fullmatch(r"[^@\s]+@[^@\s]+\.invalid", adresse.strip().lower()))
