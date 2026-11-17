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
