from django.apps import AppConfig


class AnbindungenConfig(AppConfig):
    name = "apps.anbindungen"
    label = "anbindungen"
    verbose_name = "Anbindungen"

    def ready(self) -> None:
        # Aktion „Anfrage entscheiden“ und die Links an den Arbeitsposten.
        from apps.anbindungen import (
            aktionen,  # noqa: F401
            kacheln,
        )
        from apps.cockpit import tageslauf

        kacheln.anmelden()
        tageslauf.schritt_anmelden("verwaltio_kennzahlen", reihenfolge=tageslauf.VERWALTIO)
        tageslauf.schritt_anmelden("id_abgleich", reihenfolge=tageslauf.VERWALTIO + 5)
