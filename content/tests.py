from django.test import TestCase
from django.urls import reverse

from content.models import Comment, Post
from identity.models import User
from network.models import Connection


class PostFeedTests(TestCase):
    def setUp(self):
        self.viewer = User.objects.create_user(
            username="viewer",
            email="viewer@example.com",
            password="safe-pass-123",
        )
        self.connected = User.objects.create_user(
            username="connected",
            email="connected@example.com",
            password="safe-pass-123",
        )
        self.stranger = User.objects.create_user(
            username="stranger",
            email="stranger@example.com",
            password="safe-pass-123",
        )
        self.viewer_post = Post.objects.create(author=self.viewer, title="Mine", body="mine")
        self.connected_post = Post.objects.create(
            author=self.connected,
            title="Connected",
            body="connected body",
        )
        self.stranger_post = Post.objects.create(
            author=self.stranger,
            title="Stranger",
            body="stranger body",
        )
        Connection.objects.create(user=self.viewer, connected_user=self.connected)
        Connection.objects.create(user=self.connected, connected_user=self.viewer)
        self.client.force_login(self.viewer)

    def test_feed_includes_connected_posts(self):
        response = self.client.get(reverse("content:post_list"))

        posts = list(response.context["posts"])
        self.assertIn(self.viewer_post, posts)
        self.assertIn(self.connected_post, posts)
        self.assertNotIn(self.stranger_post, posts)

    def test_connected_post_detail_is_accessible(self):
        response = self.client.get(
            reverse("content:post_detail", kwargs={"post_id": self.connected_post.id}),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.connected_post.title)

    def test_stranger_post_detail_is_hidden(self):
        response = self.client.get(
            reverse("content:post_detail", kwargs={"post_id": self.stranger_post.id}),
        )

        self.assertEqual(response.status_code, 404)

    def test_user_can_comment_on_connected_post(self):
        response = self.client.post(
            reverse("content:comment_create", kwargs={"post_id": self.connected_post.id}),
            data={"body": "Nice update!"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            Comment.objects.filter(
                post=self.connected_post,
                author=self.viewer,
                body="Nice update!",
            ).exists(),
        )

    def test_user_cannot_comment_on_stranger_post(self):
        response = self.client.post(
            reverse("content:comment_create", kwargs={"post_id": self.stranger_post.id}),
            data={"body": "I should not be able to comment"},
        )

        self.assertEqual(response.status_code, 404)
