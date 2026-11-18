from django.urls import path

from . import views

app_name = "dokumente"

urlpatterns = [
    # Ablage und Belege (Schritt 3a)
    path("", views.ablage, name="ablage"),
    path("dokument/<int:pk>/", views.dokument, name="dokument"),
    path("dokument/<int:pk>/pdf", views.dokument_pdf, name="dokument_pdf"),
    path("dokument/<int:pk>/original", views.dokument_original, name="dokument_original"),
    path("belege/hochladen/", views.beleg_hochladen, name="beleg_hochladen"),
    path("belege/vorschlag/", views.beleg_vorschlag, name="beleg_vorschlag"),
    path("belege/<int:pk>/", views.beleg, name="beleg"),
    path("belege/<int:pk>/buchung/", views.beleg_buchung, name="beleg_buchung"),
    path("belege/<int:pk>/verwerfen/", views.beleg_verwerfen, name="beleg_verwerfen"),
    path("belege/<int:pk>/umwidmen/", views.beleg_umwidmen, name="beleg_umwidmen"),
    path("belege/<int:pk>/aufteilen/", views.beleg_aufteilen, name="beleg_aufteilen"),
    path("lieferanten/", views.lieferanten, name="lieferanten"),
    path("lieferanten/<int:pk>/", views.lieferant, name="lieferant"),
    path("rechnungen/", views.rechnungen, name="rechnungen"),
    path("rechnungen/<int:pk>/", views.rechnung, name="rechnung"),
    path("rechnungen/<int:pk>/verbinden/", views.rechnung_verbinden, name="rechnung_verbinden"),
    path("rechnungen/<int:pk>/strittig/", views.rechnung_strittig, name="rechnung_strittig"),
    path("belegmappe/", views.belegmappe_seite, name="belegmappe"),
    path("belegmappe/mappe.pdf", views.belegmappe_pdf, name="belegmappe_pdf"),
    # Vorlagen und Schreiben (Schritt 3c)
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
