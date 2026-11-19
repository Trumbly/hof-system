from django.urls import path

from apps.cockpit import views as cockpit

from . import views

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
    # Gesundheits-Endpunkt ohne Anmeldung: nur „ok“ und Version, keine Daten.
    path("gesund", views.gesund, name="gesund"),
]
