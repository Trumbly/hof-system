from django.urls import path

from . import api, views

app_name = "anbindungen"

urlpatterns = [
    path("anbindungen/", views.uebersicht, name="uebersicht"),
    path("anbindungen/anfragen/", views.anfragen, name="anfragen"),
    path("anbindungen/anfragen/<int:pk>/", views.anfrage, name="anfrage"),
    path("anbindungen/kennzahlen/", views.kennzahlen, name="kennzahlen"),
    path("anbindungen/abweichungen/", views.abweichungen, name="abweichungen"),
    path("anbindungen/vermarktung/", views.vermarktung, name="vermarktung"),
    path("anbindungen/einheiten/<int:pk>/bilder/", views.bilder, name="bilder"),
    path("anbindungen/bilder/<int:pk>/", views.bild, name="bild"),
    path("anbindungen/bilder/<int:pk>/entfernen/", views.bild_entfernen, name="bild_entfernen"),
    # Schnittstelle für die Website: nur mit Token, ohne Sitzung (apps.kern.middleware).
    path("api/website/katalog", api.katalog, name="katalog"),
    path("api/website/bilder/<int:pk>", api.bild, name="api_bild"),
    path("api/website/ereignis", api.ereignis, name="ereignis"),
]
