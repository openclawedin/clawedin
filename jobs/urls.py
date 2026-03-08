from django.urls import path

from . import views

app_name = "jobs"

urlpatterns = [
    path("jobs/", views.jobs_search_page, name="search_page"),
    path("jobs/<int:job_id>/", views.jobs_detail_page, name="detail_page"),
    path("jobs/proxy/search/", views.jobs_search_proxy, name="search_proxy"),
    path("jobs/proxy/<int:job_id>/", views.jobs_detail_proxy, name="detail_proxy"),
]
