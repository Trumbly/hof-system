"""Lauf ``belege_postfach``: Rechnungspostfach per IMAP, mit Fake (Schritt 3a, Aufgabe 3)."""

from __future__ import annotations

import datetime
import io
from decimal import Decimal

import pytest
from django.conf import settings
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings

from apps.dokumente import postfach, services
from apps.dokumente.models import Beleg, Dokument
from apps.dokumente.postfach import FakePostfach, ImaplibPostfach, PostfachNichtEingerichtet
from apps.dokumente.versand import VersandNichtEingerichtet
from apps.kern.models import Arbeitsposten, Lauf
from tests.dokumente.conftest import Belegwelt, rechnung_pdf, rechnungsmail

pytestmark = pytest.mark.django_db


def _lauf() -> Lauf:
    call_command("belege_postfach", stdout=io.StringIO())
    return Lauf.objects.filter(name="belege_postfach").first()  # type: ignore[return-value]


def test_jede_pdf_anlage_wird_ein_dokument_mit_arbeitsposten(belegwelt: Belegwelt) -> None:
    """Schritt 3a, Aufgabe 3: Jede PDF-Anlage wird ein Dokument der Art „Beleg“, jeder neue
    Beleg ein Arbeitsposten „Belege zuordnen“; die Mail ist danach als gelesen markiert."""
    uid = FakePostfach.einwerfen(
        rechnungsmail(rechnung_pdf("R-1"), rechnung_pdf("R-2"), absender="fremd@x.invalid")
    )

    lauf = _lauf()

    assert (lauf.status, lauf.gebucht, lauf.arbeitsposten, lauf.fehler) == ("erfolgreich", 2, 2, 0)
    assert lauf.meldung == "1 Mails, 2 neue Belege, 0 Dubletten, 0 ohne PDF"
    dokumente = list(Dokument.objects.order_by("pk"))
    assert [d.art for d in dokumente] == ["beleg", "beleg"]
    assert {d.herkunft for d in dokumente} == {Dokument.Herkunft.POSTFACH}
    assert {d.datum for d in dokumente} == {datetime.date(2026, 11, 17)}
    assert {d.eingangskennung for d in dokumente} == {"<r1@lieferant.testhof.invalid>"}
    belege = list(Beleg.objects.order_by("pk"))
    assert [b.status for b in belege] == ["offen", "offen"]
    assert all(b.lieferant is None for b in belege)  # Absender unbekannt
    posten = Arbeitsposten.objects.filter(art=services.ART_BELEGE_ZUORDNEN, status="offen")
    assert {p.bezug for p in posten} == set(belege)
    assert all(p.lauf == lauf for p in posten)
    assert all("Lieferant fehlt" in p.hinweis for p in posten)
    assert FakePostfach.gelesen == {uid}


def test_dubletten_werden_per_hash_erkannt(belegwelt: Belegwelt) -> None:
    """Schritt 3a, Aufgabe 3: Kommt dieselbe Rechnung noch einmal, entsteht nichts Neues."""
    FakePostfach.einwerfen(rechnungsmail(rechnung_pdf("R-1")))
    _lauf()
    FakePostfach.einwerfen(
        rechnungsmail(rechnung_pdf("R-1"), message_id="<noch-mal@lieferant.testhof.invalid>")
    )
    lauf = _lauf()
    assert lauf.meldung == "1 Mails, 0 neue Belege, 1 Dubletten, 0 ohne PDF"
    assert Dokument.objects.count() == 1
    assert Beleg.objects.count() == 1
    assert Arbeitsposten.objects.filter(art=services.ART_BELEGE_ZUORDNEN).count() == 1
    assert len(FakePostfach.gelesen) == 2


def test_gelesene_mails_bleiben_beim_naechsten_lauf_liegen(belegwelt: Belegwelt) -> None:
    """Schritt 3a: Ein zweiter Lauf liest nur Ungelesenes."""
    FakePostfach.einwerfen(rechnungsmail(rechnung_pdf("R-1")))
    _lauf()
    lauf = _lauf()
    assert lauf.meldung == "0 Mails, 0 neue Belege, 0 Dubletten, 0 ohne PDF"


def test_verschieben_statt_markieren_ist_einstellbar(belegwelt: Belegwelt) -> None:
    """Schritt 3a: Gelesene Mails werden markiert oder in einen Ordner verschoben
    (HOF_BELEGE_NACH_DEM_LESEN, HOF_BELEGE_ZIELORDNER)."""
    neu = {**settings.DOKUMENTE, "BELEGE_NACH_DEM_LESEN": "verschieben"}
    with override_settings(DOKUMENTE=neu):
        uid = FakePostfach.einwerfen(rechnungsmail(rechnung_pdf("R-1")))
        _lauf()
    assert FakePostfach.ordner["INBOX"] == []
    assert [m.uid for m in FakePostfach.ordner["Abgelegt"]] == [uid]


def test_unbekannte_einstellung_bricht_ab(belegwelt: Belegwelt) -> None:
    neu = {**settings.DOKUMENTE, "BELEGE_NACH_DEM_LESEN": "loeschen"}
    FakePostfach.einwerfen(rechnungsmail(rechnung_pdf("R-1")))
    with override_settings(DOKUMENTE=neu), pytest.raises(CommandError):
        _lauf()
    # Die Ablage ist gespeichert, die Mail bleibt ungelesen: Der nächste Lauf erkennt die
    # Dublette.
    assert Dokument.objects.count() == 1
    assert FakePostfach.gelesen == set()


def test_bekannter_absender_bringt_lieferant_und_vorschlag(belegwelt: Belegwelt) -> None:
    """Standardablauf 6, Schritt 2: Das System schlägt Objekt, Kostenart und Leistungsjahr
    aus früheren Belegen des Lieferanten vor; der Lieferant ist an der Absenderadresse erkannt."""
    frueher = services.beleg_hochladen(
        rechnung_pdf("R-0"),
        datum=datetime.date(2025, 11, 2),
        lieferant=belegwelt.lieferant,
        betrag=Decimal("80.00"),
        objekt=belegwelt.halle,
        kategorie=belegwelt.versicherung,
        leistungsjahr=2025,
        umlagefaehiger_anteil=Decimal("40"),
    ).beleg
    assert frueher is not None and frueher.status == "bestaetigt"
    FakePostfach.einwerfen(rechnungsmail(rechnung_pdf("R-1")))

    _lauf()

    beleg = Beleg.objects.get(status="offen")
    assert beleg.lieferant == belegwelt.lieferant
    assert (beleg.objekt, beleg.kategorie, beleg.leistungsjahr) == (
        belegwelt.halle,
        belegwelt.versicherung,
        2026,
    )
    assert beleg.umlagefaehiger_anteil == Decimal("40.00")
    posten = Arbeitsposten.objects.get(art=services.ART_BELEGE_ZUORDNEN, status="offen")
    assert "Lieferant erkannt" in posten.hinweis and "Vorschlag" in posten.hinweis
    assert belegwelt.lieferant.name not in posten.hinweis


def test_mail_ohne_pdf_wird_arbeitsposten(belegwelt: Belegwelt) -> None:
    """Schritt 3a: Eine Mail ohne lesbares PDF geht nicht verloren."""
    FakePostfach.einwerfen(rechnungsmail())
    FakePostfach.einwerfen(rechnungsmail(b"%PDF-1.4 kaputt", message_id="<kaputt@x.invalid>"))
    lauf = _lauf()
    assert lauf.meldung == "2 Mails, 0 neue Belege, 0 Dubletten, 2 ohne PDF"
    posten = Arbeitsposten.objects.filter(art=services.ART_MAIL_OHNE_PDF)
    assert posten.count() == 2
    assert all(p.hinweis.startswith("Mail vom 17.11.2026") for p in posten)
    assert not Dokument.objects.exists()


def test_pdf_als_octet_stream_wird_erkannt(belegwelt: Belegwelt) -> None:
    """Manche Mailprogramme schicken PDFs als application/octet-stream mit Endung .pdf."""
    FakePostfach.einwerfen(rechnungsmail(rechnung_pdf("R-9"), pdf_als_octet_stream=True))
    assert _lauf().gebucht == 1


def test_bricht_der_lauf_ab_bleiben_die_mails_ungelesen(
    belegwelt: Belegwelt, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Schritt 3a: Markiert wird erst nach dem Speichern. Scheitert der Lauf, ist nichts
    abgelegt und keine Mail markiert."""
    FakePostfach.einwerfen(rechnungsmail(rechnung_pdf("R-1")))

    def kaputt(*args: object, **kwargs: object) -> None:
        raise RuntimeError("Absturz")

    monkeypatch.setattr(services, "beleg_anlegen", kaputt)
    with pytest.raises(CommandError):
        _lauf()
    assert not Dokument.objects.exists()
    assert FakePostfach.gelesen == set()
    assert Lauf.objects.get(name="belege_postfach").status == Lauf.Status.ABGEBROCHEN


def test_imaplib_postfach_ohne_url_ist_nicht_eingerichtet() -> None:
    """Schritt 3a: Der echte Client meldet ohne HOF_BELEGE_IMAP_URL „nicht eingerichtet“."""
    with pytest.raises(PostfachNichtEingerichtet):
        ImaplibPostfach()


def test_fake_postfach_verweigert_sich_in_der_produktion(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DJANGO_ENV", "prod")
    with pytest.raises(VersandNichtEingerichtet):
        FakePostfach()


def test_mail_lesen_ohne_datum_nimmt_heute() -> None:
    mail = postfach.Postfachmail("7", rechnungsmail(rechnung_pdf(), datum="kein Datum"))
    gelesen = postfach.lesen(mail)
    assert gelesen.absender == "rechnung@lieferant.testhof.invalid"
    assert len(gelesen.anlagen) == 1
    assert isinstance(gelesen.datum, datetime.date)
