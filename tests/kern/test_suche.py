"""Suche in der Kopfzeile (Review 2, B1): Service ``kern.suche``, Quellen der Apps, Seite.

Die Tastatur (Strg+K, Pfeile, Enter) prüfen die Oberflächentests in tests/e2e/test_suche.py.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.db import connection
from django.test import Client
from django.urls import reverse

from apps.bank.models import Bankumsatz
from apps.dokumente.models import Beleg
from apps.kern import suche
from apps.kern.suche import Anfrage, Treffer
from apps.stammdaten.models import Einheit, Partei, Vertrag
from testhof.aufbau import Testhof

D = Decimal


def _titel(gruppen: list[suche.Gruppe]) -> dict[str, list[str]]:
    return {g.schluessel: [t.titel for t in g.treffer] for g in gruppen}


# --- Beträge -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "bereiche"),
    [
        ("627,34", [(D("627.34"), D("627.35"))]),
        ("627.34", [(D("627.34"), D("627.35"))]),
        ("1.250,00", [(D("1250.00"), D("1250.01"))]),
        ("1,250.00", [(D("1250.00"), D("1250.01"))]),
        ("1.250", [(D("1250"), D("1251"))]),
        ("627", [(D("627"), D("628"))]),
        ("-527,34 €", [(D("527.34"), D("527.35"))]),
        ("12,5", [(D("12.5"), D("12.51"))]),
        ("V-0005", []),
        ("Bohnbach", []),
        ("", []),
    ],
)
def test_betraege_mit_komma_oder_punkt(text: str, bereiche: list[tuple[D, D]]) -> None:
    """Review 2, B1: Ein Betrag findet sich mit Komma oder Punkt, mit Tausenderpunkt und €."""
    assert list(suche.betraege(text)) == bereiche


# --- Registry ------------------------------------------------------------------------


def test_registry_jede_app_meldet_ihre_quelle() -> None:
    """Review 2, B1: Die Apps melden ihre Suchquellen in ready() an, in fester Reihenfolge."""
    assert [q.schluessel for q in suche.quellen()][:5] == [
        "einheiten",
        "vertraege",
        "parteien",
        "umsaetze",
        "belege",
    ]


def test_registry_neue_quelle_und_hoechstens_fuenf_je_gruppe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Review 2, B1: Eine weitere App kann eine Quelle ergänzen; je Gruppe höchstens fünf
    Treffer, „mehr“ zeigt, dass es weitere gibt."""
    monkeypatch.setattr(suche, "_QUELLEN", {})
    gesehen: list[tuple[str, int]] = []

    def quelle(a: Anfrage, grenze: int) -> list[Treffer]:
        gesehen.append((a.text, grenze))
        return [Treffer(f"T{i}", f"/t/{i}/") for i in range(grenze)]

    suche.quelle_anmelden("test", "Tests", quelle)
    gruppen = suche.suchen("  Gar  age ")
    assert gesehen == [("Gar age", 6)]
    assert len(gruppen) == 1
    assert gruppen[0].titel == "Tests"
    assert len(gruppen[0].treffer) == 5
    assert gruppen[0].mehr is True


def test_zu_kurze_eingabe_sucht_nicht(monkeypatch: pytest.MonkeyPatch) -> None:
    """Review 2, B1: Ein Zeichen sucht nicht (zu viele Treffer)."""
    monkeypatch.setattr(suche, "_QUELLEN", {})
    suche.quelle_anmelden("test", "Tests", lambda a, g: [Treffer("x", "/x/")])
    assert suche.suchen("G") == []
    assert suche.suchen("Ga") != []


# --- Treffer je Art (Testhof) --------------------------------------------------------


def test_findet_nw_id(testhof: Testhof) -> None:
    """Review 2, B1: NW-ID findet die Einheit (und ihren Vertrag), die Einheit zuerst."""
    gruppen = suche.suchen("NW-G-0018")
    titel = _titel(gruppen)
    assert titel["einheiten"] == ["NW-G-0018"]
    assert "V-0005" in titel["vertraege"]
    assert gruppen[0].schluessel == "einheiten"
    einheit = Einheit.objects.get(nw_id="NW-G-0018")
    assert gruppen[0].treffer[0].url == reverse("stammdaten:einheit", args=[einheit.pk])


def test_findet_v_nummer(testhof: Testhof) -> None:
    """Review 2, B1: V-Nummer findet den Vertrag, mit Einheit und Mieter darunter."""
    gruppen = suche.suchen("v-0005")
    treffer = gruppen[0].treffer[0]
    vertrag = Vertrag.objects.get(nummer="V-0005")
    assert (gruppen[0].schluessel, treffer.titel) == ("vertraege", "V-0005")
    assert treffer.url == reverse("stammdaten:vertrag", args=[vertrag.pk])
    assert "NW-G-0018" in treffer.unter
    assert treffer.status == "aktiv"


def test_findet_name_ort_und_email(testhof: Testhof) -> None:
    """Review 2, B1: Name, Ort und E-Mail finden die Partei; der Name auch ihre Verträge."""
    vertrag = Vertrag.objects.get(nummer="V-0005")
    partei = vertrag.vertragsparteien.select_related("partei").first().partei  # type: ignore[union-attr]
    email = partei.kontaktwege.filter(art="email").first()
    assert partei.nachname and partei.ort and email

    nach_name = _titel(suche.suchen(partei.nachname))
    assert partei.anzeigename in nach_name["parteien"]
    assert "V-0005" in nach_name["vertraege"]
    # Vor- und Nachname zusammen, Groß- und Kleinschreibung egal
    assert partei.anzeigename in _titel(suche.suchen(partei.anzeigename.upper()))["parteien"]
    nach_ort = suche.suchen(f"{partei.nachname} {partei.ort}")
    assert partei.anzeigename in _titel(nach_ort)["parteien"]
    nach_email = suche.suchen(email.wert)
    assert _titel(nach_email)["parteien"] == [partei.anzeigename]
    assert nach_email[0].treffer[0].url == reverse("stammdaten:partei", args=[partei.pk])


def test_findet_umsatz_nach_betrag_mit_komma_und_punkt(testhof: Testhof) -> None:
    """Review 2, B1: Ein Betrag findet den Umsatz, ob mit Komma oder Punkt geschrieben."""
    umsatz = Bankumsatz.objects.filter(betrag__gt=100).order_by("pk").first()
    assert umsatz is not None
    for text in (f"{umsatz.betrag:.2f}", f"{umsatz.betrag:.2f}".replace(".", ",")):
        gruppen = {g.schluessel: g for g in suche.suchen(text, je_gruppe=100)}
        urls = [t.url for t in gruppen["umsaetze"].treffer]
        assert reverse("kassenbuch:umsatz", args=[umsatz.pk]) in urls, text


def test_findet_umsatz_nach_gegenpartei_und_zweck(testhof: Testhof) -> None:
    """Review 2, B1: Gegenpartei und Verwendungszweck finden Umsätze."""
    titel = _titel(suche.suchen("Material Baumarkt"))
    assert titel["umsaetze"]
    assert all(t.startswith("−") for t in titel["umsaetze"])  # Ausgaben mit Minus


def test_findet_beleg_nach_rechnungsnummer_und_betrag(testhof: Testhof) -> None:
    """Review 2, B1: Rechnungsnummer (Beleg und Eingangsrechnung) und Betrag finden den Beleg."""
    beleg = Beleg.objects.exclude(rechnungsnummer="").exclude(betrag=None).first()
    assert beleg is not None and beleg.betrag is not None
    url = reverse("dokumente:beleg", args=[beleg.pk])
    for text in (
        beleg.rechnungsnummer,
        beleg.rechnungsnummer[-4:],
        f"{beleg.betrag:.2f}".replace(".", ","),
        f"{beleg.betrag:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."),
    ):
        gruppen = {g.schluessel: g for g in suche.suchen(text)}
        assert url in [t.url for t in gruppen["belege"].treffer], text


def test_findet_eingangsrechnung_nach_ihrer_nummer(testhof: Testhof) -> None:
    """Review 2, B1: Die Rechnungsnummer der Eingangsrechnung findet ihren Beleg, auch wenn
    sie am Beleg selbst fehlt."""
    beleg = Beleg.objects.filter(eingangsrechnung__isnull=False).first()
    assert beleg is not None
    Beleg.objects.filter(pk=beleg.pk).update(rechnungsnummer="")
    nummer = beleg.eingangsrechnung.rechnungsnummer
    gruppen = {g.schluessel: g for g in suche.suchen(nummer)}
    assert reverse("dokumente:beleg", args=[beleg.pk]) in [t.url for t in gruppen["belege"].treffer]


def test_kein_treffer(testhof: Testhof) -> None:
    """Review 2, B1: Ohne Treffer gibt es keine Gruppe; die Seite sagt „Nichts gefunden“."""
    assert suche.suchen("xqzxqz-nirgends") == []
    assert suche.suchen("987654,32") == []


def test_parteien_ohne_namen_keine_betraege(testhof: Testhof) -> None:
    """Review 2, B1: Ein Betrag sucht nicht in Namen und Adressen (PLZ wären Zufallstreffer)."""
    partei = Partei.objects.exclude(plz="").first()
    assert partei is not None
    assert "parteien" not in _titel(suche.suchen(partei.plz))


# --- Seite und Teil ------------------------------------------------------------------


def test_suche_htmx_liefert_gruppen(angemeldet: Client, testhof: Testhof) -> None:
    """Review 2, B1: Per HTMX kommt die Trefferliste, gruppiert, mit Links als Optionen."""
    antwort = angemeldet.get(reverse("kern:suche"), {"q": "NW-G-0018"}, HTTP_HX_REQUEST="true")
    html = antwort.content.decode()
    assert antwort.status_code == 200
    assert "<html" not in html
    assert 'class="suche-gruppe-titel" aria-hidden="true">Einheiten<' in html
    assert 'class="suche-gruppe-titel" aria-hidden="true">Verträge<' in html
    assert html.count('role="option"') >= 2


def test_suche_htmx_kein_treffer(angemeldet: Client, testhof: Testhof) -> None:
    """Review 2, B1: Ohne Treffer sagt die Liste „Nichts gefunden“."""
    antwort = angemeldet.get(reverse("kern:suche"), {"q": "xqzxqz"}, HTTP_HX_REQUEST="true")
    assert "Nichts gefunden für „xqzxqz“" in antwort.content.decode()


def test_suchseite_ohne_javascript(angemeldet: Client, testhof: Testhof) -> None:
    """Review 2, B1: Ohne JavaScript führt Enter zur Seite „Suche“ mit allen Gruppen."""
    antwort = angemeldet.get(reverse("kern:suche"), {"q": "V-0005"})
    html = antwort.content.decode()
    assert antwort.status_code == 200
    assert 'class="seitenkopf"' in html
    assert 'id="vertraege"' in html
    assert 'value="V-0005"' in html  # das Suchfeld in der Kopfzeile behält den Text


def test_suche_nur_angemeldet(client: Client, db: None) -> None:
    """Review 2, B1: Die Suche zeigt Namen; ohne Anmeldung geht es zur Anmeldung."""
    antwort = client.get(reverse("kern:suche"), {"q": "V-0005"})
    assert antwort.status_code == 302


def test_suchfeld_auf_jeder_seite(angemeldet: Client) -> None:
    """Review 2, B1: Das Suchfeld steht in der Kopfzeile (Handy) und in der Seitenleiste."""
    html = angemeldet.get(reverse("kern:startseite")).content.decode()
    assert 'id="suche-leiste"' in html and 'id="suche-kopf"' in html
    assert 'role="combobox"' in html
    assert f'hx-get="{reverse("kern:suche")}"' in html


def test_trigramm_indexe_fuer_die_suche(db: None) -> None:
    """Review 2, B1: Die Suche nutzt Trigramm-Indexe auf UPPER(feld) (wie icontains)."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT indexname, indexdef FROM pg_indexes WHERE indexname LIKE 'suche_%'")
        indexe = dict(cursor.fetchall())
    assert "suche_bank_bankumsatz_verwendungszweck" in indexe
    assert "suche_stammdaten_partei_nachname" in indexe
    assert "gin_trgm_ops" in indexe["suche_dokumente_beleg_rechnungsnummer"]
    assert "upper" in indexe["suche_stammdaten_partei_nachname"].lower()
