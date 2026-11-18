from django.apps import AppConfig

#: Rang im Tageslauf: nach dem Hof-Cockpit (80), vor der Sicherung (90). Die Kennzahlen
#: brauchen den Abgleich (Rückstände), die Fristen und die Stammdaten des Tages.
STATUSMCP = 85


class StatusmcpConfig(AppConfig):
    name = "apps.statusmcp"
    label = "statusmcp"
    verbose_name = "Status-MCP"

    def ready(self) -> None:
        from apps.cockpit import tageslauf

        tageslauf.schritt_anmelden(
            "statusmcp_aktualisieren",
            reihenfolge=STATUSMCP,
            abhaengig_von=("abgleich", "fristen"),
        )
