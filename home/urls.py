from django.urls import path

from . import views

urlpatterns = [
    path("", views.landing, name="home"),
    path("privacy/", views.privacy, name="privacy"),
    path("terms/", views.terms, name="terms"),
]
