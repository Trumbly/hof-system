"""Review 2, A1 und A10: Übergabe am Handy ohne Foto, mit Kautionsschritt, ohne Zähler und
Schlüssel (Überspringen mit Begründung), Warnung in Schritt 1, Zusammenfassung am Ende."""

from __future__ import annotations

import datetime
from decimal import Decimal

import pytest
from django.test import Client
from django.urls import reverse

from apps.stammdaten import services
from apps.stammdaten.models import Einheit, Kaution, Partei, Schluessel, Uebergabe, Vertrag
from tests.stammdaten.conftest import foto, vertrag_anlegen

pytestmark = pytest.mark.django_db
D = datetime.date
GRENZE = "----hofgrenze"


def _url(uebergabe: Uebergabe, nummer: int) -> str:
    return reverse("stammdaten:uebergabe", args=[uebergabe.pk, nummer])


def _multipart(teile: list[tuple[str, str | None, bytes]]) -> bytes:
    """Ein Formular, wie es der Browser schickt; ``None`` als Dateiname heißt Textfeld."""
    koerper = b""
    for name, dateiname, inhalt in teile:
        koerper += f"--{GRENZE}\r\n".encode()
        if dateiname is None:
            koerper += f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode()
        else:
            koerper += (
                f'Content-Disposition: form-data; name="{name}"; filename="{dateiname}"\r\n'
                "Content-Type: application/octet-stream\r\n\r\n"
            ).encode()
        koerper += inhalt + b"\r\n"
    return koerper + f"--{GRENZE}--\r\n".encode()


@pytest.fixture
def uebergabe(entwurf: Vertrag) -> Uebergabe:
    return services.uebergabe_beginnen(entwurf, Uebergabe.Art.UEBERGABE, D(2026, 12, 1))


@pytest.mark.parametrize(
    "teile",
    [
        [("fotos", "", b"")],  # leeres Mehrfach-Feld, wie Chrome und Safari es schicken
        [("fotos", "", b""), ("fotos", "", b"")],
        [("fotos", "leer.jpg", b"")],  # Datei ohne Inhalt
    ],
)
def test_fotos_ohne_foto_geht_weiter(
    uebergabe: Uebergabe, angemeldet: Client, teile: list[tuple[str, str | None, bytes]]
) -> None:
    """Review 2, A1: Schritt 3 (Fotos) mit leerem Mehrfach-Dateifeld heißt kein Foto; es
    geht zum nächsten Schritt, ohne Fehler und ohne gespeichertes Foto."""
    antwort = angemeldet.post(
        _url(uebergabe, 3),
        data=_multipart(teile),
        content_type=f"multipart/form-data; boundary={GRENZE}",
    )
    assert antwort.status_code == 302
    assert antwort["Location"] == _url(uebergabe, 4)
    assert not uebergabe.fotos.exists()


def test_unterschrift_ohne_foto_geht_weiter(uebergabe: Uebergabe, angemeldet: Client) -> None:
    """Review 2, A1: Auch das leere Einzel-Dateifeld der Unterschrift ist kein Foto."""
    antwort = angemeldet.post(
        _url(uebergabe, 6),
        data=_multipart([("zustand", None, b"gut"), ("unterschrift", "", b"")]),
        content_type=f"multipart/form-data; boundary={GRENZE}",
    )
    assert antwort["Location"] == _url(uebergabe, 7)
    uebergabe.refresh_from_db()
    assert not uebergabe.unterschrift


def test_schritt_1_warnung_verschwindet_beim_anhaken(
    uebergabe: Uebergabe, angemeldet: Client
) -> None:
    """Review 2, A10: Die Warnung „Vertrag noch nicht unterschrieben“ verschwindet beim
    Anhaken sofort (kleines Skript am Häkchen), und ein Klick auf „Speichern und weiter“
    speichert und führt zu Schritt 2. Danach ist das Häkchen gesetzt."""
    seite = angemeldet.get(_url(uebergabe, 1)).content.decode()
    assert "data-unterschrift" in seite
    assert 'getElementById("id_vertrag_unterschrieben")' in seite
    assert "punkt.hidden = feld.checked" in seite

    antwort = angemeldet.post(
        _url(uebergabe, 1), {"datum": "2026-12-01", "vertrag_unterschrieben": "on"}
    )
    assert antwort["Location"] == _url(uebergabe, 2)
    uebergabe.vertrag.refresh_from_db()
    assert uebergabe.vertrag.unterschrieben_am == D(2026, 12, 1)

    seite = angemeldet.get(_url(uebergabe, 1)).content.decode()
    assert 'name="vertrag_unterschrieben" id="id_vertrag_unterschrieben" checked' in seite
    assert "noch nicht unterschrieben" not in seite


def test_uebergabe_ohne_kaution_zaehler_und_schluessel_ganz_durch(
    garage: Einheit, mieter: Partei, angemeldet: Client
) -> None:
    """Review 2, A10: Übergabe ohne Kaution („keine Kaution vereinbart“), ohne Zähler und
    ohne Schlüssel (beide mit Begründung übersprungen) geht im Assistenten ganz durch, ohne
    Umweg über die Vertragsseite. Die Begründungen sind gespeichert."""
    vertrag = vertrag_anlegen(garage, mieter, beginn=D(2026, 12, 1), kaution="0")
    Kaution.objects.filter(vertrag=vertrag).update(betrag=None)  # Kaution noch nicht erfasst
    angemeldet.post(reverse("stammdaten:uebergabe_starten", args=[vertrag.pk, "uebergabe"]))
    uebergabe = Uebergabe.objects.get(vertrag=vertrag)
    assert not garage.schluessel.exists() and not garage.zaehler.exists()

    def schritt(n: int, daten: dict[str, object]) -> None:
        antwort = angemeldet.post(_url(uebergabe, n), daten)
        assert antwort.status_code == 302, antwort.content.decode()[:500]
        assert antwort["Location"] != _url(uebergabe, n)

    schritt(1, {"datum": "2026-12-01", "vertrag_unterschrieben": "on"})
    seite = angemeldet.get(_url(uebergabe, 2)).content.decode()
    assert "Zähler anlegen" in seite and "Überspringen und weiter" in seite
    schritt(2, {"begruendung": "Garage ohne Strom"})
    schritt(3, {})
    seite = angemeldet.get(_url(uebergabe, 4)).content.decode()
    assert "Schlüssel erfassen" in seite
    schritt(4, {"begruendung": "Tor mit Zahlenschloss"})
    seite = angemeldet.get(_url(uebergabe, 5)).content.decode()
    assert "keine Kaution vereinbart" in seite
    schritt(5, {"keine": "on"})
    schritt(6, {"zustand": "gut", "unterschrift": foto("u.png")})
    antwort = angemeldet.post(_url(uebergabe, 7))
    assert antwort["Location"] == reverse("stammdaten:vertrag", args=[vertrag.pk])

    vertrag.refresh_from_db()
    uebergabe.refresh_from_db()
    assert vertrag.status == Vertrag.Status.AKTIV
    assert vertrag.kaution.betrag == Decimal("0.00")
    assert uebergabe.uebersprungen == {
        "zaehler": "Garage ohne Strom",
        "schluessel": "Tor mit Zahlenschloss",
    }


def test_kaution_im_assistenten_vorbelegt_und_erfasst(
    uebergabe: Uebergabe, angemeldet: Client
) -> None:
    """Review 2, A10: Der Kautionsschritt ist aus dem Vertrag vorbelegt (180 €) und hält
    Betrag, Art und Eingang fest; danach ist die Kaution kein Hindernis mehr."""
    seite = angemeldet.get(_url(uebergabe, 5)).content.decode()
    assert 'name="betrag" value="180,00"' in seite
    assert 'value="buergschaft"' in seite

    antwort = angemeldet.post(
        _url(uebergabe, 5),
        {"betrag": "180,00", "art": "bar", "eingang_am": "2026-12-01", "konto": "Kasse"},
    )
    assert antwort["Location"] == _url(uebergabe, 6)
    kaution = Kaution.objects.get(vertrag=uebergabe.vertrag)
    assert (kaution.betrag, kaution.art, kaution.eingang_am) == (
        Decimal("180.00"),
        Kaution.Art.BAR,
        D(2026, 12, 1),
    )
    frisch = Uebergabe.objects.get(pk=uebergabe.pk)
    assert not any("Kaution" in h for h in services.uebergabe_hindernisse(frisch))


def test_kaution_ohne_betrag_und_ohne_keine_ist_ein_satz(
    uebergabe: Uebergabe, angemeldet: Client
) -> None:
    """Review 2, A10: Ohne Betrag und ohne „keine Kaution“ bleibt der Schritt stehen, mit
    einem ganzen Satz als Meldung."""
    antwort = angemeldet.post(_url(uebergabe, 5), {"betrag": "", "art": "ueberweisung"})
    assert antwort.status_code == 200
    assert "Bitte den Betrag der Kaution angeben" in antwort.content.decode()


def test_ohne_zaehler_ohne_begruendung_bleibt_stehen(
    uebergabe: Uebergabe, angemeldet: Client
) -> None:
    """Review 2, A10: Ohne Zähler und ohne Begründung geht es nicht still weiter."""
    antwort = angemeldet.post(_url(uebergabe, 2), {})
    assert antwort.status_code == 200
    assert "begründen" in antwort.content.decode()


def test_zaehler_und_schluessel_im_schritt_anlegen(
    uebergabe: Uebergabe, garage: Einheit, angemeldet: Client
) -> None:
    """Review 2, A10: „Zähler anlegen“ und „Schlüssel erfassen“ gehen direkt im Schritt;
    danach zeigt der Schritt den neuen Zähler bzw. Schlüssel zur Eingabe."""
    antwort = angemeldet.post(
        _url(uebergabe, 2),
        {"aktion": "zaehler_anlegen", "einheit": garage.pk, "art": "strom", "nummer": "Z-9"},
    )
    assert antwort["Location"] == _url(uebergabe, 2)
    assert garage.zaehler.filter(nummer="Z-9").exists()
    assert "Strom Z-9" in angemeldet.get(_url(uebergabe, 2)).content.decode()

    antwort = angemeldet.post(
        _url(uebergabe, 4),
        {"aktion": "schluessel_anlegen", "einheit": garage.pk, "art": "schluessel", "anzahl": 2},
    )
    assert antwort["Location"] == _url(uebergabe, 4)
    (schluessel,) = garage.schluessel.all()
    assert (schluessel.anzahl, schluessel.inhaber) == (2, Schluessel.Inhaber.HOF)
    assert (
        f'name="schluessel_{schluessel.pk}"' in angemeldet.get(_url(uebergabe, 4)).content.decode()
    )


def test_zusammenfassung_nennt_was_fehlt_mit_links(
    uebergabe: Uebergabe, angemeldet: Client
) -> None:
    """Review 2, A10: Der letzte Schritt fasst zusammen, was fehlt, je mit Link zum Schritt."""
    seite = angemeldet.get(_url(uebergabe, 7)).content.decode()
    assert "Was noch fehlt" in seite
    for nummer, text in (
        (1, "noch nicht unterschrieben"),
        (5, "Kaution ist noch nicht eingegangen"),
        (6, "Foto der Unterschrift"),
        (2, "Kein Zähler erfasst"),
        (3, "Noch kein Foto"),
        (4, "Keine Schlüssel erfasst"),
    ):
        assert f'<a href="{_url(uebergabe, nummer)}">' in seite
        assert text in seite


def test_leere_dateiteile_erreichen_keine_bildverarbeitung(
    uebergabe: Uebergabe, angemeldet: Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review 2, A1: Leere Datei-Teile (Safari: ``filename=""``, application/octet-stream,
    kein Inhalt; ebenso ``SimpleUploadedFile("", b"")``) werden vor jeder Prüfung und
    Speicherung verworfen. Weder Fotoprüfung noch Ablage sehen sie."""
    from django.core.files.uploadedfile import SimpleUploadedFile

    from apps.stammdaten import forms as f

    def nie(*_: object, **__: object) -> None:
        raise AssertionError("leerer Datei-Teil erreicht die Bildverarbeitung")

    monkeypatch.setattr(f, "foto_pruefen", nie)
    monkeypatch.setattr(services, "uebergabe_foto_hinzufuegen", nie)
    antwort = angemeldet.post(
        _url(uebergabe, 3),
        data=_multipart([("fotos", "", b""), ("fotos", "IMG_0001.HEIC", b"")]),
        content_type=f"multipart/form-data; boundary={GRENZE}",
    )
    assert antwort["Location"] == _url(uebergabe, 4)
    # Eine Datei mit Namen, aber ohne Inhalt (``SimpleUploadedFile("", b"")`` lehnt Django
    # schon beim Bauen ab, deshalb hier mit Namen).
    antwort = angemeldet.post(_url(uebergabe, 3), {"fotos": SimpleUploadedFile("leer.jpg", b"")})
    assert antwort["Location"] == _url(uebergabe, 4)
