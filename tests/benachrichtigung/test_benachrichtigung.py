"""Benachrichtigung (Schritt 9, Aufgabe 5, H11.1)."""

from __future__ import annotations

import datetime
import re
from collections.abc import Iterator
from typing import Any

import pytest
from django.core import mail
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings
from django.utils import timezone

from apps.benachrichtigung import services
from apps.benachrichtigung.checks import benachrichtigung_eingerichtet
from apps.benachrichtigung.kanaele import FakeKanal, Nachricht
from apps.benachrichtigung.models import Benachrichtigung
from apps.fristen import services as fristen
from apps.fristen.models import Frist
from apps.kern import personenfelder
from apps.kern import services as kern_services
from apps.kern.laeufe import LaufBefehl
from apps.kern.models import Arbeitsposten, Lauf
from testhof.aufbau import STICHTAG, Testhof
from tests.factories import MandantFabrik, ObjektFabrik

pytestmark = pytest.mark.django_db

BASIS = "https://hof-system.testhof.invalid"


class AbbrechenderLauf(LaufBefehl):
    lauf_name = "bank_abruf"

    def ausfuehren(self, **optionen: Any) -> None:
        mandant = MandantFabrik()
        raise RuntimeError(f"Fehler bei {mandant.bezeichnung}")


class LaufMitFehlern(LaufBefehl):
    lauf_name = "abgleich"

    def ausfuehren(self, **optionen: Any) -> str:
        self.fehler(2)
        return "2 Fehler"


@pytest.fixture(autouse=True)
def _fakes_leeren() -> Iterator[None]:
    FakeKanal.gesendet.clear()
    yield
    FakeKanal.gesendet.clear()


def _scheitern() -> None:
    with pytest.raises(CommandError):
        call_command(AbbrechenderLauf())


def _frist_faellig() -> Frist:
    frist = fristen.frist_anlegen(
        Frist.Art.SONSTIGE,
        timezone.localdate() - datetime.timedelta(days=1),
        Frist.Quelle.HAND,
        ObjektFabrik(),
    )
    call_command("fristen")
    return frist


def _gesendet() -> list[tuple[str, str, str]]:
    """(Kanal, Titel, Text) aller gesendeten Nachrichten: Mail und Fake-ntfy."""
    mails = [("mail", m.subject, str(m.body)) for m in mail.outbox]
    return mails + [(k, n.titel, n.text) for k, n in FakeKanal.gesendet]


# --- Abnahme ---------------------------------------------------------------------------


def test_still_ohne_gescheiterten_lauf_und_ohne_wartende_freigabe() -> None:
    """Abnahme Schritt 9: Ohne gescheiterten Lauf und ohne wartende Freigabe bleibt es still.
    Erfolgreiche Läufe, gescheiterte von vorgestern, Posten anderer Arten und
    zurückgestellte Posten lösen nichts aus."""
    call_command("kern_selbsttest")  # erfolgreicher Lauf, Posten der Art „Selbsttest“
    _scheitern()
    Lauf.objects.filter(name="bank_abruf").update(
        gestartet_am=timezone.now() - datetime.timedelta(hours=30)
    )
    frist = _frist_faellig()
    posten = Arbeitsposten.objects.get(art=fristen.ART_FRIST_FAELLIG, bezug_id=frist.pk)
    kern_services.arbeitsposten_zurueckstellen(posten)

    call_command("benachrichtigen")

    assert _gesendet() == []
    assert not Benachrichtigung.objects.exists()
    lauf = Lauf.objects.filter(name="benachrichtigen").get()
    assert lauf.status == Lauf.Status.ERFOLGREICH
    assert lauf.meldung == "Nichts zu melden."


def test_gescheiterter_lauf_meldet_sich_mit_zaehler_und_link() -> None:
    """Abnahme Schritt 9: Ein gescheiterter Lauf (abgebrochen oder mit Fehlern) meldet sich
    per Mail an Max und per ntfy, mit Zähler und Link."""
    _scheitern()
    call_command(LaufMitFehlern())

    call_command("benachrichtigen")

    gesendet = _gesendet()
    assert [k for k, _, _ in gesendet] == ["mail", "ntfy"]
    assert mail.outbox[0].to == ["max@testhof.invalid"]
    for _, titel, text in gesendet:
        assert titel == "Hof-System: 2 Läufe gescheitert"
        assert text == (
            "Gescheiterte Läufe (letzte 24 Stunden): 2\n"
            "- abgleich: 1\n"
            "- bank_abruf: 1\n"
            f"Läufe: {BASIS}/laeufe/"
        )
    assert FakeKanal.gesendet[0][1].link == f"{BASIS}/laeufe/"
    eintrag = Benachrichtigung.objects.get()
    assert eintrag.kanaele == ["mail", "ntfy"]
    assert eintrag.lauf == Lauf.objects.get(name="benachrichtigen")


def test_wartende_freigabe_meldet_sich_mit_zaehler_und_link() -> None:
    """Abnahme Schritt 9: Eine wartende Freigabe (offener Posten einer gemeldeten Art)
    meldet sich mit Zähler und Link auf die Arbeitslisten."""
    _frist_faellig()
    _frist_faellig()

    call_command("benachrichtigen")

    for _, titel, text in _gesendet():
        assert titel == "Hof-System: 2 Freigaben warten"
        assert text == (
            f"Wartende Freigaben: 2\n- Frist fällig: 2\nArbeitslisten: {BASIS}/arbeitslisten/"
        )
    assert FakeKanal.gesendet[0][1].link == f"{BASIS}/arbeitslisten/"


def test_beides_zusammen_eine_nachricht() -> None:
    _scheitern()
    _frist_faellig()

    ergebnis = services.benachrichtigen()

    assert ergebnis.status == "gemeldet"
    assert ergebnis.nachricht is not None
    assert ergebnis.nachricht.titel == "Hof-System: 1 Lauf gescheitert, 1 Freigabe wartet"
    assert ergebnis.nachricht.link == f"{BASIS}/"
    assert len(_gesendet()) == 2


# --- Kein Spam -------------------------------------------------------------------------


def test_dieselbe_lage_nicht_mehrfach_am_tag() -> None:
    """Schritt 9, Aufgabe 5: Dieselbe Lage meldet sich an einem Tag nur einmal. Neues
    (ein weiterer gescheiterter Lauf, mehr wartende Posten) meldet sich; weniger nicht.
    Am nächsten Tag kommt die Lage wieder."""
    _scheitern()
    _frist_faellig()
    _frist_faellig()
    # Fester Zeitpunkt für alle Aufrufe (eine Stunde voraus, damit die Läufe des Tests
    # davor liegen), so hängt „derselbe Tag“ nicht an der Uhr.
    jetzt = timezone.now() + datetime.timedelta(hours=1)

    assert services.benachrichtigen(jetzt=jetzt).status == "gemeldet"
    assert services.benachrichtigen(jetzt=jetzt).status == "schon_gemeldet"
    assert len(Benachrichtigung.objects.all()) == 1

    # Gleicher Lauf scheitert noch einmal, ein Posten ist erledigt: nichts Neues.
    _scheitern()
    fristen.frist_erledigen(Frist.objects.first())  # type: ignore[arg-type]
    assert services.benachrichtigen(jetzt=jetzt).status == "schon_gemeldet"

    # Ein anderer Lauf scheitert: neu.
    call_command(LaufMitFehlern())
    assert services.benachrichtigen(jetzt=jetzt).status == "gemeldet"
    # Wieder zwei wartende Posten wie am Morgen: schon gemeldet. Drei: neu.
    _frist_faellig()
    assert services.benachrichtigen(jetzt=jetzt).status == "schon_gemeldet"
    _frist_faellig()
    assert services.benachrichtigen(jetzt=jetzt).status == "gemeldet"

    morgen = jetzt + datetime.timedelta(days=1)
    assert services.benachrichtigen(jetzt=morgen).status == "gemeldet"
    assert services.benachrichtigen(jetzt=morgen).status == "schon_gemeldet"
    assert Benachrichtigung.objects.count() == 4


# --- Kanäle und Einstellungen ------------------------------------------------------------


def test_ein_kanal_faellt_aus_der_andere_sendet() -> None:
    _frist_faellig()
    kanaele = [FakeKanal("mail", scheitern=True), FakeKanal("ntfy")]

    ergebnis = services.benachrichtigen(kanaele=kanaele)

    assert ergebnis.status == "gemeldet"
    assert (ergebnis.gesendet, ergebnis.fehler) == (["ntfy"], ["mail"])
    assert Benachrichtigung.objects.get().kanaele == ["ntfy"]


def test_kein_kanal_sendet_dann_beim_naechsten_mal_wieder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Hat kein Kanal gesendet, gilt die Lage nicht als gemeldet; der Lauf hat Fehler.
    Ohne Kanal gilt das nur in der Produktion, sonst „übersprungen“ (test_ohne_kanal.py)."""
    _frist_faellig()

    ergebnis = services.benachrichtigen(kanaele=[FakeKanal("mail", scheitern=True)])
    assert ergebnis.status == "fehlgeschlagen"
    assert not Benachrichtigung.objects.exists()

    with override_settings(HOF_BENACHRICHTIGUNG_MAIL="", HOF_NTFY_URL=""):
        monkeypatch.setenv("DJANGO_ENV", "prod")
        call_command("benachrichtigen")
        monkeypatch.delenv("DJANGO_ENV")
    lauf = Lauf.objects.get(name="benachrichtigen")
    assert lauf.status == Lauf.Status.MIT_FEHLERN
    assert "nicht gemeldet" in lauf.meldung

    assert services.benachrichtigen().status == "gemeldet"


@override_settings(HOF_BENACHRICHTIGUNG_ARTEN=["Selbsttest"])
def test_arten_der_freigaben_sind_einstellbar() -> None:
    _frist_faellig()  # „Frist fällig“ ist jetzt nicht gemeldet
    assert services.lage_ermitteln().leer

    call_command("kern_selbsttest")

    assert services.lage_ermitteln().freigaben == {"Selbsttest": 1}


@override_settings(HOF_BENACHRICHTIGUNG_FENSTER_STUNDEN=48)
def test_zeitfenster_fuer_gescheiterte_laeufe_ist_einstellbar() -> None:
    _scheitern()
    Lauf.objects.filter(name="bank_abruf").update(
        gestartet_am=timezone.now() - datetime.timedelta(hours=30)
    )
    assert services.lage_ermitteln().gescheitert == {"bank_abruf": 1}


def test_kanaele_aus_einstellungen() -> None:
    from apps.benachrichtigung.kanaele import MailKanal, kanaele_aus_einstellungen

    assert [k.name for k in kanaele_aus_einstellungen()] == ["mail", "ntfy"]
    with override_settings(HOF_BENACHRICHTIGUNG_MAIL=""):
        assert [k.name for k in kanaele_aus_einstellungen()] == ["ntfy"]
    with override_settings(HOF_NTFY_THEMA=""):
        kanaele = kanaele_aus_einstellungen()
        assert [k.name for k in kanaele] == ["mail"]
        assert isinstance(kanaele[0], MailKanal)
    with override_settings(
        HOF_BENACHRICHTIGUNG_MAIL_KANAL="apps.benachrichtigung.kanaele.FakeKanal"
    ):
        assert [k.name for k in kanaele_aus_einstellungen()] == ["fake", "ntfy"]


def test_pruefung_fuer_check_deploy() -> None:
    assert benachrichtigung_eingerichtet() == []
    with override_settings(HOF_BASIS_URL="", HOF_BENACHRICHTIGUNG_MAIL="", HOF_NTFY_URL=""):
        assert {m.id for m in benachrichtigung_eingerichtet()} == {
            "benachrichtigung.W001",
            "benachrichtigung.W002",
        }


# --- Inhalt ohne Personendaten (mit dem Testhof) ----------------------------------------


#: Jede Zeile einer Nachricht hat eine dieser Formen: Zähler, Lauf-Name, Art, Link.
ERLAUBTE_ZEILEN = (
    r"Gescheiterte Läufe \(letzte \d+ Stunden\): \d+",
    r"- [a-z][a-z0-9_]*: \d+",
    r"- (Frist fällig|Bankfreigabe läuft ab|Bankfreigabe fehlt|Bankabruf gescheitert|"
    r"Bankumsätze fehlen|Saldo weicht ab|Mahnungen senden|Zahlungen klären|"
    r"Rechnung zahlen|Schreiben senden|Schreiben erstellen|Schlüssel fehlt|"
    r"Buchungsanfrage prüfen|ID-Abweichung|Ablesung fehlt|Belege zuordnen|Rechnung zuordnen): \d+",
    r"Wartende Freigaben: \d+",
    rf"(Läufe|Arbeitslisten): {re.escape(BASIS)}/[a-z/]*",
    r"",
)
ERLAUBTER_TITEL = r"Hof-System: (\d+ Läufe? gescheitert)?(, )?(\d+ Freigaben? warte[nt])?"


def _personenwerte() -> tuple[set[str], set[str]]:
    """Alle Werte registrierter Personenfelder in der Datenbank, dazu die Wörter aus Namen
    und Anschriften (Mandant, Objekt, Benutzer)."""
    werte: set[str] = set()
    woerter: set[str] = set()
    for modell in personenfelder.eigene_modelle():
        if hasattr(modell, "pgh_tracked_model"):
            continue
        felder = sorted(personenfelder.personenfelder(modell))
        if not felder:
            continue
        for zeile in modell._default_manager.values_list(*felder):
            for wert in zeile:
                if not wert:
                    continue
                werte.add(str(wert))
                if modell._meta.label in {"kern.Mandant", "kern.Objekt", "kern.Benutzer"}:
                    woerter |= {w for w in re.split(r"[\s,()]+", str(wert)) if len(w) >= 4}
    return werte, woerter


def test_inhalt_nur_zaehler_und_link_nie_namen_oder_betraege(testhof: Testhof) -> None:
    """Abnahme Schritt 9 (H11.1): Die Benachrichtigung enthält nur Zähler und Link, nie
    Namen oder Beträge. Geprüft mit dem Testhof: kein Wert eines Personenfelds (Mandanten,
    Anschriften, Benutzer, Hinweise an Fristen und Posten) und kein Betrag steht in Titel
    oder Text, und jede Zeile hat eine der erlaubten Formen."""
    # Fristen des Testhofs werden Posten mit Hinweis (Stichtag fest, damit es gleich bleibt).
    call_command("fristen", "--stichtag", STICHTAG.isoformat())
    with pytest.raises(CommandError):
        call_command(AbbrechenderLauf())  # Fehlertext nennt einen Mandanten
    call_command(LaufMitFehlern())
    werte, woerter = _personenwerte()
    assert any(testhof.kern.mandanten["gbr"].bezeichnung == w for w in werte)
    assert any(f.hinweis in werte for f in testhof.fristen.alle if f.hinweis)

    call_command("benachrichtigen")

    gesendet = _gesendet()
    assert [k for k, _, _ in gesendet] == ["mail", "ntfy"]
    for _, titel, text in gesendet:
        inhalt = f"{titel}\n{text}"
        for wert in werte:
            assert wert not in inhalt
        for wort in woerter:
            assert not re.search(rf"\b{re.escape(wort)}\b", inhalt), wort
        assert not re.search(r"\d+,\d{2}|€|EUR", inhalt)
        assert re.fullmatch(ERLAUBTER_TITEL, titel), titel
        for zeile in text.splitlines():
            assert any(re.fullmatch(muster, zeile) for muster in ERLAUBTE_ZEILEN), zeile
        assert "Frist fällig: 3" in text  # überfällig, heute, mit Vorlauf


def test_nachricht_bauen_fuer_leere_lage_hat_keine_zeilen() -> None:
    nachricht = services.nachricht_bauen(services.Lage())
    assert isinstance(nachricht, Nachricht)
    assert nachricht.text == ""


def test_standardarten_enthalten_die_posten_der_bank() -> None:
    """Schritt 9a: Die Posten der Bank, die auf Max warten, melden sich als Freigaben."""
    from django.conf import settings

    from apps.bank import services as bank

    arten = set(settings.HOF_BENACHRICHTIGUNG_ARTEN)
    assert {
        fristen.ART_FRIST_FAELLIG,
        bank.ART_FREIGABE,
        bank.ART_FREIGABE_FEHLT,
        bank.ART_ABRUF,
        bank.ART_ZULIEFERUNG,
        bank.ART_SALDO,
    } <= arten
