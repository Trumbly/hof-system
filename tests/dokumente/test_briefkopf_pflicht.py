"""Review 1, Punkt 4: Kein Senden mit unvollständigem Briefkopf; „freigegeben von“ ist
Pflicht bei der Freigabe einer Vorlage."""

from __future__ import annotations

from typing import Any

import pytest
from django.test import Client
from django.urls import reverse

from apps.dokumente import services
from apps.dokumente.models import Schreiben, Vorlage
from apps.dokumente.vorlagenarten import Versandweg
from apps.kern.models import Benutzer, Objekt
from tests.dokumente.conftest import kontext

UNVOLLSTAENDIG = {
    "name": "Hof (Absender nicht eingerichtet)",
    "anschrift": "",
    "email": "kontakt@hof.invalid",
    "telefon": "",
    "bank": "",
    "steuernummer": "",
}


@pytest.fixture
def ohne_absender(settings: Any) -> None:
    """Die Standardwerte der Einstellungen, solange HOF_ABSENDER_* fehlt."""
    settings.DOKUMENTE = {**settings.DOKUMENTE, "ABSENDER": dict(UNVOLLSTAENDIG)}


def test_luecken_nennen_name_und_anschrift(briefkopf: Vorlage, ohne_absender: None) -> None:
    """Review 1, Punkt 4: Ohne eingerichteten Absender fehlen Name und Anschrift; der
    Standardname „… nicht eingerichtet“ gilt als fehlend."""
    luecken = services.briefkopf_luecken()
    assert luecken == [
        "der Name des Absenders (HOF_ABSENDER_NAME)",
        "die Anschrift des Absenders (HOF_ABSENDER_ANSCHRIFT)",
    ]


def test_briefkopf_vollstaendig_in_den_tests(briefkopf: Vorlage) -> None:
    """Review 1, Punkt 4: Mit erfundenem Absender (Test-Einstellungen) fehlt nichts."""
    assert services.briefkopf_luecken() == []


def test_unbedingt_gedruckte_angabe_ist_pflicht(benutzer: Benutzer) -> None:
    """Review 1, Punkt 4: Druckt der Briefkopf die Bankverbindung ohne ``{% if %}``, ist sie
    Pflicht; steht sie nur bedingt da, nicht."""
    bedingt = services.vorlage_anlegen(
        "Kopf",
        "briefkopf",
        "{{ absender.name }}{% if absender.bank %}{{ absender.bank }}{% endif %}",
    )
    services.vorlage_freigeben(bedingt, benutzer, freigegeben_von="Test")
    assert services.briefkopf_luecken() == []
    fest = services.vorlage_neue_version(bedingt)
    services.vorlage_aendern(fest, text="{{ absender.name }} {{ absender.bank }}")
    services.vorlage_freigeben(fest, benutzer, freigegeben_von="Test")
    assert services.briefkopf_luecken() == ["die Bankverbindung des Absenders (HOF_ABSENDER_BANK)"]


def test_senden_gesperrt_solange_briefkopf_fehlt(
    mitteilung: Vorlage, objekt: Objekt, benutzer: Benutzer, ohne_absender: None
) -> None:
    """Review 1, Punkt 4: Ein Schreiben mit unvollständigem Briefkopf entsteht (Vorschau),
    lässt sich aber nicht senden, auch nicht, wenn der Absender später eingetragen wird:
    Das PDF trägt den alten Briefkopf."""
    schreiben = services.schreiben_erzeugen(mitteilung, objekt, kontext(objekt), Versandweg.MAIL)
    assert "Anschrift des Absenders" in schreiben.absender_luecken
    with pytest.raises(services.BriefkopfUnvollstaendig, match="fehlt der Name des Absenders"):
        services.schreiben_senden(schreiben, benutzer, pruefsumme=schreiben.pdf_sha256)
    schreiben.refresh_from_db()
    assert schreiben.status == Schreiben.Status.BEREIT


def test_pdf_mit_altem_briefkopf_bleibt_gesperrt(
    mitteilung: Vorlage, objekt: Objekt, benutzer: Benutzer, settings: Any
) -> None:
    """Review 1, Punkt 4: Fehlten Angaben beim Erzeugen, bleibt das Schreiben gesperrt, bis es
    verworfen und neu erzeugt ist; das neue lässt sich senden."""
    vollstaendig = settings.DOKUMENTE
    settings.DOKUMENTE = {**vollstaendig, "ABSENDER": dict(UNVOLLSTAENDIG)}
    alt = services.schreiben_erzeugen(mitteilung, objekt, kontext(objekt), Versandweg.MAIL)
    settings.DOKUMENTE = vollstaendig
    assert "„Neu erzeugen“" in services.senden_gesperrt(alt)
    with pytest.raises(services.BriefkopfUnvollstaendig):
        services.schreiben_senden(alt, benutzer, pruefsumme=alt.pdf_sha256)

    services.schreiben_verwerfen(alt)
    neu = services.schreiben_erzeugen(mitteilung, objekt, kontext(objekt), Versandweg.MAIL)
    assert services.senden_gesperrt(neu) == ""
    gesendet = services.schreiben_senden(neu, benutzer, pruefsumme=neu.pdf_sha256)
    assert gesendet.status == Schreiben.Status.GESENDET


def test_seite_sperrt_knopf_und_nennt_die_luecken(
    angemeldet: Client, mitteilung: Vorlage, objekt: Objekt, ohne_absender: None
) -> None:
    """Review 1, Punkt 4: Der Knopf „Senden“ ist gesperrt; direkt daneben steht, welche
    Angaben des Briefkopfs fehlen. Ein Senden über die Adresse geht nicht hinaus."""
    schreiben = services.schreiben_erzeugen(mitteilung, objekt, kontext(objekt), Versandweg.MAIL)
    seite = angemeldet.get(reverse("dokumente:schreiben", args=[schreiben.pk])).content.decode()
    assert 'id="senden-gesperrt"' in seite
    assert (
        "Im Briefkopf fehlt der Name des Absenders (HOF_ABSENDER_NAME) und die Anschrift" in seite
    )
    assert 'disabled aria-describedby="senden-gesperrt">Senden (Mail)' in seite

    angemeldet.post(
        reverse("dokumente:schreiben_senden", args=[schreiben.pk]),
        {"pruefsumme": schreiben.pdf_sha256},
    )
    schreiben.refresh_from_db()
    assert schreiben.status == Schreiben.Status.BEREIT


def test_freigabe_braucht_namen(benutzer: Benutzer, angemeldet: Client) -> None:
    """Review 1, Punkt 4: „freigegeben von“ ist Pflicht bei der Freigabe, im Service und auf
    der Seite (Pflichtfeld im Formular); die Seite zeigt danach, wer freigegeben hat."""
    vorlage = services.vorlage_anlegen("Hinweis", "mitteilung", "<p>{{ empfaenger.anrede }}</p>")
    with pytest.raises(services.SchreibenFehler, match="wer die Vorlage freigibt"):
        services.vorlage_freigeben(vorlage, benutzer, freigegeben_von="  ")

    seite = angemeldet.get(reverse("dokumente:vorlage", args=[vorlage.pk])).content.decode()
    assert 'name="freigegeben_von"' in seite and "required" in seite
    angemeldet.post(reverse("dokumente:vorlage_freigeben", args=[vorlage.pk]))
    vorlage.refresh_from_db()
    assert vorlage.status == Vorlage.Status.ENTWURF

    angemeldet.post(
        reverse("dokumente:vorlage_freigeben", args=[vorlage.pk]),
        {"freigegeben_von": "Max nach Prüfung durch die Kanzlei"},
    )
    vorlage.refresh_from_db()
    assert vorlage.status == Vorlage.Status.FREIGEGEBEN
    assert vorlage.freigabe_durch == "Max nach Prüfung durch die Kanzlei"
    seite = angemeldet.get(reverse("dokumente:vorlage", args=[vorlage.pk])).content.decode()
    assert "von Max nach Prüfung durch die Kanzlei" in seite
