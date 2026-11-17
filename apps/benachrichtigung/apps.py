from django.apps import AppConfig


class BenachrichtigungConfig(AppConfig):
    name = "apps.benachrichtigung"
    label = "benachrichtigung"
    verbose_name = "Benachrichtigung"

    def ready(self) -> None:
        from apps.benachrichtigung import checks  # noqa: F401  (Prüfung für check --deploy)
