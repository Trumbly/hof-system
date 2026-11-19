from django.apps import AppConfig


class BankConfig(AppConfig):
    name = "apps.bank"
    label = "bank"
    verbose_name = "Bank"

    def ready(self) -> None:
        # Aktionen „Erneut abrufen“ und „Freigabe erneuern“ (Review 1, Punkt 8).
        from apps.bank import (
            aktionen,  # noqa: F401
            suche,
            verweise,  # noqa: F401  (Links an Arbeitsposten der Bank)
        )
        from apps.cockpit import tageslauf

        tageslauf.schritt_anmelden("bank_abruf", reihenfolge=tageslauf.BANK)
        # Suche in der Kopfzeile: Umsätze (Review 2, B1).
        suche.anmelden()
