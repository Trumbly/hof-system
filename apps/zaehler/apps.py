from django.apps import AppConfig


class ZaehlerConfig(AppConfig):
    name = "apps.zaehler"
    label = "zaehler"
    verbose_name = "Zähler"

    def ready(self) -> None:
        # Prüft die Konfiguration beim Start und meldet den Link an Arbeitsposten an.
        from apps.zaehler import konfiguration, verweise  # noqa: F401

        konfiguration.laden()

        # Suche in der Kopfzeile (Review 2, B1): Zählernummer
        from apps.zaehler import suche

        suche.anmelden()

        # Täglich im Tageslauf nach den Fristen; der Lauf prüft das Ablesefenster selbst.
        from apps.cockpit import tageslauf

        tageslauf.schritt_anmelden("zaehler_stichtag", reihenfolge=tageslauf.FRISTEN + 5)
