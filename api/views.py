import json

from django.http import HttpResponseNotAllowed, JsonResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.crypto import constant_time_compare

from companies.models import Company
from content.models import Post
from identity.auth import hash_token
from identity.models import ApiToken, Resume, UserSkill


def _json_success(data=None, status=200):
    payload = {"success": True}
    if data is not None:
        payload["data"] = data
    return JsonResponse(payload, status=status)


def _json_error(message, status=400, hint=None):
    payload = {"success": False, "error": message}
    if hint:
        payload["hint"] = hint
    return JsonResponse(payload, status=status)


def _parse_json(request):
    if not request.body:
        return {}
    try:
        return json.loads(request.body.decode("utf-8"))
    except json.JSONDecodeError:
        return None


def _csrf_ok(request):
    cookie = request.COOKIES.get("csrftoken")
    header = request.META.get("HTTP_X_CSRFTOKEN")
    if not cookie or not header:
        return False
    return constant_time_compare(cookie, header)


def _require_auth(request, allow_session=True, require_csrf_for_session=False):
    if getattr(request, "auth_token", None):
        return None
    if allow_session and request.user.is_authenticated:
        if require_csrf_for_session and not _csrf_ok(request):
            return _json_error("Missing or invalid CSRF token.", status=403)
        return None
    return _json_error("Authentication required.", status=401)


def _serialize_post(post):
    return {
        "id": post.id,
        "title": post.title,
        "body": post.body,
        "created_at": post.created_at,
        "updated_at": post.updated_at,
    }


def _serialize_company(company):
    return {
        "id": company.id,
        "name": company.name,
        "slug": company.slug,
        "tagline": company.tagline,
        "description": company.description,
        "website": company.website,
        "industry": company.industry,
        "company_type": company.company_type,
        "company_size": company.company_size,
        "headquarters": company.headquarters,
        "founded_year": company.founded_year,
        "specialties": company.specialties,
        "logo_url": company.logo_url,
        "cover_url": company.cover_url,
        "created_at": company.created_at,
        "updated_at": company.updated_at,
    }


def _serialize_user_skill(skill):
    return {
        "id": skill.id,
        "name": skill.name,
        "proficiency": skill.proficiency,
        "years_of_experience": skill.years_of_experience,
        "description": skill.description,
        "created_at": skill.created_at,
        "updated_at": skill.updated_at,
    }


def _serialize_resume(resume):
    return {
        "id": resume.id,
        "title": resume.title,
        "headline": resume.headline,
        "summary": resume.summary,
        "phone": resume.phone,
        "email": resume.email,
        "website": resume.website,
        "location": resume.location,
        "created_at": resume.created_at,
        "updated_at": resume.updated_at,
    }


def health(request):
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])
    return _json_success({"status": "ok"})


def me(request):
    if request.method == "GET":
        auth_error = _require_auth(request, allow_session=True)
        if auth_error:
            return auth_error
        user = request.user
        return _json_success(
            {
                "id": user.id,
                "username": user.username,
                "email": user.email,
                "display_name": user.display_name,
                "account_type": user.account_type,
                "user_agent": user.user_agent,
                "bio": user.bio,
                "location": user.location,
                "website": user.website,
                "created_at": user.created_at,
                "updated_at": user.updated_at,
            }
        )
    if request.method in {"PATCH", "PUT"}:
        auth_error = _require_auth(
            request, allow_session=True, require_csrf_for_session=True
        )
        if auth_error:
            return auth_error
        data = _parse_json(request)
        if data is None:
            return _json_error("Invalid JSON.")
        user = request.user
        allowed = {
            "display_name",
            "email",
            "account_type",
            "user_agent",
            "bio",
            "location",
            "website",
        }
        for key in allowed:
            if key in data:
                setattr(user, key, data[key])
        user.save()
        return _json_success({"updated": True})
    return HttpResponseNotAllowed(["GET", "PATCH", "PUT"])


def tokens(request):
    if request.method == "GET":
        auth_error = _require_auth(request, allow_session=True)
        if auth_error:
            return auth_error
        tokens_qs = ApiToken.objects.filter(user=request.user)
        data = [
            {
                "id": token.id,
                "name": token.name,
                "prefix": token.prefix,
                "created_at": token.created_at,
                "last_used_at": token.last_used_at,
                "revoked_at": token.revoked_at,
            }
            for token in tokens_qs
        ]
        return _json_success({"tokens": data})
    if request.method == "POST":
        auth_error = _require_auth(
            request, allow_session=True, require_csrf_for_session=True
        )
        if auth_error:
            return auth_error
        data = _parse_json(request)
        if data is None:
            return _json_error("Invalid JSON.")
        raw_token = data.get("token", "")
        if not isinstance(raw_token, str) or len(raw_token) < 32:
            return _json_error(
                "Token must be a string with at least 32 characters.",
                hint="Generate a long random token client-side and submit it here.",
            )
        token_hash = hash_token(raw_token)
        if ApiToken.objects.filter(token_hash=token_hash).exists():
            return _json_error("Token already exists.", status=409)
        api_token = ApiToken.objects.create(
            user=request.user,
            name=data.get("name", "") or "",
            token_hash=token_hash,
            prefix=raw_token[:8],
        )
        return _json_success(
            {
                "id": api_token.id,
                "name": api_token.name,
                "prefix": api_token.prefix,
                "created_at": api_token.created_at,
            },
            status=201,
        )
    return HttpResponseNotAllowed(["GET", "POST"])


def token_detail(request, token_id):
    if request.method != "DELETE":
        return HttpResponseNotAllowed(["DELETE"])
    auth_error = _require_auth(request, allow_session=True, require_csrf_for_session=True)
    if auth_error:
        return auth_error
    token = get_object_or_404(ApiToken, id=token_id, user=request.user)
    token.revoked_at = timezone.now()
    token.save(update_fields=["revoked_at"])
    return _json_success({"revoked": True})


def posts(request):
    if request.method == "GET":
        auth_error = _require_auth(request, allow_session=True)
        if auth_error:
            return auth_error
        posts_qs = Post.objects.filter(author=request.user).order_by("-updated_at")
        return _json_success({"posts": [_serialize_post(post) for post in posts_qs]})
    if request.method == "POST":
        auth_error = _require_auth(
            request, allow_session=True, require_csrf_for_session=True
        )
        if auth_error:
            return auth_error
        data = _parse_json(request)
        if data is None:
            return _json_error("Invalid JSON.")
        title = data.get("title", "").strip()
        body = data.get("body", "").strip()
        if not title or not body:
            return _json_error("Both title and body are required.")
        post = Post.objects.create(author=request.user, title=title, body=body)
        return _json_success({"post": _serialize_post(post)}, status=201)
    return HttpResponseNotAllowed(["GET", "POST"])


def post_detail(request, post_id):
    if request.method == "GET":
        auth_error = _require_auth(request, allow_session=True)
        if auth_error:
            return auth_error
        post = get_object_or_404(Post, id=post_id, author=request.user)
        return _json_success({"post": _serialize_post(post)})
    if request.method in {"PATCH", "PUT"}:
        auth_error = _require_auth(
            request, allow_session=True, require_csrf_for_session=True
        )
        if auth_error:
            return auth_error
        post = get_object_or_404(Post, id=post_id, author=request.user)
        data = _parse_json(request)
        if data is None:
            return _json_error("Invalid JSON.")
        if "title" in data:
            post.title = data["title"]
        if "body" in data:
            post.body = data["body"]
        post.save()
        return _json_success({"post": _serialize_post(post)})
    if request.method == "DELETE":
        auth_error = _require_auth(
            request, allow_session=True, require_csrf_for_session=True
        )
        if auth_error:
            return auth_error
        post = get_object_or_404(Post, id=post_id, author=request.user)
        post.delete()
        return _json_success({"deleted": True})
    return HttpResponseNotAllowed(["GET", "PATCH", "PUT", "DELETE"])


def companies(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    auth_error = _require_auth(request, allow_session=True, require_csrf_for_session=True)
    if auth_error:
        return auth_error
    data = _parse_json(request)
    if data is None:
        return _json_error("Invalid JSON.")
    name = data.get("name", "").strip()
    if not name:
        return _json_error("Company name is required.")
    company = Company.objects.create(
        owner=request.user,
        name=name,
        tagline=data.get("tagline", ""),
        description=data.get("description", ""),
        website=data.get("website", ""),
        industry=data.get("industry", ""),
        company_type=data.get("company_type", ""),
        company_size=data.get("company_size", ""),
        headquarters=data.get("headquarters", ""),
        founded_year=data.get("founded_year") or None,
        specialties=data.get("specialties", ""),
        logo_url=data.get("logo_url", ""),
        cover_url=data.get("cover_url", ""),
    )
    return _json_success({"company": _serialize_company(company)}, status=201)


def company_detail(request, slug):
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])
    company = get_object_or_404(Company, slug=slug)
    return _json_success({"company": _serialize_company(company)})


def user_skills(request):
    if request.method == "GET":
        auth_error = _require_auth(request, allow_session=True)
        if auth_error:
            return auth_error
        skills = UserSkill.objects.filter(user=request.user).order_by("name")
        return _json_success({"skills": [_serialize_user_skill(skill) for skill in skills]})
    if request.method == "POST":
        auth_error = _require_auth(
            request, allow_session=True, require_csrf_for_session=True
        )
        if auth_error:
            return auth_error
        data = _parse_json(request)
        if data is None:
            return _json_error("Invalid JSON.")
        name = data.get("name", "").strip()
        if not name:
            return _json_error("Skill name is required.")
        skill = UserSkill.objects.create(
            user=request.user,
            name=name,
            proficiency=data.get("proficiency", ""),
            years_of_experience=data.get("years_of_experience") or None,
            description=data.get("description", ""),
        )
        return _json_success({"skill": _serialize_user_skill(skill)}, status=201)
    return HttpResponseNotAllowed(["GET", "POST"])


def user_skill_detail(request, skill_id):
    if request.method == "GET":
        auth_error = _require_auth(request, allow_session=True)
        if auth_error:
            return auth_error
        skill = get_object_or_404(UserSkill, id=skill_id, user=request.user)
        return _json_success({"skill": _serialize_user_skill(skill)})
    if request.method in {"PATCH", "PUT"}:
        auth_error = _require_auth(
            request, allow_session=True, require_csrf_for_session=True
        )
        if auth_error:
            return auth_error
        skill = get_object_or_404(UserSkill, id=skill_id, user=request.user)
        data = _parse_json(request)
        if data is None:
            return _json_error("Invalid JSON.")
        for field in ("name", "proficiency", "years_of_experience", "description"):
            if field in data:
                setattr(skill, field, data[field])
        skill.save()
        return _json_success({"skill": _serialize_user_skill(skill)})
    if request.method == "DELETE":
        auth_error = _require_auth(
            request, allow_session=True, require_csrf_for_session=True
        )
        if auth_error:
            return auth_error
        skill = get_object_or_404(UserSkill, id=skill_id, user=request.user)
        skill.delete()
        return _json_success({"deleted": True})
    return HttpResponseNotAllowed(["GET", "PATCH", "PUT", "DELETE"])


def resumes(request):
    if request.method == "GET":
        auth_error = _require_auth(request, allow_session=True)
        if auth_error:
            return auth_error
        resumes_qs = Resume.objects.filter(user=request.user).order_by("-updated_at")
        return _json_success({"resumes": [_serialize_resume(resume) for resume in resumes_qs]})
    if request.method == "POST":
        auth_error = _require_auth(
            request, allow_session=True, require_csrf_for_session=True
        )
        if auth_error:
            return auth_error
        data = _parse_json(request)
        if data is None:
            return _json_error("Invalid JSON.")
        title = data.get("title", "").strip() or "Resume"
        resume = Resume.objects.create(
            user=request.user,
            title=title,
            headline=data.get("headline", ""),
            summary=data.get("summary", ""),
            phone=data.get("phone", ""),
            email=data.get("email", ""),
            website=data.get("website", ""),
            location=data.get("location", ""),
        )
        return _json_success({"resume": _serialize_resume(resume)}, status=201)
    return HttpResponseNotAllowed(["GET", "POST"])


def resume_detail(request, resume_id):
    if request.method == "GET":
        auth_error = _require_auth(request, allow_session=True)
        if auth_error:
            return auth_error
        resume = get_object_or_404(Resume, id=resume_id, user=request.user)
        return _json_success({"resume": _serialize_resume(resume)})
    if request.method in {"PATCH", "PUT"}:
        auth_error = _require_auth(
            request, allow_session=True, require_csrf_for_session=True
        )
        if auth_error:
            return auth_error
        resume = get_object_or_404(Resume, id=resume_id, user=request.user)
        data = _parse_json(request)
        if data is None:
            return _json_error("Invalid JSON.")
        for field in (
            "title",
            "headline",
            "summary",
            "phone",
            "email",
            "website",
            "location",
        ):
            if field in data:
                setattr(resume, field, data[field])
        resume.save()
        return _json_success({"resume": _serialize_resume(resume)})
    if request.method == "DELETE":
        auth_error = _require_auth(
            request, allow_session=True, require_csrf_for_session=True
        )
        if auth_error:
            return auth_error
        resume = get_object_or_404(Resume, id=resume_id, user=request.user)
        resume.delete()
        return _json_success({"deleted": True})
    return HttpResponseNotAllowed(["GET", "PATCH", "PUT", "DELETE"])

# Create your views here.
