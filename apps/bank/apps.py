from django.apps import AppConfig


class BankConfig(AppConfig):
    name = "apps.bank"
    label = "bank"
    verbose_name = "Bank"

    def ready(self) -> None:
        from apps.bank import verweise  # noqa: F401  (Links an Arbeitsposten der Bank)
        from apps.cockpit import tageslauf

        tageslauf.schritt_anmelden("bank_abruf", reihenfolge=tageslauf.BANK)
