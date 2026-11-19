"""Testhof-Modul der Anbindungen (Schritt 10): Website und Verwaltio.

- **Buchungsanfragen** (über ``services.ereignis_annehmen``, wie von der Website gemeldet):
  eine offene Buchung für die erste freie Garage, eine angenommene Buchung für die erste
  freie Einheit, die keine Garage ist (damit nicht im Katalog), eine offene Kündigung für
  einen Stellplatzvertrag.
  Kontaktdaten von Faker (``de_DE``), Mails auf ``.invalid``, Telefon aus dem fiktiven
  Bereich.
- **Kennzahlen** aus ``FakeVerwaltio`` für die sechs Wohnungen am Stichtag.
- **ID-Abweichung:** Der Website-Fake führt eine NW-ID klein geschrieben (die des ersten
  vermarkteten Lagers); der Abgleich am Stichtag macht daraus eine Abweichung
  „andere Schreibweise“ mit Arbeitsposten „ID-Abweichung“.

Alle Zeitpunkte rechnen von ``aufbau.ZEITPUNKT`` aus. Geschrieben wird über
``apps.anbindungen.services``. Bilder legt der Testhof nicht an (Dateinamen sind zufällig).
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass

from apps.anbindungen import services
from apps.anbindungen.models import (
    Abweichung,
    Buchungsanfrage,
    Kennzahl,
)
from apps.anbindungen.verwaltio import FakeVerwaltio
from apps.anbindungen.website import FakeWebsite
from apps.stammdaten.models import Einheit, Vertrag
from testhof import erfunden, register
from testhof.aufbau import Kontext, TesthofFehler
from testhof.stammdaten import StammdatenDaten

NAME = "anbindungen"


@dataclass(frozen=True)
class AnbindungenDaten:
    buchung_offen: Buchungsanfrage
    buchung_angenommen: Buchungsanfrage
    kuendigung: Buchungsanfrage
    kennzahlen: list[Kennzahl]
    #: Die absichtliche Abweichung im Website-Fake: Hof-ID -> ID auf der Website.
    website_umbenannt: dict[str, str]
    abweichung: Abweichung

    @property
    def anfragen(self) -> list[Buchungsanfrage]:
        return [self.buchung_offen, self.buchung_angenommen, self.kuendigung]


def _ereignis(
    kontext: Kontext,
    nummer: int,
    art: str,
    einheit: Einheit,
    *,
    vor_tagen: int,
    wunsch_in_tagen: int,
    vertragsnummer: str = "",
) -> services.WebsiteEreignis:
    f, z = kontext.faker, kontext.zufall
    vorname, nachname = f.first_name(), f.last_name()
    return services.WebsiteEreignis(
        ereignis_id=f"web-testhof-{nummer:04d}",
        art=art,
        nw_id=einheit.nw_id,
        gemeldet_am=kontext.zeitpunkt - datetime.timedelta(days=vor_tagen, hours=nummer),
        wunsch_ab=kontext.stichtag + datetime.timedelta(days=wunsch_in_tagen),
        vertragsnummer=vertragsnummer,
        kontakt_name=f"{vorname} {nachname}",
        kontakt_email=erfunden.email(z, vorname, nachname),
        kontakt_telefon=erfunden.telefon(z, mobil=True),
        nachricht=(
            "Ich interessiere mich für die Einheit. Bitte melden Sie sich."
            if art == Buchungsanfrage.Art.BUCHUNG
            else "Hiermit kündige ich fristgerecht."
        ),
    )


def aufbauen(kontext: Kontext) -> AnbindungenDaten:
    stammdaten: StammdatenDaten = kontext.ablage["stammdaten"]
    frei = sorted(stammdaten.frei(), key=lambda e: e.nw_id)
    garage = next(e for e in frei if e.typ == Einheit.Typ.GARAGE)
    andere = next(e for e in frei if e.typ != Einheit.Typ.GARAGE)
    stellplatz_vertrag = next(
        v
        for v in sorted(stammdaten.aktive_vertraege(), key=lambda v: v.nummer)
        if v.vertragsart == Vertrag.Art.S and v.ende is None
    )

    offen, _ = services.ereignis_annehmen(
        _ereignis(kontext, 1, "buchung", garage, vor_tagen=2, wunsch_in_tagen=30)
    )
    angenommen, _ = services.ereignis_annehmen(
        _ereignis(kontext, 2, "buchung", andere, vor_tagen=5, wunsch_in_tagen=14)
    )
    angenommen = services.buchungsanfrage_entscheiden(
        angenommen, Buchungsanfrage.Status.ANGENOMMEN, am=kontext.zeitpunkt
    )
    kuendigung, _ = services.ereignis_annehmen(
        _ereignis(
            kontext,
            3,
            "kuendigung",
            stellplatz_vertrag.einheiten.get(),
            vor_tagen=1,
            wunsch_in_tagen=90,
            vertragsnummer=stellplatz_vertrag.nummer,
        )
    )

    services.kennzahlen_abrufen(FakeVerwaltio(), kontext.stichtag)
    kennzahlen = list(Kennzahl.objects.filter(datum=kontext.stichtag).order_by("nw_id"))

    erstes_lager = (
        services.vermarktbare_einheiten(kontext.stichtag)
        .filter(typ=Einheit.Typ.LAGER)
        .order_by("nw_id")
        .first()
    )
    if erstes_lager is None:
        raise TesthofFehler("Der Testhof hat kein vermarktetes Lager.")
    umbenannt = {erstes_lager.nw_id: erstes_lager.nw_id.lower()}
    services.id_abgleich(
        website=FakeWebsite(umbenennen=umbenannt),
        verwaltio=FakeVerwaltio(),
        datum=kontext.stichtag,
    )
    abweichung = Abweichung.objects.get(behoben_am__isnull=True)
    return AnbindungenDaten(
        buchung_offen=offen,
        buchung_angenommen=angenommen,
        kuendigung=kuendigung,
        kennzahlen=kennzahlen,
        website_umbenannt=umbenannt,
        abweichung=abweichung,
    )


register.registrieren(NAME, aufbauen=aufbauen, abhaengig_von=("stammdaten",))
