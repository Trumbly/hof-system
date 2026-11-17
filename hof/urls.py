"""Adressen des Hof-Systems."""

from allauth.account.decorators import secure_admin_login
from django.contrib import admin
from django.urls import include, path

# Die Verwaltungsseiten haben keine eigene Anmeldung: Sie leiten auf die Anmeldung
# mit zweitem Faktor um.
admin.site.login = secure_admin_login(admin.site.login)  # type: ignore[method-assign]
admin.site.site_header = "Hof-System · Verwaltung"

urlpatterns = [
    path("verwaltung/", admin.site.urls),
    path("konto/", include("allauth.urls")),
    path("", include("apps.kern.urls")),
]
