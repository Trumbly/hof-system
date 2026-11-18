from django.apps import AppConfig


class MahnwesenConfig(AppConfig):
    name = "apps.mahnwesen"
    label = "mahnwesen"
    verbose_name = "Mahnwesen"

    def ready(self) -> None:
        # Aktion und Link an „Mahnungen senden“, NW-IDs für Aufgaben im Hof-Backlog, Lauf
        # im Tageslauf (montags nach dem Abgleich) und Kachel „Mahnfälle“ im Hof-Cockpit.
        from apps.cockpit import kacheln, tageslauf
        from apps.cockpit import services as cockpit
        from apps.mahnwesen import aktionen, services  # noqa: F401
        from apps.mahnwesen.models import Mahnfall

        cockpit.kennung_anmelden(Mahnfall, services.fall_kennungen)
        tageslauf.schritt_anmelden(
            "mahnlauf",
            reihenfolge=tageslauf.MAHNWESEN,
            nur_wochentag=0,
            abhaengig_von=("abgleich",),
        )
        kacheln.kachel_anmelden(
            "mahnfaelle",
            "mahnwesen/_kachel_mahnfaelle.html",
            services.kachel_mahnfaelle,
            kacheln.MAHNFAELLE,
        )
