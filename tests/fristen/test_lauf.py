"""Lauf ``fristen`` (Schritt 9, Aufgabe 2): fällige Fristen werden Arbeitsposten."""

from __future__ import annotations

import datetime

import pytest
from django.core.management import call_command

from apps.fristen import services
from apps.fristen.models import Frist
from apps.kern import aktionen, zeit
from apps.kern import services as kern_services
from apps.kern.models import Arbeitsposten, Lauf, Objekt
from tests.factories import ObjektFabrik

pytestmark = pytest.mark.django_db

HEUTE = zeit.heute()


def tage(n: int) -> datetime.date:
    return HEUTE + datetime.timedelta(days=n)


@pytest.fixture
def objekt() -> Objekt:
    return ObjektFabrik()  # type: ignore[no-any-return]


def _frist(objekt: Objekt, datum: datetime.date, art: str = Frist.Art.SONSTIGE, **kw: int) -> Frist:
    return services.frist_anlegen(art, datum, Frist.Quelle.HAND, objekt, **kw)  # type: ignore[arg-type]


def _posten() -> list[Arbeitsposten]:
    return list(Arbeitsposten.objects.filter(art=services.ART_FRIST_FAELLIG).order_by("pk"))


def _offene_posten() -> list[Arbeitsposten]:
    return [p for p in _posten() if p.status == Arbeitsposten.Status.OFFEN]


def test_faellige_und_ueberfaellige_fristen_werden_arbeitsposten(objekt: Objekt) -> None:
    """Schritt 9, Aufgabe 2: Der Lauf fristen macht fällige und überfällige Fristen zu
    Arbeitsposten „Frist fällig“; spätere Fristen nicht, erledigte nicht."""
    ueberfaellig = _frist(objekt, tage(-5), Frist.Art.KUENDIGUNG)
    heute = _frist(objekt, HEUTE, Frist.Art.VERTRAGSENDE)
    vorlauf = _frist(objekt, tage(7), Frist.Art.SPERRFRIST, vorlauf_tage=7)
    _frist(objekt, tage(8), Frist.Art.EICHFRIST, vorlauf_tage=7)  # noch nicht fällig
    services.frist_erledigen(_frist(objekt, tage(-9), Frist.Art.ABLESUNG))

    call_command("fristen")

    lauf = Lauf.objects.get(name="fristen")
    assert lauf.status == Lauf.Status.ERFOLGREICH
    assert lauf.arbeitsposten == 3
    posten = _posten()
    assert [p.bezug for p in posten] == [ueberfaellig, heute, vorlauf]
    assert all(p.lauf == lauf for p in posten)
    assert [p.faellig_am for p in posten] == [tage(-5), HEUTE, tage(7)]
    assert all(p.aktionen == list(services.POSTEN_AKTIONEN) for p in posten)
    # Hinweis nur mit ID, Art, Quelle und Datum.
    assert posten[0].hinweis == (
        f"Frist #{ueberfaellig.pk}: Kündigungsfrist (Quelle: von Hand), Datum {tage(-5):%d.%m.%Y}."
    )


def test_lauf_ist_idempotent_hoechstens_ein_offener_posten_je_frist(objekt: Objekt) -> None:
    """Schritt 9, Aufgabe 2: Je Frist gibt es höchstens einen offenen Posten, auch wenn der
    Lauf mehrmals läuft. Ein zurückgestellter Posten zählt als offen."""
    frist = _frist(objekt, tage(-1))
    call_command("fristen")
    call_command("fristen")
    assert len(_posten()) == 1

    kern_services.arbeitsposten_zurueckstellen(_posten()[0])
    call_command("fristen")
    assert len(_posten()) == 1

    laeufe = list(Lauf.objects.filter(name="fristen").order_by("pk"))
    assert [lauf.arbeitsposten for lauf in laeufe] == [1, 0, 0]
    assert frist.pk in services.offene_fristen().values_list("pk", flat=True)


def test_erledigte_frist_schliesst_ihren_posten(objekt: Objekt) -> None:
    """Schritt 9, Aufgabe 2: Wird die Frist erledigt, schließt sich der Posten; der nächste
    Lauf legt keinen neuen an."""
    frist = _frist(objekt, tage(-1))
    call_command("fristen")

    services.frist_erledigen(frist)

    assert _offene_posten() == []
    assert _posten()[0].status == Arbeitsposten.Status.ERLEDIGT
    call_command("fristen")
    assert len(_posten()) == 1


def test_aktion_frist_erledigt_am_posten(objekt: Objekt) -> None:
    """Die Aktion „Frist erledigt“ in der Arbeitsliste setzt das Erledigt-Datum der Frist."""
    frist = _frist(objekt, tage(-1))
    call_command("fristen")
    posten = _posten()[0]

    assert [a.schluessel for a in aktionen.fuer_posten(posten)] == list(services.POSTEN_AKTIONEN)
    aktionen.holen(services.AKTION_ERLEDIGEN).ausfuehren(posten, None)  # type: ignore[arg-type]

    frist.refresh_from_db()
    posten.refresh_from_db()
    assert frist.erledigt_am == HEUTE
    assert posten.status == Arbeitsposten.Status.ERLEDIGT


def test_posten_ohne_faellige_frist_schliesst_der_lauf(objekt: Objekt) -> None:
    """Ist eine Frist auf anderem Weg nicht mehr fällig (z. B. per Rücknahme), schließt der
    nächste Lauf ihren Posten."""
    frist = _frist(objekt, tage(-1))
    call_command("fristen")
    Frist.objects.filter(pk=frist.pk).update(datum=tage(30))  # wie eine Rücknahme

    call_command("fristen")

    assert _offene_posten() == []
    assert "1 geschlossen" in Lauf.objects.filter(name="fristen").first().meldung  # type: ignore[union-attr]


def test_stichtag_als_option(objekt: Objekt) -> None:
    _frist(objekt, tage(20))
    call_command("fristen", "--stichtag", tage(19).isoformat())
    assert _posten() == []
    call_command("fristen", "--stichtag", tage(20).isoformat())
    assert len(_posten()) == 1
