"""Versand per Mail: SMTP von kontakt@ und Kopie in „Gesendet“ per IMAP-Append (H8.3).

Die Clients sind austauschbar (Einstellung ``DOKUMENTE``):

- SMTP läuft über das Mail-Backend von Django (``EMAIL_URL``; lokal Mailpit, in Tests
  ``locmem``). ``MAIL_CLIENT`` nennt die Klasse, Standard ``DjangoMailClient``.
- IMAP: ``IMAP_CLIENT`` nennt die Klasse. ``ImaplibClient`` spricht mit dem Postfach
  (``IMAP_URL``, z. B. ``imaps://benutzer:passwort@mail.beispiel.invalid:993``),
  ``FakeImapClient`` legt die Kopie nur im Speicher ab (Entwicklung und Tests).

Die Fakes verweigern sich in der Produktion (``DJANGO_ENV=prod``), damit dort nichts
als versendet gilt, was nirgends angekommen ist.
"""

from __future__ import annotations

import imaplib
import os
import time
import urllib.parse
from dataclasses import dataclass
from typing import Any, ClassVar, Protocol

from django.conf import settings
from django.core.mail import EmailMessage, get_connection
from django.utils.module_loading import import_string


class VersandFehler(RuntimeError):
    """Der Versand ist gescheitert. Die Meldung nennt keine Personendaten."""


class VersandNichtEingerichtet(VersandFehler):
    pass


def einstellung(schluessel: str) -> Any:
    return settings.DOKUMENTE[schluessel]


def _fake_erlaubt(name: str) -> None:
    if os.environ.get("DJANGO_ENV", "").strip().lower() == "prod":
        raise VersandNichtEingerichtet(
            f"{name} ist ein Fake für Entwicklung und Tests und in der Produktion gesperrt."
        )


# --- SMTP ----------------------------------------------------------------------------


class MailClient(Protocol):
    def senden(self, nachricht: EmailMessage) -> None: ...


class DjangoMailClient:
    """Versand über das Mail-Backend von Django (SMTP, lokal Mailpit, in Tests locmem)."""

    def senden(self, nachricht: EmailMessage) -> None:
        verbindung = get_connection(fail_silently=False)
        try:
            anzahl = verbindung.send_messages([nachricht])
        except OSError as fehler:
            raise VersandFehler(f"SMTP: {type(fehler).__name__}") from fehler
        if anzahl != 1:
            raise VersandFehler("SMTP hat die Mail nicht angenommen.")


# --- IMAP ----------------------------------------------------------------------------


class ImapClient(Protocol):
    def in_gesendet_ablegen(self, roh: bytes) -> None: ...


@dataclass
class ImaplibClient:
    """Legt eine Kopie per IMAP-Append im Ordner „Gesendet“ ab (Flag \\Seen)."""

    url: str = ""
    ordner: str = ""

    def __post_init__(self) -> None:
        self.url = self.url or einstellung("IMAP_URL")
        self.ordner = self.ordner or einstellung("IMAP_ORDNER_GESENDET")
        if not self.url:
            # Schon beim Anlegen, also vor SMTP: Ohne Kopie geht keine Mail hinaus.
            raise VersandNichtEingerichtet(
                "Für die Kopie in „Gesendet“ fehlt HOF_IMAP_URL. Die Mail ging nicht hinaus."
            )

    def in_gesendet_ablegen(self, roh: bytes) -> None:
        teile = urllib.parse.urlsplit(self.url)
        if teile.scheme not in ("imap", "imaps") or not teile.hostname:
            raise VersandNichtEingerichtet("HOF_IMAP_URL muss mit imap:// oder imaps:// beginnen.")
        benutzer = urllib.parse.unquote(teile.username or "")
        passwort = urllib.parse.unquote(teile.password or "")
        try:
            verbindung: imaplib.IMAP4 = (
                imaplib.IMAP4_SSL(teile.hostname, teile.port or 993, timeout=30)
                if teile.scheme == "imaps"
                else imaplib.IMAP4(teile.hostname, teile.port or 143, timeout=30)
            )
            with verbindung:
                if teile.scheme == "imap":
                    verbindung.starttls()
                verbindung.login(benutzer, passwort)
                ergebnis, _ = verbindung.append(
                    self.ordner, r"(\Seen)", imaplib.Time2Internaldate(time.time()), roh
                )
                if ergebnis != "OK":
                    raise VersandFehler(f"IMAP-Append in {self.ordner}: {ergebnis}")
                verbindung.logout()
        except (OSError, imaplib.IMAP4.error) as fehler:
            raise VersandFehler(f"IMAP: {type(fehler).__name__}") from fehler


class FakeImapClient:
    """Legt die Kopie nur im Speicher ab. Tests lesen ``FakeImapClient.gesendet``."""

    gesendet: ClassVar[list[bytes]] = []

    def __init__(self) -> None:
        _fake_erlaubt("FakeImapClient")

    def in_gesendet_ablegen(self, roh: bytes) -> None:
        self.gesendet.append(roh)

    @classmethod
    def zuruecksetzen(cls) -> None:
        cls.gesendet.clear()


def mail_client() -> MailClient:
    klasse = import_string(einstellung("MAIL_CLIENT"))
    client: MailClient = klasse()
    return client


def imap_client() -> ImapClient:
    klasse = import_string(einstellung("IMAP_CLIENT"))
    client: ImapClient = klasse()
    return client


@dataclass(frozen=True)
class MailErgebnis:
    message_id: str
    #: Leer, wenn die Kopie in „Gesendet“ liegt; sonst der Grund (ohne Personendaten).
    kopie_fehler: str = ""


def mail_senden(nachricht: EmailMessage) -> MailErgebnis:
    """Sendet per SMTP und legt die Kopie in „Gesendet“ ab.

    Die Kopie entsteht aus derselben Nachricht (gleiche Message-ID und gleiches Datum).
    Scheitert SMTP, kommt ein ``VersandFehler`` und nichts ist hinausgegangen. Scheitert
    nur die Kopie, ist die Mail trotzdem versendet: Das Ergebnis nennt dann den Grund,
    damit der Aufrufer das Schreiben als gesendet führt und einen Arbeitsposten anlegt.
    """
    roh = nachricht.message()
    message_id = str(roh["Message-ID"])
    nachricht.extra_headers = {
        **nachricht.extra_headers,
        "Message-ID": message_id,
        "Date": str(roh["Date"]),
    }
    roh_bytes = nachricht.message().as_bytes()
    imap = imap_client()  # vor SMTP: fehlt die Einrichtung, geht nichts hinaus
    mail_client().senden(nachricht)
    try:
        imap.in_gesendet_ablegen(roh_bytes)
    except VersandFehler as fehler:
        return MailErgebnis(message_id, kopie_fehler=str(fehler))
    return MailErgebnis(message_id)
