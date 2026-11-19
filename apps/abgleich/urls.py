from django.urls import path

from . import views

app_name = "abgleich"

urlpatterns = [
    path("rueckstaende/", views.rueckstaende, name="rueckstaende"),
    path("vertrag/<int:pk>/", views.mietkonto, name="mietkonto"),
    path("vertrag/<int:pk>/handzahlung/", views.handzahlung, name="handzahlung"),
    path("klaerfall/<int:pk>/", views.klaerfall, name="klaerfall"),
    path("klaerfall/<int:pk>/zuordnen/", views.zuordnen, name="zuordnen"),
    path("klaerfall/<int:pk>/suche/", views.vertrag_suche, name="vertrag_suche"),
    path("klaerfall/<int:pk>/aufteilen/", views.aufteilen, name="aufteilen"),
    path("klaerfall/<int:pk>/regel/", views.regel_aus_klaerfall, name="regel_aus_klaerfall"),
    path("regeln/", views.zahlerregeln, name="zahlerregeln"),
    path("regeln/<int:regel>/", views.zahlerregel, name="zahlerregel"),
]
