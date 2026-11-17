from django.apps import AppConfig


class BankConfig(AppConfig):
    name = "apps.bank"
    label = "bank"
    verbose_name = "Bank"

    def ready(self) -> None:
        from apps.bank import verweise  # noqa: F401  (Links an Arbeitsposten der Bank)
