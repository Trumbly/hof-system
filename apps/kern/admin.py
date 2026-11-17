"""Verwaltungsseiten des Kerns. Fachdaten ändern die Seiten und Services, nicht der Admin."""

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from apps.kern.models import Benutzer


@admin.register(Benutzer)
class BenutzerAdmin(UserAdmin):  # type: ignore[type-arg]
    pass
