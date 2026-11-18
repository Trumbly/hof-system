"""Regelimport aus hof-fints und Rückrechnung im Probelauf (Abnahme Schritt 6)."""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.abgleich import importieren
from apps.abgleich.importformat import REGELN_YAML
from apps.abgleich.models import Zahlerregel
from apps.kassenbuch.models import Zuordnungsregel
from apps.kern.models import Lauf
from apps.stammdaten.models import Vertrag
from testhof import pruefsumme
from testhof.aufbau import Testhof

BEISPIEL = Path(__file__).parents[1] / "fixtures" / "abgleich" / "regeln_beispiel.yaml"


def _befehl(*argumente: str) -> str:
    ausgabe = io.StringIO()
    call_command(*argumente, stdout=ausgabe)
    return ausgabe.getvalue()


def test_regelimport_probelauf_aendert_nichts(testhof: Testhof) -> None:
    """Abnahme Schritt 6: Der Regelimport läuft im Probelauf (Standard) und ändert nichts,
    auch kein Laufprotokoll. Der Bericht nennt nur Zähler, Eintrag-IDs und NW-IDs."""
    vorher = pruefsumme.pruefsumme()
    laeufe = Lauf.objects.count()

    text = _befehl("abgleich_regeln_import", str(BEISPIEL))

    assert pruefsumme.pruefsumme() == vorher
    assert Lauf.objects.count() == laeufe
    assert "Probelauf: nichts geändert." in text
    assert "Einträge gelesen: 9" in text
    assert "Zahlerregeln neu: 4" in text
    assert "Vertrag nicht gefunden: 1 (Einträge 4)" in text
    assert "Wohnraum (Verwaltio): 1 (Einträge 5); NW-IDs NW-W-0001" in text
    assert "keine Bedingung für den Zahler: 1 (Einträge 6)" in text
    assert "doppelt in der Datei: 1 (Einträge 7)" in text
    assert "Einheit nicht gefunden: 1 (Einträge 8); NW-IDs NW-G-0099" in text
    for verboten in ("Erika", "Testfirma", "DE38", "85.00", "Lager", "Landwirt"):
        assert verboten not in text


def test_regelimport_ausfuehren_und_zweimal(testhof: Testhof) -> None:
    """Mit --ausfuehren entstehen die Zahlerregeln (Regel im Kassenbuch nach IBAN, sonst
    nach Name und Zweck); ein zweiter Import findet sie unverändert vor."""
    regeln = Zuordnungsregel.objects.count()

    text = _befehl("abgleich_regeln_import", str(BEISPIEL), "--ausfuehren")

    assert "Ausgeführt." in text
    assert Zuordnungsregel.objects.count() == regeln + 4
    eins = Zahlerregel.objects.get(herkunft="hof-fints:1")
    assert eins.vertrag == Vertrag.objects.get(nummer="V-0020")
    assert str(eins.betrag) == "85.00"
    assert eins.regel.gegen_iban == "DE38049414175814551889"
    zwei = Zahlerregel.objects.get(herkunft="hof-fints:2")
    assert [v.nummer for v in zwei.vertraege()] == ["V-0030", "V-0031"]
    drei = Zahlerregel.objects.get(herkunft="hof-fints:3")
    assert (drei.regel.gegen_iban, drei.regel.gegenpartei) == ("", "Testfirma GmbH")
    assert Zahlerregel.objects.get(herkunft="hof-fints:50").nicht_vertragsbezogen
    lauf = Lauf.objects.filter(name="abgleich_regeln_import").first()
    assert lauf is not None and lauf.gebucht == 4

    zweiter = importieren.importieren(BEISPIEL.read_text(), REGELN_YAML, ausfuehren=True)
    assert (zweiter.neu, zweiter.unveraendert) == (0, 4)


def test_regelimport_haengt_an_vorhandene_regel_und_nennt_abweichung(
    testhof: Testhof, tmp_path: Path
) -> None:
    """Gibt es im Kassenbuch schon eine Regel mit der IBAN, bekommt sie die Zahlerregel; eine
    abweichende Zahlerregel wird nicht überschrieben, sondern gezählt (mit NW-ID)."""
    regel = Zuordnungsregel.objects.get(bezeichnung="Miete G-03")  # im Testhof ohne Zahlerregel
    vertrag = Vertrag.objects.get(nummer="V-0003")
    nw = vertrag.einheiten.get().nw_id
    andere = Zuordnungsregel.objects.get(bezeichnung="Miete G-05")  # hat Vertrag V-0005
    datei = tmp_path / "regeln.yaml"
    datei.write_text(
        "mietregeln:\n"
        f"  - id: 1\n    zahler: {{iban: '{regel.gegen_iban}'}}\n    vertrag: V-0003\n"
        f"  - id: 2\n    zahler: {{iban: '{andere.gegen_iban}'}}\n    vertrag: V-0006\n"
        f"  - id: 3\n    zahler: {{name: 'Niemand', zweck: 'Garage'}}\n    nw_id: {nw}\n",
        encoding="utf-8",
    )

    bericht = importieren.importieren(datei.read_text(), REGELN_YAML, ausfuehren=True)

    assert (bericht.neu, bericht.an_vorhandene_regel, bericht.neue_kassenbuch_regeln) == (1, 1, 0)
    assert Zahlerregel.objects.get(regel=regel).vertrag == vertrag
    assert bericht.fehlerhaft[importieren.ABWEICHEND] == 1
    assert bericht.eintraege_je_art[importieren.ABWEICHEND] == ["2"]
    assert bericht.fehlerhaft[importieren.NICHT_LAUFEND] == 1  # V-0003 ist beendet
    assert nw in bericht.nw_ids_je_art[importieren.NICHT_LAUFEND]
    assert Zahlerregel.objects.get(regel=andere).vertrag.nummer == "V-0005"  # type: ignore[union-attr]


def test_regelimport_kaputte_datei(tmp_path: Path, db: None) -> None:
    datei = tmp_path / "regeln.yaml"
    datei.write_text("nur: text\n", encoding="utf-8")
    with pytest.raises(CommandError, match="Abschnitte"):
        _befehl("abgleich_regeln_import", str(datei))


def test_rueckrechnung_falscher_zeitraum(db: None) -> None:
    with pytest.raises(CommandError, match="vor"):
        _befehl("abgleich_rueckrechnung", "--von", "2026-12", "--bis", "2026-07")
