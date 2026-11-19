from django.urls import path

from apps.cockpit import views as cockpit

from . import stil, suchseite, views

app_name = "kern"

urlpatterns = [
    # Die Startseite ist das Hof-Cockpit (Schritt 9); der Name bleibt kern:startseite.
    path("", cockpit.startseite, name="startseite"),
    path("arbeitslisten/", views.arbeitslisten, name="arbeitslisten"),
    path(
        "arbeitslisten/<int:pk>/<slug:aktion>/",
        views.arbeitsposten_aktion,
        name="arbeitsposten_aktion",
    ),
    path("protokoll/", views.protokoll, name="protokoll"),
    path("protokoll/vorgang/<uuid:pk>/", views.vorgang, name="vorgang"),
    path(
        "protokoll/vorgang/<uuid:pk>/zuruecknehmen/",
        views.vorgang_zuruecknehmen,
        name="vorgang_zuruecknehmen",
    ),
    path("laeufe/", views.laeufe, name="laeufe"),
    # Bausteine der neuen Gestaltung mit Beispielen (Review 2, C3)
    path("stil/", stil.stilseite, name="stil"),
    path("stil/dialog/", stil.stil_dialog, name="stil_dialog"),
    # Suche in der Kopfzeile (Review 2, B1) und Konto mit zweitem Faktor (Review 2, B15)
    path("suche/", suchseite.suchen, name="suche"),
    path("konto/", suchseite.konto, name="konto"),
    # Gesundheits-Endpunkt ohne Anmeldung: nur „ok“ und Version, keine Daten.
    path("gesund", views.gesund, name="gesund"),
]
