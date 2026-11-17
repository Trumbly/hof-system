from django.urls import path

from . import views

app_name = "fristen"

urlpatterns = [
    path("", views.fristenliste, name="liste"),
    path("<int:pk>/erledigt/", views.erledigt, name="erledigt"),
]
