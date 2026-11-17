"""Adressen des Hof-Systems."""

from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("verwaltung/", admin.site.urls),
    path("", include("apps.kern.urls")),
]
