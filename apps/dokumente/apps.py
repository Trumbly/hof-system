from django.apps import AppConfig


class DokumenteConfig(AppConfig):
    name = "apps.dokumente"
    label = "dokumente"
    verbose_name = "Dokumente"

    def ready(self) -> None:
        from apps.dokumente import bezuege

        bezuege.objekt_anmelden()
