from django.apps import AppConfig


class StammdatenConfig(AppConfig):
    name = "apps.stammdaten"
    label = "stammdaten"
    verbose_name = "Stammdaten"

    def ready(self) -> None:
        # Prüft die Konfiguration beim Start: Ein Tippfehler fällt sofort auf, nicht erst
        # bei der ersten Kündigung.
        from apps.stammdaten import konfiguration

        konfiguration.laden()

        from apps.stammdaten import kacheln  # Kacheln „Leerstand“ und „Datenstand“

        kacheln.anmelden()

        # Protokoll: Filter nach Einheit und Partei schließt ihre Verträge ein (Review 2, B14).
        from apps.stammdaten import protokoll

        protokoll.registrieren()
