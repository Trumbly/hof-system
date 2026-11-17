"""Briefversand über die REST-API von LetterXpress (API v3, Doku Stand Juli 2026).

Grundlage ist die öffentliche Dokumentation (letterxpress.de, „Dokumentation
LetterXpress API v3“):

- Basis ``https://api.letterxpress.de/v3``, JSON, jede Anfrage trägt
  ``{"auth": {"username", "apikey", "mode": "test"|"live"}}``; auch GET und DELETE
  schicken diesen Körper. Im Modus ``test`` landen Aufträge nur in der Postbox.
- ``POST /printjobs`` mit ``letter``: ``base64_file``, ``base64_file_checksum`` (MD5 des
  Base64-Texts), ``specification`` (``color`` "1"/"4", ``mode`` simplex/duplex,
  ``shipping`` national/international/auto), optional ``registered`` ("r1" =
  Einwurf-Einschreiben, "r2" = Einschreiben), ``filename_original``, ``notice``.
- ``GET /printjobs/{id}``: ``status`` (queue, hold, done, canceled, draft) und ``items``
  mit ``status``, ``tracking_code`` (Sendungsnummer) und ``tracking_status``.
- Die Anschrift liest LetterXpress aus dem Anschriftfeld des PDFs (DIN 5008).

Der Client ist austauschbar (``DOKUMENTE["BRIEF_CLIENT"]``). ``FakeLetterXpressClient``
hält Aufträge im Speicher; gegen die Testumgebung oder die echte API geht der Client nur
mit ausdrücklicher Freigabe von Max (Zugangsdaten in der Umgebung, Modus ``test``).
"""

from __future__ import annotations

import base64
import datetime
import hashlib
import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, ClassVar, Protocol

from apps.dokumente.versand import (
    VersandFehler,
    VersandNichtEingerichtet,
    _fake_erlaubt,
    einstellung,
)

BASIS_URL = "https://api.letterxpress.de/v3"

#: Einschreiben-Arten der API.
EINWURF_EINSCHREIBEN = "r1"
EINSCHREIBEN = "r2"


class LetterXpressFehler(VersandFehler):
    pass


@dataclass(frozen=True)
class Auftrag:
    """Stand eines Druckauftrags bei LetterXpress."""

    auftrag_id: str
    status: str  # queue, hold, done, canceled, draft
    sendungsnummer: str = ""
    sendungsstatus: str = ""
    einlieferungsdatum: datetime.date | None = None

    @property
    def zugestellt(self) -> bool:
        return self.sendungsstatus.strip().lower().startswith("zugestellt")


class BriefClient(Protocol):
    def auftrag_senden(
        self, pdf: bytes, *, einschreiben: str | None, dateiname: str, notiz: str
    ) -> Auftrag: ...

    def auftrag_abrufen(self, auftrag_id: str) -> Auftrag: ...


def _datum(text: Any) -> datetime.date | None:
    if not text:
        return None
    try:
        return datetime.datetime.strptime(str(text)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def auftrag_aus_antwort(daten: dict[str, Any]) -> Auftrag:
    """Liest ``data`` einer Antwort von ``/printjobs`` oder ``/printjobs/{id}``.

    Annahme: Das Einlieferungsdatum ist der Tag, an dem der Auftrag „done“ wurde und die
    Sendung „sent“ ist (``updated_at``); die API nennt kein eigenes Feld dafür.
    """
    posten = (daten.get("items") or [{}])[0]
    status = str(daten.get("status") or "")
    eingeliefert = None
    if status == "done" and posten.get("status") == "sent":
        eingeliefert = _datum(daten.get("dispatch_date")) or _datum(daten.get("updated_at"))
    sendungsstatus = posten.get("tracking_status") or ""
    return Auftrag(
        auftrag_id=str(daten["id"]),
        status=status,
        sendungsnummer=str(posten.get("tracking_code") or ""),
        sendungsstatus="" if sendungsstatus == "--" else str(sendungsstatus),
        einlieferungsdatum=eingeliefert,
    )


def letter(pdf: bytes, *, einschreiben: str | None, dateiname: str, notiz: str) -> dict[str, Any]:
    """Das ``letter``-Objekt für ``POST /printjobs``."""
    if einschreiben not in (None, EINWURF_EINSCHREIBEN, EINSCHREIBEN):
        raise ValueError(f"Unbekannte Einschreiben-Art {einschreiben}")
    datei = base64.b64encode(pdf).decode("ascii")
    brief: dict[str, Any] = {
        "base64_file": datei,
        # MD5 als Prüfsumme verlangt die API; keine Sicherheitsfunktion.
        "base64_file_checksum": hashlib.md5(
            datei.encode("ascii"), usedforsecurity=False
        ).hexdigest(),
        "specification": {"color": "1", "mode": "simplex", "shipping": "national"},
        "filename_original": dateiname,
        "notice": notiz[:255],
    }
    if einschreiben:
        brief["registered"] = einschreiben
    return brief


@dataclass
class LetterXpressClient:
    """Spricht mit der API v3. Zugangsdaten aus ``DOKUMENTE`` (Umgebung)."""

    benutzer: str = ""
    apikey: str = ""
    modus: str = ""
    basis_url: str = BASIS_URL
    timeout: float = 60.0

    def __post_init__(self) -> None:
        self.benutzer = self.benutzer or einstellung("LETTERXPRESS_BENUTZER")
        self.apikey = self.apikey or einstellung("LETTERXPRESS_APIKEY")
        self.modus = self.modus or einstellung("LETTERXPRESS_MODUS")
        if not self.benutzer or not self.apikey:
            raise VersandNichtEingerichtet(
                "LetterXpress ist nicht eingerichtet (LETTERXPRESS_BENUTZER, LETTERXPRESS_APIKEY)."
            )
        if self.modus not in ("test", "live"):
            raise VersandNichtEingerichtet("LETTERXPRESS_MODUS muss „test“ oder „live“ sein.")

    def _anfrage(self, methode: str, pfad: str, koerper: dict[str, Any] | None = None) -> Any:
        daten = {
            "auth": {"username": self.benutzer, "apikey": self.apikey, "mode": self.modus},
            **(koerper or {}),
        }
        anfrage = urllib.request.Request(  # noqa: S310  (feste https-Adresse)
            self.basis_url + pfad,
            data=json.dumps(daten).encode("utf-8"),
            method=methode,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(anfrage, timeout=self.timeout) as antwort:  # noqa: S310
                inhalt = json.loads(antwort.read().decode("utf-8"))
        except urllib.error.HTTPError as fehler:
            raise LetterXpressFehler(
                f"LetterXpress {methode} {pfad}: HTTP {fehler.code}"
            ) from fehler
        except (OSError, ValueError) as fehler:
            raise LetterXpressFehler(
                f"LetterXpress {methode} {pfad}: {type(fehler).__name__}"
            ) from fehler
        if not isinstance(inhalt, dict) or inhalt.get("status") != 200:
            status = inhalt.get("status") if isinstance(inhalt, dict) else "?"
            raise LetterXpressFehler(f"LetterXpress {methode} {pfad}: Status {status}")
        return inhalt.get("data")

    def auftrag_senden(
        self, pdf: bytes, *, einschreiben: str | None, dateiname: str, notiz: str
    ) -> Auftrag:
        daten = self._anfrage(
            "POST",
            "/printjobs",
            {"letter": letter(pdf, einschreiben=einschreiben, dateiname=dateiname, notiz=notiz)},
        )
        return auftrag_aus_antwort(daten)

    def auftrag_abrufen(self, auftrag_id: str) -> Auftrag:
        if not auftrag_id.isdigit():
            raise ValueError("Auftragsnummern von LetterXpress sind Zahlen.")
        return auftrag_aus_antwort(self._anfrage("GET", f"/printjobs/{auftrag_id}"))


@dataclass
class _FakeAuftrag:
    daten: dict[str, Any]
    letter: dict[str, Any] = field(repr=False)


class FakeLetterXpressClient:
    """Nimmt Aufträge an wie die API, ohne Netz. Für Entwicklung und Tests.

    - ``auftrag_senden`` prüft das ``letter``-Objekt wie die API (Prüfsumme, Felder) und
      legt einen Auftrag im Status „queue“ an.
    - ``einliefern(id, am)`` setzt ihn auf „done“/„sent“ mit Sendungsnummer (bei
      Einschreiben), ``zustellen(id, am)`` setzt die Sendungsverfolgung auf „Zugestellt“.
    - ``auftraege`` hält alle angenommenen Aufträge (für Tests).
    """

    auftraege: ClassVar[dict[str, _FakeAuftrag]] = {}
    _naechste: ClassVar[int] = 6_035_000

    def __init__(self) -> None:
        _fake_erlaubt("FakeLetterXpressClient")

    @classmethod
    def zuruecksetzen(cls) -> None:
        cls.auftraege.clear()

    def auftrag_senden(
        self, pdf: bytes, *, einschreiben: str | None, dateiname: str, notiz: str
    ) -> Auftrag:
        brief = letter(pdf, einschreiben=einschreiben, dateiname=dateiname, notiz=notiz)
        if not pdf.startswith(b"%PDF"):
            raise LetterXpressFehler("LetterXpress (Fake): Die Datei ist kein PDF.")
        pruefsumme = hashlib.md5(brief["base64_file"].encode(), usedforsecurity=False).hexdigest()
        if brief["base64_file_checksum"] != pruefsumme:
            raise LetterXpressFehler("LetterXpress (Fake): Prüfsumme passt nicht.")
        type(self)._naechste += 1
        auftrag_id = str(type(self)._naechste)
        heute = datetime.date.today().isoformat()
        daten: dict[str, Any] = {
            "id": int(auftrag_id),
            "shipping": "national",
            "mode": "simplex",
            "color": "1",
            "registered": einschreiben,
            "notice": brief["notice"],
            "status": "queue",
            "dispatch_date": None,
            "filename_original": dateiname,
            "created_at": f"{heute} 08:00:00",
            "updated_at": f"{heute} 08:00:00",
            "items": [
                {"pages": 1, "status": "queue", "tracking_code": None, "tracking_status": "--"}
            ],
        }
        self.auftraege[auftrag_id] = _FakeAuftrag(daten=daten, letter=brief)
        return auftrag_aus_antwort(daten)

    def auftrag_abrufen(self, auftrag_id: str) -> Auftrag:
        try:
            return auftrag_aus_antwort(self.auftraege[auftrag_id].daten)
        except KeyError as fehler:
            raise LetterXpressFehler(
                f"LetterXpress (Fake): Auftrag {auftrag_id} fehlt."
            ) from fehler

    @classmethod
    def einliefern(cls, auftrag_id: str, am: datetime.date) -> None:
        daten = cls.auftraege[auftrag_id].daten
        daten["status"] = "done"
        daten["updated_at"] = f"{am.isoformat()} 10:00:00"
        posten = daten["items"][0]
        posten["status"] = "sent"
        if daten["registered"]:
            posten["tracking_code"] = f"RR{int(auftrag_id):09d}DE"

    @classmethod
    def zustellen(cls, auftrag_id: str, am: datetime.date) -> None:
        posten = cls.auftraege[auftrag_id].daten["items"][0]
        posten["tracking_status"] = (
            f"Zugestellt: Die Sendung wurde am {am:%d.%m.%y} erfolgreich zugestellt."
        )


def brief_client() -> BriefClient:
    from django.utils.module_loading import import_string

    klasse = import_string(einstellung("BRIEF_CLIENT"))
    client: BriefClient = klasse()
    return client
