from django.apps import AppConfig


class SollConfig(AppConfig):
    name = "apps.soll"
    label = "soll"
    verbose_name = "Sollstellung"

    def ready(self) -> None:
        # Prüft die Konfiguration beim Start, meldet Links an Arbeitsposten und den
        # Stornoweg für Rücknahmen im Protokoll.
        from apps.soll import konfiguration, services, verweise  # noqa: F401

        konfiguration.laden()
        services.stornowege_registrieren()
