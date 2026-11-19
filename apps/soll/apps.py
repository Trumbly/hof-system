from django.apps import AppConfig


class SollConfig(AppConfig):
    name = "apps.soll"
    label = "soll"
    verbose_name = "Sollstellung"

    def ready(self) -> None:
        # Prüft die Konfiguration beim Start, meldet Links an Arbeitsposten und den
        # Stornoweg für Rücknahmen im Protokoll.
        from apps.soll import konfiguration, services, verweise  # noqa: F401

        konfiguration.laden()
        services.stornowege_registrieren()
        # Rücknahme eines Vorgangs: Soll-Haken danach (Review 2, A4).
        from apps.soll import ruecknahme

        ruecknahme.registrieren()

        # Kachel „Offene Posten“; soll_monat ist idempotent und läuft darum täglich mit.
        from apps.cockpit import tageslauf
        from apps.soll import kacheln

        kacheln.anmelden()
        tageslauf.schritt_anmelden("soll_monat", reihenfolge=tageslauf.SOLL)
