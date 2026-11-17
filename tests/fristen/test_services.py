"""Services der Fristen (Schritt 9, Aufgabe 1)."""

from __future__ import annotations

import datetime

import pytest
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.utils import timezone
from pghistory.models import Events

from apps.fristen import services
from apps.fristen.models import Frist, Fristverschiebung
from apps.kern import services as kern_services
from apps.kern.models import Arbeitsposten, Objekt
from tests.factories import ObjektFabrik

pytestmark = pytest.mark.django_db

HEUTE = timezone.localdate()


def tage(n: int) -> datetime.date:
    return HEUTE + datetime.timedelta(days=n)


@pytest.fixture
def objekt() -> Objekt:
    return ObjektFabrik()  # type: ignore[no-any-return]


def _anlegen(bezug: Objekt, datum: datetime.date | None = None, **kwargs: object) -> Frist:
    return services.frist_anlegen(
        kwargs.pop("art", Frist.Art.SONSTIGE),  # type: ignore[arg-type]
        datum or tage(5),
        kwargs.pop("quelle", Frist.Quelle.HAND),  # type: ignore[arg-type]
        bezug,
        **kwargs,  # type: ignore[arg-type]
    )


def _offene_fristen_ids() -> set[int]:
    return set(services.offene_fristen().values_list("pk", flat=True))


# --- Abnahme ---------------------------------------------------------------------------


def test_jede_frist_nennt_ihre_quelle(objekt: Objekt) -> None:
    """Abnahme Schritt 9: Jede Frist nennt ihre Quelle. Ohne gültige Quelle (oder ohne
    gespeicherten Bezug) entsteht keine Frist; jede Quelle aus der Liste lässt sich setzen."""
    for quelle in Frist.Quelle.values:
        frist = _anlegen(objekt, quelle=quelle)
        assert frist.quelle == quelle
        assert frist.get_quelle_display()

    for falsch in ("", "irgendwo"):
        with pytest.raises(services.FristFehler, match="Quelle"):
            _anlegen(objekt, quelle=falsch)
    with pytest.raises(services.FristFehler, match="Bezug"):
        _anlegen(Objekt(kuerzel="NEU"))
    assert Frist.objects.count() == len(Frist.Quelle.values)
    assert not Frist.objects.filter(quelle="").exists()

    # Auch an den Services vorbei nimmt die Datenbank keine Frist ohne gültige Quelle.
    frist = Frist.objects.first()
    assert frist is not None
    with pytest.raises(IntegrityError), transaction.atomic():
        Frist.objects.filter(pk=frist.pk).update(quelle="")


def test_frist_verschwindet_erst_mit_erledigt_datum(objekt: Objekt) -> None:
    """Abnahme Schritt 9: Eine Frist verschwindet erst mit einem Erledigt-Datum. Sie bleibt
    offen, wenn ihr Datum vorbei ist, der Lauf sie meldet, ihr Posten auf anderem Weg erledigt
    wird oder sie verschoben wird; erst frist_erledigen nimmt sie aus den offenen Fristen."""
    frist = _anlegen(objekt, tage(-3))
    assert frist.pk in _offene_fristen_ids()

    call_command("fristen")
    posten = Arbeitsposten.objects.get(art=services.ART_FRIST_FAELLIG)
    kern_services.arbeitsposten_erledigen(posten)  # Posten weg, Frist nicht
    services.frist_verschieben(frist, tage(-1), grund="Handwerker kommt später")
    call_command("fristen", "--stichtag", tage(30).isoformat())

    assert frist.pk in _offene_fristen_ids()
    assert frist.pk in set(services.faellige_fristen(HEUTE).values_list("pk", flat=True))

    services.frist_erledigen(frist, am=HEUTE)

    assert frist.pk not in _offene_fristen_ids()
    assert frist.pk not in set(services.faellige_fristen(tage(365)).values_list("pk", flat=True))
    frist.refresh_from_db()
    assert frist.erledigt_am == HEUTE
    # Nicht gelöscht: Sie steht weiter in der Tabelle und am Bezug.
    assert list(services.fristen_zu(objekt)) == [frist]


# --- frist_anlegen ---------------------------------------------------------------------


def test_frist_anlegen_ist_idempotent_je_quelle_art_bezug(objekt: Objekt) -> None:
    """Schritt 9a: frist_anlegen ist idempotent je Quelle, Art und Bezug."""
    erste = _anlegen(objekt, tage(5), art=Frist.Art.KUENDIGUNG, quelle=Frist.Quelle.VERTRAG)
    zweite = _anlegen(objekt, tage(5), art=Frist.Art.KUENDIGUNG, quelle=Frist.Quelle.VERTRAG)
    anderes_datum = _anlegen(objekt, tage(9), art=Frist.Art.KUENDIGUNG, quelle=Frist.Quelle.VERTRAG)

    assert erste.pk == zweite.pk == anderes_datum.pk
    assert anderes_datum.datum == tage(5)  # ohne datum_angleichen unverändert
    assert Frist.objects.count() == 1

    # Andere Art, andere Quelle oder anderer Bezug: eigene Frist.
    _anlegen(objekt, art=Frist.Art.VERTRAGSENDE, quelle=Frist.Quelle.VERTRAG)
    _anlegen(objekt, art=Frist.Art.KUENDIGUNG, quelle=Frist.Quelle.HAND)
    _anlegen(ObjektFabrik(), art=Frist.Art.KUENDIGUNG, quelle=Frist.Quelle.VERTRAG)
    assert Frist.objects.count() == 4


def test_frist_anlegen_mit_datum_angleichen_verschiebt(objekt: Objekt) -> None:
    frist = _anlegen(objekt, tage(5), quelle=Frist.Quelle.VERTRAG)
    wieder = _anlegen(objekt, tage(8), quelle=Frist.Quelle.VERTRAG, datum_angleichen=True)

    assert wieder.pk == frist.pk
    assert wieder.datum == tage(8)
    verschiebung = Fristverschiebung.objects.get()
    assert (verschiebung.von_datum, verschiebung.nach_datum) == (tage(5), tage(8))


def test_nach_erledigen_entsteht_dieselbe_frist_nicht_noch_einmal(objekt: Objekt) -> None:
    """Schritt 9a: Ein erneuter Aufruf mit demselben Datum legt eine erledigte Frist nicht
    wieder an. Mit neuem Datum entsteht die nächste (z. B. die nächste Bankfreigabe)."""
    art, quelle = Frist.Art.BANKFREIGABE, Frist.Quelle.BANKFREIGABE
    erste = _anlegen(objekt, tage(-1), art=art, quelle=quelle)
    services.frist_erledigen(erste)

    assert _anlegen(objekt, tage(-1), art=art, quelle=quelle).pk == erste.pk
    naechste = _anlegen(objekt, tage(89), art=art, quelle=quelle)

    assert naechste.pk != erste.pk
    assert Frist.objects.count() == 2
    assert list(services.fristen_zu(objekt, nur_offen=True)) == [naechste]


def test_frist_anlegen_prueft_art_und_vorlauf(objekt: Objekt) -> None:
    with pytest.raises(services.FristFehler, match="Art"):
        _anlegen(objekt, art="unbekannt")
    with pytest.raises(services.FristFehler, match="Vorlauf"):
        _anlegen(objekt, vorlauf_tage=-1)


def test_frist_steht_im_protokoll(objekt: Objekt) -> None:
    frist = _anlegen(objekt)
    assert Events.objects.tracks(frist).filter(pgh_label="insert").count() == 1


# --- verschieben, erledigen, fristen_zu --------------------------------------------------


def test_frist_verschieben_mit_protokoll(objekt: Objekt) -> None:
    """Schritt 9a: frist_verschieben hält von, nach und Grund fest; das Protokoll zeigt die
    Änderung des Datums."""
    frist = _anlegen(objekt, tage(2))
    services.frist_verschieben(frist, tage(20), grund="Abstimmung mit dem Steuerbüro")
    services.frist_verschieben(frist, tage(20), grund="unverändert")  # gleiches Datum: nichts

    frist.refresh_from_db()
    assert frist.datum == tage(20)
    verschiebung = frist.verschiebungen.get()
    assert (verschiebung.von_datum, verschiebung.nach_datum) == (tage(2), tage(20))
    assert verschiebung.grund == "Abstimmung mit dem Steuerbüro"
    aenderung = Events.objects.tracks(frist).filter(pgh_label="update").get()
    assert aenderung.pgh_diff["datum"] == [tage(2).isoformat(), tage(20).isoformat()]


def test_erledigte_frist_laesst_sich_nicht_verschieben(objekt: Objekt) -> None:
    frist = services.frist_erledigen(_anlegen(objekt))
    with pytest.raises(services.FristFehler, match="erledigt"):
        services.frist_verschieben(frist, tage(30))


def test_verschieben_aus_der_faelligkeit_schliesst_den_posten(objekt: Objekt) -> None:
    frist = _anlegen(objekt, tage(-2))
    call_command("fristen")
    posten = Arbeitsposten.objects.get(art=services.ART_FRIST_FAELLIG)

    services.frist_verschieben(frist, tage(-1))  # weiter fällig: Posten bleibt
    posten.refresh_from_db()
    assert posten.status == Arbeitsposten.Status.OFFEN

    services.frist_verschieben(frist, tage(10))  # nicht mehr fällig: Posten zu
    posten.refresh_from_db()
    assert posten.status == Arbeitsposten.Status.ERLEDIGT


def test_frist_erledigen(objekt: Objekt) -> None:
    frist = _anlegen(objekt)
    with pytest.raises(services.FristFehler, match="Zukunft"):
        services.frist_erledigen(frist, am=tage(1))

    services.frist_erledigen(frist, am=tage(-2))
    services.frist_erledigen(frist, am=HEUTE)  # schon erledigt: bleibt

    frist.refresh_from_db()
    assert frist.erledigt_am == tage(-2)


def test_fristen_zu_bezug(objekt: Objekt) -> None:
    anderes = ObjektFabrik()
    spaet = _anlegen(objekt, tage(9), art=Frist.Art.VERTRAGSENDE)
    frueh = _anlegen(objekt, tage(1), art=Frist.Art.KUENDIGUNG)
    erledigt = services.frist_erledigen(_anlegen(objekt, tage(3), art=Frist.Art.SPERRFRIST))
    _anlegen(anderes)

    assert list(services.fristen_zu(objekt)) == [frueh, erledigt, spaet]
    assert list(services.fristen_zu(objekt, nur_offen=True)) == [frueh, spaet]


def test_faellig_mit_vorlauf(objekt: Objekt) -> None:
    """Ab Datum minus Vorlauf gilt eine Frist als fällig."""
    frist = _anlegen(objekt, tage(10), vorlauf_tage=7)

    assert frist.faellig_ab == tage(3)
    assert not services.faellige_fristen(tage(2)).exists()
    assert list(services.faellige_fristen(tage(3))) == [frist]
