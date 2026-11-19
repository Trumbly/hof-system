from django.apps import AppConfig


class CockpitConfig(AppConfig):
    name = "apps.cockpit"
    label = "cockpit"
    verbose_name = "Hof-Cockpit"

    def ready(self) -> None:
        # Eigene Kacheln (Freigaben, Läufe, Sicherung, Platzhalter „Rückstände“), eigene
        # Schritte des Tageslaufs und der Empfänger für Aufgaben im Hof-Backlog.
        from apps.cockpit import eigene_kacheln, services, tageslauf
        from apps.kern import aufgaben

        eigene_kacheln.anmelden()
        aufgaben.empfaenger_anmelden(services.aufgabe_vormerken)
        # Rücknahme eines Vorgangs: Aufgaben im Hof-Backlog folgen den Arbeitsposten (das
        # Erledigen merkt sie vor), sie werden nicht selbst zurückgedreht (Review 2, A4).
        from apps.cockpit.models import NotionAufgabe
        from apps.kern import services as kern

        kern.ruecknahmeweg_registrieren(NotionAufgabe, abgeleitet=lambda _daten: True)
        tageslauf.schritt_anmelden("aufgaben_abgleich", reihenfolge=tageslauf.COCKPIT)
        tageslauf.schritt_anmelden("sicherung_pruefen", reihenfolge=tageslauf.SICHERUNG)
