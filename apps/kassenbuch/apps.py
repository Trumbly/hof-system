from django.apps import AppConfig


class KassenbuchConfig(AppConfig):
    name = "apps.kassenbuch"
    label = "kassenbuch"
    verbose_name = "Kassenbuch"

    def ready(self) -> None:
        # Aktionen der Arbeitsliste, Links an Arbeitsposten und Stornowege im Protokoll.
        from apps.kassenbuch import aktionen, services, verweise  # noqa: F401

        services.stornowege_registrieren()

        from apps.cockpit import tageslauf

        tageslauf.schritt_anmelden(
            "kassenbuch_einordnen", reihenfolge=tageslauf.EINORDNEN, abhaengig_von=("bank_abruf",)
        )
