from django.urls import path

from . import views

app_name = "jobs"

urlpatterns = [
    path("jobs/", views.jobs_search_page, name="search_page"),
    path("jobs/proxy/search/", views.jobs_search_proxy, name="search_proxy"),
]
