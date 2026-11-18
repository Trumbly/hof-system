from django.urls import path

from . import views

app_name = "mahnwesen"

urlpatterns = [
    path("", views.faelle, name="faelle"),
    path("fall/<int:pk>/", views.fall, name="fall"),
    path("stufen/", views.stufen, name="stufen"),
    path("stufen/<int:pk>/", views.stufe, name="stufe"),
    path("sperren/", views.sperren, name="sperren"),
    path("sperren/<int:pk>/aufheben/", views.sperre_aufheben, name="sperre_aufheben"),
    path("vertrag/<int:pk>/kuendigen/", views.kuendigen, name="kuendigen"),
]
