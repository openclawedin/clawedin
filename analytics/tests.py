from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from analytics.middleware import match_skill_page_route
from analytics.models import SkillPageRequestMetric
from identity.auth import generate_api_token, hash_token, token_prefix
from identity.models import AgentDeployment, ApiToken, User


class SkillPageAnalyticsMiddlewareTests(TestCase):
    databases = "__all__"

    def test_matches_documented_skill_route(self):
        self.assertEqual(
            match_skill_page_route("/api/v1/me/"),
            (SkillPageRequestMetric.SOURCE_SKILL_MD, "/api/v1/me/"),
        )

    def test_tracks_anonymous_documented_request(self):
        response = self.client.get("/login/")

        self.assertEqual(response.status_code, 200)
        metric = SkillPageRequestMetric.objects.get(
            source=SkillPageRequestMetric.SOURCE_SKILL_MD,
            normalized_path="/login/",
            method="GET",
        )
        self.assertEqual(metric.actor_key, "anon")
        self.assertIsNone(metric.user)
        self.assertEqual(metric.total_calls, 1)
        self.assertEqual(metric.success_calls, 1)
        self.assertEqual(metric.error_calls, 0)

    def test_tracks_bearer_authenticated_skill_route(self):
        user = User.objects.create_user(
            username="skill-agent",
            password="secret123",
            email="agent@example.com",
        )
        raw_token = generate_api_token()
        ApiToken.objects.create(
            user=user,
            name="default",
            token_hash=hash_token(raw_token),
            prefix=token_prefix(raw_token),
        )

        response = self.client.get(
            "/api/v1/me/",
            HTTP_AUTHORIZATION=f"Bearer {raw_token}",
        )

        self.assertEqual(response.status_code, 200)
        metric = SkillPageRequestMetric.objects.get(
            source=SkillPageRequestMetric.SOURCE_SKILL_MD,
            normalized_path="/api/v1/me/",
            method="GET",
        )
        self.assertEqual(metric.user, user)
        self.assertEqual(metric.actor_key, f"user:{user.id}")
        self.assertEqual(metric.total_calls, 1)
        self.assertEqual(metric.success_calls, 1)

    @patch("identity.views._agent_clawedin_request")
    @patch("identity.views._resolve_pod")
    @patch("identity.views.load_kube_config")
    def test_tracks_agent_dashboard_prompt_call(self, _load_kube_config, _resolve_pod, agent_request):
        user = User.objects.create_user(
            username="dashboard-owner",
            password="secret123",
            email="owner@example.com",
            account_type=User.HUMAN,
        )
        self.client.force_login(user)
        AgentDeployment.objects.create(
            user=user,
            deployment_name="openclaw-agent-1",
            namespace="agents-dashboard-owner",
            pod_name="openclaw-agent-1-abc123",
            gateway_token="gateway-token",
            secret_name="gateway-secret",
            web_auth_token="web-token",
            web_auth_secret_name="web-secret",
        )

        class Metadata:
            name = "openclaw-agent-1-abc123"
            labels = {"app": "openclaw-agent", "owner": user.username, "deployment": "openclaw-agent-1"}

        class Pod:
            metadata = Metadata()

        _resolve_pod.return_value = (Pod(), "agents-dashboard-owner")
        agent_request.return_value = (200, {"text": "hello"})

        fake_kubernetes = SimpleNamespace(client=SimpleNamespace(CoreV1Api=lambda: object()))
        with patch.dict("sys.modules", {"kubernetes": fake_kubernetes}):
            response = self.client.post(
                reverse("identity:agent_dashboard_chat", args=["openclaw-agent-1-abc123"]),
                data='{"text":"hello"}',
                content_type="application/json",
            )

        self.assertEqual(response.status_code, 200)
        metric = SkillPageRequestMetric.objects.get(
            source=SkillPageRequestMetric.SOURCE_AGENT_DASHBOARD,
            normalized_path="/agents/manager/<pod_name>/dashboard/chat/",
            method="POST",
        )
        self.assertEqual(metric.user, user)
        self.assertEqual(metric.total_calls, 1)
        self.assertEqual(metric.success_calls, 1)
