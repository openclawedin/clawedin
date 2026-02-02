from django.urls import path

from . import views

app_name = "companies"

urlpatterns = [
    path("companies/", views.company_list, name="company_list"),
    path("companies/new/", views.company_create, name="company_create"),
    path("companies/<slug:slug>/", views.company_detail, name="company_detail"),
]
