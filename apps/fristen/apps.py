from django.apps import AppConfig


class FristenConfig(AppConfig):
    name = "apps.fristen"
    label = "fristen"
    verbose_name = "Fristen"

    def ready(self) -> None:
        from apps.cockpit import tageslauf
        from apps.fristen import (
            aktionen,  # noqa: F401  (registriert „Frist erledigt“)
            kacheln,
        )

        kacheln.anmelden()
        # Rücknahme eines Vorgangs: Fristen an entfernten Objekten erledigen (Review 2, A4).
        from apps.fristen import ruecknahme

        ruecknahme.registrieren()
        tageslauf.schritt_anmelden("fristen", reihenfolge=tageslauf.FRISTEN)
