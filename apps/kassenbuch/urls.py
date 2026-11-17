from django.urls import path

from . import views

app_name = "kassenbuch"

urlpatterns = [
    path("kassenbuch/", views.buchungen, name="buchungen"),
    path("kassenbuch/umsatz/<int:pk>/", views.umsatz, name="umsatz"),
    path("kassenbuch/umsatz/<int:pk>/zuordnen/", views.zuordnen, name="zuordnen"),
    path("kassenbuch/umsatz/<int:pk>/aufteilen/", views.aufteilen, name="aufteilen"),
    path(
        "kassenbuch/umsatz/<int:pk>/aufteilen/zeile/",
        views.aufteilen_zeile,
        name="aufteilen_zeile",
    ),
    path("kassenbuch/umsatz/<int:pk>/stornieren/", views.stornieren, name="stornieren"),
    path("kassenbuch/umsatz/<int:pk>/regel/", views.regel_aus_umsatz, name="regel_aus_umsatz"),
    path("kassenbuch/regeln/", views.regeln, name="regeln"),
    path("kassenbuch/regeln/neu/", views.regel_bearbeiten, name="regel_neu"),
    path("kassenbuch/regeln/<int:pk>/", views.regel_bearbeiten, name="regel"),
    path("kassenbuch/regeln/<int:pk>/anwenden/", views.regel_anwenden, name="regel_anwenden"),
    path("kassenbuch/abschluesse/", views.abschluesse, name="abschluesse"),
    path("kassenbuch/bericht/", views.bericht, name="bericht"),
    path(
        "kassenbuch/bericht/<int:objekt>/<int:jahr>/<int:monat>/pdf/",
        views.bericht_pdf,
        name="bericht_pdf",
    ),
]
