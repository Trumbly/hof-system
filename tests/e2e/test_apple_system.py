"""Apple-Design, Bereich System (PR F): Zähler, Fristen, Läufe, Protokoll und Vorgang,
Anbindungen, Datenstand, Suche, Konto mit den Seiten von allauth, Anmeldung und Fehlerseite.

Je Seite bei 390 und 1440 px, hell und dunkel: die Prüfung des Smoke-Tests (Überlauf,
englische Texte, Überlappung in Formularen) und Klickflächen mindestens 44 px
(``test_design._kleine_ziele``). Bei 320 px und mit Verdana bei 360 und 390 px nur die
Prüfung des Smoke-Tests (Überlauf).

Detailseiten (Zähler, Vorgang, Anfrage, Bilder) findet der Test über den ersten passenden
Link ihrer Liste, damit ``tests/e2e/adressen.py`` unverändert bleibt. Anmeldung und zweiter
Faktor laufen ohne Sitzung; der zweite Faktor nur bis zur Eingabe des Codes (kein Code wird
verbraucht).
"""

from __future__ import annotations

import re
from typing import Any

import pytest
from playwright.sync_api import Browser, Page

from testhof import kern as testhof_kern
from tests.e2e.conftest import DESKTOP, HANDY
from tests.e2e.server import ARTEFAKTE, Server
from tests.e2e.test_design import _kleine_ziele
from tests.e2e.test_smoke import ZIEL_PX, pruefen

pytestmark = pytest.mark.e2e

BREITEN = {"1440": DESKTOP, "390": HANDY}
SCHEMAS = ("light", "dark")
ORDNER = ARTEFAKTE / "apple-system"

#: Schlüssel → Adresse. ``(liste, muster)``: erster Link der Liste, auf den der reguläre
#: Ausdruck passt.
SEITEN: dict[str, str | tuple[str, str]] = {
    "zaehler": "/zaehler/",
    "zaehler_detail": ("/zaehler/", r"^/zaehler/\d+/$"),
    "zaehler_ablesung": "",  # aus adressen["seiten"]
    "zaehler_besuch": "/zaehler/besuch/",
    "fristen": "/fristen/",
    "laeufe": "/laeufe/",
    "protokoll": "/protokoll/",
    "vorgang": ("/protokoll/", r"^/protokoll/vorgang/[0-9a-f-]+/$"),
    "anbindungen": "/anbindungen/",
    "anfragen": "/anbindungen/anfragen/?status=alle",
    "bilder": ("/anbindungen/vermarktung/?alle=1", r"^/anbindungen/einheiten/\d+/bilder/$"),
    "katalog": "/anbindungen/vermarktung/",
    "kennzahlen": "/anbindungen/kennzahlen/",
    "abweichungen": "/anbindungen/abweichungen/",
    "datenstand": "/stammdaten/datenstand/",
    "suche": "/suche/?q=NW-G",
    "konto": "/konto/",
    "passwort": "/konto/password/change/",
    "mfa": "/konto/2fa/",
    "abmelden": "/konto/logout/",
}
#: Ohne Sitzung: Anmeldung, zweiter Faktor (nach dem Passwort), Fehlerseite 404.
OHNE_SITZUNG = ("anmeldung", "zweiter_faktor", "fehler_404")


def _adresse(seite: Page, adressen: dict[str, Any], schluessel: str) -> str:
    if schluessel == "zaehler_ablesung":
        return str(adressen["seiten"]["zaehler_ablesung"])
    ziel = SEITEN[schluessel]
    if isinstance(ziel, str):
        return ziel
    liste, muster = ziel
    seite.goto(liste)
    hrefs = seite.eval_on_selector_all(
        ".inhalt-neu a[href]", "as => as.map(a => a.getAttribute('href'))"
    )
    treffer = [h for h in hrefs if re.match(muster, h)]
    assert treffer, f"Kein Link „{muster}“ auf {liste}"
    return str(treffer[0])


def _ohne_sitzung(seite: Page, adressen: dict[str, Any], schluessel: str) -> int:
    """Öffnet eine Seite ohne Sitzung und gibt den Status zurück."""
    if schluessel == "fehler_404":
        antwort = seite.goto("/gibt-es-nicht/", wait_until="networkidle")
    else:
        antwort = seite.goto(adressen["login"], wait_until="networkidle")
        if schluessel == "zweiter_faktor":
            seite.fill("input[name=login]", testhof_kern.EMAIL)
            seite.fill("input[name=password]", testhof_kern.PASSWORT)
            seite.click("button[type=submit]")
            seite.wait_for_selector("input[name=code]")
            return 200
    assert antwort is not None
    return antwort.status


def _pruefen(seite: Page, ziele: bool) -> list[str]:
    befunde = pruefen(seite, handy=True)
    if ziele and (klein := _kleine_ziele(seite)):
        befunde.append(f"Klickflächen unter {ZIEL_PX} px: {', '.join(klein[:6])}")
    return befunde


def _kontext(browser: Browser, server: Server, sitzung: Any, groesse: dict[str, int], schema: str):  # type: ignore[no-untyped-def]
    return browser.new_context(
        base_url=server.basis_url,
        storage_state=str(sitzung) if sitzung else None,
        viewport=groesse,  # type: ignore[arg-type]
        color_scheme=schema,  # type: ignore[arg-type]
        locale="en-US",
        timezone_id="Europe/Berlin",
    )


@pytest.mark.parametrize("schema", SCHEMAS)
@pytest.mark.parametrize("breite", list(BREITEN))
@pytest.mark.parametrize("schluessel", list(SEITEN) + list(OHNE_SITZUNG))
def test_apple_system_seite(
    browser: Browser,
    anmeldung: Any,
    server: Server,
    adressen: dict[str, Any],
    schluessel: str,
    breite: str,
    schema: str,
) -> None:
    """Apple-Design, Bereich System: Seite bei 390 und 1440 px, hell und dunkel, ohne Befund
    des Smoke-Tests und mit Klickflächen von mindestens 44 px."""
    ohne = schluessel in OHNE_SITZUNG
    kontext = _kontext(browser, server, None if ohne else anmeldung, BREITEN[breite], schema)
    seite = kontext.new_page()
    seite.set_default_timeout(15_000)
    try:
        if ohne:
            status = _ohne_sitzung(seite, adressen, schluessel)
            assert status == (404 if schluessel == "fehler_404" else 200)
        else:
            antwort = seite.goto(_adresse(seite, adressen, schluessel), wait_until="networkidle")
            assert antwort is not None and antwort.status == 200, schluessel
        ziel = ORDNER / breite / f"{schluessel}-{schema}.png"
        ziel.parent.mkdir(parents=True, exist_ok=True)
        seite.screenshot(path=ziel, full_page=True)
        befunde = _pruefen(seite, ziele=schluessel != "fehler_404")
        assert not befunde, f"{schluessel} bei {breite} px ({schema}):\n- " + "\n- ".join(befunde)
    finally:
        kontext.close()


#: Schmale Breiten: 320 px mit der Systemschrift, 360 und 390 px mit Verdana als Ersatz für
#: die breiteren Schriften der Pipeline unter Linux (wie ``test_seiten_r2_8``).
VERDANA = "* { font-family: Verdana, sans-serif !important; }"
SCHMALE = {"320": (320, ""), "360-breit": (360, VERDANA), "390-breit": (390, VERDANA)}


@pytest.mark.parametrize("schmal", list(SCHMALE))
@pytest.mark.parametrize("schluessel", list(SEITEN) + list(OHNE_SITZUNG))
def test_apple_system_schmal(
    browser: Browser,
    anmeldung: Any,
    server: Server,
    adressen: dict[str, Any],
    schluessel: str,
    schmal: str,
) -> None:
    """Apple-Design, Bereich System: bei 320 px und mit breiter Schrift bei 360 und 390 px
    kein Überlauf, keine Überlappung."""
    breite, stil = SCHMALE[schmal]
    ohne = schluessel in OHNE_SITZUNG
    groesse = {"width": breite, "height": 700}
    kontext = _kontext(browser, server, None if ohne else anmeldung, groesse, "light")
    seite = kontext.new_page()
    seite.set_default_timeout(15_000)
    try:
        if ohne:
            _ohne_sitzung(seite, adressen, schluessel)
        else:
            seite.goto(_adresse(seite, adressen, schluessel), wait_until="networkidle")
        if stil:
            seite.add_style_tag(content=stil)
        befunde = pruefen(seite, handy=True)
        assert not befunde, f"{schluessel} bei {schmal} px:\n- " + "\n- ".join(befunde)
    finally:
        kontext.close()


@pytest.mark.parametrize("schrift", ("system", "verdana"))
def test_fristen_zeile_mit_erledigt_bleibt_flach(
    seite: Page, adressen: dict[str, Any], schrift: str
) -> None:
    """Befund System (PR A): Fristen-Zeilen mit „Erledigt“ werden nicht höher als nötig.

    Der Knopf bleibt 44 px. Eine Zeile ist nie höher als das Größere von Knopf und höchstem
    Text ihrer Zellen, jeweils mit Innenabstand und Rahmen der Zelle (3 px Toleranz):
    Zweizeiliger Inhalt (Art mit Hinweis, Datum mit Badge) darf die Zeile höher machen, der
    Knopf nicht. Zeilen mit nur einzeiligem Text sind höchstens 52 px hoch. Die Aktionen
    passen in ihre Zelle, und die Spalten stehen in allen Gruppen gleich. Mit Verdana als
    Ersatz für die breiteren Schriften unter Linux (wie ``test_seiten_r2_8``)."""
    seite.goto(adressen["seiten"]["fristen"], wait_until="networkidle")
    if schrift == "verdana":
        seite.add_style_tag(content=VERDANA)
    mass = seite.evaluate(
        """() => {
        const zeilen = [...document.querySelectorAll('#fristen tr.frist')]
            .filter(z => z.getBoundingClientRect().height > 0 && z.querySelector('.knopf'));
        const text = (td) => {
            const r = document.createRange();
            r.selectNodeContents(td);
            return r.getBoundingClientRect().height;
        };
        // Innenabstand und Rahmen einer Zelle, wie der Browser sie rechnet
        const rand = (td) => {
            const s = getComputedStyle(td);
            return parseFloat(s.paddingTop) + parseFloat(s.paddingBottom)
                + parseFloat(s.borderTopWidth) + parseFloat(s.borderBottomWidth);
        };
        const zeile = (z) => {
            const zellen = [...z.children].filter(td => !td.classList.contains('frist-aktionen'));
            const aktionen = z.querySelector('.frist-aktionen');
            const knopf = aktionen.querySelector('.knopf').getBoundingClientRect().height;
            const knoepfe = aktionen.querySelector('.zeilen-aktionen').getBoundingClientRect();
            return {
                hoehe: z.getBoundingClientRect().height,
                text: Math.max(...zellen.map(td => text(td) + rand(td))),
                knopfzeile: knopf + rand(aktionen),
                zeilenhoehe: parseFloat(getComputedStyle(zellen[0]).lineHeight),
                hochster_text: Math.max(...zellen.map(text)),
                knopf,
                passt: knoepfe.right <= aktionen.getBoundingClientRect().right + 0.5,
            };
        };
        const art = [...document.querySelectorAll('#fristen > .karte .tab > thead th:nth-child(2)')]
            .map(th => Math.round(th.getBoundingClientRect().left));
        return {zeilen: zeilen.map(zeile), art: [...new Set(art)]};
    }"""
    )
    zeilen = mass["zeilen"]
    assert zeilen, mass
    for z in zeilen:
        assert z["knopf"] >= ZIEL_PX - 0.5, z
        # Toleranz 3 px: Die Textbox einer Zeile ist etwas niedriger als ihre Zeilenhöhe
        # (Durchschuss), und Browser runden Zeilenhöhen unterschiedlich.
        assert z["hoehe"] <= max(z["knopfzeile"], z["text"]) + 3, z
        assert z["passt"], z
    einzeilig = [z for z in zeilen if z["hochster_text"] <= 1.5 * z["zeilenhoehe"]]
    assert einzeilig and max(z["hoehe"] for z in einzeilig) <= 52, zeilen
    assert len(mass["art"]) == 1, mass


def test_laeufe_lange_meldung_gekuerzt(seite: Page, adressen: dict[str, Any]) -> None:
    """Befund System (PR A): Lange Meldungen der Läufe stehen auf drei Zeilen gekürzt;
    „ganze Meldung“ zeigt sie ganz."""
    seite.goto(adressen["seiten"]["laeufe"], wait_until="networkidle")
    box = seite.locator(".gekuerzt").first
    text = box.locator(".gekuerzt-text")
    kurz = text.bounding_box()
    zeile = float(text.evaluate("e => parseFloat(getComputedStyle(e).lineHeight)"))
    assert kurz is not None and kurz["height"] <= 3 * zeile + 1
    box.locator("summary").click()
    lang = text.bounding_box()
    assert lang is not None and lang["height"] > kurz["height"]


def test_protokoll_filter_schneidet_nicht_ab(seite: Page, adressen: dict[str, Any]) -> None:
    """Befund System (PR A): Bei 1440 px stehen alle Filter des Protokolls in einer Zeile, und
    der Platzhalter des Suchfelds passt ganz hinein."""
    seite.goto(adressen["seiten"]["protokoll"], wait_until="networkidle")
    mass = seite.evaluate(
        """() => {
        const form = document.querySelector('#protokoll-filter');
        const oben = [...form.children].map(e => Math.round(e.getBoundingClientRect().bottom));
        const feld = form.querySelector('input[type=search], input[name=suche]');
        const probe = document.createElement('span');
        probe.style.font = getComputedStyle(feld).font;
        probe.textContent = feld.placeholder;
        document.body.append(probe);
        const text = probe.getBoundingClientRect().width;
        probe.remove();
        const s = getComputedStyle(feld);
        const platz = feld.clientWidth - parseFloat(s.paddingLeft) - parseFloat(s.paddingRight);
        return {zeilen: new Set(oben).size, text, platz};
    }"""
    )
    assert mass["zeilen"] == 1, mass
    assert mass["text"] <= mass["platz"], mass
