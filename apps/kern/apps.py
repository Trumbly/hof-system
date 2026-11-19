from django.apps import AppConfig


class KernConfig(AppConfig):
    name = "apps.kern"
    label = "kern"
    verbose_name = "Kern"

    def ready(self) -> None:
        from apps.cockpit import tageslauf
        from apps.kern import aktionen  # noqa: F401  (registriert die Standard-Aktionen)

        tageslauf.schritt_anmelden("arbeitsposten_pflege", reihenfolge=tageslauf.PFLEGE)
