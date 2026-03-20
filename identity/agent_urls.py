from django.urls import path

from . import views


urlpatterns = [
    path("login/", views.UserLoginView.as_view(), name="agent_login"),
    path("logout/", views.UserLogoutView.as_view(), name="agent_logout"),
    path("register/", views.register, name="agent_register"),
]
