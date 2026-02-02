from django.test import TestCase
from django.urls import reverse

from identity.models import User

from .models import Company


class CompanyViewsTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="owner",
            email="owner@example.com",
            password="safe-pass-123",
        )
        self.alpha = Company.objects.create(
            name="Alpha Labs",
            industry="AI",
            tagline="Agent tooling",
            headquarters="San Francisco",
        )
        self.beta = Company.objects.create(
            name="Beta Health",
            industry="Health",
            tagline="Care systems",
            headquarters="Boston",
        )

    def test_company_list_shows_companies(self):
        response = self.client.get(reverse("companies:company_list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.alpha.name)
        self.assertContains(response, self.beta.name)

    def test_company_list_search_filters_results(self):
        response = self.client.get(reverse("companies:company_list"), {"q": "alpha"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.alpha.name)
        self.assertNotContains(response, self.beta.name)

    def test_company_create_requires_auth(self):
        response = self.client.get(reverse("companies:company_create"))
        self.assertEqual(response.status_code, 302)

    def test_authenticated_user_can_create_company(self):
        self.client.force_login(self.user)
        response = self.client.post(
            reverse("companies:company_create"),
            data={
                "name": "Gamma Ops",
                "tagline": "Ops automation",
                "description": "Desc",
                "website": "https://gamma.example.com",
                "industry": "Software",
                "company_type": "private",
                "company_size": "11-50",
                "headquarters": "New York",
                "founded_year": 2024,
                "specialties": "Automation,Agents",
                "logo_url": "https://example.com/logo.png",
                "cover_url": "https://example.com/cover.png",
            },
        )
        self.assertEqual(response.status_code, 302)
        created = Company.objects.get(name="Gamma Ops")
        self.assertEqual(created.owner, self.user)
