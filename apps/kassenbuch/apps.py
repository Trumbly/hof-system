from django.apps import AppConfig


class KassenbuchConfig(AppConfig):
    name = "apps.kassenbuch"
    label = "kassenbuch"
    verbose_name = "Kassenbuch"

    def ready(self) -> None:
        # Aktionen der Arbeitsliste, Links an Arbeitsposten und Stornowege im Protokoll.
        # Posteingang der Arbeitslisten: „Umsätze einordnen“ in der Zeile (Review 2, B3).
        from apps.kassenbuch import (  # noqa: F401
            aktionen,
            posteingang,
            services,
            verweise,
        )

        services.stornowege_registrieren()
        # Rücknahme eines Vorgangs: Buchungen stornieren (Review 2, A4).
        from apps.kassenbuch import ruecknahme

        ruecknahme.registrieren()

        from apps.cockpit import tageslauf

        tageslauf.schritt_anmelden(
            "kassenbuch_einordnen", reihenfolge=tageslauf.EINORDNEN, abhaengig_von=("bank_abruf",)
        )
