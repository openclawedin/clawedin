from unittest.mock import Mock, patch

from django.test import TestCase
from django.urls import reverse
from requests import RequestException


class JobsApiTests(TestCase):
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
