from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render

from network.models import Connection

from .forms import CommentForm, PostForm
from .models import Comment, Post


def _visible_post_for_user(user, post_id):
    connection_ids = Connection.objects.filter(user=user).values_list(
        "connected_user_id",
        flat=True,
    )
    return get_object_or_404(
        Post.objects.select_related("author"),
        id=post_id,
        author_id__in=[user.id, *connection_ids],
    )


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
    post = _visible_post_for_user(request.user, post_id)
    comment_form = CommentForm()
    comments = (
        post.comments.filter(parent__isnull=True)
        .select_related("author")
        .prefetch_related("replies__author")
    )
    return render(
        request,
        "content/post_detail.html",
        {
            "post": post,
            "can_edit": post.author_id == request.user.id,
            "comments": comments,
            "comment_form": comment_form,
        },
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


@login_required
def comment_create(request, post_id):
    post = _visible_post_for_user(request.user, post_id)
    if request.method != "POST":
        return redirect("content:post_detail", post_id=post.id)
    form = CommentForm(request.POST)
    if form.is_valid():
        comment = form.save(commit=False)
        comment.post = post
        comment.author = request.user
        comment.parent = None
        comment.save()
        return redirect("content:post_detail", post_id=post.id)

    comments = (
        post.comments.filter(parent__isnull=True)
        .select_related("author")
        .prefetch_related("replies__author")
    )
    return render(
        request,
        "content/post_detail.html",
        {
            "post": post,
            "can_edit": post.author_id == request.user.id,
            "comments": comments,
            "comment_form": form,
        },
    )


@login_required
def comment_delete(request, post_id, comment_id):
    post = _visible_post_for_user(request.user, post_id)
    comment = get_object_or_404(Comment, id=comment_id, post=post, author=request.user)
    if request.method == "POST":
        comment.delete()
    return redirect("content:post_detail", post_id=post.id)


@login_required
def comment_reply(request, post_id, comment_id):
    post = _visible_post_for_user(request.user, post_id)
    parent = get_object_or_404(Comment, id=comment_id, post=post)
    if request.method != "POST":
        return redirect("content:post_detail", post_id=post.id)

    form = CommentForm(request.POST)
    if form.is_valid():
        reply = form.save(commit=False)
        reply.post = post
        reply.author = request.user
        reply.parent = parent
        reply.save()
        return redirect("content:post_detail", post_id=post.id)

    comments = (
        post.comments.filter(parent__isnull=True)
        .select_related("author")
        .prefetch_related("replies__author")
    )
    return render(
        request,
        "content/post_detail.html",
        {
            "post": post,
            "can_edit": post.author_id == request.user.id,
            "comments": comments,
            "comment_form": CommentForm(),
            "reply_error_for_comment_id": parent.id,
            "reply_form_errors": form.body.errors,
        },
    )
