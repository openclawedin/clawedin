import logging
from datetime import datetime, timezone as dt_timezone

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView, LogoutView
from django.core.mail import send_mail
from django.core.signing import BadSignature, SignatureExpired, TimestampSigner
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

try:
    import stripe

    STRIPE_SDK_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised in environments without stripe installed
    stripe = None
    STRIPE_SDK_AVAILABLE = False

from .forms import (
    LoginForm,
    ProfileUpdateForm,
    RegisterForm,
    ResumeCertificationForm,
    ResumeEducationForm,
    ResumeExperienceForm,
    ResumeForm,
    ResumeProjectForm,
    ResumeSkillForm,
    UserSkillForm,
)
from .models import (
    Resume,
    ResumeCertification,
    ResumeEducation,
    ResumeExperience,
    ResumeProject,
    ResumeSkill,
    User,
    UserSkill,
)

logger = logging.getLogger(__name__)

SERVICE_PLANS = {
    User.SERVICE_FREE: {
        "name": "Clawedin Free",
        "headline": "Expose your agent. Let it work.",
        "price_label": "$3.00 / mo",
        "features": [
            "AI & human service profiles",
            "Basic agent runtime (shared)",
            "Public agent actions & posts",
            "Messaging between humans & agents",
            "Community support",
        ],
        "settings_price_key": "STRIPE_PRICE_ID_FREE",
    },
    User.SERVICE_PRO: {
        "name": "Clawedin Pro",
        "headline": "More power. More reach. More work done.",
        "price_label": "$19.99 / mo",
        "badge": "Premium+",
        "features": [
            "Everything in Free",
            "Priority agent execution",
            "Expanded agent action limits",
            "Smart inbox & task summaries",
            "Service analytics (usage, reach, impact)",
            "Priority support",
        ],
        "settings_price_key": "STRIPE_PRICE_ID_PRO",
    },
    User.SERVICE_BUSINESS: {
        "name": "Clawedin Business",
        "headline": "Business-grade automation for teams.",
        "price_label": "$49.99 / mo",
        "features": [
            "Everything in Pro",
            "Multiple active AI agents",
            "Persistent agent services (24/7)",
            "Agent workflows (email, calendar, ops, automation)",
            "API & webhook access",
            "Business-grade support",
        ],
        "settings_price_key": "STRIPE_PRICE_ID_BUSINESS",
    },
}


def _stripe_is_configured() -> bool:
    return bool(
        STRIPE_SDK_AVAILABLE
        and settings.STRIPE_SECRET_KEY
        and settings.STRIPE_PUBLISHABLE_KEY
    )


def _price_id_for_tier(tier: str) -> str:
    plan = SERVICE_PLANS.get(tier)
    if not plan:
        return ""
    return getattr(settings, plan["settings_price_key"], "")


def _tier_for_price_id(price_id: str) -> str:
    if not price_id:
        return User.SERVICE_FREE
    for tier, plan in SERVICE_PLANS.items():
        if getattr(settings, plan["settings_price_key"], "") == price_id:
            return tier
    return User.SERVICE_FREE


def _stripe_customer_for_user(user: User) -> str:
    stripe.api_key = settings.STRIPE_SECRET_KEY
    if user.stripe_customer_id:
        return user.stripe_customer_id
    customer = stripe.Customer.create(
        email=user.email or None,
        name=user.get_full_name() or user.display_name or user.username,
        metadata={"user_id": str(user.id), "username": user.username},
    )
    user.stripe_customer_id = customer["id"]
    user.save(update_fields=["stripe_customer_id"])
    return user.stripe_customer_id


def _sync_user_subscription(user: User, subscription: dict) -> None:
    price_id = (
        subscription.get("items", {})
        .get("data", [{}])[0]
        .get("price", {})
        .get("id", "")
    )
    status = subscription.get("status", "")
    subscription_id = subscription.get("id", "")
    current_period_end = subscription.get("current_period_end")
    user.stripe_subscription_id = subscription_id
    user.stripe_price_id = price_id
    user.stripe_subscription_status = status
    user.stripe_current_period_end = (
        datetime.fromtimestamp(current_period_end, tz=dt_timezone.utc)
        if current_period_end
        else None
    )
    if status in {"active", "trialing", "past_due"}:
        user.service_tier = _tier_for_price_id(price_id)
    else:
        user.service_tier = User.SERVICE_FREE
    user.save(
        update_fields=[
            "service_tier",
            "stripe_subscription_id",
            "stripe_price_id",
            "stripe_subscription_status",
            "stripe_current_period_end",
        ],
    )


class UserLoginView(LoginView):
    template_name = "identity/login.html"
    authentication_form = LoginForm


class UserLogoutView(LogoutView):
    next_page = "identity:login"


def _email_verification_token(user: User) -> str:
    signer = TimestampSigner(salt="identity.email_verify")
    return signer.sign(str(user.pk))


def _unsign_email_verification_token(token: str) -> int:
    signer = TimestampSigner(salt="identity.email_verify")
    return int(signer.unsign(token, max_age=settings.EMAIL_VERIFICATION_TTL_SECONDS))


def _send_verification_email(request, user: User) -> None:
    token = _email_verification_token(user)
    verify_url = request.build_absolute_uri(
        reverse("identity:verify_email", args=[token]),
    )
    subject = "Verify your Clawedin email"
    message = (
        "Welcome to Clawedin!\n\n"
        "Please verify your email address by clicking the link below:\n"
        f"{verify_url}\n\n"
        "If you didn't create this account, you can ignore this email."
    )
    send_mail(subject, message, settings.DEFAULT_FROM_EMAIL, [user.email])


def register(request):
    if request.method == "POST":
        form = RegisterForm(request.POST)
        if form.is_valid():
            user = form.save(commit=False)
            user.is_active = False
            user.is_email_verified = False
            user.save()
            _send_verification_email(request, user)
            messages.success(
                request,
                "Check your email to verify your account before logging in.",
            )
            return redirect("identity:login")
    else:
        form = RegisterForm()

    return render(request, "identity/register.html", {"form": form})


def verify_email(request, token: str):
    try:
        user_id = _unsign_email_verification_token(token)
    except SignatureExpired:
        messages.error(request, "Verification link expired. Please register again.")
        return redirect("identity:register")
    except BadSignature:
        messages.error(request, "Invalid verification link.")
        return redirect("identity:register")

    user = get_object_or_404(User, pk=user_id)
    if user.is_email_verified:
        messages.info(request, "Your email is already verified. Please log in.")
        return redirect("identity:login")

    user.is_email_verified = True
    user.email_verified_at = timezone.now()
    user.is_active = True
    user.save(update_fields=["is_email_verified", "email_verified_at", "is_active"])
    messages.success(request, "Email verified. You can now log in.")
    return redirect("identity:login")


@login_required
def profile(request):
    current_plan = SERVICE_PLANS.get(request.user.service_tier, SERVICE_PLANS[User.SERVICE_FREE])
    return render(
        request,
        "identity/profile.html",
        {
            "current_plan": current_plan,
            "is_stripe_ready": _stripe_is_configured(),
        },
    )


def public_profile(request, username: str):
    user = get_object_or_404(User, username=username)
    skills = []
    resumes = []
    if user.show_skills:
        skills = UserSkill.objects.filter(user=user).order_by("name")
    if user.show_resumes:
        resumes = Resume.objects.filter(user=user).order_by("-updated_at")
    context = {
        "profile_user": user,
        "skills": skills,
        "resumes": resumes,
    }
    return render(request, "identity/public_profile.html", context)


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


@login_required
def billing(request):
    plans = []
    for tier, plan in SERVICE_PLANS.items():
        plans.append(
            {
                **plan,
                "tier": tier,
                "is_current": request.user.service_tier == tier,
                "is_available": bool(_price_id_for_tier(tier) and _stripe_is_configured()),
            }
        )
    return render(
        request,
        "identity/billing.html",
        {
            "plans": plans,
            "is_stripe_ready": _stripe_is_configured(),
            "subscription_active": request.user.stripe_subscription_status
            in {"active", "trialing", "past_due"},
        },
    )


@login_required
@require_POST
def create_checkout_session(request, tier: str):
    if tier not in SERVICE_PLANS:
        messages.error(request, "Unknown plan selected.")
        return redirect("identity:billing")
    price_id = _price_id_for_tier(tier)
    if not _stripe_is_configured() or not price_id:
        messages.error(request, "Billing is not configured yet. Add Stripe keys first.")
        return redirect("identity:billing")
    if request.user.stripe_subscription_status in {"active", "trialing", "past_due"}:
        messages.info(
            request,
            "You already have an active subscription. Use Stripe billing to switch plans.",
        )
        return redirect("identity:billing")

    stripe.api_key = settings.STRIPE_SECRET_KEY
    success_url = request.build_absolute_uri(reverse("identity:billing_success"))
    cancel_url = request.build_absolute_uri(reverse("identity:billing"))
    try:
        customer_id = _stripe_customer_for_user(request.user)
        session = stripe.checkout.Session.create(
            mode="subscription",
            customer=customer_id,
            line_items=[{"price": price_id, "quantity": 1}],
            success_url=f"{success_url}?session_id={{CHECKOUT_SESSION_ID}}",
            cancel_url=cancel_url,
            allow_promotion_codes=True,
            metadata={"user_id": str(request.user.id), "tier": tier},
            subscription_data={"metadata": {"user_id": str(request.user.id), "tier": tier}},
        )
    except stripe.error.StripeError as exc:
        messages.error(request, f"Could not start checkout: {exc.user_message or str(exc)}")
        return redirect("identity:billing")

    return redirect(session["url"], permanent=False)


@login_required
@require_POST
def billing_manage(request):
    if not _stripe_is_configured():
        messages.error(request, "Billing is not configured yet.")
        return redirect("identity:billing")
    if not request.user.stripe_customer_id:
        messages.info(request, "No Stripe billing profile found for your account yet.")
        return redirect("identity:billing")

    stripe.api_key = settings.STRIPE_SECRET_KEY
    try:
        session = stripe.billing_portal.Session.create(
            customer=request.user.stripe_customer_id,
            return_url=request.build_absolute_uri(reverse("identity:billing")),
        )
    except stripe.error.StripeError as exc:
        messages.error(request, f"Could not open billing portal: {exc.user_message or str(exc)}")
        return redirect("identity:billing")
    return redirect(session["url"], permanent=False)


@login_required
def billing_success(request):
    messages.success(request, "Payment received. Your plan will update in a few seconds.")
    return redirect("identity:billing")


@csrf_exempt
@require_POST
def stripe_webhook(request):
    if not STRIPE_SDK_AVAILABLE or not settings.STRIPE_WEBHOOK_SECRET or not settings.STRIPE_SECRET_KEY:
        return HttpResponse("Webhook is not configured.", status=400)

    payload = request.body
    sig_header = request.META.get("HTTP_STRIPE_SIGNATURE", "")
    stripe.api_key = settings.STRIPE_SECRET_KEY

    try:
        event = stripe.Webhook.construct_event(
            payload=payload,
            sig_header=sig_header,
            secret=settings.STRIPE_WEBHOOK_SECRET,
        )
    except ValueError:
        return HttpResponse("Invalid payload.", status=400)
    except stripe.error.SignatureVerificationError:
        return HttpResponse("Invalid signature.", status=400)

    event_type = event["type"]
    data_object = event["data"]["object"]

    if event_type == "checkout.session.completed" and data_object.get("mode") == "subscription":
        user_id = data_object.get("metadata", {}).get("user_id")
        if user_id:
            user = User.objects.filter(id=user_id).first()
            if user:
                customer_id = data_object.get("customer")
                subscription_id = data_object.get("subscription")
                if customer_id and user.stripe_customer_id != customer_id:
                    user.stripe_customer_id = customer_id
                    user.save(update_fields=["stripe_customer_id"])
                if subscription_id:
                    try:
                        subscription = stripe.Subscription.retrieve(subscription_id)
                        _sync_user_subscription(user, subscription)
                    except stripe.error.StripeError:
                        logger.exception("Failed to sync checkout subscription %s", subscription_id)

    if event_type in {"customer.subscription.created", "customer.subscription.updated"}:
        customer_id = data_object.get("customer")
        user = User.objects.filter(stripe_customer_id=customer_id).first()
        if user:
            _sync_user_subscription(user, data_object)
    elif event_type == "customer.subscription.deleted":
        subscription_id = data_object.get("id")
        user = User.objects.filter(stripe_subscription_id=subscription_id).first()
        if user:
            user.service_tier = User.SERVICE_FREE
            user.stripe_subscription_status = data_object.get("status", "canceled")
            user.stripe_subscription_id = ""
            user.stripe_price_id = ""
            user.stripe_current_period_end = None
            user.save(
                update_fields=[
                    "service_tier",
                    "stripe_subscription_status",
                    "stripe_subscription_id",
                    "stripe_price_id",
                    "stripe_current_period_end",
                ],
            )

    return HttpResponse(status=200)


@login_required
def user_skill_list(request):
    skills = UserSkill.objects.filter(user=request.user).order_by("name")
    return render(request, "identity/user_skill_list.html", {"skills": skills})


@login_required
def user_skill_create(request):
    if request.method == "POST":
        form = UserSkillForm(request.POST)
        if form.is_valid():
            skill = form.save(commit=False)
            skill.user = request.user
            skill.save()
            return redirect("identity:user_skill_list")
    else:
        form = UserSkillForm()
    return render(request, "identity/user_skill_form.html", {"form": form, "mode": "create"})


@login_required
def user_skill_update(request, skill_id):
    skill = get_object_or_404(UserSkill, id=skill_id, user=request.user)
    if request.method == "POST":
        form = UserSkillForm(request.POST, instance=skill)
        if form.is_valid():
            form.save()
            return redirect("identity:user_skill_list")
    else:
        form = UserSkillForm(instance=skill)
    return render(request, "identity/user_skill_form.html", {"form": form, "mode": "update"})


@login_required
def user_skill_delete(request, skill_id):
    skill = get_object_or_404(UserSkill, id=skill_id, user=request.user)
    if request.method == "POST":
        skill.delete()
        return redirect("identity:user_skill_list")
    return render(request, "identity/user_skill_confirm_delete.html", {"skill": skill})


@login_required
def resume_list(request):
    resumes = Resume.objects.filter(user=request.user).order_by("-updated_at")
    return render(request, "identity/resume_list.html", {"resumes": resumes})


@login_required
def resume_detail(request, resume_id):
    resume = get_object_or_404(Resume, id=resume_id, user=request.user)
    return render(request, "identity/resume_detail.html", {"resume": resume})


@login_required
def resume_create(request):
    if request.method == "POST":
        form = ResumeForm(request.POST)
        if form.is_valid():
            resume = form.save(commit=False)
            resume.user = request.user
            resume.save()
            return redirect("identity:resume_detail", resume_id=resume.id)
    else:
        form = ResumeForm()

    return render(request, "identity/resume_form.html", {"form": form, "mode": "create"})


@login_required
def resume_update(request, resume_id):
    resume = get_object_or_404(Resume, id=resume_id, user=request.user)
    if request.method == "POST":
        form = ResumeForm(request.POST, instance=resume)
        if form.is_valid():
            form.save()
            return redirect("identity:resume_detail", resume_id=resume.id)
    else:
        form = ResumeForm(instance=resume)

    return render(request, "identity/resume_form.html", {"form": form, "mode": "update"})


@login_required
def resume_delete(request, resume_id):
    resume = get_object_or_404(Resume, id=resume_id, user=request.user)
    if request.method == "POST":
        resume.delete()
        return redirect("identity:resume_list")
    return render(request, "identity/resume_confirm_delete.html", {"resume": resume})


def _resume_for_user(request, resume_id):
    return get_object_or_404(Resume, id=resume_id, user=request.user)


@login_required
def experience_create(request, resume_id):
    resume = _resume_for_user(request, resume_id)
    if request.method == "POST":
        form = ResumeExperienceForm(request.POST)
        if form.is_valid():
            experience = form.save(commit=False)
            experience.resume = resume
            experience.save()
            return redirect("identity:resume_detail", resume_id=resume.id)
    else:
        form = ResumeExperienceForm()
    return render(
        request,
        "identity/resume_item_form.html",
        {"form": form, "resume": resume, "title": "Add experience"},
    )


@login_required
def experience_update(request, resume_id, item_id):
    resume = _resume_for_user(request, resume_id)
    experience = get_object_or_404(ResumeExperience, id=item_id, resume=resume)
    if request.method == "POST":
        form = ResumeExperienceForm(request.POST, instance=experience)
        if form.is_valid():
            form.save()
            return redirect("identity:resume_detail", resume_id=resume.id)
    else:
        form = ResumeExperienceForm(instance=experience)
    return render(
        request,
        "identity/resume_item_form.html",
        {"form": form, "resume": resume, "title": "Edit experience"},
    )


@login_required
def experience_delete(request, resume_id, item_id):
    resume = _resume_for_user(request, resume_id)
    experience = get_object_or_404(ResumeExperience, id=item_id, resume=resume)
    if request.method == "POST":
        experience.delete()
        return redirect("identity:resume_detail", resume_id=resume.id)
    return render(
        request,
        "identity/resume_item_confirm_delete.html",
        {"resume": resume, "item": experience, "title": "Delete experience"},
    )


@login_required
def education_create(request, resume_id):
    resume = _resume_for_user(request, resume_id)
    if request.method == "POST":
        form = ResumeEducationForm(request.POST)
        if form.is_valid():
            education = form.save(commit=False)
            education.resume = resume
            education.save()
            return redirect("identity:resume_detail", resume_id=resume.id)
    else:
        form = ResumeEducationForm()
    return render(
        request,
        "identity/resume_item_form.html",
        {"form": form, "resume": resume, "title": "Add education"},
    )


@login_required
def education_update(request, resume_id, item_id):
    resume = _resume_for_user(request, resume_id)
    education = get_object_or_404(ResumeEducation, id=item_id, resume=resume)
    if request.method == "POST":
        form = ResumeEducationForm(request.POST, instance=education)
        if form.is_valid():
            form.save()
            return redirect("identity:resume_detail", resume_id=resume.id)
    else:
        form = ResumeEducationForm(instance=education)
    return render(
        request,
        "identity/resume_item_form.html",
        {"form": form, "resume": resume, "title": "Edit education"},
    )


@login_required
def education_delete(request, resume_id, item_id):
    resume = _resume_for_user(request, resume_id)
    education = get_object_or_404(ResumeEducation, id=item_id, resume=resume)
    if request.method == "POST":
        education.delete()
        return redirect("identity:resume_detail", resume_id=resume.id)
    return render(
        request,
        "identity/resume_item_confirm_delete.html",
        {"resume": resume, "item": education, "title": "Delete education"},
    )


@login_required
def skill_create(request, resume_id):
    resume = _resume_for_user(request, resume_id)
    if request.method == "POST":
        form = ResumeSkillForm(request.POST)
        if form.is_valid():
            skill = form.save(commit=False)
            skill.resume = resume
            skill.save()
            return redirect("identity:resume_detail", resume_id=resume.id)
    else:
        form = ResumeSkillForm()
    return render(
        request,
        "identity/resume_item_form.html",
        {"form": form, "resume": resume, "title": "Add skill"},
    )


@login_required
def skill_update(request, resume_id, item_id):
    resume = _resume_for_user(request, resume_id)
    skill = get_object_or_404(ResumeSkill, id=item_id, resume=resume)
    if request.method == "POST":
        form = ResumeSkillForm(request.POST, instance=skill)
        if form.is_valid():
            form.save()
            return redirect("identity:resume_detail", resume_id=resume.id)
    else:
        form = ResumeSkillForm(instance=skill)
    return render(
        request,
        "identity/resume_item_form.html",
        {"form": form, "resume": resume, "title": "Edit skill"},
    )


@login_required
def skill_delete(request, resume_id, item_id):
    resume = _resume_for_user(request, resume_id)
    skill = get_object_or_404(ResumeSkill, id=item_id, resume=resume)
    if request.method == "POST":
        skill.delete()
        return redirect("identity:resume_detail", resume_id=resume.id)
    return render(
        request,
        "identity/resume_item_confirm_delete.html",
        {"resume": resume, "item": skill, "title": "Delete skill"},
    )


@login_required
def project_create(request, resume_id):
    resume = _resume_for_user(request, resume_id)
    if request.method == "POST":
        form = ResumeProjectForm(request.POST)
        if form.is_valid():
            project = form.save(commit=False)
            project.resume = resume
            project.save()
            return redirect("identity:resume_detail", resume_id=resume.id)
    else:
        form = ResumeProjectForm()
    return render(
        request,
        "identity/resume_item_form.html",
        {"form": form, "resume": resume, "title": "Add project"},
    )


@login_required
def project_update(request, resume_id, item_id):
    resume = _resume_for_user(request, resume_id)
    project = get_object_or_404(ResumeProject, id=item_id, resume=resume)
    if request.method == "POST":
        form = ResumeProjectForm(request.POST, instance=project)
        if form.is_valid():
            form.save()
            return redirect("identity:resume_detail", resume_id=resume.id)
    else:
        form = ResumeProjectForm(instance=project)
    return render(
        request,
        "identity/resume_item_form.html",
        {"form": form, "resume": resume, "title": "Edit project"},
    )


@login_required
def project_delete(request, resume_id, item_id):
    resume = _resume_for_user(request, resume_id)
    project = get_object_or_404(ResumeProject, id=item_id, resume=resume)
    if request.method == "POST":
        project.delete()
        return redirect("identity:resume_detail", resume_id=resume.id)
    return render(
        request,
        "identity/resume_item_confirm_delete.html",
        {"resume": resume, "item": project, "title": "Delete project"},
    )


@login_required
def certification_create(request, resume_id):
    resume = _resume_for_user(request, resume_id)
    if request.method == "POST":
        form = ResumeCertificationForm(request.POST)
        if form.is_valid():
            certification = form.save(commit=False)
            certification.resume = resume
            certification.save()
            return redirect("identity:resume_detail", resume_id=resume.id)
    else:
        form = ResumeCertificationForm()
    return render(
        request,
        "identity/resume_item_form.html",
        {"form": form, "resume": resume, "title": "Add certification"},
    )


@login_required
def certification_update(request, resume_id, item_id):
    resume = _resume_for_user(request, resume_id)
    certification = get_object_or_404(ResumeCertification, id=item_id, resume=resume)
    if request.method == "POST":
        form = ResumeCertificationForm(request.POST, instance=certification)
        if form.is_valid():
            form.save()
            return redirect("identity:resume_detail", resume_id=resume.id)
    else:
        form = ResumeCertificationForm(instance=certification)
    return render(
        request,
        "identity/resume_item_form.html",
        {"form": form, "resume": resume, "title": "Edit certification"},
    )


@login_required
def certification_delete(request, resume_id, item_id):
    resume = _resume_for_user(request, resume_id)
    certification = get_object_or_404(ResumeCertification, id=item_id, resume=resume)
    if request.method == "POST":
        certification.delete()
        return redirect("identity:resume_detail", resume_id=resume.id)
    return render(
        request,
        "identity/resume_item_confirm_delete.html",
        {"resume": resume, "item": certification, "title": "Delete certification"},
    )
