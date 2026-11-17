"""Ablage: Dokument, Dokumentbezug, Dateiname, Dubletten, Fotos, Löschfrist (Schritt 3a)."""

from __future__ import annotations

import datetime
import io

import pytest
from django.contrib.contenttypes.models import ContentType
from django.test import Client, override_settings
from django.urls import reverse
from PIL import Image
from pypdf import PdfReader

from apps.dokumente import dokumentarten, services, umwandeln
from apps.dokumente.models import Dokument, Dokumentbezug
from apps.kern import personenfelder
from apps.kern.models import Objekt
from testhof.aufbau import Testhof
from tests.dokumente.conftest import Belegwelt, gueltige_buchung, rechnung_pdf

pytestmark = pytest.mark.django_db

TAG = datetime.date(2026, 11, 17)


def foto(
    farbe: str = "white", groesse: tuple[int, int] = (400, 600), exif_drehung: int = 0
) -> bytes:
    bild = Image.new("RGB", groesse, farbe)
    puffer = io.BytesIO()
    exif = Image.Exif()
    if exif_drehung:
        exif[0x0112] = exif_drehung  # Orientation
    bild.save(puffer, format="JPEG", exif=exif)
    return puffer.getvalue()


def ablegen(inhalt: bytes, art: str = dokumentarten.BELEG) -> services.Ablage:
    return services.dokument_ablegen(inhalt, art=art, datum=TAG, herkunft=Dokument.Herkunft.UPLOAD)


def test_dateiname_aus_art_datum_und_id_nie_aus_personennamen(belegwelt: Belegwelt) -> None:
    """Schritt 3a, Aufgabe 2: Dateien liegen im Medienordner, Name aus Art, Datum und ID;
    nie aus Personennamen, auch wenn die Bemerkung einen Namen nennt."""
    ablage = services.dokument_ablegen(
        rechnung_pdf(),
        art=dokumentarten.BELEG,
        datum=TAG,
        herkunft=Dokument.Herkunft.UPLOAD,
        bemerkung="Von Erika Beispiel bekommen",
    )
    dokument = ablage.dokument
    assert ablage.neu
    assert dokument.datei.name == f"ablage/2026/beleg_2026-11-17_{dokument.pk}.pdf"
    assert "Erika" not in dokument.datei.name and "Beispiel" not in dokument.datei.name
    assert dokument.sha256 and len(dokument.sha256) == 64
    with dokument.datei.open("rb") as datei:
        assert datei.read() == rechnung_pdf()


def test_dublette_wird_am_hash_erkannt(belegwelt: Belegwelt) -> None:
    """Schritt 3a, Aufgabe 3: Dieselbe Datei ein zweites Mal legt nichts Neues an."""
    erste = ablegen(rechnung_pdf())
    zweite = ablegen(rechnung_pdf())
    andere = ablegen(rechnung_pdf(nummer="R-2"))
    assert zweite.neu is False and zweite.dokument == erste.dokument
    assert andere.neu and andere.dokument != erste.dokument
    assert Dokument.objects.count() == 2


def test_foto_wird_pdf_und_das_original_bleibt(belegwelt: Belegwelt) -> None:
    """Schritt 3a, Aufgabe 4 (H6.2): Ein Foto vom Handy wird ein PDF; das Foto bleibt als
    Original. Die Drehung aus EXIF ist angewandt, das PDF hat genau eine Seite."""
    bild = foto(groesse=(600, 400), exif_drehung=6)  # quer aufgenommen, hochkant gemeint
    dokument = ablegen(bild).dokument
    assert dokument.dateityp == "image/jpeg"
    assert dokument.datei.name.endswith(f"beleg_2026-11-17_{dokument.pk}.pdf")
    assert dokument.original.name.endswith(f"original/beleg_2026-11-17_{dokument.pk}.jpg")
    with dokument.datei.open("rb") as datei:
        seiten = PdfReader(datei).pages
        assert len(seiten) == 1
        assert seiten[0].mediabox.height > seiten[0].mediabox.width
    with dokument.original.open("rb") as datei:
        assert datei.read() == bild
    assert dokument.sha256 == services._sha256(bild)


def test_ungueltige_datei_wird_abgewiesen(belegwelt: Belegwelt) -> None:
    """Schritt 3a: Weder PDF noch Foto: nichts wird abgelegt."""
    with pytest.raises(umwandeln.DateiUngueltig):
        ablegen(b"Das ist kein Beleg")
    with pytest.raises(umwandeln.DateiUngueltig):
        ablegen(b"")
    assert not Dokument.objects.exists()


def test_loeschfrist_je_art_aus_der_konfiguration(belegwelt: Belegwelt) -> None:
    """Schritt 3a: Löschfrist je Dokumentart aus DOKUMENTE["LOESCHFRIST_JAHRE"], gerechnet ab
    dem Ende des Jahres (10 Jahre für Belege, 6 für Mahnungen)."""
    beleg = ablegen(rechnung_pdf()).dokument
    mahnung = ablegen(rechnung_pdf("M-1"), art=dokumentarten.MAHNUNG_LIEFERANT).dokument
    assert beleg.loeschfrist == datetime.date(2036, 12, 31)
    assert mahnung.loeschfrist == datetime.date(2032, 12, 31)


def test_loeschfrist_folgt_der_einstellung(belegwelt: Belegwelt) -> None:
    """Schritt 3a: Die Fristen stehen an einer Stelle in den Einstellungen."""
    from django.conf import settings as django_settings

    neu = {**django_settings.DOKUMENTE, "LOESCHFRIST_JAHRE": {"beleg": 8}}
    with override_settings(DOKUMENTE=neu):
        dokument = ablegen(rechnung_pdf()).dokument
    assert dokument.loeschfrist == datetime.date(2034, 12, 31)


def test_art_und_datum_aendern_benennt_die_datei_um(belegwelt: Belegwelt) -> None:
    """Schritt 3a: Nach einer Änderung von Art oder Datum folgen Dateiname und Löschfrist."""
    dokument = ablegen(rechnung_pdf()).dokument
    alt = dokument.datei.name
    services.dokument_aendern(
        dokument, art=dokumentarten.MAHNUNG_LIEFERANT, datum=datetime.date(2025, 3, 1)
    )
    dokument.refresh_from_db()
    assert dokument.datei.name == f"ablage/2025/mahnung_lieferant_2025-03-01_{dokument.pk}.pdf"
    assert not dokument.datei.storage.exists(alt)
    assert dokument.loeschfrist == datetime.date(2031, 12, 31)
    with dokument.datei.open("rb") as datei:
        assert datei.read() == rechnung_pdf()


def test_dokumentbezug_ist_generisch(belegwelt: Belegwelt) -> None:
    """Schritt 3a, Aufgabe 1: Dokumentbezug zu beliebigen Modellen (hier Objekt, Bankumsatz und
    Buchung; Einheit, Partei und Vertrag kommen in Schritt 4 ohne Änderung dazu)."""
    dokument = ablegen(rechnung_pdf()).dokument
    umsatz = belegwelt.ausgabe(datetime.date(2026, 11, 3), "119.00")
    buchung = gueltige_buchung(umsatz)
    for bezug in (belegwelt.hof, umsatz, buchung):
        services.dokument_bezug_setzen(dokument, bezug)
        services.dokument_bezug_setzen(dokument, bezug)  # idempotent
    assert Dokumentbezug.objects.filter(dokument=dokument).count() == 3
    typen = {b.bezug_typ.model_class() for b in dokument.bezuege.all()}
    assert {t.__name__ for t in typen if t} == {"Objekt", "Bankumsatz", "Buchung"}
    for bezug in (belegwelt.hof, umsatz, buchung):
        assert list(services.dokumente_zu(bezug)) == [dokument]

    assert services.dokument_bezug_loesen(dokument, belegwelt.hof)
    assert not services.dokumente_zu(belegwelt.hof).exists()
    assert (
        Dokumentbezug.objects.filter(bezug_typ=ContentType.objects.get_for_model(Objekt)).count()
        == 0
    )


def test_dokumentbezug_mit_stammdaten(testhof: Testhof) -> None:
    """Schritt 3a mit Schritt 4: Derselbe generische Dokumentbezug hängt ein Dokument an
    Einheit, Partei und Vertrag, ohne Änderung an der Ablage."""
    stammdaten = testhof.stammdaten
    vertrag = next(iter(stammdaten.vertraege.values()))
    einheit = next(iter(stammdaten.einheiten.values()))
    partei = stammdaten.parteien[0]
    dokument = ablegen(rechnung_pdf("Vertrag-Scan"), art=dokumentarten.SONSTIGES).dokument
    for bezug in (einheit, partei, vertrag):
        services.dokument_bezug_setzen(dokument, bezug)
        assert dokument in services.dokumente_zu(bezug)
    assert {type(b.bezug).__name__ for b in dokument.bezuege.all()} == {
        "Einheit",
        "Partei",
        "Vertrag",
    }


def test_personenfelder_der_ablage(belegwelt: Belegwelt) -> None:
    """Schritt 3a (CLAUDE.md, Regel 4.6): Lieferant (kann eine Person sein), Bemerkung und
    Kennung der Mail sind Personenfelder."""
    from apps.dokumente.models import Lieferant

    assert personenfelder.personenfelder(Lieferant) >= {"name", "iban", "email"}
    assert personenfelder.personenfelder(Dokument) >= {"bemerkung", "eingangskennung"}


def test_dateien_nur_mit_anmeldung(angemeldet: Client) -> None:
    """Schritt 3a, Aufgabe 2: Dateien gibt es nur über Django mit Anmeldung, nie direkt."""
    dokument = services.dokument_ablegen(
        foto(), art=dokumentarten.BELEG, datum=TAG, herkunft=Dokument.Herkunft.UPLOAD
    ).dokument
    for name in ("dokumente:dokument_pdf", "dokumente:dokument_original", "dokumente:dokument"):
        antwort = Client().get(reverse(name, args=[dokument.pk]))
        assert antwort.status_code == 302, name
        assert antwort["Location"].startswith(reverse("account_login"))
    antwort = angemeldet.get(reverse("dokumente:dokument_pdf", args=[dokument.pk]))
    assert antwort.status_code == 200
    assert antwort["Content-Type"] == "application/pdf"
    assert b"".join(antwort.streaming_content).startswith(b"%PDF")  # type: ignore[attr-defined]
    antwort = angemeldet.get(reverse("dokumente:dokument_original", args=[dokument.pk]))
    assert antwort["Content-Type"] == "image/jpeg"
    seite = angemeldet.get(reverse("dokumente:dokument", args=[dokument.pk])).content.decode()
    assert "aufbewahren bis" in seite and "31.12.2036" in seite
    assert angemeldet.get(reverse("dokumente:ablage")).status_code == 200
