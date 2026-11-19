"""Umsatz einordnen im Posteingang (Review 2, B2 und B3) und Bündeln (Review 1, Punkt 5)."""

from __future__ import annotations

import datetime
from decimal import Decimal

import pytest
from django.test import Client
from django.urls import reverse

from apps.kassenbuch import einordnen, services
from apps.kassenbuch.models import Buchung, Kategorie, Zuordnungsregel
from apps.kern.models import Arbeitsposten
from tests.kassenbuch.conftest import KleinerHof, neue_iban, umsatz

pytestmark = pytest.mark.django_db

HTMX = {"HTTP_HX_REQUEST": "true"}
JAN = datetime.date(2026, 1, 10)
FEB = datetime.date(2026, 2, 10)


@pytest.fixture
def material(klein: KleinerHof) -> Kategorie:
    """Kategorie „Material“; dazu eine frühere Ausgabe des Kontos auf HOF (Verlauf)."""
    vorher = umsatz(
        klein.konto,
        datetime.date(2025, 12, 1),
        "9.99",
        eingang=False,
        iban=neue_iban(),
        zweck="Vorlauf",
    )
    services.umsatz_zuordnen(vorher, kategorie=klein.strom, objekt=klein.hof)
    return services.kategorie_anlegen("Material", Kategorie.Art.AUSGABE)


def _baumarkt(klein: KleinerHof, datum: datetime.date, iban: str, betrag: str = "48.90"):  # type: ignore[no-untyped-def]
    return umsatz(
        klein.konto,
        datum,
        betrag,
        eingang=False,
        iban=iban,
        name="Baumarkt Beispiel KG",
        zweck=f"Material Baumarkt {datum:%m/%Y}",
    )


def _posten(eintrag: object) -> Arbeitsposten:
    services.umsaetze_einordnen()
    return Arbeitsposten.objects.get(art=services.ART_EINORDNEN, bezug_id=eintrag.pk)  # type: ignore[attr-defined]


# --- Überschneidung gleichrangiger Regeln (Review 1, Punkt 5) ------------------------


def test_gleichrangige_ueberlappende_regel_laesst_sich_nicht_speichern(klein: KleinerHof) -> None:
    """Review 1, Punkt 5: Zwei Zuordnungsregeln mit gleicher Priorität und überlappenden
    Bedingungen lassen sich nicht speichern; die Meldung ist ein deutscher Satz mit IDs."""
    iban = neue_iban()
    erste = services.regel_anlegen(
        "Strom", kategorie=klein.strom, objekt=klein.hof, gegen_iban=iban, richtung="ausgang"
    )
    # Gleiche Bedingungen: umfasst
    with pytest.raises(services.KassenbuchFehler, match=rf"Regel #{erste.pk}.*Priorität \(10\)"):
        services.regel_anlegen(
            "Strom 2", kategorie=klein.strom, objekt=klein.halle, gegen_iban=iban
        )
    # Ein vorhandener Umsatz passt auf beide
    eintrag = umsatz(klein.konto, JAN, "40.00", eingang=False, iban=iban, zweck="Abschlag Strom")
    with pytest.raises(services.KassenbuchFehler, match=rf"Umsatz #{eintrag.pk} passt auf beide"):
        services.regel_anlegen(
            "Abschlag", kategorie=klein.strom, objekt=klein.hof, zweck="Abschlag"
        )
    # Andere Priorität, andere IBAN oder andere Richtung: erlaubt
    services.regel_anlegen(
        "Abschlag", kategorie=klein.strom, objekt=klein.hof, zweck="Abschlag", prioritaet=20
    )
    services.regel_anlegen(
        "Andere", kategorie=klein.strom, objekt=klein.hof, gegen_iban=neue_iban()
    )
    services.regel_anlegen(
        "Eingang", kategorie=klein.miete, objekt=klein.hof, gegen_iban=iban, richtung="eingang"
    )
    # Auch eine Änderung der Priorität in den Konflikt hinein wird abgelehnt.
    hoch = services.regel_anlegen(
        "Hoch", kategorie=klein.strom, objekt=klein.hof, zweck="Abschlag Strom", prioritaet=30
    )
    with pytest.raises(services.KassenbuchFehler, match="überschneidet sich"):
        services.regel_aendern(hoch, prioritaet=20)
    assert Zuordnungsregel.objects.count() == 5


def test_regelseite_zeigt_ueberschneidung_als_satz(klein: KleinerHof, angemeldet: Client) -> None:
    """Review 1, Punkt 5: Das Formular der Regel zeigt die Ablehnung als ganzen Satz."""
    iban = neue_iban()
    services.regel_anlegen("Strom", kategorie=klein.strom, objekt=klein.hof, gegen_iban=iban)
    antwort = angemeldet.post(
        reverse("kassenbuch:regel_neu"),
        {
            "bezeichnung": "Doppelt",
            "gegen_iban": iban,
            "kategorie": klein.strom.pk,
            "objekt": klein.hof.pk,
            "prioritaet": 10,
            "aktiv": "on",
        },
    )
    assert antwort.status_code == 200
    assert "überschneidet sich mit Regel" in antwort.content.decode()


def test_testhof_hat_keine_gleichrangigen_ueberschneidungen(testhof: object) -> None:
    """Review 1, Punkt 5: Der Testhof legt keine zwei gleichrangigen Regeln an, die sich
    überschneiden (früher #118/#119: Versicherung nach Zweck und VR-Sammelregel)."""
    for regel in Zuordnungsregel.objects.filter(aktiv=True):
        assert services.ueberschneidungen(regel) == [], regel.pk


# --- Vorschlag (Review 2, B3) ---------------------------------------------------------


def test_vorschlag_aus_zweck_und_konto(klein: KleinerHof, material: Kategorie) -> None:
    """Review 2, B3: Der Vorschlag kommt aus dem Zweck („Material“ in „Material Baumarkt“),
    das Objekt aus dem Verlauf des Kontos. Die Kategorien sind nach Richtung gefiltert."""
    for betrag in ("20.00", "21.00"):  # zwei auf HALLE, eine auf HOF (Fixture)
        alt = umsatz(klein.konto, JAN, betrag, eingang=False, iban=neue_iban(), zweck="Sonst")
        services.umsatz_zuordnen(alt, kategorie=klein.strom, objekt=klein.halle)
    eintrag = _baumarkt(klein, FEB, neue_iban())

    vorschlag = einordnen.vorschlag(eintrag)

    assert (vorschlag.kategorie, vorschlag.objekt) == (material, klein.halle)
    assert "Zweck" in vorschlag.quelle
    ausgaben = set(einordnen.kategorien_fuer("ausgang"))
    assert material in ausgaben and klein.strom in ausgaben
    assert klein.miete not in ausgaben and klein.wohnraum not in ausgaben
    assert klein.miete in set(einordnen.kategorien_fuer("eingang"))
    assert material not in set(einordnen.kategorien_fuer("eingang"))


def test_vorschlag_aus_verlauf_und_regel_merken(klein: KleinerHof, material: Kategorie) -> None:
    """Review 2, B3: Häufigste Kategorie und häufigstes Objekt derselben Gegenpartei; „Als
    Regel merken“ ist angehakt, wenn die Gegenpartei schon vorkam."""
    iban = neue_iban()
    erster = _baumarkt(klein, JAN, iban)
    assert einordnen.vorschlag(erster).regel_merken is False  # erstes Vorkommen
    zweiter = _baumarkt(klein, FEB, iban)
    services.umsatz_zuordnen(erster, kategorie=klein.strom, objekt=klein.halle)

    vorschlag = einordnen.vorschlag(zweiter)

    assert (vorschlag.kategorie, vorschlag.objekt) == (klein.strom, klein.halle)
    assert vorschlag.quelle == "wie der frühere Umsatz dieser Gegenpartei"
    assert vorschlag.regel_merken is True


def test_uebernehmen_mit_regel_merken(klein: KleinerHof, material: Kategorie) -> None:
    """Review 2, B3: „Übernehmen“ mit „Als Regel merken“ bucht und legt eine Regel aus
    Gegen-IBAN und Richtung an; die Buchung trägt die Regel, der Posten ist erledigt."""
    iban = neue_iban()
    eintrag = _baumarkt(klein, JAN, iban)
    posten = _posten(eintrag)

    ergebnis = services.einordnen_uebernehmen(
        [eintrag], kategorie=material, objekt=klein.hof, regel_merken=True
    )

    assert ergebnis.regel is not None and ergebnis.regel_fehler == ""
    assert (ergebnis.regel.gegen_iban, ergebnis.regel.richtung) == (iban, "ausgang")
    (buchung,) = Buchung.objects.filter(umsatz=eintrag)
    assert (buchung.kategorie, buchung.objekt, buchung.regel) == (
        material,
        klein.hof,
        ergebnis.regel,
    )
    posten.refresh_from_db()
    assert posten.status == Arbeitsposten.Status.ERLEDIGT
    # Eine zweite gleiche Regel wird nicht angelegt; gebucht wird trotzdem.
    noch_einer = _baumarkt(klein, FEB, iban)
    zweite = services.einordnen_uebernehmen(
        [noch_einer], kategorie=material, objekt=klein.hof, regel_merken=True
    )
    assert zweite.regel is None and "überschneidet sich" in zweite.regel_fehler
    assert services.ist_gebucht(noch_einer)


# --- Posteingang (Review 2, B2) -------------------------------------------------------


def test_arbeitsliste_zeigt_arten_und_umsatz_mit_vorschlag_in_der_zeile(
    klein: KleinerHof, material: Kategorie, angemeldet: Client
) -> None:
    """Review 2, B2 und B3: Links die Arten mit Zahl, rechts die Posten. Umsatz, Vorschlag
    (vorgewählt) und Knöpfe stehen in der Zeile; Namen kommen aus dem Bezug, nicht aus dem
    Hinweis."""
    eintrag = _baumarkt(klein, JAN, neue_iban())
    posten = _posten(eintrag)

    html = angemeldet.get(reverse("kern:arbeitslisten")).content.decode()

    assert 'class="posteingang-arten"' in html
    assert "Umsätze einordnen" in html and '<span class="zaehler">1</span>' in html
    assert f'id="posten-{posten.pk}"' in html
    assert f'<option value="{material.pk}" selected>Material</option>' in html
    assert f'<option value="{klein.miete.pk}"' not in html  # keine Einnahmen bei einer Ausgabe
    assert "Baumarkt Beispiel KG" in html  # aus dem Umsatz gelesen
    assert "Beispiel" not in posten.hinweis
    assert reverse("kern:posten_uebernehmen", args=[posten.pk]) in html


def test_ein_klick_uebernimmt_und_die_zeile_verschwindet(
    klein: KleinerHof, material: Kategorie, angemeldet: Client
) -> None:
    """Review 2, B3 (Ziel 1 Klick je Umsatz): „Übernehmen“ per HTMX bucht den Vorschlag. Die
    Zeile verschwindet, die Meldung sagt, was gebucht ist, mit Link (B-Leitlinien)."""
    eintrag = _baumarkt(klein, JAN, neue_iban())
    posten = _posten(eintrag)

    antwort = angemeldet.post(reverse("kern:posten_uebernehmen", args=[posten.pk]), **HTMX)

    html = antwort.content.decode()
    assert antwort.status_code == 200
    assert f'id="posten-{posten.pk}"' not in html
    assert f"Umsatz #{eintrag.pk} gebucht: Material · HOF." in html
    assert reverse("kassenbuch:umsatz", args=[eintrag.pk]) in html
    (buchung,) = Buchung.objects.filter(umsatz=eintrag)
    assert (buchung.kategorie, buchung.objekt) == (material, klein.hof)


def test_werte_der_zeile_gehen_vor(
    klein: KleinerHof, material: Kategorie, angemeldet: Client
) -> None:
    """Review 2, B2: Ändert Max Kategorie oder Objekt in der Zeile, gilt das; ohne Haken
    entsteht keine Regel."""
    eintrag = _baumarkt(klein, JAN, neue_iban())
    posten = _posten(eintrag)
    p = f"p{posten.pk}"

    angemeldet.post(
        reverse("kern:posten_uebernehmen", args=[posten.pk]),
        {f"{p}-da": "1", f"{p}-kategorie": klein.strom.pk, f"{p}-objekt": klein.halle.pk},
        **HTMX,
    )

    (buchung,) = Buchung.objects.filter(umsatz=eintrag)
    assert (buchung.kategorie, buchung.objekt) == (klein.strom, klein.halle)
    assert not Zuordnungsregel.objects.exists()


def test_ohne_vorschlag_kommt_ein_satz_am_posten(klein: KleinerHof, angemeldet: Client) -> None:
    """Review 2, B-Leitlinien: Fehler als deutsche Sätze. Ohne Kategorie kein Buchen."""
    eintrag = umsatz(klein.konto, JAN, "12.00", eingang=False, iban=neue_iban(), zweck="xyz")
    posten = _posten(eintrag)
    p = f"p{posten.pk}"

    antwort = angemeldet.post(
        reverse("kern:posten_uebernehmen", args=[posten.pk]),
        {f"{p}-da": "1", f"{p}-kategorie": "", f"{p}-objekt": klein.hof.pk},
        **HTMX,
    )

    assert "Bitte Kategorie und Objekt wählen" in antwort.content.decode()
    assert not services.ist_gebucht(eintrag)


def test_mehrere_umsaetze_zugleich_uebernehmen(
    klein: KleinerHof, material: Kategorie, angemeldet: Client
) -> None:
    """Review 2, B2 und B3: Mehrfachauswahl „für alle übernehmen“ bucht jeden gewählten
    Umsatz nach seinem Vorschlag; die Liste kommt neu mit einer Meldung."""
    a = _baumarkt(klein, JAN, neue_iban())
    b = _baumarkt(klein, FEB, neue_iban(), "12.00")
    services.umsaetze_einordnen()
    ids = list(
        Arbeitsposten.objects.filter(art=services.ART_EINORDNEN).values_list("pk", flat=True)
    )

    antwort = angemeldet.post(
        reverse("kern:posten_sammel"), {"aktion": "uebernehmen", "posten": ids}, **HTMX
    )

    assert "2 von 2 Posten übernommen." in antwort.content.decode()
    assert services.ist_gebucht(a) and services.ist_gebucht(b)


def test_gleiche_gegenpartei_ohne_regel_als_gruppe(
    klein: KleinerHof, material: Kategorie, angemeldet: Client
) -> None:
    """Review 1, Punkt 5: Posten mit derselben Ursache (gleiche Gegenpartei ohne Regel)
    erscheinen als eine Gruppe; „für alle anwenden“ bucht alle und merkt eine Regel."""
    iban = neue_iban()
    umsaetze = [_baumarkt(klein, datetime.date(2026, m, 5), iban) for m in (1, 2, 3)]
    services.umsaetze_einordnen()

    html = angemeldet.get(reverse("kern:arbeitslisten")).content.decode()
    assert "3 Umsätze von Baumarkt Beispiel KG ohne Regel" in html
    assert "Für alle 3 anwenden" in html

    ids = [p.pk for p in Arbeitsposten.objects.filter(art=services.ART_EINORDNEN)]
    antwort = angemeldet.post(
        reverse("kern:posten_gruppe"),
        {
            "gruppe": "g1",
            "g1-da": "1",
            "g1-posten": ids,
            "g1-kategorie": material.pk,
            "g1-objekt": klein.hof.pk,
            "g1-regel": "1",
        },
        **HTMX,
    )

    assert "3 Umsätze gebucht: Material · HOF." in antwort.content.decode()
    (regel,) = Zuordnungsregel.objects.all()
    assert all(services.ist_gebucht(u) for u in umsaetze)
    assert set(Buchung.objects.filter(umsatz__in=umsaetze).values_list("regel", flat=True)) == {
        regel.pk
    }
    assert not Arbeitsposten.objects.filter(status="offen").exists()


def test_gleichrangige_regeln_als_gruppe(
    klein: KleinerHof, material: Kategorie, angemeldet: Client
) -> None:
    """Review 1, Punkt 5: Umsätze, auf die zwei gleichrangige Regeln passen, stehen als eine
    Gruppe „Regeln #… und #… passen mit gleicher Priorität“ mit Link zu den Regeln."""
    a = services.regel_anlegen("A", kategorie=material, objekt=klein.hof, zweck="Material")
    b = services.regel_anlegen("B", kategorie=klein.strom, objekt=klein.hof, zweck="Baumarkt")
    for m in (1, 2):
        _baumarkt(klein, datetime.date(2026, m, 5), neue_iban())
    services.umsaetze_einordnen()

    html = angemeldet.get(reverse("kern:arbeitslisten")).content.decode()

    assert f"2 Umsätze: Regeln #{a.pk} und #{b.pk} passen mit gleicher Priorität" in html
    assert reverse("kassenbuch:regeln") in html


def test_zuordnen_als_dialog_nimmt_den_posten_aus_der_liste(
    klein: KleinerHof, material: Kategorie, angemeldet: Client
) -> None:
    """Review 2, B2 (Taste e): Die Bearbeitung öffnet als Dialog, vorausgefüllt; nach dem
    Buchen verschwindet der Posten per OOB aus der Liste, und der Dialog schließt."""
    eintrag = _baumarkt(klein, JAN, neue_iban())
    posten = _posten(eintrag)
    url = reverse("kassenbuch:zuordnen", args=[eintrag.pk])

    dialog = angemeldet.get(url, **HTMX).content.decode()
    assert 'class="dialog-formular"' in dialog and "Als Regel merken" in dialog
    assert f'<option value="{material.pk}" selected>' in dialog

    antwort = angemeldet.post(
        url,
        {"kategorie": material.pk, "objekt": klein.hof.pk, "leistungsjahr": 2026},
        **HTMX,
    )
    assert f'<li id="posten-{posten.pk}" hx-swap-oob="delete">' in antwort.content.decode()
    assert "dialogSchliessen" in antwort["HX-Trigger"]
    assert Buchung.objects.get(umsatz=eintrag).betrag == Decimal("48.90")
