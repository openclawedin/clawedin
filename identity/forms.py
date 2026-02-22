from django import forms
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm

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


class LoginForm(AuthenticationForm):
    username = forms.CharField(
        max_length=150,
        widget=forms.TextInput(attrs={"autofocus": True}),
    )


class RegisterForm(UserCreationForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["email"].required = True

    class Meta(UserCreationForm.Meta):
        model = User
        fields = (
            "username",
            "email",
            "display_name",
            "account_type",
            "user_agent",
            "password1",
            "password2",
        )


class ProfileUpdateForm(forms.ModelForm):
    class Meta:
        model = User
        fields = (
            "display_name",
            "email",
            "account_type",
            "user_agent",
            "bio",
            "location",
            "website",
            "show_email",
            "show_location",
            "show_website",
            "show_bio",
            "show_user_agent",
            "show_skills",
            "show_resumes",
        )


class SolanaTransferForm(forms.Form):
    mint_address = forms.CharField(
        max_length=64,
        label="Token mint address",
        help_text="Solana token mint (contract) address.",
    )
    recipient = forms.CharField(max_length=64)
    amount = forms.DecimalField(max_digits=20, decimal_places=9, min_value=0)


class AgentLaunchForm(forms.Form):
    openai_api_key = forms.CharField(
        label="OpenAI API key",
        required=False,
        widget=forms.PasswordInput(render_value=False),
        help_text="Stored on your profile and injected into the agent container.",
    )


class ResumeForm(forms.ModelForm):
    class Meta:
        model = Resume
        fields = (
            "title",
            "headline",
            "summary",
            "phone",
            "email",
            "website",
            "location",
        )


class ResumeExperienceForm(forms.ModelForm):
    class Meta:
        model = ResumeExperience
        fields = (
            "title",
            "company",
            "company_name",
            "location",
            "employment_type",
            "start_date",
            "end_date",
            "is_current",
            "description",
        )

    def clean(self):
        cleaned_data = super().clean()
        company = cleaned_data.get("company")
        company_name = cleaned_data.get("company_name")
        if not company and not company_name:
            self.add_error(
                "company_name",
                "Select a company or enter a company name.",
            )
        return cleaned_data


class ResumeEducationForm(forms.ModelForm):
    class Meta:
        model = ResumeEducation
        fields = (
            "school",
            "degree",
            "field_of_study",
            "start_date",
            "end_date",
            "grade",
            "activities",
            "description",
        )


class ResumeSkillForm(forms.ModelForm):
    class Meta:
        model = ResumeSkill
        fields = (
            "name",
            "proficiency",
            "years_of_experience",
        )


class ResumeProjectForm(forms.ModelForm):
    class Meta:
        model = ResumeProject
        fields = (
            "name",
            "role",
            "start_date",
            "end_date",
            "url",
            "description",
        )


class ResumeCertificationForm(forms.ModelForm):
    class Meta:
        model = ResumeCertification
        fields = (
            "name",
            "issuer",
            "issue_date",
            "expiration_date",
            "credential_id",
            "credential_url",
        )


class UserSkillForm(forms.ModelForm):
    class Meta:
        model = UserSkill
        fields = (
            "name",
            "proficiency",
            "years_of_experience",
            "description",
        )
