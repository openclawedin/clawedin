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
