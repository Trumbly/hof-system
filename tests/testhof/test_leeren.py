"""Review 1, Punkt 3: ``testhof --leeren`` räumt alles auf, und Posten ohne Bezug schließt
der Lauf ``arbeitsposten_pflege``."""

from __future__ import annotations

import io
import re

import pytest
from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType
from django.core.management import call_command
from pghistory.models import Events

from apps.cockpit.models import NotionAufgabe
from apps.cockpit.notion import FakeNotion
from apps.kern import services as kern_services
from apps.kern.models import Arbeitsposten, Benutzer, Lauf
from apps.mahnwesen.models import Mahnstufe
from testhof import aufbau, kern, pruefsumme
from tests.factories import MandantFabrik, ObjektFabrik

pytestmark = pytest.mark.django_db


def _befehl(*argumente: str) -> str:
    ausgabe = io.StringIO()
    call_command("testhof", *argumente, stdout=ausgabe)
    return ausgabe.getvalue()


def _pruefsumme(ausgabe: str) -> str:
    treffer = re.search(r"Prüfsumme: ([0-9a-f]{64})", ausgabe)
    assert treffer, ausgabe
    return treffer.group(1)


def _laeufe_wie_am_rechner() -> None:
    """Was Max nach dem Aufbau tut: Läufe starten, die Posten, Fristen, Schreiben und
    Aufgaben im Hof-Backlog anlegen."""
    call_command("kern_selbsttest", stdout=io.StringIO())
    call_command("bank_abruf", stdout=io.StringIO())
    call_command("fristen", stdout=io.StringIO())
    call_command("aufgaben_abgleich", stdout=io.StringIO())


def test_leeren_raeumt_alle_tabellen_der_apps_auf() -> None:
    """Review 1, Punkt 3: Nach --leeren sind Arbeitsposten, Läufe, Fristen, Schreiben,
    Aufgaben im Hof-Backlog (auch im Fake) und alle übrigen Daten der Apps weg."""
    aufbau.aufbauen(seed=42)
    _laeufe_wie_am_rechner()
    assert Arbeitsposten.objects.exists() and Lauf.objects.exists()
    assert NotionAufgabe.objects.exists() and FakeNotion.seiten

    aufbau.leeren()

    voll = [m._meta.label for m in aufbau.geleerte_modelle() if m._default_manager.exists()]
    assert voll == []
    assert not FakeNotion.seiten
    assert not Benutzer.objects.filter(email=kern.EMAIL).exists()


def test_leeren_behaelt_konfiguration_und_fremde_benutzer() -> None:
    """Review 1, Punkt 3: Bleiben dürfen nur Modelltypen, Rechte, die Stufen aus der
    Datenmigration und Benutzer außerhalb des Testhofs."""
    stufen = Mahnstufe.objects.count()
    typen = ContentType.objects.count()
    rechte = Permission.objects.count()
    eigener = kern_services.benutzer_anlegen(
        "eigener@entwicklung.invalid", "Nur-ein-Testpasswort-42"
    )
    aufbau.aufbauen(seed=42)

    aufbau.leeren()

    assert stufen and Mahnstufe.objects.count() == stufen
    assert ContentType.objects.count() == typen
    assert Permission.objects.count() == rechte
    assert list(Benutzer.objects.all()) == [eigener]


def test_geleerte_modelle_umfassen_protokoll_laeufe_und_posten() -> None:
    """Review 1, Punkt 3: Die Liste ist generisch; Migrationen und Modelltypen fehlen darin."""
    labels = {m._meta.label for m in aufbau.geleerte_modelle()}

    assert {
        "kern.Arbeitsposten",
        "kern.Lauf",
        "kern.ArbeitspostenEvent",
        "fristen.Frist",
        "dokumente.Schreiben",
        "cockpit.NotionAufgabe",
        "benachrichtigung.Benachrichtigung",
        "pghistory.Context",
    } <= labels
    assert not labels & {
        "contenttypes.ContentType",
        "auth.Permission",
        "kern.Benutzer",
        "kern.Benutzer_groups",
        "mahnwesen.Mahnstufe",
    }


def test_neu_aufbauen_nach_laeufen_ergibt_dieselbe_pruefsumme() -> None:
    """Review 1, Punkt 3: Die Prüfsumme bleibt zweimal gleich, auch wenn zwischen den
    Aufbauten Läufe Posten angelegt haben; danach zeigt kein Posten ins Leere."""
    erste = _pruefsumme(_befehl("--seed", "42"))
    _laeufe_wie_am_rechner()

    zweite = _pruefsumme(_befehl("--seed", "42", "--leeren"))
    dritte = _pruefsumme(_befehl("--seed", "42", "--leeren"))

    assert erste == zweite == dritte
    assert kern_services.verwaiste_arbeitsposten() == []
    assert pruefsumme.pruefsumme() == erste


# --- Pflege: Posten ohne Bezug --------------------------------------------------------


def test_posten_ohne_bezug_wird_geschlossen_und_protokolliert() -> None:
    """Review 1, Punkt 3: Ein Arbeitsposten, dessen Bezug fehlt, wird beim nächsten Lauf
    geschlossen und protokolliert (Vermerk mit IDs, Protokoll mit dem Lauf als Kontext)."""
    objekt = ObjektFabrik()
    objekt_id = objekt.pk
    verwaist = kern_services.arbeitsposten_anlegen("Selbsttest", bezug=objekt, hinweis="Prüfen")
    zurueck = kern_services.arbeitsposten_anlegen("Selbsttest", bezug=objekt)
    kern_services.arbeitsposten_zurueckstellen(zurueck)
    mandant = MandantFabrik()
    gesund = kern_services.arbeitsposten_anlegen("Selbsttest", bezug=mandant)
    ohne = kern_services.arbeitsposten_anlegen("Selbsttest")
    objekt.delete()  # nur im Test: so fehlt der Bezug wie nach einem alten --leeren

    call_command("arbeitsposten_pflege", stdout=io.StringIO())

    lauf = Lauf.objects.get(name="arbeitsposten_pflege")
    assert lauf.status == Lauf.Status.ERFOLGREICH
    assert f"#{verwaist.pk}" in lauf.meldung and f"#{zurueck.pk}" in lauf.meldung
    for posten in (verwaist, zurueck):
        posten.refresh_from_db()
        assert posten.status == Arbeitsposten.Status.ERLEDIGT
        assert f"Bezug kern.objekt #{objekt_id} fehlt (Lauf #{lauf.pk})" in posten.hinweis
    assert verwaist.hinweis.startswith("Prüfen\n")
    for posten in (gesund, ohne):
        posten.refresh_from_db()
        assert posten.status == Arbeitsposten.Status.OFFEN

    ereignis = Events.objects.tracks(verwaist).order_by("pgh_created_at", "pgh_id").last()
    assert ereignis is not None and ereignis.pgh_label == "update"
    assert ereignis.pgh_context["lauf"] == lauf.pk
    assert ereignis.pgh_context["lauf_name"] == "arbeitsposten_pflege"


def test_pflege_laeuft_im_tageslauf_vor_hof_backlog_und_benachrichtigung() -> None:
    """Review 1, Punkt 3: Die Pflege ist ein Schritt des Tageslaufs, vor dem Abgleich mit
    dem Hof-Backlog und der Benachrichtigung."""
    from apps.cockpit import tageslauf

    namen = [s.name for s in tageslauf.schritte()]
    assert namen.index("arbeitsposten_pflege") < namen.index("aufgaben_abgleich")
    assert namen.index("arbeitsposten_pflege") < namen.index("benachrichtigen")


def test_pflege_ohne_verwaiste_posten() -> None:
    """Review 1, Punkt 3: Ohne verwaiste Posten meldet die Pflege das und ändert nichts."""
    call_command("arbeitsposten_pflege", stdout=io.StringIO())
    lauf = Lauf.objects.get(name="arbeitsposten_pflege")
    assert lauf.meldung == "Kein Arbeitsposten ohne Bezug."
