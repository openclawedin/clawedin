from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView, LogoutView
from django.shortcuts import redirect, render

from .forms import LoginForm, ProfileUpdateForm, RegisterForm


class UserLoginView(LoginView):
    template_name = "identity/login.html"
    authentication_form = LoginForm


class UserLogoutView(LogoutView):
    next_page = "identity:login"


def register(request):
    if request.method == "POST":
        form = RegisterForm(request.POST)
        if form.is_valid():
            user = form.save()
            login(request, user)
            return redirect("identity:profile")
    else:
        form = RegisterForm()

    return render(request, "identity/register.html", {"form": form})


@login_required
def profile(request):
    return render(request, "identity/profile.html")


@login_required
def profile_update(request):
    if request.method == "POST":
        form = ProfileUpdateForm(request.POST, instance=request.user)
        if form.is_valid():
            form.save()
            return redirect("identity:profile")
    else:
        form = ProfileUpdateForm(instance=request.user)

    return render(request, "identity/profile_update.html", {"form": form})
