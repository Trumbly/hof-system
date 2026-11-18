"""Das Rechnungspostfach (H6.1): ein eigenes Postfach, an das Rechnungen weitergeleitet werden.

Der Lauf ``belege_postfach`` liest es mit einem austauschbaren Client
(``DOKUMENTE["BELEGE_POSTFACH_CLIENT"]``):

- ``ImaplibPostfach`` spricht per IMAP mit dem Postfach (``HOF_BELEGE_IMAP_URL``, Ordner
  ``HOF_BELEGE_IMAP_ORDNER``). Die Verbindung baut ``versand.imap_verbinden`` auf, dieselbe
  wie für die Kopie in „Gesendet“ (Schritt 3c).
- ``FakePostfach`` hält Mails nur im Speicher (Entwicklung und Tests); er verweigert sich in
  der Produktion.

Gelesen werden ungelesene Mails. Danach markiert der Lauf sie als gelesen oder verschiebt
sie in einen Ordner (``HOF_BELEGE_NACH_DEM_LESEN`` = ``markieren`` oder ``verschieben``,
Zielordner ``HOF_BELEGE_ZIELORDNER``).
"""

from __future__ import annotations

import datetime
import email
import email.policy
import imaplib
from collections.abc import Iterator
from dataclasses import dataclass, field
from email.message import EmailMessage
from email.utils import parseaddr, parsedate_to_datetime
from typing import ClassVar, Protocol

from django.conf import settings
from django.utils import timezone
from django.utils.module_loading import import_string

from apps.dokumente.versand import (
    VersandFehler,
    VersandNichtEingerichtet,
    _fake_erlaubt,
    imap_verbinden,
)

MARKIEREN = "markieren"
VERSCHIEBEN = "verschieben"


class PostfachFehler(VersandFehler):
    """Das Postfach ließ sich nicht lesen. Die Meldung nennt keine Personendaten."""


class PostfachNichtEingerichtet(PostfachFehler, VersandNichtEingerichtet):
    pass


@dataclass(frozen=True)
class Postfachmail:
    """Eine Mail aus dem Postfach: Kennung im Ordner und Rohdaten."""

    uid: str
    roh: bytes


class PostfachClient(Protocol):
    def ungelesene(self) -> list[Postfachmail]: ...

    def erledigen(self, uids: list[str]) -> None:
        """Markiert die Mails als gelesen oder verschiebt sie (je nach Einstellung)."""
        ...


def einstellung(schluessel: str) -> str:
    wert: str = settings.DOKUMENTE[schluessel]
    return wert


def nach_dem_lesen() -> str:
    wert = einstellung("BELEGE_NACH_DEM_LESEN").strip().lower()
    if wert not in (MARKIEREN, VERSCHIEBEN):
        raise PostfachNichtEingerichtet(
            "HOF_BELEGE_NACH_DEM_LESEN muss „markieren“ oder „verschieben“ sein."
        )
    return wert


# --- imaplib -------------------------------------------------------------------------


@dataclass
class ImaplibPostfach:
    """Liest das Rechnungspostfach per IMAP."""

    url: str = ""
    ordner: str = ""

    def __post_init__(self) -> None:
        self.url = self.url or einstellung("BELEGE_IMAP_URL")
        self.ordner = self.ordner or einstellung("BELEGE_IMAP_ORDNER")
        if not self.url:
            raise PostfachNichtEingerichtet("Für das Rechnungspostfach fehlt HOF_BELEGE_IMAP_URL.")

    def ungelesene(self) -> list[Postfachmail]:
        mails: list[Postfachmail] = []
        try:
            with imap_verbinden(self.url, "HOF_BELEGE_IMAP_URL") as verbindung:
                self._ordner_waehlen(verbindung, readonly=True)
                ergebnis, daten = verbindung.uid("SEARCH", "UNSEEN")
                if ergebnis != "OK":
                    raise PostfachFehler(f"IMAP-Suche: {ergebnis}")
                for roh_uid in bytes(daten[0] or b"").split():
                    uid = roh_uid.decode("ascii")
                    # BODY.PEEK: Lesen setzt das Flag \\Seen noch nicht.
                    ergebnis, teile = verbindung.uid("FETCH", uid, "(BODY.PEEK[])")
                    if ergebnis != "OK":
                        raise PostfachFehler(f"IMAP-Abruf: {ergebnis}")
                    roh = next((t[1] for t in teile if isinstance(t, tuple) and len(t) > 1), b"")
                    mails.append(Postfachmail(uid=uid, roh=bytes(roh)))
        except (OSError, imaplib.IMAP4.error) as fehler:
            raise PostfachFehler(f"IMAP: {type(fehler).__name__}") from fehler
        return mails

    def erledigen(self, uids: list[str]) -> None:
        if not uids:
            return
        weg = nach_dem_lesen()
        menge = ",".join(uids)
        try:
            with imap_verbinden(self.url, "HOF_BELEGE_IMAP_URL") as verbindung:
                self._ordner_waehlen(verbindung, readonly=False)
                verbindung.uid("STORE", menge, "+FLAGS", r"(\Seen)")
                if weg == VERSCHIEBEN:
                    ziel = einstellung("BELEGE_ZIELORDNER")
                    ergebnis, _ = verbindung.uid("COPY", menge, ziel)
                    if ergebnis != "OK":
                        raise PostfachFehler(f"IMAP-Kopie nach {ziel}: {ergebnis}")
                    verbindung.uid("STORE", menge, "+FLAGS", r"(\Deleted)")
                    verbindung.expunge()
        except (OSError, imaplib.IMAP4.error) as fehler:
            raise PostfachFehler(f"IMAP: {type(fehler).__name__}") from fehler

    def _ordner_waehlen(self, verbindung: imaplib.IMAP4, *, readonly: bool) -> None:
        ergebnis, _ = verbindung.select(self.ordner, readonly=readonly)
        if ergebnis != "OK":
            raise PostfachFehler(f"IMAP-Ordner {self.ordner}: {ergebnis}")


# --- Fake ----------------------------------------------------------------------------


class FakePostfach:
    """Postfach im Speicher. Tests legen Mails mit ``einwerfen`` hinein und lesen
    ``FakePostfach.ordner`` (Ordnername -> Mails) und ``gelesen``."""

    #: Mails je Ordner, in Eingangsreihenfolge
    ordner: ClassVar[dict[str, list[Postfachmail]]] = {}
    #: UIDs mit Flag \\Seen
    gelesen: ClassVar[set[str]] = set()
    _naechste_uid: ClassVar[int] = 1

    def __init__(self) -> None:
        _fake_erlaubt("FakePostfach")

    @classmethod
    def einwerfen(cls, roh: bytes) -> str:
        uid = str(cls._naechste_uid)
        cls._naechste_uid += 1
        cls.ordner.setdefault(einstellung("BELEGE_IMAP_ORDNER"), []).append(Postfachmail(uid, roh))
        return uid

    @classmethod
    def zuruecksetzen(cls) -> None:
        cls.ordner.clear()
        cls.gelesen.clear()
        cls._naechste_uid = 1

    def ungelesene(self) -> list[Postfachmail]:
        eingang = self.ordner.get(einstellung("BELEGE_IMAP_ORDNER"), [])
        return [m for m in eingang if m.uid not in self.gelesen]

    def erledigen(self, uids: list[str]) -> None:
        weg = nach_dem_lesen()
        self.gelesen.update(uids)
        if weg == VERSCHIEBEN:
            quelle = self.ordner.setdefault(einstellung("BELEGE_IMAP_ORDNER"), [])
            ziel = self.ordner.setdefault(einstellung("BELEGE_ZIELORDNER"), [])
            ziel.extend(m for m in quelle if m.uid in uids)
            quelle[:] = [m for m in quelle if m.uid not in uids]


def postfach_client() -> PostfachClient:
    klasse = import_string(einstellung("BELEGE_POSTFACH_CLIENT"))
    client: PostfachClient = klasse()
    return client


# --- Mails lesen ---------------------------------------------------------------------


@dataclass(frozen=True)
class Anlage:
    inhalt: bytes
    dateityp: str = "application/pdf"


@dataclass(frozen=True)
class Rechnungsmail:
    """Was der Lauf aus einer Mail braucht: Kennung, Absender, Datum und PDF-Anlagen."""

    uid: str
    message_id: str
    absender: str
    datum: datetime.date
    anlagen: list[Anlage] = field(default_factory=list)


def _ist_pdf(teil: EmailMessage) -> bool:
    typ = teil.get_content_type()
    name = (teil.get_filename() or "").lower()
    return typ == "application/pdf" or (
        typ in ("application/octet-stream", "application/x-pdf") and name.endswith(".pdf")
    )


def _teile(nachricht: EmailMessage) -> Iterator[EmailMessage]:
    for teil in nachricht.walk():
        if isinstance(teil, EmailMessage) and not teil.is_multipart():
            yield teil


def _datum(nachricht: EmailMessage) -> datetime.date:
    try:
        zeit = parsedate_to_datetime(str(nachricht["Date"] or ""))
    except (TypeError, ValueError):
        return timezone.localdate()
    if zeit.tzinfo is None:
        return zeit.date()
    return timezone.localdate(zeit)


def lesen(mail: Postfachmail) -> Rechnungsmail:
    """Zerlegt eine Mail. PDF-Anlagen sind alle Teile vom Typ PDF (auch eingebettete)."""
    nachricht = email.message_from_bytes(mail.roh, policy=email.policy.default)
    assert isinstance(nachricht, EmailMessage)  # noqa: S101  (policy.default liefert das)
    anlagen = []
    for teil in _teile(nachricht):
        if not _ist_pdf(teil):
            continue
        inhalt = teil.get_payload(decode=True)
        if isinstance(inhalt, bytes) and inhalt.startswith(b"%PDF-"):
            anlagen.append(Anlage(inhalt=inhalt))
    return Rechnungsmail(
        uid=mail.uid,
        message_id="".join(str(nachricht["Message-ID"] or "").split())[:255],
        absender=parseaddr(str(nachricht["From"] or ""))[1].strip().lower(),
        datum=_datum(nachricht),
        anlagen=anlagen,
    )
