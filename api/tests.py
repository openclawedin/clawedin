from unittest.mock import Mock, patch

from django.test import TestCase, override_settings
from django.urls import reverse
from requests import RequestException

from identity.auth import check_token
from identity.models import ApiToken, User


class JobsApiTests(TestCase):
    databases = "__all__"

    @patch("api.views.requests.get")
    def test_jobs_search_endpoint(self, mock_get):
        mock_response = Mock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {"count": 1, "results": [{"id": 1, "title": "Engineer"}]}
        mock_get.return_value = mock_response

        response = self.client.get(reverse("api:jobs_search"), {"q": "python", "page": "2"})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["success"])
        self.assertEqual(payload["data"]["jobs"]["count"], 1)
        called_params = mock_get.call_args.kwargs["params"]
        self.assertEqual(called_params["search"], "python")
        self.assertEqual(called_params["page"], "2")

    @patch("api.views.requests.get")
    def test_jobs_detail_endpoint(self, mock_get):
        mock_response = Mock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {"id": 42, "title": "Data Engineer"}
        mock_get.return_value = mock_response

        response = self.client.get(reverse("api:job_detail", kwargs={"job_id": 42}))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["success"])
        self.assertEqual(payload["data"]["job"]["id"], 42)
        self.assertIn("/api/jobs/42/", mock_get.call_args.args[0])

    @patch("api.views.requests.get")
    def test_jobs_search_handles_upstream_error(self, mock_get):
        mock_get.side_effect = RequestException("boom")

        response = self.client.get(reverse("api:jobs_search"), {"search": "engineer"})

        self.assertEqual(response.status_code, 502)
        payload = response.json()
        self.assertFalse(payload["success"])


@override_settings(
    BEARER_TOKEN_SHARED_SECRET="shared-secret",
    BEARER_TOKEN_ISSUER="clawedin-app",
    BEARER_TOKEN_ACCEPTED_ISSUERS=["clawedin-app"],
)
class TokensApiTests(TestCase):
    databases = "__all__"

    def setUp(self):
        self.user = User.objects.create_user(
            username="apitokenuser",
            email="api-token@example.com",
            password="safe-pass-123",
        )
        self.client.force_login(self.user)

    def test_post_tokens_generates_and_returns_single_token(self):
        response = self.client.post(
            reverse("api:tokens"),
            data="{}",
            content_type="application/json",
            HTTP_X_CSRFTOKEN="test-csrf-token",
        )

        self.assertEqual(response.status_code, 403)

    def test_post_tokens_rotates_existing_token_with_valid_csrf(self):
        self.client.cookies["csrftoken"] = "test-csrf-token"
        first_response = self.client.post(
            reverse("api:tokens"),
            data="{}",
            content_type="application/json",
            HTTP_X_CSRFTOKEN="test-csrf-token",
        )
        self.assertEqual(first_response.status_code, 201)
        first_payload = first_response.json()["data"]

        second_response = self.client.post(
            reverse("api:tokens"),
            data='{"name":"CLI"}',
            content_type="application/json",
            HTTP_X_CSRFTOKEN="test-csrf-token",
        )

        self.assertEqual(second_response.status_code, 201)
        self.assertEqual(ApiToken.objects.filter(user=self.user).count(), 1)

        token = ApiToken.objects.get(user=self.user)
        second_payload = second_response.json()["data"]
        self.assertNotEqual(first_payload["token"], second_payload["token"])
        self.assertTrue(second_payload["regenerated"])
        self.assertEqual(token.name, "CLI")
        self.assertTrue(check_token(second_payload["token"], token.token_hash))

    def test_get_tokens_returns_current_token_metadata_only(self):
        self.client.cookies["csrftoken"] = "test-csrf-token"
        self.client.post(
            reverse("api:tokens"),
            data="{}",
            content_type="application/json",
            HTTP_X_CSRFTOKEN="test-csrf-token",
        )

        response = self.client.get(reverse("api:tokens"))

        self.assertEqual(response.status_code, 200)
        tokens = response.json()["data"]["tokens"]
        self.assertEqual(len(tokens), 1)
        self.assertIn("prefix", tokens[0])
