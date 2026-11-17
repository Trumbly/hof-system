"""Die 16 Sonderfälle des Testhofs (Bauplan, Abschnitt „Testdaten“).

Jeder Fall bekommt ein Erzeugungsmodul, sobald sein Bauschritt dran ist. Das Modul
nennt die Schlüssel der Fälle, die es erzeugt, bei der Anmeldung
(``register.registrieren(..., sonderfaelle=("doppelter_umsatz",))``).
``ohne_modul()`` listet die Fälle, für die das noch fehlt.
"""

from __future__ import annotations

from dataclasses import dataclass

from testhof import register


@dataclass(frozen=True)
class Sonderfall:
    schluessel: str
    name: str
    beschreibung: str
    #: Bauschritte, die den Fall prüfen.
    schritte: tuple[int, ...]


SONDERFAELLE: tuple[Sonderfall, ...] = (
    Sonderfall(
        "doppelter_umsatz",
        "Doppelter Umsatz nach erneutem Abruf",
        "Ein erneuter Abruf liefert einen Umsatz, der schon gespeichert ist. "
        "Er darf nur einmal im System stehen.",
        (1,),
    ),
    Sonderfall(
        "rueckbuchung",
        "Rückbuchung einer Zahlung",
        "Eine eingegangene Zahlung wird von der Bank zurückgebucht. Kassenbuch und "
        "Abgleich müssen beide Umsätze zusammen sehen.",
        (2, 6),
    ),
    Sonderfall(
        "sammelrechnung_zwei_objekte",
        "Sammelrechnung für zwei Objekte",
        "Eine Eingangsrechnung betrifft zwei Objekte und wird auf beide aufgeteilt.",
        (2, 3),
    ),
    Sonderfall(
        "wohnraummiete_selbes_konto",
        "Wohnraummiete auf demselben Konto: Kassenbuch ja, Abgleich nein",
        "Miete für Wohnraum (geführt in Verwaltio) geht auf dasselbe Konto ein. Sie gehört "
        "ins Kassenbuch, aber nicht in den Abgleich gegen Sollposten.",
        (2, 6),
    ),
    Sonderfall(
        "vertrag_monatsmitte",
        "Vertragsbeginn und Vertragsende mitten im Monat",
        "Ein Vertrag beginnt und einer endet nicht am Monatsersten. Die Sollstellung "
        "rechnet den Teilmonat anteilig.",
        (5,),
    ),
    Sonderfall(
        "staffelmiete",
        "Staffelmiete mit einer Stufe im Testzeitraum",
        "Eine Staffelmiete erhöht sich innerhalb der 24 Monate um eine Stufe.",
        (5,),
    ),
    Sonderfall(
        "jahreszahler",
        "Jahreszahler: zwölf Monate in einer Zahlung",
        "Ein Mieter zahlt die Miete für ein Jahr in einer Überweisung.",
        (5, 6),
    ),
    Sonderfall(
        "sammelzahler",
        "Sammelzahler: eine Überweisung für drei Verträge",
        "Eine Überweisung deckt die Sollposten von drei Verträgen desselben Zahlers.",
        (6,),
    ),
    Sonderfall(
        "fremder_zahler",
        "Fremder Zahler: Partner oder Firma überweist",
        "Nicht die Vertragspartei, sondern Partner oder Firma überweist die Miete.",
        (6,),
    ),
    Sonderfall(
        "falsche_einheit_im_verwendungszweck",
        "Verwendungszweck nennt die falsche Einheit",
        "Der Verwendungszweck nennt eine andere Einheit als die, zu der die Zahlung gehört.",
        (6,),
    ),
    Sonderfall(
        "ueberzahlung_guthaben",
        "Überzahlung mit Guthaben für den Folgemonat",
        "Eine Zahlung übersteigt das Soll; der Rest wird Guthaben für den Folgemonat.",
        (6,),
    ),
    Sonderfall(
        "barzahlung",
        "Barzahlung ohne Bankumsatz",
        "Eine Miete wird bar gezahlt; es gibt keinen Bankumsatz, nur einen Eintrag im Kassenbuch.",
        (6,),
    ),
    Sonderfall(
        "teilzahlung_nachzahlung",
        "Teilzahlung mit späterer Nachzahlung",
        "Ein Sollposten wird erst teilweise und später vollständig bezahlt.",
        (6, 7),
    ),
    Sonderfall(
        "dauerauftrag_alte_miete",
        "Dauerauftrag mit alter Miete nach einer Erhöhung",
        "Nach einer Mieterhöhung läuft der Dauerauftrag mit dem alten Betrag weiter.",
        (6, 7),
    ),
    Sonderfall(
        "rechtsfall_mahnsperre",
        "Rechtsfall mit Mahnsperre",
        "Ein Vertrag ist ein Rechtsfall; für ihn gehen keine Mahnungen hinaus.",
        (7,),
    ),
    Sonderfall(
        "kuendigung_rueckgabe_kaution",
        "Kündigung, Rückgabe und Kautionsabrechnung",
        "Ein Vertrag wird gekündigt, die Einheit zurückgegeben und die Kaution abgerechnet.",
        (4,),
    ),
)


def nach_schluessel() -> dict[str, Sonderfall]:
    return {fall.schluessel: fall for fall in SONDERFAELLE}


def erzeugende_module() -> dict[str, list[str]]:
    """Je Sonderfall die Namen der Module, die ihn erzeugen."""
    ergebnis: dict[str, list[str]] = {fall.schluessel: [] for fall in SONDERFAELLE}
    for modul in register.laden().values():
        for schluessel in modul.sonderfaelle:
            ergebnis.setdefault(schluessel, []).append(modul.name)
    return ergebnis


def ohne_modul() -> list[Sonderfall]:
    """Sonderfälle, die noch kein Modul erzeugt."""
    module = erzeugende_module()
    return [fall for fall in SONDERFAELLE if not module[fall.schluessel]]
