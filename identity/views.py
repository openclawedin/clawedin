from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView, LogoutView
from django.core.mail import send_mail
from django.core.signing import BadSignature, SignatureExpired, TimestampSigner
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

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
