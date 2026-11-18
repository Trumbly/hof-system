from django.apps import AppConfig


class AbgleichConfig(AppConfig):
    name = "apps.abgleich"
    label = "abgleich"
    verbose_name = "Abgleich"

    def ready(self) -> None:
        # Aktionen der Arbeitsliste „Zahlungen klären“, Links an Arbeitsposten, Stornowege
        # im Protokoll, Schritt im Tageslauf und Kachel „Rückstände“ im Hof-Cockpit.
        from apps.abgleich import aktionen, services, verweise  # noqa: F401
        from apps.abgleich.models import Klaerfall
        from apps.cockpit import kacheln, tageslauf
        from apps.cockpit import services as cockpit

        services.stornowege_registrieren()
        cockpit.kennung_anmelden(Klaerfall, services.klaerfall_kennungen)
        tageslauf.schritt_anmelden(
            "abgleich",
            reihenfolge=tageslauf.ABGLEICH,
            abhaengig_von=("bank_abruf", "kassenbuch_einordnen"),
        )
        kacheln.kachel_anmelden(
            "rueckstaende",
            "abgleich/_kachel_rueckstaende.html",
            services.kachel_rueckstaende,
            kacheln.RUECKSTAENDE,
        )
