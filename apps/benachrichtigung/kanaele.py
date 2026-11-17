"""Kanäle der Benachrichtigung: Mail an Max und Push über ntfy, dazu Fakes für Tests.

Jeder Kanal hat ``name``, ``senden(nachricht)`` und die Klassenmethode
``aus_einstellungen()``, die eine fertige Instanz liefert oder ``None``, wenn der Kanal
nicht eingerichtet ist. Welche Klasse gilt, steht in den Einstellungen
``HOF_BENACHRICHTIGUNG_MAIL_KANAL`` und ``HOF_BENACHRICHTIGUNG_NTFY_KANAL``; in den
Tests sind es Fakes, damit nichts ins Netz geht.
"""

from __future__ import annotations

import json
import logging
import smtplib
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import ClassVar, Protocol

from django.conf import settings
from django.core.mail import send_mail
from django.utils.module_loading import import_string

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Nachricht:
    """Eine Benachrichtigung. Enthält nur Zähler, Lauf-Namen, Arten und Links."""

    titel: str
    text: str
    link: str


class KanalFehler(Exception):
    """Der Kanal konnte nicht senden. Die Meldung nennt keine Inhalte."""


class Kanal(Protocol):
    name: str

    def senden(self, nachricht: Nachricht) -> None: ...


class MailKanal:
    """Mail an Max über das Mail-Backend von Django (SMTP; lokal Mailpit)."""

    name = "mail"

    def __init__(self, empfaenger: str) -> None:
        self.empfaenger = empfaenger

    @classmethod
    def aus_einstellungen(cls) -> MailKanal | None:
        empfaenger: str = settings.HOF_BENACHRICHTIGUNG_MAIL
        return cls(empfaenger) if empfaenger else None

    def senden(self, nachricht: Nachricht) -> None:
        try:
            send_mail(
                nachricht.titel,
                nachricht.text,
                None,  # DEFAULT_FROM_EMAIL
                [self.empfaenger],
                fail_silently=False,
            )
        except (smtplib.SMTPException, OSError) as fehler:
            raise KanalFehler(f"Mail nicht gesendet: {type(fehler).__name__}") from fehler


class NtfyKanal:
    """Push über ntfy (selbst gehostet oder ntfy.sh mit privatem Thema).

    Sendet als JSON an die Wurzel des Servers (``POST <server>/`` mit ``topic``,
    ``title``, ``message``, ``click``), damit Umlaute nicht in HTTP-Kopfzeilen stehen.
    Mit Token als ``Authorization: Bearer <token>``.
    """

    name = "ntfy"
    timeout_sekunden = 10

    def __init__(self, server_url: str, thema: str, token: str = "") -> None:
        teile = urllib.parse.urlsplit(server_url)
        if teile.scheme not in ("http", "https") or not teile.netloc:
            raise ValueError("Die ntfy-Adresse muss mit http:// oder https:// beginnen.")
        self.server_url = server_url.rstrip("/") + "/"
        self.thema = thema
        self.token = token

    @classmethod
    def aus_einstellungen(cls) -> NtfyKanal | None:
        url: str = settings.HOF_NTFY_URL
        thema: str = settings.HOF_NTFY_THEMA
        if not (url and thema):
            return None
        return cls(url, thema, settings.HOF_NTFY_TOKEN)

    def anfrage(self, nachricht: Nachricht) -> urllib.request.Request:
        """Die HTTP-Anfrage an ntfy (eigene Methode, damit Tests sie prüfen können)."""
        daten = {
            "topic": self.thema,
            "title": nachricht.titel,
            "message": nachricht.text,
            "tags": ["warning"],
        }
        if nachricht.link:
            daten["click"] = nachricht.link
        kopf = {"Content-Type": "application/json"}
        if self.token:
            kopf["Authorization"] = f"Bearer {self.token}"
        return urllib.request.Request(  # noqa: S310  (Schema oben auf http/https geprüft)
            self.server_url,
            data=json.dumps(daten).encode("utf-8"),
            headers=kopf,
            method="POST",
        )

    def senden(self, nachricht: Nachricht) -> None:
        try:
            with urllib.request.urlopen(  # noqa: S310  (Schema geprüft)
                self.anfrage(nachricht), timeout=self.timeout_sekunden
            ) as antwort:
                status = antwort.status
        except (urllib.error.URLError, OSError) as fehler:
            raise KanalFehler(f"ntfy nicht erreicht: {type(fehler).__name__}") from fehler
        if status >= 300:
            raise KanalFehler(f"ntfy antwortet mit HTTP {status}")


# --- Fakes für Tests ---------------------------------------------------------------


class FakeKanal:
    """Sendet nicht, sondern merkt sich die Nachrichten in ``FakeKanal.gesendet``.

    ``scheitern=True`` simuliert einen Ausfall des Kanals.
    """

    gesendet: ClassVar[list[tuple[str, Nachricht]]] = []

    def __init__(self, name: str = "fake", *, scheitern: bool = False) -> None:
        self.name = name
        self.scheitern = scheitern

    @classmethod
    def aus_einstellungen(cls) -> FakeKanal | None:
        return cls()

    def senden(self, nachricht: Nachricht) -> None:
        if self.scheitern:
            raise KanalFehler(f"{self.name}: simulierter Ausfall")
        FakeKanal.gesendet.append((self.name, nachricht))


class FakeNtfyKanal(FakeKanal):
    """Fake für ntfy: aktiv, wenn Adresse und Thema eingestellt sind, wie der echte."""

    @classmethod
    def aus_einstellungen(cls) -> FakeNtfyKanal | None:
        if not (settings.HOF_NTFY_URL and settings.HOF_NTFY_THEMA):
            return None
        return cls("ntfy")


def kanaele_aus_einstellungen() -> list[Kanal]:
    """Die eingerichteten Kanäle, in der Reihenfolge Mail, ntfy."""
    kanaele: list[Kanal] = []
    for pfad in (
        settings.HOF_BENACHRICHTIGUNG_MAIL_KANAL,
        settings.HOF_BENACHRICHTIGUNG_NTFY_KANAL,
    ):
        kanal = import_string(pfad).aus_einstellungen()
        if kanal is not None:
            kanaele.append(kanal)
    return kanaele
