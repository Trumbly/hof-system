from django.urls import path

from . import datenstandseite, uebergaben, views

app_name = "stammdaten"

urlpatterns = [
    path("", views.uebersicht, name="uebersicht"),
    path("einheiten/", views.einheiten, name="einheiten"),
    path("einheiten/neu/", views.einheit_neu, name="einheit_neu"),
    path("einheiten/<int:pk>/", views.einheit, name="einheit"),
    path("parteien/", views.parteien, name="parteien"),
    path("parteien/neu/", views.partei_neu, name="partei_neu"),
    path("parteien/<int:pk>/", views.partei, name="partei"),
    path("parteien/suche/", views.partei_suche, name="partei_suche"),
    path(
        "kontaktwege/<int:pk>/entfernen/",
        views.kontaktweg_entfernen,
        name="kontaktweg_entfernen",
    ),
    path("vertraege/", views.vertraege, name="vertraege"),
    path("vertraege/neu/", views.vertrag_neu, name="vertrag_neu"),
    path("vertraege/<int:pk>/", views.vertrag, name="vertrag"),
    path("vertraege/<int:pk>/dokument/", views.vertragsdokument, name="vertragsdokument"),
    path(
        "vertraege/<int:pk>/<str:art>/beginnen/",
        views.uebergabe_starten,
        name="uebergabe_starten",
    ),
    # Anstehende Übergaben und Rückgaben (Knopf „Übergabe“ unten am Handy, Review 2, C1)
    path("uebergaben/", uebergaben.uebergaben, name="uebergaben"),
    path("uebergaben/<int:pk>/schritt/<int:schritt>/", views.uebergabe, name="uebergabe"),
    path("uebergaben/fotos/<int:pk>/", views.uebergabe_foto, name="uebergabe_foto"),
    path("checkpunkte/<int:pk>/", views.checkpunkt, name="checkpunkt"),
    path("leerstand/", views.leerstand_seite, name="leerstand"),
    path("datenstand/", datenstandseite.seite, name="datenstand"),
    path("datenstand/scans/", datenstandseite.scans_hochladen, name="datenstand_scans"),
]
