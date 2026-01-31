from django import forms
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm

from .models import User


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
