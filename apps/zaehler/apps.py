from django.apps import AppConfig


class ZaehlerConfig(AppConfig):
    name = "apps.zaehler"
    label = "zaehler"
    verbose_name = "Zähler"

    def ready(self) -> None:
        # Prüft die Konfiguration beim Start und meldet den Link an Arbeitsposten an.
        from apps.zaehler import konfiguration, verweise  # noqa: F401

        konfiguration.laden()
