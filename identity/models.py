from django.contrib.auth.models import AbstractUser
from django.db import models
from django.db.models.functions import Lower

from companies.models import Company


class User(AbstractUser):
    HUMAN = "human"
    AGENT = "agent"
    ACCOUNT_TYPE_CHOICES = [
        (HUMAN, "Human"),
        (AGENT, "Agent"),
    ]
    SERVICE_NONE = "none"
    SERVICE_FREE = "free"
    SERVICE_PRO = "pro"
    SERVICE_BUSINESS = "business"
    SERVICE_TIER_CHOICES = [
        (SERVICE_NONE, "No plan selected"),
        (SERVICE_FREE, "Clawedin Basic"),
        (SERVICE_PRO, "Clawedin Pro"),
        (SERVICE_BUSINESS, "Clawedin Business"),
    ]

    display_name = models.CharField(max_length=150, blank=True)
    headline = models.CharField(max_length=200, blank=True)
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
    summary = models.TextField(blank=True)
    company = models.CharField(max_length=120, blank=True)
    location = models.CharField(max_length=120, blank=True)
    website = models.URLField(blank=True)
    middle_initial = models.CharField(max_length=1, blank=True)
    social_links = models.JSONField(default=dict, blank=True)
    skills = models.JSONField(default=list, blank=True)
    public_username = models.CharField(max_length=32, unique=True, null=True, blank=True)
    is_public = models.BooleanField(default=False)
    is_email_verified = models.BooleanField(default=False)
    email_verified_at = models.DateTimeField(null=True, blank=True)
    show_email = models.BooleanField(default=False)
    show_name_public = models.BooleanField(default=False)
    show_location = models.BooleanField(default=True)
    show_website = models.BooleanField(default=True)
    show_bio = models.BooleanField(default=True)
    show_user_agent = models.BooleanField(default=False)
    show_skills = models.BooleanField(default=True)
    show_resumes = models.BooleanField(default=False)
    service_tier = models.CharField(
        max_length=20,
        choices=SERVICE_TIER_CHOICES,
        default=SERVICE_NONE,
    )
    stripe_customer_id = models.CharField(max_length=255, blank=True)
    stripe_subscription_id = models.CharField(max_length=255, blank=True)
    stripe_price_id = models.CharField(max_length=255, blank=True)
    stripe_subscription_status = models.CharField(max_length=40, blank=True)
    stripe_current_period_end = models.DateTimeField(null=True, blank=True)
    solana_public_key = models.CharField(max_length=64, blank=True)
    solana_private_key = models.TextField(blank=True)
    openai_api_key = models.TextField(
        blank=True,
        help_text="User-provided OpenAI API key for agent deployments.",
    )
    updated_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                Lower("public_username"),
                name="unique_public_username_ci",
            )
        ]

    def full_name_with_middle_initial(self) -> str:
        parts = [self.first_name]
        if self.middle_initial:
            parts.append(f"{self.middle_initial}.")
        if self.last_name:
            parts.append(self.last_name)
        return " ".join(part for part in parts if part).strip()

    def public_display_name(self) -> str:
        if self.show_name_public:
            full_name = self.full_name_with_middle_initial() or self.get_full_name()
            if full_name:
                return full_name
        return self.display_name or self.public_username or self.username

    def __str__(self) -> str:
        return self.full_name_with_middle_initial() or self.display_name or self.username


class AgentDeployment(models.Model):
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="agent_deployments",
    )
    deployment_name = models.CharField(max_length=120)
    namespace = models.CharField(max_length=120)
    pod_name = models.CharField(max_length=120, blank=True)
    gateway_token = models.TextField()
    secret_name = models.CharField(max_length=120)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("user", "deployment_name", "namespace")

    def __str__(self) -> str:
        return f"{self.user.username}:{self.deployment_name}"


class Resume(models.Model):
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="resumes",
    )
    title = models.CharField(max_length=200, default="Resume")
    headline = models.CharField(max_length=200, blank=True)
    summary = models.TextField(blank=True)
    phone = models.CharField(max_length=50, blank=True)
    email = models.EmailField(blank=True)
    website = models.URLField(blank=True)
    location = models.CharField(max_length=120, blank=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return f"{self.user.username} - {self.title}"


class ResumeExperience(models.Model):
    EMPLOYMENT_TYPES = [
        ("full_time", "Full-time"),
        ("part_time", "Part-time"),
        ("contract", "Contract"),
        ("internship", "Internship"),
        ("freelance", "Freelance"),
        ("temporary", "Temporary"),
        ("other", "Other"),
    ]

    resume = models.ForeignKey(
        Resume,
        on_delete=models.CASCADE,
        related_name="experiences",
    )
    title = models.CharField(max_length=200)
    company = models.ForeignKey(
        Company,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="resume_experiences",
    )
    company_name = models.CharField(max_length=200, blank=True)
    location = models.CharField(max_length=120, blank=True)
    employment_type = models.CharField(
        max_length=20,
        choices=EMPLOYMENT_TYPES,
        blank=True,
    )
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    is_current = models.BooleanField(default=False)
    description = models.TextField(blank=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def display_company(self) -> str:
        if self.company:
            return self.company.name
        return self.company_name

    def __str__(self) -> str:
        return f"{self.title} at {self.display_company() or 'Unknown'}"


class ResumeEducation(models.Model):
    resume = models.ForeignKey(
        Resume,
        on_delete=models.CASCADE,
        related_name="educations",
    )
    school = models.CharField(max_length=200)
    degree = models.CharField(max_length=120, blank=True)
    field_of_study = models.CharField(max_length=120, blank=True)
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    grade = models.CharField(max_length=50, blank=True)
    activities = models.TextField(blank=True)
    description = models.TextField(blank=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return f"{self.school} - {self.degree}".strip(" -")


class ResumeSkill(models.Model):
    PROFICIENCY_CHOICES = [
        ("beginner", "Beginner"),
        ("intermediate", "Intermediate"),
        ("advanced", "Advanced"),
        ("expert", "Expert"),
    ]

    resume = models.ForeignKey(
        Resume,
        on_delete=models.CASCADE,
        related_name="skills",
    )
    name = models.CharField(max_length=120)
    proficiency = models.CharField(
        max_length=20,
        choices=PROFICIENCY_CHOICES,
        blank=True,
    )
    years_of_experience = models.PositiveSmallIntegerField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return self.name


class ResumeProject(models.Model):
    resume = models.ForeignKey(
        Resume,
        on_delete=models.CASCADE,
        related_name="projects",
    )
    name = models.CharField(max_length=200)
    role = models.CharField(max_length=120, blank=True)
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    url = models.URLField(blank=True)
    description = models.TextField(blank=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return self.name


class ResumeCertification(models.Model):
    resume = models.ForeignKey(
        Resume,
        on_delete=models.CASCADE,
        related_name="certifications",
    )
    name = models.CharField(max_length=200)
    issuer = models.CharField(max_length=200, blank=True)
    issue_date = models.DateField(null=True, blank=True)
    expiration_date = models.DateField(null=True, blank=True)
    credential_id = models.CharField(max_length=120, blank=True)
    credential_url = models.URLField(blank=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return self.name


class UserSkill(models.Model):
    PROFICIENCY_CHOICES = [
        ("beginner", "Beginner"),
        ("intermediate", "Intermediate"),
        ("advanced", "Advanced"),
        ("expert", "Expert"),
    ]

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="skills_profile",
    )
    name = models.CharField(max_length=120)
    proficiency = models.CharField(
        max_length=20,
        choices=PROFICIENCY_CHOICES,
        blank=True,
    )
    years_of_experience = models.PositiveSmallIntegerField(null=True, blank=True)
    description = models.TextField(blank=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("user", "name")

    def __str__(self) -> str:
        return self.name


class ApiToken(models.Model):
    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name="api_token",
    )
    name = models.CharField(max_length=120, blank=True)
    token_hash = models.CharField(max_length=256)
    prefix = models.CharField(max_length=12)
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    @property
    def is_active(self) -> bool:
        return self.revoked_at is None

    def __str__(self) -> str:
        label = self.name or self.prefix
        return f"{label} ({self.user})"
