from django.urls import path

from . import views

app_name = "kern"

urlpatterns = [
    path("", views.startseite, name="startseite"),
]
