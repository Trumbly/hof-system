"""Kanäle der Benachrichtigung: ntfy und Mail, ohne Netz (Schritt 9, Aufgabe 5)."""

from __future__ import annotations

import json
import smtplib
import urllib.error
import urllib.request
from typing import Any

import pytest
from django.core import mail

from apps.benachrichtigung import kanaele
from apps.benachrichtigung.kanaele import KanalFehler, MailKanal, Nachricht, NtfyKanal

NACHRICHT = Nachricht(
    titel="Hof-System: 1 Lauf gescheitert",
    text="Gescheiterte Läufe (letzte 24 Stunden): 1\n- fristen: 1",
    link="https://hof-system.testhof.invalid/laeufe/",
)


class _Antwort:
    def __init__(self, status: int) -> None:
        self.status = status

    def __enter__(self) -> _Antwort:
        return self

    def __exit__(self, *args: object) -> None:
        return None


def test_ntfy_sendet_json_mit_thema_titel_link_und_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """Schritt 9, Aufgabe 5: Push über ntfy als JSON an den Server, mit privatem Thema und
    Token; Umlaute stehen im JSON, nicht in Kopfzeilen."""
    anfragen: list[urllib.request.Request] = []

    def urlopen(anfrage: urllib.request.Request, timeout: float) -> _Antwort:
        anfragen.append(anfrage)
        return _Antwort(200)

    monkeypatch.setattr(kanaele.urllib.request, "urlopen", urlopen)
    NtfyKanal("https://ntfy.testhof.invalid/", "hof-privat", "tk_nur_fuer_tests").senden(NACHRICHT)

    anfrage = anfragen[0]
    assert anfrage.full_url == "https://ntfy.testhof.invalid/"
    assert anfrage.get_method() == "POST"
    assert anfrage.get_header("Authorization") == "Bearer tk_nur_fuer_tests"
    assert anfrage.get_header("Content-type") == "application/json"
    daten: dict[str, Any] = json.loads(anfrage.data)  # type: ignore[arg-type]
    assert daten == {
        "topic": "hof-privat",
        "title": NACHRICHT.titel,
        "message": NACHRICHT.text,
        "click": NACHRICHT.link,
        "tags": ["warning"],
    }


def test_ntfy_ohne_token_ohne_kopfzeile() -> None:
    anfrage = NtfyKanal("http://ntfy.testhof.invalid", "thema").anfrage(NACHRICHT)
    assert anfrage.get_header("Authorization") is None
    assert anfrage.full_url == "http://ntfy.testhof.invalid/"


@pytest.mark.parametrize(
    "ausnahme", [urllib.error.URLError("nicht erreichbar"), TimeoutError("zu langsam")]
)
def test_ntfy_ausfall_wird_kanalfehler(
    monkeypatch: pytest.MonkeyPatch, ausnahme: Exception
) -> None:
    def urlopen(anfrage: urllib.request.Request, timeout: float) -> _Antwort:
        raise ausnahme

    monkeypatch.setattr(kanaele.urllib.request, "urlopen", urlopen)
    with pytest.raises(KanalFehler, match="ntfy"):
        NtfyKanal("https://ntfy.testhof.invalid", "thema").senden(NACHRICHT)


def test_ntfy_fehlerstatus_wird_kanalfehler(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(kanaele.urllib.request, "urlopen", lambda a, timeout: _Antwort(302))
    with pytest.raises(KanalFehler, match="HTTP 302"):
        NtfyKanal("https://ntfy.testhof.invalid", "thema").senden(NACHRICHT)


@pytest.mark.parametrize("url", ["file:///tmp/x", "ntfy.testhof.invalid", "ftp://x.invalid"])
def test_ntfy_nur_http_und_https(url: str) -> None:
    with pytest.raises(ValueError, match="http"):
        NtfyKanal(url, "thema")


def test_mail_an_max() -> None:
    MailKanal("max@testhof.invalid").senden(NACHRICHT)

    gesendet = mail.outbox[0]
    assert gesendet.to == ["max@testhof.invalid"]
    assert gesendet.subject == NACHRICHT.titel
    assert gesendet.body == NACHRICHT.text


def test_mail_ausfall_wird_kanalfehler(monkeypatch: pytest.MonkeyPatch) -> None:
    def send_mail(*args: object, **kwargs: object) -> int:
        raise smtplib.SMTPServerDisconnected("weg")

    monkeypatch.setattr(kanaele, "send_mail", send_mail)
    with pytest.raises(KanalFehler, match="Mail"):
        MailKanal("max@testhof.invalid").senden(NACHRICHT)
