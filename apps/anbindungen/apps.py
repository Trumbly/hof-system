from django.apps import AppConfig


class AnbindungenConfig(AppConfig):
    name = "apps.anbindungen"
    label = "anbindungen"
    verbose_name = "Anbindungen"

    def ready(self) -> None:
        # Aktion „Anfrage entscheiden“ und die Links an den Arbeitsposten.
        from apps.anbindungen import aktionen  # noqa: F401
