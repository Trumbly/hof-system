from django.apps import AppConfig


class BenachrichtigungConfig(AppConfig):
    name = "apps.benachrichtigung"
    label = "benachrichtigung"
    verbose_name = "Benachrichtigung"

    def ready(self) -> None:
        from apps.benachrichtigung import checks  # noqa: F401  (Prüfung für check --deploy)
        from apps.cockpit import tageslauf

        tageslauf.schritt_anmelden("benachrichtigen", reihenfolge=tageslauf.BENACHRICHTIGEN)
