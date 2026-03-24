from datetime import datetime, timezone
from unittest.mock import Mock, patch

from django.test import Client, TestCase, override_settings
from django.urls import reverse

from companies.models import Company
from content.models import Post
from .auth import authenticate_bearer_token, check_token, mint_bearer_token
from .kube import agent_user_bearer_secret_name_for_deployment, agent_web_auth_secret_name_for_deployment
from .models import AgentDashboardTurn, AgentDeployment, ApiToken, User
from .views import (
    AGENT_DEFAULT_ANTHROPIC_MODEL,
    AGENT_DEFAULT_OPENAI_MODEL,
    _agent_models_config,
    _agent_secret_string_data,
    _delete_namespaced_secret_if_present,
    _maybe_queue_dashboard_bootstrap_turn,
    _resolve_agent_launch_credentials,
    _upsert_namespaced_secret,
)


@override_settings(
    STRIPE_SECRET_KEY="sk_test_123",
    STRIPE_PUBLISHABLE_KEY="pk_test_123",
    STRIPE_WEBHOOK_SECRET="whsec_123",
    STRIPE_PRICE_ID_FREE="price_free",
    STRIPE_PRICE_ID_PRO="price_pro",
    STRIPE_PRICE_ID_BUSINESS="price_business",
)
class BillingTests(TestCase):
    databases = "__all__"

    def setUp(self):
        self.user = User.objects.create_user(
            username="armen",
            email="armen@example.com",
            password="safe-pass-123",
        )
        self.client.force_login(self.user)

    def test_checkout_session_creates_customer_and_redirects(self):
        with (
            patch("identity.views.STRIPE_SDK_AVAILABLE", True),
            patch("identity.views.stripe") as mock_stripe,
        ):
            mock_stripe.Customer.create.return_value = {"id": "cus_123"}
            mock_stripe.checkout.Session.create.return_value = {
                "url": "https://checkout.stripe.test/session"
            }

            response = self.client.post(
                reverse("identity:checkout_session", args=[User.SERVICE_PRO]),
            )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, "https://checkout.stripe.test/session")
        self.user.refresh_from_db()
        self.assertEqual(self.user.stripe_customer_id, "cus_123")
        self.assertEqual(mock_stripe.checkout.Session.create.call_count, 1)

    def test_subscription_webhook_updates_user_tier(self):
        self.user.stripe_customer_id = "cus_123"
        self.user.save(update_fields=["stripe_customer_id"])
        subscription_payload = {
            "id": "sub_123",
            "customer": "cus_123",
            "status": "active",
            "current_period_end": 1760000000,
            "items": {
                "data": [
                    {
                        "price": {"id": "price_business"},
                    }
                ]
            },
        }

        with (
            patch("identity.views.STRIPE_SDK_AVAILABLE", True),
            patch("identity.views.stripe") as mock_stripe,
        ):
            mock_stripe.Webhook.construct_event.return_value = {
                "type": "customer.subscription.updated",
                "data": {"object": subscription_payload},
            }
            response = self.client.post(
                reverse("identity:stripe_webhook"),
                data="{}",
                content_type="application/json",
                HTTP_STRIPE_SIGNATURE="sig_123",
            )

        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertEqual(self.user.service_tier, User.SERVICE_BUSINESS)
        self.assertEqual(self.user.stripe_subscription_id, "sub_123")
        self.assertEqual(self.user.stripe_subscription_status, "active")
        self.assertEqual(
            self.user.stripe_current_period_end,
            datetime.fromtimestamp(1760000000, tz=timezone.utc),
        )

    def test_subscription_deleted_webhook_resets_to_no_plan(self):
        self.user.service_tier = User.SERVICE_PRO
        self.user.stripe_subscription_id = "sub_123"
        self.user.stripe_subscription_status = "active"
        self.user.stripe_price_id = "price_pro"
        self.user.save(
            update_fields=[
                "service_tier",
                "stripe_subscription_id",
                "stripe_subscription_status",
                "stripe_price_id",
            ],
        )

        with (
            patch("identity.views.STRIPE_SDK_AVAILABLE", True),
            patch("identity.views.stripe") as mock_stripe,
        ):
            mock_stripe.Webhook.construct_event.return_value = {
                "type": "customer.subscription.deleted",
                "data": {"object": {"id": "sub_123", "status": "canceled"}},
            }
            response = self.client.post(
                reverse("identity:stripe_webhook"),
                data="{}",
                content_type="application/json",
                HTTP_STRIPE_SIGNATURE="sig_123",
            )

        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertEqual(self.user.service_tier, User.SERVICE_NONE)
        self.assertEqual(self.user.stripe_subscription_status, "canceled")
        self.assertEqual(self.user.stripe_subscription_id, "")
        self.assertEqual(self.user.stripe_price_id, "")


class PublicProfileJsonTests(TestCase):
    databases = "__all__"

    def setUp(self):
        self.user = User.objects.create_user(
            username="publicuser",
            email="public@example.com",
            password="safe-pass-123",
            display_name="Public User",
            bio="Public bio",
            location="SF",
            website="https://example.com",
            show_email=True,
            show_location=True,
            show_website=True,
            show_bio=True,
            show_user_agent=False,
            show_skills=True,
            show_resumes=False,
        )

    def test_public_profile_json_by_format_query(self):
        response = self.client.get(
            reverse("identity:public_profile", args=[self.user.username]) + "?format=json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/json")
        payload = response.json()
        self.assertEqual(payload["username"], "publicuser")
        self.assertEqual(payload["contact"]["email"], "public@example.com")
        self.assertEqual(payload["about"]["bio"], "Public bio")
        self.assertEqual(payload["visibility"]["show_skills"], True)
        self.assertEqual(payload["resumes"], [])

    def test_public_profile_json_route_and_privacy(self):
        self.user.show_email = False
        self.user.save(update_fields=["show_email"])
        response = self.client.get(reverse("identity:public_profile_json", args=[self.user.username]))
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIsNone(payload["contact"]["email"])


class AgentLaunchConfigTests(TestCase):
    databases = "__all__"

    def setUp(self):
        self.user = User.objects.create_user(
            username="agentuser",
            email="agent@example.com",
            password="safe-pass-123",
        )

    def test_resolve_openai_provider_uses_saved_key(self):
        self.user.openai_api_key = "sk-openai-saved"
        self.user.save(update_fields=["openai_api_key"])

        resolved = _resolve_agent_launch_credentials(
            self.user,
            {
                "model_provider": "openai",
                "openai_api_key": "",
                "anthropic_api_key": "",
            },
        )

        self.assertEqual(resolved["provider"], "openai")
        self.assertEqual(resolved["default_model"], AGENT_DEFAULT_OPENAI_MODEL)
        self.assertEqual(resolved["openai_api_key"], "sk-openai-saved")
        self.assertEqual(resolved["errors"], {})
        self.assertEqual(
            resolved["secret_string_data"],
            {"OPENAI_API_KEY": "sk-openai-saved"},
        )

    def test_resolve_anthropic_provider_requires_claude_key(self):
        resolved = _resolve_agent_launch_credentials(
            self.user,
            {
                "model_provider": "anthropic",
                "openai_api_key": "sk-openai-live",
                "anthropic_api_key": "",
            },
        )

        self.assertEqual(
            resolved["errors"],
            {"anthropic_api_key": "Enter a Claude API key or choose OpenAI as the provider."},
        )

    def test_resolve_anthropic_provider_uses_saved_or_submitted_key(self):
        self.user.anthropic_api_key = "sk-ant-saved"
        self.user.save(update_fields=["anthropic_api_key"])

        resolved = _resolve_agent_launch_credentials(
            self.user,
            {
                "model_provider": "anthropic",
                "openai_api_key": "",
                "anthropic_api_key": "",
            },
        )

        self.assertEqual(resolved["provider"], "anthropic")
        self.assertEqual(resolved["default_model"], AGENT_DEFAULT_ANTHROPIC_MODEL)
        self.assertEqual(resolved["anthropic_api_key"], "sk-ant-saved")
        self.assertEqual(
            resolved["secret_string_data"],
            {"ANTHROPIC_API_KEY": "sk-ant-saved"},
        )

    def test_secret_string_data_only_includes_present_keys(self):
        self.assertEqual(_agent_secret_string_data("", ""), {})
        self.assertEqual(
            _agent_secret_string_data("sk-openai", "sk-ant"),
            {
                "OPENAI_API_KEY": "sk-openai",
                "ANTHROPIC_API_KEY": "sk-ant",
            },
        )

    def test_agent_models_config_tracks_selected_provider(self):
        self.assertEqual(
            _agent_models_config("openai"),
            {
                "defaults": {
                    "model": {"primary": AGENT_DEFAULT_OPENAI_MODEL},
                    "models": {AGENT_DEFAULT_OPENAI_MODEL: {}},
                }
            },
        )
        self.assertEqual(
            _agent_models_config("anthropic"),
            {
                "defaults": {
                    "model": {"primary": AGENT_DEFAULT_ANTHROPIC_MODEL},
                    "models": {AGENT_DEFAULT_ANTHROPIC_MODEL: {}},
                }
            },
        )


@override_settings(
    BEARER_TOKEN_SHARED_SECRET="shared-secret",
    BEARER_TOKEN_ISSUER="clawedin-app",
    BEARER_TOKEN_ACCEPTED_ISSUERS=["clawedin-app"],
)
class ApiTokenProfileTests(TestCase):
    databases = "__all__"

    def setUp(self):
        self.user = User.objects.create_user(
            username="tokenuser",
            email="token@example.com",
            password="safe-pass-123",
        )
        self.client.force_login(self.user)

    def test_profile_can_create_single_bearer_token(self):
        response = self.client.post(reverse("identity:api_token_create"))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(ApiToken.objects.filter(user=self.user).count(), 1)

        token = ApiToken.objects.get(user=self.user)
        session = self.client.session
        raw_token = session.get("generated_api_token")
        self.assertTrue(raw_token)
        self.assertEqual(token.prefix, raw_token[:12])
        self.assertTrue(check_token(raw_token, token.token_hash))

    def test_profile_regenerate_rotates_existing_token(self):
        self.client.post(reverse("identity:api_token_create"))
        first_token = self.client.session["generated_api_token"]

        response = self.client.post(reverse("identity:api_token_regenerate"))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(ApiToken.objects.filter(user=self.user).count(), 1)

        token = ApiToken.objects.get(user=self.user)
        second_token = self.client.session["generated_api_token"]
        self.assertNotEqual(first_token, second_token)
        self.assertEqual(token.prefix, second_token[:12])
        self.assertTrue(check_token(second_token, token.token_hash))
        self.assertFalse(check_token(first_token, token.token_hash))

    def test_bearer_token_authenticates_login_required_api_call(self):
        self.client.post(reverse("identity:api_token_create"))
        raw_token = self.client.session["generated_api_token"]
        self.client.logout()

        response = self.client.get(
            reverse("api:me"),
            HTTP_AUTHORIZATION=f"Bearer {raw_token}",
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["success"])
        self.assertEqual(payload["data"]["username"], self.user.username)

    def test_bearer_token_can_submit_django_form_without_csrf(self):
        self.client.post(reverse("identity:api_token_create"))
        raw_token = self.client.session["generated_api_token"]
        self.client.logout()

        response = self.client.post(
            reverse("companies:company_create"),
            {"name": "Bearer Co"},
            HTTP_AUTHORIZATION=f"Bearer {raw_token}",
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(Company.objects.filter(owner=self.user, name="Bearer Co").exists())

    def test_bearer_token_can_submit_post_form_without_session(self):
        self.client.post(reverse("identity:api_token_create"))
        raw_token = self.client.session["generated_api_token"]
        self.client.logout()

        response = self.client.post(
            reverse("content:post_create"),
            {"title": "Bearer title", "body": "Bearer body"},
            HTTP_AUTHORIZATION=f"Bearer {raw_token}",
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(Post.objects.filter(author=self.user, title="Bearer title").exists())

    def test_bearer_token_can_submit_form_without_csrf_via_redirect_auth_header(self):
        self.client.post(reverse("identity:api_token_create"))
        raw_token = self.client.session["generated_api_token"]
        self.client.logout()

        response = self.client.post(
            reverse("companies:company_create"),
            {"name": "Bearer Redirect Co"},
            REDIRECT_HTTP_AUTHORIZATION=f"Bearer {raw_token}",
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(Company.objects.filter(owner=self.user, name="Bearer Redirect Co").exists())

    def test_bearer_token_can_fetch_csrf_token(self):
        self.client.post(reverse("identity:api_token_create"))
        raw_token = self.client.session["generated_api_token"]
        self.client.logout()

        response = self.client.get(
            reverse("api:csrf"),
            HTTP_AUTHORIZATION=f"Bearer {raw_token}",
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["success"])
        self.assertTrue(payload["data"]["csrf_token"])
        self.assertEqual(payload["data"]["header"], "X-CSRFToken")
        self.assertEqual(payload["data"]["cookie"], "csrftoken")
        self.assertEqual(response["X-CSRFToken"], payload["data"]["csrf_token"])
        self.assertIn("csrftoken", response.cookies)

    def test_session_can_fetch_csrf_token_for_form_or_api_posts(self):
        response = self.client.get(reverse("api:csrf"))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["success"])
        self.assertTrue(payload["data"]["csrf_token"])
        self.assertIn("csrftoken", response.cookies)


class AgentWebAuthSecretTests(TestCase):
    databases = "__all__"

    def test_secret_name_is_normalized_for_deployments(self):
        name = agent_web_auth_secret_name_for_deployment("OpenClaw Agent Demo", 42)

        self.assertEqual(name, "agent-web-auth-openclaw-agent-demo")

    def test_upsert_secret_creates_when_missing(self):
        v1 = Mock()
        body = object()
        api_exception = type("FakeApiException", (Exception,), {})

        missing = api_exception()
        missing.status = 404
        v1.read_namespaced_secret.side_effect = missing

        _upsert_namespaced_secret(v1, "agents", "web-auth", body, api_exception)

        v1.create_namespaced_secret.assert_called_once_with("agents", body)
        v1.patch_namespaced_secret.assert_not_called()

    def test_upsert_secret_patches_when_present(self):
        v1 = Mock()
        body = object()
        api_exception = type("FakeApiException", (Exception,), {})

        _upsert_namespaced_secret(v1, "agents", "web-auth", body, api_exception)

        v1.patch_namespaced_secret.assert_called_once_with("web-auth", "agents", body)


class AgentDashboardConfigTests(TestCase):
    databases = "__all__"

    def setUp(self):
        self.user = User.objects.create_user(
            username="dashboard-config-user",
            email="dashboard@example.com",
            password="safe-pass-123",
            account_type=User.HUMAN,
        )
        self.client.force_login(self.user)

    def test_dashboard_config_persists_selected_items(self):
        response = self.client.post(
            reverse("identity:agent_dashboard_config", args=["openclaw-agent-1-abc123"]),
            data='{"items":["tracked_api_calls","failed_api_calls","prompt_turns"]}',
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertEqual(
            self.user.agent_dashboard_items,
            ["tracked_api_calls", "failed_api_calls", "prompt_turns"],
        )
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertEqual(
            payload["selectedDashboardItemKeys"],
            ["tracked_api_calls", "failed_api_calls", "prompt_turns"],
        )

    def test_delete_secret_helper_ignores_missing_secret_name(self):
        v1 = Mock()

        _delete_namespaced_secret_if_present(v1, "agents", "")

        v1.delete_namespaced_secret.assert_not_called()

    def test_delete_secret_helper_swallows_api_errors(self):
        v1 = Mock()
        v1.delete_namespaced_secret.side_effect = RuntimeError("boom")

        _delete_namespaced_secret_if_present(v1, "agents", "web-auth")

        v1.delete_namespaced_secret.assert_called_once_with("web-auth", "agents")


class AgentDashboardBootstrapTests(TestCase):
    databases = "__all__"

    def setUp(self):
        self.user = User.objects.create_user(
            username="dashboard-bootstrap-user",
            email="bootstrap@example.com",
            password="safe-pass-123",
            account_type=User.HUMAN,
            display_name="Bootstrap User",
        )
        self.deployment = AgentDeployment.objects.create(
            user=self.user,
            deployment_name="openclaw-agent-57-c91e2cc6",
            namespace="clawedin",
            pod_name="openclaw-agent-57-c91e2cc6-77fb9bdddc-68vmk",
            gateway_token="gateway-token",
            secret_name="agent-gateway-secret",
            web_auth_token="web-auth-token",
            web_auth_secret_name="agent-web-auth-openclaw-agent-57-c91e2cc6",
        )

    @patch("identity.views.threading.Thread")
    def test_bootstrap_turn_is_queued_once_after_gateway_is_online(self, mock_thread):
        mock_thread.return_value = Mock(start=Mock())

        created = _maybe_queue_dashboard_bootstrap_turn(
            self.user,
            self.deployment,
            self.deployment.pod_name,
            self.deployment.namespace,
            {"ok": True},
        )

        self.assertTrue(created)
        self.assertEqual(AgentDashboardTurn.objects.count(), 1)
        turn = AgentDashboardTurn.objects.get()
        self.assertEqual(turn.status, AgentDashboardTurn.STATUS_QUEUED)
        self.assertIn("CLAWEDIN_USER_BEARER_TOKEN", turn.prompt_text)
        self.assertIn("CLAWEDIN_USER_BEARER_TOKEN_FILE", turn.prompt_text)
        self.assertIn("/var/run/secrets/clawedin-user-bearer/token", turn.prompt_text)
        self.assertIn(
            agent_user_bearer_secret_name_for_deployment(self.deployment.deployment_name, self.user.id),
            turn.prompt_text,
        )
        self.deployment.refresh_from_db()
        self.assertIsNotNone(self.deployment.dashboard_bootstrap_sent_at)
        mock_thread.assert_called_once()
        mock_thread.return_value.start.assert_called_once()

        created_again = _maybe_queue_dashboard_bootstrap_turn(
            self.user,
            self.deployment,
            self.deployment.pod_name,
            self.deployment.namespace,
            {"ok": True},
        )

        self.assertFalse(created_again)
        self.assertEqual(AgentDashboardTurn.objects.count(), 1)

    @patch("identity.views.threading.Thread")
    def test_bootstrap_waits_until_gateway_is_online(self, mock_thread):
        created = _maybe_queue_dashboard_bootstrap_turn(
            self.user,
            self.deployment,
            self.deployment.pod_name,
            self.deployment.namespace,
            {"ok": False},
        )

        self.assertFalse(created)
        self.assertEqual(AgentDashboardTurn.objects.count(), 0)
        self.deployment.refresh_from_db()
        self.assertIsNone(self.deployment.dashboard_bootstrap_sent_at)
        mock_thread.assert_not_called()


@override_settings(DEBUG=False, ROOT_URLCONF="clawedin.test_error_urls")
class ProductionErrorPageTests(TestCase):
    databases = "__all__"

    def test_404_uses_safe_template_without_route_leakage(self):
        response = self.client.get("/missing-page/")

        self.assertEqual(response.status_code, 404)
        body = response.content.decode()
        self.assertIn("That page is not available.", body)
        self.assertNotIn("Using the URLconf defined in", body)
        self.assertNotIn("urlpatterns", body)
        self.assertNotIn("The current path", body)

    def test_500_uses_safe_template_without_traceback_details(self):
        client = Client(raise_request_exception=False)
        response = client.get("/boom/")

        self.assertEqual(response.status_code, 500)
        body = response.content.decode()
        self.assertIn("Something went wrong on our side.", body)
        self.assertNotIn("Traceback", body)
        self.assertNotIn("RuntimeError", body)
        self.assertNotIn("sensitive internal failure", body)


@override_settings(
    BEARER_TOKEN_SHARED_SECRET="shared-secret",
    BEARER_TOKEN_ISSUER="clawedin-app",
    BEARER_TOKEN_ACCEPTED_ISSUERS=["clawedin-app"],
    USER_DOMAIN_DB_ALIAS="users",
)
class SharedBearerTokenTests(TestCase):
    databases = "__all__"

    def test_signed_token_with_shared_secret_is_accepted(self):
        user = User.objects.create_user(
            username="sharedtokenuser",
            email="shared@example.com",
            password="safe-pass-123",
        )

        token = mint_bearer_token(user)
        auth_result = authenticate_bearer_token(token)

        self.assertIsNotNone(auth_result)
        self.assertEqual(auth_result.user.pk, user.pk)
        self.assertIsNone(auth_result.api_token)

    def test_issuer_validation_rejects_unaccepted_issuer(self):
        user = User.objects.create_user(
            username="badissueruser",
            email="bad-issuer@example.com",
            password="safe-pass-123",
        )

        token = mint_bearer_token(user, issuer="other-app")
        auth_result = authenticate_bearer_token(token)

        self.assertIsNone(auth_result)

    @patch("identity.auth.get_user_model")
    def test_shared_user_lookup_reads_from_users_alias(self, mock_get_user_model):
        user = Mock(is_active=True, password="hashed-password")
        manager = Mock()
        manager.db_manager.return_value.filter.return_value.first.return_value = user
        mock_get_user_model.return_value = Mock(_default_manager=manager)

        with patch("identity.auth._find_stored_api_token", return_value=None):
            with patch("identity.auth._password_hash_marker", return_value="marker"):
                with patch("identity.auth.signing.loads", return_value={
                    "user_id": "123",
                    "iss": "clawedin-app",
                    "purpose": "job_apply_cli",
                    "pwd": "marker",
                }):
                    auth_result = authenticate_bearer_token("signed-token")

        self.assertIsNotNone(auth_result)
        manager.db_manager.assert_called_once_with("users")
