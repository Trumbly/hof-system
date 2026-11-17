"""Testhof-Modul der Fristen (Schritt 9a)."""

from __future__ import annotations

import datetime

from django.core.management import call_command

from apps.fristen import services
from apps.fristen.models import Frist
from apps.kern.models import Arbeitsposten, Objekt
from testhof import register
from testhof.aufbau import STICHTAG, Testhof


def test_testhof_hat_fristen_von_hand_an_objekten(testhof: Testhof) -> None:
    """Schritt 9a: Der Testhof bringt ein paar Fristen mit Quelle „von Hand“ an den
    Objekten des Kerns mit; eine davon ist erledigt."""
    assert "fristen" in register.laden()
    fristen = testhof.fristen.alle
    assert Frist.objects.count() == len(fristen) == 5
    assert {f.quelle for f in fristen} == {Frist.Quelle.HAND}
    assert {type(f.bezug) for f in fristen} == {Objekt}
    assert [f.pk for f in Frist.objects.filter(erledigt_am__isnull=False)] == [
        testhof.fristen.erledigt.pk
    ]
    assert testhof.fristen.ueberfaellig.datum == STICHTAG - datetime.timedelta(days=10)


def test_lauf_fristen_am_stichtag_des_testhofs(testhof: Testhof) -> None:
    """Am Stichtag sind drei Fristen fällig: überfällig, heute und mit Vorlauf."""
    call_command("fristen", "--stichtag", STICHTAG.isoformat())

    posten = Arbeitsposten.objects.filter(art=services.ART_FRIST_FAELLIG)
    assert {p.bezug_id for p in posten} == {
        testhof.fristen.ueberfaellig.pk,
        testhof.fristen.heute.pk,
        testhof.fristen.mit_vorlauf.pk,
    }
