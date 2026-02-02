from django.urls import path

from . import views

app_name = "content"

urlpatterns = [
    path("posts/", views.post_list, name="post_list"),
    path("posts/new/", views.post_create, name="post_create"),
    path("posts/<int:post_id>/", views.post_detail, name="post_detail"),
    path("posts/<int:post_id>/edit/", views.post_update, name="post_update"),
    path("posts/<int:post_id>/delete/", views.post_delete, name="post_delete"),
    path("posts/<int:post_id>/comments/new/", views.comment_create, name="comment_create"),
    path(
        "posts/<int:post_id>/comments/<int:comment_id>/reply/",
        views.comment_reply,
        name="comment_reply",
    ),
    path(
        "posts/<int:post_id>/comments/<int:comment_id>/delete/",
        views.comment_delete,
        name="comment_delete",
    ),
]
