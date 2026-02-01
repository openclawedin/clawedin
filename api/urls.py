from django.urls import path

from . import views

app_name = "api"

urlpatterns = [
    path("health/", views.health, name="health"),
    path("me/", views.me, name="me"),
    path("tokens/", views.tokens, name="tokens"),
    path("tokens/<int:token_id>/", views.token_detail, name="token_detail"),
    path("posts/", views.posts, name="posts"),
    path("posts/<int:post_id>/", views.post_detail, name="post_detail"),
    path("companies/", views.companies, name="companies"),
    path("companies/<slug:slug>/", views.company_detail, name="company_detail"),
    path("skills/", views.user_skills, name="user_skills"),
    path("skills/<int:skill_id>/", views.user_skill_detail, name="user_skill_detail"),
    path("resumes/", views.resumes, name="resumes"),
    path("resumes/<int:resume_id>/", views.resume_detail, name="resume_detail"),
]
