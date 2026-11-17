from django.apps import AppConfig


class FristenConfig(AppConfig):
    name = "apps.fristen"
    label = "fristen"
    verbose_name = "Fristen"

    def ready(self) -> None:
        from apps.fristen import aktionen  # noqa: F401  (registriert „Frist erledigt“)
