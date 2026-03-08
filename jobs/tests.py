from unittest.mock import Mock, patch

from django.test import TestCase
from django.urls import reverse
from requests import RequestException


class JobsViewsTests(TestCase):
    def test_jobs_page_renders(self):
        response = self.client.get(reverse("jobs:search_page"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Search jobs")

    def test_job_detail_page_renders(self):
        response = self.client.get(reverse("jobs:detail_page", kwargs={"job_id": 42}))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Loading job details")

    @patch("jobs.views.requests.get")
    def test_jobs_proxy_passes_allowed_params(self, mock_get):
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"count": 1, "results": [{"id": 1, "title": "Engineer"}]}
        mock_response.raise_for_status.return_value = None
        mock_get.return_value = mock_response

        response = self.client.get(
            reverse("jobs:search_proxy"),
            {"q": "python", "location": "San Francisco", "page_size": "5", "not_allowed": "ignored"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["count"], 1)
        mock_get.assert_called_once()
        called_params = mock_get.call_args.kwargs["params"]
        self.assertEqual(called_params["search"], "python")
        self.assertEqual(called_params["location"], "San Francisco")
        self.assertEqual(called_params["page_size"], "5")
        self.assertNotIn("not_allowed", called_params)

    @patch("jobs.views.requests.get")
    def test_jobs_proxy_handles_upstream_errors(self, mock_get):
        mock_get.side_effect = RequestException("boom")

        response = self.client.get(reverse("jobs:search_proxy"), {"search": "data engineer"})

        self.assertEqual(response.status_code, 502)
        self.assertIn("error", response.json())

    @patch("jobs.views.requests.get")
    def test_job_detail_proxy_returns_payload(self, mock_get):
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"id": 42, "title": "Engineer"}
        mock_response.raise_for_status.return_value = None
        mock_get.return_value = mock_response

        response = self.client.get(reverse("jobs:detail_proxy", kwargs={"job_id": 42}))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["id"], 42)
        self.assertIn("/api/jobs/42/", mock_get.call_args.args[0])
