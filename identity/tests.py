from datetime import datetime, timezone
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse

from .models import User


@override_settings(
    STRIPE_SECRET_KEY="sk_test_123",
    STRIPE_PUBLISHABLE_KEY="pk_test_123",
    STRIPE_WEBHOOK_SECRET="whsec_123",
    STRIPE_PRICE_ID_FREE="price_free",
    STRIPE_PRICE_ID_PRO="price_pro",
    STRIPE_PRICE_ID_BUSINESS="price_business",
)
class BillingTests(TestCase):
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
