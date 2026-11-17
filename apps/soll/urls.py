from django.urls import path

from . import views

app_name = "soll"

urlpatterns = [
    path("", views.liste, name="liste"),
    path("vertraege/<int:pk>/", views.vertrag, name="vertrag"),
    path("posten/<int:pk>/stornieren/", views.stornieren, name="stornieren"),
]
