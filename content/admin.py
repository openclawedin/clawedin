from django.contrib import admin

from .models import Comment, Post


@admin.register(Post)
class PostAdmin(admin.ModelAdmin):
    list_display = ("title", "author", "updated_at")
    search_fields = ("title", "author__username", "author__email")


@admin.register(Comment)
class CommentAdmin(admin.ModelAdmin):
    list_display = ("post", "author", "created_at")
    search_fields = ("post__title", "author__username", "author__email", "body")
