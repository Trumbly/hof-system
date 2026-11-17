from django.urls import path

from . import views

app_name = "zaehler"

urlpatterns = [
    path("", views.uebersicht, name="uebersicht"),
    path("neu/", views.neu, name="neu"),
    path("besuch/", views.besuch, name="besuch"),
    path("<int:pk>/", views.zaehler, name="zaehler"),
    path("objekte/<int:pk>/ablesung/", views.ablesung, name="ablesung"),
    path("staende/<int:pk>/foto/", views.foto, name="foto"),
]
