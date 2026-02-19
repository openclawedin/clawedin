from django.urls import path, re_path

from . import views

app_name = "identity"

urlpatterns = [
    re_path(r"^(openclaw-agent-[a-z0-9-]+)/$", views.agent_gui_root_redirect, name="agent_gui_root_redirect"),
    re_path(
        r"^(openclaw-agent-[a-z0-9-]+)/(?P<subpath>.+)$",
        views.agent_gui_root_redirect,
        name="agent_gui_root_redirect_subpath",
    ),
    path("login/", views.UserLoginView.as_view(), name="login"),
    path("logout/", views.UserLogoutView.as_view(), name="logout"),
    path("register/", views.register, name="register"),
    path("verify-email/<str:token>/", views.verify_email, name="verify_email"),
    path("u/<str:username>/", views.public_profile, name="public_profile"),
    path("profile/", views.profile, name="profile"),
    path("agents/manager/", views.agent_manager, name="agent_manager"),
    path("agents/manager/<str:pod_name>/", views.agent_detail, name="agent_detail"),
    path(
        "agents/manager/<str:pod_name>/terminal/",
        views.agent_terminal,
        name="agent_terminal",
    ),
    path(
        "agents/manager/<str:pod_name>/gui/",
        views.agent_gui,
        name="agent_gui",
    ),
    path(
        "agents/gui/<str:pod_name>/",
        views.agent_gui_proxy,
        name="agent_gui_proxy",
    ),
    path(
        "agents/gui/<str:pod_name>/<path:subpath>",
        views.agent_gui_proxy,
        name="agent_gui_proxy_subpath",
    ),
    path(
        "admin/deployed-agents/",
        views.deployed_agents,
        name="deployed_agents",
    ),
    path("profile/edit/", views.profile_update, name="profile_update"),
    path(
        "profile/solana-wallet/create/",
        views.solana_wallet_create,
        name="solana_wallet_create",
    ),
    path(
        "profile/solana-wallet/regenerate/",
        views.solana_wallet_regenerate,
        name="solana_wallet_regenerate",
    ),
    path(
        "profile/solana-wallet/transfer/",
        views.solana_transfer,
        name="solana_transfer",
    ),
    path("profile/billing/", views.billing, name="billing"),
    path(
        "profile/billing/checkout/<str:tier>/",
        views.create_checkout_session,
        name="checkout_session",
    ),
    path("profile/billing/manage/", views.billing_manage, name="billing_manage"),
    path("profile/billing/success/", views.billing_success, name="billing_success"),
    path("stripe/webhook/", views.stripe_webhook, name="stripe_webhook"),
    path("profile/skills/", views.user_skill_list, name="user_skill_list"),
    path("profile/skills/new/", views.user_skill_create, name="user_skill_create"),
    path(
        "profile/skills/<int:skill_id>/edit/",
        views.user_skill_update,
        name="user_skill_update",
    ),
    path(
        "profile/skills/<int:skill_id>/delete/",
        views.user_skill_delete,
        name="user_skill_delete",
    ),
    path("resumes/", views.resume_list, name="resume_list"),
    path("resumes/new/", views.resume_create, name="resume_create"),
    path("resumes/<int:resume_id>/", views.resume_detail, name="resume_detail"),
    path("resumes/<int:resume_id>/edit/", views.resume_update, name="resume_update"),
    path("resumes/<int:resume_id>/delete/", views.resume_delete, name="resume_delete"),
    path(
        "resumes/<int:resume_id>/experiences/new/",
        views.experience_create,
        name="experience_create",
    ),
    path(
        "resumes/<int:resume_id>/experiences/<int:item_id>/edit/",
        views.experience_update,
        name="experience_update",
    ),
    path(
        "resumes/<int:resume_id>/experiences/<int:item_id>/delete/",
        views.experience_delete,
        name="experience_delete",
    ),
    path(
        "resumes/<int:resume_id>/education/new/",
        views.education_create,
        name="education_create",
    ),
    path(
        "resumes/<int:resume_id>/education/<int:item_id>/edit/",
        views.education_update,
        name="education_update",
    ),
    path(
        "resumes/<int:resume_id>/education/<int:item_id>/delete/",
        views.education_delete,
        name="education_delete",
    ),
    path(
        "resumes/<int:resume_id>/skills/new/",
        views.skill_create,
        name="skill_create",
    ),
    path(
        "resumes/<int:resume_id>/skills/<int:item_id>/edit/",
        views.skill_update,
        name="skill_update",
    ),
    path(
        "resumes/<int:resume_id>/skills/<int:item_id>/delete/",
        views.skill_delete,
        name="skill_delete",
    ),
    path(
        "resumes/<int:resume_id>/projects/new/",
        views.project_create,
        name="project_create",
    ),
    path(
        "resumes/<int:resume_id>/projects/<int:item_id>/edit/",
        views.project_update,
        name="project_update",
    ),
    path(
        "resumes/<int:resume_id>/projects/<int:item_id>/delete/",
        views.project_delete,
        name="project_delete",
    ),
    path(
        "resumes/<int:resume_id>/certifications/new/",
        views.certification_create,
        name="certification_create",
    ),
    path(
        "resumes/<int:resume_id>/certifications/<int:item_id>/edit/",
        views.certification_update,
        name="certification_update",
    ),
    path(
        "resumes/<int:resume_id>/certifications/<int:item_id>/delete/",
        views.certification_delete,
        name="certification_delete",
    ),
]
