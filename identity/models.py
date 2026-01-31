from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    HUMAN = "human"
    AGENT = "agent"
    ACCOUNT_TYPE_CHOICES = [
        (HUMAN, "Human"),
        (AGENT, "Agent"),
    ]

    display_name = models.CharField(max_length=150, blank=True)
    account_type = models.CharField(
        max_length=10,
        choices=ACCOUNT_TYPE_CHOICES,
        default=HUMAN,
    )
    user_agent = models.CharField(
        max_length=255,
        blank=True,
        help_text="Optional agent or client identifier.",
    )
    bio = models.TextField(blank=True)
    location = models.CharField(max_length=120, blank=True)
    website = models.URLField(blank=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return self.get_full_name() or self.display_name or self.username
