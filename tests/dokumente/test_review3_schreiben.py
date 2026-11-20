"""Review 3, Punkt 6: Hinweis „Senden gesperrt“ als ein Satz, „Neu erzeugen“ nach dem
Nachtragen des Briefkopfs, Hilfetext der Serienmitteilung ohne Platzhalter-Syntax."""

from __future__ import annotations

import datetime
from decimal import Decimal
from typing import Any

import pytest
from django.test import Client
from django.urls import reverse
from django.utils.html import strip_tags

from apps.dokumente import services
from apps.dokumente.models import Schreiben, Vorlage
from apps.dokumente.vorlagenarten import Versandweg
from apps.kern.models import Benutzer, Objekt
from tests.dokumente.conftest import freigegeben, kontext
from tests.dokumente.test_briefkopf_pflicht import UNVOLLSTAENDIG

pytestmark = pytest.mark.django_db


def _html(client: Client, schreiben: Schreiben) -> str:
    return client.get(reverse("dokumente:schreiben", args=[schreiben.pk])).content.decode()


def _text(client: Client, schreiben: Schreiben) -> str:
    return " ".join(strip_tags(_html(client, schreiben)).split())


def _ohne_absender(settings: Any) -> Any:
    vollstaendig = settings.DOKUMENTE
    settings.DOKUMENTE = {**vollstaendig, "ABSENDER": dict(UNVOLLSTAENDIG)}
    return vollstaendig


def test_hinweis_gesperrt_nicht_doppelt(
    angemeldet: Client, mitteilung: Vorlage, objekt: Objekt, settings: Any
) -> None:
    """Review 3, Punkt 6: Der Hinweis lautet nicht „Senden gesperrt: Senden ist gesperrt: …“,
    sondern ist ein Satz."""
    _ohne_absender(settings)
    schreiben = services.schreiben_erzeugen(mitteilung, objekt, kontext(objekt), Versandweg.MAIL)
    text = _text(angemeldet, schreiben)
    assert "Senden gesperrt: Senden ist gesperrt" not in text
    assert text.count("Senden ist gesperrt") == 1


def test_sperrsatz_setzt_den_anfang_genau_einmal() -> None:
    """Review 3, Punkt 6: Der Hinweis entsteht an einer Stelle (``sperrsatz``): „Senden ist
    gesperrt: “ steht genau einmal davor, auch wenn ein Grund (etwa eine weitere Sperre) ihn
    schon trägt oder mehrere Gründe zusammenkommen."""
    assert services.sperrsatz([]) == ""
    assert services.sperrsatz(["Im Briefkopf fehlt der Name."]) == (
        "Senden ist gesperrt: Im Briefkopf fehlt der Name."
    )
    zwei = services.sperrsatz(
        [
            "Senden ist gesperrt: Im Briefkopf fehlt der Name.",
            "Senden gesperrt: die Anschrift fehlt.",
        ]
    )
    assert zwei == "Senden ist gesperrt: Im Briefkopf fehlt der Name. Die Anschrift fehlt."
    assert zwei.count("gesperrt") == 1


def test_kontext_bleibt_mit_typen_erhalten(mitteilung: Vorlage, objekt: Objekt) -> None:
    """Review 3, Punkt 6: Das Schreiben merkt sich seine Angaben (ohne Absender, Datum,
    Zeichen); Decimal und Datum kommen mit ihrem Typ zurück."""
    werte = kontext(objekt) | {"summe": {"offen": Decimal("12.30")}}
    schreiben = services.schreiben_erzeugen(mitteilung, objekt, werte, Versandweg.MAIL)
    schreiben.refresh_from_db()
    zurueck = services._kontext_laden(schreiben.kontext)
    assert zurueck == werte
    assert isinstance(zurueck["summe"]["offen"], Decimal)
    assert zurueck["vertrag"]["ende"] == datetime.date(2027, 3, 31)
    assert "absender" not in schreiben.kontext


def test_neu_erzeugen_nach_nachgetragenem_briefkopf(
    angemeldet: Client, mitteilung: Vorlage, objekt: Objekt, benutzer: Benutzer, settings: Any
) -> None:
    """Review 3, Punkt 6: Ist der Briefkopf nachgetragen, gibt es den Knopf „Neu erzeugen“
    statt „verwerfen und neu erzeugen“. Ein Klick erzeugt das Schreiben mit denselben
    Angaben neu und verwirft das alte; das neue lässt sich senden."""
    vollstaendig = _ohne_absender(settings)
    alt = services.schreiben_erzeugen(mitteilung, objekt, kontext(objekt), Versandweg.MAIL)
    url = reverse("dokumente:schreiben_neu_erzeugen", args=[alt.pk])
    assert f'action="{url}"' not in _html(angemeldet, alt)  # Briefkopf fehlt noch

    settings.DOKUMENTE = vollstaendig
    assert "verwerfen und neu erzeugen" not in _text(angemeldet, alt)
    seite = _html(angemeldet, alt)
    assert f'action="{url}"' in seite and ">Neu erzeugen</button>" in seite

    antwort = angemeldet.post(url)
    alt.refresh_from_db()
    assert alt.status == Schreiben.Status.VERWORFEN
    neu = Schreiben.objects.exclude(pk=alt.pk).get()
    assert antwort["Location"] == reverse("dokumente:schreiben", args=[neu.pk])
    assert neu.status == Schreiben.Status.BEREIT
    assert neu.absender_luecken == ""
    assert (neu.bezug_typ_id, neu.bezug_id) == (alt.bezug_typ_id, alt.bezug_id)
    assert (neu.versandweg, neu.empfaenger_email) == (alt.versandweg, alt.empfaenger_email)
    assert (neu.betreff, neu.inhalt_html) == (alt.betreff, alt.inhalt_html)
    assert services.senden_gesperrt(neu) == ""
    services.schreiben_senden(neu, benutzer, pruefsumme=neu.pdf_sha256)


def test_neu_erzeugen_nur_wenn_bereit(mitteilung: Vorlage, objekt: Objekt) -> None:
    """Review 3, Punkt 6: Ein verworfenes Schreiben lässt sich nicht neu erzeugen."""
    alt = services.schreiben_erzeugen(mitteilung, objekt, kontext(objekt), Versandweg.MAIL)
    services.schreiben_verwerfen(alt)
    assert not services.neu_erzeugbar(alt)
    with pytest.raises(services.SchreibenFehler):
        services.schreiben_neu_erzeugen(alt)


def test_serie_hilfetext_ohne_platzhalter(angemeldet: Client) -> None:
    """Review 3, Punkt 6: Der Hilfetext der Serienmitteilung zeigt keine Platzhalter-Syntax,
    sondern „erscheint im Schreiben an der Stelle des Mitteilungstexts“."""
    seite = angemeldet.get(reverse("dokumente:serie_neu")).content.decode()
    assert "mitteilung.text" not in seite
    assert "rscheint im Schreiben an der Stelle des Mitteilungstexts" in seite


FREITEXT_VORLAGE = (
    "<p>{{ empfaenger.anrede }},</p>\n<p>wir möchten Sie zum Objekt\n{{ objekt.kuerzel }}"
    " informieren.</p>\n{% if mitteilung.text %}{{ mitteilung.text|linebreaks }}{% endif %}\n"
    "<p>Mit freundlichen Grüßen<br>{{ absender.name }}</p>"
)


def test_mailtext_mit_freitext_in_absaetzen(
    briefkopf: Vorlage, benutzer: Benutzer, objekt: Objekt
) -> None:
    """Review 3, Mailtext: Der Freitext einer Mitteilung steht als Absätze im Schreiben, nicht
    als „<p>…</p>“. Der Mailtext ist Klartext: Absätze durch Leerzeilen getrennt, keine
    Tags, Entities aufgelöst, Umbrüche im Quelltext zählen nicht."""
    vorlage = freigegeben("Mitteilung an Nutzer", "mitteilung", FREITEXT_VORLAGE, benutzer)
    werte = kontext(objekt) | {
        "mitteilung": {"text": "Erster Absatz & mehr.\n\nZweiter Absatz,\nzweite Zeile <b>."}
    }
    schreiben = services.schreiben_erzeugen(vorlage, objekt, werte, Versandweg.MAIL)
    assert "&lt;p&gt;" not in schreiben.inhalt_html
    assert "<p>Erster Absatz &amp; mehr.</p>" in schreiben.inhalt_html
    assert "&lt;b&gt;" in schreiben.inhalt_html  # eingetipptes HTML bleibt maskiert

    text = services.mail_text(schreiben)
    assert "<p>" not in text and "&amp;" not in text
    assert text == (
        "Guten Tag Frau Beispiel,\n\n"
        f"wir möchten Sie zum Objekt {objekt.kuerzel} informieren.\n\n"
        "Erster Absatz & mehr.\n\n"
        "Zweiter Absatz,\nzweite Zeile <b>.\n\n"
        f"Mit freundlichen Grüßen\n{services.absender()['name']}\n"
    )
