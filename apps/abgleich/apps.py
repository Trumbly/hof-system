from django.apps import AppConfig


class AbgleichConfig(AppConfig):
    name = "apps.abgleich"
    label = "abgleich"
    verbose_name = "Abgleich"

    def ready(self) -> None:
        # Aktionen der Arbeitsliste „Zahlungen klären“, Links an Arbeitsposten und Stornowege
        # im Protokoll.
        from apps.abgleich import aktionen, services, verweise  # noqa: F401

        services.stornowege_registrieren()
