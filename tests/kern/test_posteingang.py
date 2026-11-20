"""Posteingang der Arbeitslisten: Arten, Aktionen, Links (Review 2, B2; Review 1, Punkt 8)."""

from __future__ import annotations

import pytest
from django.core.management import call_command
from django.test import Client
from django.urls import reverse
from django.utils.html import strip_tags

from apps.bank import enablebanking
from apps.bank import services as bank
from apps.bank.models import Bankabruf, Bankkonto
from apps.kern import services
from apps.kern.models import Arbeitsposten, Lauf
from apps.kern.templatetags.kern import pfade_verlinken
from tests.bank.conftest import fake, vivid, vr  # noqa: F401  (Fixtures)

pytestmark = pytest.mark.django_db

HTMX = {"HTTP_HX_REQUEST": "true"}


def test_links_die_arten_mit_zahl_rechts_die_posten(angemeldet: Client) -> None:
    """Review 2, B2: Links die Arten mit Zahl, rechts die Posten; ``?art=`` wählt eine Art.
    Am Handy erst die Arten: Erst mit einer Wahl trägt der Posteingang „gewaehlt“."""
    for _ in range(2):
        services.arbeitsposten_anlegen("Frist fällig")
    einzeln = services.arbeitsposten_anlegen("Selbsttest")

    alle = angemeldet.get(reverse("kern:arbeitslisten")).content.decode()
    assert "Alle offenen" in alle and '<span class="zaehler">3</span>' in alle
    assert "Frist fällig" in alle and '<span class="zaehler">2</span>' in alle
    assert "posteingang-gewaehlt" not in alle
    assert 'src="/static/js/posteingang.js"' in alle

    nur = angemeldet.get(reverse("kern:arbeitslisten"), {"art": "Frist fällig"}).content.decode()
    assert "posteingang-gewaehlt" in nur
    assert f'id="posten-{einzeln.pk}"' not in nur
    assert 'aria-current="page"><span>Frist fällig</span>' in nur

    teil = angemeldet.get(
        reverse("kern:arbeitslisten"), {"art": "Selbsttest"}, HTTP_HX_TARGET="posteingang", **HTMX
    ).content.decode()
    assert "<html" not in teil and f'id="posten-{einzeln.pk}"' in teil


def test_alle_offenen_gewaehlt_zeigt_am_handy_die_liste(angemeldet: Client) -> None:
    """Apple-Design, Bereich Arbeitslisten: „Alle offenen“ führt auf ``?art=alle``. Dann trägt
    der Posteingang „gewaehlt“ (am Handy die Liste statt der Arten), „Alle offenen“ ist
    gewählt, und das Formular der Liste gibt die Ansicht an die Aktionen weiter."""
    posten = services.arbeitsposten_anlegen("Selbsttest")

    ohne = angemeldet.get(reverse("kern:arbeitslisten")).content.decode()
    assert 'href="/arbeitslisten/?art=alle"' in ohne

    alle = angemeldet.get(reverse("kern:arbeitslisten"), {"art": "alle"}).content.decode()
    assert "posteingang-gewaehlt" in alle
    assert 'aria-current="page"><span>Alle offenen</span>' in alle
    assert f'id="posten-{posten.pk}"' in alle
    assert '<input type="hidden" name="art" value="alle">' in alle
    assert 'hx-get="/arbeitslisten/?art=alle" hx-trigger="posteingangNeu' in alle

    antwort = angemeldet.post(
        reverse("kern:posten_sammel"),
        {"aktion": "zurueckstellen", "posten": [posten.pk], "art": "alle"},
    )
    assert antwort.status_code == 302 and antwort["Location"] == "/arbeitslisten/?art=alle"


def test_mehrfachauswahl_mit_aktion_fuer_alle(angemeldet: Client) -> None:
    """Review 2, B2: Mehrfachauswahl; eine Aktion ohne Eingabe gilt für alle gewählten."""
    a = services.arbeitsposten_anlegen("Selbsttest")
    b = services.arbeitsposten_anlegen("Selbsttest")
    c = services.arbeitsposten_anlegen("Selbsttest")

    antwort = angemeldet.post(
        reverse("kern:posten_sammel"),
        {"aktion": "zurueckstellen", "posten": [a.pk, b.pk]},
        **HTMX,
    )

    assert "2 von 2 Posten zurückstellen." in antwort.content.decode()
    status = dict(Arbeitsposten.objects.values_list("pk", "status"))
    assert (status[a.pk], status[b.pk], status[c.pk]) == (
        "zurueckgestellt",
        "zurueckgestellt",
        "offen",
    )
    assert angemeldet.post(reverse("kern:posten_sammel"), {"aktion": "loeschen"}).status_code == 400


def test_tastatur_und_naechster_posten(angemeldet: Client) -> None:
    """Review 2, B2: Tastatur j/k, Enter, e (static/js/posteingang.js). Jede Zeile trägt die
    Marken, die das Skript braucht; die Seite nennt die Tasten."""
    posten = services.arbeitsposten_anlegen("Selbsttest")

    html = angemeldet.get(reverse("kern:arbeitslisten")).content.decode()

    assert "j/k wechselt den Posten" in html and "Enter übernimmt" in html
    assert f'id="posten-{posten.pk}" data-posten tabindex="-1"' in html
    skript = (
        __import__("pathlib").Path(__file__).resolve().parents[2] / "static/js/posteingang.js"
    ).read_text()
    for teil in ('e.key === "j"', 'e.key === "k"', 'e.key === "Enter"', 'e.key === "e"'):
        assert teil in skript
    assert "htmx:afterSettle" in skript  # nach einer Aktion: nächster Posten


def test_pfade_in_meldungen_sind_links() -> None:
    """Review 1, Punkt 8: Pfade in Meldungen sind Links; der übrige Text bleibt maskiert."""
    html = str(pfade_verlinken("Erneuern: /bank/konten/3/freigabe/. <b>x</b>"))
    assert '<a href="/bank/konten/3/freigabe/">/bank/konten/3/freigabe/</a>.' in html
    assert "&lt;b&gt;x&lt;/b&gt;" in html
    assert "docs/betrieb.md" in str(pfade_verlinken("Siehe docs/betrieb.md, Sicherung."))
    assert "<a" not in str(pfade_verlinken("Siehe docs/betrieb.md, Sicherung."))


def test_jeder_posten_verlinkt_auf_seinen_bezug(angemeldet: Client) -> None:
    """Review 1, Punkt 8: Jeder Posten hat einen Link zu seinem Bezug, auch ein Lauf."""
    lauf = services.lauf_starten("selbsttest")
    posten = services.arbeitsposten_anlegen("Selbsttest", bezug=lauf)

    html = angemeldet.get(reverse("kern:arbeitslisten")).content.decode()

    assert f"{reverse('kern:laeufe')}#lauf-{lauf.pk}" in html
    assert f"Lauf #{lauf.pk} selbsttest" in html
    assert posten.hinweis == ""


def test_bankabruf_gescheitert_mit_passenden_aktionen(
    vivid: Bankkonto,  # noqa: F811
    fake: enablebanking.FakeEnableBanking,  # noqa: F811
    angemeldet: Client,
) -> None:
    """Review 1, Punkt 8: „Bankabruf gescheitert“ bietet „Erneut abrufen“ und „Freigabe
    erneuern“ statt „Erledigt“. „Erneut abrufen“ startet bank_abruf für dieses Konto; gelingt
    es, ist der Posten erledigt, und die Meldung verlinkt die Umsätze."""
    fake.fehler = "Umsätze abrufen: HTTP 500"
    call_command("bank_abruf")
    posten = Arbeitsposten.objects.get(art=bank.ART_ABRUF)
    assert posten.aktionen == list(bank.AKTIONEN_ABRUF)
    assert "erledigen" not in posten.aktionen

    html = angemeldet.get(reverse("kern:arbeitslisten")).content.decode()
    assert "Erneut abrufen" in html and "Freigabe erneuern" in html
    assert reverse("bank:freigabe", args=[vivid.pk]) in html

    url = reverse("kern:arbeitsposten_aktion", args=[posten.pk, "bank_erneut_abrufen"])
    nochmal = angemeldet.post(url, **HTMX).content.decode()
    assert "wieder gescheitert: Umsätze abrufen: HTTP 500" in nochmal
    posten.refresh_from_db()
    assert posten.status == Arbeitsposten.Status.OFFEN

    fake.fehler = None
    gelungen = angemeldet.post(url, **HTMX).content.decode()
    assert f"Abruf für Konto #{vivid.pk} gelungen" in strip_tags(gelungen)
    posten.refresh_from_db()
    assert posten.status == Arbeitsposten.Status.ERLEDIGT
    letzter = Lauf.objects.filter(name="bank_abruf").order_by("-pk").first()
    assert letzter is not None and letzter.status == Lauf.Status.ERFOLGREICH
    assert vivid.abrufe.filter(status=Bankabruf.Status.ERFOLGREICH).exists()


def test_abruf_starten_nur_fuer_enable_banking(
    vivid: Bankkonto,  # noqa: F811
    vr: Bankkonto,  # noqa: F811
    fake: enablebanking.FakeEnableBanking,  # noqa: F811
) -> None:
    """Review 1, Punkt 8: ``bank.services.abruf_starten`` (Signatur wie R2-9) ruft ein Konto
    als eigenen Lauf ab und liefert Lauf und Abruf; ein zugeliefertes Konto lehnt es ab."""
    ergebnis = bank.abruf_starten(vivid)
    assert ergebnis.lauf.name == "bank_abruf"
    assert ergebnis.abruf is not None and ergebnis.abruf.konto == vivid
    with pytest.raises(bank.BankFehler, match="ruft das Hof-System nicht selbst ab"):
        bank.abruf_starten(vr)
