from django.apps import AppConfig


class DokumenteConfig(AppConfig):
    name = "apps.dokumente"
    label = "dokumente"
    verbose_name = "Dokumente"

    def ready(self) -> None:
        from apps.dokumente import aktionen, bezuege, services  # noqa: F401
        from apps.fristen import services as fristen
        from apps.kassenbuch import services as kassenbuch

        bezuege.objekt_anmelden()
        # Rücknahme eines Vorgangs: Schreiben verwerfen, versandte sperren (Review 2, A4).
        from apps.dokumente import ruecknahme

        ruecknahme.registrieren()
        # Kacheln „Belege zuordnen“ und „Eingangsrechnungen“ im Hof-Cockpit.
        from apps.dokumente import kacheln

        kacheln.anmelden()
        # Monatsabschluss (H6.6): Buchungen ohne Pflichtbeleg erscheinen als Hinweis.
        kassenbuch.belegpruefung_anmelden(services.buchungen_ohne_beleg)
        # Lauf kassenbuch_einordnen: Abbuchungen offener Eingangsrechnungen erkennen (3b).
        kassenbuch.abgleich_anmelden("eingangsrechnungen", services.rechnungen_abgleichen)
        # Review 2, A7 (danach): Beleg und Abbuchung passen, nur der Betrag nicht.
        kassenbuch.abgleich_anmelden(
            "eingangsrechnungen_abweichungen", services.belegabweichungen_pruefen
        )
        # Lauf fristen: Posten „Rechnung zahlen“ nach der Fälligkeit als überfällig markieren.
        fristen.pruefung_anmelden(
            "Rechnungen überfällig", services.rechnungen_ueberfaellig_markieren
        )
        # Suche in der Kopfzeile: Belege und Eingangsrechnungen (Review 2, B1).
        from apps.dokumente import suche

        suche.anmelden()
