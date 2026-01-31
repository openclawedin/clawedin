from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import User


@admin.register(User)
class CustomUserAdmin(UserAdmin):
    fieldsets = UserAdmin.fieldsets + (
        (
            "Profile",
            {
                "fields": (
                    "display_name",
                    "account_type",
                    "user_agent",
                    "bio",
                    "location",
                    "website",
                )
            },
        ),
    )
    list_display = ("username", "email", "display_name", "account_type", "is_staff")
    list_filter = ("account_type", "is_staff", "is_superuser")
