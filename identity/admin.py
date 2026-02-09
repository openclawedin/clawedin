from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import (
    Resume,
    ResumeCertification,
    ResumeEducation,
    ResumeExperience,
    ResumeProject,
    ResumeSkill,
    UserSkill,
    User,
)


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
                    "solana_public_key",
                    "service_tier",
                    "stripe_customer_id",
                    "stripe_subscription_id",
                    "stripe_price_id",
                    "stripe_subscription_status",
                    "stripe_current_period_end",
                )
            },
        ),
    )
    list_display = (
        "username",
        "email",
        "display_name",
        "account_type",
        "service_tier",
        "is_staff",
    )
    list_filter = ("account_type", "service_tier", "is_staff", "is_superuser")


@admin.register(Resume)
class ResumeAdmin(admin.ModelAdmin):
    list_display = ("title", "user", "updated_at")
    search_fields = ("title", "user__username", "user__email")


@admin.register(ResumeExperience)
class ResumeExperienceAdmin(admin.ModelAdmin):
    list_display = ("title", "resume", "company", "company_name", "is_current")
    search_fields = ("title", "company_name", "resume__user__username")


@admin.register(ResumeEducation)
class ResumeEducationAdmin(admin.ModelAdmin):
    list_display = ("school", "degree", "resume")
    search_fields = ("school", "degree", "resume__user__username")


@admin.register(ResumeSkill)
class ResumeSkillAdmin(admin.ModelAdmin):
    list_display = ("name", "proficiency", "resume")
    search_fields = ("name", "resume__user__username")


@admin.register(ResumeProject)
class ResumeProjectAdmin(admin.ModelAdmin):
    list_display = ("name", "role", "resume")
    search_fields = ("name", "role", "resume__user__username")


@admin.register(ResumeCertification)
class ResumeCertificationAdmin(admin.ModelAdmin):
    list_display = ("name", "issuer", "resume")
    search_fields = ("name", "issuer", "resume__user__username")


@admin.register(UserSkill)
class UserSkillAdmin(admin.ModelAdmin):
    list_display = ("name", "proficiency", "user", "years_of_experience")
    search_fields = ("name", "user__username", "user__email")
