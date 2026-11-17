from django.urls import path

from . import views, zulieferung

app_name = "bank"

urlpatterns = [
    path("bank/", views.konten, name="konten"),
    path("bank/umsaetze/", views.umsaetze, name="umsaetze"),
    path("bank/import/", views.import_seite, name="import"),
    path("bank/konten/<int:pk>/freigabe/", views.freigabe, name="freigabe"),
    path("bank/freigabe/rueckkehr/", views.freigabe_rueckkehr, name="freigabe_rueckkehr"),
    # Zulieferung vom hof-mac: nur mit Token, ohne Sitzung (apps.kern.middleware).
    path("api/bank/zulieferung", zulieferung.zulieferung, name="zulieferung"),
]
