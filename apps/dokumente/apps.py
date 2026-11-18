from django.apps import AppConfig


class DokumenteConfig(AppConfig):
    name = "apps.dokumente"
    label = "dokumente"
    verbose_name = "Dokumente"

    def ready(self) -> None:
        from apps.dokumente import aktionen, bezuege, services  # noqa: F401
        from apps.kassenbuch import services as kassenbuch

        bezuege.objekt_anmelden()
        # Monatsabschluss (H6.6): Buchungen ohne Pflichtbeleg erscheinen als Hinweis.
        kassenbuch.belegpruefung_anmelden(services.buchungen_ohne_beleg)
