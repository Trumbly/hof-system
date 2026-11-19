from django.apps import AppConfig


class AbgleichConfig(AppConfig):
    name = "apps.abgleich"
    label = "abgleich"
    verbose_name = "Abgleich"

    def ready(self) -> None:
        # Aktionen der Arbeitsliste „Zahlungen klären“, Links an Arbeitsposten, Stornowege
        # im Protokoll, Schritt im Tageslauf und Kachel „Rückstände“ im Hof-Cockpit.
        # Posteingang der Arbeitslisten: Klärfall mit Karten in der Zeile (Review 2, B4).
        from apps.abgleich import (  # noqa: F401
            aktionen,
            posteingang,
            services,
            verweise,
        )
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
