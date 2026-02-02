from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render

from network.models import Connection

from .forms import PostForm
from .models import Post


@login_required
def post_list(request):
    connection_ids = Connection.objects.filter(user=request.user).values_list(
        "connected_user_id",
        flat=True,
    )
    posts = Post.objects.filter(author_id__in=[request.user.id, *connection_ids]).select_related(
        "author",
    )
    return render(request, "content/post_list.html", {"posts": posts})


@login_required
def post_detail(request, post_id):
    connection_ids = Connection.objects.filter(user=request.user).values_list(
        "connected_user_id",
        flat=True,
    )
    post = get_object_or_404(
        Post.objects.select_related("author"),
        id=post_id,
        author_id__in=[request.user.id, *connection_ids],
    )
    return render(
        request,
        "content/post_detail.html",
        {"post": post, "can_edit": post.author_id == request.user.id},
    )


@login_required
def post_create(request):
    if request.method == "POST":
        form = PostForm(request.POST)
        if form.is_valid():
            post = form.save(commit=False)
            post.author = request.user
            post.save()
            return redirect("content:post_detail", post_id=post.id)
    else:
        form = PostForm()
    return render(request, "content/post_form.html", {"form": form, "mode": "create"})


@login_required
def post_update(request, post_id):
    post = get_object_or_404(Post, id=post_id, author=request.user)
    if request.method == "POST":
        form = PostForm(request.POST, instance=post)
        if form.is_valid():
            form.save()
            return redirect("content:post_detail", post_id=post.id)
    else:
        form = PostForm(instance=post)
    return render(request, "content/post_form.html", {"form": form, "mode": "update", "post": post})


@login_required
def post_delete(request, post_id):
    post = get_object_or_404(Post, id=post_id, author=request.user)
    if request.method == "POST":
        post.delete()
        return redirect("content:post_list")
    return render(request, "content/post_confirm_delete.html", {"post": post})
