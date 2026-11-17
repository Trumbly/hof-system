from django.urls import path

from . import views

app_name = "kern"

urlpatterns = [
    path("", views.startseite, name="startseite"),
    path("arbeitslisten/", views.arbeitslisten, name="arbeitslisten"),
    path(
        "arbeitslisten/<int:pk>/<slug:aktion>/",
        views.arbeitsposten_aktion,
        name="arbeitsposten_aktion",
    ),
    path("protokoll/", views.protokoll, name="protokoll"),
    path("protokoll/zuruecknehmen/", views.zuruecknehmen, name="zuruecknehmen"),
    path("laeufe/", views.laeufe, name="laeufe"),
    # Gesundheits-Endpunkt ohne Anmeldung: nur „ok“ und Version, keine Daten.
    path("gesund", views.gesund, name="gesund"),
]
