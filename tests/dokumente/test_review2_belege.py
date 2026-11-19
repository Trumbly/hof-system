"""Review 2, A6 und A7: Beleg-Formular nach dem Bestätigen, Vorschlag, Abweichung zur
Abbuchung. Alles mit dem Testhof (erfundene Daten)."""

from __future__ import annotations

import io
import re
from decimal import Decimal

import pytest
from django.core.management import call_command
from django.test import Client
from django.urls import reverse

from apps.bank import fingerabdruck
from apps.bank.models import Bankumsatz
from apps.dokumente import services
from apps.dokumente.models import Beleg
from apps.kern import services as kern_services
from apps.kern.models import Arbeitsposten
from testhof.aufbau import Testhof


def _wert(html: str, name: str) -> str | None:
    """Der value eines Eingabefelds, None wenn es keinen hat."""
    feld = re.search(rf'<input[^>]*name="{name}"[^>]*>', html)
    assert feld is not None, name
    wert = re.search(r'value="([^"]*)"', feld.group(0))
    return wert.group(1) if wert else None


def _abbuchung_zum_offenen_beleg(testhof: Testhof) -> Bankumsatz:
    """Die Abbuchung, deren Rechnung der offene Beleg aus dem Postfach ist (die letzte
    Ausgabe dieses Lieferanten, testhof.belege)."""
    lieferant = _offen(testhof).lieferant
    assert lieferant is not None
    kandidaten = [
        u
        for u in Bankumsatz.objects.filter(richtung=Bankumsatz.Richtung.AUSGANG).order_by(
            "-buchungsdatum", "-pk"
        )
        if fingerabdruck.iban_normal(u.gegen_iban) == lieferant.iban
    ]
    return kandidaten[0]


def _offen(testhof: Testhof) -> Beleg:
    """Eine eigene Kopie des offenen Belegs (das Objekt des Testhofs bleibt unverändert)."""
    return Beleg.objects.get(pk=testhof.belege.offen.pk)


def _bestaetigen(beleg: Beleg, betrag: Decimal, nummer: str = "") -> Beleg:
    return services.beleg_bestaetigen(
        beleg,
        lieferant=beleg.lieferant,
        betrag=betrag,
        objekt=beleg.objekt,
        kategorie=beleg.kategorie,
        leistungsjahr=beleg.leistungsjahr,
        rechnungsnummer=nummer,
    )


def _posten(beleg: Beleg) -> list[Arbeitsposten]:
    return list(
        Arbeitsposten.objects.filter(
            art=services.ART_BELEG_ABWEICHUNG, bezug_id=beleg.pk, status="offen"
        )
    )


# --- A6 ------------------------------------------------------------------------------


def test_beleg_formular_zeigt_datum_und_betrag_nach_dem_bestaetigen(
    testhof: Testhof, angemeldet: Client
) -> None:
    """Review 2, A6: Nach dem Bestätigen stehen Belegdatum und Betrag im Formular. Das
    Datumsfeld (type=date) braucht JJJJ-MM-TT; vorher stand dort „17.10.2026“, und der
    Browser zeigte ein leeres Feld."""
    beleg = _offen(testhof)
    seite = angemeldet.get(reverse("dokumente:beleg", args=[beleg.pk])).content.decode()
    daten = {
        "lieferant": beleg.lieferant_id,
        "datum": _wert(seite, "datum"),
        "betrag": "627,34",
        "objekt": beleg.objekt_id,
        "kategorie": beleg.kategorie_id,
        "leistungsjahr": beleg.leistungsjahr,
        "umlagefaehiger_anteil": "",
        "faellig_am": "",
        "rechnungsnummer": "",
    }
    assert daten["datum"] == f"{beleg.dokument.datum:%Y-%m-%d}"

    antwort = angemeldet.post(reverse("dokumente:beleg", args=[beleg.pk]), daten)
    assert antwort.status_code == 302, antwort.content.decode()[:2000]

    beleg.refresh_from_db()
    assert beleg.status == Beleg.Status.BESTAETIGT
    assert beleg.betrag == Decimal("627.34")
    html = angemeldet.get(antwort["Location"]).content.decode()
    assert _wert(html, "datum") == f"{beleg.dokument.datum:%Y-%m-%d}"
    assert _wert(html, "betrag") == "627,34"


def test_vorschlag_nennt_die_kostenart_der_vorbelegung(
    testhof: Testhof, angemeldet: Client
) -> None:
    """Review 2, A6: Der Hinweis über dem offenen Beleg nennt den Beleg, aus dem die
    Vorbelegung stammt, und genau deren Kostenart. Vorher nannte er den neuesten Beleg des
    Lieferanten (eine andere Kostenart als im Feld)."""
    beleg = _offen(testhof)
    assert beleg.vorschlag_aus_id is not None and beleg.vorschlag_aus_id != beleg.pk
    html = angemeldet.get(reverse("dokumente:beleg", args=[beleg.pk])).content.decode()

    hinweis = re.search(r'role="status">([^<]*)<', html)
    assert hinweis is not None
    assert f"Beleg #{beleg.vorschlag_aus_id} " in hinweis.group(1)
    assert beleg.kategorie is not None
    assert beleg.kategorie.bezeichnung in hinweis.group(1)
    auswahl = re.search(r'<select name="kategorie".*?</select>', html, re.S)
    assert auswahl is not None
    gewaehlt = re.search(r'<option value="(\d+)" selected', auswahl.group(0))
    assert gewaehlt is not None and int(gewaehlt.group(1)) == beleg.kategorie_id


def test_vorschlag_verweist_nicht_auf_sich_selbst(testhof: Testhof, angemeldet: Client) -> None:
    """Review 2, A6: Ein bestätigter Beleg ist der neueste seines Lieferanten. Weder die
    Seite noch der Vorschlag per HTMX nennen ihn als Quelle („Vorschlag aus Beleg #23“)."""
    beleg = _bestaetigen(_offen(testhof), Decimal("12.34"))
    assert services.vorschlag(beleg.lieferant, beleg.dokument.datum) is not None
    assert services.vorschlag(beleg.lieferant, beleg.dokument.datum).aus_beleg == beleg  # type: ignore[union-attr]

    html = angemeldet.get(reverse("dokumente:beleg", args=[beleg.pk])).content.decode()
    assert f"Vorschlag aus Beleg #{beleg.pk}" not in html

    antwort = angemeldet.get(
        reverse("dokumente:beleg_vorschlag"),
        {"lieferant": beleg.lieferant_id, "beleg": beleg.pk},
    )
    teil = antwort.content.decode()
    assert f"Beleg #{beleg.pk} " not in teil
    treffer = services.vorschlag(beleg.lieferant, beleg.dokument.datum, ohne=beleg)
    assert treffer is not None and treffer.aus_beleg != beleg
    assert f"Vorschlag aus Beleg #{treffer.aus_beleg.pk} " in teil
    assert treffer.kategorie.bezeichnung in teil


# --- A7 ------------------------------------------------------------------------------


def test_abweichender_betrag_bei_passender_rechnungsnummer_wird_arbeitsposten(
    testhof: Testhof, angemeldet: Client
) -> None:
    """Review 2, A7: Rechnung 4779 über 627,34 €, Abbuchung „… Rechnung 4779“ über
    527,34 €. Der Testhof legt den offenen Beleg aus dem Postfach zu dieser Abbuchung an;
    Max hat 100 € zu viel eingetippt. Es entsteht der Posten „Beleg und Abbuchung weichen
    ab“. Die Beträge stehen auf der Seite des Belegs, nicht im Hinweis (der geht nach Notion)."""
    umsatz = _abbuchung_zum_offenen_beleg(testhof)
    nummer = umsatz.verwendungszweck.rsplit(" ", 1)[-1]
    assert f"Rechnung {nummer}" in umsatz.verwendungszweck
    falsch = umsatz.betrag + Decimal("100.00")

    beleg = _bestaetigen(_offen(testhof), falsch, nummer)

    posten = _posten(beleg)
    assert len(posten) == 1
    hinweis = posten[0].hinweis
    assert f"Umsatz #{umsatz.pk}" in hinweis and "Rechnungsnummer" in hinweis
    assert "€" not in hinweis
    for betrag in (falsch, umsatz.betrag):
        assert f"{betrag}" not in hinweis and f"{betrag}".replace(".", ",") not in hinweis

    html = angemeldet.get(reverse("dokumente:beleg", args=[beleg.pk])).content.decode()
    assert "Beleg und Abbuchung weichen ab" in html
    for betrag in (falsch, umsatz.betrag):
        assert f"{betrag:.2f}".replace(".", ",") in html


def test_abweichung_nur_nach_lieferant_mit_grenze(testhof: Testhof) -> None:
    """Review 2, A7: Ohne Rechnungsnummer genügt der Lieferant (Gegen-IBAN), solange der
    Betrag höchstens um 20 % abweicht. Ein ganz anderer Betrag ist eine andere Rechnung."""
    umsatz = _abbuchung_zum_offenen_beleg(testhof)
    beleg = _bestaetigen(_offen(testhof), umsatz.betrag + Decimal("1.00"))
    assert len(_posten(beleg)) == 1
    assert "Lieferant" in _posten(beleg)[0].hinweis

    # Betrag korrigiert: Der Posten ist erledigt.
    beleg = _bestaetigen(beleg, umsatz.betrag * 3)
    assert _posten(beleg) == []


def test_passender_betrag_gibt_keinen_posten(testhof: Testhof) -> None:
    """Review 2, A7: Stimmt der Betrag, entsteht nichts; der Testhof selbst hat keinen
    solchen Posten (Belege passen genau zu ihren Abbuchungen)."""
    assert not Arbeitsposten.objects.filter(art=services.ART_BELEG_ABWEICHUNG).exists()
    umsatz = _abbuchung_zum_offenen_beleg(testhof)
    nummer = umsatz.verwendungszweck.rsplit(" ", 1)[-1]
    beleg = _bestaetigen(_offen(testhof), umsatz.betrag, nummer)
    assert _posten(beleg) == []


def test_erledigte_abweichung_kommt_im_lauf_nicht_wieder(testhof: Testhof) -> None:
    """Review 2, A7: Der Lauf kassenbuch_einordnen prüft erneut. Hat Max den Posten
    erledigt (die Abbuchung ist richtig), legt er ihn nicht wieder an."""
    umsatz = _abbuchung_zum_offenen_beleg(testhof)
    nummer = umsatz.verwendungszweck.rsplit(" ", 1)[-1]
    beleg = _bestaetigen(_offen(testhof), umsatz.betrag + Decimal("100.00"), nummer)
    kern_services.arbeitsposten_erledigen(_posten(beleg)[0])

    call_command("kassenbuch_einordnen", stdout=io.StringIO())

    assert _posten(beleg) == []
    assert services.offene_abweichungen(beleg) == []


@pytest.mark.parametrize(
    ("nummer", "zweck", "passt"),
    [
        ("4779", "Winterdienst 10/2026 Rechnung 4779", True),
        ("4779", "Winterdienst 10/2026 Rechnung 14779", False),
        ("RE-2026 0815", "Zahlung re-20260815", True),
        ("", "Rechnung 4779", False),
    ],
)
def test_nummer_im_zweck(nummer: str, zweck: str, passt: bool) -> None:
    """Review 2, A7: Die Rechnungsnummer zählt nur als eigenes Wort (oder lang genug)."""
    assert services.nummer_im_zweck(nummer, zweck) is passt


def test_nachtraeglich_geaenderter_betrag_wird_erkannt(testhof: Testhof) -> None:
    """Review 2, A7: Erst stimmt der Betrag (527,34 € wie die Abbuchung „Rechnung 4779“),
    dann ändert Max ihn auf der Seite des Belegs auf 100 € mehr. Das Speichern erkennt die
    Abweichung; zurückgeändert ist der Posten erledigt."""
    umsatz = _abbuchung_zum_offenen_beleg(testhof)
    nummer = umsatz.verwendungszweck.rsplit(" ", 1)[-1]
    beleg = _bestaetigen(_offen(testhof), umsatz.betrag, nummer)
    assert _posten(beleg) == []

    beleg = _bestaetigen(beleg, umsatz.betrag + Decimal("100.00"), nummer)
    assert len(_posten(beleg)) == 1

    beleg = _bestaetigen(beleg, umsatz.betrag, nummer)
    assert _posten(beleg) == []


def test_abweichung_auch_bei_verknuepfter_buchung(testhof: Testhof, angemeldet: Client) -> None:
    """Review 2, A7: Auch wenn Beleg und Buchung schon verknüpft sind (Sammelrechnung des
    Testhofs, zwei Teile an ihren Buchungen), fällt ein geänderter Betrag auf: Die Teile
    ergeben nicht mehr den Betrag der Abbuchung. Die Seite zeigt beide Beträge."""
    teil, anderer = (Beleg.objects.get(pk=b.pk) for b in testhof.belege.sammelrechnung)
    assert teil.buchung_id is not None and anderer.buchung_id is not None
    assert services.abweichungen(teil) == []
    alt = teil.betrag
    assert alt is not None

    teil = _bestaetigen(teil, alt + Decimal("100.00"))
    (posten,) = _posten(teil)
    assert "verknüpften Abbuchung" in posten.hinweis and "€" not in posten.hinweis
    html = angemeldet.get(reverse("dokumente:beleg", args=[teil.pk])).content.decode()
    assert "Beleg und Abbuchung weichen ab" in html and "verknüpfte Buchung" in html

    call_command("kassenbuch_einordnen", stdout=io.StringIO())
    assert len(_posten(teil)) == 1  # der Lauf legt keinen zweiten an

    teil = _bestaetigen(teil, alt)
    assert _posten(teil) == []
