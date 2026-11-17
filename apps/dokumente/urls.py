from django.urls import path

from . import views

app_name = "dokumente"

urlpatterns = [
    path("vorlagen/", views.vorlagen, name="vorlagen"),
    path("vorlagen/neu/", views.vorlage_neu, name="vorlage_neu"),
    path("vorlagen/<int:pk>/", views.vorlage, name="vorlage"),
    path("vorlagen/<int:pk>/bearbeiten/", views.vorlage_bearbeiten, name="vorlage_bearbeiten"),
    path("vorlagen/<int:pk>/vorschau.pdf", views.vorlage_vorschau, name="vorlage_vorschau"),
    path("vorlagen/<int:pk>/freigeben/", views.vorlage_freigeben, name="vorlage_freigeben"),
    path(
        "vorlagen/<int:pk>/neue-version/", views.vorlage_neue_version, name="vorlage_neue_version"
    ),
    path("schreiben/", views.schreiben_liste, name="schreiben_liste"),
    path("schreiben/<int:pk>/", views.schreiben, name="schreiben"),
    path("schreiben/<int:pk>/pdf", views.schreiben_pdf, name="schreiben_pdf"),
    path("schreiben/<int:pk>/nachweis", views.schreiben_nachweis, name="schreiben_nachweis"),
    path("schreiben/<int:pk>/senden/", views.schreiben_senden, name="schreiben_senden"),
    path("schreiben/<int:pk>/verwerfen/", views.schreiben_verwerfen, name="schreiben_verwerfen"),
    path(
        "schreiben/<int:pk>/abschliessen/",
        views.schreiben_abschliessen,
        name="schreiben_abschliessen",
    ),
    path(
        "schreiben/<int:pk>/versandstand/",
        views.schreiben_versandstand,
        name="schreiben_versandstand",
    ),
    path("serien/neu/", views.serie_neu, name="serie_neu"),
    path("serien/<uuid:serie>/", views.serie, name="serie"),
    path("serien/<uuid:serie>/senden/", views.serie_senden, name="serie_senden"),
]
